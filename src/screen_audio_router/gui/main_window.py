"""Native Qt main window for live routing intent inspection."""

from __future__ import annotations

from PySide6.QtCore import QItemSelection, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from screen_audio_router.config import AppConfig, ConfigurationStore
from screen_audio_router.config.resolver import RoutingConfigResolver
from screen_audio_router.config.schema import RoutingMode
from screen_audio_router.gui.application_list import (
    ApplicationTableModel,
    ApplicationViewModel,
    RoutingModeDelegate,
)
from screen_audio_router.gui.manual_routing import ManualRoutingService
from screen_audio_router.gui.runtime import GuiRuntime
from screen_audio_router.router.manual_routing import (
    ManualRoutingController,
    RoutingOperationResult,
)
from screen_audio_router.router.matcher import ApplicationMatcher


class MainWindow(QMainWindow):
    router_enabled_changed = Signal(bool)
    close_quit_requested = Signal()
    hidden_to_tray = Signal()
    application_count_changed = Signal(int)

    def __init__(
        self,
        config: AppConfig,
        store: ConfigurationStore | None = None,
        runtime: GuiRuntime | None = None,
        routing_service: ManualRoutingService | None = None,
    ) -> None:
        super().__init__()
        self.store = store or ConfigurationStore()
        self.view_model = ApplicationViewModel(config, self.store)
        self.model = ApplicationTableModel(self.view_model, self)
        resolver = RoutingConfigResolver(config)
        self.runtime = runtime or GuiRuntime(
            ApplicationMatcher(resolver.matcher_aliases()), config.settings.poll_interval_ms, self
        )
        self.routing_service = routing_service or ManualRoutingService(
            ManualRoutingController(config, self.store, self.runtime.backend), self
        )
        self.setWindowTitle("Screen Audio Router")
        self.resize(1040, 620)
        self.enabled_checkbox = QCheckBox("Router enabled")
        self.enabled_checkbox.setChecked(config.enabled)
        self.enabled_checkbox.setToolTip(
            "Controls routing decisions. Audio moves only after a direct user action."
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(3, RoutingModeDelegate(self.table))
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.empty_state = QLabel(
            "No applications detected. Open an application and it will appear here."
        )
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        details = QGroupBox("Application details")
        details_layout = QFormLayout(details)
        self.detail_id = QLabel("—")
        self.detail_name = QLabel("—")
        self.detail_display = QLabel("—")
        self.detail_audio = QLabel("—")
        self.detail_mode = QLabel("—")
        self.detail_configuration = QLabel("—")
        self.detail_desired = QLabel("—")
        self.detail_decision = QLabel("—")
        self.detail_counts = QLabel("—")
        self.operation_message = QLabel("Select an application to inspect its routing decision.")
        self.operation_message.setWordWrap(True)
        self.fixed_output = QComboBox()
        self.fixed_output.setEnabled(False)
        self.apply_button = QPushButton("Apply Now")
        self.apply_button.setToolTip("Apply the current decision to this application only")
        self.apply_button.setEnabled(False)
        self.reset_button = QPushButton("Reset Configuration")
        self.reset_button.setToolTip(
            "Remove this application rule and return to the safe Ignore default without moving audio"
        )
        self.reset_button.setEnabled(False)
        for label, widget in (
            ("Application", self.detail_name),
            ("Application ID", self.detail_id),
            ("Configuration", self.detail_configuration),
            ("Mode", self.detail_mode),
            ("Controlling screen", self.detail_display),
            ("Current audio", self.detail_audio),
            ("Desired audio", self.detail_desired),
            ("Decision", self.detail_decision),
            ("Runtime objects", self.detail_counts),
            ("Fixed output", self.fixed_output),
            ("Manual action", self.apply_button),
            ("Rule action", self.reset_button),
            ("Last result", self.operation_message),
        ):
            details_layout.addRow(label, widget)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(self.enabled_checkbox)
        layout.addWidget(self.empty_state)
        layout.addWidget(self.table, 1)
        layout.addWidget(details)
        self.setCentralWidget(container)
        self.statusBar().showMessage("Waiting for live KWin and audio data…")

        self.enabled_checkbox.toggled.connect(self._enabled_changed)
        self.fixed_output.currentIndexChanged.connect(self._fixed_output_changed)
        self.apply_button.clicked.connect(self._apply_now)
        self.reset_button.clicked.connect(self._reset_configuration)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.model.dataChanged.connect(self._model_changed)
        self.model.modelReset.connect(self._restore_selection)
        self.model.mode_requested.connect(self._mode_requested)
        self.runtime.snapshot_changed.connect(self._snapshot_changed)
        self.runtime.error_occurred.connect(self._show_error)
        self.routing_service.completed.connect(self._routing_completed)
        self.routing_service.failed.connect(self._routing_failed)
        self.routing_service.busy_changed.connect(self._routing_busy_changed)
        self._seen_application_ids: set[str] = set()
        self._lifecycle_managed = False
        self._tray_available = False

    def start_runtime(self) -> bool:
        try:
            self.runtime.start()
        except RuntimeError as exc:
            self._show_error(str(exc), dialog=True)
            return False
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._lifecycle_managed and self._tray_available:
            event.ignore()
            self.hide()
            self.hidden_to_tray.emit()
            return
        if self._lifecycle_managed:
            event.accept()
            self.close_quit_requested.emit()
            return
        self.routing_service.stop()
        self.runtime.stop()
        event.accept()

    def configure_lifecycle(self, tray_available: bool) -> None:
        self._lifecycle_managed = True
        self._tray_available = tray_available

    def set_router_enabled(self, enabled: bool) -> None:
        self.enabled_checkbox.setChecked(enabled)

    def _snapshot_changed(self, entries, outputs) -> None:
        selected_id = self._selected_application_id()
        new_ids = [
            entry.application_id
            for entry in entries
            if entry.application_id not in self._seen_application_ids
            and not self.view_model.config.has_application_rule(entry.application_id)
        ]
        self._seen_application_ids.update(entry.application_id for entry in entries)
        self.model.update_snapshot(entries, outputs)
        self.application_count_changed.emit(len(entries))
        self.empty_state.setVisible(not entries)
        self.table.setVisible(bool(entries))
        self._select_application(selected_id)
        if new_ids:
            row = self._row_for_application(new_ids[-1])
            name = row.application.display_name if row else new_ids[-1]
            self.statusBar().showMessage(f"New application discovered: {name}")
        else:
            self.statusBar().showMessage(
                f"Live: {len(entries)} applications, {len(outputs)} audio outputs"
            )

    def _enabled_changed(self, enabled: bool) -> None:
        try:
            self.routing_service.controller.set_enabled(enabled)
        except OSError as exc:
            self.enabled_checkbox.blockSignals(True)
            self.enabled_checkbox.setChecked(not enabled)
            self.enabled_checkbox.blockSignals(False)
            self._show_error(str(exc))
            return
        self.model.refresh()
        self.router_enabled_changed.emit(enabled)

    def _fixed_output_changed(self, index: int) -> None:
        row = self._selected_row()
        output_id = self.fixed_output.itemData(index)
        if row is None or output_id is None:
            return
        self.routing_service.select_fixed_output(
            row.application, self.view_model.outputs, str(output_id)
        )

    def _mode_requested(self, application_id: str, mode: RoutingMode) -> None:
        row = self._row_for_application(application_id)
        if row is not None:
            self.routing_service.change_mode(row.application, self.view_model.outputs, mode)

    def _apply_now(self) -> None:
        row = self._selected_row()
        if row is not None:
            self.routing_service.apply_now(row.application, self.view_model.outputs)

    def _reset_configuration(self) -> None:
        row = self._selected_row()
        if row is not None and row.is_configured:
            self.routing_service.reset_configuration(
                row.application, self.view_model.outputs
            )

    def _routing_completed(self, result: RoutingOperationResult) -> None:
        self.operation_message.setText(result.message)
        self.statusBar().showMessage(result.message)
        self.model.refresh()
        self._select_application(result.application_id)
        self.runtime.refresh_audio()

    def _routing_failed(self, message: str) -> None:
        self.operation_message.setText(f"Routing failed: {message}")
        self._show_error(message)

    def _routing_busy_changed(self, busy: bool) -> None:
        self.table.setEnabled(not busy)
        self.enabled_checkbox.setEnabled(not busy)
        if busy:
            self.fixed_output.setEnabled(False)
            self.apply_button.setEnabled(False)
            self.reset_button.setEnabled(False)
            self.operation_message.setText("Applying routing…")
        else:
            self._update_details()

    def _selection_changed(self, selected: QItemSelection, deselected: QItemSelection) -> None:
        del selected, deselected
        self._update_details()

    def _model_changed(self, top_left, bottom_right, roles=()) -> None:
        del top_left, bottom_right, roles
        self._update_details()

    def _restore_selection(self) -> None:
        if self.model.rowCount() and not self.table.selectionModel().hasSelection():
            self.table.selectRow(0)
        self._update_details()

    def _update_details(self) -> None:
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            for label in (
                self.detail_id,
                self.detail_name,
                self.detail_display,
                self.detail_audio,
                self.detail_mode,
                self.detail_configuration,
                self.detail_desired,
                self.detail_decision,
                self.detail_counts,
            ):
                label.setText("—")
            self._replace_fixed_options([], None, False)
            self.apply_button.setEnabled(False)
            self.reset_button.setEnabled(False)
            return
        row = self.model.row(indexes[0].row())
        entry = row.application
        self.detail_id.setText(entry.application_id)
        self.detail_name.setText(entry.display_name)
        self.detail_configuration.setText(
            "Configured" if row.is_configured else "Not configured (safe default: Ignore)"
        )
        self.detail_mode.setText(row.mode.value.replace("_", " ").title())
        self.detail_display.setText(row.screen)
        self.detail_audio.setText(row.audio_output)
        output_names = {output.id: output.description for output in self.view_model.outputs}
        desired = row.decision.desired_output
        self.detail_desired.setText(output_names.get(desired, desired) if desired else "—")
        self.detail_decision.setText(row.status)
        self.detail_counts.setText(
            f"{len(entry.windows)} window(s), {len(entry.streams)} stream(s)"
        )
        options = self.view_model.fixed_output_options(entry.application_id)
        self._replace_fixed_options(
            options, row.fixed_output_id, row.mode is RoutingMode.FIXED_DEVICE
        )
        self.apply_button.setEnabled(
            not self.routing_service.busy
            and row.mode is not RoutingMode.IGNORE
            and self.view_model.config.enabled
        )
        self.reset_button.setEnabled(not self.routing_service.busy and row.is_configured)

    def _replace_fixed_options(
        self, options: list[tuple[str, str, bool]], selected_id: str | None, enabled: bool
    ) -> None:
        self.fixed_output.blockSignals(True)
        self.fixed_output.clear()
        for output_id, label, available in options:
            self.fixed_output.addItem(label, output_id)
            if not available:
                item = self.fixed_output.model().item(self.fixed_output.count() - 1)
                if item is not None:
                    item.setEnabled(False)
        if selected_id:
            selected = self.fixed_output.findData(selected_id)
            if selected >= 0:
                self.fixed_output.setCurrentIndex(selected)
        self.fixed_output.setEnabled(enabled and bool(options))
        self.fixed_output.blockSignals(False)

    def _selected_application_id(self) -> str | None:
        indexes = self.table.selectionModel().selectedRows()
        return self.model.row(indexes[0].row()).application.application_id if indexes else None

    def _selected_row(self):
        indexes = self.table.selectionModel().selectedRows()
        return self.model.row(indexes[0].row()) if indexes else None

    def _row_for_application(self, application_id: str):
        for row_index in range(self.model.rowCount()):
            row = self.model.row(row_index)
            if row.application.application_id == application_id:
                return row
        return None

    def _select_application(self, application_id: str | None) -> None:
        if application_id:
            for row_index in range(self.model.rowCount()):
                if self.model.row(row_index).application.application_id == application_id:
                    self.table.selectRow(row_index)
                    return
        self._restore_selection()

    def _show_error(self, message: str, *, dialog: bool = False) -> None:
        self.statusBar().showMessage(f"Error: {message}")
        if dialog:
            QMessageBox.critical(self, "Screen Audio Router", message)


def configuration_error_message(error: Exception) -> str:
    return (
        f"The configuration could not be loaded:\n\n{error}\n\n"
        "Fix or move the reported JSON file, then start Screen Audio Router again. "
        "The invalid file was not overwritten."
    )
