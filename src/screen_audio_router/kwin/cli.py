"""KWin discovery and live event debug CLI."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtDBus import QDBusConnection, QDBusInterface

from screen_audio_router.kwin.dbus_service import KWinDBusService
from screen_audio_router.kwin.integration import KWinIntegration, WindowEvent, WindowEventType
from screen_audio_router.kwin.models import WindowInfo

PACKAGE_ID = "screen-audio-router"


def parse_and_run(arguments: Sequence[str]) -> int | None:
    if not arguments or arguments[0] != "kwin":
        return None
    parser = argparse.ArgumentParser(prog="python -m screen_audio_router")
    commands = parser.add_subparsers(dest="command", required=True)
    kwin = commands.add_parser("kwin", help="inspect KWin window state")
    actions = kwin.add_subparsers(dest="kwin_command", required=True)
    actions.add_parser("status", help="check session DBus and KWin script status")
    windows = actions.add_parser("windows", help="print a current window snapshot")
    windows.add_argument("--all", action="store_true", help="include non-normal windows")
    watch = actions.add_parser("watch", help="watch live window events")
    watch.add_argument("--all", action="store_true", help="show non-normal windows")
    args = parser.parse_args(arguments)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    if args.kwin_command == "status":
        return _status()

    integration = KWinIntegration()
    service = KWinDBusService(integration)
    try:
        service.register()
        reload_kwin_script()
    except RuntimeError as exc:
        print(f"KWin error: {exc}")
        return 2

    if args.kwin_command == "windows":
        QTimer.singleShot(1200, lambda: (_print_windows(integration, args.all), app.quit()))
    else:
        integration.add_listener(lambda event: _print_event(event, args.all))
        print("Watching KWin events. Press Ctrl+C to stop.")
        signal.signal(signal.SIGINT, lambda *_: app.quit())
        interrupt_timer = QTimer()
        interrupt_timer.start(250)
        interrupt_timer.timeout.connect(lambda: None)
    return app.exec()


def _status() -> int:
    bus = QDBusConnection.sessionBus()
    kwin = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus)
    script_path = _installed_script_path()
    print("KWIN STATUS")
    print(f"  Session bus: {'available' if bus.isConnected() else 'unavailable'}")
    print(f"  KWin DBus: {'available' if kwin.isValid() else 'unavailable'}")
    print(f"  Script installed: {'yes' if script_path.exists() else 'no'}")
    loaded = kwin.call("isScriptLoaded", PACKAGE_ID)
    print(f"  Script loaded: {'yes' if loaded.arguments() and loaded.arguments()[0] else 'no'}")
    return 0 if bus.isConnected() and kwin.isValid() and script_path.exists() else 2


def reload_kwin_script() -> None:
    script_path = _installed_script_path()
    if not script_path.exists():
        raise RuntimeError("KWin script is not installed; run scripts/install-kwin-script.sh")
    interface = QDBusInterface(
        "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", QDBusConnection.sessionBus()
    )
    if not interface.isValid():
        raise RuntimeError("org.kde.KWin scripting interface is unavailable")
    interface.call("unloadScript", PACKAGE_ID)
    reply = interface.call("loadScript", str(script_path), PACKAGE_ID)
    if reply.type() == reply.MessageType.ErrorMessage or not reply.arguments():
        raise RuntimeError(f"could not load KWin script: {reply.errorMessage()}")
    interface.call("start")


def _installed_script_path() -> Path:
    return Path.home() / ".local/share/kwin/scripts" / PACKAGE_ID / "contents/code/main.js"


def _print_windows(integration: KWinIntegration, include_all: bool) -> None:
    print("WINDOWS")
    windows = integration.windows(include_all)
    if not windows:
        print("  No matching windows received")
    for window in windows:
        _print_window(window)


def _print_window(window: WindowInfo) -> None:
    print(f"\n{window.caption or window.resource_class or 'Untitled window'}")
    print(f"  ID: {window.runtime_id} (runtime only)")
    print(f"  Class: {window.resource_class or 'Unknown'}")
    print(f"  Name: {window.resource_name or 'Unknown'}")
    print(f"  PID: {window.pid if window.pid is not None else 'Unknown'}")
    print(f"  Output: {window.output_name or 'Unknown'}")
    print(f"  Active: {str(window.active).lower()}")
    print(f"  Type: {window.window_type or 'Unknown'}")


def _print_event(event: WindowEvent, include_all: bool) -> None:
    if event.event_type is WindowEventType.SNAPSHOT:
        print(f"[{_time()}] SNAPSHOT RECEIVED")
        return
    window = event.window
    if window is None or (not include_all and not window.normal_window):
        return
    label = window.resource_class or window.caption or window.runtime_id
    if event.event_type is WindowEventType.OUTPUT_CHANGED:
        print(f"[{_time()}] OUTPUT_CHANGED {label}: {event.old_output} -> {window.output_name}")
    else:
        print(f"[{_time()}] {event.event_type.value.upper()} {label} output={window.output_name}")


def _time() -> str:
    return datetime.now(UTC).astimezone().strftime("%H:%M:%S")
