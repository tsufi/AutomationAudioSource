from screen_audio_router.audio.models import AudioStream
from screen_audio_router.kwin.models import WindowInfo
from screen_audio_router.router.matcher import (
    AliasRule,
    ApplicationMatcher,
    MatchConfidence,
    normalize,
)


def window(app_id="brave-browser", pid=100):
    return WindowInfo(
        runtime_id="window-1",
        resource_class=app_id,
        resource_name="brave",
        caption="Changing title",
        pid=pid,
        output_name="DP-2",
        normal_window=True,
        window_type="normal",
    )


def test_exact_pid_match_is_strongest():
    result = ApplicationMatcher().match(
        AudioStream("1", application_name="unrelated", process_id=100),
        {"brave-browser": [window()]},
    )
    assert result.application_id == "brave-browser"
    assert result.confidence is MatchConfidence.EXACT
    assert result.reason == "exact PID match"


def test_normalized_application_identity_match():
    result = ApplicationMatcher().match(
        AudioStream("1", application_name="BRAVE-BROWSER"),
        {"brave-browser": [window()]},
    )
    assert result.confidence is MatchConfidence.HIGH
    assert "identity" in result.reason


def test_alias_rule_is_explainable_and_not_in_generic_logic():
    matcher = ApplicationMatcher((AliasRule("com.edde746.plezy", ("mpv",)),))
    result = matcher.match(
        AudioStream("327", application_name="mpv"),
        {"com.edde746.plezy": [window("com.edde746.plezy", 5776)]},
    )
    assert result.application_id == "com.edde746.plezy"
    assert result.confidence is MatchConfidence.HIGH
    assert result.reason == "configured alias"


def test_equal_candidate_applications_are_unmatched():
    stream = AudioStream("1", application_name="helper")
    windows = {"one": [window("one")], "two": [window("two")]}
    matcher = ApplicationMatcher((AliasRule("one", ("helper",)), AliasRule("two", ("helper",))))
    result = matcher.match(stream, windows)
    assert result.application_id is None
    assert result.confidence is MatchConfidence.UNMATCHED
    assert "ambiguous" in result.reason


def test_normalization_is_conservative():
    assert normalize(" Brave-Browser ") == "brave-browser"
    assert normalize("com.example.App") == "com.example.app"
    assert normalize("com.example.App") != normalize("example-app")
