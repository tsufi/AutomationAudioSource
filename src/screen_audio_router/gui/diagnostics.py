"""Read-only Qt diagnostics dialog backed by the existing runtime snapshot."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from screen_audio_router.diagnostics import DiagnosticSnapshot, render_diagnostic_report


class DiagnosticsDialog(QDialog):
    """Render snapshots only; never poll, own DBus, persist, or execute routing."""

    def __init__(self, snapshot_provider: Callable[[], DiagnosticSnapshot], parent=None) -> None:
        super().__init__(parent)
        self.snapshot_provider = snapshot_provider
        self.snapshot: DiagnosticSnapshot | None = None
        self.setWindowTitle("Screen Audio Router Diagnostics")
        self.resize(920, 680)
        self.tabs = QTabWidget()
        self.overview = _text_panel()
        self.applications = _text_panel()
        self.audio = _text_panel()
        self.kwin = _text_panel()
        self.mappings = _text_panel()
        self.events = _text_panel()
        for label, panel in (
            ("Overview", self.overview),
            ("Applications", self.applications),
            ("Audio & Matching", self.audio),
            ("KWin Windows", self.kwin),
            ("Display Mappings", self.mappings),
            ("Recent Events", self.events),
        ):
            self.tabs.addTab(panel, label)
        self.refresh_button = QPushButton("Refresh")
        self.copy_button = QPushButton("Copy Diagnostics")
        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        controls = QHBoxLayout()
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.copy_button)
        controls.addStretch(1)
        controls.addWidget(close_buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addLayout(controls)
        self.refresh_button.clicked.connect(self.refresh)
        self.copy_button.clicked.connect(self.copy_diagnostics)
        close_buttons.rejected.connect(self.close)

    def show_and_refresh(self) -> None:
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        self.snapshot = self.snapshot_provider()
        report = render_diagnostic_report(self.snapshot)
        sections = _report_sections(report)
        self.overview.setPlainText(sections.get("Overview", ""))
        self.applications.setPlainText(sections.get("Applications", ""))
        self.audio.setPlainText(
            "Audio Outputs\n-------------\n"
            + sections.get("Audio Outputs", "")
            + "\n\nAudio Streams and Matching\n--------------------------\n"
            + sections.get("Audio Streams and Matching", "")
        )
        self.kwin.setPlainText(sections.get("KWin Windows", ""))
        self.mappings.setPlainText(
            sections.get("Display Mappings", "")
            + "\n\nConfiguration Summary\n---------------------\n"
            + sections.get("Configuration Summary", "")
        )
        self.events.setPlainText(sections.get("Recent Events", ""))

    def copy_diagnostics(self) -> str:
        if self.snapshot is None:
            self.refresh()
        report = render_diagnostic_report(self.snapshot)
        QApplication.clipboard().setText(report)
        return report


def _text_panel() -> QPlainTextEdit:
    panel = QPlainTextEdit()
    panel.setReadOnly(True)
    return panel


def _report_sections(report: str) -> dict[str, str]:
    headings = {
        "Overview",
        "Applications",
        "Audio Outputs",
        "Audio Streams and Matching",
        "KWin Windows",
        "Display Mappings",
        "Configuration Summary",
        "Recent Events",
    }
    result: dict[str, list[str]] = {}
    current: str | None = None
    lines = report.splitlines()
    for index, line in enumerate(lines):
        if line in headings and index + 1 < len(lines) and set(lines[index + 1]) == {"-"}:
            current = line
            result[current] = []
            continue
        if current is not None and line and set(line) == {"-"}:
            continue
        if current is not None:
            result[current].append(line)
    return {heading: "\n".join(content).strip() for heading, content in result.items()}
