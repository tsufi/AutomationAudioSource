"""Qt signal adapter for the automatic routing coordinator."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from screen_audio_router.config.schema import AppConfig
from screen_audio_router.router.automatic_routing import AutomaticRoutingController


class AutomaticRoutingService(QObject):
    event_received = Signal(object)
    refresh_requested = Signal()

    def __init__(
        self, config: AppConfig, backend, execution_lock, parent=None, display_mappings=None
    ) -> None:
        super().__init__(parent)
        self.controller = AutomaticRoutingController.from_backend(
            config,
            backend,
            execution_lock,
            event_sink=self.event_received.emit,
            refresh_requested=self.refresh_requested.emit,
            display_mappings=display_mappings,
        )

    def stop(self) -> None:
        self.controller.stop()
