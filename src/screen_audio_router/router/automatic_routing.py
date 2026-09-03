"""Continuous, conservative AUTO routing coordination."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import Protocol

from screen_audio_router.audio.backend import AudioBackend
from screen_audio_router.audio.models import AudioOutput
from screen_audio_router.config.resolver import RoutingConfigResolver
from screen_audio_router.config.schema import AppConfig, RoutingMode
from screen_audio_router.router.application_registry import ApplicationEntry, UnmatchedStream
from screen_audio_router.router.decisions import (
    DisplayAudioMapping,
    RouteAction,
    RouteDecision,
    RouteExecutionResult,
)
from screen_audio_router.router.engine import RouteExecutor, RouterEngine


class CancelHandle(Protocol):
    def cancel(self) -> None: ...


Schedule = Callable[[float, Callable[[], None]], CancelHandle]


@dataclass(frozen=True, slots=True)
class AutomaticRoutingEvent:
    kind: str
    application_id: str
    message: str
    decision: RouteDecision | None = None
    execution: RouteExecutionResult | None = None


@dataclass(frozen=True, slots=True)
class DisplayReevaluationResult:
    connector: str | None
    requested: tuple[str, ...]
    skipped: tuple[str, ...]


def _thread_timer(delay: float, callback: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


class AutomaticRoutingController:
    """Debounces runtime state and executes only fresh, configured AUTO moves."""

    def __init__(
        self,
        config: AppConfig,
        executor: RouteExecutor,
        *,
        engine: RouterEngine | None = None,
        debounce_ms: int = 250,
        schedule: Schedule = _thread_timer,
        event_sink: Callable[[AutomaticRoutingEvent], None] | None = None,
        refresh_requested: Callable[[], None] | None = None,
        worker=None,
        display_mappings: Callable[[], tuple[DisplayAudioMapping, ...]] | None = None,
    ) -> None:
        self.config = config
        self.executor = executor
        self.engine = engine or RouterEngine()
        self.debounce_seconds = debounce_ms / 1000
        self._schedule = schedule
        self._event_sink = event_sink or (lambda event: None)
        self._refresh_requested = refresh_requested or (lambda: None)
        self._worker = worker or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="auto-routing"
        )
        self._owns_worker = worker is None
        self._display_mappings = display_mappings
        self._lock = threading.RLock()
        self._entries: dict[str, ApplicationEntry] = {}
        self._outputs: tuple[AudioOutput, ...] = ()
        self._ambiguous_apps: set[str] = set()
        self._fingerprints: dict[str, tuple] = {}
        self._generation: dict[str, int] = {}
        self._timers: dict[str, CancelHandle] = {}
        self._inflight: set[str] = set()
        self._rerun_after_inflight: set[str] = set()
        self._manual_apps: set[str] = set()
        self._ready = False
        self._stopped = False

    @classmethod
    def from_backend(cls, config: AppConfig, backend: AudioBackend, execution_lock=None, **kwargs):
        return cls(config, RouteExecutor(backend, execution_lock), **kwargs)

    @property
    def ready(self) -> bool:
        return self._ready

    def update_snapshot(
        self,
        entries: tuple[ApplicationEntry, ...],
        outputs: tuple[AudioOutput, ...],
        unmatched: tuple[UnmatchedStream, ...] = (),
    ) -> None:
        with self._lock:
            if self._stopped:
                return
            old_ids = set(self._entries)
            self._entries = {entry.application_id: entry for entry in entries}
            self._outputs = outputs
            self._ambiguous_apps = {
                app_id
                for app_id in self._entries
                if any(app_id.casefold() in item.reason.casefold() for item in unmatched)
            }
            current = {
                app_id: self._fingerprint(entry, outputs) for app_id, entry in self._entries.items()
            }
            changed = {
                app_id
                for app_id, value in current.items()
                if self._fingerprints.get(app_id) != value
            }
            removed = old_ids - set(current)
            self._fingerprints = current
            for app_id in removed:
                self.invalidate(app_id)
            if self._ready:
                for app_id in changed:
                    self._queue_if_auto(app_id)

    def mark_ready(self) -> None:
        with self._lock:
            if self._ready or self._stopped:
                return
            self._ready = True
            for app_id in self._entries:
                self._queue_if_auto(app_id)

    def relevant_event(self, application_id: str) -> None:
        with self._lock:
            if self._ready and not self._stopped:
                self._queue_if_auto(application_id)

    def reevaluate_display(self, connector: str | None) -> DisplayReevaluationResult:
        """Queue only configured AUTO applications currently using one connector."""
        requested: list[str] = []
        skipped: list[str] = []
        with self._lock:
            for application_id, entry in self._entries.items():
                if connector is None or entry.current_display != connector:
                    continue
                if (
                    self.config.has_application_rule(application_id)
                    and self.config.application_rule(application_id).mode is RoutingMode.AUTO
                ):
                    self.invalidate(application_id)
                    if self._ready and not self._stopped:
                        self._queue_if_auto(application_id)
                        requested.append(application_id)
                    else:
                        skipped.append(application_id)
                else:
                    skipped.append(application_id)
        return DisplayReevaluationResult(connector, tuple(requested), tuple(skipped))

    def configuration_changed(
        self, application_id: str | None = None, *, reevaluate: bool = False
    ) -> None:
        with self._lock:
            targets = [application_id] if application_id else list(self._generation)
            for target in targets:
                if target:
                    self.invalidate(target)
            if reevaluate and self._ready:
                for target in [application_id] if application_id else list(self._entries):
                    if target:
                        self._queue_if_auto(target)

    def begin_manual(self, application_id: str) -> None:
        with self._lock:
            self._manual_apps.add(application_id)
            self.invalidate(application_id)

    def end_manual(self, application_id: str) -> None:
        with self._lock:
            self._manual_apps.discard(application_id)
            self.invalidate(application_id)

    def invalidate(self, application_id: str) -> None:
        with self._lock:
            self._generation[application_id] = self._generation.get(application_id, 0) + 1
            timer = self._timers.pop(application_id, None)
            if timer is not None:
                timer.cancel()

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            for app_id in set(self._generation) | set(self._entries):
                self._generation[app_id] = self._generation.get(app_id, 0) + 1
        if self._owns_worker:
            self._worker.shutdown(wait=True, cancel_futures=True)

    def _queue_if_auto(self, application_id: str) -> None:
        if (
            application_id in self._manual_apps
            or not self.config.enabled
            or not self.config.has_application_rule(application_id)
        ):
            return
        if self.config.application_rule(application_id).mode is not RoutingMode.AUTO:
            return
        self.invalidate(application_id)
        generation = self._generation[application_id]
        self._timers[application_id] = self._schedule(
            self.debounce_seconds,
            lambda: self._evaluate(application_id, generation),
        )

    def _evaluate(self, application_id: str, generation: int) -> None:
        with self._lock:
            self._timers.pop(application_id, None)
            if not self._valid_generation(application_id, generation):
                return
            if application_id in self._inflight:
                self._rerun_after_inflight.add(application_id)
                return
            application = self._entries.get(application_id)
            if application is None:
                return
            resolver = RoutingConfigResolver(self.config)
            decision = self.engine.decide(
                application,
                resolver.application_rule(application_id),
                self._display_mappings() if self._display_mappings else resolver.display_mappings(),
                self._outputs,
                enabled=resolver.enabled,
                ambiguous_audio=application_id in self._ambiguous_apps,
            )
            if decision.action is not RouteAction.MOVE:
                if decision.reason.value not in {"already_correct", "no_streams"}:
                    self._emit(
                        "skipped",
                        application_id,
                        f"AUTO skipped: {decision.reason.value}",
                        decision,
                    )
                return
            self._inflight.add(application_id)
            self._emit("requested", application_id, "AUTO route requested", decision)
            future = self._worker.submit(self._execute_fresh, application_id, generation, decision)
            future.add_done_callback(lambda item: self._completed(application_id, item))

    def _execute_fresh(
        self, application_id: str, generation: int, decision: RouteDecision
    ) -> tuple[str, RouteDecision, RouteExecutionResult | None]:
        operation_lock = self.executor.execution_lock
        with operation_lock if operation_lock is not None else nullcontext():
            with self._lock:
                if not self._decision_is_fresh(application_id, generation, decision):
                    return "stale", decision, None
            try:
                current_output = getattr(self.executor.backend, "get_stream_output", None)
                if callable(current_output):
                    remaining = tuple(
                        stream_id
                        for stream_id in decision.stream_ids
                        if current_output(stream_id) != decision.desired_output
                    )
                    if not remaining:
                        return "already_correct", decision, RouteExecutionResult(decision, ())
                    decision = replace(decision, stream_ids=remaining)
                return "executed", decision, self.executor.execute(decision)
            except Exception:  # noqa: BLE001 - backend failures must not terminate runtime
                return "failed", decision, None

    def _completed(self, application_id: str, future: Future) -> None:
        try:
            status, decision, result = future.result()
        except Exception as exc:  # noqa: BLE001
            self._emit("failure", application_id, f"AUTO route failure: {exc}")
        else:
            if status == "stale":
                self._emit("stale", application_id, "AUTO stale decision discarded", decision)
            elif status == "already_correct":
                self._emit(
                    "skipped",
                    application_id,
                    "AUTO backend state already correct",
                    decision,
                )
            elif status == "failed" or result is None:
                self._emit("failure", application_id, "AUTO route failure", decision)
            else:
                succeeded = tuple(item for item in result.streams if item.succeeded)
                failed = tuple(item for item in result.streams if not item.succeeded)
                kind = (
                    "partial_failure"
                    if succeeded and failed
                    else "failure"
                    if failed
                    else "success"
                )
                message = {
                    "success": "AUTO route success",
                    "partial_failure": "AUTO route partial failure",
                    "failure": "AUTO route failure",
                }[kind]
                self._emit(kind, application_id, message, decision, result)
                self._refresh_requested()
        finally:
            with self._lock:
                self._inflight.discard(application_id)
                if application_id in self._rerun_after_inflight:
                    self._rerun_after_inflight.discard(application_id)
                    self._queue_if_auto(application_id)

    def _decision_is_fresh(
        self, application_id: str, generation: int, decision: RouteDecision
    ) -> bool:
        if not self._valid_generation(application_id, generation):
            return False
        application = self._entries.get(application_id)
        if application is None or application.current_display != decision.controlling_display:
            return False
        if decision.desired_output not in {
            output.id for output in self._outputs if output.availability.value != "unavailable"
        }:
            return False
        current_streams = {item.stream.stream_id for item in application.streams}
        return set(decision.stream_ids).issubset(current_streams)

    def _valid_generation(self, application_id: str, generation: int) -> bool:
        return (
            not self._stopped
            and self._ready
            and self.config.enabled
            and application_id not in self._manual_apps
            and self._generation.get(application_id) == generation
            and self.config.has_application_rule(application_id)
            and self.config.application_rule(application_id).mode is RoutingMode.AUTO
        )

    def _emit(
        self,
        kind: str,
        application_id: str,
        message: str,
        decision: RouteDecision | None = None,
        execution: RouteExecutionResult | None = None,
    ) -> None:
        self._event_sink(AutomaticRoutingEvent(kind, application_id, message, decision, execution))

    @staticmethod
    def _fingerprint(application: ApplicationEntry, outputs: tuple[AudioOutput, ...]) -> tuple:
        return (
            application.current_display,
            tuple(
                (item.stream.stream_id, item.stream.current_output) for item in application.streams
            ),
            tuple((output.id, output.availability.value) for output in outputs),
        )
