"""Safe debug CLI for inspecting and explicitly applying route decisions."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from PySide6.QtCore import QCoreApplication, QTimer

from screen_audio_router.audio import AudioBackendError, PactlAudioBackend
from screen_audio_router.config import ConfigError, ConfigurationStore
from screen_audio_router.config.resolver import RoutingConfigResolver
from screen_audio_router.config.schema import RoutingMode
from screen_audio_router.kwin.cli import reload_kwin_script
from screen_audio_router.kwin.dbus_service import KWinDBusService
from screen_audio_router.kwin.integration import KWinIntegration, WindowEventType
from screen_audio_router.router.application_registry import ApplicationRegistry
from screen_audio_router.router.decisions import (
    ApplicationRoutingRule,
    DisplayAudioMapping,
    RouteAction,
    RouteDecision,
)
from screen_audio_router.router.engine import RouteExecutor, RouterEngine
from screen_audio_router.router.matcher import ApplicationMatcher


def parse_and_run(arguments: Sequence[str]) -> int | None:
    if not arguments or arguments[0] != "router":
        return None
    parser = argparse.ArgumentParser(prog="python -m screen_audio_router")
    commands = parser.add_subparsers(dest="command", required=True)
    router = commands.add_parser("router", help="inspect pure routing decisions")
    actions = router.add_subparsers(dest="router_command", required=True)
    list_parser = actions.add_parser("list", help="list decisions; unspecified apps are IGNORE")
    _add_intent_options(list_parser, mode_required=False)
    explain = actions.add_parser("explain", help="explain one temporary routing decision")
    explain.add_argument("application")
    _add_intent_options(explain, mode_required=False)
    apply = actions.add_parser("apply", help="explicitly execute one temporary MOVE decision")
    apply.add_argument("application")
    _add_intent_options(apply, mode_required=False)
    args = parser.parse_args(arguments)

    try:
        mappings = tuple(_parse_mapping(value) for value in args.map)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        persisted = ConfigurationStore().load()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2
    resolver = RoutingConfigResolver(persisted)
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    kwin = KWinIntegration()
    registry = ApplicationRegistry(ApplicationMatcher(resolver.matcher_aliases()))
    kwin.add_listener(
        lambda event: registry.sync_windows(kwin.windows(include_all=True))
        if event.event_type is WindowEventType.SNAPSHOT
        else registry.handle_window_event(event)
    )
    service = KWinDBusService(kwin)
    try:
        service.register()
        reload_kwin_script()
    except RuntimeError as exc:
        print(f"Router debug error: {exc}")
        return 2

    def finish() -> None:
        backend = PactlAudioBackend()
        try:
            registry.refresh_streams(backend.list_streams())
            outputs = backend.list_outputs()
        except AudioBackendError as exc:
            print(f"Audio discovery error: {exc}")
            app.exit(2)
            return
        engine = RouterEngine()
        if args.router_command == "list":
            print("ROUTING DECISIONS")
            for entry in registry.entries:
                saved_rule = resolver.application_rule(entry.application_id)
                mode = RoutingMode(args.mode) if args.mode else saved_rule.mode
                fixed = args.fixed_output if args.fixed_output is not None else saved_rule.fixed_output_id
                effective_mappings = mappings if args.map else resolver.display_mappings()
                decision = engine.decide(
                    entry,
                    ApplicationRoutingRule(entry.application_id, mode, fixed),
                    effective_mappings,
                    outputs,
                    enabled=False if args.disabled else resolver.enabled,
                )
                _print_decision(entry.display_name, decision)
        else:
            entry = registry.find(args.application)
            if entry is None:
                print(f"Application not found: {args.application}")
                app.exit(2)
                return
            saved_rule = resolver.application_rule(entry.application_id)
            mode = RoutingMode(args.mode) if args.mode else saved_rule.mode
            fixed = args.fixed_output if args.fixed_output is not None else saved_rule.fixed_output_id
            effective_mappings = mappings if args.map else resolver.display_mappings()
            decision = engine.decide(
                entry,
                ApplicationRoutingRule(entry.application_id, mode, fixed),
                effective_mappings,
                outputs,
                enabled=False if args.disabled else resolver.enabled,
            )
            _print_decision(entry.display_name, decision)
            if args.router_command == "apply":
                if decision.action is not RouteAction.MOVE:
                    print("Nothing executed: decision is not MOVE")
                else:
                    result = RouteExecutor(backend).execute(decision)
                    for stream in result.streams:
                        status = "OK" if stream.succeeded else f"FAILED: {stream.error}"
                        print(f"APPLY stream {stream.stream_id}: {status}")
        app.quit()

    QTimer.singleShot(1300, finish)
    return app.exec()


def _add_intent_options(parser: argparse.ArgumentParser, *, mode_required: bool) -> None:
    parser.add_argument(
        "--mode", choices=[mode.value for mode in RoutingMode], required=mode_required
    )
    parser.add_argument("--fixed-output", help="stable sink name for fixed mode")
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="DISPLAY=OUTPUT",
        help="temporary display-to-output mapping; repeatable",
    )
    parser.add_argument("--disabled", action="store_true", help="evaluate globally disabled state")


def _parse_mapping(value: str) -> DisplayAudioMapping:
    display, separator, output = value.partition("=")
    if not separator or not display or not output:
        raise ValueError(f"invalid mapping {value!r}; expected DISPLAY=OUTPUT")
    return DisplayAudioMapping(display, output)


def _print_decision(display_name: str, decision: RouteDecision) -> None:
    print(f"\nAPPLICATION: {display_name}")
    print(f"  ID: {decision.application_id}")
    print(f"  Mode: {decision.mode.value}")
    print(f"  Controlling display: {decision.controlling_display or 'None'}")
    print(f"  Current outputs: {', '.join(decision.current_outputs) or 'None'}")
    print(f"  Desired output: {decision.desired_output or 'None'}")
    print(f"  Streams to move: {', '.join(decision.stream_ids) or 'None'}")
    print(f"  Destination available: {decision.destination_available}")
    print(f"  Decision: {decision.action.value}")
    print(f"  Reason: {decision.reason.value}")
    if decision.details:
        print(f"  Details: {decision.details}")
