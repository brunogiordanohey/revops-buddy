from revops_buddy.conversation import ActivationGate
import pytest


def test_angels_accepts_singular_speech_recognition_alias():
    for name in ("Angel", "Angels", "Angels’", "Angel's", "Angelus"):
        assert ActivationGate().observe(f"{name}, introduce yourself").activated
    assert not ActivationGate().observe("That sounds angelic").activated
    assert not ActivationGate().observe("The angeluses are here").activated


def test_custom_activation_name_is_honored():
    gate = ActivationGate(activation_name="Revenue")
    assert not gate.observe("Angels, introduce yourself").activated
    assert gate.observe("Revenue, introduce yourself").activated


@pytest.mark.parametrize("text", ["Stop.", "Angel, stop.", "Angelus, stop.", "Angels, please stop", "Please stop talking", "That's enough", "Stop, Angels"])
def test_stop_commands_never_activate_an_answer(text):
    gate = ActivationGate()
    gate.observe("Angels, introduce yourself")
    decision = gate.observe(text)
    assert not decision.activated
    assert decision.reason == "stop_command"
    assert not gate.observe("What else?").activated


@pytest.mark.parametrize("text", ["Don't stop", "Stop by tomorrow", "Tell me how to stop a slipping deal", "Why did you stop?"])
def test_mentions_of_stop_are_not_silence_controls(text):
    assert not ActivationGate().is_stop_command(text)
