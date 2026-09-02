from dataclasses import replace

from screen_audio_router.audio.backend import AudioObjectNotFoundError
from screen_audio_router.audio.models import AudioOutput, AudioStream, Availability
from screen_audio_router.config.schema import RoutingMode
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import ApplicationEntry, MatchedStream
from screen_audio_router.router.decisions import (
    ApplicationRoutingRule,
    DisplayAudioMapping,
    RouteAction,
    RouteReason,
)
from screen_audio_router.router.engine import RouteExecutor, RouterEngine
from screen_audio_router.router.matcher import MatchConfidence, MatchResult


def output(name):
    return AudioOutput(name, name, Availability.AVAILABLE)


def application(display="display-a", outputs=("gc7",), stream_ids=None, *, audio_only=False):
    stream_ids = stream_ids or tuple(str(index + 1) for index in range(len(outputs)))
    window = None if audio_only else WindowInfo(
        "window", "example", "example", "Example", 10, display, False, True, "normal"
    )
    streams = tuple(
        MatchedStream(
            AudioStream(stream_id, application_name="example", current_output=current),
            MatchResult("example", MatchConfidence.HIGH, "test", 85),
        )
        for stream_id, current in zip(stream_ids, outputs, strict=True)
    )
    return ApplicationEntry(
        "example",
        "Example",
        () if window is None else (window,),
        streams,
        window,
        False,
        None if window is None else display,
        tuple(sorted(set(outputs))),
        audio_only,
    )


def decide(app=None, mode=RoutingMode.AUTO, fixed=None, mappings=None, outputs=None, **kwargs):
    return RouterEngine().decide(
        app or application(),
        ApplicationRoutingRule("example", mode, fixed),
        mappings or [DisplayAudioMapping("display-a", "gc7")],
        outputs or [output("gc7"), output("hdmi")],
        **kwargs,
    )


def test_global_router_disabled():
    decision = decide(enabled=False)
    assert (decision.action, decision.reason) == (RouteAction.NO_ACTION, RouteReason.GLOBAL_DISABLED)


def test_ignore_always_does_nothing():
    decision = decide(mode=RoutingMode.IGNORE, fixed="hdmi")
    assert (decision.action, decision.reason) == (RouteAction.NO_ACTION, RouteReason.MODE_IGNORE)


def test_fixed_device_already_correct():
    decision = decide(mode=RoutingMode.FIXED_DEVICE, fixed="gc7")
    assert (decision.action, decision.reason) == (RouteAction.NO_ACTION, RouteReason.ALREADY_CORRECT)


def test_fixed_device_needs_move_and_ignores_display_mapping():
    app = application(display="tv", outputs=("hdmi",))
    decision = decide(
        app, RoutingMode.FIXED_DEVICE, "gc7", [DisplayAudioMapping("tv", "hdmi")]
    )
    assert decision.action is RouteAction.MOVE
    assert decision.reason is RouteReason.FIXED_DEVICE
    assert decision.desired_output == "gc7"


def test_auto_mapped_and_unmapped_display():
    mapped = decide(application(outputs=("gc7",)), mappings=[DisplayAudioMapping("display-a", "hdmi")])
    assert (mapped.action, mapped.desired_output) == (RouteAction.MOVE, "hdmi")
    unmapped = decide(mappings=[DisplayAudioMapping("other", "gc7")])
    assert (unmapped.action, unmapped.reason) == (RouteAction.NO_ACTION, RouteReason.DISPLAY_UNMAPPED)


def test_two_displays_mapped_to_same_output_need_no_move():
    mappings = [DisplayAudioMapping("display-a", "gc7"), DisplayAudioMapping("display-b", "gc7")]
    first = decide(application("display-a", ("gc7",)), mappings=mappings)
    second = decide(application("display-b", ("gc7",)), mappings=mappings)
    assert first.reason is second.reason is RouteReason.ALREADY_CORRECT


def test_pc_to_tv_and_tv_to_pc_decisions():
    mappings = [DisplayAudioMapping("pc", "gc7"), DisplayAudioMapping("tv", "hdmi")]
    to_tv = decide(application("tv", ("gc7",)), mappings=mappings)
    to_pc = decide(application("pc", ("hdmi",)), mappings=mappings)
    assert (to_tv.action, to_tv.desired_output) == (RouteAction.MOVE, "hdmi")
    assert (to_pc.action, to_pc.desired_output) == (RouteAction.MOVE, "gc7")


def test_no_streams_and_ambiguous_audio_are_safe():
    no_streams = replace(application(), streams=(), current_audio_outputs=())
    assert decide(no_streams).reason is RouteReason.NO_STREAMS
    ambiguous = decide(no_streams, ambiguous_audio=True)
    assert (ambiguous.action, ambiguous.reason) == (RouteAction.UNMATCHED, RouteReason.AMBIGUOUS_AUDIO)


def test_audio_only_auto_has_no_controlling_window():
    decision = decide(application(audio_only=True))
    assert decision.reason is RouteReason.NO_CONTROLLING_WINDOW


def test_audio_only_fixed_device_is_valid():
    decision = decide(application(outputs=("hdmi",), audio_only=True), RoutingMode.FIXED_DEVICE, "gc7")
    assert (decision.action, decision.desired_output) == (RouteAction.MOVE, "gc7")


def test_multiple_streams_all_correct_or_only_incorrect_selected():
    correct = decide(application(outputs=("gc7", "gc7"), stream_ids=("1", "2")))
    assert correct.reason is RouteReason.ALREADY_CORRECT
    mixed = decide(application(outputs=("gc7", "hdmi"), stream_ids=("1", "2")))
    assert mixed.stream_ids == ("2",)


def test_destination_missing_has_no_fallback():
    decision = decide(mode=RoutingMode.FIXED_DEVICE, fixed="missing", outputs=[output("gc7")])
    assert decision.action is RouteAction.DEVICE_MISSING
    assert decision.reason is RouteReason.DESTINATION_MISSING
    assert decision.desired_output == "missing"
    assert decision.stream_ids == ()


def test_auto_re_evaluation_after_fixed_uses_current_tv_mapping():
    app = application("tv", ("gc7",))
    fixed = decide(app, RoutingMode.FIXED_DEVICE, "gc7", [DisplayAudioMapping("tv", "hdmi")])
    automatic = decide(app, RoutingMode.AUTO, mappings=[DisplayAudioMapping("tv", "hdmi")])
    assert fixed.reason is RouteReason.ALREADY_CORRECT
    assert (automatic.action, automatic.desired_output) == (RouteAction.MOVE, "hdmi")


def test_mapping_removed_and_window_removed_are_safe():
    removed_mapping = decide(mappings=[DisplayAudioMapping("other", "gc7")])
    assert removed_mapping.reason is RouteReason.DISPLAY_UNMAPPED
    no_window = replace(application(), windows=(), controlling_window=None, current_display=None)
    assert decide(no_window).reason is RouteReason.NO_CONTROLLING_WINDOW


def test_invalid_fixed_rule_is_explainable_error():
    decision = decide(mode=RoutingMode.FIXED_DEVICE)
    assert (decision.action, decision.reason) == (RouteAction.ERROR, RouteReason.INSUFFICIENT_STATE)


class FakeBackend:
    def __init__(self, failing=()):
        self.failing = set(failing)
        self.moves = []

    def move_stream(self, stream_id, output_id):
        if stream_id in self.failing:
            raise AudioObjectNotFoundError("stream disappeared")
        self.moves.append((stream_id, output_id))


def test_executor_runs_only_move_and_reports_disappearing_stream():
    decision = decide(application(outputs=("hdmi", "hdmi"), stream_ids=("1", "2")))
    backend = FakeBackend(failing=("2",))
    result = RouteExecutor(backend).execute(decision)
    assert backend.moves == [("1", "gc7")]
    assert result.streams[0].succeeded is True
    assert result.streams[1].succeeded is False
    no_action = decide(application())
    assert RouteExecutor(backend).execute(no_action).streams == ()
