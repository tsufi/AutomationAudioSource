"""Typed display mapping, runtime resolution, and explicit migration services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from screen_audio_router.audio.models import AudioOutput, Availability
from screen_audio_router.config import ConfigurationStore
from screen_audio_router.config.schema import (
    AppConfig,
    AudioOutputIdentity,
    DisplayIdentity,
    DisplayMapping,
)
from screen_audio_router.config.schema import (
    DisplayIdentityKind as ConfigIdentityKind,
)
from screen_audio_router.display import (
    DisplayDevice,
)
from screen_audio_router.display import (
    DisplayIdentityKind as DiscoveryIdentityKind,
)
from screen_audio_router.router.decisions import DisplayAudioMapping


class RuntimeResolutionKind(StrEnum):
    RESOLVED_STABLE = "resolved_stable"
    RESOLVED_CONNECTOR_FALLBACK = "resolved_connector_fallback"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class RuntimeDisplayResolution:
    runtime_connector: str
    kind: RuntimeResolutionKind
    display_id: str | None = None
    device: DisplayDevice | None = None


@dataclass(frozen=True, slots=True)
class DisplayMappingRow:
    display_id: str
    persisted_display_id: str | None
    identity_kind: str
    friendly_name: str
    connector: str | None
    connected: bool
    mapped_output_id: str | None
    mapped_output_name: str | None
    output_available: bool
    mapping_origin: str


class MappingOperationStatus(StrEnum):
    APPLIED = "applied"
    NO_CHANGE = "no_change"
    INVALID_DISPLAY = "invalid_display"
    INVALID_OUTPUT = "invalid_output"
    PERSISTENCE_ERROR = "persistence_error"


@dataclass(frozen=True, slots=True)
class MappingOperationResult:
    status: MappingOperationStatus
    display_id: str
    connector: str | None = None
    old_output_id: str | None = None
    new_output_id: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in {MappingOperationStatus.APPLIED, MappingOperationStatus.NO_CHANGE}


class MigrationStatus(StrEnum):
    MIGRATED = "migrated"
    ALREADY_STABLE = "already_stable"
    NO_CANDIDATE = "no_candidate"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"
    PERSISTENCE_ERROR = "persistence_error"


@dataclass(frozen=True, slots=True)
class MigrationResult:
    status: MigrationStatus
    source_display_id: str
    target_display_id: str | None = None
    output_id: str | None = None
    removes_duplicate: bool = False
    error: str | None = None


class RuntimeDisplayResolver:
    """Resolve KWin connector names using only the current discovery snapshot."""

    def __init__(self, displays: tuple[DisplayDevice, ...]) -> None:
        self.displays = displays

    def resolve(self, runtime_connector: str) -> RuntimeDisplayResolution:
        matches = tuple(
            item for item in self.displays if item.connected and item.connector == runtime_connector
        )
        if len(matches) != 1:
            return RuntimeDisplayResolution(runtime_connector, RuntimeResolutionKind.UNRESOLVED)
        device = matches[0]
        kind = (
            RuntimeResolutionKind.RESOLVED_CONNECTOR_FALLBACK
            if device.identity_kind is DiscoveryIdentityKind.CONNECTOR_FALLBACK
            else RuntimeResolutionKind.RESOLVED_STABLE
        )
        return RuntimeDisplayResolution(runtime_connector, kind, device.stable_id, device)

    def resolved_mapping(
        self, runtime_connector: str, config: AppConfig
    ) -> DisplayAudioMapping | None:
        resolution = self.resolve(runtime_connector)
        stable = next(
            (
                item
                for item in config.display_mappings
                if resolution.kind is RuntimeResolutionKind.RESOLVED_STABLE
                and item.display.id == resolution.display_id
            ),
            None,
        )
        selected = stable or _fallback_mapping(config, runtime_connector)
        return DisplayAudioMapping(runtime_connector, selected.output.id) if selected else None

    def routing_mappings(self, config: AppConfig) -> tuple[DisplayAudioMapping, ...]:
        connectors = {item.connector for item in self.displays if item.connected}
        connectors.update(
            connector
            for mapping in config.display_mappings
            if (connector := _fallback_connector(mapping.display)) is not None
        )
        return tuple(
            mapping
            for connector in sorted(connectors)
            if (mapping := self.resolved_mapping(connector, config)) is not None
        )


class DisplayMappingService:
    """Own typed mapping mutations; it never performs routing or discovery."""

    def __init__(self, config: AppConfig, store: ConfigurationStore) -> None:
        self.config = config
        self.store = store

    def rows(
        self, displays: tuple[DisplayDevice, ...], outputs: tuple[AudioOutput, ...]
    ) -> tuple[DisplayMappingRow, ...]:
        devices = {item.stable_id: item for item in displays}
        mappings = {item.display.id: item for item in self.config.display_mappings}
        output_by_id = {item.id: item for item in outputs}
        consumed: set[str] = set()
        rows = []
        for device in displays:
            mapping = mappings.get(device.stable_id) or _fallback_mapping(
                self.config, device.connector
            )
            if mapping:
                consumed.add(mapping.display.id)
            rows.append(self._row_for_device(device, mapping, output_by_id))
        rows.extend(
            self._row_for_saved(mapping, output_by_id)
            for display_id, mapping in mappings.items()
            if display_id not in devices and display_id not in consumed
        )
        return tuple(
            sorted(
                rows,
                key=lambda row: (not row.connected, row.friendly_name.casefold(), row.display_id),
            )
        )

    def assign_output(
        self,
        display_id: str,
        output_id: str,
        displays: tuple[DisplayDevice, ...],
        outputs: tuple[AudioOutput, ...],
    ) -> MappingOperationResult:
        device = next((item for item in displays if item.stable_id == display_id), None)
        output = next((item for item in outputs if item.id == output_id), None)
        if device is None:
            return MappingOperationResult(MappingOperationStatus.INVALID_DISPLAY, display_id)
        if output is None or output_id.isdecimal():
            return MappingOperationResult(
                MappingOperationStatus.INVALID_OUTPUT, display_id, device.connector
            )
        previous = next(
            (item for item in self.config.display_mappings if item.display.id == display_id), None
        )
        if previous and previous.output.id == output_id:
            return MappingOperationResult(
                MappingOperationStatus.NO_CHANGE, display_id, device.connector, output_id, output_id
            )
        mapping = DisplayMapping(_config_identity(device), _output_identity(output))
        old_state = list(self.config.display_mappings)
        self.config.set_display_mapping(mapping)
        try:
            self.store.save(self.config)
        except Exception as exc:  # noqa: BLE001 - typed persistence boundary
            self.config.display_mappings = old_state
            return MappingOperationResult(
                MappingOperationStatus.PERSISTENCE_ERROR,
                display_id,
                device.connector,
                previous.output.id if previous else None,
                output_id,
                str(exc),
            )
        return MappingOperationResult(
            MappingOperationStatus.APPLIED,
            display_id,
            device.connector,
            previous.output.id if previous else None,
            output_id,
        )

    def remove_mapping(
        self, display_id: str, displays: tuple[DisplayDevice, ...] = ()
    ) -> MappingOperationResult:
        previous = next(
            (item for item in self.config.display_mappings if item.display.id == display_id), None
        )
        connector = next(
            (item.connector for item in displays if item.stable_id == display_id), None
        )
        if previous is None:
            return MappingOperationResult(MappingOperationStatus.NO_CHANGE, display_id, connector)
        old_state = list(self.config.display_mappings)
        self.config.remove_display_mapping(display_id)
        try:
            self.store.save(self.config)
        except Exception as exc:  # noqa: BLE001
            self.config.display_mappings = old_state
            return MappingOperationResult(
                MappingOperationStatus.PERSISTENCE_ERROR,
                display_id,
                connector,
                previous.output.id,
                error=str(exc),
            )
        return MappingOperationResult(
            MappingOperationStatus.APPLIED, display_id, connector, previous.output.id
        )

    def preview_migration(
        self, source_display_id: str, displays: tuple[DisplayDevice, ...]
    ) -> MigrationResult:
        source = next(
            (item for item in self.config.display_mappings if item.display.id == source_display_id),
            None,
        )
        if source is None:
            return MigrationResult(MigrationStatus.NO_CANDIDATE, source_display_id)
        connector = _fallback_connector(source.display)
        if connector is None:
            return MigrationResult(
                MigrationStatus.ALREADY_STABLE,
                source_display_id,
                source_display_id,
                source.output.id,
            )
        candidates = tuple(
            item
            for item in displays
            if item.connected
            and item.connector == connector
            and item.identity_kind is not DiscoveryIdentityKind.CONNECTOR_FALLBACK
        )
        if not candidates:
            return MigrationResult(
                MigrationStatus.NO_CANDIDATE, source_display_id, output_id=source.output.id
            )
        if len(candidates) != 1:
            return MigrationResult(
                MigrationStatus.AMBIGUOUS, source_display_id, output_id=source.output.id
            )
        target = candidates[0]
        existing = next(
            (item for item in self.config.display_mappings if item.display.id == target.stable_id),
            None,
        )
        if existing and existing.output.id != source.output.id:
            return MigrationResult(
                MigrationStatus.CONFLICT, source_display_id, target.stable_id, source.output.id
            )
        return MigrationResult(
            MigrationStatus.MIGRATED,
            source_display_id,
            target.stable_id,
            source.output.id,
            existing is not None,
        )

    def migrate_fallback(
        self, source_display_id: str, displays: tuple[DisplayDevice, ...]
    ) -> MigrationResult:
        preview = self.preview_migration(source_display_id, displays)
        if preview.status is not MigrationStatus.MIGRATED:
            return preview
        source = next(
            item for item in self.config.display_mappings if item.display.id == source_display_id
        )
        target = next(item for item in displays if item.stable_id == preview.target_display_id)
        old_state = list(self.config.display_mappings)
        self.config.remove_display_mapping(source_display_id)
        if not preview.removes_duplicate:
            self.config.set_display_mapping(DisplayMapping(_config_identity(target), source.output))
        try:
            self.store.save(self.config)
        except Exception as exc:  # noqa: BLE001
            self.config.display_mappings = old_state
            return MigrationResult(
                MigrationStatus.PERSISTENCE_ERROR,
                source_display_id,
                preview.target_display_id,
                source.output.id,
                preview.removes_duplicate,
                str(exc),
            )
        return preview

    @staticmethod
    def _row_for_device(device, mapping, outputs):
        output = mapping.output if mapping else None
        return DisplayMappingRow(
            device.stable_id,
            mapping.display.id if mapping else None,
            device.identity_kind.value,
            device.friendly_name,
            device.connector,
            True,
            output.id if output else None,
            output.description if output else None,
            _available(outputs.get(output.id)) if output else False,
            (
                "connector_fallback"
                if mapping
                and mapping.display.identity_kind is ConfigIdentityKind.CONNECTOR_FALLBACK
                else "stable"
                if mapping
                else "none"
            ),
        )

    @staticmethod
    def _row_for_saved(mapping, outputs):
        display = mapping.display
        return DisplayMappingRow(
            display.id,
            display.id,
            display.identity_kind.value,
            display.friendly_name or display.model or display.id,
            None,
            False,
            mapping.output.id,
            mapping.output.description or mapping.output.id,
            _available(outputs.get(mapping.output.id)),
            (
                "connector_fallback"
                if display.identity_kind is ConfigIdentityKind.CONNECTOR_FALLBACK
                else "stable"
            ),
        )


def _config_identity(device: DisplayDevice) -> DisplayIdentity:
    fallback = device.identity_kind is DiscoveryIdentityKind.CONNECTOR_FALLBACK
    return DisplayIdentity(
        device.stable_id,
        connector=device.connector if fallback else None,
        manufacturer=device.manufacturer,
        model=device.model,
        serial=device.serial,
        friendly_name=device.friendly_name,
        identity_kind=ConfigIdentityKind.CONNECTOR_FALLBACK
        if fallback
        else ConfigIdentityKind.STABLE,
    )


def _output_identity(output: AudioOutput) -> AudioOutputIdentity:
    return AudioOutputIdentity(output.id, output.id, output.description, output.card_name)


def _available(output: AudioOutput | None) -> bool:
    return output is not None and output.availability is not Availability.UNAVAILABLE


def _fallback_connector(identity: DisplayIdentity) -> str | None:
    if identity.identity_kind is not ConfigIdentityKind.CONNECTOR_FALLBACK:
        return None
    return identity.connector or identity.id.removeprefix("connector:")


def _fallback_mapping(config: AppConfig, connector: str) -> DisplayMapping | None:
    return next(
        (
            item
            for item in config.display_mappings
            if _fallback_connector(item.display) == connector
        ),
        None,
    )
