import json
import os
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from screen_audio_router import __version__
from screen_audio_router.gui.about import AboutDialog
from screen_audio_router.single_instance import InstanceGuard, instance_lock_path


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_canonical_release_version_is_available():
    assert __version__ == "1.0.0"
    metadata = json.loads(
        (Path(__file__).parents[1] / "kwin/screen-audio-router/metadata.json").read_text()
    )
    assert metadata["KPlugin"]["Version"] == __version__


def test_about_dialog_has_release_and_platform_information(qt_app):
    dialog = AboutDialog()
    text = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Screen Audio Router" in text
    assert f"Version {__version__}" in text
    assert "KDE Plasma / Wayland" in text
    assert "No root required" in text
    assert not dialog.windowIcon().isNull()
    dialog.close()


def test_instance_guard_blocks_second_runtime_and_allows_clean_restart(tmp_path):
    path = tmp_path / "runtime" / "screen-audio-router.lock"
    first = InstanceGuard(path)
    second = InstanceGuard(path)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_instance_lock_is_user_runtime_scoped():
    assert instance_lock_path({"XDG_RUNTIME_DIR": "/tmp/session-1000"}) == Path(
        "/tmp/session-1000/screen-audio-router.lock"
    )


def test_desktop_asset_install_and_uninstall_preserve_configuration(tmp_path):
    root = Path(__file__).parents[1]
    home = tmp_path / "home"
    data = tmp_path / "data"
    config = tmp_path / "config"
    saved_config = config / "screen-audio-router" / "config.json"
    saved_config.parent.mkdir(parents=True)
    saved_config.write_text("preserve")
    environment = os.environ | {
        "HOME": str(home),
        "XDG_DATA_HOME": str(data),
        "XDG_CONFIG_HOME": str(config),
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(
        ["bash", str(root / "scripts/install-desktop-assets.sh")],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert (data / "applications/screen-audio-router.desktop").is_file()
    assert (data / "icons/hicolor/1254x1254/apps/screen-audio-router.png").is_file()
    subprocess.run(
        ["bash", str(root / "scripts/uninstall.sh")],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert saved_config.read_text() == "preserve"
    assert not (data / "applications/screen-audio-router.desktop").exists()
