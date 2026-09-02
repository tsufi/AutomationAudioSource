import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from screen_audio_router.audio.models import AudioOutput, AudioStream, Availability
from screen_audio_router.config.schema import (
    AppConfig,
    ApplicationRule,
    AudioOutputIdentity,
    DisplayIdentity,
    DisplayIdentityKind,
    DisplayMapping,
    MatcherAliasConfig,
    RoutingMode,
)
from screen_audio_router.diagnostics import (
    DiagnosticCategory,
    DiagnosticEventHistory,
    DiagnosticSeverity,
    build_diagnostic_snapshot,
    explain_decision,
    render_diagnostic_report,
    sanitize_diagnostic_text,
)
from screen_audio_router.gui.main_window import MainWindow
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import (
    ApplicationEntry,
    MatchedStream,
    UnmatchedStream,
)
from screen_audio_router.router.decisions import RouteAction, RouteDecision, RouteReason
from screen_audio_router.router.manual_routing import RoutingOperationResult
from screen_audio_router.router.matcher import MatchConfidence, MatchResult


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def output(output_id="sink.gc7", name="Sound Blaster GC7", available=True):
    return AudioOutput(
        output_id,
        name,
        Availability.AVAILABLE if available else Availability.UNAVAILABLE,
        card_name="card.one",
        active_port="analog-output",
    )


def app_entry(*, streams=True, windows=True, current="sink.other", reason="configured alias"):
    window = WindowInfo(
        "window-plezy",
        "com.edde746.plezy",
        "plezy",
        "Plezy",
        5776,
        "display.tv",
        True,
        True,
        "normal",
    )
    stream = AudioStream(
        "8548",
        application_name="mpv",
        process_binary="mpv",
        process_id=5776,
        media_name="Plezy playback",
        current_output=current,
    )
    matched = MatchedStream(
        stream,
        MatchResult("com.edde746.plezy", MatchConfidence.HIGH, reason, 75),
    )
    return ApplicationEntry(
        "com.edde746.plezy",
        "Plezy",
        (window,) if windows else (),
        (matched,) if streams else (),
        window if windows else None,
        windows,
        "display.tv" if windows else None,
        (current,) if streams else (),
        not windows,
    )


def diagnostic_config(mode=RoutingMode.AUTO, fixed=None, enabled=True, mapped=True):
    config = AppConfig(enabled=enabled)
    config.applications = [
        ApplicationRule(
            "com.edde746.plezy", "Plezy", mode, fixed_output_id=fixed
        )
    ]
    config.matcher_aliases = [
        MatcherAliasConfig("com.edde746.plezy", process_binaries=("mpv",))
    ]
    if mapped:
        config.display_mappings = [
            DisplayMapping(
                DisplayIdentity(
                    "display.tv",
                    connector="DP-3",
                    friendly_name="Samsung TV",
                    identity_kind=DisplayIdentityKind.CONNECTOR_FALLBACK,
                ),
                AudioOutputIdentity("sink.gc7", "sink.gc7", "Sound Blaster GC7"),
            )
        ]
    return config


def snapshot(config=None, entry=None, outputs=None, unmatched=()):
    config = config or diagnostic_config()
    entry = entry or app_entry()
    outputs = (output(),) if outputs is None else outputs
    return build_diagnostic_snapshot(
        config,
        (entry,),
        tuple(outputs),
        unmatched,
        configuration_path="/tmp/config.json",
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_snapshot_overview_health_and_logical_counts(enabled):
    value = snapshot(diagnostic_config(enabled=enabled))
    assert value.router_enabled is enabled
    assert value.audio_backend_health == "Available"
    assert value.kwin_health == "Connected"
    assert value.dbus_health == "Owned"
    assert len(value.applications) == 1
    assert len(value.outputs) == 1
    assert len(value.streams) == 1
    assert len(value.windows) == 1


def test_configured_and_unconfigured_application_diagnostics():
    assert snapshot().applications[0].configured
    value = AppConfig()
    row = snapshot(value).applications[0]
    assert not row.configured
    assert row.decision.mode is RoutingMode.IGNORE


@pytest.mark.parametrize(
    ("reason", "action", "expected"),
    [
        (RouteReason.GLOBAL_DISABLED, RouteAction.NO_ACTION, "Router is disabled"),
        (RouteReason.MODE_IGNORE, RouteAction.NO_ACTION, "Application is ignored"),
        (RouteReason.DISPLAY_UNMAPPED, RouteAction.NO_ACTION, "No audio output is mapped"),
        (RouteReason.DESTINATION_MISSING, RouteAction.DEVICE_MISSING, "unavailable"),
        (RouteReason.NO_STREAMS, RouteAction.NO_ACTION, "no audio streams"),
        (RouteReason.NO_CONTROLLING_WINDOW, RouteAction.NO_ACTION, "No controlling window"),
        (RouteReason.ALREADY_CORRECT, RouteAction.NO_ACTION, "already using"),
        (RouteReason.AMBIGUOUS_AUDIO, RouteAction.UNMATCHED, "ambiguous"),
    ],
)
def test_friendly_decision_explanations(reason, action, expected):
    decision = RouteDecision("app", RoutingMode.AUTO, action, reason)
    assert expected in explain_decision(decision)


def test_move_and_fixed_device_decisions_are_engine_derived_and_explained():
    automatic = snapshot().applications[0].decision
    assert automatic.action is RouteAction.MOVE
    assert automatic.reason is RouteReason.AUTO_DISPLAY_MAPPING
    assert automatic.stream_ids == ("8548",)
    assert "Sound Blaster GC7" in explain_decision(
        automatic, {"sink.gc7": "Sound Blaster GC7"}
    )
    fixed = snapshot(
        diagnostic_config(RoutingMode.FIXED_DEVICE, "sink.gc7")
    ).applications[0].decision
    assert fixed.reason is RouteReason.FIXED_DEVICE


def test_no_stream_no_window_unmapped_missing_and_already_correct_snapshots():
    assert snapshot(entry=app_entry(streams=False)).applications[0].decision.reason is RouteReason.NO_STREAMS
    assert snapshot(entry=app_entry(windows=False)).applications[0].decision.reason is RouteReason.NO_CONTROLLING_WINDOW
    assert snapshot(diagnostic_config(mapped=False)).applications[0].decision.reason is RouteReason.DISPLAY_UNMAPPED
    assert snapshot(outputs=()).applications[0].decision.reason is RouteReason.DESTINATION_MISSING
    assert snapshot(entry=app_entry(current="sink.gc7")).applications[0].decision.reason is RouteReason.ALREADY_CORRECT


def test_matched_and_ambiguous_stream_details_are_exposed():
    matched = snapshot().streams[0]
    assert matched.matched_application == "com.edde746.plezy"
    assert matched.confidence == "HIGH"
    assert matched.match_reason == "configured alias"
    ambiguous_stream = AudioStream("9001", application_name="mpv", current_output="sink.gc7")
    unmatched = UnmatchedStream(
        ambiguous_stream,
        "ambiguous configured alias for com.edde746.plezy: 2 candidate streams",
    )
    value = snapshot(entry=app_entry(streams=False), unmatched=(unmatched,))
    assert value.streams[0].ambiguous
    assert value.streams[0].matched_application is None
    assert value.applications[0].unmatched_stream_count == 1
    assert value.applications[0].decision.reason is RouteReason.AMBIGUOUS_AUDIO


def test_window_output_and_mapping_details_include_stability_and_availability():
    value = snapshot()
    assert value.windows[0].runtime_id == "window-plezy"
    assert value.windows[0].caption == "Plezy"
    mapping = value.display_mappings[0]
    assert mapping.friendly_name == "Samsung TV"
    assert mapping.identity_kind == "connector_fallback"
    assert mapping.available
    missing = snapshot(outputs=()).display_mappings[0]
    assert not missing.available


def test_config_summary_counts_are_correct():
    value = snapshot()
    assert value.config_version == 1
    assert value.application_rule_count == 1
    assert len(value.display_mappings) == 1
    assert value.matcher_alias_count == 1


def test_event_history_is_bounded_and_ordered_with_typed_severity():
    history = DiagnosticEventHistory(limit=3)
    for number in range(5):
        history.add(
            DiagnosticCategory.RUNTIME,
            f"event {number}",
            severity=DiagnosticSeverity.WARNING if number == 4 else DiagnosticSeverity.INFO,
        )
    assert [event.message for event in history.events] == ["event 2", "event 3", "event 4"]
    assert history.events[-1].severity is DiagnosticSeverity.WARNING
    assert history.events[-1].timestamp.tzinfo is not None


def test_report_is_useful_and_sanitizes_urls_and_secrets(monkeypatch):
    monkeypatch.setenv("SCREEN_AUDIO_ROUTER_TEST_SECRET", "must-not-appear")
    history = DiagnosticEventHistory()
    history.add(
        DiagnosticCategory.AUDIO,
        "backend failed at https://example.test/play?token=abc password=hunter2",
        severity=DiagnosticSeverity.ERROR,
    )
    value = build_diagnostic_snapshot(
        diagnostic_config(),
        (app_entry(),),
        (output(),),
        events=history.events,
    )
    report = render_diagnostic_report(value)
    assert "Plezy" in report and "Audio would be moved to Sound Blaster GC7" in report
    assert "https://example.test/play?[redacted]" in report
    assert "abc" not in report and "hunter2" not in report
    assert "must-not-appear" not in report


def test_sanitizer_redacts_authentication_fields():
    text = sanitize_diagnostic_text("authorization=Bearer-secret api_key=xyz")
    assert "Bearer-secret" not in text and "xyz" not in text


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


class FakeRegistry:
    unmatched_streams = ()


class FakeKWin:
    def __init__(self):
        self.calls = 0

    def windows(self, include_all=False):
        self.calls += 1
        return list(app_entry().windows)


class FakeRuntime(QObject):
    snapshot_changed = Signal(object, object)
    error_occurred = Signal(str)

    def __init__(self):
        super().__init__()
        self.backend = FakeBackend()
        self.registry = FakeRegistry()
        self.kwin = FakeKWin()
        self.refreshes = 0
        self.starts = 0
        self._started = True
        self.audio_error = None
        self.kwin_error = None

    def start(self):
        self.starts += 1

    def stop(self):
        pass

    def refresh_audio(self):
        self.refreshes += 1


def test_diagnostics_ui_is_read_only_and_reuses_runtime_without_side_effects(qt_app):
    config = diagnostic_config()
    original = config.to_dict()
    store = MemoryStore()
    runtime = FakeRuntime()
    window = MainWindow(config, store, runtime)
    runtime.snapshot_changed.emit((app_entry(),), (output(),))
    qt_app.processEvents()
    window.show_diagnostics()
    first_dialog = window.diagnostics_dialog
    window.show_diagnostics()
    report = first_dialog.copy_diagnostics()
    qt_app.processEvents()
    assert window.diagnostics_dialog is first_dialog
    assert first_dialog.applications.isReadOnly()
    assert "Plezy" in QApplication.clipboard().text() == report
    assert runtime.refreshes == 0
    assert runtime.starts == 0
    assert runtime.backend.moves == []
    assert store.saved == []
    assert config.to_dict() == original
    window.close()


def test_runtime_and_routing_events_appear_in_history(qt_app):
    config = diagnostic_config()
    runtime = FakeRuntime()
    window = MainWindow(config, MemoryStore(), runtime)
    runtime.snapshot_changed.emit((app_entry(),), (output(),))
    runtime.error_occurred.emit("simulated backend refresh error")
    qt_app.processEvents()
    messages = [event.message for event in window.diagnostic_history.events]
    assert any("Application discovered" in message for message in messages)
    assert any("Audio stream appeared" in message for message in messages)
    assert any("Audio output appeared" in message for message in messages)
    assert "simulated backend refresh error" in messages
    assert any(event.severity is DiagnosticSeverity.ERROR for event in window.diagnostic_history.events)
    decision = RouteDecision(
        "com.edde746.plezy",
        RoutingMode.AUTO,
        RouteAction.MOVE,
        RouteReason.AUTO_DISPLAY_MAPPING,
    )
    window._routing_completed(
        RoutingOperationResult(
            "com.edde746.plezy",
            decision,
            None,
            False,
            "Partially routed Plezy: 1 moved, 1 failed",
            ("8548",),
            ("8549",),
        )
    )
    routing_event = window.diagnostic_history.events[-1]
    assert routing_event.category is DiagnosticCategory.ROUTING
    assert routing_event.severity is DiagnosticSeverity.WARNING
    assert routing_event.details == "moved=1 failed=1"
    window.close()


def test_diagnostics_core_has_no_executor_or_backend_polling_imports():
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "screen_audio_router"
    source = (root / "diagnostics.py").read_text() + (root / "gui" / "diagnostics.py").read_text()
    for forbidden in ("RouteExecutor", "ManualRoutingController", "PactlAudioBackend", "KWinDBusService"):
        assert forbidden not in source
