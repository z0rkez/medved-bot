"""Разбор свободного текста: нормализация, клаузы, поиск шаблонов с учётом отрицаний.

Шаблон: строка терминов через пробел. Термин с `*` на конце сравнивается по префиксу,
без `*` по точному совпадению. Все термины должны найтись в одной клаузе
на расстоянии не больше 5 слов. Если между ними или за 2 слова до них стоит
отрицание («не», «нет», «без»), совпадение не засчитывается.
"""

import re

NEG = frozenset({"не", "нет", "без", "нету", "ни"})
MAX_SPAN = 5
NEG_LOOKBEHIND = 2

_TOKEN = re.compile(r"[а-яa-z0-9]+")
_SPLIT = re.compile(r"[.,;:!?\n()]+|\s(?:но|а|однако)\s")


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def clauses(text: str) -> list[list[str]]:
    parts = _SPLIT.split(normalize(text))
    return [toks for p in parts if (toks := _TOKEN.findall(p))]


def _term_ok(term: str, tok: str) -> bool:
    return tok.startswith(term[:-1]) if term.endswith("*") else tok == term


def _match(terms: list[str], tokens: list[str]) -> bool | None:
    """True: найдено без отрицания, False: найдено с отрицанием, None: не найдено."""
    used: list[int] = []
    for term in terms:
        i = next((i for i, t in enumerate(tokens) if i not in used and _term_ok(term, t)), None)
        if i is None:
            return None
        used.append(i)
    lo, hi = min(used), max(used)
    if hi - lo > MAX_SPAN:
        return None
    window = range(max(0, lo - NEG_LOOKBEHIND), hi + 1)
    return not any(tokens[i] in NEG for i in window if i not in used)


def mentions(text: str, patterns: list[str]) -> bool:
    """Есть ли в тексте хотя бы один шаблон без отрицания."""
    for toks in clauses(text):
        for p in patterns:
            if _match(p.split(), toks) is True:
                return True
    return False


def numbers(text: str) -> list[float]:
    return [float(m.replace(",", ".")) for m in re.findall(r"\d+(?:[.,]\d+)?", text)]


def yes_no(text: str) -> bool | None:
    t = normalize(text).strip()
    if t.startswith(("да", "есть", "бывает", "yes", "угу", "ага")):
        return True
    if t.startswith(("нет", "не ", "no", "неа")) or t in {"не", "н"}:
        return False
    return None
