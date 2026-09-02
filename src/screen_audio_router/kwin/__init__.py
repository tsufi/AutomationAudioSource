"""KWin/Wayland window discovery and event transport."""

from screen_audio_router.kwin.integration import KWinIntegration, WindowEvent, WindowEventType
from screen_audio_router.kwin.models import KWinPayloadError, WindowInfo

__all__ = [
    "KWinIntegration",
    "KWinPayloadError",
    "WindowEvent",
    "WindowEventType",
    "WindowInfo",
]
