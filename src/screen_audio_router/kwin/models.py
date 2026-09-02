"""Typed runtime window facts received from KWin."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class KWinPayloadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WindowInfo:
    runtime_id: str
    resource_class: str | None = None
    resource_name: str | None = None
    caption: str | None = None
    pid: int | None = None
    output_name: str | None = None
    active: bool = False
    normal_window: bool = False
    window_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WindowInfo:
        if not isinstance(payload, dict):
            raise KWinPayloadError("window payload must be an object")
        runtime_id = payload.get("runtimeId")
        if not isinstance(runtime_id, str) or not runtime_id:
            raise KWinPayloadError("window payload requires a non-empty runtimeId")
        pid = payload.get("pid")
        if pid is not None and (isinstance(pid, bool) or not isinstance(pid, int)):
            raise KWinPayloadError("window pid must be an integer or null")
        active = payload.get("active", False)
        normal = payload.get("normalWindow", False)
        if not isinstance(active, bool) or not isinstance(normal, bool):
            raise KWinPayloadError("active and normalWindow must be booleans")
        string_fields = {
            key: payload.get(key)
            for key in ("resourceClass", "resourceName", "caption", "outputName", "windowType")
        }
        if any(value is not None and not isinstance(value, str) for value in string_fields.values()):
            raise KWinPayloadError("window text fields must be strings or null")
        return cls(
            runtime_id=runtime_id,
            resource_class=string_fields["resourceClass"],
            resource_name=string_fields["resourceName"],
            caption=string_fields["caption"],
            pid=pid,
            output_name=string_fields["outputName"],
            active=active,
            normal_window=normal,
            window_type=string_fields["windowType"],
        )
