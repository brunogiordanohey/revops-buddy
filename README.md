# RevOps Buddy v2

An AI RevOps teammate that joins your Google Meet sales calls, listens to the whole conversation, and answers pipeline questions out loud when someone calls it by name: **"Angels, what's left to close Acme?"**

> RevOps Buddy v1 (2025) was a local push-to-talk prototype (Whisper + Llama on Groq + a CSV). It lives at the [`v1` tag](https://github.com/brunogiordanohey/revops-buddy/tree/v1). In v2 the bot joins real calls and answers to **"Angels"**.

Blog post: [One year later: my voice agent now joins the call](https://www.brugiordano.com/one-year-later-voice-agent-joins-the-call/)

## How it works

```
Google Meet ──audio──▶ MeetingBaas ──WSS, PCM 16 kHz──▶ Deepgram (streaming STT, speaker names)
                                                              │
                                     wake word "Angels" ◀─────┤ every sentence → 40-sentence call memory
                                              │
                                              ▼
                       Claude (tools, read-only) ──▶ local CRM snapshot (synced from HubSpot before the call)
                                              │
Google Meet ◀──voice── MeetingBaas ◀──WSS── ElevenLabs (TTS)
```

1. **Joining.** `POST /meetings` asks MeetingBaas to send a bot into the call. MeetingBaas opens two WebSockets to this server: one carries the meeting audio in, the other carries the bot's voice out.
2. **Listening.** Deepgram transcribes in streaming. Every final sentence goes into a memory of the last 40 sentences, with who said it.
3. **Deciding to speak.** The bot only answers when someone says its name (or a short follow-up from the same person right after it spoke). Everything else stays as context.
4. **Answering.** Claude gets the question plus the call memory and uses read-only tools (pipeline summary, forecasts, deal lookup…). Totals are computed in code, never by the model. Answers are written for speech: rounded amounts, the important part first.
5. **Ready before the call.** Before joining, if the last HubSpot sync is older than 5 minutes, it syncs HubSpot into an immutable local snapshot (all pages or none). During the call the tools read that snapshot, so nobody waits on the CRM API. If the sync fails, it keeps the previous snapshot.
6. **Speaking.** ElevenLabs turns the answer into voice, streamed back into the call. A short "one moment" covers slow answers; saying "stop" cuts it off.

Animated explainers: [`docs/animations/`](docs/animations/) (open the HTML files in a browser; add `?lang=en` for English).

## Quickstart

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv venv .venv && uv pip install -e '.[dev]'
cp .env.example .env          # fill in the provider keys
```

MeetingBaas must reach this server over public `wss://`, so expose it with a tunnel (e.g. `cloudflared tunnel --url http://localhost:8000`) and put that HTTPS origin in `PUBLIC_BASE_URL`.

```bash
.venv/bin/python -m revops_buddy.server
curl -X POST http://localhost:8000/meetings \
  -H 'Content-Type: application/json' \
  -d '{"meeting_url": "https://meet.google.com/abc-defg-hij", "language": "en"}'
```

Admit the bot in Meet and ask: *"Angels, what's our pipeline this quarter?"*

With `DATA_MODE=synthetic` (the default) it answers from a bundled demo company, so you need no CRM. For your own data set `DATA_MODE=live`, a HubSpot private app token with CRM read scopes, and a company config (`COMPANY_CONFIG_PATH`, same shape as `revops_buddy/fixtures/company.json`).

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `PUBLIC_BASE_URL` | yes | Public HTTPS origin MeetingBaas connects back to |
| `MEETINGBAAS_API_KEY` | yes | Sends the bot into the call |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | yes | Answers and tool use (e.g. `claude-haiku-5-5`) |
| `DEEPGRAM_API_KEY` | yes | Speech to text |
| `ELEVENLABS_API_KEY`, `TTS_VOICE_ID` | yes | Text to speech (`TTS_VOICE_ID_ES` for Spanish calls) |
| `DATA_MODE` | no | `synthetic` (default) or `live` |
| `HUBSPOT_ACCESS_TOKEN`, `COMPANY_CONFIG_PATH` | live mode | CRM sync and field mapping |
| `DATABASE_URL` | no | Defaults to local SQLite; Postgres URLs work too |
| `BOT_NAME` | no | The bot's name and wake word (default `Angels`) |
| `MEETING_LANGUAGE` | no | `en` (default) or `es`; pinned for the whole call |
| `API_TOKEN` | no | If set, POST routes require `Authorization: Bearer <token>` |
| `PRESYNC_MAX_AGE_SECONDS` | no | Re-sync HubSpot if the snapshot is older (default 300) |

`GET /health` shows which providers are configured (booleans only).

## Limits

- A prototype: one call at a time, Google Meet only, English and Spanish.
- Read-only CRM. The private version can also update deals (with spoken confirmation) and post deal links in the meeting chat; those are not included here.
- Latency from the end of a question to the first spoken word is about 3 seconds, mostly the model.
- Costs come from MeetingBaas, Deepgram, ElevenLabs and Anthropic usage.

## Tests

```bash
.venv/bin/python -m pytest -q
```

All tests run offline with fake providers.

## License

MIT
