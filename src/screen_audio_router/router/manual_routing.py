"""Explicit, user-initiated application routing operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from screen_audio_router.audio.backend import AudioBackend
from screen_audio_router.audio.models import AudioOutput
from screen_audio_router.config import ConfigurationStore
from screen_audio_router.config.resolver import RoutingConfigResolver
from screen_audio_router.config.schema import AppConfig, RoutingMode
from screen_audio_router.router.application_registry import ApplicationEntry
from screen_audio_router.router.decisions import (
    DisplayAudioMapping,
    RouteAction,
    RouteDecision,
    RouteExecutionResult,
    RouteReason,
)
from screen_audio_router.router.engine import RouteExecutor, RouterEngine


@dataclass(frozen=True, slots=True)
class RoutingOperationResult:
    application_id: str
    decision: RouteDecision
    execution_result: RouteExecutionResult | None
    success: bool
    message: str
    streams_moved: tuple[str, ...] = ()
    streams_failed: tuple[str, ...] = ()


class ManualRoutingController:
    """Owns persistence, policy evaluation, and execution for direct user actions only."""

    def __init__(
        self,
        config: AppConfig,
        store: ConfigurationStore,
        backend: AudioBackend,
        engine: RouterEngine | None = None,
        execution_lock=None,
        display_mappings: Callable[[], tuple[DisplayAudioMapping, ...]] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.engine = engine or RouterEngine()
        self.executor = RouteExecutor(backend, execution_lock)
        self._display_mappings = display_mappings

    def change_mode(
        self,
        application: ApplicationEntry,
        outputs: tuple[AudioOutput, ...],
        mode: RoutingMode,
    ) -> RoutingOperationResult:
        existing = self.config.application_rule(application.application_id)
        fixed_output_id = existing.fixed_output_id
        if mode is RoutingMode.FIXED_DEVICE and not fixed_output_id:
            if not outputs:
                raise ValueError("Fixed Device requires an available audio output")
            fixed_output_id = outputs[0].id
        if mode is not RoutingMode.FIXED_DEVICE:
            fixed_output_id = None
        self.config.set_application_rule(
            replace(
                existing,
                name=application.display_name,
                mode=mode,
                fixed_output_id=fixed_output_id,
            )
        )
        self.store.save(self.config)
        return self._evaluate_and_execute(application, outputs)

    def select_fixed_output(
        self,
        application: ApplicationEntry,
        outputs: tuple[AudioOutput, ...],
        output_id: str,
    ) -> RoutingOperationResult:
        existing = self.config.application_rule(application.application_id)
        self.config.set_application_rule(
            replace(
                existing,
                name=application.display_name,
                mode=RoutingMode.FIXED_DEVICE,
                fixed_output_id=output_id,
            )
        )
        self.store.save(self.config)
        return self._evaluate_and_execute(application, outputs)

    def apply_now(
        self, application: ApplicationEntry, outputs: tuple[AudioOutput, ...]
    ) -> RoutingOperationResult:
        return self._evaluate_and_execute(application, outputs)

    def set_enabled(self, enabled: bool) -> None:
        self.config.enabled = enabled
        self.store.save(self.config)

    def reset_configuration(
        self, application: ApplicationEntry, outputs: tuple[AudioOutput, ...]
    ) -> RoutingOperationResult:
        """Forget one explicit rule without applying any audio operation."""
        self.config.remove_application_rule(application.application_id)
        self.store.save(self.config)
        resolver = RoutingConfigResolver(self.config)
        decision = self.engine.decide(
            application,
            resolver.application_rule(application.application_id),
            self._resolved_display_mappings(resolver),
            outputs,
            enabled=resolver.enabled,
        )
        return RoutingOperationResult(
            application.application_id,
            decision,
            None,
            True,
            f"Reset {application.display_name} to the safe unconfigured Ignore state; audio was not changed",
        )

    def _evaluate_and_execute(
        self, application: ApplicationEntry, outputs: tuple[AudioOutput, ...]
    ) -> RoutingOperationResult:
        resolver = RoutingConfigResolver(self.config)
        decision = self.engine.decide(
            application,
            resolver.application_rule(application.application_id),
            self._resolved_display_mappings(resolver),
            outputs,
            enabled=resolver.enabled,
        )
        execution = self.executor.execute(decision) if decision.action is RouteAction.MOVE else None
        return _operation_result(application, outputs, decision, execution)

    def _resolved_display_mappings(
        self, resolver: RoutingConfigResolver
    ) -> tuple[DisplayAudioMapping, ...]:
        return self._display_mappings() if self._display_mappings else resolver.display_mappings()


def _operation_result(
    application: ApplicationEntry,
    outputs: tuple[AudioOutput, ...],
    decision: RouteDecision,
    execution: RouteExecutionResult | None,
) -> RoutingOperationResult:
    names = {output.id: output.description for output in outputs}
    destination = names.get(decision.desired_output or "", decision.desired_output or "output")
    if execution is not None:
        moved = tuple(item.stream_id for item in execution.streams if item.succeeded)
        failed = tuple(item.stream_id for item in execution.streams if not item.succeeded)
        if failed:
            first_error = next(
                (item.error for item in execution.streams if not item.succeeded and item.error),
                None,
            )
            summary = f"{len(moved)} moved, {len(failed)} failed"
            if moved:
                message = f"Partially routed {application.display_name} to {destination}: {summary}"
            else:
                message = f"Could not route {application.display_name} to {destination}: {summary}"
            if first_error:
                message = f"{message} ({first_error})"
        else:
            message = f"Routed {application.display_name} to {destination}"
        return RoutingOperationResult(
            application.application_id,
            decision,
            execution,
            not failed,
            message,
            moved,
            failed,
        )
    messages = {
        RouteReason.GLOBAL_DISABLED: "Router is disabled",
        RouteReason.MODE_IGNORE: f"{application.display_name} is ignored; audio was not changed",
        RouteReason.ALREADY_CORRECT: f"Already using {destination}",
        RouteReason.DISPLAY_UNMAPPED: (
            f"No display mapping for {decision.controlling_display or 'current display'}"
        ),
        RouteReason.DESTINATION_MISSING: f"{destination} is unavailable",
        RouteReason.NO_STREAMS: "No audio streams to route",
        RouteReason.NO_CONTROLLING_WINDOW: "No controlling window",
        RouteReason.AMBIGUOUS_AUDIO: "Audio match is ambiguous",
    }
    success = decision.action is RouteAction.NO_ACTION and decision.reason in {
        RouteReason.MODE_IGNORE,
        RouteReason.ALREADY_CORRECT,
        RouteReason.NO_STREAMS,
    }
    return RoutingOperationResult(
        application.application_id,
        decision,
        None,
        success,
        messages.get(decision.reason, decision.details or "Routing was not applied"),
    )
