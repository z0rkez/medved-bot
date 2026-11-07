"""Движок диалога. Не зависит от транспорта: веб, MAX, Telegram вызывают `handle`."""

from dataclasses import dataclass, field
from enum import IntEnum

from .knowledge import (CHRONIC, COMBOS, CRISIS, MODIFIERS, RED_FLAGS, SPECIALISTS, SYMPTOMS,
                        SYMPTOMS_BY_ID)
from .text import mentions, normalize, numbers, yes_no

MAX_FOLLOWUPS = 3
RESTART = {"/start", "start", "заново", "начать заново", "старт", "сначала"}

DURATIONS = {
    "Меньше суток": "lt1d",
    "1–3 дня": "1_3d",
    "4–7 дней": "4_7d",
    "Больше недели": "gt1w",
    "Больше месяца": "gt1m",
}
DURATION_TITLES = {v: k.lower() for k, v in DURATIONS.items()}
TEMPS = ["Нет, до 37", "37–38", "38–39", "Выше 39"]
CHRONIC_BUTTONS = ["Нет хронических", "Сахарный диабет", "Гипертония", "Болезни сердца", "Астма"]


class Urgency(IntEnum):
    ROUTINE = 0
    URGENT = 1
    EMERGENCY = 2


@dataclass
class Reply:
    text: str
    buttons: list[str] = field(default_factory=list)
    done: bool = False
    meta: dict = field(default_factory=dict)  # для богатых клиентов (веб): шаг, жалобы, итог


@dataclass
class Session:
    state: str = "start"
    age: float | None = None
    sex: str | None = None  # "m" | "f"
    complaints: list[str] = field(default_factory=list)
    symptoms: list[str] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)
    emergency: str | None = None
    crisis: bool = False
    duration: str | None = None
    temp: float | None = None
    chronic: set[str] = field(default_factory=set)
    chronic_asked: bool = False
    pregnancy: str | None = None  # "yes" | "no" | "maybe"
    retries: int = 0

    @property
    def pregnant(self) -> bool:
        return self.pregnancy in ("yes", "maybe")

    @property
    def child(self) -> bool:
        return self.age is not None and self.age < 18


@dataclass
class Assessment:
    urgency: Urgency
    reasons: list[str]
    specialist: str
    alternatives: list[str]
    route: list[str]
    symptom_titles: list[str]
    crisis: bool = False


# ---------- разбор ответов ----------

def parse_age(text: str) -> float | None:
    t = normalize(text)
    nums = numbers(t)
    if not nums:
        return None
    v = nums[0]
    if "мес" in t:
        v /= 12
    elif "нед" in t:
        v /= 52
    return v if 0 <= v <= 120 else None


def parse_sex(text: str) -> str | None:
    t = normalize(text).strip()
    if t.startswith(("муж", "м", "парен", "мальч")):
        return "m"
    if t.startswith(("жен", "ж", "девуш", "девоч")):
        return "f"
    return None


def parse_temp(text: str) -> float | None:
    t = normalize(text)
    if t.startswith("нет"):  # кнопка «Нет, до 37»: число здесь граница, а не замер
        return 36.6
    nums = [n for n in numbers(t) if 34 <= n <= 43]
    if "выше" in t and nums:
        return nums[0] + 0.5
    if len(nums) >= 2:
        return (nums[0] + nums[1]) / 2
    if nums:
        return nums[0]
    if yes_no(t) is False or "нормальн" in t:
        return 36.6
    return None


def parse_duration(text: str) -> str | None:
    if text in DURATIONS:
        return DURATIONS[text]
    t = normalize(text)
    if "месяц" in t or "год" in t:
        return "gt1m"
    if "недел" in t:
        return "gt1w"
    if any(w in t for w in ("сегодня", "час", "утр", "вечер", "ночь")):
        return "lt1d"
    if "вчера" in t:
        return "1_3d"
    nums = numbers(t)
    if nums and ("дн" in t or "день" in t or "суток" in t):
        d = nums[0]
        return "lt1d" if d < 1 else "1_3d" if d <= 3 else "4_7d" if d <= 7 else "gt1w"
    return None


def detect_symptoms(text: str) -> list[str]:
    return [s.id for s in SYMPTOMS if mentions(text, s.patterns)]


def detect_red_flag(text: str) -> str | None:
    return next((f.title for f in RED_FLAGS if mentions(text, f.patterns)), None)


# ---------- оценка ----------

def assess(s: Session) -> Assessment:
    reasons: list[str] = []
    urgency = Urgency.ROUTINE
    if s.emergency:
        urgency = Urgency.EMERGENCY
        reasons.append(s.emergency)

    scores: dict[str, float] = {}
    for sid in s.symptoms:
        for spec, w in SYMPTOMS_BY_ID[sid].weights.items():
            scores[spec] = scores.get(spec, 0) + w

    if s.duration == "gt1m":  # давняя проблема: смещаемся к узкому специалисту
        scores = {k: v * (1 if k == "therapist" else 1.5) for k, v in scores.items()}

    conditions = set(s.chronic) | ({"pregnant"} if s.pregnant else set())
    for m in MODIFIERS:
        if m.condition in conditions and set(m.symptoms) & set(s.symptoms):
            scores[m.specialist] = scores.get(m.specialist, 0) + m.bonus
            if m.urgent:
                urgency = max(urgency, Urgency.URGENT)
                reasons.append(m.note)

    febrile = s.temp is not None and s.temp >= 38
    for c in COMBOS:
        if c.symptoms <= set(s.symptoms) and (febrile or not c.fever):
            scores[c.specialist] = scores.get(c.specialist, 0) + c.bonus
            if c.urgent:
                urgency = max(urgency, Urgency.URGENT)
                reasons.append(c.note)

    if s.temp is not None:
        if s.age is not None and s.age < 1 and s.temp >= 38:
            urgency = Urgency.EMERGENCY
            reasons.append("температура 38 и выше у ребёнка до года")
        elif s.temp >= 39:
            urgency = max(urgency, Urgency.URGENT)
            reasons.append("температура выше 39")
        elif s.temp >= 38 and s.duration in ("4_7d", "gt1w", "gt1m"):
            urgency = max(urgency, Urgency.URGENT)
            reasons.append("температура выше 38 держится больше 3 дней")

    if s.child and "therapist" in scores:
        scores["pediatrician"] = scores.get("pediatrician", 0) + scores.pop("therapist")
    primary = "pediatrician" if s.child else "therapist"

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top = ranked[0][0] if ranked else primary
    top_score = ranked[0][1] if ranked else 0
    alternatives = [k for k, v in ranked[1:] if v >= top_score * 0.6 and k != primary][:2]

    if SPECIALISTS[top].direct:
        route = [top]
    else:
        route = [primary, top]

    return Assessment(
        urgency=urgency,
        reasons=reasons,
        specialist=top,
        alternatives=alternatives,
        route=route,
        symptom_titles=[SYMPTOMS_BY_ID[x].title for x in s.symptoms],
        crisis=s.crisis,
    )


# ---------- форматирование ----------

def _spec(sid: str, child: bool) -> str:
    name = SPECIALISTS[sid].name
    if child and sid not in ("pediatrician", "therapist", "dent", "trauma"):
        return "детский " + name
    return name


def _card_rows(s: Session) -> list[tuple[str, str]]:
    sex = {"m": "мужской", "f": "женский"}.get(s.sex or "", "не указан")
    age = "не указан" if s.age is None else (f"{round(s.age * 12)} мес." if s.age < 1 else f"{int(s.age)}")
    rows = [
        ("Возраст", age),
        ("Пол", sex),
        ("Жалобы", "; ".join(s.complaints) or "не указаны"),
    ]
    if s.duration:
        rows.append(("Длительность", DURATION_TITLES[s.duration]))
    if s.temp is not None:
        rows.append(("Температура", "нет" if s.temp < 37 else f"около {s.temp:g}"))
    chronic = [CHRONIC[c].title for c in sorted(s.chronic)]
    rows.append(("Хронические заболевания", ", ".join(chronic) or "нет"))
    if s.pregnancy in ("yes", "maybe"):
        rows.append(("Беременность", "да" if s.pregnancy == "yes" else "возможна"))
    return rows


def _card(s: Session) -> str:
    rows = dict(_card_rows(s))
    lines = ["**Карточка для врача**", f"Возраст: {rows.pop('Возраст')}, пол: {rows.pop('Пол')}"]
    lines += [f"{k}: {v}" for k, v in rows.items()]
    return "\n".join(lines)


EMERGENCY_STEPS = ["Не оставайтесь одни", "Откройте входную дверь",
                   "Приготовьте паспорт и полис ОМС", "Соберите список лекарств, которые принимаете"]


def result_meta(s: Session, a: Assessment) -> dict:
    """Итог в виде данных: веб рисует по нему экран результата."""
    kind = ("crisis" if a.crisis else "emergency" if a.urgency is Urgency.EMERGENCY
            else "urgent" if a.urgency is Urgency.URGENT else "routine")
    child = s.child
    if len(a.route) == 1:
        route = [{"name": _spec(a.route[0], child), "note": "Без направления"}]
    else:
        route = [{"name": _spec(a.route[0], child), "note": "Без направления: осмотр и направление дальше"},
                 {"name": _spec(a.route[1], child), "note": f"По направлению: {_spec(a.route[0], child)}"}]
    return {
        "kind": kind,
        "reasons": a.reasons,
        "symptoms": a.symptom_titles,
        "route": route,
        "alternatives": [_spec(x, child) for x in a.alternatives],
        "card": [list(r) for r in _card_rows(s)],
        "steps": EMERGENCY_STEPS if kind == "emergency" else [],
        "child": child,
    }


DISCLAIMER = "_Это не диагноз, а подсказка, к какому врачу обратиться. Решение о лечении принимает врач._"


def format_result(s: Session, a: Assessment) -> str:
    if a.crisis:
        lines = [
            "**Вам сейчас очень тяжело, и вы не обязаны справляться с этим в одиночку.**",
            "Позвоните прямо сейчас: **112** (бесплатно, круглосуточно).",
        ]
        if s.child:
            lines.append("Детский телефон доверия: **8-800-2000-122** (бесплатно, анонимно).")
        lines.append("Если рядом есть близкий человек, расскажите ему, что происходит.")
        return "\n".join(lines)

    if a.urgency is Urgency.EMERGENCY:
        lines = [
            "🚨 **Срочно вызовите скорую: 103** (с мобильного также 112).",
            f"Причина: {', '.join(a.reasons)}.",
            "Пока ждёте: не оставайтесь одни, откройте входную дверь, приготовьте паспорт, полис и список лекарств.",
            "",
            _card(s),
        ]
        return "\n".join(lines)

    child = s.child
    if a.urgency is Urgency.URGENT:
        head = ("⚠️ **Нужен осмотр врача сегодня.** Вызовите врача на дом или обратитесь в кабинет неотложной помощи поликлиники. "
                "При ухудшении звоните 103.")
    else:
        head = "✅ **Можно записаться на плановый приём** через Госуслуги, сайт поликлиники или регистратуру."
    lines = [head]
    if a.reasons:
        lines.append(f"Причина: {', '.join(a.reasons)}.")
    lines.append("")
    if a.symptom_titles:
        lines.append(f"Распознанные жалобы: {', '.join(a.symptom_titles)}.")
    else:
        lines.append("Жалобы не удалось точно распознать, поэтому начните с врача первичного звена.")

    if len(a.route) == 1:
        lines.append(f"**Куда обратиться: {_spec(a.route[0], child)}** (без направления).")
    else:
        lines.append(f"**Маршрут: {_spec(a.route[0], child)} → {_spec(a.route[1], child)}**")
        lines.append(f"Скорее всего, понадобится {_spec(a.route[1], child)}. "
                     f"По ОМС к нему обычно направляет {_spec(a.route[0], child)}.")
    if a.alternatives:
        lines.append(f"Также может понадобиться: {', '.join(_spec(x, child) for x in a.alternatives)}.")
    lines += ["", _card(s), "", DISCLAIMER]
    return "\n".join(lines)


# ---------- диалог ----------

def _ask_age() -> Reply:
    return Reply("Здравствуйте! Я помогу понять, к какому врачу обратиться.\n"
                 "Если сейчас угрожает жизнь (сильная боль в груди, трудно дышать, потеря сознания), "
                 "сразу звоните **103**.\n\nСколько лет пациенту? Для ребёнка до года можно указать месяцы.")


def _finish(s: Session) -> Reply:
    s.state = "done"
    a = assess(s)
    return Reply(format_result(s, a), ["Начать заново"], done=True, meta={"result": result_meta(s, a)})


def _next(s: Session, prefix: str = "") -> Reply:
    """Задать следующий незаданный вопрос."""
    p = prefix + "\n\n" if prefix else ""
    if s.emergency or s.crisis:
        return _finish(s)
    if s.followups:
        s.state = "followup"
        q = SYMPTOMS_BY_ID[s.followups[0]].followup.question
        return Reply(p + q, ["Да", "Нет"])
    if s.duration is None:
        s.state = "duration"
        return Reply(p + "Как давно это началось?", list(DURATIONS))
    if s.temp is None:
        s.state = "temp"
        return Reply(p + "Есть ли повышенная температура?", TEMPS)
    if not s.chronic_asked:
        s.state = "chronic"
        s.chronic_asked = True
        return Reply(p + "Есть ли хронические заболевания? Можно перечислить своими словами.", CHRONIC_BUTTONS)
    if s.sex == "f" and s.age is not None and 12 <= s.age <= 55 and s.pregnancy is None:
        s.state = "pregnancy"
        return Reply(p + "Есть ли беременность или её вероятность?", ["Да", "Нет", "Не знаю"])
    return _finish(s)


STEPS = ["age", "sex", "complaints", "duration", "temp", "chronic", "pregnancy"]


def _progress(s: Session) -> dict:
    total = 7 if s.sex == "f" and s.age is not None and 12 <= s.age <= 55 else 6
    state = "complaints" if s.state == "followup" else s.state
    step = total if state == "done" else STEPS.index(state) + 1 if state in STEPS else 1
    return {"step": step, "total": total}


def handle(s: Session, text: str) -> Reply:
    r = _handle(s, text)
    r.meta.update(_progress(s))
    return r


def _handle(s: Session, text: str) -> Reply:
    text = text.strip()
    if normalize(text) in RESTART or s.state == "start":
        s.__init__()
        s.state = "age"
        return _ask_age()

    if s.state == "done":
        return Reply("Консультация завершена. Хотите пройти заново?", ["Начать заново"], done=True)

    if s.state == "age":
        age = parse_age(text)
        if age is None:
            return Reply("Напишите возраст числом, например: 34 или 8 месяцев.")
        s.age = age
        s.state = "sex"
        return Reply("Пол пациента?", ["Мужской", "Женский"])

    if s.state == "sex":
        sex = parse_sex(text)
        if sex is None:
            return Reply("Выберите пол.", ["Мужской", "Женский"])
        s.sex = sex
        s.state = "complaints"
        return Reply("Опишите своими словами, что беспокоит. Например: «болит горло, температура, слабость».")

    if s.state == "complaints":
        if mentions(text, CRISIS.patterns):
            s.complaints.append(text)
            s.crisis = True
            return _finish(s)
        if flag := detect_red_flag(text):
            s.complaints.append(text)
            s.emergency = flag
            return _finish(s)
        found = [x for x in detect_symptoms(text) if x not in s.symptoms]
        if not found and not s.symptoms:
            s.retries += 1
            if s.retries < 2:
                return Reply("Не совсем понял жалобы. Опишите проще, что и где болит или беспокоит. "
                             "Например: «кашель и насморк» или «болит поясница».")
        s.complaints.append(text)
        s.symptoms += found
        s.followups = [x for x in s.symptoms if SYMPTOMS_BY_ID[x].followup][:MAX_FOLLOWUPS]
        titles = [SYMPTOMS_BY_ID[x].title for x in s.symptoms]
        r = _next(s)
        if titles and not r.done:
            r.meta["recognized"] = titles
            r.meta["question"] = r.text
            r.text = f"Понял: {', '.join(titles)}.\n\n{r.text}"
        return r

    if s.state == "followup":
        ans = yes_no(text)
        if ans is None:
            return Reply("Ответьте, пожалуйста, да или нет.", ["Да", "Нет"])
        sid = s.followups.pop(0)
        if ans:
            s.emergency = SYMPTOMS_BY_ID[sid].followup.reason
        return _next(s)

    if s.state == "duration":
        d = parse_duration(text)
        if d is None:
            return Reply("Выберите вариант.", list(DURATIONS))
        s.duration = d
        return _next(s)

    if s.state == "temp":
        t = parse_temp(text)
        if t is None:
            return Reply("Выберите вариант или напишите число, например 38.2.", TEMPS)
        s.temp = t
        return _next(s)

    if s.state == "chronic":
        if mentions(text, CRISIS.patterns):
            s.crisis = True
            return _finish(s)
        s.chronic = {cid for cid, c in CHRONIC.items() if mentions(text, c.patterns)}
        if mentions(text, ["беремен*"]):
            s.pregnancy = "yes"
        return _next(s)

    if s.state == "pregnancy":
        t = normalize(text)
        s.pregnancy = "maybe" if "знаю" in t or "возможн" in t else "yes" if yes_no(t) else "no"
        return _finish(s)

    return Reply("Что-то пошло не так. Начнём заново?", ["Начать заново"])
