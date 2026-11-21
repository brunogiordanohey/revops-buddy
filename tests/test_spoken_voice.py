import asyncio


import base64


import json


import re


from pathlib import Path


import pytest


from loguru import logger


from websockets.protocol import State


from revops_buddy.config import Settings


from revops_buddy.conversation import AnswerService, spoken_sentences


from revops_buddy.voice import sanitize_tts_text, _transcript_speech_end


from test_voice_runtime import _send_audio, _start_session


@pytest.mark.parametrize('text, expected', [
    ('Dr. A. Smith has U.S. revenue of $12.5 million. Next.', ['Dr. A. Smith has U.S. revenue of $12.5 million.', 'Next.']),
    ('Sono del dott. Rossi. Bene.', ['Sono del dott. Rossi.', 'Bene.']),
    ('La Sra. Ruiz tiene 12,5 euros. Bien.', ['La Sra. Ruiz tiene 12,5 euros.', 'Bien.']),
    ('It is USD 12.50. "Ready?" Yes.', ['It is USD 12.50.', '"Ready?"', 'Yes.']),
])
def test_sentence_boundaries(text, expected):
    assert spoken_sentences(text) == expected
    # Repeated streaming chunks must produce exactly the same sentences.
    buffer, completed = '', []
    for char in text:
        buffer += char
        parts = spoken_sentences(buffer)
        completed.extend(parts[:-1])
        buffer = parts[-1]
    assert completed + [buffer] == expected


def test_voice_config_defaults_and_overrides():
    settings = Settings.from_env({})
    assert (settings.tts_transport, settings.tts_stability, settings.tts_similarity, settings.tts_text_normalization) == ('dialogue', .4, .75, 'auto')
    settings = Settings.from_env({'TTS_TRANSPORT': 'http', 'TTS_STABILITY': '.6', 'TTS_SIMILARITY': '.65', 'TTS_TEXT_NORMALIZATION': 'on'})
    voice = settings.voice_settings({})
    assert (voice['tts_transport'], voice['tts_stability'], voice['tts_similarity'], voice['tts_text_normalization']) == ('http', .6, .65, 'on')
    for name, value in [('TTS_STABILITY', 'nan'), ('TTS_SIMILARITY', '1.1'), ('TTS_TRANSPORT', 'udp'), ('TTS_TEXT_NORMALIZATION', 'invalid')]:
        with pytest.raises(ValueError):
            Settings.from_env({name: value})


@pytest.mark.parametrize('model, language, expected', [
    ('eleven_v4_turbo', 'en', '[thoughtful] Hi. Fine.'),
    ('eleven_v4_turbo', 'es', 'Hi. Fine.'),
    ('eleven_v4_turbo', 'it', 'Hi. Fine.'),
    ('eleven_flash_v2_5', 'en', 'Hi. Fine.'),
])
def test_supported_tags_only(model, language, expected):
    text, used = sanitize_tts_text('[unknown] [thoughtful] Hi. [curious] Fine.', model=model, language=language)
    assert text == expected
    later, _ = sanitize_tts_text('[curious] Later.', model=model, language=language, tag_used=used)
    assert later == 'Later.'


class OfflineDialogueSocket:
    state = State.OPEN

    def __init__(self):
        self.messages = []
        self.service = None
        self.text_received = asyncio.Event()
        self.closed_context = asyncio.Event()

    async def send(self, raw):
        message = json.loads(raw)
        self.messages.append(message)
        if 'inputs' in message:
            self.text_received.set()
        if message.get('close_context'):
            self.closed_context.set()

    async def audio(self, context, packets=1, value=1):
        await self.service._handle_message({'context_id': context, 'audio': base64.b64encode(bytes([value, 0]) * (320 * packets)).decode()})

    async def final(self, context):
        await self.service._handle_message({'context_id': context, 'is_final': True})


@pytest.fixture
def dialogue_socket(monkeypatch):
    from pipecat.services.elevenlabs.dialogue.tts import ElevenLabsDialogueTTSService
    socket = OfflineDialogueSocket()

    async def connect(service):
        socket.service = service
        service._websocket = socket
        await service._on_websocket_connected()

    async def disconnect(service):
        service._websocket = None
        service._clear_connection_state()

    monkeypatch.setattr(ElevenLabsDialogueTTSService, '_connect', connect)
    monkeypatch.setattr(ElevenLabsDialogueTTSService, '_disconnect', disconnect)
    return socket


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


async def finish(incoming, task):
    await incoming.put({'type': 'websocket.disconnect', 'code': 1000})
    await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_dialogue_one_context_pcm_and_first_byte_latency(monkeypatch, dialogue_socket):
    socket = dialogue_socket
    logs = []
    sink = logger.add(lambda message: logs.append(str(message)), level='INFO')

    async def stream(text, context):
        yield "It's in proposal."
        yield 'Worth twelve thousand.'

    incoming, outgoing, transcript, _, task = await _start_session(monkeypatch, stream=stream, settings_extra={'tts_model': 'eleven_v4_turbo'})
    try:
        await _send_audio(incoming)
        await asyncio.wait_for(socket.closed_context.wait(), 3)
        inputs = [m for m in socket.messages if 'inputs' in m]
        assert len(inputs) == 2
        context = inputs[0]['context_id']
        assert inputs[1]['context_id'] == context
        assert [m['inputs'][0]['new_turn'] for m in inputs] == [True, False]
        registration = next(m for m in socket.messages if m.get('context_id') == context and 'voices' in m)
        assert registration['voice_settings'] == {'stability': .4}
        assert any(m.get('flush') and m['context_id'] == context for m in socket.messages)
        assert 'output_format=pcm_16000' in socket.service._build_websocket_url()
        await socket.audio(context, packets=2)
        await socket.audio(context)
        latency = [line for line in logs if 'end_user_speech_to_first_tts_byte_ms=' in line]
        assert len(latency) == 1
        assert re.search(r'ms=\d+', latency[0])
        assert 'proposal' not in latency[0]
        assert await asyncio.wait_for(outgoing.get(), 3)
        await socket.final(context)
    finally:
        logger.remove(sink)
        await finish(incoming, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("hold_generation", [True, False])
@pytest.mark.parametrize("trigger", ["interim", "speech_start"])
async def test_barge_in_discards_buffered_and_late_audio_and_never_resumes(monkeypatch, dialogue_socket, hold_generation, trigger):
    socket = dialogue_socket
    cancelled = asyncio.Event()
    interrupted = asyncio.Event()
    from revops_buddy.voice.protocol import MeetingBaasFrameSerializer
    from pipecat.frames.frames import InterruptionFrame
    original_serialize = MeetingBaasFrameSerializer.serialize
    count_at_interruption = []

    async def serialize(serializer, frame):
        result = await original_serialize(serializer, frame)
        if isinstance(frame, InterruptionFrame) and not interrupted.is_set():
            count_at_interruption.append(outgoing.qsize())
            interrupted.set()
        return result

    monkeypatch.setattr(MeetingBaasFrameSerializer, 'serialize', serialize)

    if trigger == "speech_start":
        from test_voice_runtime import ScriptedSTT
        from pipecat.frames.frames import InputAudioRawFrame, UserStartedSpeakingFrame
        original_process = ScriptedSTT.process_frame

        async def process(stt, frame, direction):
            if isinstance(frame, InputAudioRawFrame) and stt.count >= 1:
                stt.count += 1
                await stt.push_frame(UserStartedSpeakingFrame(), direction)
            else:
                await original_process(stt, frame, direction)

        monkeypatch.setattr(ScriptedSTT, 'process_frame', process)

    async def pcm():
        await incoming.put({'type': 'websocket.receive', 'bytes': b'\x01\x00' * 1600})

    async def stream(text, context):
        try:
            yield 'The old answer starts.'
            if hold_generation:
                await asyncio.Future()
                yield 'This must never resume.'
        finally:
            cancelled.set()

    incoming, outgoing, transcript, _, task = await _start_session(monkeypatch, stream=stream, script=('Angels, say hello', 'Angel, stop.'), settings_extra={'tts_model': 'eleven_v4_turbo', 'transport_protocol': 'meetingbaas'})
    try:
        await incoming.put({'type': 'websocket.receive', 'text': json.dumps({'protocol_version': 1, 'start_time': 0})})
        await pcm()
        await asyncio.wait_for(socket.text_received.wait(), 3)
        context = next(m['context_id'] for m in socket.messages if 'inputs' in m)
        # Queue a long answer, then interrupt while audio is playing.
        await socket.audio(context, packets=100)
        first_packet = await asyncio.wait_for(outgoing.get(), 3)
        assert len(first_packet['bytes']) == 640  # 20 ms of 16 kHz PCM16
        await pcm()
        await asyncio.wait_for(cancelled.wait(), 3)
        await wait_until(lambda: not socket.service.audio_context_available(context))
        await asyncio.wait_for(interrupted.wait(), 3)
        # At most the packet already being written may survive queue flushing.
        await socket.audio(context, packets=100, value=2)
        await asyncio.sleep(.12)
        assert outgoing.qsize() - count_at_interruption[0] <= 1
        assert all('resume' not in text for _, text in transcript)
        await socket.final(context)
    finally:
        await finish(incoming, task)


@pytest.mark.asyncio
async def test_ack_and_answer_use_separate_ordered_contexts(monkeypatch, dialogue_socket):
    socket = dialogue_socket
    logs = []
    sink = logger.add(lambda message: logs.append(str(message)), level='INFO')

    async def stream(text, context):
        await socket.text_received.wait()  # acknowledgment has started
        yield "It's ready."

    incoming, outgoing, _, _, task = await _start_session(monkeypatch, stream=stream, settings_extra={'tts_model': 'eleven_v4_turbo', 'progress_ack_delay_seconds': .02})
    try:
        await _send_audio(incoming)
        await asyncio.wait_for(socket.text_received.wait(), 3)
        ack_context = next(m['context_id'] for m in socket.messages if 'inputs' in m)
        await socket.audio(ack_context, packets=1, value=1)
        assert not any('end_user_speech_to_first_tts_byte_ms=' in line for line in logs)
        # The installed adapter pauses text processing until the ack drains.
        await socket.final(ack_context)
        first = (await asyncio.wait_for(outgoing.get(), 3))['bytes']
        await wait_until(lambda: len([m for m in socket.messages if 'inputs' in m]) == 2)
        contexts = [m['context_id'] for m in socket.messages if 'inputs' in m]
        assert contexts[0] != contexts[1]
        await socket.audio(contexts[1], packets=1, value=2)
        second = (await asyncio.wait_for(outgoing.get(), 3))['bytes']
        # MeetingBaas receives raw PCM bytes: the ack audio comes out before the answer audio.
        assert first[:2] == b'\x01\x00'
        assert second[:2] == b'\x02\x00'
        assert len([line for line in logs if 'end_user_speech_to_first_tts_byte_ms=' in line]) == 1
        await socket.final(contexts[1])
    finally:
        logger.remove(sink)
        await finish(incoming, task)


def test_latency_uses_word_endpoint_instead_of_commit_arrival():
    # Three seconds of silence/STT waiting after the final word must be included.
    assert _transcript_speech_end({'words': [{'end': 7., 'type': 'word'}]}, 10., 100.) == 97.
    assert _transcript_speech_end({'channel': {'alternatives': [{'words': [{'end': 7.}]}]}}, 10., 100.) == 97.
    assert _transcript_speech_end({}, 10., 100.) is None
    assert _transcript_speech_end({'words': [{'end': float('nan')}]}, 10., 100.) is None


def test_prompt_requires_spoken_style_and_preserves_tool_precision():
    prompt = AnswerService._system_prompt()
    for rule in ('calm teammate', 'contractions', 'one idea per sentence',
                 'unless explicitly asked for more', 'Never recite CRM fields',
                 'No URLs, parentheses or spoken lists', 'never round tool inputs',
                 'exact stored value', 'Vary your openers', 'Spanish or Italian',
                 'spoken_amount', 'verbatim', 'nearest thousand', 'halfway ties toward zero'):
        assert rule.casefold() in prompt.casefold()


async def test_rounded_answer_does_not_change_tool_result_or_exact_followup():
    from types import SimpleNamespace
    from test_answer_service import FakeClient, make_service
    call = SimpleNamespace(type='tool_use', id='pipeline', name='get_pipeline_summary', input={})
    client = FakeClient([
        ([], [call], 'tool_use'),
        (["We've got about two hundred sixty-one thousand across fourteen open deals."], [], 'end_turn'),
        (['The exact total is 261,500 US dollars.'], [], 'end_turn'),
    ])
    service = make_service(client)
    rounded = await service.answer('Roughly how much is open?', [])
    result = client._calls[1]['messages'][-1]['content'][0]['content']
    assert json.loads(result)['total'] == '261500'
    exact = await service.answer('What is the exact total?', [{'role': 'assistant', 'content': rounded}])
    assert '261,500' in exact


@pytest.mark.asyncio
async def test_http_uses_one_request_with_supported_settings(monkeypatch):
    import aiohttp
    requests = []
    closed = asyncio.Event()

    class Response:
        status = 200

        @property
        def content(self):
            async def chunks():
                yield json.dumps({'audio_base64': base64.b64encode(b'\x01\x00' * 640).decode()}).encode()
            return chunks()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session:
        def post(self, url, **kwargs):
            requests.append(kwargs)
            return Response()

        async def close(self):
            closed.set()

    monkeypatch.setattr(aiohttp, 'ClientSession', Session)

    async def stream(text, context):
        yield "It's in proposal."
        yield 'Worth twelve thousand.'

    incoming, outgoing, _, _, task = await _start_session(monkeypatch, stream=stream, settings_extra={'tts_model': 'eleven_v4_turbo', 'tts_transport': 'http', 'tts_stability': .3, 'tts_similarity': .65})
    try:
        await _send_audio(incoming)
        await asyncio.wait_for(outgoing.get(), 3)
        assert len(requests) == 1
        payload = requests[0]['json']
        assert payload['text'] == "It's in proposal. Worth twelve thousand."
        assert payload['voice_settings'] == {'stability': .3, 'similarity_boost': .65}
        assert payload['apply_text_normalization'] == 'auto'
        assert requests[0]['params']['output_format'] == 'pcm_16000'
    finally:
        await finish(incoming, task)
    assert closed.is_set()


