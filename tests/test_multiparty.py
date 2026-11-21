import asyncio


import time


from types import SimpleNamespace


import pytest


from revops_buddy.conversation import ActivationGate, AnswerService


from test_answer_service import FakeClient


from test_voice_runtime import _start_session, _send_audio


def test_followup_belongs_to_activator_and_does_not_refresh_for_others():
    now = [0.0]
    gate = ActivationGate(clock=lambda: now[0])
    assert gate.observe("Angels, pipeline?", speaker=" Taylor   Example ").reason == "activation_name"
    now[0] = 2
    assert gate.observe("what about Q3?", speaker="taylor example").reason == "followup_window"
    now[0] = 7
    other = gate.observe("what about Q3?", speaker="Tom")
    assert not other.activated and other.reason == "other_speaker"
    assert gate._last_activation == 2
    assert gate.observe("what about Q3?", speaker=None).activated
    gate.interrupt()
    assert gate._activator is None
    assert not gate.observe("what about Q3?", speaker="Taylor Example").activated
    gate.observe("Angels, pipeline?", speaker="Taylor")
    now[0] += 9
    assert gate.observe("what about Q3?", speaker="Tom").reason == "not_addressed"


def test_offer_reply_waits_for_activator():
    gate = ActivationGate()
    gate.observe("Angels, pipeline?", speaker="Taylor")
    gate.expect_reply()
    other = gate.observe("yes", speaker="Tom")
    assert not other.activated and other.reason == "other_speaker"
    assert gate.observe("yes", speaker=" TAYLOR ").reason == "offer_reply"


def test_unknown_activator_and_solo_keep_existing_behavior():
    gate = ActivationGate()
    gate.observe("Angels, pipeline?", speaker="user")
    assert gate.observe("what about Q3?", speaker="Tom").activated
    gate.observe("Angels, pipeline?", speaker="Taylor")
    assert gate.observe("what about Q3?", speaker="Tom", human_count=1).reason == "solo"
    assert gate.observe("stop", speaker="Tom").reason == "stop_command"
    assert gate._activator is None


def test_solo_activation_clears_stale_activator():
    gate = ActivationGate(clock=lambda: 0.0)
    assert gate.observe("Angels, pipeline?", human_count=3, speaker="Taylor").activated
    assert gate.observe("what about Q3?", human_count=1, speaker="Tom").reason == "solo"
    assert gate._activator is None


