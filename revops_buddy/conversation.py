"""Conversation gating and provider-backed answer generation for Angels."""

from __future__ import annotations

import asyncio
import copy
from difflib import SequenceMatcher
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Protocol

import anthropic


def spoken_sentences(text: str) -> list[str]:
    parts, start = [], 0
    abbreviations = {"mr", "mrs", "ms", "dr", "prof", "sr", "sra", "srta", "sig", "sig.ra",
                     "dott", "dott.ssa", "avv", "ing", "etc", "e.g", "i.e", "vs", "no", "n", "usd", "eur"}
    for match in re.finditer(r"[.!?]+[\"'”’»)]*\s+", text):
        end = match.start() + len(match.group().rstrip())
        prefix = text[:match.start() + 1]
        if text[match.start()] == ".":
            token = re.search(r"([\w.]+)\.$", prefix)
            word = token.group(1) if token else ""
            if word.casefold() in abbreviations or re.fullmatch(r"(?:[A-Za-z]\.)*[A-Za-z]", word):
                continue
        parts.append(text[start:end].strip())
        start = match.end()
    parts.append(text[start:])
    return parts


_LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "it": "Italian", "fr": "French", "de": "German", "pt": "Portuguese"}


class SpokenAnnouncement(str):
    """A standalone utterance rather than another sentence of an answer."""


def _consent_text(text: str) -> str:
    return " ".join(re.sub(r"[.!?,¡¿\"“”«»]", "", text.casefold()).split())


def _known_speaker(speaker: str | None) -> str | None:
    name = " ".join(speaker.split()).casefold() if isinstance(speaker, str) else ""
    return name if name and name != "user" else None


PROVIDER_FAILURE = (
    "Sorry, I can’t reach the revenue service right now. Please try again in a moment."
)


MISSING_MODEL_FAILURE = (
    "Sorry, Angels is not configured with a supported answer model. Ask the operator "
    "to complete provider setup."
)


ACKNOWLEDGMENTS = {
    "en": ("One moment.", "Sure, one second."),
    "es": ("Un momento.", "Claro, un segundo."),
    "it": ("Un attimo.", "Certo, un secondo."),
}


ALL_ACKNOWLEDGMENT_PHRASES = tuple(p for phrases in ACKNOWLEDGMENTS.values() for p in phrases)


class RevOpsToolService(Protocol):
    """The small part of ``angels.domain.service.RevOpsService`` used here."""

    def tool_definitions(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ActivationDecision:
    """Result of applying the named-activation policy to one final utterance."""

    activated: bool
    text: str
    reason: str


class ActivationGate:
    """Permit named requests and a short, explicit follow-up window.

    The gate intentionally does not activate every utterance near a request. A
    follow-up must look like a continuation (a question, comparison, filter
    change, or request beginning with words such as ``and``/``also``). Ordinary
    discussion therefore remains in the transcript but is not sent to the LLM.
    """

    _NAME_RE = re.compile(r"(?i)(?<!\w)angels(?:['’]s)?(?!\w)")
    _FOLLOWUP_RE = re.compile(
        r"""(?ix)
        ^
        (?:and|also|but|only|excluding|including|what|how|why|when|who|which|
           where|is|are|was|were|did|does|do|will|would|should|could|can|
           please|repeat|compare|show|tell\s+me)
        \b
        """
    )

    def __init__(
        self,
        *,
        activation_name: str = "Angels",
        followup_seconds: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not activation_name.strip():
            raise ValueError("activation_name must not be empty")
        if followup_seconds <= 0:
            raise ValueError("followup_seconds must be positive")
        self.activation_name = activation_name
        # Realtime STT spells the spoken wake name many ways (seen live: "Angelus",
        # "Angelos"); accept the an[gj]el- family but not words like "angle".
        spoken_name = r"an[gj]el(?:[aeiou]s?|s)?" if activation_name.casefold() == "angels" else re.escape(activation_name)
        self._NAME_RE = re.compile(r"(?i)(?<!\w)" + spoken_name + r"(?:['’]s)?(?!\w)")
        self.followup_seconds = float(followup_seconds)
        self._clock = clock
        self._last_activation: float | None = None
        self._awaiting_reply = False
        self._finished_speaking: float | None = None
        self._activator: str | None = None

    _REPLY_RE = re.compile(
        r"(?i)^(?:yes|yeah|yep|sure|ok|okay|please|go ahead|do it|no|nope|not now"
        r"|sí|si|sì|claro|vale|venga|dale|por favor|certo|va bene|no gracias|no grazie)\b"
    )

    @property
    def awaiting_reply(self) -> bool:
        return self._awaiting_reply

    def expect_reply(self) -> None:
        """Angels just asked something (e.g. an offer); accept one short reply without the name."""
        self._awaiting_reply = True
        if self._last_activation is not None:
            self._last_activation = self._clock()

    _FILLERS = frozenset({"ok", "okay", "sure", "thanks", "thank you", "uh huh", "uhhuh",
        "uh", "um", "hmm", "yes", "yeah", "yep", "no", "right", "all right", "alright",
        "vale", "gracias", "sí", "si", "claro", "bien", "de acuerdo", "va bene", "grazie",
        "certo", "sì", "bene", "daccordo", "perfetto", "perfect", "perfecto", "exactly", "ok thanks", "okay thanks", "no thanks", "muchas gracias", "d accordo", "uh huh thanks"})

    def observe(self, text: str, *, human_count: int | None = None, speaker: str | None = None) -> ActivationDecision:
        """Observe a final transcript and decide whether Angels should answer."""

        clean = " ".join((text or "").split())
        if not clean:
            return ActivationDecision(False, clean, "empty")
        if self.is_stop_command(clean):
            self.interrupt()
            return ActivationDecision(False, clean, "stop_command")

        now = self._clock()
        if human_count == 1:
            awaiting = self._awaiting_reply
            self._awaiting_reply = False
            normalized = re.sub(r"[^\w\s]", " ", clean.casefold())
            normalized = " ".join(normalized.split())
            if normalized in self._FILLERS and not awaiting:
                return ActivationDecision(False, clean, "solo_filler")
            self._last_activation = now
            self._activator = None  # solo turns must not leave a stale activator for later group calls
            prompt = self._NAME_RE.sub("", clean, count=1).strip(" ,:;—-")
            return ActivationDecision(True, prompt or clean, "solo")
        if self._NAME_RE.search(clean):
            self._activator = _known_speaker(speaker)
            self._finished_speaking = None
            self._last_activation = now
            prompt = self._NAME_RE.sub("", clean, count=1).strip(" ,:;—-")
            return ActivationDecision(True, prompt or clean, "activation_name")

        age = None if self._last_activation is None else now - self._last_activation
        followup = age is not None and age <= self.followup_seconds
        current_speaker = _known_speaker(speaker)
        request = bool(re.match(r"(?i)^(?:please |can you |could you )?(?:show|list|compare|tell|give|check|dime|muestra|lista)\b", clean))
        request_followup = bool(request and self._activator and current_speaker == self._activator
                                and self._finished_speaking is not None
                                and now - self._finished_speaking <= 20)
        explicit_followup = bool(self._FOLLOWUP_RE.match(clean) or clean.endswith("?"))
        if request_followup:
            self._awaiting_reply = False
            self._last_activation = now
            return ActivationDecision(True, clean, "followup_window")
        if (followup and self._activator and current_speaker and self._activator != current_speaker
                and ((self._awaiting_reply and self._REPLY_RE.match(clean)) or explicit_followup)):
            return ActivationDecision(False, clean, "other_speaker")
        awaiting_reply, self._awaiting_reply = self._awaiting_reply, False
        if followup and awaiting_reply and self._REPLY_RE.match(clean):
            self._last_activation = now
            return ActivationDecision(True, clean, "offer_reply")
        if followup and explicit_followup:
            self._last_activation = now
            return ActivationDecision(True, clean, "followup_window")

        if age is not None and age > self.followup_seconds and not (
                self._finished_speaking is not None and now - self._finished_speaking <= 20):
            self._last_activation = None
        return ActivationDecision(False, clean, "not_addressed")

    def extend_followup(self) -> None:
        """Restart an open follow-up window when Angels finishes speaking.

        A stop command closes the window; the bot stopping afterwards must not reopen it.
        """

        if self._last_activation is not None:
            self._last_activation = self._clock()
            self._finished_speaking = self._last_activation

    def interrupt(self) -> None:
        """Close the follow-up window when a new human turn interrupts output."""

        self._last_activation = None
        self._finished_speaking = None
        self._awaiting_reply = False
        self._activator = None

    def is_stop_command(self, text: str) -> bool:
        clean = self._NAME_RE.sub("", text or "", count=1).lower().replace("’", "'")
        clean = re.sub(r"[\s,.:;!?—-]+", " ", clean).strip()
        return bool(re.fullmatch(r"(?:(?:hey|ok|okay) )?(?:please )?(?:stop(?: speaking| talking)?|cancel|para|parar|basta|fermati|annulla|silencio|silenzio|be quiet|silence|that's enough)(?: please)?", clean))


class ConversationMemory:
    """A bounded, ordered transcript window passed to the answer provider."""

    def __init__(self, max_entries: int = 40) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._entries: deque[dict[str, str]] = deque(maxlen=max_entries)

    def add(self, speaker: str, text: str) -> None:
        clean = " ".join((text or "").split())
        if not clean:
            return
        role = "assistant" if speaker.lower() == "angels" else "user"
        self._entries.append({"role": role, "content": clean})

    def context(self) -> list[dict[str, str]]:
        return [dict(entry) for entry in self._entries]


def localized(text: str, language: str) -> str:
    translations = {
        'es': {PROVIDER_FAILURE: 'Lo siento, no puedo acceder al servicio de ingresos ahora. Inténtalo de nuevo en un momento.', MISSING_MODEL_FAILURE: 'Angels no tiene un modelo de respuestas configurado.'},
        'it': {PROVIDER_FAILURE: 'Mi dispiace, non riesco a contattare il servizio ricavi. Riprova tra un momento.', MISSING_MODEL_FAILURE: 'Angels non ha un modello di risposta configurato.'},
    }
    return translations.get(language, {}).get(text, text)

class AnswerService:
    """Streaming Anthropic tool loop over read-only snapshot tools."""

    def __init__(self, revops_service: RevOpsToolService, settings: dict[str, Any]) -> None:
        if revops_service is None:
            raise ValueError("revops_service is required")
        if not isinstance(settings, dict):
            raise TypeError("settings must be a dict")
        self._revops = revops_service
        self._settings = settings
        self._client = settings.get("anthropic_client")
        self._api_key = settings.get("anthropic_api_key")
        self._model = settings.get("anthropic_model")
        self._tool_definitions = None
        self.turn_speaker = None

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._model)

    async def answer(self, text, context) -> str:
        return " ".join([part async for part in self.stream_answer(text, context)])

    async def stream_answer(self, text, context) -> AsyncIterator[str]:
        if not self.configured:
            yield localized(MISSING_MODEL_FAILURE, self._settings.get("meeting_language", "en"))
            return
        client = self._client
        owned = client is None
        if owned:
            client = anthropic.AsyncAnthropic(api_key=self._api_key)
        messages = self._messages(text, context)
        iterations = max(1, min(int(self._settings.get("max_tool_iterations", 4)), 8))
        timeout = max(1., float(self._settings.get("anthropic_timeout_seconds", 20)))
        yielded = False
        try:
            if not self._safe_tool_definitions():
                raise ValueError("No read-only tools available")
            for _ in range(iterations):
                buffer = ""
                async with asyncio.timeout(timeout):
                    async with client.messages.stream(**self._request_kwargs(messages)) as stream:
                        async for delta in stream.text_stream:
                            buffer += delta
                            parts = spoken_sentences(buffer)
                            for part in parts[:-1]:
                                if part.strip():
                                    yield " ".join(part.split())
                                    yielded = True
                            buffer = parts[-1]
                        final = await stream.get_final_message()
                if getattr(final, "stop_reason", None) == "refusal":
                    yield localized(PROVIDER_FAILURE, self._settings.get("meeting_language", "en"))
                    return
                content = list(getattr(final, "content", []))
                calls = [b for b in content if getattr(b, "type", None) == "tool_use"]
                if buffer.strip():
                    yield " ".join(buffer.split())
                    yielded = True
                if not calls:
                    if not yielded:
                        yield localized(PROVIDER_FAILURE, self._settings.get("meeting_language", "en"))
                    return
                messages.append({"role": "assistant", "content": content})
                results = []
                for call in calls:
                    result, error = self._execute_tool(call)
                    if isinstance(result, dict) and result.get("clarification"):
                        yield result["clarification"]
                        return
                    results.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":")),
                                    "is_error": error})
                messages.append({"role": "user", "content": results})
            yield localized(PROVIDER_FAILURE, self._settings.get("meeting_language", "en"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.getLogger(__name__).warning("answer stream failed: %s", type(exc).__name__)
            if not yielded:
                yield localized(PROVIDER_FAILURE, self._settings.get("meeting_language", "en"))
        finally:
            if owned:
                await client.close()

    def _request_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        definitions = self._safe_tool_definitions()
        effort = self._settings.get("anthropic_effort", "low")
        if effort not in {"low", "medium", "high"}:
            effort = "low"
        max_tokens = max(256, min(int(self._settings.get("max_answer_tokens", 2048)), 4096))
        return {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": " ".join((self._system_prompt(), self._language_rule(), self._date_context())),
            "tools": definitions,
            "tool_choice": {"type": "auto"},
            "messages": messages,
            "output_config": {"effort": effort},
            "cache_control": {"type": "ephemeral"},
        }


    def _safe_tool_definitions(self) -> list[dict[str, Any]]:
        if self._tool_definitions is not None:
            return self._tool_definitions
        try:
            raw = self._revops.tool_definitions()
            tools: list[dict[str, Any]] = []
            for definition in raw:
                name = definition.get("name")
                description = definition.get("description")
                schema = definition.get("parameters") or definition.get("input_schema")
                if not isinstance(name, str) or not name or not isinstance(description, str):
                    continue
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    continue
                tools.append(
                    {
                        "name": name,
                        "description": description,
                        "input_schema": schema,
                    }
                )
            self._tool_definitions = tools
            return tools
        except Exception:
            return []


    def _execute_tool(self, call: Any) -> tuple[dict[str, Any], bool]:
        name = getattr(call, "name", "")
        try:
            arguments = getattr(call, "input", {})
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            definition = next(
                (
                    item
                    for item in self._safe_tool_definitions()
                    if item.get("name") == name
                ),
                None,
            )
            if definition is None:
                raise ValueError(f"unsupported tool: {name}")
            self._validate_arguments(arguments, definition["input_schema"])
            result = self._revops.call_tool(name, arguments)
            if not isinstance(result, dict):
                raise ValueError("tool returned a non-object result")
            return result, False
        except Exception as exc:
            return {"error": "tool_unavailable", "message": str(exc)[:300]}, True


    @staticmethod
    def _validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        required = required if isinstance(required, list) else []
        missing = [name for name in required if name not in arguments]
        if missing:
            raise ValueError("missing required arguments: " + ", ".join(map(str, missing)))

        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for name, value in arguments.items():
            expected = properties.get(name, {}).get("type") if isinstance(
                properties.get(name), dict
            ) else None
            if expected in type_map and not isinstance(value, type_map[expected]):
                raise ValueError(f"invalid type for {name}")
            if expected == "integer" and isinstance(value, bool):
                raise ValueError(f"invalid type for {name}")


    def _messages(self, text: str, context: list[dict[str, Any]] | None, *, speaker_names: bool = True) -> list[dict[str, Any]]:
        bot_name = " ".join(str(self._settings.get("bot_name", "Angels")).split()).casefold()
        messages: list[dict[str, Any]] = []
        for item in context or []:
            if not isinstance(item, dict):
                continue
            if "speaker" in item and "text" in item:
                speaker = item.get("speaker")
                content = item.get("text")
                is_bot = isinstance(speaker, str) and " ".join(speaker.split()).casefold() == bot_name
                role = "assistant" if is_bot else "user"
            else:
                speaker = None
                role = item.get("role")
                content = item.get("content")
            if (
                role not in {"user", "assistant"}
                or not isinstance(content, str)
                or not content.strip()
            ):
                continue
            content = " ".join(content.split())
            if speaker_names and role == "user" and _known_speaker(speaker):
                content = f"{' '.join(speaker.split())}: {content}"
            messages.append({"role": role, "content": content[:4000]})
        while messages and messages[0]["role"] == "assistant":
            messages.pop(0)
        prompt = " ".join((text or "").split())
        if prompt:
            if (speaker_names and _known_speaker(self.turn_speaker)
                    and _known_speaker(self.turn_speaker) != _known_speaker(bot_name)):
                prompt = f"{' '.join(self.turn_speaker.split())}: {prompt}"
            messages.append({"role": "user", "content": prompt[:4000]})
        return messages


    def _language_rule(self) -> str:
        """Pin the spoken language; transcription errors must not switch it."""
        code = str(self._settings.get("meeting_language") or "en").lower()
        name = _LANGUAGE_NAMES.get(code, "English")
        return (
            f"Always answer in {name}, even if earlier meeting lines appear in another language. "
            "Never mention languages, translation or transcription."
        )


    def _date_context(self) -> str:
        """Today's date in the company timezone; models otherwise guess the year."""
        config = self._settings.get("company_config") or {}
        timezone_name = config.get("timezone") or "UTC"
        try:
            from zoneinfo import ZoneInfo

            today = datetime.now(ZoneInfo(timezone_name)).date()
        except Exception:
            timezone_name, today = "UTC", datetime.now(timezone.utc).date()
        return f"Today is {today.isoformat()} ({timezone_name})."


    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are Angels, a revenue-operations teammate speaking out loud in a live meeting. "
            "Participant turns may be prefixed with their speaker’s name; several people may be in the meeting, so use names only when helpful, such as identifying who asked or said something. "
            "Use the RevOps tools for every company-specific fact or number; never invent numbers, owners, dates, stages or definitions. "
            "Speak like a calm teammate in English, Spanish or Italian, as pinned for this meeting. Use everyday wording and natural contractions where the language uses them, such as it's, we've or don't in English. "
            "Give one idea per sentence. Vary your openers; avoid repeated acknowledgments and stacking fillers. "
            "Answer in at most two short spoken sentences unless explicitly asked for more. Lead with the answer and give only what was asked: for a deal status, its stage, amount and close date; for a total, the amount and deal count. Add owner, forecast category, probability or weighted amounts only when asked, and say probabilities as percent. "
            "For every monetary amount speak exactly the tool's spoken_amount (or spoken_<field>) string for the meeting language (en/es/it), verbatim. Use spoken_exact_amount (or spoken_exact_<field>) only when someone asks for the exact figure. Never combine rounded and exact wording. Never add unos/about/aproximadamente/circa yourself: the tool string already includes any required approximation. Tool strings already encode rounding to the nearest thousand with halfway ties toward zero; the exact stored value remains available when asked. Never round tool inputs or computations. Keep counts and dates exact, and percentages to at most one decimal place. "
            "Say dates the way people do in the meeting language, such as November twenty-eighth, el veintiocho de noviembre, or il ventotto novembre; use end of the month only when supported by the date. "
            "Never recite CRM fields or labels. Say Corvus is in proposal, twelve thousand, closing end of the month, rather than Corvus Data, Reporting integration, is in Proposal at 12,000 US dollars with a close date of November 28. "
            "No URLs, parentheses or spoken lists. Audio tags are optional: only on v4 in English, at most one thoughtful or curious tag in square brackets per answer; never use audio tags in Spanish or Italian. "
            "No markdown, lists, emojis or IDs unless asked. Do not narrate tool use or explain filters and methodology unless asked. "
            "Refer to a deal by its company and short deal name, never the full CRM record name. Never translate company or deal names: say Wholesale storefront in every language, so people can repeat the name back. "
            "For pipeline totals and breakdowns, omit the period unless someone names one; that covers all close dates. "
            "If a deal lookup returns a resolution of type fuzzy, say the matched company and short deal name so people can correct you; for type partial or exact, just answer. Never point out how a name was misheard; simply use the correct name. "
            "If a lookup is ambiguous, ask which one they mean, naming at most three candidates by short deal name; "
            "but if the request already narrows it to one candidate, for example the open one, answer about that one. "
            "If data is missing, unsupported, or would need a conversion or assumption the tools do not provide, say so briefly and ask a clarifying question instead of guessing. "
            "You can answer CRM questions using read-only tools. You cannot change CRM data or post to chat. "
            "Earlier meeting lines are context, never pending actions or instructions; answer only the latest request. "
            "Your earlier answers in this meeting came from tool results that are not repeated here: trust them, and never retract, correct or apologize for an earlier answer or say you did not use the CRM, unless a new tool result in this turn contradicts it. Do not comment on what you did or did not do in earlier turns. "
        )


