import json
import os

import pytest

from screen_audio_router.audio.models import AudioOutput, AudioStream, Availability
from screen_audio_router.config import AppConfig, ConfigError, ConfigManager, ConfigurationStore
from screen_audio_router.config.cli import parse_and_run
from screen_audio_router.config.manager import default_config_path
from screen_audio_router.config.resolver import RoutingConfigResolver
from screen_audio_router.config.schema import (
    ApplicationRule,
    AudioOutputIdentity,
    DisplayIdentity,
    DisplayIdentityKind,
    DisplayMapping,
    MatcherAliasConfig,
    RoutingMode,
)
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.application_registry import ApplicationEntry, MatchedStream
from screen_audio_router.router.engine import RouterEngine
from screen_audio_router.router.matcher import MatchConfidence, MatchResult


def test_default_config_round_trip(tmp_path):
    path = tmp_path / "screen-audio-router" / "config.json"
    manager = ConfigManager(path)

    loaded = manager.load()

    assert loaded == AppConfig()
    assert json.loads(path.read_text())["version"] == 1


def test_unknown_application_rule_defaults_to_ignore():
    rule = ApplicationRule(id="new-app", name="New App")

    assert rule.mode is RoutingMode.IGNORE


def test_config_unknown_application_resolves_to_ignore():
    rule = AppConfig().application_rule("never-seen")
    assert rule.id == "never-seen"
    assert rule.mode is RoutingMode.IGNORE


def test_global_enabled_state_persists(tmp_path):
    store = ConfigurationStore(tmp_path / "config.json")
    store.save(AppConfig(enabled=False))
    assert store.load().enabled is False


def test_fixed_rule_requires_output():
    config = AppConfig(
        applications=[ApplicationRule(id="chat", name="Chat", mode=RoutingMode.FIXED_DEVICE)]
    )

    with pytest.raises(ConfigError, match="requires fixed_output_id"):
        config.validate()


def test_invalid_existing_config_is_not_overwritten(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("not json")

    with pytest.raises(ConfigError):
        ConfigManager(path).load()

    assert path.read_text() == "not json"


@pytest.mark.parametrize("mode", [RoutingMode.AUTO, RoutingMode.IGNORE, RoutingMode.FIXED_DEVICE])
def test_application_modes_persist(mode, tmp_path):
    output = "sink.stable" if mode is RoutingMode.FIXED_DEVICE else None
    config = AppConfig(applications=[ApplicationRule("app", "App", mode, fixed_output_id=output)])
    store = ConfigurationStore(tmp_path / "config.json")
    store.save(config)
    assert store.load().application_rule("app") == config.applications[0]


def test_invalid_fixed_rule_is_skipped_with_warning(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "applications": [{"id": "bad", "name": "Bad", "mode": "fixed"}],
            }
        )
    )
    loaded = ConfigurationStore(path).load()
    assert loaded.applications == []
    assert "requires fixed_output_id" in loaded.validation_warnings[0]
    assert json.loads(path.read_text())["applications"][0]["id"] == "bad"


def mapping(display, output, *, display_name=None, output_name=None):
    return DisplayMapping(
        DisplayIdentity(
            display,
            connector=display,
            friendly_name=display_name,
            identity_kind=DisplayIdentityKind.CONNECTOR_FALLBACK,
        ),
        AudioOutputIdentity(output, output, output_name),
    )


def test_mappings_missing_hardware_and_shared_output_survive_round_trip(tmp_path):
    config = AppConfig(
        display_mappings=[mapping("display-a", "sink.one"), mapping("display-b", "sink.one")]
    )
    store = ConfigurationStore(tmp_path / "config.json")
    store.save(config)
    loaded = store.load()
    assert [(item.display.id, item.output.id) for item in loaded.display_mappings] == [
        ("display-a", "sink.one"),
        ("display-b", "sink.one"),
    ]
    assert loaded.display_mappings[0].display.identity_kind is DisplayIdentityKind.CONNECTOR_FALLBACK


def test_unmapped_display_stays_unmapped():
    config = AppConfig(display_mappings=[mapping("mapped", "sink")])
    assert config.remove_display_mapping("other") is False
    assert [item.display.id for item in config.display_mappings] == ["mapped"]


def test_atomic_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"original": true}')

    def fail_replace(source, destination):
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated crash"):
        ConfigurationStore(path).save(AppConfig())
    assert path.read_text() == '{"original": true}'
    assert list(tmp_path.iterdir()) == [path]


def test_store_creates_directory(tmp_path):
    path = tmp_path / "nested" / "config.json"
    ConfigurationStore(path).save(AppConfig())
    assert path.exists()


def test_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_config_path() == tmp_path / "screen-audio-router" / "config.json"


def test_future_version_rejected_without_overwrite(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"version": 999}')
    with pytest.raises(ConfigError, match="unsupported configuration version"):
        ConfigurationStore(path).load()
    assert path.read_text() == '{"version": 999}'


def test_unknown_top_level_fields_are_preserved(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"version": 1, "future_feature": {"enabled": true}}')
    store = ConfigurationStore(path)
    config = store.load()
    store.save(config)
    assert json.loads(path.read_text())["future_feature"] == {"enabled": True}


def test_runtime_identifiers_are_not_serialized():
    raw = json.dumps(AppConfig().to_dict())
    assert "runtime_id" not in raw
    assert "stream_id" not in raw
    assert '"pid"' not in raw


def test_matcher_alias_persists_and_user_alias_overrides_bootstrap(tmp_path):
    config = AppConfig(
        matcher_aliases=[MatcherAliasConfig("com.edde746.plezy", ("custom-helper",))]
    )
    store = ConfigurationStore(tmp_path / "config.json")
    store.save(config)
    aliases = RoutingConfigResolver(store.load()).matcher_aliases()
    plezy = next(item for item in aliases if item.application_id == "com.edde746.plezy")
    assert plezy.audio_application_names == ("custom-helper",)


def test_bootstrap_plezy_alias_remains_without_saved_alias():
    aliases = RoutingConfigResolver(AppConfig()).matcher_aliases()
    assert any(
        alias.application_id == "com.edde746.plezy" and "mpv" in alias.audio_application_names
        for alias in aliases
    )


def test_router_engine_consumes_loaded_rule_and_mapping(tmp_path):
    config = AppConfig(
        applications=[ApplicationRule("app", "App", RoutingMode.AUTO)],
        display_mappings=[mapping("display", "sink.stable", display_name="Monitor", output_name="Speakers")],
    )
    store = ConfigurationStore(tmp_path / "config.json")
    store.save(config)
    resolver = RoutingConfigResolver(store.load())
    window = WindowInfo("runtime-only", "app", "app", "Title", 123, "display", False, True, "normal")
    stream = AudioStream("456", application_name="app", current_output="other")
    entry = ApplicationEntry(
        "app",
        "App",
        (window,),
        (MatchedStream(stream, MatchResult("app", MatchConfidence.HIGH, "test", 85)),),
        window,
        False,
        "display",
        ("other",),
    )
    decision = RouterEngine().decide(
        entry,
        resolver.application_rule("app"),
        resolver.display_mappings(),
        [AudioOutput("sink.stable", "Renamed speakers", Availability.AVAILABLE)],
        enabled=resolver.enabled,
    )
    assert decision.desired_output == "sink.stable"
    assert decision.stream_ids == ("456",)
    persisted = json.loads(store.path.read_text())
    assert persisted["display_mappings"][0]["output"]["id"] == "sink.stable"
    assert persisted["display_mappings"][0]["output"]["description"] == "Speakers"


def test_config_cli_change_does_not_import_or_execute_audio(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert parse_and_run(["config", "set-app", "new-app", "auto"]) == 0
    loaded = ConfigurationStore().load()
    assert loaded.application_rule("new-app").mode is RoutingMode.AUTO
