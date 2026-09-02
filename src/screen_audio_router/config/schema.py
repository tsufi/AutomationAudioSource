"""Versioned, dependency-free configuration models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

CURRENT_CONFIG_VERSION = 1


class ConfigError(ValueError):
    """Raised when persisted configuration is invalid or unsupported."""


class RoutingMode(StrEnum):
    IGNORE = "ignore"
    AUTO = "auto"
    FIXED_DEVICE = "fixed"


class DisplayIdentityKind(StrEnum):
    STABLE = "stable"
    CONNECTOR_FALLBACK = "connector_fallback"


@dataclass(slots=True)
class GeneralSettings:
    poll_interval_ms: int = 2000
    start_minimized: bool = False
    close_to_tray: bool = True

    def validate(self) -> None:
        if not 250 <= self.poll_interval_ms <= 60_000:
            raise ConfigError("poll_interval_ms must be between 250 and 60000")


@dataclass(slots=True)
class DisplayIdentity:
    id: str
    connector: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    uuid: str | None = None
    friendly_name: str | None = None
    identity_kind: DisplayIdentityKind = DisplayIdentityKind.CONNECTOR_FALLBACK


@dataclass(slots=True)
class AudioOutputIdentity:
    id: str
    sink_name: str
    description: str | None = None
    card_name: str | None = None
    profile: str | None = None


@dataclass(slots=True)
class DisplayMapping:
    display: DisplayIdentity
    output: AudioOutputIdentity


@dataclass(slots=True)
class MatchRule:
    resource_class: str | None = None
    process_binary: str | None = None
    application_name: str | None = None


@dataclass(slots=True)
class ApplicationRule:
    id: str
    name: str
    mode: RoutingMode = RoutingMode.IGNORE
    window_match: MatchRule = field(default_factory=MatchRule)
    audio_match: MatchRule = field(default_factory=MatchRule)
    fixed_output_id: str | None = None

    def validate(self) -> None:
        if self.mode is RoutingMode.FIXED_DEVICE and not self.fixed_output_id:
            raise ConfigError(f"fixed application rule {self.id!r} requires fixed_output_id")
        if self.mode is not RoutingMode.FIXED_DEVICE and self.fixed_output_id is not None:
            raise ConfigError(f"non-fixed application rule {self.id!r} cannot set fixed_output_id")


@dataclass(slots=True)
class MatcherAliasConfig:
    application_id: str
    audio_application_names: tuple[str, ...] = ()
    process_binaries: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.application_id:
            raise ConfigError("matcher alias requires application_id")
        if not self.audio_application_names and not self.process_binaries:
            raise ConfigError(f"matcher alias {self.application_id!r} has no audio identities")


@dataclass(slots=True)
class AppConfig:
    version: int = CURRENT_CONFIG_VERSION
    enabled: bool = True
    settings: GeneralSettings = field(default_factory=GeneralSettings)
    display_mappings: list[DisplayMapping] = field(default_factory=list)
    applications: list[ApplicationRule] = field(default_factory=list)
    matcher_aliases: list[MatcherAliasConfig] = field(default_factory=list)
    unknown_fields: dict[str, Any] = field(default_factory=dict, repr=False)
    validation_warnings: list[str] = field(default_factory=list, repr=False, compare=False)

    def validate(self) -> None:
        if self.version != CURRENT_CONFIG_VERSION:
            raise ConfigError(f"unsupported configuration version: {self.version}")
        self.settings.validate()
        for rule in self.applications:
            rule.validate()
        for alias in self.matcher_aliases:
            alias.validate()

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.unknown_fields)
        data.update(
            {
                "version": self.version,
                "enabled": self.enabled,
                "settings": {
                    "poll_interval_ms": self.settings.poll_interval_ms,
                    "start_minimized": self.settings.start_minimized,
                    "close_to_tray": self.settings.close_to_tray,
                },
                "display_mappings": [
                    {
                        "display": {
                            "id": mapping.display.id,
                            "connector": mapping.display.connector,
                            "manufacturer": mapping.display.manufacturer,
                            "model": mapping.display.model,
                            "serial": mapping.display.serial,
                            "uuid": mapping.display.uuid,
                            "friendly_name": mapping.display.friendly_name,
                            "identity_kind": mapping.display.identity_kind.value,
                        },
                        "output": {
                            "id": mapping.output.id,
                            "sink_name": mapping.output.sink_name,
                            "description": mapping.output.description,
                            "card_name": mapping.output.card_name,
                            "profile": mapping.output.profile,
                        },
                    }
                    for mapping in self.display_mappings
                ],
                "applications": [
                    {
                        "id": rule.id,
                        "name": rule.name,
                        "mode": rule.mode.value,
                        "window_match": {
                            "resource_class": rule.window_match.resource_class,
                            "process_binary": rule.window_match.process_binary,
                            "application_name": rule.window_match.application_name,
                        },
                        "audio_match": {
                            "resource_class": rule.audio_match.resource_class,
                            "process_binary": rule.audio_match.process_binary,
                            "application_name": rule.audio_match.application_name,
                        },
                        "fixed_output_id": rule.fixed_output_id,
                    }
                    for rule in self.applications
                ],
                "matcher_aliases": [
                    {
                        "application_id": alias.application_id,
                        "audio_application_names": list(alias.audio_application_names),
                        "process_binaries": list(alias.process_binaries),
                    }
                    for alias in self.matcher_aliases
                ],
            }
        )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        version = data.get("version", CURRENT_CONFIG_VERSION)
        if version != CURRENT_CONFIG_VERSION:
            raise ConfigError(f"unsupported configuration version: {version}")
        warnings: list[str] = []
        try:
            settings_data = data.get("settings", {})
            mappings = [
                DisplayMapping(
                    display=DisplayIdentity(
                        **(
                            item["display"]
                            | {
                                "identity_kind": DisplayIdentityKind(
                                    item["display"].get(
                                        "identity_kind", DisplayIdentityKind.CONNECTOR_FALLBACK
                                    )
                                )
                            }
                        )
                    ),
                    output=AudioOutputIdentity(**item["output"]),
                )
                for item in data.get("display_mappings", [])
            ]
            applications: list[ApplicationRule] = []
            for item in data.get("applications", []):
                rule = ApplicationRule(
                    id=item["id"],
                    name=item["name"],
                    mode=RoutingMode(item.get("mode", RoutingMode.IGNORE)),
                    window_match=MatchRule(**item.get("window_match", {})),
                    audio_match=MatchRule(**item.get("audio_match", {})),
                    fixed_output_id=item.get("fixed_output_id"),
                )
                try:
                    rule.validate()
                except ConfigError as exc:
                    warnings.append(str(exc))
                else:
                    applications.append(rule)
            aliases = [
                MatcherAliasConfig(
                    application_id=item["application_id"],
                    audio_application_names=tuple(item.get("audio_application_names", [])),
                    process_binaries=tuple(item.get("process_binaries", [])),
                )
                for item in data.get("matcher_aliases", [])
            ]
            known = {
                "version",
                "enabled",
                "settings",
                "display_mappings",
                "applications",
                "matcher_aliases",
            }
            config = cls(
                version=version,
                enabled=data.get("enabled", True),
                settings=GeneralSettings(**settings_data),
                display_mappings=mappings,
                applications=applications,
                matcher_aliases=aliases,
                unknown_fields={key: value for key, value in data.items() if key not in known},
                validation_warnings=warnings,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"invalid configuration: {exc}") from exc
        config.validate()
        return config

    def application_rule(self, application_id: str) -> ApplicationRule:
        return next(
            (rule for rule in self.applications if rule.id.casefold() == application_id.casefold()),
            ApplicationRule(application_id, application_id, RoutingMode.IGNORE),
        )

    def has_application_rule(self, application_id: str) -> bool:
        return any(
            rule.id.casefold() == application_id.casefold() for rule in self.applications
        )

    def set_application_rule(self, rule: ApplicationRule) -> None:
        rule.validate()
        self.applications = [item for item in self.applications if item.id.casefold() != rule.id.casefold()]
        self.applications.append(rule)
        self.applications.sort(key=lambda item: item.id.casefold())

    def remove_application_rule(self, application_id: str) -> bool:
        original = len(self.applications)
        self.applications = [
            rule for rule in self.applications if rule.id.casefold() != application_id.casefold()
        ]
        return len(self.applications) != original

    def set_display_mapping(self, mapping: DisplayMapping) -> None:
        self.display_mappings = [
            item for item in self.display_mappings if item.display.id != mapping.display.id
        ]
        self.display_mappings.append(mapping)
        self.display_mappings.sort(key=lambda item: item.display.id)

    def remove_display_mapping(self, display_id: str) -> bool:
        original = len(self.display_mappings)
        self.display_mappings = [
            item for item in self.display_mappings if item.display.id != display_id
        ]
        return len(self.display_mappings) != original
