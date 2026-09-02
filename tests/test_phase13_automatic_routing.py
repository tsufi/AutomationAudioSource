from concurrent.futures import Future
from pathlib import Path

import pytest

from screen_audio_router.audio.backend import AudioObjectNotFoundError
from screen_audio_router.audio.models import AudioOutput, AudioStream, Availability
from screen_audio_router.config.schema import (
    AppConfig,
    ApplicationRule,
    AudioOutputIdentity,
    DisplayIdentity,
    DisplayMapping,
    RoutingMode,
)
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import ApplicationEntry, MatchedStream
from screen_audio_router.router.automatic_routing import AutomaticRoutingController
from screen_audio_router.router.engine import RouteExecutor
from screen_audio_router.router.matcher import MatchConfidence, MatchResult


def output(name, availability=Availability.AVAILABLE):
    return AudioOutput(name, name, availability)


def entry(display="pc", streams=(("1", "gc7"),), app_id="example"):
    window = WindowInfo("w", app_id, app_id, app_id.title(), 10, display, False, True, "normal")
    matched = tuple(
        MatchedStream(
            AudioStream(stream_id, process_binary=app_id, current_output=current),
            MatchResult(app_id, MatchConfidence.HIGH, "test", 90),
        )
        for stream_id, current in streams
    )
    return ApplicationEntry(
        app_id,
        app_id.title(),
        (window,),
        matched,
        window,
        False,
        display,
        tuple(sorted({current for _, current in streams})),
    )


def config(mode=RoutingMode.AUTO, enabled=True, configured=True):
    mappings = [
        DisplayMapping(DisplayIdentity("pc"), AudioOutputIdentity("gc7", "gc7")),
        DisplayMapping(DisplayIdentity("tv"), AudioOutputIdentity("hdmi", "hdmi")),
        DisplayMapping(DisplayIdentity("desk"), AudioOutputIdentity("gc7", "gc7")),
    ]
    rules = [ApplicationRule("example", "Example", mode)] if configured else []
    return AppConfig(enabled=enabled, display_mappings=mappings, applications=rules)


class Handle:
    def __init__(self, callback):
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class Scheduler:
    def __init__(self):
        self.handles = []

    def __call__(self, delay, callback):
        assert 0.15 <= delay <= 0.4
        handle = Handle(callback)
        self.handles.append(handle)
        return handle

    def run(self):
        pending, self.handles = self.handles, []
        for handle in pending:
            if not handle.cancelled:
                handle.callback()


class Worker:
    def submit(self, operation, *args):
        future = Future()
        try:
            future.set_result(operation(*args))
        except Exception as exc:  # noqa: BLE001 - mirrors an executor Future
            future.set_exception(exc)
        return future


class DeferredWorker:
    def __init__(self):
        self.jobs = []

    def submit(self, operation, *args):
        future = Future()
        self.jobs.append((future, operation, args))
        return future

    def run(self):
        future, operation, args = self.jobs.pop(0)
        future.set_result(operation(*args))


class Backend:
    def __init__(self, failing=()):
        self.moves = []
        self.failing = set(failing)

    def move_stream(self, stream_id, destination):
        if stream_id in self.failing:
            raise AudioObjectNotFoundError("gone")
        self.moves.append((stream_id, destination))


def controller(cfg=None, backend=None):
    scheduler = Scheduler()
    backend = backend or Backend()
    events = []
    ctl = AutomaticRoutingController(
        cfg or config(),
        RouteExecutor(backend),
        schedule=scheduler,
        worker=Worker(),
        event_sink=events.append,
    )
    return ctl, scheduler, backend, events


def synchronize(ctl, scheduler, app=None, outputs=None):
    ctl.update_snapshot((app or entry(),), tuple(outputs or (output("gc7"), output("hdmi"))))
    ctl.mark_ready()
    scheduler.run()


def test_startup_waits_for_readiness_then_reconciles_auto():
    ctl, scheduler, backend, _ = controller()
    ctl.update_snapshot((entry("tv"),), (output("gc7"), output("hdmi")))
    scheduler.run()
    assert backend.moves == []
    ctl.mark_ready()
    scheduler.run()
    assert backend.moves == [("1", "hdmi")]


@pytest.mark.parametrize("mode", [RoutingMode.IGNORE, RoutingMode.FIXED_DEVICE])
def test_non_auto_modes_never_runtime_route(mode):
    cfg = config(mode)
    if mode is RoutingMode.FIXED_DEVICE:
        cfg.applications[0].fixed_output_id = "hdmi"
    ctl, scheduler, backend, _ = controller(cfg)
    synchronize(ctl, scheduler, entry("tv"))
    assert backend.moves == []


def test_unknown_application_never_routes():
    ctl, scheduler, backend, _ = controller(config(configured=False))
    synchronize(ctl, scheduler, entry("tv"))
    assert backend.moves == []


def test_disabled_never_routes_and_enabling_alone_does_not_sweep():
    cfg = config(enabled=False)
    ctl, scheduler, backend, _ = controller(cfg)
    synchronize(ctl, scheduler, entry("tv"))
    cfg.enabled = True
    ctl.configuration_changed()
    scheduler.run()
    assert backend.moves == []
    ctl.relevant_event("example")
    scheduler.run()
    assert backend.moves == [("1", "hdmi")]


def test_display_change_routes_both_directions():
    ctl, scheduler, backend, _ = controller()
    synchronize(ctl, scheduler, entry("tv"))
    ctl.update_snapshot((entry("pc", (("1", "hdmi"),)),), (output("gc7"), output("hdmi")))
    scheduler.run()
    assert backend.moves == [("1", "hdmi"), ("1", "gc7")]


def test_same_destination_and_already_correct_do_not_move():
    ctl, scheduler, backend, _ = controller()
    synchronize(ctl, scheduler, entry("pc"))
    ctl.update_snapshot((entry("desk"),), (output("gc7"), output("hdmi")))
    scheduler.run()
    assert backend.moves == []


def test_unmapped_and_missing_or_unavailable_destination_do_not_move():
    ctl, scheduler, backend, events = controller()
    synchronize(ctl, scheduler, entry("unmapped"))
    ctl.update_snapshot((entry("tv"),), (output("gc7"),))
    scheduler.run()
    ctl.update_snapshot(
        (entry("tv"),), (output("gc7"), output("hdmi", Availability.UNAVAILABLE))
    )
    scheduler.run()
    assert backend.moves == []
    assert {event.decision.reason.value for event in events} >= {"display_unmapped", "destination_missing"}


def test_destination_reappearance_and_stream_appearance_route():
    ctl, scheduler, backend, _ = controller()
    synchronize(ctl, scheduler, entry("tv", ()), (output("gc7"),))
    ctl.update_snapshot((entry("tv", (("9", "gc7"),)),), (output("gc7"), output("hdmi")))
    scheduler.run()
    assert backend.moves == [("9", "hdmi")]


def test_multiple_streams_moves_only_wrong_stream():
    ctl, scheduler, backend, _ = controller()
    synchronize(ctl, scheduler, entry("tv", (("right", "hdmi"), ("wrong", "gc7"))))
    assert backend.moves == [("wrong", "hdmi")]


def test_rapid_events_coalesce_to_latest_display():
    ctl, scheduler, backend, _ = controller()
    ctl.update_snapshot((entry("pc", (("1", "hdmi"),)),), (output("gc7"), output("hdmi")))
    ctl.mark_ready()
    ctl.update_snapshot((entry("tv", (("1", "gc7"),)),), (output("gc7"), output("hdmi")))
    scheduler.run()
    assert backend.moves == [("1", "hdmi")]


@pytest.mark.parametrize("new_mode", [RoutingMode.IGNORE, RoutingMode.FIXED_DEVICE])
def test_mode_change_invalidates_queued_auto(new_mode):
    cfg = config()
    ctl, scheduler, backend, events = controller(cfg)
    ctl.update_snapshot((entry("tv"),), (output("gc7"), output("hdmi")))
    ctl.mark_ready()
    cfg.applications[0].mode = new_mode
    cfg.applications[0].fixed_output_id = "gc7" if new_mode is RoutingMode.FIXED_DEVICE else None
    ctl.configuration_changed("example")
    scheduler.run()
    assert backend.moves == []
    assert not [event for event in events if event.kind == "requested"]


def test_global_disable_and_application_exit_cancel_pending_work():
    cfg = config()
    ctl, scheduler, backend, _ = controller(cfg)
    ctl.update_snapshot((entry("tv"),), (output("gc7"), output("hdmi")))
    ctl.mark_ready()
    cfg.enabled = False
    ctl.configuration_changed()
    ctl.update_snapshot((), (output("gc7"), output("hdmi")))
    scheduler.run()
    assert backend.moves == []


def test_manual_operation_suppresses_runtime_events_and_invalidates_queue():
    ctl, scheduler, backend, _ = controller()
    ctl.update_snapshot((entry("tv"),), (output("gc7"), output("hdmi")))
    ctl.mark_ready()
    ctl.begin_manual("example")
    scheduler.run()
    ctl.relevant_event("example")
    scheduler.run()
    assert backend.moves == []
    ctl.end_manual("example")
    scheduler.run()
    assert backend.moves == []
    ctl.relevant_event("example")
    scheduler.run()
    assert backend.moves == [("1", "hdmi")]


def test_duplicate_snapshot_and_post_move_refresh_cause_no_loop():
    ctl, scheduler, backend, _ = controller()
    synchronize(ctl, scheduler, entry("tv"))
    ctl.update_snapshot((entry("tv"),), (output("gc7"), output("hdmi")))
    scheduler.run()
    ctl.update_snapshot((entry("tv", (("1", "hdmi"),)),), (output("gc7"), output("hdmi")))
    scheduler.run()
    assert backend.moves == [("1", "hdmi")]


def test_partial_failure_is_recorded_without_rollback_or_retry():
    ctl, scheduler, backend, events = controller(backend=Backend(failing=("bad",)))
    synchronize(ctl, scheduler, entry("tv", (("ok", "gc7"), ("bad", "gc7"))))
    scheduler.run()
    assert backend.moves == [("ok", "hdmi")]
    assert events[-1].kind == "partial_failure"
    assert scheduler.handles == []


def test_stop_cancels_pending_and_rejects_future_events():
    ctl, scheduler, backend, _ = controller()
    ctl.update_snapshot((entry("tv"),), (output("gc7"), output("hdmi")))
    ctl.mark_ready()
    ctl.stop()
    scheduler.run()
    ctl.relevant_event("example")
    scheduler.run()
    assert backend.moves == []


@pytest.mark.parametrize("change", ["display", "stream", "destination", "mode"])
def test_stale_decision_is_discarded_before_execution(change):
    scheduler = Scheduler()
    worker = DeferredWorker()
    backend = Backend()
    events = []
    cfg = config()
    ctl = AutomaticRoutingController(
        cfg,
        RouteExecutor(backend),
        schedule=scheduler,
        worker=worker,
        event_sink=events.append,
    )
    ctl.update_snapshot((entry("tv"),), (output("gc7"), output("hdmi")))
    ctl.mark_ready()
    scheduler.run()
    if change == "display":
        ctl.update_snapshot((entry("pc"),), (output("gc7"), output("hdmi")))
    elif change == "stream":
        ctl.update_snapshot((entry("tv", ()),), (output("gc7"), output("hdmi")))
    elif change == "destination":
        ctl.update_snapshot((entry("tv"),), (output("gc7"),))
    else:
        cfg.applications[0].mode = RoutingMode.IGNORE
        ctl.configuration_changed("example")
    worker.run()
    assert backend.moves == []
    assert any(event.kind == "stale" for event in events)


def test_event_during_inflight_is_coalesced_and_serialized_after_completion():
    scheduler = Scheduler()
    worker = DeferredWorker()
    backend = Backend()
    ctl = AutomaticRoutingController(
        config(), RouteExecutor(backend), schedule=scheduler, worker=worker
    )
    ctl.update_snapshot((entry("tv"),), (output("gc7"), output("hdmi")))
    ctl.mark_ready()
    scheduler.run()
    ctl.update_snapshot((entry("pc", (("1", "hdmi"),)),), (output("gc7"), output("hdmi")))
    scheduler.run()
    assert len(worker.jobs) == 1
    worker.run()
    scheduler.run()
    assert len(worker.jobs) == 1
    worker.run()
    assert backend.moves == [("1", "gc7")]


def test_no_default_sink_or_card_profile_calls_exist():
    source = __import__(
        "screen_audio_router.router.automatic_routing", fromlist=["AutomaticRoutingController"]
    )
    text = Path(source.__file__).read_text(encoding="utf-8")
    assert "set_default" not in text
    assert "set_card_profile" not in text
