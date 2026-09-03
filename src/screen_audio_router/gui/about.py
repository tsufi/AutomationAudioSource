"""Native About dialog for release and runtime information."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from screen_audio_router import __version__
from screen_audio_router.gui.icons import application_icon


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Screen Audio Router")
        self.setWindowIcon(application_icon())
        icon = QLabel()
        icon.setPixmap(application_icon().pixmap(96, 96))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel(f"<h2>Screen Audio Router</h2><p>Version {__version__}</p>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description = QLabel(
            "Automatically routes application audio based on the physical display "
            "containing the application's controlling window."
        )
        description.setWordWrap(True)
        details = QLabel(
            "KDE Plasma / Wayland focused<br>"
            "PipeWire / PulseAudio compatibility backend<br>"
            "No root required"
        )
        details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout = QVBoxLayout(self)
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(details)
        layout.addWidget(buttons)

    def show_dialog(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
