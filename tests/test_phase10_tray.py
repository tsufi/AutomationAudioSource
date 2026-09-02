import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from screen_audio_router.audio.models import AudioOutput, AudioStream, Availability
from screen_audio_router.config.schema import AppConfig, ApplicationRule, RoutingMode
from screen_audio_router.gui.lifecycle import DesktopApplication
from screen_audio_router.gui.main_window import MainWindow
from screen_audio_router.gui.tray import SystemTrayController
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import ApplicationEntry, MatchedStream
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
        self.starts = 0
        self.stops = 0
        self.refreshes = 0
        self.dbus_owned = False

    def start(self):
        self.starts += 1
        self.dbus_owned = True

    def stop(self):
        self.stops += 1
        self.dbus_owned = False

    def refresh_audio(self):
        self.refreshes += 1


def runtime_entry():
    window = WindowInfo(
        "window-one",
        resource_class="app.one",
        resource_name="app-one",
        output_name="DP-1",
        active=True,
        normal_window=True,
        window_type="normal",
    )
    stream = AudioStream("stream-one", application_name="app.one", current_output="sink.one")
    matched = MatchedStream(
        stream, MatchResult("app.one", MatchConfidence.EXACT, "test", 100)
    )
    return ApplicationEntry(
        "app.one", "App One", (window,), (matched,), window, True, "DP-1", ("sink.one",)
    )


def output():
    return AudioOutput("sink.one", "Speakers", Availability.AVAILABLE)


def make_desktop(qt_app, *, enabled=True, tray_available=True):
    config = AppConfig(enabled=enabled)
    store = MemoryStore()
    runtime = FakeRuntime()
    window = MainWindow(config, store, runtime)
    desktop = DesktopApplication(
        qt_app,
        config,
        store,
        window=window,
        tray_available=tray_available,
    )
    desktop.tray.show_close_message = lambda: None
    return desktop, config, store, runtime


@pytest.mark.parametrize("enabled", [True, False])
def test_tray_controller_has_required_menu_and_reflects_config(qt_app, enabled):
    tray = SystemTrayController(enabled, available=False)
    texts = [action.text() for action in tray.menu.actions()]
    assert "Open" in texts
    assert "Router Enabled" in texts
    assert "Quit" in texts
    assert tray.enabled_action.isCheckable()
    assert tray.enabled_action.isChecked() is enabled


@pytest.mark.parametrize(("initial", "requested"), [(True, False), (False, True)])
def test_tray_enabled_toggle_persists_and_synchronizes_without_routing(
    qt_app, initial, requested
):
    desktop, config, store, runtime = make_desktop(qt_app, enabled=initial)
    desktop.tray.enabled_action.setChecked(requested)
    qt_app.processEvents()
    assert config.enabled is requested
    assert store.saved[-1]["enabled"] is requested
    assert desktop.window.enabled_checkbox.isChecked() is requested
    assert runtime.backend.moves == []
    desktop.shutdown()


def test_main_window_enabled_change_updates_tray(qt_app):
    desktop, config, store, runtime = make_desktop(qt_app)
    desktop.window.enabled_checkbox.setChecked(False)
    qt_app.processEvents()
    assert config.enabled is False
    assert store.saved[-1]["enabled"] is False
    assert not desktop.tray.enabled_action.isChecked()
    assert runtime.backend.moves == []
    desktop.shutdown()


def test_close_with_tray_hides_without_stopping_or_reinitializing(qt_app):
    desktop, _, _, runtime = make_desktop(qt_app)
    original_window = desktop.window
    desktop.start()
    assert runtime.starts == 1 and runtime.dbus_owned
    desktop.window.close()
    qt_app.processEvents()
    assert desktop.window.isHidden()
    assert runtime.stops == 0 and runtime.dbus_owned
    desktop.tray.open_action.trigger()
    qt_app.processEvents()
    assert desktop.window is original_window
    assert not desktop.window.isHidden()
    assert runtime.starts == 1 and runtime.dbus_owned
    desktop.shutdown()


def test_runtime_updates_while_hidden_without_routing(qt_app):
    desktop, config, _, runtime = make_desktop(qt_app)
    config.applications = [ApplicationRule("app.one", "App One", RoutingMode.AUTO)]
    desktop.start()
    desktop.window.close()
    runtime.snapshot_changed.emit((runtime_entry(),), (output(),))
    qt_app.processEvents()
    assert desktop.window.model.rowCount() == 1
    assert desktop.tray.status_action.text() == "Applications detected: 1"
    assert runtime.backend.moves == []
    assert runtime.dbus_owned
    desktop.shutdown()


def test_tray_quit_stops_workers_and_releases_dbus(qt_app):
    desktop, _, _, runtime = make_desktop(qt_app)
    desktop.start()
    desktop.tray.quit_action.trigger()
    qt_app.processEvents()
    assert desktop._shutdown is True
    assert runtime.stops >= 1
    assert not runtime.dbus_owned
    assert not desktop.tray.tray_icon.isVisible()


def test_sigint_uses_real_shutdown_path(qt_app):
    desktop, _, _, runtime = make_desktop(qt_app)
    desktop.start()
    desktop.window.close()
    desktop.handle_sigint(2, None)
    assert desktop._shutdown is True
    assert runtime.stops >= 1
    assert not runtime.dbus_owned


def test_no_tray_close_quits_instead_of_leaving_invisible_process(qt_app):
    desktop, _, _, runtime = make_desktop(qt_app, tray_available=False)
    desktop.start()
    desktop.window.close()
    qt_app.processEvents()
    assert desktop._shutdown is True
    assert runtime.stops >= 1
    assert not runtime.dbus_owned


def test_disabled_router_keeps_discovery_running(qt_app):
    desktop, _, _, runtime = make_desktop(qt_app, enabled=False)
    desktop.start()
    runtime.snapshot_changed.emit((runtime_entry(),), (output(),))
    qt_app.processEvents()
    assert runtime.starts == 1
    assert desktop.window.model.rowCount() == 1
    assert runtime.backend.moves == []
    desktop.shutdown()


def test_lifecycle_and_tray_modules_have_no_routing_execution_hooks():
    from pathlib import Path

    gui = Path(__file__).parents[1] / "src" / "screen_audio_router" / "gui"
    source = (gui / "lifecycle.py").read_text() + (gui / "tray.py").read_text()
    for forbidden in ("RouteExecutor", "ManualRoutingController", "move_stream", "pactl"):
        assert forbidden not in source
