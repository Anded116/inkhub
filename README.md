# inkhub

A second life for old e-ink Kindles. inkhub turns a **Kindle Keyboard (Kindle 3)** into a
desk companion for your PC, while it stays a normal e-reader the rest of the time.

[Русская версия](README.ru.md)

## What it does today

**Desk clock and dashboard.** A hotkey on the Kindle opens a full-screen e-ink dashboard:

- large clock and date, synced from the PC (or from the internet when the PC is off);
- today's meetings from Google Calendar: a timeline of the next hours, the next meeting
  in large type with its full title, and a countdown; the whole screen inverts a few
  minutes before a meeting starts so you notice it from across the room;
- weather now and for the next three days (Open-Meteo, no API key);
- Bitcoin price with a weekly chart (CoinGecko, no API key);
- portrait or landscape, with the 5-way controller rotating along with the screen;
- keeps working without the PC: the PC pre-renders the next 24 hours of screens,
  and the Kindle only draws the time on top.

Pressing Home returns to the Kindle exactly where you left it.

**Kindle Hub, the PC app.** A small Windows tray app renders the screens and serves them
to the Kindle over the home Wi-Fi. Its window shows a live preview of the Kindle screen,
the connection history for the last 24 hours, the state of every data source, and warns
when a VPN cuts the PC off from the home network.

## In progress: offline Reddit reader

A keyboard-driven Reddit reader for the Kindle. Once a day the PC downloads hot posts and
top comments from the user's own subscriptions (read-only, about 150 API requests a day),
converts images to grayscale for e-ink and renders pages. The Kindle reads them offline:
feed → post → comment threads, collapsing threads and paging with the physical keyboard.
Nothing is posted, voted or sent; downloaded data stays on the user's devices and is
replaced daily.

## How it works

```
 PC (Windows)                                  Kindle Keyboard
 ┌────────────────────────────┐  home Wi-Fi    ┌──────────────────────────────┐
 │ hub/app.py  tray + window  │◄── once a ─────│ kindleclock/clock.sh         │
 │ hub/kindle_hub.py  server  │    minute      │  event loop: ticks + keys    │
 │ hub/render.py  draws frame │───────────────►│  frame → /dev/fb0 → refresh  │
 │ hub/widgets/  data sources │  only changed  │  offline: pre-rendered frames│
 └────────────────────────────┘  rows, gzip    └──────────────────────────────┘
```

- The PC renders each frame with Pillow in the Kindle's exact framebuffer format
  (4 bpp, inverted grayscale) and sends only the rows that changed since the last frame.
- On the Kindle a shell script freezes the stock UI, grabs the keyboard (so key presses
  don't leak into the Kindle menus), writes frames to the framebuffer and refreshes the
  e-ink panel with [FBInk](https://github.com/NiLuJe/FBInk). Low-level bits (input grab,
  display rotation, HTTP) are a small LuaJIT FFI tool, `kindle/kindleclock/bin/kctool.lua`.
- Everything is drawn in pure black and white: e-ink shows mid-grays poorly, so
  categories are told apart by hatching, not shades.

## Requirements

- Kindle Keyboard (Kindle 3) with the jailbreak and [launchpad](https://github.com/kindle-keyboard-k3g/kindle-launchpad)
  installed;
- a Windows PC on the same network with Python 3.13 and `pip install pillow pystray
  icalendar recurring-ical-events`.

## Setup

1. Copy the Kindle part: plug the Kindle in and run `python deploy.py` (or copy
   `kindle/` to the root of the Kindle drive). Set your PC's address in
   `kindleclock/config.sh` (`HUB_URL`).
2. On the PC copy `hub/secrets.example.toml` to `hub/secrets.toml` and fill in your
   Google Calendar secret iCal link and coordinates. `secrets.toml` is never committed.
3. Run `python hub/app.py --install` once to add **Kindle Hub** to the Start menu,
   then start it from there.
4. On the Kindle press **Shift, C, L**. Home or Back exits.

Keys on the clock screen: **Menu** sets date and time manually, **S** syncs the time,
**R** rotates the screen, the page keys force a full e-ink refresh.

## Privacy

The calendar link, coordinates and any future API credentials live only in
`hub/secrets.toml` on your PC. Logs, caches and the downloaded data stay on your own PC
and Kindle and are not sent anywhere.

## License

MIT, see [LICENSE](LICENSE). Bundled third-party binaries and fonts keep their own
licenses, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
