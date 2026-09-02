"""Explainable and conservative window-to-audio matching."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from screen_audio_router.audio.models import AudioStream
from screen_audio_router.kwin.models import WindowInfo


class MatchConfidence(IntEnum):
    UNMATCHED = 0
    LOW = 25
    MEDIUM = 50
    HIGH = 75
    EXACT = 100


@dataclass(frozen=True, slots=True)
class AliasRule:
    application_id: str
    audio_application_names: tuple[str, ...] = ()
    process_binaries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MatchResult:
    application_id: str | None
    confidence: MatchConfidence
    reason: str
    score: int


def default_alias_rules() -> tuple[AliasRule, ...]:
    """Bootstrap aliases proven by integration; future config can replace these."""

    return (
        AliasRule(
            application_id="com.edde746.plezy",
            audio_application_names=("mpv",),
            process_binaries=("mpv",),
        ),
    )


class ApplicationMatcher:
    def __init__(self, aliases: tuple[AliasRule, ...] = ()) -> None:
        self.aliases = {normalize(rule.application_id): rule for rule in aliases}

    def match(self, stream: AudioStream, windows_by_app: dict[str, list[WindowInfo]]) -> MatchResult:
        candidates: list[tuple[int, str, str]] = []
        for application_id, windows in windows_by_app.items():
            score, reason = self._score(stream, application_id, windows)
            if score:
                candidates.append((score, application_id, reason))
        if not candidates:
            return MatchResult(None, MatchConfidence.UNMATCHED, "no matching window evidence", 0)
        candidates.sort(key=lambda item: (-item[0], item[1]))
        best = candidates[0]
        if len(candidates) > 1 and candidates[1][0] == best[0]:
            return MatchResult(
                None,
                MatchConfidence.UNMATCHED,
                f"ambiguous equal-confidence applications: {best[1]}, {candidates[1][1]}",
                best[0],
            )
        return MatchResult(best[1], confidence_for_score(best[0]), best[2], best[0])

    def is_alias_stream(self, application_id: str, stream: AudioStream) -> bool:
        rule = self.aliases.get(normalize(application_id))
        if rule is None:
            return False
        names = {normalize(value) for value in rule.audio_application_names}
        binaries = {normalize(value) for value in rule.process_binaries}
        return normalize(stream.application_name) in names or normalize(stream.process_binary) in binaries

    def _score(
        self, stream: AudioStream, application_id: str, windows: list[WindowInfo]
    ) -> tuple[int, str]:
        if stream.process_id is not None and any(
            window.pid is not None and window.pid == stream.process_id for window in windows
        ):
            return 100, "exact PID match"
        identities = {normalize(application_id)}
        for window in windows:
            identities.update((normalize(window.resource_class), normalize(window.resource_name)))
        stream_names = {normalize(stream.application_name), normalize(stream.process_binary)} - {""}
        direct = identities & stream_names
        if direct:
            return 85, f"normalized application identity match: {min(direct)}"
        if self.is_alias_stream(application_id, stream):
            return 75, "configured alias"
        return 0, "no matching evidence"


def normalize(value: str | None) -> str:
    return value.strip().casefold() if value else ""


def confidence_for_score(score: int) -> MatchConfidence:
    if score >= 100:
        return MatchConfidence.EXACT
    if score >= 75:
        return MatchConfidence.HIGH
    if score >= 50:
        return MatchConfidence.MEDIUM
    if score > 0:
        return MatchConfidence.LOW
    return MatchConfidence.UNMATCHED
