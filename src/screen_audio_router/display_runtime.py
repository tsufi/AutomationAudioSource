"""Runtime ownership for read-only display snapshots and mapping resolution."""

from __future__ import annotations

from dataclasses import dataclass

from screen_audio_router.config import ConfigurationStore
from screen_audio_router.config.schema import AppConfig
from screen_audio_router.display import DisplayDevice, DisplayDiscovery
from screen_audio_router.display_mapping import DisplayMappingService, RuntimeDisplayResolver
from screen_audio_router.router.decisions import DisplayAudioMapping


@dataclass(frozen=True, slots=True)
class DisplayRefreshResult:
    displays: tuple[DisplayDevice, ...]
    changed: bool
    error: str | None = None


class DisplayRuntimeContext:
    """Atomically replace discovery snapshots without writing configuration."""

    def __init__(
        self,
        config: AppConfig,
        store: ConfigurationStore,
        discovery: DisplayDiscovery | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.discovery = discovery or DisplayDiscovery()
        self.service = DisplayMappingService(config, store)
        self.displays: tuple[DisplayDevice, ...] = ()
        self.error: str | None = None

    def refresh(self) -> DisplayRefreshResult:
        try:
            discovered = tuple(self.discovery.discover())
        except OSError as exc:
            self.error = str(exc)
            return DisplayRefreshResult(self.displays, False, self.error)
        changed = discovered != self.displays
        self.displays = discovered
        self.error = None
        return DisplayRefreshResult(self.displays, changed)

    def resolver(self) -> RuntimeDisplayResolver:
        return RuntimeDisplayResolver(self.displays)

    def routing_mappings(self) -> tuple[DisplayAudioMapping, ...]:
        return self.resolver().routing_mappings(self.config)
