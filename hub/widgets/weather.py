"""Погода от Open-Meteo (без ключа): сейчас, день и ближайшие часы."""

import math
from datetime import datetime, timedelta
from urllib.parse import urlencode

from feeds import Feed, get_json
from render import BLACK, WHITE, Context, ellipsize, fit, font, put, text_size

WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# Коды погоды WMO → (значок, описание).
CODES = {
    0: ("clear", "ясно"), 1: ("partly", "малооблачно"), 2: ("partly", "переменная облачность"),
    3: ("cloudy", "пасмурно"), 45: ("fog", "туман"), 48: ("fog", "изморозь"),
    51: ("drizzle", "морось"), 53: ("drizzle", "морось"), 55: ("drizzle", "сильная морось"),
    56: ("drizzle", "ледяная морось"), 57: ("drizzle", "ледяная морось"),
    61: ("rain", "небольшой дождь"), 63: ("rain", "дождь"), 65: ("rain", "сильный дождь"),
    66: ("rain", "ледяной дождь"), 67: ("rain", "ледяной дождь"),
    71: ("snow", "небольшой снег"), 73: ("snow", "снег"), 75: ("snow", "сильный снег"), 77: ("snow", "снежная крупа"),
    80: ("rain", "ливень"), 81: ("rain", "ливень"), 82: ("rain", "сильный ливень"),
    85: ("snow", "снегопад"), 86: ("snow", "сильный снегопад"),
    95: ("storm", "гроза"), 96: ("storm", "гроза с градом"), 99: ("storm", "гроза с градом"),
}


def temp(value: float) -> str:
    t = round(value)
    return f"+{t}°" if t > 0 else ("0°" if t == 0 else f"−{-t}°")


def _sun(draw, cx, cy, r, width):
    draw.ellipse((cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5), outline=BLACK, width=width)
    for k in range(8):
        a = k * math.pi / 4
        draw.line((cx + math.cos(a) * r * 0.68, cy + math.sin(a) * r * 0.68,
                   cx + math.cos(a) * r * 0.95, cy + math.sin(a) * r * 0.95), fill=BLACK, width=width)


def _moon(draw, cx, cy, r, width):
    draw.ellipse((cx - r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62), fill=BLACK)
    draw.ellipse((cx - r * 0.3, cy - r * 0.85, cx + r * 0.8, cy + r * 0.25), fill=WHITE)


def _cloud(draw, cx, cy, r, width, fill=WHITE):
    parts = [(-0.45, 0.05, 0.38), (0.0, -0.18, 0.5), (0.45, 0.08, 0.36)]
    base = (cx - r * 0.85, cy + r * 0.02, cx + r * 0.85, cy + r * 0.45)
    for dx, dy, rr in parts:
        draw.ellipse((cx + (dx - rr) * r, cy + (dy - rr) * r, cx + (dx + rr) * r, cy + (dy + rr) * r),
                     fill=fill, outline=BLACK, width=width)
    draw.rounded_rectangle(base, radius=r * 0.2, fill=fill, outline=BLACK, width=width)
    # Внутренние дуги кругов закрываем заливкой, остаётся только внешний контур.
    for dx, dy, rr in parts:
        inner = rr - width / r
        draw.ellipse((cx + (dx - inner) * r, cy + (dy - inner) * r, cx + (dx + inner) * r, cy + (dy + inner) * r), fill=fill)
    draw.rectangle((base[0] + width, base[1], base[2] - width, base[3] - width), fill=fill)


def icon(draw, kind: str, cx: int, cy: int, size: int, day: bool = True) -> None:
    r = size / 2
    width = max(2, size // 22)
    if kind == "clear":
        (_sun if day else _moon)(draw, cx, cy, r, width)
        return
    if kind == "partly":
        (_sun if day else _moon)(draw, cx + r * 0.3, cy - r * 0.3, r * 0.7, width)
        _cloud(draw, cx - r * 0.12, cy + r * 0.12, r * 0.8, width)
        return
    cloud_cy = cy - r * 0.2 if kind in ("rain", "drizzle", "snow", "storm") else cy
    _cloud(draw, cx, cloud_cy, r * 0.95, width)
    below = cloud_cy + r * 0.5
    if kind == "rain" or kind == "drizzle":
        count = 3 if kind == "rain" else 2
        for k in range(count):
            x = cx + (k - (count - 1) / 2) * r * 0.45
            draw.line((x, below, x - r * 0.15, below + r * (0.45 if kind == "rain" else 0.25)), fill=BLACK, width=width)
    elif kind == "snow":
        for k in range(3):
            x = cx + (k - 1) * r * 0.45
            y = below + r * 0.25
            s = r * 0.12
            draw.line((x - s, y, x + s, y), fill=BLACK, width=width)
            draw.line((x, y - s, x, y + s), fill=BLACK, width=width)
    elif kind == "storm":
        pts = [(cx + r * 0.05, below), (cx - r * 0.2, below + r * 0.3), (cx + r * 0.05, below + r * 0.3), (cx - r * 0.15, below + r * 0.6)]
        draw.line(pts, fill=BLACK, width=width)
    elif kind == "fog":
        for k in range(3):
            y = cy + r * 0.6 + k * r * 0.18
            draw.line((cx - r * 0.8, y, cx + r * 0.8, y), fill=BLACK, width=width)


class Widget:
    title = "Погода"

    def __init__(self, config: dict):
        query = urlencode({
            "latitude": config.get("latitude", 55.75),
            "longitude": config.get("longitude", 37.62),
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day",
            "hourly": "temperature_2m,apparent_temperature,wind_speed_10m,weather_code,precipitation_probability,is_day",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 3,
            "wind_speed_unit": "ms",
        })
        url = "https://api.open-meteo.com/v1/forecast?" + query
        self.feed = Feed("погода", lambda: get_json(url), every=int(config.get("refresh_min", 15)) * 60)

    def available(self) -> bool:
        return True

    @staticmethod
    def current_at(data: dict, now: datetime) -> dict:
        """Погода «сейчас»: свежие данные или, для кадров на будущее, почасовой прогноз на этот час."""
        cur = data["current"]
        if now - datetime.fromisoformat(cur["time"]) < timedelta(minutes=45):
            return cur
        hourly = data["hourly"]
        key = now.strftime("%Y-%m-%dT%H:00")
        if key not in hourly["time"]:
            return cur
        i = hourly["time"].index(key)
        return {name: hourly[name][i] for name in
                ("temperature_2m", "apparent_temperature", "wind_speed_10m", "weather_code", "is_day")}

    def draw(self, draw, box, ctx: Context) -> None:
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        data = self.feed.value
        if not data:
            put(draw, x0, y0, "Погода загружается…", font(26))
            return
        daily = data["daily"]
        cur = self.current_at(data, ctx.now)
        kind, _ = CODES.get(cur["weather_code"], ("cloudy", ""))
        wide = w > h * 2.2

        # Сейчас: значок и крупная температура.
        cur_w = int(w * 0.42) if wide else w
        icon_size = int(min(h * (0.62 if wide else 0.36), cur_w * 0.3, 96))
        icon_top = y0 + ((h - icon_size) // 2 if wide else 0)
        icon(draw, kind, x0 + icon_size // 2 + 2, icon_top + icon_size // 2, icon_size, bool(cur.get("is_day", 1)))
        big = fit(draw, "+88°", cur_w - icon_size - 18, int(icon_size * 0.8), bold=True, sample="+88°")
        _, big_h, _ = text_size(draw, "+88°", big)
        put(draw, x0 + icon_size + 18, icon_top + (icon_size - big_h) // 2, temp(cur["temperature_2m"]), big)

        # Ближайшие дни: название, значок, максимум крупно и минимум под ним.
        dx0, dy0 = (x0 + cur_w + 12, y0) if wide else (x0, icon_top + icon_size + 22)
        today = ctx.now.date().isoformat()
        first = daily["time"].index(today) if today in daily["time"] else 0
        days = list(range(first, min(first + 3, len(daily["time"]))))
        if not days or y1 - dy0 < 80:
            return
        name_font = font(22 if wide else 24)
        max_font, min_font = font(26, bold=True), font(22)
        _, name_h, _ = text_size(draw, "Пд", name_font)
        _, max_h, _ = text_size(draw, "+8°", max_font)
        _, min_h, _ = text_size(draw, "+8°", min_font)
        rows = y1 - dy0 - name_h - max_h - min_h - 18
        ic = max(24, min(52, rows - 6))
        col_w = (x1 - dx0) / len(days)
        for k, i in enumerate(days):
            cx = int(dx0 + col_w * (k + 0.5))
            day = datetime.fromisoformat(daily["time"][i])
            name = "Сегодня" if k == 0 else "Завтра" if k == 1 else WEEKDAYS_SHORT[day.weekday()]
            y = dy0
            put(draw, cx, y, name, name_font, anchor="c")
            y += name_h + 8
            dk, _ = CODES.get((daily.get("weather_code") or [cur["weather_code"]] * len(daily["time"]))[i], ("cloudy", ""))
            icon(draw, dk, cx, y + ic // 2, ic, True)
            y += ic + 8
            put(draw, cx, y, temp(daily["temperature_2m_max"][i]), max_font, anchor="c")
            y += max_h + 6
            put(draw, cx, y, temp(daily["temperature_2m_min"][i]), min_font, anchor="c")
