"""Запись демо МедВед в человеческом темпе. Видео = ровно вьюпорт страницы, курсор не рисуется."""
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
W, H = 420, 860
random.seed(7)


pos = [W / 2, H / 2]


def pause(a, b=None):
    time.sleep(a if b is None else random.uniform(a, b))


def move(page, x, y):
    steps = max(12, int(((x - pos[0]) ** 2 + (y - pos[1]) ** 2) ** 0.5 / 12))
    for i in range(1, steps + 1):
        t = i / steps
        k = t * t * (3 - 2 * t)  # плавный разгон и торможение
        page.mouse.move(pos[0] + (x - pos[0]) * k, pos[1] + (y - pos[1]) * k)
        time.sleep(0.012)
    pos[:] = [x, y]


def click(page, locator):
    box = locator.bounding_box()
    move(page, box["x"] + box["width"] * random.uniform(0.4, 0.6), box["y"] + box["height"] * random.uniform(0.4, 0.6))
    pause(0.15, 0.3)
    page.mouse.down(); pause(0.08); page.mouse.up()


def chip(page, text):
    click(page, page.locator("#chips .chip", has_text=text).first)
    pause(1.0, 1.4)


def say(page, text):
    click(page, page.locator("#input"))
    pause(0.3, 0.5)
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(random.uniform(0.25, 0.4) if ch == " " else random.uniform(0.06, 0.15))
    pause(0.5, 0.8)
    page.keyboard.press("Enter")
    pause(1.2, 1.6)


def scroll(page, el, dy, steps=10):
    box = page.locator(el).bounding_box()
    move(page, box["x"] + box["width"] * 0.8, box["y"] + box["height"] * 0.55)
    for _ in range(steps):
        page.mouse.wheel(0, dy / steps)
        time.sleep(0.06)


with sync_playwright() as p:
    # без обёртки /usr/bin/chromium: она подмешивает flags.conf с масштабом 0.9
    browser = p.chromium.launch(executable_path="/usr/lib/chromium/chromium")
    ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1, color_scheme="light",
                              record_video_dir=str(OUT), record_video_size={"width": W, "height": H},
                              permissions=["clipboard-read", "clipboard-write"], locale="ru-RU")
    page = ctx.new_page()
    page.goto("http://localhost:8765/")
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready.then(() => 1)")
    page.mouse.move(*pos)
    pause(2.5)

    # сценарий 1: поясница с температурой → сегодня, терапевт → уролог
    click(page, page.locator("#go")); pause(1.8)
    say(page, "42")
    chip(page, "Женский"); pause(0.6)
    say(page, "Второй день болит поясница справа, больно писать")
    pause(1.5)
    chip(page, "1–3 дня")
    chip(page, "38–39")
    chip(page, "Нет хронических")
    chip(page, "Нет")
    pause(3.0)
    scroll(page, "#result", 420, 14); pause(2.5)
    click(page, page.locator("#result [aria-label='Скопировать карточку']")); pause(1.5)
    scroll(page, "#result", 400, 10); pause(2.5)

    # сценарий 2: признаки инсульта → экстренно
    click(page, page.locator("#result .btn.ghost")); pause(1.8)
    say(page, "67")
    chip(page, "Мужской"); pause(0.6)
    say(page, "У папы перекосило лицо и не может говорить")
    pause(4.0)

    page.close()
    ctx.close()
    browser.close()
