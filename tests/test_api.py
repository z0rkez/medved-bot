from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


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
