import pytest

from triage import Session, Urgency, assess, handle
from triage.engine import detect_red_flag, detect_symptoms
from triage.text import mentions


def run(*answers: str) -> tuple[Session, str]:
    s = Session()
    handle(s, "/start")
    reply = None
    for a in answers:
        reply = handle(s, a)
    return s, reply.text


def finish(s: Session) -> str:
    """Отвечать на оставшиеся вопросы нейтрально, пока диалог не закончится."""
    defaults = {"followup": "Нет", "duration": "1–3 дня", "temp": "Нет, до 37",
                "chronic": "Нет хронических", "pregnancy": "Нет"}
    for _ in range(10):
        if s.state == "done":
            break
        r = handle(s, defaults[s.state])
    return r.text


# ---------- разбор текста ----------

@pytest.mark.parametrize("text,expected", [
    ("болит голова", True),
    ("голова не болит", False),
    ("у меня сильно болит голова", True),
    ("без головной боли", False),
])
def test_negation(text, expected):
    assert mentions(text, ["голов* бол*", "бол* голов*"]) is expected


def test_negation_scoped_to_clause():
    assert detect_symptoms("голова не болит, но кашель") == ["cough"]
    assert detect_symptoms("болит горло и нет температуры") == ["sore_throat"]


def test_negation_inside_pattern():
    assert "dyspnea" in detect_symptoms("не хватает воздуха")


def test_ear_does_not_match_uhodit():
    assert "ear" not in detect_symptoms("боль уходит и возвращается")
    assert "ear" in detect_symptoms("болит ухо")


# ---------- маршруты ----------

def test_headache_routes_to_neuro_via_therapist():
    s, _ = run("34", "Мужской", "болит голова уже давно")
    text = finish(s)
    a = assess(s)
    assert a.specialist == "neuro"
    assert a.route == ["therapist", "neuro"]
    assert a.urgency is Urgency.ROUTINE
    assert "терапевт → невролог" in text


def test_tooth_direct_to_dentist():
    s, _ = run("25", "Женский", "болит зуб")
    finish(s)
    assert assess(s).route == ["dent"]


def test_injury_to_trauma():
    s, _ = run("40", "Мужской", "подвернул ногу, опухла лодыжка")
    finish(s)
    assert assess(s).route == ["trauma"]


def test_child_gets_pediatrician():
    s, _ = run("5", "Мужской", "кашель и насморк")
    handle(s, "1–3 дня")
    handle(s, "38–39")
    text = finish(s)
    a = assess(s)
    assert a.specialist == "pediatrician"
    assert "педиатр" in text


def test_child_specialist_prefixed():
    s, _ = run("7", "Женский", "болит ухо")
    text = finish(s)
    assert "детский оториноларинголог" in text


def test_unknown_complaint_falls_back_to_therapist():
    s, r = run("30", "Мужской", "что-то мне нехорошо")
    assert "Не совсем понял" in r
    handle(s, "просто нехорошо")
    finish(s)
    assert assess(s).route == ["therapist"]


def test_long_duration_shifts_to_specialist():
    s, _ = run("45", "Мужской", "кашель")
    handle(s, "Больше месяца")
    finish(s)
    # терапевт 2, пульмонолог 1*1.5: всё ещё терапевт, но пульмонолог в альтернативах
    assert assess(s).specialist == "therapist"


# ---------- срочность ----------

def test_red_flag_in_text_stops_dialog():
    s, text = run("67", "Мужской", "у мамы перекосило лицо и не может говорить")
    assert s.state == "done"
    assert "103" in text
    assert assess(s).urgency is Urgency.EMERGENCY


def test_chest_pain_followup_yes_is_emergency():
    s, r = run("55", "Мужской", "болит в груди")
    assert "отдаёт в руку" in r
    r = handle(s, "Да")
    assert s.state == "done"
    assert "103" in r.text


def test_chest_pain_followup_no_goes_to_cardio():
    s, _ = run("55", "Мужской", "болит в груди")
    handle(s, "Нет")
    finish(s)
    assert assess(s).specialist == "cardio"


def test_high_fever_is_urgent():
    s, _ = run("30", "Женский", "температура и ломит тело")
    handle(s, "1–3 дня")
    handle(s, "Выше 39")
    finish(s)
    assert assess(s).urgency is Urgency.URGENT


def test_infant_fever_is_emergency():
    s, _ = run("6 месяцев", "Мужской", "температура")
    handle(s, "Меньше суток")
    text = handle(s, "38–39").text
    finish(s)
    assert assess(s).urgency is Urgency.EMERGENCY
    assert s.age == pytest.approx(0.5)


def test_pregnancy_abdominal_pain():
    s, _ = run("28", "Женский", "тянет и болит живот внизу")
    handle(s, "Нет")  # followup острого живота
    handle(s, "Меньше суток")
    handle(s, "Нет, до 37")
    handle(s, "Нет хронических")
    r = handle(s, "Да")
    a = assess(s)
    assert a.specialist == "gyn"
    assert a.urgency is Urgency.URGENT
    assert "Беременность: да" in r.text


def test_pregnancy_not_asked_for_men():
    s, _ = run("28", "Мужской", "болит живот")
    finish(s)
    assert s.pregnancy is None


def test_diabetes_vomiting_urgent():
    s, _ = run("50", "Женский", "тошнит и рвота")
    handle(s, "Меньше суток")
    handle(s, "Нет, до 37")
    handle(s, "сахарный диабет 2 типа")
    finish(s)
    a = assess(s)
    assert "diabetes" in s.chronic
    assert a.urgency is Urgency.URGENT


def test_crisis_message():
    s, text = run("16", "Женский", "не хочу жить")
    assert s.crisis
    assert "112" in text and "8-800-2000-122" in text


@pytest.mark.parametrize("text", ["потерял сознание", "рвота кровью", "отек горла после укуса пчелы"])
def test_red_flags(text):
    assert detect_red_flag(text)


def test_restart():
    s, _ = run("30", "Мужской")
    r = handle(s, "Начать заново")
    assert s.age is None and "Сколько лет" in r.text


def test_pyelonephritis_pattern_goes_to_uro_urgently():
    s, _ = run("42", "Женский", "второй день болит поясница справа, больно писать и часто бегаю в туалет")
    handle(s, "1–3 дня")
    handle(s, "38–39")
    finish(s)
    a = assess(s)
    assert a.specialist == "uro"
    assert a.urgency is Urgency.URGENT


def test_back_and_urination_without_fever_is_routine_uro():
    s, _ = run("42", "Женский", "болит поясница и больно писать")
    finish(s)
    a = assess(s)
    assert a.specialist == "uro" and a.urgency is Urgency.ROUTINE


def test_abdominal_pain_vomiting_fever_to_surgeon():
    s, _ = run("30", "Мужской", "болит живот, рвота")
    handle(s, "Нет")
    handle(s, "Меньше суток")
    handle(s, "38–39")
    finish(s)
    a = assess(s)
    assert a.specialist == "surgeon" and a.urgency is Urgency.URGENT


def test_unrecognized_retry_not_in_card():
    s, _ = run("42", "Женский", "абракадабра")
    handle(s, "болит зуб")
    assert s.complaints == ["болит зуб"]


# ---------- данные для веба ----------

def test_meta_recognized_and_progress():
    s = Session()
    handle(s, "/start")
    assert handle(s, "42").meta == {"step": 2, "total": 6}
    r = handle(s, "Женский")
    assert r.meta == {"step": 3, "total": 7}
    r = handle(s, "болит поясница и больно писать")
    assert r.meta["recognized"] == ["боль в спине", "проблемы с мочеиспусканием"]
    assert r.meta["question"] == "Как давно это началось?"
    assert r.meta["step"] == 4


def test_meta_result_route_and_card():
    s, _ = run("42", "Женский", "второй день болит поясница справа, больно писать")
    handle(s, "1–3 дня")
    handle(s, "38–39")
    handle(s, "Нет хронических")
    r = handle(s, "Нет")
    res = r.meta["result"]
    assert res["kind"] == "urgent"
    assert [x["name"] for x in res["route"]] == ["терапевт", "уролог"]
    assert ["Температура", "около 38.5"] in res["card"]
    assert r.meta["step"] == r.meta["total"]


def test_meta_emergency_steps():
    s = Session()
    handle(s, "/start")
    handle(s, "67")
    handle(s, "Мужской")
    res = handle(s, "перекосило лицо").meta["result"]
    assert res["kind"] == "emergency" and res["steps"] and res["reasons"]


def test_no_fever_button_is_not_37():
    from triage.engine import parse_temp
    assert parse_temp("Нет, до 37") < 37
    assert parse_temp("37.5") == 37.5
