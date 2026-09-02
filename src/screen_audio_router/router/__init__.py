"""Application registry and matching; routing policy begins in Phase 5."""

from screen_audio_router.router.application_registry import (
    ApplicationEntry,
    ApplicationRegistry,
    MatchedStream,
    UnmatchedStream,
)
from screen_audio_router.router.decisions import (
    ApplicationRoutingRule,
    DisplayAudioMapping,
    RouteAction,
    RouteDecision,
    RouteReason,
)
from screen_audio_router.router.engine import RouteExecutor, RouterEngine
from screen_audio_router.router.matcher import (
    AliasRule,
    ApplicationMatcher,
    MatchConfidence,
    MatchResult,
)

__all__ = [
    "AliasRule",
    "ApplicationEntry",
    "ApplicationMatcher",
    "ApplicationRegistry",
    "ApplicationRoutingRule",
    "DisplayAudioMapping",
    "MatchConfidence",
    "MatchResult",
    "MatchedStream",
    "RouteAction",
    "RouteDecision",
    "RouteExecutor",
    "RouteReason",
    "RouterEngine",
    "UnmatchedStream",
]
