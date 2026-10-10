"""Курс биткойна от CoinGecko (без ключа): цена, изменение и график за неделю."""

from feeds import Feed, get_json
from render import BLACK, Context, ellipsize, fit, font, put, text_size

SYMBOLS = {"usd": "$", "eur": "€", "rub": "₽"}


def money(value: float, currency: str) -> str:
    digits = f"{round(value):,}".replace(",", " ")
    symbol = SYMBOLS.get(currency, currency.upper() + " ")
    return f"{digits} {symbol}" if currency == "rub" else f"{symbol}{digits}"


def change(now: float, then: float) -> str:
    pct = (now - then) / then * 100
    sign = "+" if pct >= 0 else "−"
    return f"{sign}{abs(pct):.1f}%".replace(".", ",")


class Widget:
    title = "Биткойн"

    def __init__(self, config: dict):
        self.currency = config.get("currency", "usd").lower()
        url = f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency={self.currency}&days=7"
        self.feed = Feed("биткойн", lambda: get_json(url)["prices"], every=int(config.get("refresh_min", 5)) * 60, retry=120)

    def available(self) -> bool:
        return True

    def draw(self, draw, box, ctx: Context) -> None:
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        prices = self.feed.value
        if not prices:
            put(draw, x0, y0, "Курс загружается…", font(20))
            return
        values = [p for _, p in prices]
        last_t, last = prices[-1]
        day_ago = next((p for t, p in prices if t >= last_t - 24 * 3600 * 1000), values[0])

        wide = w > h * 2.2
        text_w = int(w * 0.42) if wide else w
        label = font(22)
        _, label_h, _ = text_size(draw, "Пд", label)
        if wide:
            price = "BTC " + money(last, self.currency)
            price_top = y0
        else:
            put(draw, x0, y0, "BTC", font(22, bold=True))
            price = money(last, self.currency)
            price_top = y0 + label_h + 10
        price_font = fit(draw, price, text_w, int(min(h * 0.28, 48)), bold=True, sample="0")
        price_bottom = put(draw, x0, price_top, price, price_font)
        stats_y = price_bottom + 10
        stats = f"{change(last, day_ago)} за сутки"
        if ctx.offline and ctx.data_at:
            stats += f", на {ctx.data_at:%H:%M}"
        put(draw, x0, stats_y, ellipsize(draw, stats, label, text_w), label)

        # График за неделю: чёрная линия, отмечены максимум и минимум.
        if wide:
            gx0, gy0, gx1, gy1 = x0 + text_w + 16, y0 + 4, x1, y1 - 4
        else:
            gx0, gy0, gx1, gy1 = x0, stats_y + label_h + 14, x1, y1
        if gy1 - gy0 < 30:
            return
        small = font(14)
        _, small_h, _ = text_size(draw, "0", small)
        gy0 += small_h + 2
        gy1 -= small_h + 2
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1
        n = len(values)
        pts = [(gx0 + (gx1 - gx0) * i / (n - 1), gy1 - (gy1 - gy0) * (v - lo) / span) for i, v in enumerate(values)]
        draw.line(pts, fill=BLACK, width=2, joint="curve")
        hi_i, lo_i = values.index(hi), values.index(lo)
        for i, value, above in ((hi_i, hi, True), (lo_i, lo, False)):
            px, py = pts[i]
            draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=BLACK)
            text = money(value, self.currency)
            tw, _, _ = text_size(draw, text, small)
            tx = min(max(px, gx0 + tw / 2), gx1 - tw / 2)
            put(draw, int(tx), int(py - small_h - 6 if above else py + 6), text, small, anchor="c")
        lx, ly = pts[-1]
        draw.ellipse((lx - 4, ly - 4, lx + 4, ly + 4), outline=BLACK, width=2)
