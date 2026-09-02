"""User-session DBus receiver for the KWin JavaScript transport."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Slot
from PySide6.QtDBus import QDBusConnection

from screen_audio_router.kwin.integration import KWinIntegration
from screen_audio_router.kwin.models import KWinPayloadError

SERVICE_NAME = "org.screenaudiorouter.Service"
OBJECT_PATH = "/org/screenaudiorouter/Service"
INTERFACE_NAME = "org.screenaudiorouter.KWin"


class KWinDBusService(QObject):
    def __init__(self, integration: KWinIntegration) -> None:
        super().__init__()
        self.integration = integration
        self.logger = logging.getLogger(__name__)

    def register(self) -> None:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            raise RuntimeError("user-session DBus is unavailable")
        if not bus.registerService(SERVICE_NAME):
            raise RuntimeError(f"could not register DBus service {SERVICE_NAME}: {bus.lastError().message()}")
        if not bus.registerObject(
            OBJECT_PATH, INTERFACE_NAME, self, QDBusConnection.RegisterOption.ExportAllSlots
        ):
            bus.unregisterService(SERVICE_NAME)
            raise RuntimeError(f"could not register DBus object: {bus.lastError().message()}")

    def unregister(self) -> None:
        bus = QDBusConnection.sessionBus()
        bus.unregisterObject(OBJECT_PATH)
        bus.unregisterService(SERVICE_NAME)

    @Slot(str, str, result=bool)
    def ReportEvent(self, event_name: str, payload: str) -> bool:
        try:
            self.integration.handle_json_event(event_name, payload)
        except KWinPayloadError as exc:
            self.logger.warning("Rejected malformed KWin event: %s", exc)
            return False
        return True
