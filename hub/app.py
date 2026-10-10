"""Kindle Hub — приложение для ПК: сервер для Kindle Clock, окно состояния и значок в трее.

Окно целиком рисуется картинкой тем же движком, что и экран Kindle (со сглаживанием),
а клики разбираются по зонам. Закрытие окна сервер не останавливает: он работает из трея.
Запуск: pythonw app.py  (--tray — сразу в трей, так стартует автозапуск)
         python app.py --install — создать ярлык «Kindle Hub» в меню «Пуск».
"""

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
import winreg
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from render import font

ROOT = Path(__file__).parent
SETTINGS_FILE = ROOT / "app.json"
ICON_FILE = ROOT / "kindle-hub.ico"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Kindle Hub"

ONLINE_SEC = 90
LOST_ALERT_SEC = 180
GAP_MIN = 2

# Логический размер окна; на экране умножается на масштаб Windows.
WIN_W, WIN_H = 1120, 800
SS = 2  # супер-сэмплинг: рисуем вдвое крупнее и уменьшаем — сглаженные фигуры и текст
DEVICE_LONG = 410  # длинная сторона корпуса Kindle в окне

PAPER = (241, 239, 232)
INK = (22, 22, 22)
MUTED = (122, 118, 108)
MUTED_ON_INK = (170, 168, 160)
HAIR = (212, 208, 197)
CARD = (248, 247, 242)
GRAPHITE = (40, 40, 42)
KEY = (64, 64, 67)
KEY_HIGHLIGHT = (78, 78, 82)
SCREEN_WHITE = (236, 235, 229)
SCREEN_BLACK = (27, 27, 27)


# ---------- системное ----------

def pythonw() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def autostart_command() -> str:
    return f'"{pythonw()}" "{ROOT / "app.py"}" --tray'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_NAME)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


VPN_HINTS = ("wireguard", "amnezia", "openvpn", "wintun", "tap-windows", "outline")


def vpn_adapters() -> list[str]:
    """Поднятые VPN-адаптеры. Их блокировка трафика мимо туннеля отрезает ПК от локальной сети."""
    script = "Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { $_.Name + '|' + $_.InterfaceDescription }"
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=20, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split("|")[0] for line in out.splitlines() if any(h in line.lower() for h in VPN_HINTS)]


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"notify": True}


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def tray_image(state: str, size: int = 64) -> Image.Image:
    """Значок: маленький Kindle. На связи — строки текста на экране, без связи — восклицательный знак."""
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = big / 64
    d.rounded_rectangle((10 * s, 2 * s, 54 * s, 62 * s), radius=7 * s, fill=GRAPHITE + (255,))
    d.rounded_rectangle((15 * s, 7 * s, 49 * s, 44 * s), radius=1.5 * s, fill=SCREEN_WHITE + (255,))
    if state == "online":
        for k, width in enumerate((26, 18, 26, 12)):
            y = (12 + k * 8) * s
            d.rectangle((19 * s, y, (19 + width) * s, y + 3 * s), fill=INK + (255,))
    else:
        d.rounded_rectangle((30 * s, 13 * s, 34 * s, 31 * s), radius=2 * s, fill=INK + (255,))
        d.ellipse((30 * s, 35 * s, 34 * s, 39 * s), fill=INK + (255,))
    for row in range(2):
        for col in range(6):
            x = (15 + col * 5.8) * s
            y = (49 + row * 5.5) * s
            d.rounded_rectangle((x, y, x + 4 * s, y + 3.5 * s), radius=1 * s, fill=KEY + (255,))
    return img.resize((size, size), Image.LANCZOS)


def write_icon() -> None:
    tray_image("online", 256).save(ICON_FILE, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])


def install_shortcut() -> None:
    """Ярлык «Kindle Hub» в меню «Пуск»."""
    write_icon()
    programs = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    link = programs / "Kindle Hub.lnk"
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:KH_LINK); "
        "$s.TargetPath = $env:KH_TARGET; $s.Arguments = $env:KH_ARGS; "
        "$s.WorkingDirectory = $env:KH_DIR; $s.IconLocation = $env:KH_ICON; "
        "$s.Description = 'Kindle Hub'; $s.Save()"
    )
    env = dict(os.environ, KH_LINK=str(link), KH_TARGET=pythonw(), KH_ARGS=f'"{ROOT / "app.py"}"',
               KH_DIR=str(ROOT), KH_ICON=str(ICON_FILE))
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    print(f"Ярлык создан: {link}")


# ---------- рисование ----------

class Painter:
    """ImageDraw в логических координатах окна: всё умножается на масштаб и супер-сэмплинг."""

    def __init__(self, img: Image.Image, k: float):
        self.img, self.d, self.k = img, ImageDraw.Draw(img), k

    def f(self, size: float, bold: bool = False):
        return font(int(round(size * self.k)), bold)

    def box(self, b):
        return tuple(v * self.k for v in b)

    def stroke(self, width: float) -> int:
        return max(1, round(width * self.k))

    def rect(self, b, fill=None):
        self.d.rectangle(self.box(b), fill=fill)

    def rrect(self, b, r, fill=None, outline=None, width=1.0):
        self.d.rounded_rectangle(self.box(b), radius=r * self.k, fill=fill, outline=outline,
                                 width=self.stroke(width) if outline else 0)

    def line(self, pts, fill=INK, width=1.0):
        self.d.line([(x * self.k, y * self.k) for x, y in pts], fill=fill, width=self.stroke(width))

    def circle(self, cx, cy, r, fill=None, outline=None, width=1.0):
        self.d.ellipse(self.box((cx - r, cy - r, cx + r, cy + r)), fill=fill, outline=outline,
                       width=self.stroke(width) if outline else 0)

    def width(self, text: str, size: float, bold: bool = False) -> float:
        return self.d.textlength(text, font=self.f(size, bold)) / self.k

    def text(self, x, y, text: str, size: float, bold=False, fill=INK, anchor="la"):
        self.d.text((x * self.k, y * self.k), text, font=self.f(size, bold), fill=fill, anchor=anchor)

    def fit(self, text: str, size: float, max_w: float, bold=False) -> str:
        if self.width(text, size, bold) <= max_w:
            return text
        while text and self.width(text + "…", size, bold) > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    def wrap(self, text: str, size: float, max_w: float) -> list[str]:
        lines, current = [], ""
        for word in text.split():
            probe = f"{current} {word}".strip()
            if self.width(probe, size) <= max_w or not current:
                current = probe
            else:
                lines.append(current)
                current = word
        return lines + [current] if current else lines

    def paste(self, image: Image.Image, x, y):
        self.img.paste(image, (round(x * self.k), round(y * self.k)), image if image.mode == "RGBA" else None)


def kindle_device(frame: Image.Image, orient: str, long_px: int) -> Image.Image:
    """Kindle 3 (Kindle Keyboard) с кадром на экране: графитовый корпус, клавиатура, джойстик, тень.

    Рисуется вертикально в миллиметрах настоящего устройства (123 × 190 мм) и поворачивается
    так, как Kindle стоит на столе: landscape — клавиатурой влево, landscape_flip — вправо.
    """
    mm = long_px / 190
    pad = int(26 * mm)
    w, h = int(123 * mm), int(190 * mm)
    size = (w + 2 * pad, h + 2 * pad)
    x0, y0 = pad, pad

    def r(ax, ay, bx, by):
        return (x0 + ax * mm, y0 + ay * mm, x0 + bx * mm, y0 + by * mm)

    # Мягкая тень: размытый силуэт корпуса, сдвинутый вниз уже после поворота.
    shadow = Image.new("L", size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle(r(0, 0, 123, 190), radius=9 * mm, fill=120)
    shadow = shadow.filter(ImageFilter.GaussianBlur(5 * mm))

    body = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    d.rounded_rectangle(r(0, 0, 123, 190), radius=9 * mm, fill=GRAPHITE)
    d.rounded_rectangle(r(0.7, 0.7, 122.3, 189.3), radius=8.4 * mm, outline=(72, 72, 75), width=max(1, int(0.5 * mm)))
    for side in (0.9, 120.6):
        for top, bottom in ((38, 66), (72, 112)):
            d.rounded_rectangle(r(side, top, side + 1.5, bottom), radius=0.7 * mm, fill=KEY_HIGHLIGHT)

    # Экран 91 × 122 мм с текущим кадром в оттенках e-ink.
    sx0, sy0, sx1, sy1 = r(16, 14, 107, 136)
    shown = frame.convert("L")
    if orient == "landscape":
        shown = shown.transpose(Image.Transpose.ROTATE_90)
    elif orient == "landscape_flip":
        shown = shown.transpose(Image.Transpose.ROTATE_270)
    shown = shown.resize((round(sx1 - sx0), round(sy1 - sy0)), Image.LANCZOS)
    tinted = Image.merge("RGB", [shown.point(lambda v, c=c: SCREEN_BLACK[c] + (SCREEN_WHITE[c] - SCREEN_BLACK[c]) * v // 255)
                                 for c in range(3)])
    d.rounded_rectangle((sx0 - 2.4 * mm, sy0 - 2.4 * mm, sx1 + 2.4 * mm, sy1 + 2.4 * mm), radius=1.2 * mm, fill=(52, 52, 55))
    body.paste(tinted, (round(sx0), round(sy0)))
    d.text((x0 + 61.5 * mm, y0 + 140.8 * mm), "kindle", fill=(150, 150, 150), anchor="mm", font=font(max(8, int(3.6 * mm))))

    # Клавиатура: три ряда по десять клавиш, нижний ряд с пробелом, справа джойстик и кнопки.
    bottom_row = [(9, 6.2), (16.6, 6.2), (24.2, 21.4), (47.2, 6.2), (54.8, 6.2), (62.4, 6.2), (70, 6.2)]
    for row in range(4):
        keys = [(9 + col * 7.6 + (3.8 if row == 1 else 0), 6.2) for col in range(10)] if row < 3 else bottom_row
        ky = 146 + row * 9.6
        for kx, kw in keys:
            d.rounded_rectangle(r(kx, ky, kx + kw, ky + 7), radius=1.6 * mm, fill=KEY)
            d.rounded_rectangle(r(kx + 0.6, ky + 0.4, kx + kw - 0.6, ky + 1.5), radius=0.7 * mm, fill=KEY_HIGHLIGHT)
    d.rounded_rectangle(r(92, 152, 114, 174), radius=4 * mm, fill=KEY)
    d.rounded_rectangle(r(98.5, 158.5, 107.5, 167.5), radius=2.4 * mm, fill=(86, 86, 90))
    for by in (144, 178):
        d.rounded_rectangle(r(95, by, 111, by + 4), radius=1.6 * mm, fill=KEY)

    rotate = {"landscape": Image.Transpose.ROTATE_270, "landscape_flip": Image.Transpose.ROTATE_90}.get(orient)
    if rotate is not None:
        shadow, body = shadow.transpose(rotate), body.transpose(rotate)
    out = Image.new("RGBA", body.size, (0, 0, 0, 0))
    out.paste((0, 0, 0, 255), (0, int(3 * mm)), shadow.crop((0, 0, body.width, body.height - int(3 * mm))))
    out.alpha_composite(body)
    return out


# ---------- окно ----------

class App:
    def __init__(self, hub, start_hidden: bool):
        self.hub = hub
        self.events: queue.Queue = queue.Queue()
        self.settings = load_settings()
        self.autostart = autostart_enabled()
        self.state = "waiting"
        self.lost_notified = False
        self.vpn: list[str] = []
        self.started = datetime.now()
        self.zones: list[tuple[tuple, str]] = []
        self.hover: str | None = None
        self.device_key = None
        self.device_img: Image.Image | None = None
        threading.Thread(target=self.watch_vpn, name="vpn", daemon=True).start()

        self.root = root = tk.Tk()
        self.s = root.winfo_fpixels("1i") / 96
        root.title("Kindle Hub")
        root.configure(bg="#%02x%02x%02x" % PAPER)
        root.geometry(f"{round(WIN_W * self.s)}x{round(WIN_H * self.s)}")
        root.resizable(False, False)
        write_icon()
        root.iconbitmap(default=str(ICON_FILE))
        root.protocol("WM_DELETE_WINDOW", self.hide)
        self.view = tk.Label(root, bd=0, bg="#%02x%02x%02x" % PAPER)
        self.view.pack(fill="both", expand=True)
        self.view.bind("<Motion>", self.on_motion)
        self.view.bind("<Leave>", lambda _e: self.set_hover(None))
        self.view.bind("<Button-1>", self.on_click)

        self.tray = self.make_tray()
        if start_hidden:
            root.withdraw()
        self.tick()
        self.poll()

    # ---------- трей и события ----------

    def make_tray(self):
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("Открыть", lambda: self.events.put("show"), default=True),
            pystray.MenuItem("Превью в браузере", lambda: webbrowser.open(f"http://localhost:{self.hub.PORT}/")),
            pystray.MenuItem("Выход", lambda: self.events.put("quit")),
        )
        icon = pystray.Icon("KindleHub", tray_image("waiting"), "Kindle Hub", menu)
        icon.run_detached()
        return icon

    def notify(self, text: str) -> None:
        if self.settings.get("notify", True):
            try:
                self.tray.notify(text, "Kindle Hub")
            except Exception:
                pass

    def watch_vpn(self) -> None:
        while True:
            self.vpn = vpn_adapters()
            time.sleep(60)

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.redraw()

    def hide(self) -> None:
        self.root.withdraw()

    def quit(self) -> None:
        self.tray.stop()
        self.root.destroy()

    def poll(self) -> None:
        while not self.events.empty():
            event = self.events.get()
            if event == "show":
                self.show()
            elif event == "quit":
                self.quit()
                return
        if self.hub.SHOW_WINDOW.is_set():
            self.hub.SHOW_WINDOW.clear()
            self.show()
        self.root.after(200, self.poll)

    def zone_at(self, event) -> str | None:
        x, y = event.x / self.s, event.y / self.s
        for (x0, y0, x1, y1), name in self.zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    def set_hover(self, name) -> None:
        if name != self.hover:
            self.hover = name
            self.view.config(cursor="hand2" if name else "")
            self.redraw()

    def on_motion(self, event) -> None:
        self.set_hover(self.zone_at(event))

    def on_click(self, event) -> None:
        name = self.zone_at(event)
        if name == "autostart":
            self.autostart = not self.autostart
            set_autostart(self.autostart)
        elif name == "notify":
            self.settings["notify"] = not self.settings.get("notify", True)
            save_settings(self.settings)
        elif name == "preview":
            webbrowser.open(f"http://localhost:{self.hub.PORT}/")
        elif name == "log":
            os.startfile(ROOT / "hub.log")
        elif name == "folder":
            os.startfile(ROOT)
        if name:
            self.redraw()

    # ---------- состояние ----------

    def kindle_state(self, k: dict) -> tuple[str, float | None]:
        seen = k["last_seen"]
        age = (datetime.now() - seen).total_seconds() if seen else None
        if seen is None:
            return "waiting", None
        if age < ONLINE_SEC:
            return "online", age
        if k["bye_at"] and k["bye_at"] >= seen:
            return "closed", age
        return "lost", age

    def tick(self) -> None:
        k = self.hub.kindle_status()
        state, age = self.kindle_state(k)
        if state != self.state:
            self.tray.icon = tray_image("online" if state == "online" else "offline")
            self.tray.title = f"Kindle Hub — {self.headline(state, age, k)[0].lower()}"
        if state == "lost" and age >= LOST_ALERT_SEC and not self.lost_notified:
            self.notify(f"Kindle не выходит на связь {int(age // 60)} мин.")
            self.lost_notified = True
        if state == "online" and self.lost_notified:
            self.notify("Связь с Kindle восстановилась.")
            self.lost_notified = False
        self.state = state
        if self.root.state() != "withdrawn":
            self.redraw()
        self.root.after(2000, self.tick)

    @staticmethod
    def ago(seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)} с назад"
        if seconds < 3600:
            return f"{int(seconds // 60)} мин назад"
        return f"{int(seconds // 3600)} ч {int(seconds % 3600 // 60)} мин назад"

    def headline(self, state: str, age, k: dict) -> tuple[str, str]:
        if state == "online":
            return "На связи", f"последний запрос {self.ago(age)}"
        if state == "closed":
            return "Часы закрыты", f"Kindle закрыл часы в {k['bye_at']:%H:%M}"
        if state == "lost":
            fallback = "Kindle показывает запас кадров" if self.pack_info() else "Kindle рисует простые часы"
            return f"Нет связи · {int(age // 60)} мин", fallback
        return "Жду Kindle", "откройте часы на Kindle: Shift, C, L"

    def alert(self, state: str, k: dict) -> str:
        if k["ip"] and k["pc_ip"]:
            source = self.hub.route_source(k["ip"])
            if source and source != k["pc_ip"]:
                return (f"Ответы Kindle уходят с адреса {source}, а не {k['pc_ip']}: VPN перехватил локальную сеть. "
                        "Исключите 192.168.0.0/24 в настройках VPN.")
        if state == "lost" and self.vpn:
            pc = k["pc_ip"] or "адрес ПК"
            return (f"Включён VPN ({', '.join(self.vpn)}). Если он блокирует трафик мимо туннеля, ПК не принимает "
                    f"подключения из домашней сети. Проверка: с телефона откройте http://{pc}:{self.hub.PORT}/")
        return ""

    def pack_info(self) -> tuple[int, datetime] | None:
        with self.hub._lock:
            packs = list(self.hub._packs.values())
        if not packs:
            return None
        pack = max(packs, key=lambda p: p["built"])
        segs = [line.split() for line in pack["manifest"].splitlines() if line.startswith("seg ")]
        if not segs:
            return None
        return len(segs), datetime.fromtimestamp(int(segs[-1][2]))

    # ---------- данные для карточек ----------

    def cards(self) -> list[dict]:
        now = datetime.now()
        w = self.hub.WIDGETS
        out = []

        def card(title: str, widget) -> dict:
            feed = getattr(widget, "feed", None)
            note, bad = "", False
            if feed is not None:
                if feed.value is None:
                    note, bad = (("ошибка: " + feed.error) if feed.error else "загружается…"), bool(feed.error)
                else:
                    note = f"обновлено {feed.updated:%H:%M}" if feed.updated else ""
                    if feed.error:
                        note += " · сбой, повтор"
            return {"title": title, "value": "—", "sub": "", "note": note, "bad": bad}

        if "weather" in w:
            from widgets.weather import CODES, temp
            widget = w["weather"]
            c = card("Погода", widget)
            if widget.feed.value:
                cur = widget.current_at(widget.feed.value, now)
                c.update(value=temp(cur["temperature_2m"]),
                         sub=f"{CODES.get(cur['weather_code'], ('', ''))[1]}, ощущается {temp(cur['apparent_temperature'])}")
            out.append(c)
        if "calendar" in w and w["calendar"].available():
            widget = w["calendar"]
            c = card("Ближайшая встреча", widget)
            if widget.feed.value is not None:
                event, ongoing, _ = widget._state(now.astimezone())
                if event is None:
                    c.update(value="встреч нет", sub="на ближайшие сутки")
                else:
                    days = (event["start"].date() - now.date()).days
                    day = "сегодня" if days == 0 else "завтра" if days == 1 else f"{event['start']:%d.%m}"
                    c.update(value=event["title"],
                             sub=f"идёт до {event['end']:%H:%M}" if ongoing else f"{day} в {event['start']:%H:%M}")
            out.append(c)
        if "bitcoin" in w:
            from widgets.bitcoin import change, money
            widget = w["bitcoin"]
            c = card("Биткойн", widget)
            prices = widget.feed.value
            if prices:
                last_t, last = prices[-1]
                day_ago = next((p for t, p in prices if t >= last_t - 24 * 3600 * 1000), prices[0][1])
                c.update(value=money(last, widget.currency), sub=f"{change(last, day_ago)} за сутки")
            out.append(c)
        if "hour_mosaic" in w and w["hour_mosaic"].available():
            widget = w["hour_mosaic"]
            c = card("Hour Mosaic", widget)
            try:
                _, minutes = widget._load(now)
                productive = sum(1 for cat in minutes.values() if cat == "productive")
                c.update(value=f"{productive // 60} ч {productive % 60:02d} мин", sub="продуктивно сегодня")
            except Exception as exc:
                c.update(note=f"ошибка: {exc}", bad=True)
            out.append(c)
        return out

    def device(self, k: dict) -> Image.Image:
        frame = self.hub._last["image"]
        orient = k.get("orient")
        if frame is None:
            screen = k.get("screen")
            w, h = (int(screen[0]), int(screen[1])) if screen else (800, 600)
            if (orient or "landscape").startswith("landscape") and w < h:
                w, h = h, w
            frame = self.hub.render(w, h, self.hub.Context(now=datetime.now()), self.hub.WIDGETS)
        if orient not in ("portrait", "landscape", "landscape_flip"):
            orient = "landscape" if frame.width > frame.height else "portrait"
        key = (id(frame), self.hub._last["at"], orient)
        if key != self.device_key:
            self.device_key = key
            self.device_img = kindle_device(frame, orient, round(DEVICE_LONG * self.s * SS))
        return self.device_img

    # ---------- отрисовка ----------

    def redraw(self) -> None:
        ks = self.s * SS
        img = Image.new("RGB", (round(WIN_W * ks), round(WIN_H * ks)), PAPER)
        p = Painter(img, ks)
        self.zones = []
        k = self.hub.kindle_status()
        state, age = self.kindle_state(k)
        trouble = state in ("lost", "waiting")

        # Шапка во всю ширину: название слева, крупный статус справа. При проблеме — чёрная.
        bg, fg, muted = (INK, PAPER, MUTED_ON_INK) if trouble else (PAPER, INK, MUTED)
        p.rect((0, 0, WIN_W, 112), fill=bg)
        p.text(40, 30, "kindle", 34, bold=True, fill=fg)
        p.text(40 + p.width("kindle ", 34, True), 30, "hub", 34, fill=muted)
        pc = k["pc_ip"] or (self.hub.route_source(k["ip"]) if k["ip"] else None) or "этот ПК"
        p.text(42, 80, f"сервер {pc}:{self.hub.PORT} · работает с {self.started:%H:%M}", 13, fill=muted)
        title, detail = self.headline(state, age, k)
        p.text(WIN_W - 40, 30, title, 34, bold=True, fill=fg, anchor="ra")
        p.text(WIN_W - 40, 80, detail, 13, fill=muted, anchor="ra")
        dot_x = WIN_W - 40 - p.width(title, 34, True) - 22
        if state == "online":
            p.circle(dot_x, 54, 8, fill=fg)
        else:
            p.circle(dot_x, 54, 7, outline=fg, width=2.4)
        if not trouble:
            p.line([(40, 112), (WIN_W - 40, 112)], fill=INK, width=1.5)

        # Слева — Kindle с тем, что сейчас на его экране, под ним настройки.
        device = self.device(k)
        dw, dh = device.width / ks, device.height / ks
        dy = 124 + (500 - dh) / 2
        p.paste(device, 40 + (470 - dw) / 2, dy)
        at = self.hub._last["at"]
        caption_y = min(622, dy + dh - DEVICE_LONG * 26 / 190 + 10)
        p.text(275, caption_y, f"на экране Kindle · кадр {at:%H:%M:%S}" if at else "так будет выглядеть экран Kindle",
               12, fill=MUTED, anchor="ma")

        y = 652
        p.line([(40, y), (510, y)], fill=HAIR, width=1)
        y += 18
        for name, label, value in (("autostart", "Запускать вместе с Windows", self.autostart),
                                   ("notify", "Уведомлять, если Kindle пропал", self.settings.get("notify", True))):
            self.switch(p, 40, y, value, self.hover == name)
            p.text(96, y + 2, label, 15)
            self.zones.append(((36, y - 4, 96 + p.width(label, 15) + 8, y + 28), name))
            y += 36
        x = 40
        y += 4
        for name, label in (("preview", "Превью в браузере"), ("log", "Лог"), ("folder", "Папка")):
            wlen = p.width(label, 13, True)
            hovered = self.hover == name
            if hovered:
                p.rrect((x - 8, y - 5, x + wlen + 8, y + 23), 6, fill=INK)
            p.text(x, y, label, 13, bold=True, fill=PAPER if hovered else INK)
            if not hovered:
                p.line([(x, y + 21), (x + wlen, y + 21)], fill=INK, width=1)
            self.zones.append(((x - 8, y - 5, x + wlen + 8, y + 23), name))
            x += wlen + 28

        # Справа — подробности.
        rx, rw = 560, WIN_W - 40 - 560
        y = 136
        alert = self.alert(state, k)
        if alert:
            lines = p.wrap(alert, 14, rw - 36)
            box_h = 24 + len(lines) * 21
            p.rrect((rx, y, rx + rw, y + box_h), 10, fill=INK)
            for i, line in enumerate(lines):
                p.text(rx + 18, y + 12 + i * 21, line, 14, fill=PAPER)
            y += box_h + 22

        y = self.section(p, rx, y, rw, "Kindle")
        battery = "—" if k["battery"] is None else f"{k['battery']}%" + (", зарядка" if k["charging"] else "")
        tiles = [("АДРЕС", k["ip"] or "—"), ("WI-FI", k["essid"] or "—"), ("ЭКРАН", self.screen_text(k)),
                 ("ЗАРЯД", battery)]
        weights = (1.15, 1.0, 1.2, 0.65)
        tx = rx
        for (label, value), weight in zip(tiles, weights):
            tw = (rw - 3 * 14) * weight / sum(weights)
            p.text(tx, y, label, 10, bold=True, fill=MUTED)
            p.text(tx, y + 18, p.fit(value, 16, tw, True), 16, bold=True)
            tx += tw + 14
        y += 64

        y = self.section(p, rx, y, rw, "Связь за сутки")
        y = self.strip(p, rx, y, rw, k["minutes"])

        y = self.section(p, rx, y + 4, rw, "Данные")
        cards = self.cards()
        cw, ch = (rw - 14) / 2, 92
        for i, c in enumerate(cards):
            self.card(p, rx + (i % 2) * (cw + 14), y + (i // 2) * (ch + 14), cw, ch, c)
        y += ((len(cards) + 1) // 2) * (ch + 14) + 6

        pack = self.pack_info()
        if pack:
            count, until = pack
            when = until.strftime("%H:%M") + (" завтра" if until.date() > datetime.now().date() else "")
            p.text(rx, y, f"Без связи Kindle покажет запас до {when} ({count} кадров)", 13, fill=MUTED)
        else:
            p.text(rx, y, "Запас кадров на время без связи соберётся, когда Kindle выйдет на связь", 13, fill=MUTED)

        p.text(WIN_W - 40, WIN_H - 28, "крестик прячет окно в трей · выход — правой кнопкой по значку", 11,
               fill=MUTED, anchor="ra")

        self.photo = ImageTk.PhotoImage(img.resize((round(WIN_W * self.s), round(WIN_H * self.s)), Image.LANCZOS))
        self.view.config(image=self.photo)

    @staticmethod
    def screen_text(k: dict) -> str:
        orient = k.get("orient")
        if orient:
            return "вертикально" if orient == "portrait" else "горизонтально"
        if not k["screen"]:
            return "—"
        w, h, rot = k["screen"]
        return "горизонтально" if (int(w) > int(h)) != (rot in ("90", "270")) else "вертикально"

    @staticmethod
    def section(p: Painter, x, y, w, title: str) -> float:
        p.text(x, y, title.upper(), 11, bold=True)
        p.line([(x, y + 22), (x + w, y + 22)], fill=INK, width=1.2)
        return y + 36

    @staticmethod
    def switch(p: Painter, x, y, on: bool, hovered: bool) -> None:
        p.rrect((x, y, x + 44, y + 24), 12, fill=INK if on else PAPER, outline=INK, width=2.2 if hovered else 1.6)
        p.circle(x + 32 if on else x + 12, y + 12, 8, fill=PAPER if on else INK)

    def strip(self, p: Painter, x, y, w, minutes: set) -> float:
        now_min = int(time.time() // 60)
        start = now_min - 24 * 60
        scale = w / (24 * 60)
        bar_h = 30
        p.rrect((x, y, x + w, y + bar_h), 6, fill=CARD, outline=HAIR, width=1)
        run = None
        for m in range(start, now_min + 2):
            on = m in minutes
            if on and run is None:
                run = m
            elif not on and run is not None:
                p.rect((x + (run - start) * scale, y + 3, x + (m - start) * scale, y + bar_h - 3), fill=INK)
                run = None
        for m in range((start // 60 + 1) * 60, now_min + 1, 60):
            hour = datetime.fromtimestamp(m * 60).hour
            hx = x + (m - start) * scale
            if hour % 6 == 0 and hx < x + w - 70:
                p.line([(hx, y + bar_h), (hx, y + bar_h + 6)], fill=INK, width=1)
                p.text(hx, y + bar_h + 9, f"{hour:02d}:00", 11, fill=MUTED, anchor="ma")
        p.text(x + w, y + bar_h + 9, "сейчас", 11, bold=True, anchor="ra")

        gaps, longest, last = 0, 0, None
        for m in sorted(minutes):
            if last is not None and m - last > GAP_MIN:
                gaps += 1
                longest = max(longest, m - last - 1)
            last = m
        if minutes:
            text = f"Перерывов дольше {GAP_MIN} мин: {gaps}" + (f", самый долгий {self.duration(longest)}" if gaps else "")
        else:
            text = "За сутки Kindle на связь не выходил"
        p.text(x, y + bar_h + 30, text, 12, fill=MUTED)
        return y + bar_h + 58

    @staticmethod
    def duration(minutes: int) -> str:
        return f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"

    @staticmethod
    def card(p: Painter, x, y, w, h, c: dict) -> None:
        bad = c["bad"]
        p.rrect((x, y, x + w, y + h), 12, fill=INK if bad else CARD, outline=INK if bad else HAIR, width=1)
        fg, muted = (PAPER, MUTED_ON_INK) if bad else (INK, MUTED)
        p.text(x + 16, y + 13, c["title"].upper(), 10, bold=True, fill=muted)
        if c["note"] and c["sub"]:
            p.text(x + w - 16, y + 13, p.fit(c["note"], 10, w / 2 - 16), 10, fill=muted, anchor="ra")
        p.text(x + 16, y + 31, p.fit(c["value"], 22, w - 32, True), 22, bold=True, fill=fg)
        p.text(x + 16, y + 66, p.fit(c["sub"] or c["note"], 12, w - 32), 12, fill=fg)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if "--install" in sys.argv:
        install_shortcut()
        return 0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass

    import kindle_hub

    try:
        kindle_hub.start_server()
    except OSError:
        # Уже запущено: просим работающий экземпляр показать окно.
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{kindle_hub.PORT}/api/show", timeout=3)
            return 0
        except OSError:
            ctypes.windll.user32.MessageBoxW(None, f"Порт {kindle_hub.PORT} занят другой программой.", "Kindle Hub", 0x10)
            return 1
    App(kindle_hub, start_hidden="--tray" in sys.argv).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
