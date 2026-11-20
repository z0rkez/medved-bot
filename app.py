"""Веб-чат поверх движка triage."""

import hashlib
import logging
import os
import sqlite3
import time
from contextlib import closing
from datetime import date
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from triage import Reply, Session, handle

SESSION_TTL = 60 * 60
STATIC = Path(__file__).parent / "static"
TOKEN_DB = Path(__file__).with_name(".used-tokens.sqlite3")
CORE_URL = os.environ.get("CORE_URL", "https://tm-test-yakut.hostco.ru").rstrip("/")


def _omit_query(record: logging.LogRecord) -> bool:
    # Uvicorn получает исходный URL ещё до того, как браузер удалит token.
    if isinstance(record.args, tuple) and len(record.args) == 5:
        client, method, path, version, status = record.args
        record.args = (client, method, path.partition("?")[0], version, status)
    return True


logging.getLogger("uvicorn.access").addFilter(_omit_query)
logging.getLogger("httpx").setLevel(logging.WARNING)  # INFO включает полный URL запроса
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.asgi").setLevel(logging.INFO)  # TRACE включает query_string

app = FastAPI(title="МедВед: маршрутизация пациента")
_sessions: dict[str, tuple[Session, float]] = {}


class Message(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    text: str = Field(max_length=2000)


class Init(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    token: str = Field(min_length=1, max_length=16384, repr=False)


def _claim_token(token: str) -> bool:
    # Атомарно отмечаем попытку до обращения к core, в том числе на случай таймаута.
    digest = hashlib.sha256(token.encode()).digest()
    with closing(sqlite3.connect(TOKEN_DB)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS used (digest BLOB PRIMARY KEY) WITHOUT ROWID")
        return db.execute("INSERT OR IGNORE INTO used VALUES (?)", (digest,)).rowcount == 1


def _patient_age(birth: date, today: date) -> float:
    if birth > today:
        raise ValueError("Дата рождения в будущем")
    months = (today.year - birth.year) * 12 + today.month - birth.month - (today.day < birth.day)
    # Движок хранит годы. Для младенцев сохраняем точность до полного месяца.
    return months / 12 if months < 12 else months // 12


def _session(sid: str) -> Session:
    now = time.time()
    for k in [k for k, (_, t) in _sessions.items() if now - t > SESSION_TTL]:
        del _sessions[k]
    s = _sessions.get(sid, (Session(), now))[0]
    _sessions[sid] = (s, now)
    return s


@app.post("/api/message")
async def message(m: Message) -> Reply:
    return handle(_session(m.session_id), m.text)


@app.post("/api/init")
async def init(m: Init) -> Reply:
    try:
        claimed = await run_in_threadpool(_claim_token, m.token)
    except sqlite3.Error:
        claimed = False
    if not claimed:
        raise HTTPException(409, "Автоматический вход недоступен. Можно начать опрос вручную.")

    notice = ""
    age = None
    name = ""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{CORE_URL}/api/v3/users/user-for-external-service",
                params={"token": m.token}, headers={"Authorization": f"Bearer {m.token}"},
            )
        response.raise_for_status()
        patient = response.json()
        age = _patient_age(date.fromisoformat(patient["birthDate"]), date.today())
        names = patient.get("full_name") or {}
        name = " ".join(value.strip() for key in ("firstName", "middleName")
                        if isinstance(value := names.get(key), str) and value.strip())
    except httpx.HTTPStatusError as exc:
        notice = ("Ссылка для входа недействительна или истекла." if exc.response.status_code in (401, 403)
                  else "Не удалось загрузить данные пациента.")
    except (httpx.RequestError, ValueError, KeyError, TypeError, AttributeError):
        notice = "Не удалось загрузить данные пациента."

    s = _session(m.session_id)
    s.__init__(age=age if not notice else None, patient_name=name, age_from_core=not notice)
    reply = handle(s, "/start")
    if notice:
        reply.text = f"{notice} Продолжим опрос вручную.\n\n{reply.text}"
    return reply


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Referrer-Policy": "no-referrer"})
