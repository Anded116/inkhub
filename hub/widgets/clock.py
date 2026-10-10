"""Крупное время и дата."""

import math

from render import MONTHS, WEEKDAYS, Context, fit, put, text_size, vmetrics


class Widget:
    title = "Часы"

    def __init__(self, config: dict):
        # Где Kindle без ПК рисует цифры сам: заполняется при отрисовке офлайн-кадра.
        self.slot: dict | None = None

    def available(self) -> bool:
        return True

    def draw(self, draw, box, ctx: Context) -> None:
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        now = ctx.now
        hour = now.hour if ctx.h24 else (now.hour % 12 or 12)
        time_text = f"{hour:02d}:{now.minute:02d}" if ctx.h24 else f"{hour}:{now.minute:02d}"
        date_text = f"{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}"

        time_font = fit(draw, "00:00", w, int(h * 0.56), bold=True)
        date_font = fit(draw, date_text, w, max(14, min(46, int(h * 0.17))), sample="Пд")
        _, time_h, _ = text_size(draw, "00:00", time_font)
        _, date_h, _ = text_size(draw, "Пд", date_font)
        gap = max(10, h // 14)
        top = y0 + (h - time_h - gap - date_h) // 2
        # Строка fbink начинается выше цифр; цифры опускаются, чтобы она не вылезла за верх экрана.
        top += max(0, 2 - self.fbink_slot(draw, box, top, time_font)["top"])
        cx = x0 + w // 2
        if ctx.offline:
            self.slot = self.fbink_slot(draw, box, top, time_font)
        else:
            put(draw, cx, top, time_text, time_font, anchor="c")
        put(draw, cx, top + time_h + gap, date_text, date_font, anchor="c")

    @staticmethod
    def fbink_slot(draw, box, top: int, time_font) -> dict:
        """Параметры fbink, при которых цифры на Kindle встанут туда же, куда их рисует ПК.

        fbink задаёт размер как высоту строки (ascender − descender), а не em, и ставит
        базовую линию на top + ceil(scale × ascender).
        """
        units, asc, desc = vmetrics(True)
        _, glyph_h, offset = text_size(draw, "00:00", time_font)
        baseline = top - offset + time_font.getmetrics()[0]
        px = round(time_font.size * (asc - desc) / units)
        fb_top = baseline - math.ceil(px / (asc - desc) * asc)
        return {"left": box[0], "right": box[2], "clear_top": top - 8, "clear_h": glyph_h + 16,
                "top": fb_top, "px": px}
