"""Display-to-audio mapping settings built on the non-Qt domain service."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from screen_audio_router.audio.models import AudioOutput, Availability
from screen_audio_router.config.schema import DisplayIdentityKind
from screen_audio_router.display_mapping import (
    MappingOperationResult,
    MigrationResult,
    MigrationStatus,
)
from screen_audio_router.display_runtime import DisplayRuntimeContext


class MappingOutputSelector(QComboBox):
    """Report popup interaction so background refreshes cannot replace the editor."""

    popup_opened = Signal()
    popup_closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.popup_visible = False

    def showPopup(self) -> None:
        if not self.popup_visible:
            self.popup_visible = True
            self.popup_opened.emit()
        super().showPopup()

    def hidePopup(self) -> None:
        was_visible = self.popup_visible
        super().hidePopup()
        if was_visible:
            self.popup_visible = False
            self.popup_closed.emit()


class DisplayMappingSettingsDialog(QDialog):
    """Present mapping state; all mutations remain in DisplayMappingService."""

    mapping_result = Signal(object)
    migration_result = Signal(object)
    display_refreshed = Signal(object)

    def __init__(self, context: DisplayRuntimeContext, parent=None) -> None:
        super().__init__(parent)
        self.context = context
        self.outputs: tuple[AudioOutput, ...] = ()
        self.setWindowTitle("Display Audio Mappings")
        self.resize(850, 430)
        self.summary = QLabel(
            "Choose which audio output AUTO applications use on each physical display."
        )
        self.summary.setWordWrap(True)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Display", "Connection", "Audio output", "Status"))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.migration_label = QLabel("")
        self.migration_label.setWordWrap(True)
        self.migrate_button = QPushButton("Convert legacy mapping")
        self.migrate_button.setVisible(False)
        self.refresh_button = QPushButton("Refresh Displays")
        self.close_button = QPushButton("Close")
        controls = QHBoxLayout()
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.migrate_button)
        controls.addStretch(1)
        controls.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.table)
        layout.addWidget(self.migration_label)
        layout.addLayout(controls)
        self.refresh_button.clicked.connect(self.refresh_displays)
        self.migrate_button.clicked.connect(self._confirm_migration)
        self.close_button.clicked.connect(self.close)
        self._migration_source: str | None = None
        self._active_output_popups = 0
        self._row_refresh_pending = False

    def show_and_refresh(self) -> None:
        self.refresh_displays()
        self.show()
        self.raise_()
        self.activateWindow()

    def set_outputs(self, outputs: tuple[AudioOutput, ...]) -> None:
        if outputs == self.outputs:
            return
        self.outputs = outputs
        self.refresh_rows()

    def refresh_displays(self) -> None:
        result = self.context.refresh()
        self.display_refreshed.emit(result)
        self.refresh_rows()

    def refresh_rows(self) -> None:
        if self._active_output_popups:
            self._row_refresh_pending = True
            return
        self._row_refresh_pending = False
        rows = self.context.service.rows(self.context.displays, self.outputs)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self.table.setItem(index, 0, QTableWidgetItem(row.friendly_name))
            connection = row.connector if row.connected else "Disconnected"
            self.table.setItem(index, 1, QTableWidgetItem(connection or "Disconnected"))
            self.table.setCellWidget(
                index,
                2,
                self._output_selector(
                    row.display_id,
                    row.persisted_display_id,
                    row.mapped_output_id,
                    row.connected,
                ),
            )
            status_item = QTableWidgetItem(self._status_text(row))
            status_item.setToolTip(row.display_id)
            self.table.setItem(index, 3, status_item)
        self._refresh_migration()

    def assign(self, display_id: str, output_id: str) -> MappingOperationResult:
        result = self.context.service.assign_output(
            display_id, output_id, self.context.displays, self.outputs
        )
        self.mapping_result.emit(result)
        self.refresh_rows()
        return result

    def remove(self, display_id: str) -> MappingOperationResult:
        result = self.context.service.remove_mapping(display_id, self.context.displays)
        self.mapping_result.emit(result)
        self.refresh_rows()
        return result

    def migrate(self, source_display_id: str, *, confirmed: bool) -> MigrationResult | None:
        if not confirmed:
            return None
        result = self.context.service.migrate_fallback(source_display_id, self.context.displays)
        self.migration_result.emit(result)
        self.refresh_rows()
        return result

    def _output_selector(
        self,
        display_id: str,
        persisted_display_id: str | None,
        selected: str | None,
        connected: bool,
    ) -> MappingOutputSelector:
        selector = MappingOutputSelector()
        selector.addItem("No automatic routing", None)
        for output in self.outputs:
            label = output.description
            if output.availability is Availability.UNAVAILABLE:
                label += " (Unavailable)"
            selector.addItem(label, output.id)
        if selected and selector.findData(selected) < 0:
            selector.addItem(f"{selected} (Unavailable)", selected)
        selector.setCurrentIndex(max(0, selector.findData(selected)))
        selector.setEnabled(connected)
        selector.popup_opened.connect(self._output_interaction_started)
        selector.popup_closed.connect(self._output_interaction_finished)
        selector.activated.connect(
            lambda _index, current=selector, identity=display_id, persisted=persisted_display_id: (
                self._selection_changed(identity, persisted, current.currentData())
            )
        )
        return selector

    def _output_interaction_started(self) -> None:
        self._active_output_popups += 1

    def _output_interaction_finished(self) -> None:
        self._active_output_popups = max(0, self._active_output_popups - 1)
        if not self._active_output_popups and self._row_refresh_pending:
            QTimer.singleShot(0, self._flush_pending_row_refresh)

    def _flush_pending_row_refresh(self) -> None:
        if self._active_output_popups or not self._row_refresh_pending:
            return
        self.refresh_rows()

    def _selection_changed(
        self, display_id: str, persisted_display_id: str | None, output_id: str | None
    ) -> None:
        result = (
            self.remove(persisted_display_id or display_id)
            if output_id is None
            else self.assign(display_id, output_id)
        )
        if not result.succeeded:
            detail = result.error or result.status.value.replace("_", " ")
            QMessageBox.warning(self, "Display mapping", f"Could not update mapping: {detail}")

    def _refresh_migration(self) -> None:
        self._migration_source = None
        unavailable_message = ""
        for mapping in self.context.config.display_mappings:
            if mapping.display.identity_kind is not DisplayIdentityKind.CONNECTOR_FALLBACK:
                continue
            preview = self.context.service.preview_migration(
                mapping.display.id, self.context.displays
            )
            if preview.status is not MigrationStatus.MIGRATED:
                connector = mapping.display.connector or mapping.display.id.removeprefix(
                    "connector:"
                )
                reason = {
                    MigrationStatus.NO_CANDIDATE: "no unique physical display is currently available",
                    MigrationStatus.AMBIGUOUS: "the current display identity is ambiguous",
                    MigrationStatus.CONFLICT: "a conflicting physical display mapping already exists",
                    MigrationStatus.PERSISTENCE_ERROR: "the mapping could not be saved",
                    MigrationStatus.ALREADY_STABLE: "the mapping is already physical",
                }[preview.status]
                unavailable_message = (
                    f"Legacy connector mapping {connector} cannot be converted: {reason}."
                )
                continue
            target = next(
                item
                for item in self.context.displays
                if item.stable_id == preview.target_display_id
            )
            output_name = mapping.output.description or mapping.output.id
            connector = mapping.display.connector or mapping.display.id.removeprefix("connector:")
            self._migration_source = mapping.display.id
            self.migration_label.setText(
                f"Legacy connector mapping {connector} → {output_name} can be converted to "
                f"the physical display {target.friendly_name} → {output_name}."
            )
            self.migrate_button.setVisible(True)
            return
        self.migration_label.setText(unavailable_message)
        self.migrate_button.setVisible(False)

    def _confirm_migration(self) -> None:
        if self._migration_source is None:
            return
        confirmed = (
            QMessageBox.question(
                self,
                "Convert legacy mapping",
                self.migration_label.text() + "\n\nConvert this mapping now?",
            )
            is QMessageBox.StandardButton.Yes
        )
        result = self.migrate(self._migration_source, confirmed=confirmed)
        if result is not None and result.status is not MigrationStatus.MIGRATED:
            QMessageBox.warning(
                self,
                "Convert legacy mapping",
                f"The mapping was not converted: {result.status.value.replace('_', ' ')}.",
            )

    @staticmethod
    def _status_text(row) -> str:
        if not row.connected:
            return "Display disconnected — mapping retained"
        if row.mapped_output_id is None:
            return "Not mapped"
        if not row.output_available:
            return "Mapped output unavailable"
        if row.mapping_origin == "connector_fallback":
            return "Legacy connector mapping"
        return "Physical display mapping"
