"""Фоновая загрузка данных для виджетов: кадр для Kindle никогда не ждёт сеть.

Ответы просим сжатыми: iCal-календарь Google целиком весит мегабайты, а в gzip — в 20 раз меньше.
Последний удачный результат лежит на диске, чтобы после перезапуска виджеты сразу были с данными.
"""

import gzip
import json
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "cache"
TIMEOUT = 60


def fetch_bytes(url: str, timeout: int = TIMEOUT) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "kindle-hub", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
    return data


def get_json(url: str, timeout: int = TIMEOUT):
    return json.loads(fetch_bytes(url, timeout))


def get_text(url: str, timeout: int = TIMEOUT) -> str:
    return fetch_bytes(url, timeout).decode("utf-8", errors="replace")


def log(msg: str) -> None:
    """Заменяется на запись в hub.log при старте сервера."""
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


class Feed:
    """Раз в every секунд вызывает fetch() в своём потоке и хранит последний удачный результат.

    encode/decode переводят значение в JSON и обратно для кэша на диске.
    """

    def __init__(self, name: str, fetch, every: int, retry: int = 60, encode=None, decode=None):
        self.name = name
        self.value = None
        self.error: str | None = None
        self.updated: datetime | None = None
        self.next_at: datetime | None = None
        self._fetch, self._every, self._retry = fetch, every, retry
        self._encode = encode or (lambda v: v)
        self._decode = decode or (lambda v: v)
        self._cache = CACHE_DIR / f"{name}.json"
        self._load_cache()
        threading.Thread(target=self._loop, name=f"feed-{name}", daemon=True).start()

    def _load_cache(self) -> None:
        try:
            saved = json.loads(self._cache.read_text(encoding="utf-8"))
            self.value = self._decode(saved["value"])
            self.updated = datetime.fromisoformat(saved["updated"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save_cache(self) -> None:
        try:
            CACHE_DIR.mkdir(exist_ok=True)
            tmp = self._cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({"updated": self.updated.isoformat(), "value": self._encode(self.value)},
                                      ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._cache)
        except (OSError, TypeError, ValueError) as exc:
            log(f"{self.name}: кэш не записан: {exc}")

    def _wait(self, seconds: int) -> None:
        self.next_at = datetime.fromtimestamp(time.time() + seconds)
        time.sleep(seconds)

    def _loop(self) -> None:
        while True:
            try:
                self.value = self._fetch()
                self.updated = datetime.now()
                self.error = None
                self._save_cache()
                self._wait(self._every)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                log(f"{self.name}: {self.error}")
                self._wait(self._retry)
