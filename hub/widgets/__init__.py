"""Виджеты экрана. Каждый — класс Widget с title, available() и draw(draw, box, ctx).

Новый виджет: модуль в этой папке, строка в WIDGETS и место в LAYOUTS (render.py).
Включение — config.toml, ключ screen.widgets; секреты виджета — hub/secrets.toml.
"""

from . import bitcoin, calendar_events, clock, hour_mosaic, weather

WIDGETS = {
    "clock": clock.Widget,
    "weather": weather.Widget,
    "calendar": calendar_events.Widget,
    "bitcoin": bitcoin.Widget,
    "hour_mosaic": hour_mosaic.Widget,
}


def load(config: dict, secrets: dict) -> dict:
    result = {"clock": clock.Widget({})}
    for name in config.get("screen", {}).get("widgets", []):
        cls = WIDGETS.get(name)
        if cls is None:
            print(f"Неизвестный виджет: {name}")
            continue
        result[name] = cls({**config.get(name, {}), **secrets.get(name, {})})
    return result
