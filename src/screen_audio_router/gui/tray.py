"""Native Qt system-tray presentation for desktop lifecycle controls."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from screen_audio_router.gui.icons import application_icon


class SystemTrayController(QObject):
    """Own tray widgets and emit intent without owning application policy."""

    open_requested = Signal()
    enabled_requested = Signal(bool)
    display_mappings_requested = Signal()
    diagnostics_requested = Signal()
    about_requested = Signal()
    quit_requested = Signal()

    def __init__(self, enabled: bool, parent=None, *, available: bool | None = None) -> None:
        super().__init__(parent)
        detected = QSystemTrayIcon.isSystemTrayAvailable()
        self.available = detected if available is None else available
        self.tray_icon = QSystemTrayIcon(application_icon(), self)
        self.menu = QMenu()
        self.open_action = self.menu.addAction("Open Screen Audio Router")
        self.enabled_action = QAction("Router Enabled", self.menu)
        self.enabled_action.setCheckable(True)
        self.enabled_action.setChecked(enabled)
        self.menu.addAction(self.enabled_action)
        self.status_action = self.menu.addAction("Applications detected: 0")
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.display_mappings_action = self.menu.addAction("Display Mappings")
        self.diagnostics_action = self.menu.addAction("Diagnostics")
        self.about_action = self.menu.addAction("About")
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction("Quit")
        self.tray_icon.setContextMenu(self.menu)
        self.set_application_count(0)

        self.open_action.triggered.connect(self.open_requested)
        self.enabled_action.toggled.connect(self.enabled_requested)
        self.display_mappings_action.triggered.connect(self.display_mappings_requested)
        self.diagnostics_action.triggered.connect(self.diagnostics_requested)
        self.about_action.triggered.connect(self.about_requested)
        self.quit_action.triggered.connect(self.quit_requested)
        self.tray_icon.activated.connect(self._activated)

    def show(self) -> None:
        if self.available:
            self.tray_icon.show()

    def hide(self) -> None:
        self.tray_icon.hide()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled_action.blockSignals(True)
        self.enabled_action.setChecked(enabled)
        self.enabled_action.blockSignals(False)
        self._update_tooltip()

    def set_application_count(self, count: int) -> None:
        self._application_count = count
        self.status_action.setText(f"Applications detected: {count}")
        self._update_tooltip()

    def show_close_message(self) -> None:
        if self.available:
            self.tray_icon.showMessage(
                "Screen Audio Router",
                "Screen Audio Router is still running in the system tray.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _update_tooltip(self) -> None:
        state = "enabled" if self.enabled_action.isChecked() else "disabled"
        self.tray_icon.setToolTip(
            f"Screen Audio Router\nRouter {state} · {self._application_count} applications"
        )

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.open_requested.emit()
