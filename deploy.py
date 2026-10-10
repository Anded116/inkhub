"""Копирует kindle/ на подключённый по USB Kindle.

python deploy.py            обновить программу, настройки на Kindle не трогать
python deploy.py --config   заодно перезаписать config.sh на Kindle версией из репозитория
"""

import shutil
import string
import sys
from pathlib import Path

SRC = Path(__file__).parent / "kindle"
KEEP = {"config.sh", "state.sh", "clock.log"}


def find_kindle() -> Path | None:
    import ctypes

    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        label = ctypes.create_unicode_buffer(64)
        if ctypes.windll.kernel32.GetVolumeInformationW(root, label, 64, None, None, None, None, 0):
            if label.value.lower() == "kindle":
                return Path(root)
    return None


def main() -> int:
    target = find_kindle()
    if target is None:
        print("Kindle не найден: подключите его по USB.")
        return 1
    overwrite_config = "--config" in sys.argv
    for src in SRC.rglob("*"):
        if src.is_dir():
            continue
        dst = target / src.relative_to(SRC)
        if src.name in KEEP and dst.exists() and not (overwrite_config and src.name == "config.sh"):
            print(f"  оставлен   {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        print(f"  обновлён   {dst}")
    print(f"Готово: {target}. Безопасно извлеките Kindle перед отключением.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
