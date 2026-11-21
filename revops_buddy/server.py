"""Minimal MeetingBaas API and duplex voice transport."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import logging
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel
from typing import Literal

from .config import Settings
from .conversation import AnswerService
from .domain import RevOpsService
from .hubspot import HubSpotSync
from .meetingbaas import DuplexWebSocket, MeetingBaasClient, MeetingBaasError
from .repository import Repository

logger = logging.getLogger(__name__)
MB_PAIR_TIMEOUT_SECONDS = 15
MB_POLL_SECONDS = 3
MB_RETRY_WINDOW_SECONDS = 90
MB_TERMINAL_STATES = {"completed", "ended", "fatal_error", "failed", "done", "call_ended",
                      "bot_left", "left_call", "not_admitted", "waiting_room_timeout", "error"}


class RedactTokensFilter(logging.Filter):
    """Uvicorn access messages include WebSocket query strings."""
    _TOKEN = re.compile(r"(token=)[^&\s\"']+")

    def filter(self, record):
        record.msg = self._TOKEN.sub(r"\1[redacted]", str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._redact(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._redact(v) for v in record.args)
        return True

    def _redact(self, value):
        return self._TOKEN.sub(r"\1[redacted]", value) if isinstance(value, str) else value


@dataclass
class Call:
    session_id: str
    token: str
    language: str
    bot_id: str = ""
    bot_ids: set[str] = field(default_factory=set)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    pair: dict | None = None
    monitor: asyncio.Task | None = None
    stopped: bool = False
    admitted: bool = False
    retries: int = 0
    created_at: float = field(default_factory=time.monotonic)
    options: dict = field(default_factory=dict)


class MeetingRequest(BaseModel):
    meeting_url: str
    language: Literal["en", "es"] | None = None


def create_app(settings: Settings | None = None, *, meetingbaas_client=None,
               repository: Repository | None = None, sync_factory=HubSpotSync,
               voice_runner=None) -> FastAPI:
    settings = settings or Settings.from_env()
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        call = app.state.active
        if call:
            try:
                await stop(call)
            except MeetingBaasError:
                logger.warning("MeetingBaas leave failed during shutdown")
        if app.state.meetingbaas is not None:
            await app.state.meetingbaas.aclose()
        if app.state.repository is not None and repository is None:
            app.state.repository.close()

    app = FastAPI(title="RevOps Buddy", lifespan=lifespan)
    app.state.active = None
    app.state.repository = repository
    app.state.meetingbaas = meetingbaas_client
    app.state.voice_runner = voice_runner
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    for name in ("uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addFilter(RedactTokensFilter())

    def client():
        if app.state.meetingbaas is None:
            app.state.meetingbaas = MeetingBaasClient(settings.meetingbaas_api_key, settings.meetingbaas_api_base_url)
        return app.state.meetingbaas

    def ensure_repository():
        if app.state.repository is None:
            url = settings.database_url
            for prefix in ("postgres://", "postgresql://"):
                if url.startswith(prefix):
                    url = "postgresql+psycopg://" + url[len(prefix):]
            app.state.repository = Repository(url)
        app.state.repository.initialize()
        return app.state.repository

    async def presync():
        repo = await asyncio.to_thread(ensure_repository)
        config = settings.company_config()
        service = RevOpsService(repo, config)
        if settings.data_mode == "synthetic":
            await asyncio.to_thread(service.load_demo)
            return service
        snapshot = await asyncio.to_thread(repo.latest_snapshot)
        stale = True
        if snapshot:
            captured = datetime.fromisoformat(snapshot["captured_at"].replace("Z", "+00:00"))
            stale = (datetime.now(timezone.utc) - captured).total_seconds() > settings.presync_max_age_seconds
        if stale:
            try:
                sync = sync_factory(settings.hubspot_access_token, repo, config)
                await asyncio.to_thread(sync.sync)
            except Exception as exc:
                logger.warning("HubSpot presync failed (%s); continuing with previous snapshot", type(exc).__name__)
        return service

    app.state.presync = presync

    async def require_token(request: Request):
        if settings.api_token:
            expected = "Bearer " + settings.api_token
            if not secrets.compare_digest(request.headers.get("authorization", "").encode(), expected.encode()):
                raise HTTPException(401, "Bearer token required")

    async def clear_mb_pair(call):
        pair, call.pair = call.pair, None
        if pair and pair.get("task") is not asyncio.current_task():
            pair["task"].cancel()
            await asyncio.gather(pair["task"], return_exceptions=True)

    async def stop(call):
        call.stopped = True
        call.ready.set()
        if call.monitor and call.monitor is not asyncio.current_task():
            call.monitor.cancel()
            await asyncio.gather(call.monitor, return_exceptions=True)
        await clear_mb_pair(call)
        try:
            if call.bot_id:
                await client().leave(call.bot_id)
        finally:
            if app.state.active is call:
                app.state.active = None

    async def refresh(call):
        status = await client().status(call.bot_id)
        if status.startswith("in_call"):
            call.admitted = True
        if status not in MB_TERMINAL_STATES:
            return
        if (not call.stopped and not call.admitted and call.retries < settings.mb_join_retries
                and time.monotonic() - call.created_at <= MB_RETRY_WINDOW_SECONDS):
            call.retries += 1
            call.ready.clear()
            await clear_mb_pair(call)
            try:
                call.bot_id = await client().create_bot(**call.options)
                call.bot_ids.add(call.bot_id)
                call.created_at = time.monotonic()
                logger.warning("MeetingBaas exited before admission; retried once")
            finally:
                call.ready.set()
        else:
            await stop(call)

    app.state.refresh = refresh

    async def monitor(call):
        try:
            while not call.stopped:
                await asyncio.sleep(MB_POLL_SECONDS)
                async with lock:
                    if call.stopped:
                        return
                    try:
                        await refresh(call)
                    except MeetingBaasError:
                        logger.warning("MeetingBaas status request failed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Meeting monitor failed (%s)", type(exc).__name__)
            await stop(call)

    @app.get("/health")
    async def health():
        return {"ok": True, "providers": settings.providers()}

    @app.post("/meetings", dependencies=[Depends(require_token)])
    async def join(body: MeetingRequest):
        parsed = urlsplit(body.meeting_url)
        if (parsed.scheme != "https" or parsed.hostname != "meet.google.com" or parsed.username
                or parsed.password or not re.fullmatch(r"/[a-z]{3}-[a-z]{4}-[a-z]{3}", parsed.path)):
            raise HTTPException(422, "A Google Meet URL is required")
        try:
            base = settings.public_wss_base
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from None
        async with lock:
            if app.state.active is not None:
                raise HTTPException(409, "One call is already active")
            service = await app.state.presync()
            call = Call(secrets.token_urlsafe(18), secrets.token_urlsafe(32), body.language or settings.meeting_language)
            query = urlencode({"session_id": call.session_id, "token": call.token})
            call.options = {"meeting_url": body.meeting_url, "bot_name": settings.bot_name,
                            "output_url": f"{base}/audio/out?{query}", "input_url": f"{base}/audio/in?{query}",
                            "extra": {"session_id": call.session_id,
                                      "bot_image": settings.public_base_url.rstrip('/') + '/static/bot.png'}}
            app.state.active = call
            app.state.service = service
            try:
                call.bot_id = await client().create_bot(**call.options)
                call.bot_ids.add(call.bot_id)
            except MeetingBaasError as exc:
                app.state.active = None
                raise HTTPException(502, "MeetingBaas could not create the bot") from None
            except Exception:
                app.state.active = None
                raise
            finally:
                call.ready.set()
            call.monitor = asyncio.create_task(monitor(call))
            return {"bot_id": call.bot_id}

    @app.post("/meetings/{bot_id}/leave", dependencies=[Depends(require_token)])
    async def leave(bot_id: str):
        async with lock:
            call = app.state.active
            if call is None or bot_id not in call.bot_ids:
                raise HTTPException(404, "Unknown active bot")
            try:
                await stop(call)
            except MeetingBaasError:
                raise HTTPException(502, "MeetingBaas leave failed") from None
        return {"ok": True}

    async def run_audio(duplex, call):
        from .voice import run_voice_session
        runtime = replace(settings, meeting_language=call.language).voice_settings(app.state.service.company_config)
        answer = AnswerService(app.state.service, runtime)
        runtime["on_admitted"] = lambda: setattr(call, "admitted", True)
        runtime["set_turn_speaker"] = lambda name: setattr(answer, "turn_speaker", name)

        async def transcript(speaker, text):
            # The voice pipeline owns its bounded in-memory call history.
            pass

        await (app.state.voice_runner or run_voice_session)(duplex, session_id=call.session_id,
            expected_bot_id=call.bot_id, settings=runtime, on_transcript=transcript,
            answer=answer.answer, answer_stream=answer.stream_answer)

    async def pair_session(call, pair):
        duplex = None
        tasks = []
        try:
            await asyncio.wait_for(pair["ready"].wait(), MB_PAIR_TIMEOUT_SECONDS)
            duplex = DuplexWebSocket(pair["out"], pair["in"])

            async def drain_input():
                while True:
                    if (await pair["in"].receive())["type"] == "websocket.disconnect":
                        return

            tasks = [asyncio.create_task(run_audio(duplex, call)), asyncio.create_task(drain_input())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.TimeoutError:
            logger.warning("MeetingBaas audio pairing timed out")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Voice session ended (%s)", type(exc).__name__)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if duplex:
                await duplex.close()
            else:
                await asyncio.gather(*(pair[d].close(code=1011) for d in ("out", "in") if d in pair), return_exceptions=True)
            if call.pair is pair:
                call.pair = None
            pair["done"].set()

    @app.websocket("/audio/{direction}")
    async def audio_socket(websocket: WebSocket, direction: str, session_id: str = "", token: str = ""):
        await websocket.accept()
        call = app.state.active
        if (call is None or call.stopped or direction not in {"out", "in"}
                or not secrets.compare_digest(call.session_id, session_id)
                or not secrets.compare_digest(call.token, token)):
            await websocket.close(code=1008)
            return
        try:
            await asyncio.wait_for(call.ready.wait(), 30)
        except asyncio.TimeoutError:
            await websocket.close(code=1013)
            return
        if call.stopped or app.state.active is not call or not call.bot_id:
            await websocket.close(code=1008)
            return
        pair = call.pair
        if pair is None:
            pair = {"ready": asyncio.Event(), "done": asyncio.Event(), direction: websocket}
            call.pair = pair
            pair["task"] = asyncio.create_task(pair_session(call, pair))
        elif direction in pair:
            await websocket.close(code=1008)
            return
        else:
            pair[direction] = websocket
        if "out" in pair and "in" in pair:
            pair["ready"].set()
        await pair["done"].wait()

    return app


app = create_app()


def main():
    import uvicorn
    settings = Settings.from_env()
    uvicorn.run("revops_buddy.server:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
