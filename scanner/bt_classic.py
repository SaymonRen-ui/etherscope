"""Classic Bluetooth (BR/EDR) сканер для Windows через BluetoothAPIs.dll (ctypes).

Поля устройства: имя, адрес, класс устройства (CoD) с расшифровкой
major/minor/service, connected, remembered, authenticated,
last_seen / last_used, адрес радиомодуля.

Плюс fallback: список Bluetooth-устройств из PnP (когда радиомодуль
недоступен или служба не отвечает).
"""

from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes
from datetime import datetime

ERROR_NO_MORE_ITEMS = 259
ERROR_SUCCESS = 0

# --- Структуры WinAPI ---

class SYSTEMTIME(ctypes.Structure):
    _fields_ = [(n, wintypes.WORD) for n in (
        "wYear", "wMonth", "wDayOfWeek", "wDay",
        "wHour", "wMinute", "wSecond", "wMilliseconds")]


class BLUETOOTH_FIND_RADIO_PARAMS(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD)]


class BLUETOOTH_RADIO_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("address", ctypes.c_ulonglong),
        ("szName", wintypes.WCHAR * 248),
        ("ulClassofDevice", wintypes.ULONG),
        ("lmpSubversion", wintypes.USHORT),
        ("manufacturer", wintypes.USHORT),
    ]


class BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("fReturnAuthenticated", wintypes.BOOL),
        ("fReturnRemembered", wintypes.BOOL),
        ("fReturnUnknown", wintypes.BOOL),
        ("fReturnConnected", wintypes.BOOL),
        ("fIssueInquiry", wintypes.BOOL),
        ("cTimeoutMultiplier", ctypes.c_ubyte),
        ("hRadio", wintypes.HANDLE),
    ]


class BLUETOOTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("Address", ctypes.c_ulonglong),
        ("ulClassofDevice", wintypes.ULONG),
        ("fConnected", wintypes.BOOL),
        ("fRemembered", wintypes.BOOL),
        ("fAuthenticated", wintypes.BOOL),
        ("stLastSeen", SYSTEMTIME),
        ("stLastUsed", SYSTEMTIME),
        ("szName", wintypes.WCHAR * 248),
    ]


def _load_api():
    return ctypes.WinDLL("bthprops.cpl")


def format_bt_addr(addr: int) -> str:
    addr &= 0xFFFFFFFFFFFF
    parts = [(addr >> (8 * i)) & 0xFF for i in range(5, -1, -1)]
    return ":".join(f"{b:02X}" for b in parts)


def _systemtime_to_str(st: SYSTEMTIME) -> str:
    try:
        if st.wYear in (0, 1601):
            return "—"
        return datetime(st.wYear, st.wMonth, st.wDay,
                        st.wHour, st.wMinute, st.wSecond).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError):
        return "—"


# --- Расшифровка Class of Device ---
_COD_MAJOR = {
    0x00: "Разное", 0x01: "Компьютер", 0x02: "Телефон",
    0x03: "Сеть / точка доступа", 0x04: "Аудио/видео",
    0x05: "Периферия (HID)", 0x06: "Устройство обработки изображений",
    0x07: "Носимое", 0x08: "Игрушка", 0x09: "Здоровье",
}
_COD_MINOR_AUDIO = {0x00: "—", 0x01: "Гарнитура", 0x02: "Hands-free",
                    0x06: "Наушники", 0x07: "Портативное аудио",
                    0x0B: "VMS", 0x0C: "Видеокамера", 0x0E: "Игровая приставка/очки"}
_COD_MINOR_PHONE = {0x00: "—", 0x01: "Сотовый", 0x02: "Беспроводной",
                    0x03: "Смартфон", 0x04: "Модем/шлюз", 0x05: "ISDN"}
_COD_MINOR_COMPUTER = {0x00: "—", 0x01: "Десктоп", 0x02: "Сервер",
                       0x03: "Ноутбук", 0x04: "КПК/планшет", 0x05: "Планшет", 0x06: "Наручный ПК"}
_COD_MINOR_PERIPH = {0x00: "—", 0x10: "Клавиатура", 0x20: "Мышь",
                     0x30: "Комбо", 0x40: "Джойстик", 0x50: "Геймпад",
                     0x60: "Пульт", 0x80: "Датчик"}
_COD_SERVICES = {0x100000: "Позиционирование", 0x80000: "Сеть",
                 0x40000: "Рендеринг", 0x20000: "Захват",
                 0x10000: "Объект", 0x8000: "Аудио",
                 0x4000: "Телефония", 0x2000: "Информация"}


def decode_cod(cod: int) -> tuple[str, str, str]:
    major = (cod >> 8) & 0x1F
    minor = (cod >> 2) & 0x3F
    major_name = _COD_MAJOR.get(major, f"major=0x{major:02X}")
    minor_name: str
    if major == 0x04:
        minor_name = _COD_MINOR_AUDIO.get(minor, f"0x{minor:02X}")
    elif major == 0x02:
        minor_name = _COD_MINOR_PHONE.get(minor, f"0x{minor:02X}")
    elif major == 0x01:
        minor_name = _COD_MINOR_COMPUTER.get(minor, f"0x{minor:02X}")
    elif major == 0x05:
        minor_name = _COD_MINOR_PERIPH.get(minor & 0xF0, f"0x{minor:02X}")
    else:
        minor_name = f"0x{minor:02X}" if minor else "—"
    services = [n for bit, n in _COD_SERVICES.items() if cod & bit]
    return major_name, minor_name, ", ".join(services) if services else "—"


def get_radios() -> tuple[list[dict], str]:
    """Список локальных BT-радиомодулей."""
    radios: list[dict] = []
    try:
        api = _load_api()
    except OSError as e:
        return [], f"Нет BluetoothAPIs: {e}"
    find_first = api.BluetoothFindFirstRadio
    find_next = api.BluetoothFindNextRadio
    find_close = api.BluetoothFindRadioClose
    get_info = api.BluetoothGetRadioInfo
    find_first.restype = wintypes.HANDLE
    find_first.argtypes = [ctypes.POINTER(BLUETOOTH_FIND_RADIO_PARAMS),
                           ctypes.POINTER(wintypes.HANDLE)]
    find_next.restype = wintypes.BOOL
    find_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE)]
    find_close.restype = wintypes.BOOL
    find_close.argtypes = [wintypes.HANDLE]
    get_info.restype = wintypes.DWORD
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(BLUETOOTH_RADIO_INFO)]

    from ctypes import byref
    params = BLUETOOTH_FIND_RADIO_PARAMS(ctypes.sizeof(BLUETOOTH_FIND_RADIO_PARAMS))
    h_radio = wintypes.HANDLE()
    h_find = find_first(byref(params), byref(h_radio))
    if not h_find:
        return [], "Радиомодуль не найден (Bluetooth выключен?)"
    handles = [h_radio.value]
    while find_next(h_find, byref(h_radio)):
        handles.append(h_radio.value)
    find_close(h_find)

    CloseHandle = ctypes.windll.kernel32.CloseHandle
    for h in handles:
        info = BLUETOOTH_RADIO_INFO(ctypes.sizeof(BLUETOOTH_RADIO_INFO))
        rc = get_info(wintypes.HANDLE(h), byref(info))
        if rc == ERROR_SUCCESS:
            radios.append({"name": info.szName or "Bluetooth Radio",
                           "address": format_bt_addr(info.address),
                           "cod": f"0x{info.ulClassofDevice:08X}",
                           "manufacturer": info.manufacturer})
        CloseHandle(wintypes.HANDLE(h))
    return radios, ""


def scan_classic(duration_s: float = 12.0) -> tuple[list[dict], str]:
    """Активное сканирование BR/EDR. Длится ~duration_s секунд."""
    devices: dict[str, dict] = {}
    try:
        api = _load_api()
    except OSError as e:
        return paired_fallback(f"Нет BluetoothAPIs: {e}")
    try:
        find_first = api.BluetoothFindFirstDevice
        find_next = api.BluetoothFindNextDevice
        find_close = api.BluetoothFindDeviceClose
        get_info = api.BluetoothGetDeviceInfo
        find_first.restype = wintypes.HANDLE
        find_first.argtypes = [ctypes.POINTER(BLUETOOTH_DEVICE_SEARCH_PARAMS),
                               ctypes.POINTER(BLUETOOTH_DEVICE_INFO)]
        find_next.restype = wintypes.BOOL
        find_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(BLUETOOTH_DEVICE_INFO)]
        find_close.restype = wintypes.BOOL
        find_close.argtypes = [wintypes.HANDLE]
        get_info.restype = wintypes.DWORD
    except AttributeError as e:
        return paired_fallback(f"API недоступен: {e}")

    from ctypes import byref
    search = BLUETOOTH_DEVICE_SEARCH_PARAMS()
    search.dwSize = ctypes.sizeof(search)
    search.fReturnAuthenticated = True
    search.fReturnRemembered = True
    search.fReturnUnknown = True
    search.fReturnConnected = True
    search.fIssueInquiry = True
    search.cTimeoutMultiplier = max(1, min(48, int(round(duration_s / 1.28))))
    search.hRadio = None

    info = BLUETOOTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(info)
    h_find = find_first(byref(search), byref(info))
    if not h_find:
        return paired_fallback("Устройства не найдены через inquiry — показан список известных")

    deadline = time.time() + duration_s + 4
    first = True
    while True:
        if not first:
            info = BLUETOOTH_DEVICE_INFO()
            info.dwSize = ctypes.sizeof(info)
            ok = find_next(h_find, byref(info))
            if not ok:
                err = ctypes.GetLastError()
                if err == ERROR_NO_MORE_ITEMS or time.time() > deadline:
                    break
                # устройство могло уйти — обновим данные и продолжим
                time.sleep(0.2)
                if time.time() > deadline:
                    break
                continue
        first = False
        # освежить поля connected/remembered
        try:
            get_info(None, byref(info))
        except OSError:
            pass
        addr = format_bt_addr(info.Address)
        major, minor, services = decode_cod(info.ulClassofDevice)
        name = info.szName or "(без имени)"
        prev = devices.get(addr)
        devices[addr] = {
            "name": name if name != "(без имени)" else (prev["name"] if prev and prev["name"] != "(без имени)" else name),
            "address": addr,
            "cod": f"0x{info.ulClassofDevice:08X}",
            "major": major,
            "minor": minor,
            "services": services,
            "connected": "Да" if info.fConnected else "Нет",
            "remembered": "Да" if info.fRemembered else "Нет",
            "authenticated": "Да" if info.fAuthenticated else "Нет",
            "last_seen": _systemtime_to_str(info.stLastSeen),
            "last_used": _systemtime_to_str(info.stLastUsed),
        }
        if time.time() > deadline:
            break
    try:
        find_close(h_find)
    except OSError:
        pass
    rows = sorted(devices.values(),
                  key=lambda d: (d["connected"] != "Да", d["name"] or "~~~"))
    note = "" if rows else "Ничего не найдено — проверьте, что Bluetooth включён"
    return rows, note


def paired_fallback(reason: str = "") -> tuple[list[dict], str]:
    """Запасной вариант: Bluetooth-устройства из диспетчера устройств (PnP)."""
    rows: list[dict] = []
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice -Class Bluetooth | Select-Object FriendlyName, Status, InstanceId | Format-Table -HideTableHeaders -AutoSize | Out-String -Width 4096"],
            capture_output=True, timeout=20, creationflags=creationflags)
        text = p.stdout.decode("utf-8", errors="ignore") or p.stderr.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            s = line.strip()
            if not s or "FriendlyName" in s or "---" in s:
                continue
            parts = s.split(None, 1)
            if len(parts) < 2:
                continue
            rows.append({"name": parts[1] if len(parts) > 1 else parts[0],
                         "address": "—", "cod": "—", "major": "—", "minor": parts[0],
                         "services": "—", "connected": "—", "remembered": "—",
                         "authenticated": "—", "last_seen": "—", "last_used": "—"})
    except Exception as e:
        return [], f"{reason} Fallback тоже не удался: {e}"
    msg = (reason + " " if reason else "") + f"Показаны PnP-устройства ({len(rows)})."
    return rows, msg.strip()
