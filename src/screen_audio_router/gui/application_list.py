"""Thin Qt presentation model for application registry snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate

from screen_audio_router.audio.models import AudioOutput
from screen_audio_router.config.resolver import RoutingConfigResolver
from screen_audio_router.config.schema import AppConfig, RoutingMode
from screen_audio_router.router.application_registry import ApplicationEntry
from screen_audio_router.router.decisions import RouteAction, RouteDecision, RouteReason
from screen_audio_router.router.engine import RouterEngine


class ApplicationDiscoveryState(StrEnum):
    CONFIGURED = "configured"
    UNCONFIGURED = "unconfigured"


@dataclass(frozen=True, slots=True)
class ApplicationRow:
    application: ApplicationEntry
    mode: RoutingMode
    fixed_output_id: str | None
    screen: str
    audio_output: str
    status: str
    decision: RouteDecision
    discovery_state: ApplicationDiscoveryState

    @property
    def is_configured(self) -> bool:
        return self.discovery_state is ApplicationDiscoveryState.CONFIGURED


class ApplicationViewModel:
    def __init__(self, config: AppConfig, store=None) -> None:
        self.config = config
        self.engine = RouterEngine()
        self.entries: tuple[ApplicationEntry, ...] = ()
        self.outputs: tuple[AudioOutput, ...] = ()

    def rows(self) -> list[ApplicationRow]:
        resolver = RoutingConfigResolver(self.config)
        output_names = {output.id: output.description for output in self.outputs}
        rows: list[ApplicationRow] = []
        for entry in self.entries:
            configured = self.config.has_application_rule(entry.application_id)
            rule = resolver.application_rule(entry.application_id)
            decision = self.engine.decide(
                entry,
                rule,
                resolver.display_mappings(),
                self.outputs,
                enabled=resolver.enabled,
            )
            rows.append(
                ApplicationRow(
                    application=entry,
                    mode=rule.mode,
                    fixed_output_id=rule.fixed_output_id,
                    screen=entry.current_display or "No Window",
                    audio_output=_current_audio(entry, output_names),
                    status=decision_status(decision, output_names),
                    decision=decision,
                    discovery_state=(
                        ApplicationDiscoveryState.CONFIGURED
                        if configured
                        else ApplicationDiscoveryState.UNCONFIGURED
                    ),
                )
            )
        return sorted(
            rows,
            key=lambda row: (
                not row.application.active,
                row.is_configured,
                not bool(row.application.windows),
                row.application.audio_only,
                row.application.display_name.casefold(),
            ),
        )

    def update_runtime(
        self, entries: tuple[ApplicationEntry, ...], outputs: tuple[AudioOutput, ...]
    ) -> None:
        self.entries = entries
        self.outputs = outputs

    def fixed_output_options(self, application_id: str) -> list[tuple[str, str, bool]]:
        available = {output.id: output.description for output in self.outputs}
        options = [(output.id, output.description, True) for output in self.outputs]
        saved = self.config.application_rule(application_id).fixed_output_id
        if saved and saved not in available:
            options.append((saved, f"{saved} (Unavailable)", False))
        return options


class ApplicationTableModel(QAbstractTableModel):
    mode_requested = Signal(str, object)
    HEADERS = ("Application", "Screen", "Audio Output", "Routing", "Status")

    def __init__(self, view_model: ApplicationViewModel, parent=None) -> None:
        super().__init__(parent)
        self.view_model = view_model
        self._rows = view_model.rows()

    def rowCount(self, parent=None) -> int:
        parent = parent or QModelIndex()
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=None) -> int:
        parent = parent or QModelIndex()
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            values = (
                _application_label(row),
                row.screen,
                row.audio_output,
                _mode_label(row.mode),
                row.status,
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return row.application.application_id
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 2:
            return "\n".join(row.application.current_audio_outputs) or "No active audio streams"
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation is Qt.Orientation.Horizontal and role is Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsEditable if index.column() == 3 else flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or index.column() != 3:
            return False
        mode = _mode_from_label(str(value))
        self.mode_requested.emit(self._rows[index.row()].application.application_id, mode)
        return True

    def update_snapshot(self, entries, outputs) -> None:
        old_ids = [row.application.application_id for row in self._rows]
        self.view_model.update_runtime(tuple(entries), tuple(outputs))
        new_rows = self.view_model.rows()
        new_ids = [row.application.application_id for row in new_rows]
        if old_ids != new_ids:
            self.beginResetModel()
            self._rows = new_rows
            self.endResetModel()
        else:
            self._rows = new_rows
            if self._rows:
                self.dataChanged.emit(
                    self.index(0, 0),
                    self.index(len(self._rows) - 1, len(self.HEADERS) - 1),
                )

    def refresh(self) -> None:
        self.update_snapshot(self.view_model.entries, self.view_model.outputs)

    def row(self, row_index: int) -> ApplicationRow:
        return self._rows[row_index]


class RoutingModeDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        editor.addItems(["Ignore", "Auto", "Fixed Device"])
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data(Qt.ItemDataRole.EditRole))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


def decision_status(decision: RouteDecision, output_names: dict[str, str]) -> str:
    if decision.reason is RouteReason.GLOBAL_DISABLED:
        return "Router disabled"
    if decision.reason is RouteReason.MODE_IGNORE:
        return "Ignored"
    if decision.reason is RouteReason.ALREADY_CORRECT:
        return "Correct"
    if decision.action is RouteAction.MOVE:
        return (
            f"Would route to {output_names.get(decision.desired_output, decision.desired_output)}"
        )
    if decision.reason is RouteReason.DISPLAY_UNMAPPED:
        return "No display mapping"
    if decision.reason is RouteReason.DESTINATION_MISSING:
        return "Device missing"
    if decision.reason is RouteReason.NO_STREAMS:
        return "No audio"
    if decision.reason is RouteReason.NO_CONTROLLING_WINDOW:
        return "No window"
    if decision.reason is RouteReason.AMBIGUOUS_AUDIO:
        return "Audio match ambiguous"
    return "Needs attention"


def _current_audio(entry: ApplicationEntry, output_names: dict[str, str]) -> str:
    if not entry.streams:
        return "No Audio"
    if len(entry.current_audio_outputs) > 1:
        return "Multiple"
    if not entry.current_audio_outputs:
        return "Unknown"
    output_id = entry.current_audio_outputs[0]
    return output_names.get(output_id, output_id)


def _mode_label(mode: RoutingMode) -> str:
    return {
        RoutingMode.IGNORE: "Ignore",
        RoutingMode.AUTO: "Auto",
        RoutingMode.FIXED_DEVICE: "Fixed Device",
    }[mode]


def _mode_from_label(label: str) -> RoutingMode:
    return {
        "Ignore": RoutingMode.IGNORE,
        "Auto": RoutingMode.AUTO,
        "Fixed Device": RoutingMode.FIXED_DEVICE,
    }[label]


def _application_label(row: ApplicationRow) -> str:
    name = row.application.display_name or row.application.application_id
    return name if row.is_configured else f"{name}  NEW"
