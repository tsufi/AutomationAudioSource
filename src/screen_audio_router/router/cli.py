"""Application registry debug runtime and CLI."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Sequence

from PySide6.QtCore import QCoreApplication, QTimer

from screen_audio_router.audio import AudioBackendError, PactlAudioBackend
from screen_audio_router.kwin.cli import reload_kwin_script
from screen_audio_router.kwin.dbus_service import KWinDBusService
from screen_audio_router.kwin.integration import KWinIntegration, WindowEvent, WindowEventType
from screen_audio_router.router.application_registry import ApplicationEntry, ApplicationRegistry
from screen_audio_router.router.matcher import ApplicationMatcher, default_alias_rules


def parse_and_run(arguments: Sequence[str]) -> int | None:
    if not arguments or arguments[0] != "apps":
        return None
    parser = argparse.ArgumentParser(prog="python -m screen_audio_router")
    commands = parser.add_subparsers(dest="command", required=True)
    apps = commands.add_parser("apps", help="inspect unified application state")
    actions = apps.add_subparsers(dest="apps_command", required=True)
    actions.add_parser("list", help="list current applications, windows, and streams")
    actions.add_parser("watch", help="watch registry changes")
    explain = actions.add_parser("explain", help="explain one application's matches")
    explain.add_argument("application", help="stable ID or display name")
    args = parser.parse_args(arguments)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    kwin = KWinIntegration()
    registry = ApplicationRegistry(ApplicationMatcher(default_alias_rules()))
    audio = PactlAudioBackend()

    def window_event(event: WindowEvent) -> None:
        if event.event_type is WindowEventType.SNAPSHOT:
            changed = registry.sync_windows(kwin.windows(include_all=True))
        else:
            registry.handle_window_event(event)
            changed = True
        if args.apps_command == "watch" and changed:
            _print_change(event, registry)

    kwin.add_listener(window_event)
    service = KWinDBusService(kwin)
    try:
        service.register()
        reload_kwin_script()
    except RuntimeError as exc:
        print(f"Registry error: {exc}")
        return 2

    def refresh_audio() -> None:
        try:
            changed = registry.refresh_streams(audio.list_streams())
        except AudioBackendError as exc:
            print(f"Audio refresh error: {exc}")
            return
        if args.apps_command == "watch" and changed:
            print("AUDIO STATE CHANGED")
            _print_registry(registry)

    if args.apps_command == "watch":
        print("Watching application registry. Press Ctrl+C to stop.")
        signal.signal(signal.SIGINT, lambda *_: app.quit())
        interrupt_timer = QTimer()
        interrupt_timer.start(250)
        interrupt_timer.timeout.connect(lambda: None)
        audio_timer = QTimer()
        audio_timer.timeout.connect(refresh_audio)
        audio_timer.start(1500)
        QTimer.singleShot(500, refresh_audio)
    else:
        def finish() -> None:
            refresh_audio()
            if args.apps_command == "list":
                _print_registry(registry)
            else:
                _print_explanation(registry, args.application)
            app.quit()

        QTimer.singleShot(1300, finish)
    return app.exec()


def _print_registry(registry: ApplicationRegistry) -> None:
    print("APPLICATIONS")
    for entry in registry.entries:
        _print_entry(entry)
    print("\nUNMATCHED AUDIO")
    if not registry.unmatched_streams:
        print("  None")
    for unmatched in registry.unmatched_streams:
        stream = unmatched.stream
        print(f"  Stream {stream.stream_id}: {stream.application_name or stream.process_binary or 'Unknown'}")
        print(f"    Reason: {unmatched.reason}")


def _print_entry(entry: ApplicationEntry) -> None:
    print(f"\n{entry.display_name}")
    print(f"  ID: {entry.application_id}")
    print(f"  Active: {str(entry.active).lower()}")
    print(f"  Audio only: {str(entry.audio_only).lower()}")
    print(f"  Controlling window: {entry.controlling_window.runtime_id if entry.controlling_window else 'None'}")
    print(f"  Display: {entry.current_display or 'None'}")
    print("  Windows:")
    if not entry.windows:
        print("    None")
    for window in entry.windows:
        print(f"    {window.caption or 'Untitled'} [{window.runtime_id}]")
        print(f"      class={window.resource_class or 'Unknown'} output={window.output_name or 'Unknown'}")
    print("  Audio:")
    if not entry.streams:
        print("    None")
    for matched in entry.streams:
        stream = matched.stream
        print(f"    {stream.application_name or stream.process_binary or 'Unknown'} stream={stream.stream_id}")
        print(f"      output={stream.current_output or 'Unknown'}")
        print(f"      match={matched.match.confidence.name} score={matched.match.score}")
        print(f"      reason={matched.match.reason}")


def _print_explanation(registry: ApplicationRegistry, application: str) -> None:
    entry = registry.find(application)
    if entry is None:
        print(f"Application not found: {application}")
        return
    print("MATCH EXPLANATION")
    _print_entry(entry)


def _print_change(event: WindowEvent, registry: ApplicationRegistry) -> None:
    label = event.window.resource_class if event.window else "window snapshot"
    print(f"WINDOW {event.event_type.value.upper()}: {label}")
    active = [entry for entry in registry.entries if entry.active]
    if active:
        print(f"  Active application: {active[0].display_name}")
