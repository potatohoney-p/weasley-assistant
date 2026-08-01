"""The wake-word matcher is a pure function — so it gets deterministic evals.
Whisper mangles phrases in predictable ways; these cases pin the fuzziness."""

import pytest

from weasley_assistant.gateway.voice import matches_wake

SHOULD_WAKE = [
    ("weasley", "weasley"),
    ("Weasley!", "weasley"),                         # punctuation
    ("wesley", "weasley"),                           # one-letter transcription miss
    ("so anyway hey weasley schedule it", "hey weasley"),  # embedded in speech
    ("Hey Weasley", "hey weasley"),
    ("hey computer, what's up", "hey computer"),
    ("小助手你好", "hey weasley,小助手"),             # explicit multilingual variant
]

SHOULD_NOT_WAKE = [
    ("what a nice day", "hey weasley"),
    ("wake up call at nine", "hey weasley"),
    ("", "hey weasley"),
    ("hey weasley", ""),                              # no wake word configured
    ("we sell things", "hey weasley"),
]


@pytest.mark.parametrize("heard,wake", SHOULD_WAKE, ids=[h for h, _ in SHOULD_WAKE])
def test_wakes(heard, wake):
    assert matches_wake(heard, wake)


@pytest.mark.parametrize("heard,wake", SHOULD_NOT_WAKE, ids=[h or "empty" for h, _ in SHOULD_NOT_WAKE])
def test_stays_asleep(heard, wake):
    assert not matches_wake(heard, wake)
