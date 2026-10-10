"""День из Hour Mosaic одной полосой: продуктивное время чёрным, отвлечения штриховкой, нейтральное точками.

День Hour Mosaic начинается не в полночь, а в day_start_hour из его настроек:
date_key — дата момента (сейчас − day_start_hour), minute_of_day отсчитывается от начала этого дня.
"""

import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from render import BLACK, Context, fill_pattern, font, put, text_size

PATTERN = {"productive": "solid", "unproductive": "hatch", "neutral": "dots"}


def _minutes(total: int) -> str:
    return f"{total // 60} ч {total % 60:02d} мин"


class Widget:
    title = "Hour Mosaic"

    def __init__(self, config: dict):
        raw = config.get("db", r"%APPDATA%\com.clawbuster.hour-mosaic\hour-mosaic.db")
        self.db = Path(os.path.expandvars(raw))

    def available(self) -> bool:
        return self.db.exists()

    def _load(self, now) -> tuple[int, dict[int, str]]:
        with sqlite3.connect("file:" + self.db.as_posix() + "?mode=ro", uri=True, timeout=2) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'settings'").fetchone()
            try:
                day_start = int(json.loads(row[0]).get("day_start_hour", 4)) % 24
            except (TypeError, ValueError, AttributeError):
                day_start = 4
            key = (now - timedelta(hours=day_start)).strftime("%Y-%m-%d")
            rows = conn.execute("SELECT minute_of_day, category FROM minutes WHERE date_key = ?", (key,)).fetchall()
        return day_start, dict(rows)

    def draw(self, draw, box, ctx: Context) -> None:
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        day_start, minutes = self._load(ctx.now)
        shifted = ctx.now - timedelta(hours=day_start)
        now_minute = shifted.hour * 60 + shifted.minute

        # Итоги с образцами узоров вместо легенды.
        label = font(max(14, min(19, h // 5)))
        _, label_h, _ = text_size(draw, "Пд", label)
        x = x0
        for category, kind in PATTERN.items():
            total = _minutes(sum(1 for c in minutes.values() if c == category))
            swatch = (x, y0 + 1, x + label_h, y0 + label_h)
            fill_pattern(draw, swatch, kind)
            draw.rectangle(swatch, outline=BLACK)
            x += label_h + 6
            put(draw, x, y0, total, label)
            x += int(draw.textlength(total, font=label)) + 20

        # Полоса дня с часовыми отметками и текущим моментом.
        tick_font = font(13)
        _, tick_h, _ = text_size(draw, "0", tick_font)
        by0 = y0 + label_h + 10
        by1 = max(by0 + 10, y1 - tick_h - 10)
        scale = w / 1440
        start, current = 0, minutes.get(0)
        for m in range(1, 1441):
            category = minutes.get(m) if m < 1440 else None
            if m == 1440 or category != current:
                if current in PATTERN:
                    fill_pattern(draw, (int(x0 + start * scale), by0, int(x0 + m * scale) - 1, by1), PATTERN[current])
                start, current = m, category
        draw.rectangle((x0, by0, x1, by1), outline=BLACK)

        step = 3 if w > 600 else 4
        for k in range(25):
            tx = int(x0 + k * 60 * scale)
            draw.line((tx, by1, tx, by1 + (5 if k % step == 0 else 2)), fill=BLACK)
            if k % step == 0 and k < 24:
                put(draw, tx, by1 + 7, str((day_start + k) % 24), tick_font, anchor="l" if k == 0 else "c")
        nx = int(x0 + now_minute * scale)
        draw.line((nx, by0 - 5, nx, by1 + 5), fill=BLACK, width=3)
