"""Application bootstrap for Screen Audio Router."""

from __future__ import annotations

import logging
import signal
import sys

from screen_audio_router.logging import configure_logging


def startup_arguments(arguments: list[str]) -> tuple[list[str], bool]:
    return [argument for argument in arguments if argument != "--autostart"], (
        "--autostart" in arguments
    )


def main() -> int:
    configure_logging()
    arguments, autostart_launch = startup_arguments(sys.argv[1:])
    from screen_audio_router.audio.cli import parse_and_run

    cli_result = parse_and_run(arguments)
    if cli_result is not None:
        return cli_result

    from screen_audio_router.kwin.cli import parse_and_run as parse_and_run_kwin

    cli_result = parse_and_run_kwin(arguments)
    if cli_result is not None:
        return cli_result

    from screen_audio_router.router.cli import parse_and_run as parse_and_run_apps

    cli_result = parse_and_run_apps(arguments)
    if cli_result is not None:
        return cli_result

    from screen_audio_router.router.debug_cli import parse_and_run as parse_and_run_router

    cli_result = parse_and_run_router(arguments)
    if cli_result is not None:
        return cli_result

    from screen_audio_router.config.cli import parse_and_run as parse_and_run_config

    cli_result = parse_and_run_config(arguments)
    if cli_result is not None:
        return cli_result

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from screen_audio_router.config import ConfigError, ConfigManager
    from screen_audio_router.gui import DesktopApplication
    from screen_audio_router.gui.icons import configure_application_icon
    from screen_audio_router.gui.main_window import configuration_error_message

    logger = logging.getLogger(__name__)
    app = QApplication([sys.argv[0], *arguments])
    app.setApplicationName("Screen Audio Router")
    app.setOrganizationName("Screen Audio Router")
    configure_application_icon(app)
    try:
        config = ConfigManager().load()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        QMessageBox.critical(None, "Configuration Error", configuration_error_message(exc))
        return 2
    logger.info("Starting Screen Audio Router (configuration version %s)", config.version)

    desktop = DesktopApplication(app, config)
    desktop.start(autostart=autostart_launch)
    signal.signal(signal.SIGINT, desktop.handle_sigint)
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(250)
    return app.exec()
