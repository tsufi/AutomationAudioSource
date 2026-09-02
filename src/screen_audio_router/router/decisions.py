"""Typed, explainable routing intent and decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from screen_audio_router.config.schema import RoutingMode


class RouteAction(StrEnum):
    NO_ACTION = "no_action"
    MOVE = "move"
    DEVICE_MISSING = "device_missing"
    UNMATCHED = "unmatched"
    ERROR = "error"


class RouteReason(StrEnum):
    GLOBAL_DISABLED = "global_disabled"
    MODE_IGNORE = "mode_ignore"
    FIXED_DEVICE = "fixed_device"
    AUTO_DISPLAY_MAPPING = "auto_display_mapping"
    NO_CONTROLLING_WINDOW = "no_controlling_window"
    DISPLAY_UNMAPPED = "display_unmapped"
    DESTINATION_MISSING = "destination_missing"
    NO_STREAMS = "no_streams"
    ALREADY_CORRECT = "already_correct"
    AMBIGUOUS_AUDIO = "ambiguous_audio"
    INSUFFICIENT_STATE = "insufficient_state"


@dataclass(frozen=True, slots=True)
class ApplicationRoutingRule:
    application_id: str
    mode: RoutingMode = RoutingMode.IGNORE
    fixed_output_id: str | None = None


@dataclass(frozen=True, slots=True)
class DisplayAudioMapping:
    """Maps a display identity to a stable AudioOutput identity."""

    display_id: str
    output_id: str


@dataclass(frozen=True, slots=True)
class RouteDecision:
    application_id: str
    mode: RoutingMode
    action: RouteAction
    reason: RouteReason
    current_outputs: tuple[str, ...] = ()
    desired_output: str | None = None
    controlling_display: str | None = None
    stream_ids: tuple[str, ...] = ()
    destination_available: bool | None = None
    details: str = ""


@dataclass(frozen=True, slots=True)
class StreamExecutionResult:
    stream_id: str
    succeeded: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RouteExecutionResult:
    decision: RouteDecision
    streams: tuple[StreamExecutionResult, ...]

    @property
    def succeeded(self) -> bool:
        return bool(self.streams) and all(item.succeeded for item in self.streams)
