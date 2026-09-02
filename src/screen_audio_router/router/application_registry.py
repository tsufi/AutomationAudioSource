"""Unified runtime registry of KWin windows and audio streams."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from screen_audio_router.audio.models import AudioStream
from screen_audio_router.kwin.integration import WindowEvent, WindowEventType
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.matcher import (
    ApplicationMatcher,
    MatchConfidence,
    MatchResult,
    normalize,
)


@dataclass(frozen=True, slots=True)
class MatchedStream:
    stream: AudioStream
    match: MatchResult


@dataclass(frozen=True, slots=True)
class UnmatchedStream:
    stream: AudioStream
    reason: str


@dataclass(frozen=True, slots=True)
class ApplicationEntry:
    application_id: str
    display_name: str
    windows: tuple[WindowInfo, ...]
    streams: tuple[MatchedStream, ...]
    controlling_window: WindowInfo | None
    active: bool
    current_display: str | None
    current_audio_outputs: tuple[str, ...]
    audio_only: bool = False


class ApplicationRegistry:
    def __init__(self, matcher: ApplicationMatcher) -> None:
        self.matcher = matcher
        self._windows: dict[str, WindowInfo] = {}
        self._streams: dict[str, AudioStream] = {}
        self._window_recency: dict[str, int] = {}
        self._activation_recency: dict[str, int] = {}
        self._counter = 0
        self._previous_matches: dict[str, str] = {}
        self._entries: tuple[ApplicationEntry, ...] = ()
        self._unmatched: tuple[UnmatchedStream, ...] = ()

    @property
    def entries(self) -> tuple[ApplicationEntry, ...]:
        return self._entries

    @property
    def unmatched_streams(self) -> tuple[UnmatchedStream, ...]:
        return self._unmatched

    def sync_windows(self, windows: list[WindowInfo]) -> bool:
        incoming = {window.runtime_id: window for window in windows if _usable_window(window)}
        if incoming == self._windows:
            return False
        for runtime_id, window in incoming.items():
            if self._windows.get(runtime_id) != window:
                self._touch(runtime_id, activated=window.active)
        self._windows = incoming
        self._rebuild()
        return True

    def handle_window_event(self, event: WindowEvent) -> None:
        if event.event_type is WindowEventType.SNAPSHOT:
            return
        window = event.window
        if event.event_type is WindowEventType.REMOVED:
            if window is not None:
                self._windows.pop(window.runtime_id, None)
                self._window_recency.pop(window.runtime_id, None)
                self._activation_recency.pop(window.runtime_id, None)
        elif window is not None and _usable_window(window):
            if event.event_type is WindowEventType.ACTIVATED:
                self._windows = {
                    runtime_id: WindowInfo(**(current.to_dict() | {"active": False}))
                    for runtime_id, current in self._windows.items()
                }
            self._windows[window.runtime_id] = window
            self._touch(window.runtime_id, event.event_type is WindowEventType.ACTIVATED)
        self._rebuild()

    def refresh_streams(self, streams: list[AudioStream]) -> bool:
        incoming = {stream.stream_id: stream for stream in streams}
        if incoming == self._streams:
            return False
        self._streams = incoming
        self._previous_matches = {
            stream_id: app_id
            for stream_id, app_id in self._previous_matches.items()
            if stream_id in incoming
        }
        self._rebuild()
        return True

    def find(self, application: str) -> ApplicationEntry | None:
        query = normalize(application)
        return next(
            (
                entry
                for entry in self._entries
                if normalize(entry.application_id) == query or normalize(entry.display_name) == query
            ),
            None,
        )

    def _touch(self, runtime_id: str, activated: bool = False) -> None:
        self._counter += 1
        self._window_recency[runtime_id] = self._counter
        if activated:
            self._activation_recency[runtime_id] = self._counter

    def _rebuild(self) -> None:
        windows_by_app: dict[str, list[WindowInfo]] = defaultdict(list)
        for window in self._windows.values():
            app_id = stable_window_identity(window)
            if app_id:
                windows_by_app[app_id].append(window)

        matches: dict[str, list[MatchedStream]] = defaultdict(list)
        unmatched: list[UnmatchedStream] = []
        audio_only: dict[str, list[AudioStream]] = defaultdict(list)
        results = {sid: self.matcher.match(stream, windows_by_app) for sid, stream in self._streams.items()}
        self._apply_alias_ambiguity(results, windows_by_app)
        for stream_id, stream in self._streams.items():
            result = results[stream_id]
            previous = self._previous_matches.get(stream_id)
            if result.application_id is None and previous in windows_by_app:
                prior_result = self.matcher.match(stream, {previous: windows_by_app[previous]})
                if prior_result.confidence >= MatchConfidence.HIGH:
                    result = MatchResult(previous, prior_result.confidence, "maintained prior match", prior_result.score)
            if result.application_id is not None and result.confidence >= MatchConfidence.HIGH:
                matches[result.application_id].append(MatchedStream(stream, result))
                self._previous_matches[stream_id] = result.application_id
            elif result.reason.startswith("ambiguous"):
                unmatched.append(UnmatchedStream(stream, result.reason))
            else:
                identity = stable_audio_identity(stream)
                if identity:
                    audio_only[identity].append(stream)
                else:
                    unmatched.append(UnmatchedStream(stream, result.reason))

        entries = [
            self._window_entry(app_id, windows, matches.get(app_id, []))
            for app_id, windows in windows_by_app.items()
        ]
        for app_id, streams in audio_only.items():
            entries.append(self._audio_only_entry(app_id, streams))
        self._entries = tuple(sorted(entries, key=lambda entry: (entry.display_name.casefold(), entry.application_id)))
        self._unmatched = tuple(sorted(unmatched, key=lambda item: item.stream.stream_id))

    def _apply_alias_ambiguity(
        self,
        results: dict[str, MatchResult],
        windows_by_app: dict[str, list[WindowInfo]],
    ) -> None:
        for app_id in windows_by_app:
            alias_candidates = [
                stream_id
                for stream_id, stream in self._streams.items()
                if self.matcher.is_alias_stream(app_id, stream)
                and results[stream_id].application_id == app_id
            ]
            if len(alias_candidates) > 1:
                for stream_id in alias_candidates:
                    if results[stream_id].reason != "configured alias":
                        continue
                    results[stream_id] = MatchResult(
                        None,
                        MatchConfidence.UNMATCHED,
                        f"ambiguous configured alias for {app_id}: "
                        f"{len(alias_candidates)} candidate streams",
                        75,
                    )

    def _window_entry(
        self, app_id: str, windows: list[WindowInfo], streams: list[MatchedStream]
    ) -> ApplicationEntry:
        ordered_windows = tuple(sorted(windows, key=lambda window: window.runtime_id))
        controlling = max(ordered_windows, key=self._control_rank) if ordered_windows else None
        display_name = _display_name(app_id, ordered_windows)
        outputs = tuple(sorted({item.stream.current_output for item in streams if item.stream.current_output}))
        return ApplicationEntry(
            application_id=app_id,
            display_name=display_name,
            windows=ordered_windows,
            streams=tuple(sorted(streams, key=lambda item: item.stream.stream_id)),
            controlling_window=controlling,
            active=any(window.active for window in ordered_windows),
            current_display=controlling.output_name if controlling else None,
            current_audio_outputs=outputs,
        )

    def _audio_only_entry(self, app_id: str, streams: list[AudioStream]) -> ApplicationEntry:
        matched = tuple(
            MatchedStream(
                stream,
                MatchResult(app_id, MatchConfidence.HIGH, "audio-only application identity", 75),
            )
            for stream in sorted(streams, key=lambda item: item.stream_id)
        )
        outputs = tuple(sorted({stream.current_output for stream in streams if stream.current_output}))
        return ApplicationEntry(
            application_id=f"audio:{app_id}",
            display_name=streams[0].application_name or streams[0].process_binary or app_id,
            windows=(),
            streams=matched,
            controlling_window=None,
            active=False,
            current_display=None,
            current_audio_outputs=outputs,
            audio_only=True,
        )

    def _control_rank(self, window: WindowInfo) -> tuple[int, int, int, str]:
        return (
            int(window.active),
            self._activation_recency.get(window.runtime_id, 0),
            self._window_recency.get(window.runtime_id, 0),
            window.runtime_id,
        )


def stable_window_identity(window: WindowInfo) -> str:
    return normalize(window.resource_class) or normalize(window.resource_name)


def stable_audio_identity(stream: AudioStream) -> str:
    return normalize(stream.application_name) or normalize(stream.process_binary)


def _usable_window(window: WindowInfo) -> bool:
    return bool(stable_window_identity(window)) and (
        window.normal_window or window.window_type in {"dialog", "utility"}
    )


def _display_name(app_id: str, windows: tuple[WindowInfo, ...]) -> str:
    resource_name = next((window.resource_name for window in windows if window.resource_name), None)
    if resource_name:
        return resource_name.replace("-", " ").title()
    return app_id
