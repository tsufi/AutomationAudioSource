import pytest

from screen_audio_router.audio.backend import AudioCommandError, AudioObjectNotFoundError
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
from screen_audio_router.router.decisions import RouteAction, RouteReason
from screen_audio_router.router.manual_routing import ManualRoutingController
from screen_audio_router.router.matcher import MatchConfidence, MatchResult


class MemoryStore:
    def __init__(self):
        self.saved = []

    def save(self, config):
        config.validate()
        self.saved.append(config.to_dict())


class RecordingBackend:
    def __init__(self, failing=()):
        self.failing = set(failing)
        self.moves = []
        self.default_changes = []

    def move_stream(self, stream_id, output_id):
        if stream_id in self.failing:
            raise AudioObjectNotFoundError("stream disappeared")
        self.moves.append((stream_id, output_id))


class FailingBackend(RecordingBackend):
    def move_stream(self, stream_id, output_id):
        raise AudioCommandError("backend rejected move")


def output(output_id="sink.gc7", description="Sound Blaster GC7"):
    return AudioOutput(output_id, description, Availability.AVAILABLE)


def application(
    *,
    display="DP-1",
    stream_outputs=("sink.hdmi",),
    app_id="plezy",
    name="Plezy",
):
    window = WindowInfo(
        "window-1",
        resource_class=app_id,
        resource_name=name,
        output_name=display,
        active=True,
        normal_window=True,
        window_type="normal",
    )
    streams = tuple(
        MatchedStream(
            AudioStream(str(index), application_name=app_id, current_output=current),
            MatchResult(app_id, MatchConfidence.EXACT, "test", 100),
        )
        for index, current in enumerate(stream_outputs, 100)
    )
    return ApplicationEntry(
        app_id,
        name,
        (window,),
        streams,
        window,
        True,
        display,
        tuple(sorted(set(stream_outputs))),
    )


def config(mode=RoutingMode.IGNORE, fixed=None, mapped=True, enabled=True):
    value = AppConfig(enabled=enabled)
    value.applications = [ApplicationRule("plezy", "Plezy", mode, fixed_output_id=fixed)]
    if mapped:
        value.display_mappings = [
            DisplayMapping(
                DisplayIdentity("DP-1", connector="DP-1"),
                AudioOutputIdentity("sink.gc7", "sink.gc7", "Sound Blaster GC7"),
            )
        ]
    return value


def controller(value=None, backend=None):
    value = value or config()
    backend = backend or RecordingBackend()
    store = MemoryStore()
    return ManualRoutingController(value, store, backend), store, backend


def test_ignore_to_fixed_saves_and_routes_only_selected_application():
    control, store, backend = controller()
    result = control.select_fixed_output(application(), (output(),), "sink.gc7")
    assert store.saved[-1]["applications"][0]["mode"] == "fixed"
    assert backend.moves == [("100", "sink.gc7")]
    assert result.streams_moved == ("100",)


def test_fixed_already_correct_does_not_move():
    control, _, backend = controller(config(RoutingMode.FIXED_DEVICE, "sink.gc7"))
    result = control.apply_now(application(stream_outputs=("sink.gc7",)), (output(),))
    assert backend.moves == []
    assert result.decision.reason is RouteReason.ALREADY_CORRECT


def test_fixed_missing_output_is_saved_without_fallback():
    value = config()
    control, store, backend = controller(value)
    result = control.select_fixed_output(application(), (output(),), "sink.missing")
    assert value.application_rule("plezy").fixed_output_id == "sink.missing"
    assert len(store.saved) == 1
    assert backend.moves == []
    assert result.decision.action is RouteAction.DEVICE_MISSING
    assert "unavailable" in result.message


def test_explicit_auto_on_mapped_display_saves_and_moves():
    control, store, backend = controller()
    result = control.change_mode(application(), (output(),), RoutingMode.AUTO)
    assert len(store.saved) == 1
    assert backend.moves == [("100", "sink.gc7")]
    assert result.success is True


def test_explicit_auto_on_unmapped_display_saves_without_move():
    control, store, backend = controller(config(mapped=False))
    result = control.change_mode(application(), (output(),), RoutingMode.AUTO)
    assert len(store.saved) == 1
    assert backend.moves == []
    assert result.decision.reason is RouteReason.DISPLAY_UNMAPPED


def test_ignore_saves_without_audio_movement():
    control, store, backend = controller(config(RoutingMode.AUTO))
    result = control.change_mode(application(), (output(),), RoutingMode.IGNORE)
    assert len(store.saved) == 1
    assert backend.moves == []
    assert result.decision.reason is RouteReason.MODE_IGNORE


def test_global_enabled_change_never_moves_audio():
    control, store, backend = controller(config(RoutingMode.AUTO))
    control.set_enabled(False)
    assert len(store.saved) == 1
    assert backend.moves == []


def test_apply_now_executes_move_but_not_no_action():
    control, _, backend = controller(config(RoutingMode.AUTO))
    moved = control.apply_now(application(), (output(),))
    correct = control.apply_now(application(stream_outputs=("sink.gc7",)), (output(),))
    assert backend.moves == [("100", "sink.gc7")]
    assert moved.execution_result is not None
    assert correct.execution_result is None


@pytest.mark.parametrize("runtime_event", ["output_changed", "poll", "activated", "stream_added"])
def test_runtime_refresh_events_do_not_invoke_controller(runtime_event):
    del runtime_event
    _, _, backend = controller(config(RoutingMode.AUTO))
    # Runtime snapshots only feed the view model; the controller has no event subscription.
    assert backend.moves == []


def test_multiple_streams_move_only_streams_not_already_correct():
    control, _, backend = controller(config(RoutingMode.AUTO))
    result = control.apply_now(
        application(stream_outputs=("sink.gc7", "sink.hdmi", "sink.gc7")), (output(),)
    )
    assert backend.moves == [("101", "sink.gc7")]
    assert result.streams_moved == ("101",)


def test_unmatched_stream_is_never_part_of_selected_application_decision():
    selected = application()
    unrelated = AudioStream("999", application_name="standalone-mpv", current_output="sink.hdmi")
    control, _, backend = controller(config(RoutingMode.AUTO))
    control.apply_now(selected, (output(),))
    assert unrelated.stream_id not in {stream_id for stream_id, _ in backend.moves}


def test_disappearing_stream_produces_visible_partial_result():
    backend = RecordingBackend(failing=("101",))
    control, _, _ = controller(config(RoutingMode.AUTO), backend)
    result = control.apply_now(application(stream_outputs=("sink.hdmi", "sink.hdmi")), (output(),))
    assert result.success is False
    assert result.streams_moved == ("100",)
    assert result.streams_failed == ("101",)
    assert "1 moved, 1 failed" in result.message


def test_backend_execution_error_is_typed_and_does_not_escape():
    control, _, _ = controller(config(RoutingMode.AUTO), FailingBackend())
    result = control.apply_now(application(), (output(),))
    assert result.success is False
    assert result.streams_failed == ("100",)


def test_missing_fixed_output_can_be_applied_when_it_returns():
    value = config(RoutingMode.FIXED_DEVICE, "sink.returned")
    control, _, backend = controller(value)
    missing = control.apply_now(application(), (output(),))
    returned = control.apply_now(application(), (output("sink.returned", "TV HDMI"),))
    assert missing.decision.reason is RouteReason.DESTINATION_MISSING
    assert value.application_rule("plezy").fixed_output_id == "sink.returned"
    assert returned.streams_moved == ("100",)
    assert backend.moves == [("100", "sink.returned")]


def test_friendly_output_description_is_used_in_result():
    control, _, _ = controller(config(RoutingMode.AUTO))
    result = control.apply_now(application(), (output(),))
    assert "Sound Blaster GC7" in result.message
    assert "sink.gc7" not in result.message


def test_manual_routing_only_uses_per_stream_move_api():
    control, _, backend = controller(config(RoutingMode.AUTO))
    control.apply_now(application(), (output(),))
    assert backend.moves == [("100", "sink.gc7")]
    assert backend.default_changes == []


def test_rule_matching_metadata_survives_mode_change():
    value = config()
    original = value.application_rule("plezy")
    original.window_match.resource_class = "com.edde746.plezy"
    control, _, _ = controller(value)
    control.change_mode(application(), (output(),), RoutingMode.AUTO)
    assert value.application_rule("plezy").window_match.resource_class == "com.edde746.plezy"


def test_window_move_changes_decision_without_automatic_execution():
    value = config(RoutingMode.AUTO)
    value.display_mappings.append(
        DisplayMapping(
            DisplayIdentity("TV", connector="TV"),
            AudioOutputIdentity("sink.tv", "sink.tv", "Samsung HDMI"),
        )
    )
    control, _, backend = controller(value)
    moved_window = application(display="TV", stream_outputs=("sink.gc7",))
    # Merely replacing the live application snapshot performs no controller call.
    assert moved_window.current_display == "TV"
    assert backend.moves == []
    result = control.apply_now(moved_window, (output(), output("sink.tv", "Samsung HDMI")))
    assert result.streams_moved == ("100",)
    assert backend.moves == [("100", "sink.tv")]
