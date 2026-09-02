"""Adapters from persistent configuration to runtime matcher/router inputs."""

from __future__ import annotations

from screen_audio_router.config.schema import AppConfig
from screen_audio_router.router.decisions import ApplicationRoutingRule, DisplayAudioMapping
from screen_audio_router.router.matcher import AliasRule, default_alias_rules, normalize


class RoutingConfigResolver:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def application_rule(self, application_id: str) -> ApplicationRoutingRule:
        saved = self.config.application_rule(application_id)
        return ApplicationRoutingRule(saved.id, saved.mode, saved.fixed_output_id)

    def display_mappings(self) -> tuple[DisplayAudioMapping, ...]:
        return tuple(
            DisplayAudioMapping(mapping.display.id, mapping.output.id)
            for mapping in self.config.display_mappings
        )

    def matcher_aliases(self) -> tuple[AliasRule, ...]:
        combined = {normalize(alias.application_id): alias for alias in default_alias_rules()}
        for alias in self.config.matcher_aliases:
            combined[normalize(alias.application_id)] = AliasRule(
                alias.application_id,
                alias.audio_application_names,
                alias.process_binaries,
            )
        return tuple(combined[key] for key in sorted(combined))
