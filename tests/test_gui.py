import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from screen_audio_router.audio.models import AudioOutput, AudioStream, Availability
from screen_audio_router.config.schema import (
    AppConfig,
    ApplicationRule,
    AudioOutputIdentity,
    DisplayIdentity,
    DisplayMapping,
    RoutingMode,
)
from screen_audio_router.gui.application_list import (
    ApplicationTableModel,
    ApplicationViewModel,
    decision_status,
)
from screen_audio_router.gui.main_window import MainWindow, configuration_error_message
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import ApplicationEntry, MatchedStream
from screen_audio_router.router.decisions import RouteAction, RouteDecision, RouteReason
from screen_audio_router.router.matcher import MatchConfidence, MatchResult


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class MemoryStore:
    def __init__(self):
        self.saved = []

    def save(self, config):
        config.validate()
        self.saved.append(config.to_dict())


class FakeRuntime(QObject):
    snapshot_changed = Signal(object, object)
    error_occurred = Signal(str)

    def __init__(self):
        super().__init__()
        self.backend = FakeBackend()
        self.started = False
        self.stopped = False
        self.refreshes = 0

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def refresh_audio(self):
        self.refreshes += 1


class FakeBackend:
    def __init__(self):
        self.moves = []

    def move_stream(self, stream_id, output_id):
        self.moves.append((stream_id, output_id))


def output(output_id="sink.one", name="Speakers"):
    return AudioOutput(output_id, name, Availability.AVAILABLE)


def entry(
    app_id="app.one",
    *,
    name="App One",
    screen="DP-1",
    current_output="sink.one",
    active=False,
    windows=True,
    streams=True,
    audio_only=False,
):
    window = WindowInfo(
        "window-1",
        resource_class=app_id,
        resource_name=name,
        output_name=screen,
        active=active,
        normal_window=True,
        window_type="normal",
    )
    stream = AudioStream("stream-1", application_name=app_id, current_output=current_output)
    matched = MatchedStream(stream, MatchResult(app_id, MatchConfidence.EXACT, "test", 100))
    return ApplicationEntry(
        app_id,
        name,
        (window,) if windows else (),
        (matched,) if streams else (),
        window if windows else None,
        active,
        screen if windows else None,
        (current_output,) if streams and current_output else (),
        audio_only,
    )


def configured(mode=RoutingMode.AUTO, fixed=None, enabled=True):
    config = AppConfig(enabled=enabled)
    config.applications = [ApplicationRule("app.one", "App One", mode, fixed_output_id=fixed)]
    config.display_mappings = [
        DisplayMapping(
            DisplayIdentity("DP-1", connector="DP-1"),
            AudioOutputIdentity("sink.one", "sink.one", "Speakers"),
        )
    ]
    return config


@pytest.mark.parametrize(
    ("runtime_entry", "screen", "audio"),
    [
        (entry(streams=False), "DP-1", "No Audio"),
        (entry(windows=False, audio_only=True), "No Window", "Speakers"),
        (entry(), "DP-1", "Speakers"),
    ],
)
def test_rows_preserve_window_and_audio_only_applications(runtime_entry, screen, audio):
    view_model = ApplicationViewModel(configured(), MemoryStore())
    view_model.update_runtime((runtime_entry,), (output(),))
    row = view_model.rows()[0]
    assert (row.screen, row.audio_output) == (screen, audio)


def test_rows_sort_active_then_windowed_then_audio_only_alphabetically():
    view_model = ApplicationViewModel(AppConfig(), MemoryStore())
    values = (
        entry("z", name="Zulu", windows=False, audio_only=True),
        entry("b", name="Beta"),
        entry("a", name="Alpha", active=True),
    )
    view_model.update_runtime(values, ())
    assert [row.application.application_id for row in view_model.rows()] == ["a", "b", "z"]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (RoutingMode.IGNORE, "Ignored"),
        (RoutingMode.AUTO, "Correct"),
        (RoutingMode.FIXED_DEVICE, "Correct"),
    ],
)
def test_all_modes_are_rendered_as_decisions(mode, expected):
    fixed = "sink.one" if mode is RoutingMode.FIXED_DEVICE else None
    view_model = ApplicationViewModel(configured(mode, fixed), MemoryStore())
    view_model.update_runtime((entry(),), (output(),))
    assert view_model.rows()[0].status == expected


def test_auto_shows_would_route_without_execution():
    view_model = ApplicationViewModel(configured(), MemoryStore())
    view_model.update_runtime((entry(current_output="sink.other"),), (output(),))
    row = view_model.rows()[0]
    assert row.status == "Would route to Speakers"
    assert row.decision.action is RouteAction.MOVE


def test_mode_edit_emits_user_request_without_widget_policy():
    view_model = ApplicationViewModel(configured(RoutingMode.IGNORE), MemoryStore())
    model = ApplicationTableModel(view_model)
    model.update_snapshot((entry(),), (output(),))
    requests = []
    model.mode_requested.connect(
        lambda application_id, mode: requests.append((application_id, mode))
    )
    assert model.setData(model.index(0, 3), "Auto") is True
    assert requests == [("app.one", RoutingMode.AUTO)]
    assert view_model.config.application_rule("app.one").mode is RoutingMode.IGNORE


def test_missing_saved_fixed_output_remains_visible_and_unavailable():
    view_model = ApplicationViewModel(
        configured(RoutingMode.FIXED_DEVICE, "sink.missing"), MemoryStore()
    )
    view_model.update_runtime((entry(),), (output(),))
    assert view_model.fixed_output_options("app.one")[-1] == (
        "sink.missing",
        "sink.missing (Unavailable)",
        False,
    )
    assert view_model.rows()[0].status == "Device missing"


def test_model_updates_live_fields_without_replacing_application():
    view_model = ApplicationViewModel(configured(), MemoryStore())
    model = ApplicationTableModel(view_model)
    model.update_snapshot((entry(),), (output(),))
    model.update_snapshot((entry(screen="HDMI-A-1", current_output="sink.two"),), (output(),))
    assert model.rowCount() == 1
    assert model.data(model.index(0, 1)) == "HDMI-A-1"
    assert model.data(model.index(0, 2)) == "sink.two"


def test_model_tracks_application_appearance_and_disappearance():
    model = ApplicationTableModel(ApplicationViewModel(AppConfig(), MemoryStore()))
    model.update_snapshot((entry(),), ())
    assert model.rowCount() == 1
    model.update_snapshot((), ())
    assert model.rowCount() == 0


@pytest.mark.parametrize(
    ("reason", "action", "expected"),
    [
        (RouteReason.MODE_IGNORE, RouteAction.NO_ACTION, "Ignored"),
        (RouteReason.ALREADY_CORRECT, RouteAction.NO_ACTION, "Correct"),
        (RouteReason.DISPLAY_UNMAPPED, RouteAction.NO_ACTION, "No display mapping"),
        (RouteReason.DESTINATION_MISSING, RouteAction.DEVICE_MISSING, "Device missing"),
        (RouteReason.NO_STREAMS, RouteAction.NO_ACTION, "No audio"),
        (RouteReason.NO_CONTROLLING_WINDOW, RouteAction.NO_ACTION, "No window"),
        (RouteReason.AMBIGUOUS_AUDIO, RouteAction.UNMATCHED, "Audio match ambiguous"),
    ],
)
def test_friendly_decision_statuses(reason, action, expected):
    decision = RouteDecision("app", RoutingMode.AUTO, action, reason)
    assert decision_status(decision, {}) == expected


def test_main_window_receives_snapshots_and_shows_details(qt_app):
    runtime = FakeRuntime()
    window = MainWindow(configured(), MemoryStore(), runtime)
    assert window.start_runtime() is True
    runtime.snapshot_changed.emit((entry(),), (output(),))
    qt_app.processEvents()
    assert window.model.rowCount() == 1
    assert window.detail_id.text() == "app.one"
    assert "1 window" in window.detail_counts.text()
    window.close()
    assert runtime.stopped is True


def test_main_window_enabled_control_persists(qt_app):
    store = MemoryStore()
    window = MainWindow(configured(), store, FakeRuntime())
    window.enabled_checkbox.setChecked(False)
    qt_app.processEvents()
    assert window.view_model.config.enabled is False
    assert len(store.saved) == 1
    window.close()


def test_gui_explicit_auto_routes_in_worker_and_refreshes_real_snapshot(qt_app):
    runtime = FakeRuntime()
    value = configured(RoutingMode.IGNORE)
    window = MainWindow(value, MemoryStore(), runtime)
    runtime.snapshot_changed.emit((entry(current_output="sink.other"),), (output(),))
    qt_app.processEvents()
    completed = QSignalSpy(window.routing_service.completed)
    assert window.model.setData(window.model.index(0, 3), "Auto") is True
    assert completed.count() or completed.wait(1000)
    qt_app.processEvents()
    assert runtime.backend.moves == [("stream-1", "sink.one")]
    assert runtime.refreshes == 1
    assert "Routed App One to Speakers" in window.operation_message.text()

    runtime.snapshot_changed.emit((entry(current_output="sink.one"),), (output(),))
    qt_app.processEvents()
    assert window.model.row(0).status == "Correct"
    window.close()


def test_configuration_error_is_visible_and_recoverable():
    message = configuration_error_message(ValueError("bad JSON"))
    assert "bad JSON" in message
    assert "not overwritten" in message
    assert "start Screen Audio Router again" in message


def test_gui_boundary_contains_no_audio_mutation_or_subprocess_parsing():
    source_root = Path(__file__).parents[1] / "src" / "screen_audio_router" / "gui"
    source = "\n".join(path.read_text() for path in source_root.glob("*.py"))
    for forbidden in ("RouteExecutor", "move_stream", "subprocess", "pactl"):
        assert forbidden not in source


def test_kwin_and_polling_runtime_has_no_manual_routing_hook():
    runtime_source = (
        Path(__file__).parents[1]
        / "src"
        / "screen_audio_router"
        / "gui"
        / "runtime.py"
    ).read_text()
    for forbidden in ("ManualRoutingController", "RouteExecutor", "execute(", "move_stream"):
        assert forbidden not in runtime_source
