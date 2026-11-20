import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

import app as server

client = TestClient(server.app)


def say(sid, text):
    r = client.post("/api/message", json={"session_id": sid, "text": text})
    assert r.status_code == 200
    return r.json()


def test_full_dialog_over_http():
    sid = "test-session-1"
    assert "Сколько лет" in say(sid, "/start")["text"]
    assert say(sid, "30")["buttons"] == ["Мужской", "Женский"]
    say(sid, "Мужской")
    say(sid, "болит зуб")
    say(sid, "1–3 дня")
    say(sid, "Нет, до 37")
    r = say(sid, "Нет хронических")
    assert r["done"] and "стоматолог" in r["text"]


def test_index_served():
    assert "МедВед" in client.get("/").text


@pytest.fixture
def core(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "TOKEN_DB", tmp_path / "used.sqlite3")
    monkeypatch.setattr(server, "CORE_URL", "https://core.test")
    requests = []

    def respond(status=200, **fields):
        async def handle_request(transport, request):
            requests.append(request)
            if status is None:
                raise httpx.ReadTimeout("core timeout", request=request)
            return httpx.Response(status, json=fields)
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle_request)
        return requests
    return respond


@pytest.mark.parametrize("middle_name", ["Петровна", None])
def test_init_uses_only_age_and_given_names(core, middle_name, caplog):
    names = {"firstName": "Анна", "lastName": "СкрытаяФамилия"}
    if middle_name:
        names["middleName"] = middle_name
    private = {"_id": "private-id", "phone": "private-phone", "SNILS": "private-snils",
               "OMS": {"number": "private-oms"}}
    requests = core(birthDate="1987-10-26", full_name=names, **private)
    sid, token = "profile-session", "test-private-token"
    caplog.set_level(logging.INFO)
    r = client.post("/api/init", json={"session_id": sid, "token": token})
    assert r.status_code == 200
    reply = r.json()
    assert reply["text"].startswith("Здравствуйте, Анна" + (" Петровна!" if middle_name else "!"))
    assert reply["buttons"] == ["Мужской", "Женский"]
    request, = requests
    assert str(request.url) == "https://core.test/api/v3/users/user-for-external-service?token=" + token
    assert request.headers["Authorization"] == "Bearer " + token
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 5)
    session = server._session(sid)
    today = date.today()
    assert session.age == today.year - 1987 - ((today.month, today.day) < (10, 26))
    for value in (token, "private-id", "private-phone", "private-snils", "private-oms",
                  "1987-10-26", "СкрытаяФамилия"):
        assert value not in repr(session) + r.text + caplog.text
    assert "беспокоит" in say(sid, "Женский")["text"]


@pytest.mark.parametrize("status", [503, 401, None])
def test_init_falls_back_and_spends_token(core, status):
    requests = core(status)
    payload = {"session_id": "fallback-test", "token": "expired-or-unavailable"}
    response = client.post("/api/init", json=payload)
    assert response.status_code == 200
    text = response.json()["text"]
    assert "Сколько лет" in text and "вручную" in text
    if status == 401:
        assert "истекла" in text
    assert "Пол пациента?" in say(payload["session_id"], "25")["text"]
    assert client.post("/api/init", json=payload).status_code == 409
    assert len(requests) == 1


def test_concurrent_init_spends_token_once(core):
    requests = core(birthDate="2000-01-01", full_name={"firstName": "Анна"})

    def initialize(sid):
        return client.post("/api/init", json={"session_id": sid, "token": "one-use"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(initialize, ["session-one", "session-two"]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(requests) == 1
    assert "Анна" not in next(r.text for r in responses if r.status_code == 409)


@pytest.mark.parametrize("today,expected", [
    (date(2026, 8, 20), 0),
    (date(2026, 11, 19), 2 / 12),
    (date(2026, 11, 20), 3 / 12),
    (date(2027, 8, 19), 11 / 12),
    (date(2027, 8, 20), 1),
])
def test_age_at_month_and_year_boundaries(today, expected):
    assert server._patient_age(date(2026, 8, 20), today) == expected
