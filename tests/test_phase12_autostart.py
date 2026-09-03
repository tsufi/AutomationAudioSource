import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from screen_audio_router.autostart import (
    MANAGED_MARKER,
    AutostartError,
    AutostartManager,
    AutostartState,
    autostart_path,
)
from screen_audio_router.config.schema import AppConfig, ApplicationRule, RoutingMode
from screen_audio_router.gui.lifecycle import DesktopApplication
from screen_audio_router.gui.main_window import MainWindow
from screen_audio_router.main import startup_arguments


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def manager(tmp_path, *, available=True):
    return AutostartManager(
        tmp_path / "autostart" / "screen-audio-router.desktop",
        executable_available=lambda command: f"/usr/bin/{command}" if available else None,
    )


def test_xdg_and_fallback_autostart_paths():
    assert autostart_path({"XDG_CONFIG_HOME": "/tmp/example"}) == Path(
        "/tmp/example/autostart/screen-audio-router.desktop"
    )
    assert autostart_path({}) == Path.home() / ".config/autostart/screen-audio-router.desktop"


def test_absent_status_and_idempotent_disable(tmp_path):
    value = manager(tmp_path)
    assert value.status().state is AutostartState.DISABLED
    assert value.disable().state is AutostartState.DISABLED
    assert not value.path.parent.exists()


def test_enable_creates_valid_managed_desktop_entry_atomically(tmp_path):
    value = manager(tmp_path)
    status = value.enable()
    content = value.path.read_text()
    assert status.enabled
    assert value.path.parent.is_dir()
    assert "[Desktop Entry]" in content
    assert "Type=Application" in content
    assert "Name=Screen Audio Router" in content
    assert "Exec=screen-audio-router --autostart" in content
    assert "Icon=screen-audio-router" in content
    assert "Terminal=false" in content
    assert MANAGED_MARKER in content
    assert "/run/media/" not in content
    assert "/home/tsufi" not in content
    assert list(value.path.parent.iterdir()) == [value.path]


def test_safe_exec_escaping_for_arguments_with_spaces(tmp_path):
    value = AutostartManager(
        tmp_path / "entry.desktop",
        ("screen-audio-router", "--label", "Living Room"),
        executable_available=lambda _: "/usr/bin/screen-audio-router",
    )
    assert 'Exec=screen-audio-router --label "Living Room"' in value.desktop_entry()


def test_enable_rejects_unavailable_launch_target_without_file(tmp_path):
    value = manager(tmp_path, available=False)
    with pytest.raises(AutostartError, match="not available on PATH"):
        value.enable()
    assert not value.path.exists()


def test_unrelated_target_is_never_overwritten_or_removed(tmp_path):
    value = manager(tmp_path)
    value.path.parent.mkdir(parents=True)
    original = "[Desktop Entry]\nName=Unrelated\n"
    value.path.write_text(original)
    assert value.status().state is AutostartState.ERROR
    with pytest.raises(AutostartError):
        value.enable()
    with pytest.raises(AutostartError):
        value.disable()
    assert value.path.read_text() == original


def test_disable_removes_only_managed_entry(tmp_path):
    value = manager(tmp_path)
    unrelated = value.path.parent / "other.desktop"
    value.enable()
    unrelated.write_text("keep")
    assert value.disable().state is AutostartState.DISABLED
    assert not value.path.exists()
    assert unrelated.read_text() == "keep"


def test_managed_entry_with_wrong_exec_is_reported_as_error(tmp_path):
    value = manager(tmp_path)
    value.path.parent.mkdir(parents=True)
    value.path.write_text(
        "[Desktop Entry]\nType=Application\nName=Screen Audio Router\n"
        "Exec=wrong\nTerminal=false\n" + MANAGED_MARKER + "\n"
    )
    assert value.status().state is AutostartState.ERROR


def test_autostart_argument_is_removed_from_qt_arguments():
    arguments, automatic = startup_arguments(["--autostart"])
    assert arguments == [] and automatic
    assert startup_arguments([]) == ([], False)


class MemoryStore:
    def __init__(self):
        self.saved = []

    def save(self, config):
        self.saved.append(config.to_dict())


class FakeBackend:
    def __init__(self):
        self.moves = []

    def move_stream(self, stream_id, output_id):
        self.moves.append((stream_id, output_id))


class FakeRuntime(QObject):
    snapshot_changed = Signal(object, object)
    error_occurred = Signal(str)

    def __init__(self):
        super().__init__()
        self.backend = FakeBackend()
        self.starts = 0
        self.stops = 0
        self.dbus_owned = False

    def start(self):
        self.starts += 1
        self.dbus_owned = True

    def stop(self):
        self.stops += 1
        self.dbus_owned = False

    def refresh_audio(self):
        pass


def desktop(qt_app, tmp_path, *, tray=True, mode=RoutingMode.AUTO, enabled=True):
    config = AppConfig(enabled=enabled)
    config.applications = [
        ApplicationRule(
            "com.edde746.plezy",
            "Plezy",
            mode,
            fixed_output_id="sink.gc7" if mode is RoutingMode.FIXED_DEVICE else None,
        )
    ]
    store = MemoryStore()
    runtime = FakeRuntime()
    window = MainWindow(config, store, runtime, autostart_manager=manager(tmp_path))
    app = DesktopApplication(qt_app, config, store, window=window, tray_available=tray)
    app.tray.show_close_message = lambda: None
    return app, config, store, runtime


def test_manual_launch_shows_window_and_autostart_launch_hides_with_runtime(qt_app, tmp_path):
    manual, _, _, _ = desktop(qt_app, tmp_path / "manual")
    manual.start()
    assert manual.window.isVisible()
    manual.shutdown()
    automatic, _, _, runtime = desktop(qt_app, tmp_path / "automatic")
    automatic.start(autostart=True)
    assert automatic.window.isHidden()
    assert runtime.starts == 1 and runtime.dbus_owned
    assert runtime.backend.moves == []
    automatic.shutdown()


@pytest.mark.parametrize("mode", [RoutingMode.AUTO, RoutingMode.FIXED_DEVICE])
def test_autostart_never_routes_configured_applications(qt_app, tmp_path, mode):
    app, _, _, runtime = desktop(qt_app, tmp_path / mode.value, mode=mode)
    app.start(autostart=True)
    assert runtime.backend.moves == []
    app.shutdown()


def test_autostart_without_tray_shows_window(qt_app, tmp_path):
    app, _, _, runtime = desktop(qt_app, tmp_path, tray=False)
    app.start(autostart=True)
    assert app.window.isVisible()
    assert runtime.dbus_owned
    app.shutdown()


def test_checkbox_uses_filesystem_and_does_not_change_router_state(qt_app, tmp_path):
    app, config, store, runtime = desktop(qt_app, tmp_path, enabled=False)
    checkbox = app.window.autostart_checkbox
    assert not checkbox.isChecked()
    checkbox.setChecked(True)
    qt_app.processEvents()
    assert manager(tmp_path).status().enabled
    assert config.enabled is False
    assert store.saved == []
    assert runtime.backend.moves == []
    checkbox.setChecked(False)
    qt_app.processEvents()
    assert not manager(tmp_path).status().enabled
    messages = [event.message for event in app.window.diagnostic_history.events]
    assert "Autostart enabled" in messages and "Autostart disabled" in messages
    app.shutdown()


def test_ui_failure_rolls_back_and_records_diagnostic_error(qt_app, tmp_path):
    unavailable = manager(tmp_path, available=False)
    config = AppConfig()
    runtime = FakeRuntime()
    window = MainWindow(config, MemoryStore(), runtime, autostart_manager=unavailable)
    window.autostart_checkbox.setChecked(True)
    qt_app.processEvents()
    assert not window.autostart_checkbox.isChecked()
    assert not unavailable.path.exists()
    assert any(
        "Autostart operation failed" in event.message
        and event.severity.value == "error"
        for event in window.diagnostic_history.events
    )
    window.close()


def test_diagnostics_reflects_filesystem_autostart_state(qt_app, tmp_path):
    app, _, _, _ = desktop(qt_app, tmp_path)
    assert app.window._diagnostic_snapshot().autostart_health == "Disabled"
    manager(tmp_path).enable()
    assert app.window._diagnostic_snapshot().autostart_health == "Enabled"
    app.shutdown()


def test_autostart_module_has_no_routing_or_systemd_hooks():
    source = (Path(__file__).parents[1] / "src/screen_audio_router/autostart.py").read_text()
    for forbidden in (
        "RouteExecutor",
        "ManualRoutingController",
        "move_stream",
        "systemctl",
        "/home/tsufi",
        "/run/media/tsufi/Trash/AutomationAudioSource",
    ):
        assert forbidden not in source
