import os

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
    MatcherAliasConfig,
    RoutingMode,
)
from screen_audio_router.gui.application_list import (
    ApplicationDiscoveryState,
    ApplicationViewModel,
)
from screen_audio_router.gui.main_window import MainWindow
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import ApplicationEntry, MatchedStream
from screen_audio_router.router.manual_routing import ManualRoutingController
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


class RecordingBackend:
    def __init__(self):
        self.moves = []

    def move_stream(self, stream_id, output_id):
        self.moves.append((stream_id, output_id))


class FakeRuntime(QObject):
    snapshot_changed = Signal(object, object)
    error_occurred = Signal(str)

    def __init__(self):
        super().__init__()
        self.backend = RecordingBackend()
        self.refreshes = 0

    def start(self):
        pass

    def stop(self):
        pass

    def refresh_audio(self):
        self.refreshes += 1


def output():
    return AudioOutput("sink.one", "Speakers", Availability.AVAILABLE)


def entry(app_id="spotify", *, windows=True, streams=True, active=False):
    window = WindowInfo(
        f"window-{app_id}",
        resource_class=app_id,
        resource_name=app_id,
        output_name="display.one",
        active=active,
        normal_window=True,
        window_type="normal",
    )
    stream = AudioStream(
        f"stream-{app_id}", application_name=app_id, current_output="sink.other"
    )
    matched = MatchedStream(
        stream, MatchResult(app_id, MatchConfidence.EXACT, "test", 100)
    )
    return ApplicationEntry(
        app_id,
        app_id.title(),
        (window,) if windows else (),
        (matched,) if streams else (),
        window if windows else None,
        active,
        "display.one" if windows else None,
        ("sink.other",) if streams else (),
        not windows,
    )


def config_with_rule(mode=RoutingMode.AUTO, fixed_output_id=None):
    value = AppConfig()
    value.applications = [
        ApplicationRule("spotify", "Spotify", mode, fixed_output_id=fixed_output_id)
    ]
    value.display_mappings = [
        DisplayMapping(
            DisplayIdentity("display.one", connector="DP-1"),
            AudioOutputIdentity("sink.one", "sink.one", "Speakers"),
        )
    ]
    return value


def row_for(config, runtime_entry=None):
    model = ApplicationViewModel(config)
    model.update_runtime((runtime_entry or entry(),), (output(),))
    return model.rows()[0]


def test_unknown_runtime_application_is_unconfigured_safe_ignore_without_persistence():
    value = AppConfig()
    row = row_for(value)
    assert row.discovery_state is ApplicationDiscoveryState.UNCONFIGURED
    assert row.mode is RoutingMode.IGNORE
    assert value.applications == []


@pytest.mark.parametrize(
    ("mode", "fixed"),
    [
        (RoutingMode.AUTO, None),
        (RoutingMode.FIXED_DEVICE, "sink.one"),
        (RoutingMode.IGNORE, None),
    ],
)
def test_any_explicit_persisted_rule_is_configured(mode, fixed):
    assert row_for(config_with_rule(mode, fixed)).is_configured


@pytest.mark.parametrize("mode", [RoutingMode.AUTO, RoutingMode.IGNORE])
def test_user_configuration_persists_and_marks_unknown_app_configured(mode):
    value = AppConfig()
    store = MemoryStore()
    backend = RecordingBackend()
    controller = ManualRoutingController(value, store, backend)
    controller.change_mode(entry(), (output(),), mode)
    assert value.has_application_rule("spotify")
    assert row_for(value).is_configured
    assert len(store.saved) == 1
    if mode is RoutingMode.IGNORE:
        assert backend.moves == []


def test_user_fixed_device_configuration_persists_and_routes_as_explicit_action():
    value = AppConfig()
    store = MemoryStore()
    backend = RecordingBackend()
    controller = ManualRoutingController(value, store, backend)
    controller.select_fixed_output(entry(), (output(),), "sink.one")
    assert value.application_rule("spotify").mode is RoutingMode.FIXED_DEVICE
    assert row_for(value).is_configured
    assert backend.moves == [("stream-spotify", "sink.one")]


def test_reset_removes_only_rule_and_never_moves_audio():
    value = config_with_rule(RoutingMode.FIXED_DEVICE, "sink.one")
    value.enabled = False
    value.matcher_aliases = [MatcherAliasConfig("plezy", process_binaries=("mpv",))]
    value.applications.append(ApplicationRule("other", "Other", RoutingMode.IGNORE))
    original_mapping = value.display_mappings[0]
    store = MemoryStore()
    backend = RecordingBackend()
    result = ManualRoutingController(value, store, backend).reset_configuration(
        entry(), (output(),)
    )
    assert not value.has_application_rule("spotify")
    assert value.has_application_rule("other")
    assert row_for(value).mode is RoutingMode.IGNORE
    assert value.display_mappings == [original_mapping]
    assert value.matcher_aliases == [MatcherAliasConfig("plezy", process_binaries=("mpv",))]
    assert value.enabled is False
    assert backend.moves == []
    assert result.execution_result is None


@pytest.mark.parametrize(
    ("runtime_entry", "screen", "audio"),
    [
        (entry(windows=False), "No Window", "sink.other"),
        (entry(streams=False), "display.one", "No Audio"),
    ],
)
def test_audio_only_and_window_only_discovery_are_unconfigured(runtime_entry, screen, audio):
    row = row_for(AppConfig(), runtime_entry)
    assert row.discovery_state is ApplicationDiscoveryState.UNCONFIGURED
    assert row.screen == screen
    assert row.audio_output == audio


def test_unconfigured_disappear_and_reappear_never_creates_rule():
    value = AppConfig()
    model = ApplicationViewModel(value)
    model.update_runtime((entry(),), ())
    assert model.rows()
    model.update_runtime((), ())
    assert model.rows() == []
    model.update_runtime((entry(),), ())
    assert not value.has_application_rule("spotify")


def test_configured_app_rule_survives_disappear_and_is_restored_on_return():
    value = config_with_rule()
    model = ApplicationViewModel(value)
    model.update_runtime((), ())
    assert value.has_application_rule("spotify")
    model.update_runtime((entry(),), (output(),))
    assert model.rows()[0].is_configured


def test_gui_discovery_notifies_once_and_does_not_route_or_save(qt_app):
    value = AppConfig()
    store = MemoryStore()
    runtime = FakeRuntime()
    window = MainWindow(value, store, runtime)
    runtime.snapshot_changed.emit((entry(),), (output(),))
    qt_app.processEvents()
    assert "New application discovered: Spotify" in window.statusBar().currentMessage()
    runtime.snapshot_changed.emit((), (output(),))
    runtime.snapshot_changed.emit((entry(),), (output(),))
    qt_app.processEvents()
    assert "New application discovered" not in window.statusBar().currentMessage()
    assert store.saved == []
    assert runtime.backend.moves == []
    assert "NEW" in window.model.data(window.model.index(0, 0))
    assert "Not configured" in window.detail_configuration.text()
    window.close()


def test_gui_startup_snapshot_with_configured_auto_does_not_route(qt_app):
    runtime = FakeRuntime()
    window = MainWindow(config_with_rule(), MemoryStore(), runtime)
    runtime.snapshot_changed.emit((entry(),), (output(),))
    qt_app.processEvents()
    assert runtime.backend.moves == []
    assert window.model.row(0).is_configured
    window.close()


def test_gui_reset_updates_state_without_audio_movement(qt_app):
    value = config_with_rule(RoutingMode.FIXED_DEVICE, "sink.one")
    runtime = FakeRuntime()
    window = MainWindow(value, MemoryStore(), runtime)
    runtime.snapshot_changed.emit((entry(),), (output(),))
    qt_app.processEvents()
    completed = QSignalSpy(window.routing_service.completed)
    window.reset_button.click()
    assert completed.count() or completed.wait(1000)
    qt_app.processEvents()
    assert not value.has_application_rule("spotify")
    assert window.model.row(0).mode is RoutingMode.IGNORE
    assert not window.model.row(0).is_configured
    assert runtime.backend.moves == []
    window.close()


def test_empty_state_is_helpful(qt_app):
    runtime = FakeRuntime()
    window = MainWindow(AppConfig(), MemoryStore(), runtime)
    runtime.snapshot_changed.emit((), ())
    qt_app.processEvents()
    assert not window.empty_state.isHidden()
    assert "Open an application" in window.empty_state.text()
    window.close()
