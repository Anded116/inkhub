"""Ближайшие встречи из Google Календаря по секретной iCal-ссылке (без входа в аккаунт).

Ссылка: Google Календарь → настройки календаря → «Секретный адрес в формате iCal».
Хранится в hub/secrets.toml, в репозиторий не попадает.
"""

from datetime import datetime, timedelta

import icalendar
import recurring_ical_events

from feeds import Feed, get_text
from render import BLACK, WHITE, Context, clean_text, ellipsize, fill_pattern, font, put, text_size, wrap



WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def day_label(start: datetime, now: datetime) -> str:
    days = (start.date() - now.date()).days
    if days == 0:
        return ""
    return "завтра " if days == 1 else WEEKDAYS_SHORT[start.weekday()] + " "


def until(minutes: int) -> str:
    if minutes < 1:
        return "сейчас"
    hours, mins = divmod(minutes, 60)
    return f"через {hours} ч {mins:02d} мин" if hours else f"через {mins} мин"


def _declined(event, email: str) -> bool:
    if not email:
        return False
    attendees = event.get("ATTENDEE") or []
    if not isinstance(attendees, list):
        attendees = [attendees]
    return any(str(a).lower() == f"mailto:{email}" and a.params.get("PARTSTAT") == "DECLINED" for a in attendees)


def load_events(urls: list[str], email: str) -> list[dict]:
    local = datetime.now().astimezone().tzinfo
    now = datetime.now(local)
    events = []
    for url in urls:
        calendar = icalendar.Calendar.from_ical(get_text(url))
        for event in recurring_ical_events.of(calendar).between(now - timedelta(hours=12), now + timedelta(days=2)):
            if str(event.get("STATUS", "")).upper() == "CANCELLED" or _declined(event, email):
                continue
            start = event["DTSTART"].dt
            if not isinstance(start, datetime):
                continue
            end = event["DTEND"].dt if event.get("DTEND") else start + event.get("DURATION", icalendar.vDuration(timedelta(hours=1))).dt
            start = (start if start.tzinfo else start.replace(tzinfo=local)).astimezone(local)
            end = (end if end.tzinfo else end.replace(tzinfo=local)).astimezone(local)
            events.append({"title": str(event.get("SUMMARY", "Без названия")).strip(), "start": start, "end": end})
    return sorted(events, key=lambda e: e["start"])


class Widget:
    title = "Встречи"

    def __init__(self, config: dict):
        self.urls = [u for u in config.get("ical_urls", []) if u]
        email = config.get("my_email", "").lower()
        self.alert_before = timedelta(minutes=int(config.get("alert_before_min", 10)))
        self.alert_ongoing = bool(config.get("alert_while_ongoing", True))
        self.feed = Feed("календарь", lambda: load_events(self.urls, email),
                         every=int(config.get("refresh_min", 5)) * 60,
                         encode=lambda events: [{**e, "start": e["start"].isoformat(), "end": e["end"].isoformat()}
                                                for e in events],
                         decode=lambda events: [{**e, "start": datetime.fromisoformat(e["start"]),
                                                 "end": datetime.fromisoformat(e["end"])} for e in events],
                         ) if self.urls else None

    def available(self) -> bool:
        return bool(self.urls)

    def _state(self, now: datetime):
        """Главная встреча (идущая или ближайшая), идёт ли она, и остальные предстоящие."""
        events = self.feed.value or []
        ongoing = [e for e in events if e["start"] <= now < e["end"]]
        upcoming = [e for e in events if e["start"] > now]
        card = ongoing[0] if ongoing else (upcoming[0] if upcoming else None)
        return card, bool(ongoing), [e for e in upcoming if e is not card]

    def alert(self, ctx: Context) -> bool:
        """Встреча идёт или вот-вот начнётся: весь экран инвертируется."""
        if not self.feed or not self.feed.value:
            return False
        now = ctx.now.astimezone()
        card, is_ongoing, _ = self._state(now)
        if card is None:
            return False
        return (is_ongoing and self.alert_ongoing) or (not is_ongoing and card["start"] - now <= self.alert_before)

    def draw(self, draw, box, ctx: Context) -> None:
        x0, y0, x1, y1 = box
        w = x1 - x0
        events = self.feed.value
        if events is None:
            put(draw, x0, y0, "Календарь загружается…", font(26))
            return
        now = ctx.now.astimezone()
        card, is_ongoing, rest = self._state(now)
        y = self.timeline(draw, x0, y0, x1, now, events, card)

        if card is None:
            y = put(draw, x0, y, "Встреч нет", font(36, bold=True)) + 14
            put(draw, x0, y, "до конца завтрашнего дня", font(26))
            return

        span = f"{card['start']:%H:%M}–{card['end']:%H:%M}"
        if is_ongoing and ctx.offline:
            head = f"Идёт до {card['end']:%H:%M}"
        elif is_ongoing:
            left = int((card["end"] - now).total_seconds() // 60) + 1
            head = f"Идёт, осталось {left} мин"
        elif card["start"].date() == now.date() and ctx.offline:
            head = f"Сегодня, {span}"
        elif card["start"].date() == now.date():
            head = f"{until(int((card['start'] - now).total_seconds() // 60)).capitalize()} · {span}"
        else:
            head = f"{day_label(card['start'], now).strip().capitalize()}, {span}"

        # Ближайшая встреча: время крупно, название целиком (до двух строк).
        alert = self.alert(ctx)
        pad = 14 if alert else 0
        head_font, title_font, row_font = font(28, bold=True), font(36, bold=True), font(26)
        _, head_h, _ = text_size(draw, "Пд", head_font)
        _, title_h, _ = text_size(draw, "Пд", title_font)
        _, row_h, _ = text_size(draw, "Пд", row_font)
        # Название целиком важнее списка следующих встреч: до трёх строк, если они помещаются.
        title = clean_text(card["title"], bold=True)
        for max_lines in (3, 2, 1):
            lines = wrap(draw, title, title_font, w - 2 * pad, max_lines)
            card_h = pad + head_h + 14 + len(lines) * (title_h + 12) - 12 + pad
            if y + card_h <= y1:
                break
        fg = WHITE if alert else BLACK
        if alert:
            draw.rounded_rectangle((x0, y, x1, y + card_h), radius=10, fill=BLACK)
        put(draw, x0 + pad, y + pad, head, head_font, fill=fg)
        ty = y + pad + head_h + 14
        for line in lines:
            put(draw, x0 + pad, ty, line, title_font, fill=fg)
            ty += title_h + 12
        y += card_h + 20

        for event in rest:
            if y + row_h > y1:
                break
            line = f"{day_label(event['start'], now)}{event['start']:%H:%M}  {clean_text(event['title'])}"
            put(draw, x0, y, ellipsize(draw, line, row_font, w), row_font)
            y += row_h + 14

    @staticmethod
    def timeline(draw, x0: int, y: int, x1: int, now: datetime, events: list, card) -> int:
        """Шкала ближайших часов, как в Google Календаре: ближайшая встреча чёрная, остальные в штриховку.

        Возвращает, где продолжать рисовать. Если в эти часы встреч нет, шкалу не рисует.
        """
        hours = 8 if x1 - x0 >= 480 else 6
        start = now.replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=hours)
        visible = [e for e in events if e["end"] > start and e["start"] < end]
        if not visible:
            return y
        label = font(22)
        _, label_h, _ = text_size(draw, "0", label)
        bar0 = y + label_h + 8
        bar1 = bar0 + 30
        scale = (x1 - x0) / (hours * 3600)

        def at(moment: datetime) -> int:
            return round(x0 + (min(max(moment, start), end) - start).total_seconds() * scale)

        for k in range(hours + 1):
            hx = round(x0 + k * 3600 * scale)
            draw.line((hx, bar1, hx, bar1 + 6), fill=BLACK)
            if k < hours:
                put(draw, hx + 4, y, f"{(start + timedelta(hours=k)).hour}", label)
        draw.rectangle((x0, bar0, x1, bar1), outline=BLACK)
        for event in visible:
            bx0, bx1 = at(event["start"]), max(at(event["start"]) + 3, at(event["end"]))
            if event is card:
                draw.rectangle((bx0, bar0, bx1, bar1), fill=BLACK)
            else:
                fill_pattern(draw, (bx0, bar0, bx1, bar1), "hatch")
                draw.rectangle((bx0, bar0, bx1, bar1), outline=BLACK, width=2)
        nx = at(now)
        draw.polygon(((nx - 9, bar1 + 12), (nx + 9, bar1 + 12), (nx, bar1 + 1)), fill=BLACK)
        draw.line((nx, bar0, nx, bar1), fill=WHITE, width=5)
        draw.line((nx, bar0, nx, bar1), fill=BLACK, width=2)
        return bar1 + 30
