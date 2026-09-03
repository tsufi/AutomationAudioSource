"""Top-level desktop application lifecycle and tray coordination."""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from screen_audio_router.config import AppConfig, ConfigurationStore
from screen_audio_router.gui.main_window import MainWindow
from screen_audio_router.gui.tray import SystemTrayController


class DesktopApplication(QObject):
    """Own the single window, tray, runtime, and clean process shutdown path."""

    def __init__(
        self,
        app: QApplication,
        config: AppConfig,
        store: ConfigurationStore | None = None,
        *,
        window: MainWindow | None = None,
        tray: SystemTrayController | None = None,
        tray_available: bool | None = None,
    ) -> None:
        super().__init__()
        self.app = app
        self.config = config
        self.store = store or ConfigurationStore()
        self.window = window or MainWindow(config, self.store)
        self.tray = tray or SystemTrayController(config.enabled, self, available=tray_available)
        self._shutdown = False
        self._close_message_shown = False
        self.app.setQuitOnLastWindowClosed(False)
        self.window.configure_lifecycle(self.tray.available)

        self.tray.open_requested.connect(self.show_window)
        self.tray.enabled_requested.connect(self.set_enabled)
        self.tray.display_mappings_requested.connect(self.window.show_settings)
        self.tray.diagnostics_requested.connect(self.window.show_diagnostics)
        self.tray.about_requested.connect(self.window.show_about)
        self.tray.quit_requested.connect(self.shutdown)
        self.window.router_enabled_changed.connect(self.tray.set_enabled)
        self.window.close_quit_requested.connect(self.shutdown)
        self.window.hidden_to_tray.connect(self._window_hidden)
        self.window.application_count_changed.connect(self.tray.set_application_count)
        self.app.aboutToQuit.connect(self.stop_services)

    def start(self, *, autostart: bool = False) -> bool:
        self.tray.show()
        started = self.window.start_runtime()
        if not autostart or not self.tray.available or not started:
            self.window.show()
        else:
            self.window.hide()
        return started

    def show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def set_enabled(self, enabled: bool) -> None:
        self.window.set_router_enabled(enabled)
        self.tray.set_enabled(self.config.enabled)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.stop_services()
        self.tray.hide()
        self.app.quit()

    def handle_sigint(self, signum=None, frame=None) -> None:
        del signum, frame
        self.shutdown()

    def stop_services(self) -> None:
        automatic = getattr(self.window, "automatic_routing_service", None)
        if automatic is not None:
            automatic.stop()
        self.window.routing_service.stop()
        self.window.runtime.stop()
        self.window.settings_dialog.close()
        self.window.diagnostics_dialog.close()
        self.window.about_dialog.close()

    def _window_hidden(self) -> None:
        if not self._close_message_shown:
            self._close_message_shown = True
            self.tray.show_close_message()
