"""Read-only diagnostic snapshots, bounded events, and sanitized reports."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from screen_audio_router.audio.models import AudioOutput, AudioStream
from screen_audio_router.config.resolver import RoutingConfigResolver
from screen_audio_router.config.schema import AppConfig
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import ApplicationEntry, UnmatchedStream
from screen_audio_router.router.decisions import RouteAction, RouteDecision, RouteReason
from screen_audio_router.router.engine import RouterEngine


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticCategory(StrEnum):
    RUNTIME = "runtime"
    KWIN = "kwin"
    AUDIO = "audio"
    MATCHING = "matching"
    ROUTING = "routing"
    CONFIG = "config"
    LIFECYCLE = "lifecycle"


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    timestamp: datetime
    severity: DiagnosticSeverity
    category: DiagnosticCategory
    message: str
    application_id: str | None = None
    details: str | None = None


class DiagnosticEventHistory:
    def __init__(self, limit: int = 200) -> None:
        self._events: deque[DiagnosticEvent] = deque(maxlen=limit)

    @property
    def events(self) -> tuple[DiagnosticEvent, ...]:
        return tuple(self._events)

    def add(
        self,
        category: DiagnosticCategory,
        message: str,
        *,
        severity: DiagnosticSeverity = DiagnosticSeverity.INFO,
        application_id: str | None = None,
        details: str | None = None,
    ) -> DiagnosticEvent:
        event = DiagnosticEvent(
            datetime.now().astimezone(), severity, category, message, application_id, details
        )
        self._events.append(event)
        return event


@dataclass(frozen=True, slots=True)
class ApplicationDiagnostic:
    name: str
    application_id: str
    configured: bool
    decision: RouteDecision
    current_audio: tuple[str, ...]
    window_count: int
    stream_count: int
    unmatched_stream_count: int
    streams_requiring_move: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StreamDiagnostic:
    stream: AudioStream
    matched_application: str | None
    confidence: str
    match_reason: str
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class DisplayMappingDiagnostic:
    display_id: str
    friendly_name: str
    identity_kind: str
    output_id: str
    output_name: str
    available: bool


@dataclass(frozen=True, slots=True)
class DiagnosticSnapshot:
    generated_at: datetime
    router_enabled: bool
    audio_backend_health: str
    kwin_health: str
    dbus_health: str
    configuration_health: str
    autostart_health: str
    configuration_path: str
    config_version: int
    application_rule_count: int
    matcher_alias_count: int
    applications: tuple[ApplicationDiagnostic, ...]
    outputs: tuple[AudioOutput, ...]
    streams: tuple[StreamDiagnostic, ...]
    windows: tuple[WindowInfo, ...]
    display_mappings: tuple[DisplayMappingDiagnostic, ...]
    events: tuple[DiagnosticEvent, ...]


def build_diagnostic_snapshot(
    config: AppConfig,
    entries: tuple[ApplicationEntry, ...],
    outputs: tuple[AudioOutput, ...],
    unmatched: tuple[UnmatchedStream, ...] = (),
    *,
    windows: tuple[WindowInfo, ...] | None = None,
    events: tuple[DiagnosticEvent, ...] = (),
    configuration_path: str | Path = "Unknown",
    audio_backend_health: str = "Available",
    kwin_health: str = "Connected",
    dbus_health: str = "Owned",
    configuration_health: str = "Loaded",
    autostart_health: str = "Disabled",
) -> DiagnosticSnapshot:
    resolver = RoutingConfigResolver(config)
    engine = RouterEngine()
    apps: list[ApplicationDiagnostic] = []
    stream_rows: list[StreamDiagnostic] = []
    all_windows: list[WindowInfo] = []
    for entry in entries:
        relevant_unmatched = tuple(
            item
            for item in unmatched
            if entry.application_id.casefold() in item.reason.casefold()
        )
        decision = engine.decide(
            entry,
            resolver.application_rule(entry.application_id),
            resolver.display_mappings(),
            outputs,
            enabled=resolver.enabled,
            ambiguous_audio=any("ambiguous" in item.reason for item in relevant_unmatched),
        )
        apps.append(
            ApplicationDiagnostic(
                entry.display_name,
                entry.application_id,
                config.has_application_rule(entry.application_id),
                decision,
                entry.current_audio_outputs,
                len(entry.windows),
                len(entry.streams),
                len(relevant_unmatched),
                decision.stream_ids,
            )
        )
        all_windows.extend(entry.windows)
        for matched in entry.streams:
            stream_rows.append(
                StreamDiagnostic(
                    matched.stream,
                    entry.application_id,
                    matched.match.confidence.name,
                    matched.match.reason,
                    False,
                )
            )
    for item in unmatched:
        stream_rows.append(
            StreamDiagnostic(
                item.stream,
                None,
                "UNMATCHED",
                item.reason,
                "ambiguous" in item.reason.casefold(),
            )
        )
    output_names = {output.id: output.description for output in outputs}
    mappings = tuple(
        DisplayMappingDiagnostic(
            mapping.display.id,
            mapping.display.friendly_name or mapping.display.connector or mapping.display.id,
            mapping.display.identity_kind.value,
            mapping.output.id,
            output_names.get(mapping.output.id, mapping.output.description or mapping.output.id),
            any(
                output.id == mapping.output.id and output.availability.value == "available"
                for output in outputs
            ),
        )
        for mapping in config.display_mappings
    )
    unique_windows = windows if windows is not None else tuple(
        {window.runtime_id: window for window in all_windows}.values()
    )
    return DiagnosticSnapshot(
        datetime.now().astimezone(),
        config.enabled,
        audio_backend_health,
        kwin_health,
        dbus_health,
        configuration_health,
        autostart_health,
        str(configuration_path),
        config.version,
        len(config.applications),
        len(config.matcher_aliases),
        tuple(apps),
        outputs,
        tuple(stream_rows),
        tuple(unique_windows),
        mappings,
        events,
    )


def explain_decision(decision: RouteDecision, output_names: dict[str, str] | None = None) -> str:
    names = output_names or {}
    destination = names.get(decision.desired_output or "", decision.desired_output or "output")
    explanations = {
        RouteReason.GLOBAL_DISABLED: "Router is disabled.",
        RouteReason.MODE_IGNORE: "Application is ignored.",
        RouteReason.DISPLAY_UNMAPPED: "No audio output is mapped to this display.",
        RouteReason.DESTINATION_MISSING: "Configured audio output is unavailable.",
        RouteReason.NO_CONTROLLING_WINDOW: "No controlling window is available.",
        RouteReason.NO_STREAMS: "Application currently has no audio streams.",
        RouteReason.ALREADY_CORRECT: "Application is already using the desired output.",
        RouteReason.AMBIGUOUS_AUDIO: "Audio could not be associated safely because the match is ambiguous.",
        RouteReason.INSUFFICIENT_STATE: decision.details or "Routing state is incomplete.",
    }
    if decision.action is RouteAction.MOVE:
        return f"Audio would be moved to {destination}."
    return explanations.get(decision.reason, decision.details or decision.reason.value)


def render_diagnostic_report(snapshot: DiagnosticSnapshot) -> str:
    output_names = {output.id: output.description for output in snapshot.outputs}
    lines = [
        "Screen Audio Router Diagnostics",
        f"Generated: {snapshot.generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "",
        "Overview",
        "--------",
        "Application: Screen Audio Router",
        f"Router: {'Enabled' if snapshot.router_enabled else 'Disabled'}",
        f"Audio backend: {snapshot.audio_backend_health}",
        f"KWin integration: {snapshot.kwin_health}",
        f"DBus service: {snapshot.dbus_health}",
        f"Configuration: {snapshot.configuration_health}",
        f"Autostart: {snapshot.autostart_health}",
        f"Configuration path: {snapshot.configuration_path}",
        f"Applications: {len(snapshot.applications)}",
        f"Audio outputs: {len(snapshot.outputs)}",
        f"Audio streams: {len(snapshot.streams)}",
        f"Windows: {len(snapshot.windows)}",
        "",
        "Applications",
        "------------",
    ]
    for app in snapshot.applications:
        decision = app.decision
        lines.extend(
            [
                app.name,
                f"  ID: {app.application_id}",
                f"  Configuration: {'Configured' if app.configured else 'Unconfigured'}",
                f"  Mode: {decision.mode.value}",
                f"  Display: {decision.controlling_display or 'No Window'}",
                f"  Current audio: {', '.join(output_names.get(x, x) for x in app.current_audio) or 'No Audio'}",
                f"  Desired audio: {output_names.get(decision.desired_output or '', decision.desired_output or 'None')}",
                f"  Decision: {decision.action.value}",
                f"  Reason: {explain_decision(decision, output_names)}",
                f"  Streams requiring move: {', '.join(app.streams_requiring_move) or 'None'}",
                f"  Unmatched/ambiguous streams: {app.unmatched_stream_count}",
            ]
        )
    lines.extend(["", "Audio Outputs", "-------------"])
    for output in snapshot.outputs:
        lines.append(
            f"{output.description} | {output.id} | {output.availability.value} | card={output.card_name or 'Unknown'} | port={output.active_port or 'Unknown'}"
        )
    lines.extend(["", "Audio Streams and Matching", "--------------------------"])
    for item in snapshot.streams:
        stream = item.stream
        lines.append(
            f"{stream.stream_id} | app={stream.application_name or 'Unknown'} | media={stream.media_name or 'Unknown'} | binary={stream.process_binary or 'Unknown'} | pid={stream.process_id or 'Unknown'} | output={stream.current_output or 'Unknown'} | match={item.matched_application or 'Unmatched'} | confidence={item.confidence} | reason={item.match_reason}"
        )
    lines.extend(["", "KWin Windows", "------------"])
    for window in snapshot.windows:
        lines.append(
            f"{window.runtime_id} | class={window.resource_class or 'Unknown'} | title={window.caption or 'Unknown'} | display={window.output_name or 'Unknown'} | active={window.active} | pid={window.pid or 'Unknown'}"
        )
    lines.extend(["", "Display Mappings", "----------------"])
    for mapping in snapshot.display_mappings:
        lines.append(
            f"{mapping.friendly_name} ({mapping.display_id}, {mapping.identity_kind}) -> {mapping.output_name} | {'Available' if mapping.available else 'Unavailable'}"
        )
    lines.extend(
        [
            "",
            "Configuration Summary",
            "---------------------",
            f"Schema version: {snapshot.config_version}",
            f"Application rules: {snapshot.application_rule_count}",
            f"Display mappings: {len(snapshot.display_mappings)}",
            f"Matcher aliases: {snapshot.matcher_alias_count}",
            "",
            "Recent Events",
            "-------------",
        ]
    )
    for event in snapshot.events:
        detail = f" | {event.details}" if event.details else ""
        lines.append(
            f"{event.timestamp.strftime('%H:%M:%S')} {event.severity.value.upper()} "
            f"{event.category.value}: {event.message}{detail}"
        )
    return sanitize_diagnostic_text("\n".join(lines))


def sanitize_diagnostic_text(value: str) -> str:
    value = re.sub(r"(https?://[^\s?]+)\?[^\s|]*", r"\1?[redacted]", value)
    value = re.sub(
        r"(?i)\b(password|passwd|token|api[_-]?key|authorization|secret)\s*[:=]\s*[^\s|]+",
        lambda match: f"{match.group(1)}=[redacted]",
        value,
    )
    return value
