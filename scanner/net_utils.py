"""Общие утилиты: переводы канал<->частота, сигнал, OUI, экспорт."""

from __future__ import annotations

import csv
import json
from datetime import datetime


def channel_to_freq_mhz(channel: int) -> int | None:
    """Номер Wi-Fi канала -> частота в МГц."""
    try:
        ch = int(channel)
    except (TypeError, ValueError):
        return None
    if 1 <= ch <= 13:
        return 2412 + (ch - 1) * 5
    if ch == 14:
        return 2484
    # 5 ГГц — стандартные каналы
    ch5 = {
        32: 5160, 36: 5180, 40: 5200, 44: 5220, 48: 5240,
        52: 5260, 56: 5280, 60: 5300, 64: 5320, 68: 5340,
        96: 5480, 100: 5500, 104: 5520, 108: 5540, 112: 5560,
        116: 5580, 120: 5600, 124: 5620, 128: 5640, 132: 5660,
        136: 5680, 140: 5700, 144: 5720, 149: 5745, 153: 5765,
        157: 5785, 161: 5805, 165: 5825, 169: 5845, 173: 5865,
    }
    if ch in ch5:
        return ch5[ch]
    if 36 <= ch <= 177:  # эвристика для 5 ГГц
        return 5000 + ch * 5
    if 1 <= ch <= 233:  # 6 ГГц: ch -> 5950 + ch*5
        maybe = 5950 + ch * 5
        if 5925 <= maybe <= 7125:
            return maybe
    return None


def freq_to_band(freq_mhz: int | None) -> str:
    if not freq_mhz:
        return "—"
    if 2400 <= freq_mhz < 2500:
        return "2.4 ГГц"
    if 5000 <= freq_mhz < 5900:
        return "5 ГГц"
    if 5925 <= freq_mhz <= 7125:
        return "6 ГГц"
    return "—"


def signal_pct_to_dbm(pct: int | float | None) -> int | None:
    """Грубая оценка: 100% ~ -50 дБм, 0% ~ -100 дБм."""
    if pct is None:
        return None
    try:
        p = max(0, min(100, float(pct)))
    except (TypeError, ValueError):
        return None
    return int(round(p / 2 - 100))


def signal_label(pct) -> str:
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return "—"
    if p >= 80:
        return "Отличный"
    if p >= 60:
        return "Хороший"
    if p >= 40:
        return "Средний"
    if p >= 20:
        return "Слабый"
    return "Очень слабый"


def security_short(auth: str = "", encryption: str = "") -> str:
    a = (auth or "").upper().replace("-", "").replace(" ", "")
    e = (encryption or "").upper().replace("-", "").replace(" ", "")
    if "OPEN" in a or "ОТКРЫТ" in (auth or "").upper() or a == "":
        return "Открытая"
    tag = auth or "—"
    if e and e not in ("NONE", "—"):
        tag = f"{auth} / {encryption}"
    if "WPA3" in a:
        return f"[SEC] {tag}"
    if "WPA2" in a or "WPA" in a:
        return f"[SEC] {tag}"
    if "WEP" in a or "WEP" in e:
        return f"[!] {tag}"
    return tag


# --- Мини-база OUI (первые 3 байта MAC -> вендор) ---
_OUI = {
    "B4:E5:4C": "Keenetic",
    "A8:41:F4": "Realtek",
    "B4:E5:4D": "Keenetic",
    "D8:3A:DD": "Xiaomi",
    "E4:AA:EC": "Xiaomi",
    "F0:9F:C2": "Ubiquiti",
    "80:2A:A8": "Ubiquiti",
    "18:E8:29": "Cisco",
    "00:1A:11": "Google",
    "3C:22:FB": "Google Nest",
    "DC:A6:32": "Raspberry Pi",
    "B8:27:EB": "Raspberry Pi",
    "FC:A1:83": "Espressif (ESP32)",
    "24:6F:28": "Espressif (ESP32)",
    "30:AE:A4": "Espressif (ESP32)",
    "7C:DF:A1": "Espressif (ESP32)",
    "AC:67:B2": "Espressif",
    "50:02:91": "TP-Link",
    "98:DA:C4": "TP-Link",
    "14:CF:92": "TP-Link",
    "C0:4A:00": "TP-Link",
    "E8:48:B8": "Huawei",
    "48:AD:08": "Huawei",
    "04:F0:21": "Huawei",
    "8C:3B:AD": "Apple",
    "F0:18:98": "Apple",
    "A4:83:E7": "Apple",
    "D0:C5:D3": "Apple",
    "00:25:00": "Apple",
    "3C:06:30": "Apple",
    "60:38:E0": "Samsung",
    "E4:7D:BD": "Samsung",
    "78:59:D8": "Samsung",
    "00:12:FB": "Samsung",
    "D0:04:01": "Samsung",
    "38:AA:3C": "Intel",
    "7C:70:DB": "Intel",
    "40:A8:F0": "Intel",
    "9C:B6:D0": "Ralink/MediaTek",
    "00:0C:E7": "MediaTek",
    "44:D9:E7": "Ubiquiti",
    "F4:92:BF": "Murata",
    "00:1B:C5": "Shenzhen",
    "2C:CF:67": "HTC",
    "00:26:BB": "HTC",
}


def oui_lookup(mac: str) -> str:
    """Производитель ОБОРУДОВАНИЯ точки доступа по первым 3 байтам MAC.

    Это вендор железа (роутера), а не интернет-провайдера — провайдера
    по радиоскану определить нельзя.
    """
    if not mac:
        return "—"
    parts = mac.upper().replace("-", ":").split(":")
    if len(parts) < 3:
        return "—"
    try:
        first = int(parts[0], 16)
    except ValueError:
        return "—"
    if first & 0x02:
        # locally administered bit — случайный/виртуальный MAC
        return "Случайный MAC"
    key = ":".join(parts[:3])
    return _OUI.get(key, "—")


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def export_json(rows: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def export_csv(rows: list[dict], path: str) -> None:
    if not rows:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("")
        return
    keys: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in keys:
                keys.append(k)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
