"""Native Qt main window for live routing intent inspection."""

from __future__ import annotations

import threading

from PySide6.QtCore import QItemSelection, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from screen_audio_router.autostart import AutostartManager, AutostartState
from screen_audio_router.config import AppConfig, ConfigurationStore
from screen_audio_router.config.resolver import RoutingConfigResolver
from screen_audio_router.config.schema import RoutingMode
from screen_audio_router.diagnostics import (
    DiagnosticCategory,
    DiagnosticEventHistory,
    DiagnosticSeverity,
    build_diagnostic_snapshot,
)
from screen_audio_router.display_mapping import MappingOperationStatus, MigrationStatus
from screen_audio_router.display_runtime import DisplayRuntimeContext
from screen_audio_router.gui.application_list import (
    ApplicationTableModel,
    ApplicationViewModel,
    RoutingModeDelegate,
)
from screen_audio_router.gui.automatic_routing import AutomaticRoutingService
from screen_audio_router.gui.diagnostics import DiagnosticsDialog
from screen_audio_router.gui.manual_routing import ManualRoutingService
from screen_audio_router.gui.runtime import GuiRuntime
from screen_audio_router.gui.settings_window import DisplayMappingSettingsDialog
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
        autostart_manager: AutostartManager | None = None,
        automatic_routing_service: AutomaticRoutingService | None = None,
        display_context: DisplayRuntimeContext | None = None,
    ) -> None:
        super().__init__()
        self.diagnostic_history = DiagnosticEventHistory()
        self.store = store or ConfigurationStore()
        self.autostart_manager = autostart_manager or AutostartManager()
        self.display_context = display_context or DisplayRuntimeContext(config, self.store)
        self.view_model = ApplicationViewModel(
            config, self.store, self.display_context.routing_mappings
        )
        self.model = ApplicationTableModel(self.view_model, self)
        resolver = RoutingConfigResolver(config)
        self.runtime = runtime or GuiRuntime(
            ApplicationMatcher(resolver.matcher_aliases()), config.settings.poll_interval_ms, self
        )
        execution_lock = threading.RLock()
        self.routing_service = routing_service or ManualRoutingService(
            ManualRoutingController(
                config,
                self.store,
                self.runtime.backend,
                execution_lock=execution_lock,
                display_mappings=self.display_context.routing_mappings,
            ),
            self,
        )
        self.automatic_routing_service = automatic_routing_service or AutomaticRoutingService(
            config,
            self.runtime.backend,
            execution_lock,
            self,
            display_mappings=self.display_context.routing_mappings,
        )
        self.setWindowTitle("Screen Audio Router")
        self.resize(1040, 620)
        self.enabled_checkbox = QCheckBox("Router enabled")
        self.enabled_checkbox.setChecked(config.enabled)
        self.enabled_checkbox.setToolTip(
            "Master switch for continuous AUTO routing and explicit routing actions."
        )
        self.diagnostics_button = QPushButton("Diagnostics")
        self.settings_button = QPushButton("Display Mappings")
        self.autostart_checkbox = QCheckBox("Start automatically at login")
        autostart_status = self.autostart_manager.status()
        self.autostart_checkbox.setChecked(autostart_status.enabled)
        self.autostart_checkbox.setEnabled(autostart_status.state is not AutostartState.ERROR)
        self.autostart_checkbox.setToolTip(autostart_status.message)
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

        self.application_details = QGroupBox("Application details")
        self.application_details.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        details_layout = QFormLayout(self.application_details)
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

        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.empty_state)
        table_layout.addWidget(self.table, 1)
        table_container.setMinimumHeight(120)
        table_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self.details_scroll_area = QScrollArea()
        self.details_scroll_area.setWidgetResizable(True)
        self.details_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.details_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.details_scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.details_scroll_area.setMinimumHeight(160)
        self.details_scroll_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.details_scroll_area.setWidget(self.application_details)

        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter.addWidget(table_container)
        self.main_splitter.addWidget(self.details_scroll_area)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setCollapsible(0, False)
        self.main_splitter.setCollapsible(1, False)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([360, 240])

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(self.enabled_checkbox)
        layout.addWidget(self.autostart_checkbox)
        layout.addWidget(self.diagnostics_button)
        layout.addWidget(self.settings_button)
        layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(container)
        self.statusBar().showMessage("Waiting for live KWin and audio data…")

        self.enabled_checkbox.toggled.connect(self._enabled_changed)
        self.autostart_checkbox.toggled.connect(self._autostart_changed)
        self.diagnostics_button.clicked.connect(self.show_diagnostics)
        self.settings_button.clicked.connect(self.show_settings)
        self.fixed_output.currentIndexChanged.connect(self._fixed_output_changed)
        self.apply_button.clicked.connect(self._apply_now)
        self.reset_button.clicked.connect(self._reset_configuration)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.model.dataChanged.connect(self._model_changed)
        self.model.modelReset.connect(self._restore_selection)
        self.model.mode_requested.connect(self._mode_requested)
        self.runtime.snapshot_changed.connect(self._snapshot_changed)
        display_refresh_requested = getattr(self.runtime, "display_refresh_requested", None)
        if display_refresh_requested is not None:
            display_refresh_requested.connect(self._refresh_displays)
        runtime_ready = getattr(self.runtime, "ready", None)
        if runtime_ready is not None:
            runtime_ready.connect(self.automatic_routing_service.controller.mark_ready)
        self.runtime.error_occurred.connect(self._show_error)
        self.automatic_routing_service.event_received.connect(self._automatic_routing_event)
        self.automatic_routing_service.refresh_requested.connect(self.runtime.refresh_audio)
        self.routing_service.completed.connect(self._routing_completed)
        self.routing_service.failed.connect(self._routing_failed)
        self.routing_service.busy_changed.connect(self._routing_busy_changed)
        self._seen_application_ids: set[str] = set()
        self._lifecycle_managed = False
        self._tray_available = False
        self.diagnostics_dialog = DiagnosticsDialog(self._diagnostic_snapshot, self)
        self.settings_dialog = DisplayMappingSettingsDialog(self.display_context, self)
        self.settings_dialog.mapping_result.connect(self._mapping_result)
        self.settings_dialog.migration_result.connect(self._migration_result)
        self.settings_dialog.display_refreshed.connect(self._display_refreshed)
        self._diagnostic_app_ids: set[str] = set()
        self._diagnostic_stream_ids: set[str] = set()
        self._diagnostic_output_ids: set[str] = set()
        self._diagnostic_displays: dict[str, str | None] = {}
        self._manual_application_id: str | None = None

    def start_runtime(self) -> bool:
        self._refresh_displays()
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
        self.automatic_routing_service.stop()
        self.runtime.stop()
        event.accept()

    def configure_lifecycle(self, tray_available: bool) -> None:
        self._lifecycle_managed = True
        self._tray_available = tray_available

    def set_router_enabled(self, enabled: bool) -> None:
        self.enabled_checkbox.setChecked(enabled)

    def _snapshot_changed(self, entries, outputs) -> None:
        self.settings_dialog.set_outputs(tuple(outputs))
        registry = getattr(self.runtime, "registry", None)
        self.automatic_routing_service.controller.update_snapshot(
            tuple(entries), tuple(outputs), tuple(getattr(registry, "unmatched_streams", ()))
        )
        selected_id = self._selected_application_id()
        new_ids = [
            entry.application_id
            for entry in entries
            if entry.application_id not in self._seen_application_ids
            and not self.view_model.config.has_application_rule(entry.application_id)
        ]
        self._seen_application_ids.update(entry.application_id for entry in entries)
        self._record_snapshot_events(entries, outputs)
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
        if self.diagnostics_dialog.isVisible():
            self.diagnostics_dialog.refresh()

    def _enabled_changed(self, enabled: bool) -> None:
        self.automatic_routing_service.controller.configuration_changed()
        try:
            self.routing_service.controller.set_enabled(enabled)
        except OSError as exc:
            self._record_error(DiagnosticCategory.CONFIG, str(exc))
            self.enabled_checkbox.blockSignals(True)
            self.enabled_checkbox.setChecked(not enabled)
            self.enabled_checkbox.blockSignals(False)
            self._show_error(str(exc))
            return
        self.model.refresh()
        self.router_enabled_changed.emit(enabled)
        self.diagnostic_history.add(
            DiagnosticCategory.CONFIG,
            f"Router {'enabled' if enabled else 'disabled'} by user",
        )

    def _autostart_changed(self, enabled: bool) -> None:
        try:
            status = (
                self.autostart_manager.enable() if enabled else self.autostart_manager.disable()
            )
        except OSError as exc:
            actual = self.autostart_manager.status()
            self.autostart_checkbox.blockSignals(True)
            self.autostart_checkbox.setChecked(actual.enabled)
            self.autostart_checkbox.blockSignals(False)
            self.autostart_checkbox.setToolTip(str(exc))
            self.diagnostic_history.add(
                DiagnosticCategory.CONFIG,
                f"Autostart operation failed: {exc}",
                severity=DiagnosticSeverity.ERROR,
            )
            self._show_error(f"Could not update autostart: {exc}")
            return
        self.autostart_checkbox.setToolTip(status.message)
        self.diagnostic_history.add(
            DiagnosticCategory.CONFIG,
            f"Autostart {'enabled' if status.enabled else 'disabled'}",
        )
        self.statusBar().showMessage(status.message)

    def _fixed_output_changed(self, index: int) -> None:
        row = self._selected_row()
        output_id = self.fixed_output.itemData(index)
        if row is None or output_id is None:
            return
        self._begin_manual_routing(row.application.application_id)
        self.diagnostic_history.add(
            DiagnosticCategory.ROUTING,
            f"Manual fixed-output route requested for {row.application.display_name}",
            application_id=row.application.application_id,
        )
        accepted = self.routing_service.select_fixed_output(
            row.application, self.view_model.outputs, str(output_id)
        )
        if not accepted:
            self._end_rejected_manual_routing(row.application.application_id)

    def _mode_requested(self, application_id: str, mode: RoutingMode) -> None:
        row = self._row_for_application(application_id)
        if row is not None:
            self._begin_manual_routing(application_id)
            self.diagnostic_history.add(
                DiagnosticCategory.ROUTING,
                f"Routing mode {mode.value} requested for {row.application.display_name}",
                application_id=application_id,
            )
            accepted = self.routing_service.change_mode(
                row.application, self.view_model.outputs, mode
            )
            if not accepted:
                self._end_rejected_manual_routing(application_id)

    def _apply_now(self) -> None:
        row = self._selected_row()
        if row is not None:
            self._begin_manual_routing(row.application.application_id)
            self.diagnostic_history.add(
                DiagnosticCategory.ROUTING,
                f"Apply Now requested for {row.application.display_name}",
                application_id=row.application.application_id,
            )
            accepted = self.routing_service.apply_now(row.application, self.view_model.outputs)
            if not accepted:
                self._end_rejected_manual_routing(row.application.application_id)

    def _reset_configuration(self) -> None:
        row = self._selected_row()
        if row is not None and row.is_configured:
            self._begin_manual_routing(row.application.application_id)
            accepted = self.routing_service.reset_configuration(
                row.application, self.view_model.outputs
            )
            if not accepted:
                self._end_rejected_manual_routing(row.application.application_id)

    def _routing_completed(self, result: RoutingOperationResult) -> None:
        self.automatic_routing_service.controller.end_manual(result.application_id)
        self._manual_application_id = None
        severity = DiagnosticSeverity.INFO if result.success else DiagnosticSeverity.WARNING
        self.diagnostic_history.add(
            DiagnosticCategory.ROUTING,
            result.message,
            severity=severity,
            application_id=result.application_id,
            details=(f"moved={len(result.streams_moved)} failed={len(result.streams_failed)}"),
        )
        self.operation_message.setText(result.message)
        self.statusBar().showMessage(result.message)
        self.model.refresh()
        self._select_application(result.application_id)
        self.runtime.refresh_audio()

    def _routing_failed(self, message: str) -> None:
        if self._manual_application_id is not None:
            self.automatic_routing_service.controller.end_manual(self._manual_application_id)
            self._manual_application_id = None
        self._record_error(DiagnosticCategory.ROUTING, message)
        self.operation_message.setText(f"Routing failed: {message}")
        self._show_error(message)

    def _begin_manual_routing(self, application_id: str) -> None:
        self._manual_application_id = application_id
        self.automatic_routing_service.controller.begin_manual(application_id)

    def _end_rejected_manual_routing(self, application_id: str) -> None:
        self.automatic_routing_service.controller.end_manual(application_id)
        self._manual_application_id = None

    def _automatic_routing_event(self, event) -> None:
        severity = (
            DiagnosticSeverity.ERROR
            if event.kind == "failure"
            else DiagnosticSeverity.WARNING
            if event.kind in {"partial_failure", "stale"}
            else DiagnosticSeverity.INFO
        )
        details_parts = []
        if event.decision is not None:
            details_parts.extend(
                (
                    f"current={','.join(event.decision.current_outputs) or 'none'}",
                    f"desired={event.decision.desired_output or 'none'}",
                    f"attempted={','.join(event.decision.stream_ids) or 'none'}",
                    f"reason={event.decision.reason.value}",
                )
            )
        if event.execution is not None:
            succeeded = [item.stream_id for item in event.execution.streams if item.succeeded]
            failed = [item.stream_id for item in event.execution.streams if not item.succeeded]
            errors = [item.error for item in event.execution.streams if item.error]
            details_parts.extend(
                (
                    f"succeeded={','.join(succeeded) or 'none'}",
                    f"failed={','.join(failed) or 'none'}",
                )
            )
            if errors:
                details_parts.append(f"errors={' | '.join(errors)}")
        self.diagnostic_history.add(
            DiagnosticCategory.ROUTING,
            event.message,
            severity=severity,
            application_id=event.application_id,
            details=" ".join(details_parts) or None,
        )
        if event.kind in {"success", "partial_failure", "failure"}:
            self.statusBar().showMessage(event.message)

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
        self._record_error(DiagnosticCategory.RUNTIME, message)
        self.statusBar().showMessage(f"Error: {message}")
        if dialog:
            QMessageBox.critical(self, "Screen Audio Router", message)

    def show_diagnostics(self) -> None:
        self.diagnostics_dialog.show_and_refresh()

    def show_settings(self) -> None:
        self.settings_dialog.set_outputs(tuple(self.view_model.outputs))
        self.settings_dialog.show_and_refresh()

    def _refresh_displays(self) -> None:
        result = self.display_context.refresh()
        self._display_refreshed(result)
        self.model.refresh()

    def _display_refreshed(self, result) -> None:
        if result.error:
            self.diagnostic_history.add(
                DiagnosticCategory.RUNTIME,
                f"Display discovery unavailable: {result.error}",
                severity=DiagnosticSeverity.WARNING,
            )
        elif result.changed:
            self.diagnostic_history.add(
                DiagnosticCategory.RUNTIME,
                f"Display snapshot refreshed: {len(result.displays)} connected",
            )

    def _mapping_result(self, result) -> None:
        success = result.status in {
            MappingOperationStatus.APPLIED,
            MappingOperationStatus.NO_CHANGE,
        }
        self.diagnostic_history.add(
            DiagnosticCategory.CONFIG,
            f"Display mapping {result.status.value}",
            severity=DiagnosticSeverity.INFO if success else DiagnosticSeverity.ERROR,
            details=(
                f"display={result.display_id} connector={result.connector or 'disconnected'} "
                f"old={result.old_output_id or 'none'} new={result.new_output_id or 'none'}"
            ),
        )
        if result.status is MappingOperationStatus.APPLIED:
            targeted = self.automatic_routing_service.controller.reevaluate_display(
                result.connector
            )
            self._record_targeted_reevaluation(targeted)
        self.model.refresh()

    def _migration_result(self, result) -> None:
        success = result.status is MigrationStatus.MIGRATED
        self.diagnostic_history.add(
            DiagnosticCategory.CONFIG,
            f"Display mapping migration {result.status.value}",
            severity=DiagnosticSeverity.INFO if success else DiagnosticSeverity.WARNING,
            details=(
                f"source={result.source_display_id} target={result.target_display_id or 'none'}"
            ),
        )
        if success:
            target = next(
                (
                    item
                    for item in self.display_context.displays
                    if item.stable_id == result.target_display_id
                ),
                None,
            )
            targeted = self.automatic_routing_service.controller.reevaluate_display(
                target.connector if target else None
            )
            self._record_targeted_reevaluation(targeted)
        self.model.refresh()

    def _record_targeted_reevaluation(self, result) -> None:
        requested = ",".join(result.requested) or "none"
        skipped = ",".join(result.skipped) or "none"
        self.diagnostic_history.add(
            DiagnosticCategory.ROUTING,
            (
                "Targeted AUTO reevaluation requested"
                if result.requested
                else "Targeted AUTO reevaluation skipped"
            ),
            details=(
                f"connector={result.connector or 'none'} requested={requested} skipped={skipped}"
            ),
        )

    def _diagnostic_snapshot(self):
        registry = getattr(self.runtime, "registry", None)
        unmatched = tuple(getattr(registry, "unmatched_streams", ()))
        kwin = getattr(self.runtime, "kwin", None)
        windows = None
        if kwin is not None:
            windows = tuple(kwin.windows(include_all=True))
        path = getattr(self.store, "path", "In-memory/test configuration")
        started = bool(getattr(self.runtime, "_started", True))
        audio_error = getattr(self.runtime, "audio_error", None)
        kwin_error = getattr(self.runtime, "kwin_error", None)
        display_rows = self.display_context.service.rows(
            self.display_context.displays, tuple(self.view_model.outputs)
        )
        migration_statuses = {
            mapping.display.id: self.display_context.service.preview_migration(
                mapping.display.id, self.display_context.displays
            ).status.value
            for mapping in self.view_model.config.display_mappings
        }
        return build_diagnostic_snapshot(
            self.view_model.config,
            self.view_model.entries,
            self.view_model.outputs,
            unmatched,
            windows=windows,
            events=self.diagnostic_history.events,
            configuration_path=path,
            audio_backend_health=f"Error: {audio_error}" if audio_error else "Available",
            kwin_health=f"Error: {kwin_error}" if kwin_error else "Connected",
            dbus_health="Owned" if started else "Not owned",
            autostart_health=self.autostart_manager.status().state.value.title(),
            display_rows=display_rows,
            display_discovery_health=(
                f"Error: {self.display_context.error}"
                if self.display_context.error
                else "Available"
            ),
            resolved_display_mappings=self.display_context.routing_mappings(),
            migration_statuses=migration_statuses,
        )

    def _record_snapshot_events(self, entries, outputs) -> None:
        app_ids = {entry.application_id for entry in entries}
        stream_ids = {matched.stream.stream_id for entry in entries for matched in entry.streams}
        registry = getattr(self.runtime, "registry", None)
        stream_ids.update(
            item.stream.stream_id for item in getattr(registry, "unmatched_streams", ())
        )
        output_ids = {output.id for output in outputs}
        displays = {entry.application_id: entry.current_display for entry in entries}
        for app_id in sorted(app_ids - self._diagnostic_app_ids):
            self.diagnostic_history.add(
                DiagnosticCategory.RUNTIME,
                f"Application discovered: {app_id}",
                application_id=app_id,
            )
        for stream_id in sorted(stream_ids - self._diagnostic_stream_ids):
            self.diagnostic_history.add(
                DiagnosticCategory.AUDIO, f"Audio stream appeared: {stream_id}"
            )
        for stream_id in sorted(self._diagnostic_stream_ids - stream_ids):
            self.diagnostic_history.add(
                DiagnosticCategory.AUDIO, f"Audio stream disappeared: {stream_id}"
            )
        for output_id in sorted(output_ids - self._diagnostic_output_ids):
            self.diagnostic_history.add(
                DiagnosticCategory.AUDIO, f"Audio output appeared: {output_id}"
            )
        for output_id in sorted(self._diagnostic_output_ids - output_ids):
            self.diagnostic_history.add(
                DiagnosticCategory.AUDIO,
                f"Audio output disappeared: {output_id}",
                severity=DiagnosticSeverity.WARNING,
            )
        for app_id in sorted(app_ids & self._diagnostic_app_ids):
            previous = self._diagnostic_displays.get(app_id)
            current = displays.get(app_id)
            if previous != current:
                self.diagnostic_history.add(
                    DiagnosticCategory.KWIN,
                    f"Controlling display changed: {previous or 'No Window'} -> "
                    f"{current or 'No Window'}",
                    application_id=app_id,
                )
        self._diagnostic_app_ids = app_ids
        self._diagnostic_stream_ids = stream_ids
        self._diagnostic_output_ids = output_ids
        self._diagnostic_displays = displays

    def _record_error(self, category: DiagnosticCategory, message: str) -> None:
        self.diagnostic_history.add(category, message, severity=DiagnosticSeverity.ERROR)


def configuration_error_message(error: Exception) -> str:
    return (
        f"The configuration could not be loaded:\n\n{error}\n\n"
        "Fix or move the reported JSON file, then start Screen Audio Router again. "
        "The invalid file was not overwritten."
    )
