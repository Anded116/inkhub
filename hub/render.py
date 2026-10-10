"""Отрисовка экрана Kindle: раскладка виджетов по ориентации и общие инструменты рисования.

Экран e-ink показывает чёрное и белое чётко, а полутона — плохо, поэтому всё рисуется
чёрным по белому; разные категории различаются штриховкой, а не оттенком.
"""

import struct
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

FONTS_DIR = Path(__file__).parent / "fonts"

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]

BLACK = 0
WHITE = 255

# Строки раскладки: (вес высоты строки, [(виджет, вес ширины), ...]).
# Недоступный виджет выпадает, его место занимают соседи.
LAYOUTS = {
    "landscape": [
        (48, [("clock", 58), ("weather", 42)]),
        (52, [("calendar", 68), ("bitcoin", 32)]),
    ],
    "portrait": [
        (27, [("clock", 1)]),
        (21, [("weather", 1)]),
        (37, [("calendar", 1)]),
        (15, [("bitcoin", 1)]),
    ],
}
STATUS_H = 34
GAP = 18


@dataclass
class Context:
    now: datetime
    battery: int | None = None
    charging: bool = False
    h24: bool = True
    night: bool = False
    # Кадр «на будущее» для работы Kindle без ПК: часы и строку состояния рисует сам Kindle,
    # а меняющиеся данные (курс) помечаются временем, на которое они верны.
    offline: bool = False
    data_at: datetime | None = None


_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "OpenSans-Bold.ttf" if bold else "OpenSans-Regular.ttf"
    key = (name, max(8, int(size)))
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(str(FONTS_DIR / name), key[1])
    return _font_cache[key]


@cache
def vmetrics(bold: bool) -> tuple[int, int, int]:
    """unitsPerEm, ascender и descender из таблиц head/hhea шрифта — так их видит fbink на Kindle."""
    data = (FONTS_DIR / ("OpenSans-Bold.ttf" if bold else "OpenSans-Regular.ttf")).read_bytes()
    tables = {}
    for i in range(struct.unpack(">H", data[4:6])[0]):
        tag, _, offset, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        tables[tag] = offset
    units = struct.unpack(">H", data[tables[b"head"] + 18:tables[b"head"] + 20])[0]
    asc, desc = struct.unpack(">hh", data[tables[b"hhea"] + 4:tables[b"hhea"] + 8])
    return units, asc, desc


@cache
def font_charset(bold: bool) -> frozenset[int]:
    """Символы, которые есть в шрифте (по таблице cmap): остальное на экране было бы квадратиками."""
    data = (FONTS_DIR / ("OpenSans-Bold.ttf" if bold else "OpenSans-Regular.ttf")).read_bytes()
    tables = {}
    for i in range(struct.unpack(">H", data[4:6])[0]):
        tag, _, offset, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        tables[tag] = offset
    cmap = tables[b"cmap"]
    chars: set[int] = set()
    for i in range(struct.unpack(">H", data[cmap + 2:cmap + 4])[0]):
        platform, encoding, offset = struct.unpack(">HHI", data[cmap + 4 + 8 * i:cmap + 12 + 8 * i])
        sub = cmap + offset
        if (platform, encoding) != (3, 1) or struct.unpack(">H", data[sub:sub + 2])[0] != 4:
            continue
        segs = struct.unpack(">H", data[sub + 6:sub + 8])[0] // 2
        ends = struct.unpack(f">{segs}H", data[sub + 14:sub + 14 + 2 * segs])
        starts = struct.unpack(f">{segs}H", data[sub + 16 + 2 * segs:sub + 16 + 4 * segs])
        for first, last in zip(starts, ends):
            chars.update(range(first, last + 1))
    chars.discard(0xFFFF)
    return frozenset(chars)


def clean_text(text: str, bold: bool = False) -> str:
    """Убирает эмодзи и прочие символы, которых нет в шрифте, и лишние пробелы."""
    charset = font_charset(bold)
    return " ".join("".join(ch for ch in text if ord(ch) in charset).split())


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt) -> tuple[int, int, int]:
    """Ширина, высота глифов и смещение их верха относительно точки рисования."""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=fnt)
    return right - left, bottom - top, top


def put(draw: ImageDraw.ImageDraw, x: int, top: int, text: str, fnt, fill=BLACK, anchor: str = "l") -> int:
    """Пишет строку так, что верх глифов ровно на top; anchor l/c/r по x. Возвращает низ глифов."""
    w, h, t = text_size(draw, text, fnt)
    left = draw.textbbox((0, 0), text, font=fnt)[0]
    if anchor == "c":
        x -= w // 2
    elif anchor == "r":
        x -= w
    draw.text((x - left, top - t), text, font=fnt, fill=fill)
    return top + h


def fit(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int, bold: bool = False, sample: str | None = None):
    """Самый крупный шрифт, в котором строка влезает в max_w × max_h."""
    size = max(10, int(max_h * 1.6))
    while size > 10:
        fnt = font(size, bold)
        w, _, _ = text_size(draw, text, fnt)
        _, h, _ = text_size(draw, sample or text, fnt)
        if w <= max_w and h <= max_h:
            return fnt
        size -= 2
    return font(10, bold)


def ellipsize(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> str:
    if draw.textlength(text, font=fnt) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=fnt) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int, max_lines: int) -> list[str]:
    """Перенос по словам; последняя строка при нехватке места обрезается многоточием."""
    lines, current = [], ""
    for word in text.split():
        probe = f"{current} {word}".strip()
        if draw.textlength(probe, font=fnt) <= max_w or not current:
            current = probe
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [ellipsize(draw, " ".join(lines[max_lines - 1:]), fnt, max_w)]
    return [ellipsize(draw, line, fnt, max_w) for line in lines]


PATTERNS = {
    "solid": lambda x, y: True,
    "hatch": lambda x, y: (x + y) % 4 == 0,
    "dense": lambda x, y: (x + y) % 3 == 0 or (x - y) % 3 == 0,
    "dots": lambda x, y: x % 3 == 0 and y % 3 == 0,
}


def fill_pattern(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], kind: str) -> None:
    """Заливка узором вместо серого: e-ink показывает её чётко."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0 + 1, y1 - y0 + 1
    if w <= 0 or h <= 0:
        return
    test = PATTERNS[kind]
    data = bytes(BLACK if test(x0 + x, y0 + y) else WHITE for y in range(h) for x in range(w))
    tile = Image.frombytes("L", (w, h), data)
    mask = Image.frombytes("L", (w, h), bytes(255 - v for v in data))
    draw._image.paste(tile, (x0, y0), mask)


def status_text(ctx: Context) -> str:
    parts = []
    if ctx.battery is not None:
        parts.append(f"Заряд {ctx.battery}%" + (", заряжается" if ctx.charging else ""))
    parts.append("связь с ПК")
    return " · ".join(parts)


def layout(width: int, height: int, widgets: dict) -> list[tuple[object, tuple[int, int, int, int]]]:
    """Раскладывает доступные виджеты по строкам; часы есть всегда."""
    rows = []
    for weight, cells in LAYOUTS["landscape" if width > height else "portrait"]:
        present = [(widgets[name], w) for name, w in cells if name in widgets and widgets[name].available()]
        if present:
            rows.append((weight, present))
    if len(rows) == 1:
        rows = [(1, rows[0][1])]
    total = sum(weight for weight, _ in rows)
    area_h = height - STATUS_H
    boxes, y = [], 0
    for i, (weight, cells) in enumerate(rows):
        row_h = area_h - y if i == len(rows) - 1 else area_h * weight // total
        cell_total = sum(w for _, w in cells)
        x = 0
        for j, (widget, w) in enumerate(cells):
            cell_w = width - x if j == len(cells) - 1 else width * w // cell_total
            boxes.append((widget, (x + GAP, y + GAP, x + cell_w - GAP, y + row_h - GAP)))
            x += cell_w
        y += row_h
    return boxes


def render(width: int, height: int, ctx: Context, widgets: dict) -> Image.Image:
    """Рисует логический кадр (уже в нужной ориентации) в градациях серого."""
    img = Image.new("L", (width, height), WHITE)
    draw = ImageDraw.Draw(img)
    placed = layout(width, height, widgets)
    for widget, box in placed:
        try:
            widget.draw(draw, box, ctx)
        except Exception as exc:  # один сломанный виджет не должен ломать экран
            put(draw, box[0], box[1], f"{widget.title}: ошибка", font(18, bold=True))
            put(draw, box[0], box[1] + 26, ellipsize(draw, str(exc), font(14), box[2] - box[0]), font(14))

    # Тонкие разделители между строками раскладки.
    tops = sorted({box[1] for _, box in placed})
    for top in tops[1:]:
        draw.line((GAP, top - GAP, width - GAP, top - GAP), fill=BLACK)
    for widget, box in placed:
        if box[0] > GAP:
            draw.line((box[0] - GAP, box[1] + 4, box[0] - GAP, box[3] - 4), fill=BLACK)

    draw.line((GAP, height - STATUS_H, width - GAP, height - STATUS_H), fill=BLACK)
    if not ctx.offline:
        put(draw, width // 2, height - STATUS_H + 8, status_text(ctx), font(19), anchor="c")

    # Виджет может потребовать внимания (встреча вот-вот начнётся): тогда весь экран инвертируется.
    alert = any(getattr(widget, "alert", lambda _ctx: False)(ctx) for widget, _ in placed)
    if ctx.night != alert:
        img = ImageOps.invert(img)
    img.info["inverted"] = ctx.night != alert
    return img
