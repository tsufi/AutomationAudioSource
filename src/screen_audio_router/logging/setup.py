"""Central logging setup."""

from __future__ import annotations

import logging
import os


def configure_logging(level: int | str | None = None) -> None:
    configured_level = level or os.environ.get("SCREEN_AUDIO_ROUTER_LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=configured_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

