"""Centralized Qt application-icon loading."""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from screen_audio_router.identity import APPLICATION_ICON_NAME


def application_icon() -> QIcon:
    """Load the packaged icon without depending on the process working directory."""
    try:
        resource = files("screen_audio_router").joinpath("assets/icon.png")
        pixmap = QPixmap()
        if pixmap.loadFromData(resource.read_bytes(), "PNG"):
            return QIcon(pixmap)
    except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError):
        pass
    return QIcon.fromTheme(APPLICATION_ICON_NAME)


def configure_application_icon(app: QApplication) -> QIcon:
    """Apply the stable KDE identity and packaged icon to a Qt application."""
    icon = application_icon()
    app.setDesktopFileName(APPLICATION_ICON_NAME)
    if not icon.isNull():
        app.setWindowIcon(icon)
    return icon
