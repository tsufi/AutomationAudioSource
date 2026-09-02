"""In-memory runtime window state and validated KWin events."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from screen_audio_router.kwin.models import KWinPayloadError, WindowInfo


class WindowEventType(StrEnum):
    SNAPSHOT = "snapshot"
    ADDED = "added"
    UPDATED = "updated"
    REMOVED = "removed"
    ACTIVATED = "activated"
    OUTPUT_CHANGED = "output_changed"


@dataclass(frozen=True, slots=True)
class WindowEvent:
    event_type: WindowEventType
    window: WindowInfo | None = None
    old_output: str | None = None


class KWinIntegration:
    def __init__(self) -> None:
        self._windows: dict[str, WindowInfo] = {}
        self._listeners: list[Callable[[WindowEvent], None]] = []

    def windows(self, include_all: bool = False) -> list[WindowInfo]:
        windows = list(self._windows.values())
        if not include_all:
            windows = [window for window in windows if window.normal_window]
        return sorted(windows, key=lambda window: (window.caption or "", window.runtime_id))

    def add_listener(self, listener: Callable[[WindowEvent], None]) -> None:
        self._listeners.append(listener)

    def handle_json_event(self, event_name: str, raw_payload: str) -> None:
        try:
            event_type = WindowEventType(event_name)
            payload = json.loads(raw_payload)
        except (ValueError, json.JSONDecodeError) as exc:
            raise KWinPayloadError(f"invalid KWin event: {exc}") from exc
        if event_type is WindowEventType.SNAPSHOT:
            self._handle_snapshot(payload)
            return
        if event_type is WindowEventType.REMOVED:
            self._handle_removed(payload)
            return
        window = WindowInfo.from_dict(payload)
        previous = self._windows.get(window.runtime_id)
        self._windows[window.runtime_id] = window
        old_output = previous.output_name if previous else None
        if event_type is WindowEventType.ACTIVATED:
            self._set_active(window.runtime_id)
            window = self._windows[window.runtime_id]
        self._emit(WindowEvent(event_type, window, old_output))

    def _handle_snapshot(self, payload: object) -> None:
        if not isinstance(payload, list):
            raise KWinPayloadError("snapshot payload must be an array")
        windows = [WindowInfo.from_dict(item) for item in payload]
        self._windows = {window.runtime_id: window for window in windows}
        self._emit(WindowEvent(WindowEventType.SNAPSHOT))

    def _handle_removed(self, payload: object) -> None:
        if not isinstance(payload, dict) or not isinstance(payload.get("runtimeId"), str):
            raise KWinPayloadError("removed payload requires runtimeId")
        window = self._windows.pop(payload["runtimeId"], None)
        self._emit(WindowEvent(WindowEventType.REMOVED, window))

    def _set_active(self, runtime_id: str) -> None:
        for key, current in list(self._windows.items()):
            self._windows[key] = WindowInfo(**(current.to_dict() | {"active": key == runtime_id}))

    def _emit(self, event: WindowEvent) -> None:
        for listener in tuple(self._listeners):
            listener(event)
