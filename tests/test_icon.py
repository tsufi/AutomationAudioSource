import os
from importlib.resources import files
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication

from screen_audio_router.gui.icons import application_icon, configure_application_icon
from screen_audio_router.identity import APPLICATION_ICON_NAME


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_packaged_icon_exists_with_detected_dimensions():
    asset = files("screen_audio_router").joinpath("assets/icon.png")
    assert asset.is_file()
    size = QImageReader(str(asset)).size()
    assert (size.width(), size.height()) == (1254, 1254)


def test_icon_loading_does_not_depend_on_working_directory(qt_app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not application_icon().isNull()


def test_qapplication_receives_icon_and_stable_desktop_identity(qt_app):
    icon = configure_application_icon(qt_app)
    assert not icon.isNull()
    assert not qt_app.windowIcon().isNull()
    assert qt_app.desktopFileName() == APPLICATION_ICON_NAME


def test_launcher_and_installer_use_stable_icon_identity():
    root = Path(__file__).parents[1]
    launcher = (root / "resources/screen-audio-router.desktop").read_text()
    installer = (root / "scripts/install-desktop-assets.sh").read_text()
    assert "Icon=screen-audio-router\n" in launcher
    assert "Exec=screen-audio-router\n" in launcher
    assert "hicolor/1254x1254/apps" in installer
    assert "screen-audio-router.png" in installer
    assert "/run/media/" not in launcher + installer
