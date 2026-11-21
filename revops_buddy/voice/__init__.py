from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
import time
from collections import deque
from collections.abc import Mapping
from typing import Any, Awaitable, Callable

from ..conversation import ActivationGate, ACKNOWLEDGMENTS, SpokenAnnouncement, _known_speaker

_UNKNOWN_HUMAN = object()

DIALOGUE_TTS_MODELS = frozenset({"eleven_v4_turbo", "eleven_v4", "eleven_v3", "eleven_v3_conversational"})


def sanitize_tts_text(text: str, *, model: str, language: str, tag_used: bool = False) -> tuple[str, bool]:
    """Allow one supported delivery tag per answer; never speak unknown markup."""
    def tag(match):
        nonlocal tag_used
        if (model.startswith("eleven_v4") and language == "en" and not tag_used
                and match.group(0) in {"[thoughtful]", "[curious]"}):
            tag_used = True
            return match.group(0)
        return ""
    return " ".join(re.sub(r"\[[^\[\]]*\]", tag, text).split()), tag_used


def _transcript_speech_end(result: Any, audio_seconds: float, received_at: float | None) -> float | None:
    """Map provider word timestamps onto the locally received PCM clock."""
    if received_at is None:
        return None
    words = _value(result, "words", None)
    if words is None:
        alternatives = _value(_value(result, "channel", {}), "alternatives", []) or []
        words = _value(alternatives[0], "words", []) if alternatives else []
    ends = [_value(word, "end") for word in words or [] if _value(word, "type", "word") == "word"]
    ends = [float(end) for end in ends if isinstance(end, (float, int)) and math.isfinite(end) and end >= 0]
    if not ends or max(ends) > audio_seconds + .1:
        return None
    return received_at - max(0, audio_seconds - max(ends))


def update_human_participants(seen: set[Any], participants: Any) -> None:
    """Record every distinct human participant ever seen in MeetingBaas events."""
    if not isinstance(participants, list):
        return
    for person in participants:
        if not isinstance(person, dict) or person.get("isSelf") is True:
            continue
        key = next((value for value in (person.get("id"), person.get("participant_id"), person.get("name"))
                    if (isinstance(value, str) and value.strip()) or type(value) is int), _UNKNOWN_HUMAN)
        seen.add(key)


def _normalized_words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold()) if isinstance(text, str) else []


def _is_bot_echo(text: str, recent_speech: list[str], *, awaiting_reply: bool = False) -> bool:
    words = _normalized_words(text)
    if not words:
        return False
    for spoken in recent_speech:
        spoken_words = _normalized_words(spoken)
        if words == spoken_words:
            return True
        # A person answering a clarifying question often repeats Angels' own words
        # ("the warehouse automation deal"), so after a question only an exact
        # repeat counts, and otherwise the transcript must cover most of the
        # utterance, not just a fragment of it.
        if awaiting_reply:
            continue
        if len(words) >= 2 and len(words) >= 0.6 * len(spoken_words):
            spoken_set = set(spoken_words)
            contained = sum(1 for word in words if word in spoken_set)
            if contained / len(words) >= 0.8:
                return True
    return False


def _value(container: Any, name: str, default: Any = None) -> Any:
    if isinstance(container, Mapping):
        return container.get(name, default)
    return getattr(container, name, default)


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value.strip()


def _user_speaker(serializer: Any) -> str:
    """Prefer recent utterance metadata, then the sole active speaker."""
    speaker_for_utterance = _value(serializer, "speaker_for_utterance")
    if callable(speaker_for_utterance):
        speaker = speaker_for_utterance()
        if speaker:
            return speaker
    speakers = list(_value(serializer, "active_speakers", None) or [])
    return speakers[0] if len(speakers) == 1 else "user"


async def run_voice_session(
    websocket: Any,
    *,
    session_id: str,
    expected_bot_id: str,
    settings: Any,
    on_transcript: Callable[[str, str], Awaitable[None]],
    answer: Callable[[str, list[dict[str, str]]], Awaitable[str]],
    answer_stream: Any = None,
) -> None:
    """Run one accepted MeetingBaas duplex WebSocket as a Pipecat voice session."""
    session_id = _required_text(session_id, "session_id")
    protocol = _value(settings, "transport_protocol", "meetingbaas")
    if protocol not in {"meetingbaas"}:
        raise ValueError("unsupported transport_protocol")
    if not callable(on_transcript) or not callable(answer):
        raise TypeError("on_transcript and answer must be callable")

    api_settings = _value(settings, "api_keys", {}) or {}
    elevenlabs_key = _required_text(_value(api_settings, "elevenlabs"), "api_keys.elevenlabs")
    voice_id = _required_text(_value(settings, "tts_voice_id"), "settings.tts_voice_id")

    stt_provider = _value(settings, "stt_provider", "deepgram")
    tts_provider = _value(settings, "tts_provider", "elevenlabs")
    if stt_provider not in {"deepgram", "elevenlabs"}:
        raise ValueError(f"unsupported STT provider: {stt_provider!r}")
    deepgram_key = _required_text(_value(api_settings, "deepgram"), "api_keys.deepgram") if stt_provider == "deepgram" else None
    if tts_provider != "elevenlabs":
        raise ValueError(f"unsupported TTS provider: {tts_provider!r}; only 'elevenlabs' is supported")

    rate = int(_value(settings, "sample_rate", 16000))
    if rate <= 0:
        raise ValueError("sample_rate must be positive")
    progress_ack_delay = float(_value(settings, "progress_ack_delay_seconds", 1.6))
    if not (0 <= progress_ack_delay <= 30) or progress_ack_delay != progress_ack_delay or progress_ack_delay in (float("inf"), float("-inf")):
        raise ValueError("progress_ack_delay_seconds must be a finite number between 0 and 30")
    bot_name = _required_text(_value(settings, "bot_name", "Angels"), "settings.bot_name")

    try:
        from .protocol import MeetingBaasFrameSerializer
        from pipecat.frames.frames import (
            CancelFrame,
            EndFrame,
            InterruptionFrame,
            TranscriptionFrame,
            TTSSpeakFrame,
            UserStartedSpeakingFrame,
            UserStoppedSpeakingFrame,
            LLMFullResponseStartFrame,
            LLMFullResponseEndFrame,
            AggregatedTextFrame,
            TTSAudioRawFrame,
            InterimTranscriptionFrame,
            BotStartedSpeakingFrame,
            BotStoppedSpeakingFrame,
        )
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineTask, PipelineParams
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
        from pipecat.services.deepgram.stt import DeepgramSTTService
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService, ElevenLabsHttpTTSService, ElevenLabsTTSSettings
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )
    except Exception as exc:
        raise RuntimeError("Pipecat speech dependencies are unavailable for this voice session") from exc

    from loguru import logger
    timing = {"turn": 0, "start": None, "audio_sent": False, "speech_end": None, "speech_end_source": "transcription_commit",
              "audio_seconds": 0.0, "audio_received": None}

    serializer_class = MeetingBaasFrameSerializer


    class MeasuredSerializer(serializer_class):
        @property
        def human_ids(self) -> set[Any]:
            if not hasattr(self, "_human_ids"):
                self._human_ids: set[Any] = set()
            return self._human_ids

        @property
        def human_count(self):
            if not getattr(self, "_participants_seen", False):
                return None
            # An unidentified human must never enable solo mode, even on their own.
            return max(2, len(self.human_ids)) if _UNKNOWN_HUMAN in self.human_ids else len(self.human_ids)

        async def deserialize(self, data):
            if protocol == "meetingbaas" and isinstance(data, str):
                try:
                    participants = json.loads(data)
                except (ValueError, TypeError, RecursionError):
                    participants = None
                if isinstance(participants, list):
                    self._participants_seen = True
                    update_human_participants(self.human_ids, participants)
            was_recording = getattr(self, "recording_started", False)
            frame = await super().deserialize(data)
            if not was_recording and getattr(self, "recording_started", False):
                callback = _value(settings, "on_admitted")
                if callback:
                    callback()
            if frame is not None and hasattr(frame, "audio"):
                duration = len(frame.audio) / (2 * rate)
                timing["audio_seconds"] += duration
                timing["audio_received"] = time.monotonic()
            return frame


    class FirstByteTiming:
        """Observe bytes as the provider appends them, before playout buffering."""
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._latency_contexts = {}
            self._latency_measurement = None

        async def process_frame(self, frame, direction):
            if isinstance(frame, (LLMFullResponseStartFrame, TTSSpeakFrame)):
                self._latency_measurement = getattr(frame, "_angels_timing", None)
            elif isinstance(frame, InterruptionFrame):
                self._latency_contexts.clear()
                self._latency_measurement = None
            await super().process_frame(frame, direction)

        async def on_turn_context_created(self, context_id):
            if self._latency_measurement is not None:
                self._latency_contexts[context_id] = self._latency_measurement
            await super().on_turn_context_created(context_id)

        async def append_to_audio_context(self, context_id, frame):
            measured = self._latency_contexts.get(context_id)
            if (isinstance(frame, TTSAudioRawFrame) and frame.audio and measured
                    and measured[0] == timing["turn"] and measured[1] is not None
                    and not timing["audio_sent"]):
                timing["audio_sent"] = True
                logger.info("Angels turn={} end_user_speech_to_first_tts_byte_ms={:.0f} speech_end_source={}",
                            measured[0], (time.monotonic() - measured[1]) * 1000, measured[2])
            await super().append_to_audio_context(context_id, frame)

        async def remove_audio_context(self, context_id):
            self._latency_contexts.pop(context_id, None)
            await super().remove_audio_context(context_id)

    def measured_service(base):
        return type(f"Measured{base.__name__}", (FirstByteTiming, base), {})

    serializer = MeasuredSerializer(sample_rate=rate)
    transport = FastAPIWebsocketTransport(
        websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=rate,
            audio_out_sample_rate=rate,
            serializer=serializer,
            audio_out_10ms_chunks=2,
            audio_out_end_silence_secs=0,
            audio_out_auto_silence=False,
        ),
    )
    if stt_provider == "elevenlabs":
        from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService, CommitStrategy
        stt = ElevenLabsRealtimeSTTService(api_key=elevenlabs_key, sample_rate=rate, include_timestamps=True, commit_strategy=CommitStrategy.VAD, settings=ElevenLabsRealtimeSTTService.Settings(
            language=_value(settings, "stt_language", None),
            vad_silence_threshold_secs=_value(settings, "stt_silence_seconds", 0.45),
            keyterms=list(_value(settings, "stt_keyterms", None) or []) or None,
        ))
    else:
        from pipecat.transcriptions.language import Language
        stt_options = {}
        if hasattr(DeepgramSTTService, "Settings"):
            stt_options["settings"] = DeepgramSTTService.Settings(language=Language(_value(settings, "stt_language", "en")))
        stt = DeepgramSTTService(api_key=deepgram_key, sample_rate=rate, **stt_options)
    tts_model = _value(settings, "tts_model", "eleven_flash_v2_5")
    requested_transport = _value(settings, "tts_transport", "dialogue")
    if requested_transport not in {"dialogue", "http"}:
        raise ValueError("tts_transport must be dialogue or http")
    dialogue = requested_transport == "dialogue" and tts_model in DIALOGUE_TTS_MODELS
    stability = float(_value(settings, "tts_stability", 0.4))
    similarity = float(_value(settings, "tts_similarity", 0.75))
    if not (0 <= stability <= 1 and 0 <= similarity <= 1):
        raise ValueError("TTS stability and similarity must be between 0 and 1")
    normalization = _value(settings, "tts_text_normalization", "auto")
    if normalization not in {"auto", "on", "off"}:
        raise ValueError("invalid TTS text normalization")
    http_session = None
    if dialogue:
        from pipecat.services.elevenlabs.dialogue.tts import ElevenLabsDialogueTTSService
        # Installed 1.15 adapter maps 16 kHz to pcm_16000. Its endpoint only
        # accepts stability; similarity and normalization are deliberately absent.
        tts = measured_service(ElevenLabsDialogueTTSService)(
            api_key=elevenlabs_key, sample_rate=rate,
            settings=ElevenLabsDialogueTTSService.Settings(
                voice=voice_id, model=tts_model, stability=stability),
        )
    elif requested_transport == "http" or tts_model in DIALOGUE_TTS_MODELS:
        import aiohttp
        http_session = aiohttp.ClientSession()
        tts = measured_service(ElevenLabsHttpTTSService)(
            api_key=elevenlabs_key, aiohttp_session=http_session, sample_rate=rate,
            settings=ElevenLabsHttpTTSService.Settings(
                voice=voice_id, model=tts_model, stability=stability,
                similarity_boost=similarity, apply_text_normalization=normalization),
        )
    else:
        # Legacy Flash models use the text-to-speech websocket endpoint.
        tts = measured_service(ElevenLabsTTSService)(
            api_key=elevenlabs_key, sample_rate=rate,
            settings=ElevenLabsTTSSettings(
                voice=voice_id, model=tts_model, stability=stability, similarity_boost=similarity,
                apply_text_normalization=normalization),
        )
    logger.info("Angels TTS transport={} sample_rate={}",
                "dialogue" if dialogue else "http" if http_session else "websocket", rate)
    activation_gate = ActivationGate(activation_name=bot_name)
    history: deque[dict[str, str]] = deque(maxlen=40)
    bot_speech: deque[tuple[float, str]] = deque(maxlen=128)
    failure: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    class TurnCoordinator(FrameProcessor):
        def __init__(self) -> None:
            super().__init__()
            self._answer_task: asyncio.Task[str | None] | None = None
            self._responding = False
            self._ack_phrases = ACKNOWLEDGMENTS.get(_value(settings, "meeting_language", "en"), ACKNOWLEDGMENTS["en"])
            self._ack_index = 0

        async def speak(self, speech: str, *, answer_context: bool = False, is_answer: bool = False) -> None:
            speech, _ = sanitize_tts_text(speech, model=tts_model,
                                         language=_value(settings, "meeting_language", "en"))
            if not speech:
                return
            await on_transcript(bot_name, speech)
            bot_speech.append((time.monotonic(), speech))
            frame = AggregatedTextFrame(text=speech, text_type="sentence") if answer_context else TTSSpeakFrame(text=speech)
            if is_answer:
                frame._angels_timing = (timing["turn"], timing["start"], timing["speech_end_source"])
            await self.push_frame(frame, FrameDirection.DOWNSTREAM)

        def _fail(self, exc: BaseException) -> None:
            if not failure.done():
                failure.set_exception(exc)

        async def cancel_answer(self) -> None:
            task = self._answer_task
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._answer_task = None

        async def _run_answer(self, prompt: str, context: list[dict[str, str]]) -> str | None:
            first_chunk = asyncio.Event()
            ack_started = asyncio.Event()
            ack_task = asyncio.create_task(self._progress_ack(first_chunk, ack_started), name=f"angels-progress-ack-{session_id}")
            async def chunks():
                if answer_stream is not None:
                    async for part in answer_stream(prompt, context):
                        yield part
                else:
                    yield await answer(prompt, context)
            async def first_chunks():
                async for part in chunks():
                    first_chunk.set()
                    yield part
            try:
                parts = []
                http_parts = []
                tag_used = False
                context_started = False
                async for response in first_chunks():
                    if not isinstance(response, str) or not response.strip():
                        raise TypeError("answer must return a nonempty string")
                    # Complete the acknowledgment enqueue before starting the answer.
                    # Pipecat drains its standalone audio context before this turn.
                    if ack_started.is_set():
                        await ack_task
                    if isinstance(response, SpokenAnnouncement):
                        if dialogue and context_started:
                            await self.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
                            context_started = False
                        elif http_session is not None and http_parts:
                            await self.speak(" ".join(http_parts), is_answer=True)
                            http_parts.clear()
                        parts.append(str(response))
                        history.append({"speaker": bot_name, "text": str(response)})
                        await self.speak(response)
                        continue
                    speech, tag_used = sanitize_tts_text(
                        response, model=tts_model, language=_value(settings, "meeting_language", "en"),
                        tag_used=tag_used)
                    if not speech:
                        continue
                    if dialogue and not context_started:
                        start_frame = LLMFullResponseStartFrame()
                        start_frame._angels_timing = (timing["turn"], timing["start"], timing["speech_end_source"])
                        await self.push_frame(start_frame, FrameDirection.DOWNSTREAM)
                        context_started = True
                    parts.append(speech)
                    history.append({"speaker": bot_name, "text": speech})
                    if http_session is None:
                        await self.speak(speech, answer_context=dialogue, is_answer=True)
                    else:
                        http_parts.append(speech)
                if dialogue and context_started:
                    # The adapter closes this context and generates buffered text.
                    await self.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
                elif http_session is not None and http_parts:
                    # HTTP accepts the whole answer in one request, preserving
                    # prosody without per-sentence request stitching.
                    await self.speak(" ".join(http_parts), is_answer=True)
                if parts and parts[-1].rstrip().endswith("?"):
                    activation_gate.expect_reply()  # e.g. "Want me to put the list in the chat?"
                spoken = " ".join(parts)
                callback = _value(settings, "on_answer_complete")
                if parts and callback:
                    callback(prompt, spoken)
                return spoken
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._fail(exc)
                return None
            finally:
                first_chunk.set()
                ack_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ack_task

        async def _progress_ack(self, first_chunk: asyncio.Event, ack_started: asyncio.Event) -> None:
            """Emit one brief filler when the first answer chunk is slow to arrive."""
            waiter = asyncio.create_task(first_chunk.wait())
            try:
                await asyncio.wait({waiter}, timeout=progress_ack_delay)
            finally:
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter
            if first_chunk.is_set():
                return
            phrase = self._ack_phrases[self._ack_index % len(self._ack_phrases)]
            self._ack_index += 1
            ack_started.set()
            await self.speak(phrase)

        async def _handle_transcription(self, text: str) -> None:
            try:
                speaker = _user_speaker(serializer)
                logger.info("Angels heard={!r}", text[:300])
                now = time.monotonic()
                recent = [speech for seen_at, speech in bot_speech if now - seen_at <= 20]
                if not activation_gate.is_stop_command(text):
                    try:
                        echo = _is_bot_echo(text, recent, awaiting_reply=activation_gate.awaiting_reply)
                    except Exception:
                        # Echo filtering is best effort; it must not terminate the audio loop.
                        logger.exception("Angels echo check failed")
                        echo = False
                    if echo:
                        logger.info("Angels turn={} ignored_bot_echo", timing["turn"])
                        return
                await on_transcript(speaker, text)
                context = list(history)
                history.append({"speaker": speaker, "text": text})
                if activation_gate.is_stop_command(text):
                    activation_gate.interrupt()
                    # Flush audio before waiting for provider/generation cancellation.
                    await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
                    await self.cancel_answer()
                    self._responding = False
                    logger.info("Angels turn={} silent_stop_command", timing["turn"])
                    return
                decision = activation_gate.observe(text, human_count=getattr(serializer, "human_count", None),
                                                   speaker=speaker if _known_speaker(speaker) else None)
                if not bool(getattr(decision, "activated", False)):
                    return
                prompt = str(getattr(decision, "text", "") or text).strip() or text
            except Exception as exc:
                self._fail(exc)
                return

            await self.cancel_answer()
            timing.update(turn=timing["turn"] + 1,
                          start=timing["speech_end"] or time.monotonic(), audio_sent=False)
            timing["speech_end"] = None
            self._responding = True
            set_turn_speaker = _value(settings, "set_turn_speaker")
            if set_turn_speaker:
                set_turn_speaker(speaker if _known_speaker(speaker) else None)
            self._answer_task = asyncio.create_task(
                self._run_answer(prompt, context),
                name=f"angels-answer-{session_id}",
            )

        async def process_frame(self, frame: Any, direction: FrameDirection) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, UserStartedSpeakingFrame):
                timing["speech_end"] = None
            if isinstance(frame, UserStoppedSpeakingFrame):
                timing["speech_end"] = time.monotonic()
                timing["speech_end_source"] = "vad"
            if isinstance(frame, TranscriptionFrame) and timing["speech_end"] is None:
                endpoint = _transcript_speech_end(
                    getattr(frame, "result", None), timing["audio_seconds"], timing["audio_received"])
                timing["speech_end"] = endpoint
                timing["speech_end_source"] = "stt_word_timestamps" if endpoint is not None else "transcription_commit"
            if isinstance(frame, BotStartedSpeakingFrame):
                self._responding = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._responding = False
                activation_gate.extend_followup()
            answer_pending = self._answer_task is not None and not self._answer_task.done()
            should_interrupt = self._responding or answer_pending or (isinstance(frame, InterimTranscriptionFrame) and activation_gate.is_stop_command(frame.text))
            if isinstance(frame, (UserStartedSpeakingFrame, InterimTranscriptionFrame)) and should_interrupt:
                if isinstance(frame, UserStartedSpeakingFrame) or frame.text.strip():
                    # Flush audio before waiting for provider/generation cancellation.
                    await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
                    await self.cancel_answer()
                    self._responding = False
                    logger.info("Angels turn={} interrupted_on_recognized_speech", timing["turn"])
            if isinstance(frame, (UserStartedSpeakingFrame, InterruptionFrame, EndFrame, CancelFrame)):
                await self.cancel_answer()
            elif isinstance(frame, TranscriptionFrame):
                text = str(getattr(frame, "text", "")).strip()
                if text:
                    await self._handle_transcription(text)
                    timing["speech_end"] = None
            await self.push_frame(frame, direction)

    coordinator = TurnCoordinator()
    register = _value(settings, "register_speaker")
    if register:
        register(coordinator.speak)
    pipeline = Pipeline([transport.input(), stt, coordinator, tts, transport.output()])
    task = PipelineTask(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=rate, audio_out_sample_rate=rate),
    )

    async def on_client_disconnected(*_args: Any) -> None:
        await task.cancel()

    transport.event_handler("on_client_disconnected")(on_client_disconnected)

    runner = PipelineRunner(handle_sigint=False)
    runner_task = asyncio.create_task(runner.run(task), name=f"angels-runner-{session_id}")
    failure_error: BaseException | None = None

    try:
        while True:
            done, _pending = await asyncio.wait(
                {runner_task, failure}, return_when=asyncio.FIRST_COMPLETED
            )
            if failure.done():
                failure_error = failure.exception()
                break
            if runner_task.done():
                await runner_task
                break
    finally:
        await coordinator.cancel_answer()
        if runner_task is not None and not runner_task.done():
            runner_task.cancel()
        with contextlib.suppress(Exception):
            await task.cancel()
        await asyncio.gather(runner_task, return_exceptions=True)
        if failure.done():
            failure.exception()
        if http_session is not None:
            await http_session.close()

    if failure_error is not None:
        raise failure_error
