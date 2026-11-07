"""Веб-сервер для демо: чат в браузере поверх движка triage."""

import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from triage import Session, handle

SESSION_TTL = 60 * 60
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="МедВед: маршрутизация пациента")
_sessions: dict[str, tuple[Session, float]] = {}


class Message(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    text: str = Field(max_length=2000)


def _session(sid: str) -> Session:
    now = time.time()
    for k in [k for k, (_, t) in _sessions.items() if now - t > SESSION_TTL]:
        del _sessions[k]
    s = _sessions.get(sid, (Session(), now))[0]
    _sessions[sid] = (s, now)
    return s


@app.post("/api/message")
def message(m: Message) -> dict:
    r = handle(_session(m.session_id), m.text)
    return {"text": r.text, "buttons": r.buttons, "done": r.done, "meta": r.meta}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
