"""Safe inspection and mutation commands for persistent configuration."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from screen_audio_router.config.manager import ConfigurationStore
from screen_audio_router.config.schema import (
    ApplicationRule,
    AudioOutputIdentity,
    ConfigError,
    DisplayIdentity,
    DisplayIdentityKind,
    DisplayMapping,
    RoutingMode,
)


def parse_and_run(arguments: Sequence[str]) -> int | None:
    if not arguments or arguments[0] != "config":
        return None
    parser = argparse.ArgumentParser(prog="python -m screen_audio_router")
    root = parser.add_subparsers(dest="command", required=True)
    config = root.add_parser("config", help="inspect or update persistent configuration")
    actions = config.add_subparsers(dest="config_command", required=True)
    actions.add_parser("path")
    actions.add_parser("show")
    actions.add_parser("validate")
    actions.add_parser("apps")
    actions.add_parser("displays")
    enabled = actions.add_parser("set-enabled")
    enabled.add_argument("enabled", choices=("true", "false"))
    set_app = actions.add_parser("set-app")
    set_app.add_argument("application")
    set_app.add_argument("mode", choices=("ignore", "auto", "fixed"))
    set_app.add_argument("output", nargs="?")
    mapping = actions.add_parser("map-display")
    mapping.add_argument("display")
    mapping.add_argument("output")
    mapping.add_argument("--display-name")
    mapping.add_argument("--output-name")
    unmap = actions.add_parser("unmap-display")
    unmap.add_argument("display")
    args = parser.parse_args(arguments)
    store = ConfigurationStore()

    if args.config_command == "path":
        print(store.path)
        return 0
    try:
        current = store.load()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2
    if args.config_command == "show":
        print(json.dumps(current.to_dict(), indent=2, sort_keys=True))
        _print_warnings(current.validation_warnings)
    elif args.config_command == "validate":
        _print_warnings(current.validation_warnings)
        print(f"Configuration is valid (version {current.version})")
    elif args.config_command == "apps":
        print("APPLICATION RULES")
        if not current.applications:
            print("  None (unknown applications resolve to IGNORE)")
        for rule in current.applications:
            suffix = f" -> {rule.fixed_output_id}" if rule.fixed_output_id else ""
            print(f"  {rule.id}: {rule.mode.value}{suffix}")
    elif args.config_command == "displays":
        print("DISPLAY AUDIO MAPPINGS")
        if not current.display_mappings:
            print("  None")
        for item in current.display_mappings:
            print(f"  {item.display.id} -> {item.output.id} [{item.display.identity_kind.value}]")
    elif args.config_command == "set-enabled":
        current.enabled = args.enabled == "true"
        store.save(current)
        print(f"Saved router enabled={str(current.enabled).lower()}; no audio action executed")
    elif args.config_command == "set-app":
        mode = RoutingMode(args.mode)
        if mode is RoutingMode.FIXED_DEVICE and not args.output:
            parser.error("fixed mode requires OUTPUT")
        if mode is not RoutingMode.FIXED_DEVICE and args.output:
            parser.error("OUTPUT is only valid for fixed mode")
        current.set_application_rule(
            ApplicationRule(args.application, args.application, mode, fixed_output_id=args.output)
        )
        store.save(current)
        print(f"Saved {args.application} mode={mode.value}; no audio action executed")
    elif args.config_command == "map-display":
        current.set_display_mapping(
            DisplayMapping(
                DisplayIdentity(
                    id=args.display,
                    connector=args.display,
                    friendly_name=args.display_name,
                    identity_kind=DisplayIdentityKind.CONNECTOR_FALLBACK,
                ),
                AudioOutputIdentity(
                    id=args.output,
                    sink_name=args.output,
                    description=args.output_name,
                ),
            )
        )
        store.save(current)
        print(f"Saved {args.display} -> {args.output}; no audio action executed")
    elif args.config_command == "unmap-display":
        removed = current.remove_display_mapping(args.display)
        store.save(current)
        print(f"{'Removed' if removed else 'No existing'} mapping for {args.display}")
    return 0


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"Warning: skipped invalid rule: {warning}")
