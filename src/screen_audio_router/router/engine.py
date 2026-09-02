"""Pure routing decisions and isolated explicit execution."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import nullcontext

from screen_audio_router.audio.backend import AudioBackend, AudioBackendError
from screen_audio_router.audio.models import AudioOutput, Availability
from screen_audio_router.config.schema import RoutingMode
from screen_audio_router.router.application_registry import ApplicationEntry
from screen_audio_router.router.decisions import (
    ApplicationRoutingRule,
    DisplayAudioMapping,
    RouteAction,
    RouteDecision,
    RouteExecutionResult,
    RouteReason,
    StreamExecutionResult,
)


class RouterEngine:
    def decide(
        self,
        application: ApplicationEntry,
        rule: ApplicationRoutingRule,
        mappings: Iterable[DisplayAudioMapping],
        available_outputs: Iterable[AudioOutput],
        *,
        enabled: bool = True,
        ambiguous_audio: bool = False,
    ) -> RouteDecision:
        base = {
            "application_id": application.application_id,
            "mode": rule.mode,
            "current_outputs": application.current_audio_outputs,
            "controlling_display": application.current_display,
        }
        if not enabled:
            return RouteDecision(**base, action=RouteAction.NO_ACTION, reason=RouteReason.GLOBAL_DISABLED)
        if rule.mode is RoutingMode.IGNORE:
            return RouteDecision(**base, action=RouteAction.NO_ACTION, reason=RouteReason.MODE_IGNORE)

        desired_output: str | None
        intent_reason: RouteReason
        if rule.mode is RoutingMode.FIXED_DEVICE:
            desired_output = rule.fixed_output_id
            intent_reason = RouteReason.FIXED_DEVICE
            if not desired_output:
                return RouteDecision(
                    **base,
                    action=RouteAction.ERROR,
                    reason=RouteReason.INSUFFICIENT_STATE,
                    details="FIXED_DEVICE requires fixed_output_id",
                )
        elif rule.mode is RoutingMode.AUTO:
            if application.controlling_window is None or not application.current_display:
                return RouteDecision(
                    **base,
                    action=RouteAction.NO_ACTION,
                    reason=RouteReason.NO_CONTROLLING_WINDOW,
                )
            mapped = {mapping.display_id: mapping.output_id for mapping in mappings}
            desired_output = mapped.get(application.current_display)
            intent_reason = RouteReason.AUTO_DISPLAY_MAPPING
            if desired_output is None:
                return RouteDecision(
                    **base,
                    action=RouteAction.NO_ACTION,
                    reason=RouteReason.DISPLAY_UNMAPPED,
                )
        else:
            return RouteDecision(
                **base,
                action=RouteAction.ERROR,
                reason=RouteReason.INSUFFICIENT_STATE,
                details=f"unsupported routing mode: {rule.mode}",
            )

        output_ids = {
            output.id
            for output in available_outputs
            if output.availability is not Availability.UNAVAILABLE
        }
        if desired_output not in output_ids:
            return RouteDecision(
                **base,
                action=RouteAction.DEVICE_MISSING,
                reason=RouteReason.DESTINATION_MISSING,
                desired_output=desired_output,
                destination_available=False,
            )
        if not application.streams:
            return RouteDecision(
                **base,
                action=RouteAction.UNMATCHED if ambiguous_audio else RouteAction.NO_ACTION,
                reason=RouteReason.AMBIGUOUS_AUDIO if ambiguous_audio else RouteReason.NO_STREAMS,
                desired_output=desired_output,
                destination_available=True,
            )
        streams_to_move = tuple(
            matched.stream.stream_id
            for matched in application.streams
            if matched.stream.current_output != desired_output
        )
        if not streams_to_move:
            return RouteDecision(
                **base,
                action=RouteAction.NO_ACTION,
                reason=RouteReason.ALREADY_CORRECT,
                desired_output=desired_output,
                destination_available=True,
                details=f"routing intent: {intent_reason.value}",
            )
        return RouteDecision(
            **base,
            action=RouteAction.MOVE,
            reason=intent_reason,
            desired_output=desired_output,
            stream_ids=streams_to_move,
            destination_available=True,
        )


class RouteExecutor:
    """Executes only a caller-supplied MOVE decision; it contains no policy."""

    def __init__(self, backend: AudioBackend, execution_lock=None) -> None:
        self.backend = backend
        self.execution_lock = execution_lock

    def execute(self, decision: RouteDecision) -> RouteExecutionResult:
        if decision.action is not RouteAction.MOVE or not decision.desired_output:
            return RouteExecutionResult(decision, ())
        results: list[StreamExecutionResult] = []
        context = self.execution_lock if self.execution_lock is not None else nullcontext()
        with context:
            for stream_id in decision.stream_ids:
                try:
                    self.backend.move_stream(stream_id, decision.desired_output)
                except AudioBackendError as exc:
                    results.append(StreamExecutionResult(stream_id, False, str(exc)))
                    continue
                results.append(StreamExecutionResult(stream_id, True))
        return RouteExecutionResult(decision, tuple(results))
