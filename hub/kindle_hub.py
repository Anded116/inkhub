"""Kindle Hub: рисует экран для Kindle Clock и отдаёт его по локальной сети.

Kindle раз в минуту спрашивает /api/frame, сервер рисует кадр, сравнивает с тем,
что уже на экране у Kindle, и отдаёт только изменившиеся строки видеопамяти.
Запуск без окна: python kindle_hub.py (превью: http://localhost:8765/); с окном — app.py.
"""

import gzip
import hashlib
import io
import json
import operator
import re
import socket
import sys
import threading
import time
import tomllib
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

import feeds
import widgets
from render import STATUS_H, Context, render

ROOT = Path(__file__).parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
SECRETS_FILE = ROOT / "secrets.toml"
SECRETS = tomllib.loads(SECRETS_FILE.read_text(encoding="utf-8")) if SECRETS_FILE.exists() else {}
WIDGETS = widgets.load(CONFIG, SECRETS)
KEEP_FRAMES = 40

_lock = threading.Lock()
_seq = 0
_frames: dict[int, tuple[tuple, bytes]] = {}
_bands: dict[int, bytes] = {}
_last = {"image": None, "client": None, "at": None}

# Что известно о Kindle: для окна приложения и для понимания, жива ли связь.
KINDLE_PATHS = ("/api/ping", "/api/frame", "/api/time", "/api/px/", "/api/bye", "/api/pack")
_kindle = {"ip": None, "pc_ip": None, "last_seen": None, "bye_at": None, "essid": None, "orient": None,
           "battery": None, "charging": False, "screen": None, "minutes": set()}
SHOW_WINDOW = threading.Event()

_SHIFT4 = bytes(((v & 15) << 4) for v in range(256))


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(ROOT / "hub.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


feeds.log = log


KINDLE_STATE = ROOT / "cache" / "kindle.json"
KINDLE_SAVED = ("ip", "essid", "orient", "battery", "charging", "screen")


def save_kindle_state() -> None:
    """Последнее, что Kindle сообщал о себе: после перезапуска окно сразу показывает Wi-Fi, экран, заряд."""
    try:
        KINDLE_STATE.parent.mkdir(exist_ok=True)
        KINDLE_STATE.write_text(json.dumps({key: _kindle[key] for key in KINDLE_SAVED}, ensure_ascii=False),
                                encoding="utf-8")
    except OSError:
        pass


def note_kindle(ip: str, pc_ip: str, path: str, q: dict) -> None:
    now = datetime.now()
    with _lock:
        _kindle.update(ip=ip, pc_ip=pc_ip, last_seen=now)
        _kindle["minutes"].add(int(now.timestamp() // 60))
        if path == "/api/bye":
            _kindle["bye_at"] = now
        elif path == "/api/ping":
            _kindle["bye_at"] = None
        if path == "/api/frame":
            battery = q.get("batt", [""])[0]
            _kindle.update(battery=int(battery) if battery.isdigit() else None,
                           charging=q.get("chg", ["0"])[0] == "1",
                           essid=q.get("essid", [None])[0] or _kindle["essid"],
                           orient=q.get("orient", [None])[0] or _kindle["orient"],
                           screen=(q.get("w", ["?"])[0], q.get("h", ["?"])[0], q.get("rot", ["0"])[0]))
            save_kindle_state()


def load_history() -> None:
    """Восстанавливает по hub.log, в какие минуты последних суток Kindle выходил на связь."""
    try:
        saved = json.loads(KINDLE_STATE.read_text(encoding="utf-8"))
        _kindle.update({key: saved.get(key) for key in KINDLE_SAVED if key in saved})
        if _kindle["screen"]:
            _kindle["screen"] = tuple(_kindle["screen"])
    except (OSError, ValueError):
        pass
    path = ROOT / "hub.log"
    if not path.exists():
        return
    since = datetime.now() - timedelta(days=1)
    pattern = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\d+\.\d+\.\d+\.\d+) (/api/\S+)")
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pattern.match(line)
            if not m or m.group(2).startswith("127.") or not m.group(3).startswith(KINDLE_PATHS):
                continue
            at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            if at < since:
                continue
            _kindle["minutes"].add(int(at.timestamp() // 60))
            _kindle.update(ip=m.group(2), last_seen=at)
            if m.group(3) == "/api/bye":
                _kindle["bye_at"] = at
            elif m.group(3) == "/api/ping":
                _kindle["bye_at"] = None


def route_source(ip: str) -> str | None:
    """С какого своего адреса ПК отвечает Kindle. Чужой адрес = маршрут перехватил VPN."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((ip, 9))
            return sock.getsockname()[0]
    except OSError:
        return None


def kindle_status() -> dict:
    with _lock:
        state = dict(_kindle)
        state["minutes"] = set(_kindle["minutes"])
    cutoff = int(time.time() // 60) - 24 * 60
    state["minutes"] = {m for m in state["minutes"] if m >= cutoff}
    return state


def encode(img: Image.Image, bpp: int, line_length: int, white: int, nib: str) -> bytes:
    """Переводит картинку L в байты видеопамяти Kindle построчно."""
    w, h = img.size
    if bpp == 8:
        data = img.tobytes()
        if white == 0:
            data = data.translate(bytes(255 - v for v in range(256)))
        row_bytes = w
        pad_byte = white
    else:
        levels = bytes((v * 15 + 127) // 255 for v in range(256))
        if white == 0:
            levels = bytes(15 - v for v in levels)
        q = img.tobytes().translate(levels)
        first, second = q[0::2], q[1::2]
        if nib == "lo":
            first, second = second, first
        data = bytes(map(operator.or_, first.translate(_SHIFT4), second))
        row_bytes = w // 2
        pad_byte = (white << 4) | white
    if line_length == row_bytes:
        return data
    pad = bytes([pad_byte]) * (line_length - row_bytes)
    return b"".join(data[r * row_bytes:(r + 1) * row_bytes] + pad for r in range(h))


def changed_rows(old: bytes, new: bytes, line_length: int) -> tuple[int, int]:
    rows = len(new) // line_length
    top = next((r for r in range(rows) if old[r * line_length:(r + 1) * line_length] != new[r * line_length:(r + 1) * line_length]), None)
    if top is None:
        return 0, 0
    bottom = next(r for r in range(rows - 1, -1, -1) if old[r * line_length:(r + 1) * line_length] != new[r * line_length:(r + 1) * line_length])
    return top, bottom - top + 1


def make_frame(q: dict) -> str:
    global _seq

    def num(key, default, lo, hi):
        try:
            value = int(q.get(key, [default])[0])
        except ValueError:
            value = default
        return max(lo, min(hi, value))

    w, h = num("w", 600, 100, 2000), num("h", 800, 100, 2000)
    bpp = 4 if num("bpp", 4, 4, 8) == 4 else 8
    line_length = num("ll", w * bpp // 8, w * bpp // 8, 4096)
    white = num("white", 0, 0, 255)
    nib = "lo" if q.get("nib", ["hi"])[0] == "lo" else "hi"
    rot = num("rot", 0, 0, 270)
    have = num("have", 0, 0, 1 << 30)
    battery = q.get("batt", [""])[0]
    ctx = Context(
        now=datetime.now(),
        battery=int(battery) if battery.isdigit() else None,
        charging=q.get("chg", ["0"])[0] == "1",
        h24=q.get("h24", ["1"])[0] != "0",
        night=q.get("night", ["0"])[0] == "1",
    )

    logical = (h, w) if rot in (90, 270) else (w, h)
    img = render(logical[0], logical[1], ctx, WIDGETS)
    shown = img.rotate(rot, expand=True) if rot else img
    data = encode(shown, bpp, line_length, white, nib)
    fmt = (w, h, bpp, line_length, white, nib, rot)

    with _lock:
        _last.update(image=img, client=q.get("client", ["?"])[0], at=ctx.now)
        previous = _frames.get(have)
        if previous and previous[0] == fmt:
            top, rows = changed_rows(previous[1], data, line_length)
            if rows == 0:
                return f"seq={have}\ntop=0\nrows=0\n"
        else:
            top, rows = 0, h
        _seq += 1
        seq = _seq
        _frames[seq] = (fmt, data)
        _bands[seq] = gzip.compress(data[top * line_length:(top + rows) * line_length], 6)
        for old in sorted(_frames)[:-KEEP_FRAMES]:
            _frames.pop(old, None)
            _bands.pop(old, None)
    return f"seq={seq}\ntop={top}\nrows={rows}\nflash={1 if rows == h else 0}\n"


# ---------- запас кадров на время без связи ----------

PACK_HOURS = 24
PACK_TTL = 600
_packs: dict[tuple, dict] = {}
_pack_frames: dict[str, bytes] = {}
_pack_building: set[tuple] = set()


def change_points(start: datetime, end: datetime) -> list[datetime]:
    """Моменты, когда экран без часов меняется: каждый час (погода, смена дня) и встречи."""
    points = {start, end}
    hour = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while hour < end:
        points.add(hour)
        hour += timedelta(hours=1)
    calendar = WIDGETS.get("calendar")
    events = calendar.feed.value if calendar is not None and calendar.feed is not None else None
    if events:
        before = calendar.alert_before
        for event in events:
            for moment in (event["start"] - before, event["start"], event["end"]):
                moment = moment.astimezone().replace(tzinfo=None, second=0, microsecond=0)
                if start < moment < end:
                    points.add(moment)
    return sorted(points)


def build_pack(key: tuple) -> None:
    w, h, bpp, line_length, white, nib, h24, night = key
    try:
        built = datetime.now().replace(second=0, microsecond=0)
        points = change_points(built, built + timedelta(hours=PACK_HOURS))
        lines, frames, slot = [f"fmt {w} {h} {bpp} {line_length} {white} {nib}"], {}, None
        for a, b in zip(points, points[1:]):
            ctx = Context(now=a, h24=h24, night=night, offline=True, data_at=built)
            img = render(w, h, ctx, WIDGETS)
            slot = slot or WIDGETS["clock"].slot
            band = gzip.compress(encode(img, bpp, line_length, white, nib), 6)
            digest = hashlib.sha1(band).hexdigest()[:12]
            frames[digest] = band
            lines.append(f"seg {int(a.timestamp())} {int(b.timestamp())} {digest} {int(img.info['inverted'])}")
        lines.insert(1, f"clock {slot['left']} {slot['right']} {slot['clear_top']} {slot['clear_h']} {slot['top']} {slot['px']}")
        lines.insert(2, f"status {h - STATUS_H + 1} {STATUS_H - 1}")
        with _lock:
            _packs[key] = {"built": built, "manifest": "\n".join(lines) + "\n", "frames": set(frames)}
            _pack_frames.update(frames)
            keep = set().union(*(p["frames"] for p in _packs.values()))
            for digest in [d for d in _pack_frames if d not in keep]:
                del _pack_frames[digest]
        log(f"Запас кадров на {PACK_HOURS} ч: {len(points) - 1} отрезков, {len(frames)} разных кадров")
    except Exception as exc:
        log(f"Запас кадров не собран: {exc!r}")
    finally:
        with _lock:
            _pack_building.discard(key)


def pack_manifest(q: dict) -> str:
    def num(name, default):
        try:
            return int(q.get(name, [default])[0])
        except ValueError:
            return int(default)

    if num("rot", 0) != 0:
        return "none\n"
    bpp = 8 if num("bpp", 4) == 8 else 4
    w, h = max(100, min(2000, num("w", 600))), max(100, min(2000, num("h", 800)))
    key = (w, h, bpp, max(w * bpp // 8, min(4096, num("ll", w * bpp // 8))), max(0, min(255, num("white", 0))),
           "lo" if q.get("nib", ["hi"])[0] == "lo" else "hi", q.get("h24", ["1"])[0] != "0",
           q.get("night", ["0"])[0] == "1")
    with _lock:
        pack = _packs.get(key)
        fresh = pack is not None and (datetime.now() - pack["built"]).total_seconds() < PACK_TTL
        if not fresh and key not in _pack_building:
            _pack_building.add(key)
            threading.Thread(target=build_pack, args=(key,), name="pack", daemon=True).start()
    return pack["manifest"] if pack else "building\n"


def time_text() -> str:
    now = time.time()
    offset = int(datetime.now().astimezone().utcoffset().total_seconds())
    return f"utc={int(now + 0.5)}\nutcoff={offset}\n"


def preview_png(orient: str) -> bytes:
    with _lock:
        img = _last["image"]
    if orient in ("portrait", "landscape") or img is None:
        w, h = (800, 600) if orient == "landscape" else (600, 800)
        img = render(w, h, Context(now=datetime.now(), battery=None), WIDGETS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Kindle Hub</title>
<style>body{{font:15px system-ui,sans-serif;margin:24px;background:#f4f4f2;color:#222}}
img{{border:10px solid #ddd;border-radius:6px;background:#fff;max-width:100%;image-rendering:pixelated}}
.row{{display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start}}h1{{font-size:20px}}</style></head>
<body><h1>Kindle Hub</h1><p>{status}</p>
<div class="row"><figure><img src="/preview.png?orient=last&t={t}"><figcaption>Сейчас на Kindle</figcaption></figure>
<figure><img src="/preview.png?orient=portrait&t={t}"><figcaption>Вертикально</figcaption></figure>
<figure><img src="/preview.png?orient=landscape&t={t}"><figcaption>Горизонтально</figcaption></figure></div>
<script>setTimeout(()=>location.reload(),30000)</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        q = parse_qs(url.query)
        local = self.client_address[0].startswith("127.")
        if not local and url.path.startswith(KINDLE_PATHS):
            note_kindle(self.client_address[0], self.connection.getsockname()[0], url.path, q)
        try:
            if url.path in ("/api/ping", "/api/bye"):
                self._send(b"ok\n", "text/plain")
            elif url.path == "/api/show" and local:
                SHOW_WINDOW.set()
                self._send(b"ok\n", "text/plain")
            elif url.path == "/api/time":
                self._send(time_text().encode(), "text/plain")
            elif url.path == "/api/frame":
                q.setdefault("client", [self.client_address[0]])
                self._send(make_frame(q).encode(), "text/plain")
            elif url.path == "/api/pack":
                self._send(pack_manifest(q).encode(), "text/plain")
            elif url.path.startswith("/api/packframe/"):
                with _lock:
                    band = _pack_frames.get(url.path.rsplit("/", 1)[1])
                if band is None:
                    self._send(b"gone\n", "text/plain", 404)
                else:
                    self._send(band, "application/octet-stream")
            elif url.path.startswith("/api/px/"):
                seq = int(url.path.rsplit("/", 1)[1])
                with _lock:
                    band = _bands.get(seq)
                if band is None:
                    self._send(b"gone\n", "text/plain", 404)
                else:
                    self._send(band, "application/octet-stream")
            elif url.path == "/preview.png":
                self._send(preview_png(q.get("orient", ["last"])[0]), "image/png")
            elif url.path == "/":
                with _lock:
                    client, at = _last["client"], _last["at"]
                status = f"Kindle {client} заходил в {at:%H:%M:%S}" if at else "Kindle ещё не подключался"
                self._send(PAGE.format(status=status, t=int(time.time())).encode(), "text/html; charset=utf-8")
            else:
                self._send(b"not found\n", "text/plain", 404)
        except Exception as exc:
            log(f"Ошибка {url.path}: {exc!r}")
            self._send(b"error\n", "text/plain", 500)

    def log_message(self, fmt, *args) -> None:
        path = self.path.split("?")[0]
        if not self.client_address[0].startswith("127.") and path.startswith(KINDLE_PATHS) and not path.startswith("/api/px/"):
            log(f"{self.client_address[0]} {path}")


PORT = int(CONFIG.get("server", {}).get("port", 8765))


def start_server() -> ThreadingHTTPServer:
    """Поднимает сервер в фоновом потоке. OSError — порт занят (уже запущен другой экземпляр)."""
    host = CONFIG.get("server", {}).get("host", "0.0.0.0")
    # На Windows reuse_address позволил бы второму экземпляру молча занять тот же порт.
    ThreadingHTTPServer.allow_reuse_address = False
    server = ThreadingHTTPServer((host, PORT), Handler)
    load_history()
    names = ", ".join(w.title for w in WIDGETS.values() if w.available())
    log(f"Kindle Hub слушает {host}:{PORT}, виджеты: {names}")
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    return server


def main() -> int:
    try:
        start_server()
    except OSError:
        log(f"Порт {PORT} занят: Kindle Hub уже запущен")
        return 1
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
