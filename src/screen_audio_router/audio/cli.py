"""Safe, explicit development CLI for the audio backend."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from screen_audio_router.audio.backend import AudioBackendError
from screen_audio_router.audio.pactl_backend import PactlAudioBackend


def add_audio_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    audio = subparsers.add_parser("audio", help="inspect or explicitly control live audio")
    commands = audio.add_subparsers(dest="audio_command", required=True)
    commands.add_parser("status", help="show audio server status")
    commands.add_parser("outputs", help="list available outputs")
    commands.add_parser("streams", help="list active playback streams")
    commands.add_parser("cards", help="list cards and profiles")
    move = commands.add_parser("move", help="move one explicit stream to one named output")
    move.add_argument("stream_id", help="dynamic sink-input ID from the streams command")
    move.add_argument("output", help="stable sink name from the outputs command")
    profile = commands.add_parser("set-profile", help="activate an explicit card profile")
    profile.add_argument("card", help="stable card name from the cards command")
    profile.add_argument("profile", help="profile name from the cards command")


def run_audio_command(args: argparse.Namespace) -> int:
    backend = PactlAudioBackend()
    try:
        if args.audio_command == "status":
            info = backend.server_info()
            print("AUDIO SERVER")
            print(f"  Name: {info.server_name}")
            print(f"  Version: {info.server_version or 'Unknown'}")
            print(f"  Default output: {info.default_output or 'Unknown'}")
        elif args.audio_command == "outputs":
            outputs = backend.list_outputs()
            print("OUTPUTS")
            if not outputs:
                print("  No outputs found")
            for output in outputs:
                print(f"\n{output.description}")
                print(f"  Sink: {output.sink_name}")
                print(f"  Card: {output.card_name or 'Unknown'}")
                print(f"  State: {output.state or 'Unknown'}")
                print(f"  Availability: {output.availability.value}")
        elif args.audio_command == "streams":
            streams = backend.list_streams()
            print("STREAMS")
            if not streams:
                print("  No active playback streams")
            for stream in streams:
                print(f"\n{stream.application_name or stream.process_binary or 'Unknown application'}")
                print(f"  ID: {stream.stream_id} (dynamic)")
                print(f"  Binary: {stream.process_binary or 'Unknown'}")
                print(f"  PID: {stream.process_id if stream.process_id is not None else 'Unknown'}")
                print(f"  Media: {_safe_media_name(stream.media_name)}")
                print(f"  Current output: {stream.current_output or 'Unresolved'}")
        elif args.audio_command == "cards":
            print("CARDS")
            for card in backend.list_cards():
                print(f"\n{card.description}")
                print(f"  Card: {card.card_name}")
                print(f"  Active profile: {card.active_profile or 'Unknown'}")
                for profile in card.profiles:
                    marker = "*" if profile.name == card.active_profile else " "
                    availability = "unknown" if profile.available is None else str(profile.available).lower()
                    print(f"  {marker} {profile.name}: {profile.description} [{availability}]")
        elif args.audio_command == "move":
            backend.move_stream(args.stream_id, args.output)
            current = backend.get_stream_output(args.stream_id)
            if current != args.output:
                raise AudioBackendError(
                    f"move command completed but stream reports {current or 'no resolved output'}"
                )
            print(f"Moved stream {args.stream_id} to {current}")
        elif args.audio_command == "set-profile":
            backend.set_card_profile(args.card, args.profile)
            print(f"Set {args.card} profile to {args.profile}")
    except AudioBackendError as exc:
        print(f"Audio error: {exc}")
        return 2
    return 0


def _safe_media_name(media_name: str | None) -> str:
    if not media_name:
        return "Unknown"
    if "?" in media_name:
        return f"{media_name.split('?', 1)[0]}?<redacted>"
    return media_name


def parse_and_run(arguments: Sequence[str]) -> int | None:
    if not arguments or arguments[0] != "audio":
        return None
    parser = argparse.ArgumentParser(prog="python -m screen_audio_router")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_audio_parser(subparsers)
    args = parser.parse_args(arguments)
    return run_audio_command(args)
