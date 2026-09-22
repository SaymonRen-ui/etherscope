"""Wi-Fi сканер для Windows через WLAN API (wlanapi.dll) + fallback на netsh.

Основной путь: WlanScan (принудительный активный скан, как делает сам
Windows) -> WlanGetAvailableNetworkList (сети) -> WlanGetNetworkBssList
(точки: BSSID, РЕАЛЬНЫЙ RSSI в дБм, частота, PHY, скорости).

Поля строки: SSID, BSSID, сигнал %, дБм (настоящие), канал, частота,
диапазон, радио (802.11...), аутентификация, шифрование, тип сети,
скорости, производитель оборудования по OUI, подключена ли.
"""

from __future__ import annotations

import ctypes
import locale
import re
import subprocess
import time
from ctypes import wintypes

from .net_utils import (
    freq_to_band,
    oui_lookup,
    security_short,
    signal_pct_to_dbm,
)

ERROR_SUCCESS = 0


class ScanCancelled(Exception):
    """Сканирование остановлено пользователем (кнопка Стоп)."""

# ---------------- ctypes-структуры WLAN API ----------------

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class DOT11_SSID(ctypes.Structure):
    _fields_ = [("uSSIDLength", wintypes.DWORD),
                ("ucSSID", ctypes.c_ubyte * 32)]


class WLAN_AVAILABLE_NETWORK(ctypes.Structure):
    _fields_ = [
        ("strProfileName", wintypes.WCHAR * 256),
        ("dot11Ssid", DOT11_SSID),
        ("dot11BssType", wintypes.DWORD),
        ("uNumberOfBssids", wintypes.DWORD),
        ("bNetworkConnectable", wintypes.BOOL),
        ("wlanNotConnectableReason", wintypes.DWORD),
        ("uNumberOfPhyTypes", wintypes.DWORD),
        ("dot11PhyTypes", wintypes.DWORD * 8),
        ("bMorePhyTypes", wintypes.BOOL),
        ("wlanSignalQuality", wintypes.DWORD),
        ("bSecurityEnabled", wintypes.BOOL),
        ("dot11DefaultAuthAlgorithm", wintypes.DWORD),
        ("dot11DefaultCipherAlgorithm", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("dwReserved", wintypes.DWORD),
    ]


class WLAN_RATE_SET(ctypes.Structure):
    _fields_ = [("uRateSetLength", wintypes.DWORD),
                ("usRateSet", wintypes.USHORT * 126)]


class WLAN_BSS_ENTRY(ctypes.Structure):
    _fields_ = [
        ("dot11Ssid", DOT11_SSID),
        ("uPhyId", wintypes.DWORD),
        ("dot11Bssid", ctypes.c_ubyte * 6),
        ("dot11BssType", wintypes.DWORD),
        ("dot11BssPhyType", wintypes.DWORD),
        ("lRssi", ctypes.c_long),
        ("uLinkQuality", wintypes.DWORD),
        ("bInRegDomain", ctypes.c_ubyte),
        ("usBeaconPeriod", wintypes.USHORT),
        ("ullTimestamp", ctypes.c_ulonglong),
        ("ullHostTimestamp", ctypes.c_ulonglong),
        ("usCapabilityInformation", wintypes.USHORT),
        ("ulChCenterFrequency", wintypes.DWORD),
        ("wlanRateSet", WLAN_RATE_SET),
        ("ulIeOffset", wintypes.DWORD),
        ("ulIeSize", wintypes.DWORD),
    ]


SZ_AVAIL = ctypes.sizeof(WLAN_AVAILABLE_NETWORK)   # 628
SZ_BSS = ctypes.sizeof(WLAN_BSS_ENTRY)             # 360
SZ_IFACE_ENTRY = 16 + 256 * 2 + 4                  # GUID + descr + state = 532

# ---------------- Справочники ----------------

_AUTH = {
    1: "Open", 2: "Shared", 3: "WPA", 4: "WPA-Personal",
    5: "WPA-None", 6: "WPA2-Enterprise", 7: "WPA2-Personal",
    8: "IHV", 9: "WPA3", 10: "WPA3-SAE", 11: "OWE", 12: "WPA3-Enterprise",
}
_CIPHER = {
    0: "None", 1: "WEP-40", 2: "TKIP", 4: "CCMP",
    5: "WEP-104", 6: "BIP", 8: "GCMP", 9: "GCMP-256",
    10: "CCMP-256", 11: "BIP-GMAC-128", 12: "BIP-GMAC-256",
    13: "BIP-CMAC-256", 0x100: "IHV",
}
_PHY = {
    1: "802.11b (FHSS)", 2: "802.11b", 3: "802.11 (IR)", 4: "802.11a",
    5: "802.11b", 6: "802.11g", 7: "802.11n", 8: "802.11ac",
    10: "802.11ad", 11: "802.11ax", 12: "802.11be",
}
_BSS_TYPE = {1: "Infrastructure", 2: "Ad-hoc", 3: "Any"}


def _fmt_mac(bssid) -> str:
    return ":".join(f"{b:02X}" for b in bytes(bssid))


def _decode_ssid(raw: bytes) -> str:
    s = bytes(raw).decode("utf-8", errors="replace").strip()
    return s if s else "<скрытая сеть>"


def freq_mhz_to_channel(freq: int | None) -> int | None:
    if not freq:
        return None
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        ch = (freq - 2412) // 5 + 1
        return ch if (freq - 2412) % 5 == 0 else None
    if 5000 <= freq < 5900 and (freq - 5000) % 5 == 0:
        return (freq - 5000) // 5
    if 5925 <= freq <= 7125 and (freq - 5950) % 5 == 0:
        return (freq - 5950) // 5
    return None


def _load_wlanapi():
    api = ctypes.WinDLL("wlanapi")
    api.WlanOpenHandle.argtypes = [wintypes.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.DWORD),
                                   ctypes.POINTER(wintypes.HANDLE)]
    api.WlanOpenHandle.restype = wintypes.DWORD
    api.WlanEnumInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_void_p)]
    api.WlanEnumInterfaces.restype = wintypes.DWORD
    api.WlanScan.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID),
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    api.WlanScan.restype = wintypes.DWORD
    api.WlanGetAvailableNetworkList.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID),
                                                wintypes.DWORD, ctypes.c_void_p,
                                                ctypes.POINTER(ctypes.c_void_p)]
    api.WlanGetAvailableNetworkList.restype = wintypes.DWORD
    api.WlanGetNetworkBssList.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID),
                                          ctypes.POINTER(DOT11_SSID), wintypes.DWORD,
                                          wintypes.BOOL, ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p)]
    api.WlanGetNetworkBssList.restype = wintypes.DWORD
    api.WlanQueryInterface.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID),
                                       wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.POINTER(wintypes.DWORD),
                                       ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.c_void_p]
    api.WlanQueryInterface.restype = wintypes.DWORD
    api.WlanFreeMemory.argtypes = [ctypes.c_void_p]
    api.WlanCloseHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    return api


def _pick_interface(api, client) -> GUID | None:
    plist = ctypes.c_void_p()
    if api.WlanEnumInterfaces(client, None, ctypes.byref(plist)) != ERROR_SUCCESS:
        return None
    try:
        base = plist.value
        if not base:
            return None
        n = ctypes.cast(base, ctypes.POINTER(wintypes.DWORD))[0]
        if n < 1:
            return None
        best = None
        best_state = -1
        for i in range(n):
            off = 8 + i * SZ_IFACE_ENTRY
            guid = GUID.from_buffer_copy(ctypes.string_at(base + off, 16))
            state = ctypes.cast(base + off + 528,
                                ctypes.POINTER(wintypes.DWORD))[0]
            if state > best_state:
                best_state = state
                best = guid
        return best
    finally:
        api.WlanFreeMemory(plist)


def _connected_bssid(api, client, guid: GUID) -> str | None:
    """BSSID текущего подключения (opcode 7 = current_connection)."""
    size = wintypes.DWORD()
    pdata = ctypes.c_void_p()
    try:
        if api.WlanQueryInterface(client, ctypes.byref(guid), 7, None,
                                   ctypes.byref(size), ctypes.byref(pdata),
                                   None) != ERROR_SUCCESS:
            return None
        base = pdata.value
        if not base:
            return None
        state = ctypes.cast(base, ctypes.POINTER(wintypes.DWORD))[0]
        if state != 1:  # 1 = connected
            return None
        raw = ctypes.string_at(base + 8 + 512 + 36 + 4, 6)
        return ":".join(f"{b:02X}" for b in raw)
    except (OSError, ValueError):
        return None
    finally:
        try:
            if pdata.value:
                api.WlanFreeMemory(pdata)
        except OSError:
            pass


def _avail_list(api, client, guid: GUID) -> list[dict]:
    plist = ctypes.c_void_p()
    if api.WlanGetAvailableNetworkList(client, ctypes.byref(guid), 0, None,
                                        ctypes.byref(plist)) != ERROR_SUCCESS:
        return []
    try:
        base = plist.value
        if not base:
            return []
        n = ctypes.cast(base, ctypes.POINTER(wintypes.DWORD))[0]
        out = []
        for i in range(n):
            raw = ctypes.string_at(base + 8 + i * SZ_AVAIL, SZ_AVAIL)
            e = WLAN_AVAILABLE_NETWORK.from_buffer_copy(raw)
            ssid_len = int(e.dot11Ssid.uSSIDLength)
            ssid_raw = bytes(e.dot11Ssid.ucSSID[:max(0, min(32, ssid_len))])
            out.append({
                "ssid": _decode_ssid(ssid_raw),
                "ssid_raw": ssid_raw,
                "profile": e.strProfileName or "",
                "bss_type": _BSS_TYPE.get(int(e.dot11BssType),
                                          str(int(e.dot11BssType))),
                "connectable": bool(e.bNetworkConnectable),
                "signal_pct": int(e.wlanSignalQuality),
                "auth": _AUTH.get(int(e.dot11DefaultAuthAlgorithm),
                                  f"auth={int(e.dot11DefaultAuthAlgorithm)}"),
                "encryption": _CIPHER.get(int(e.dot11DefaultCipherAlgorithm),
                                          f"cipher={int(e.dot11DefaultCipherAlgorithm)}"),
                "connected": bool(int(e.dwFlags) & 0x01),
                "has_profile": bool(int(e.dwFlags) & 0x02),
            })
        return out
    finally:
        api.WlanFreeMemory(plist)


def _bss_list_all(api, client, guid: GUID) -> list[dict]:
    """Весь BSS-список одним запросом (SSID=NULL).

    По конкретному SSID драйвер Realtek отдаёт пустой список, а общий —
    полный: BSSID, настоящий RSSI/дБм, частота, PHY, скорости, SSID точки.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for secured in (False, True):
        plist = ctypes.c_void_p()
        if api.WlanGetNetworkBssList(client, ctypes.byref(guid), None, 3,
                                      secured, None,
                                      ctypes.byref(plist)) != ERROR_SUCCESS:
            continue
        try:
            base = plist.value
            if not base:
                continue
            n = ctypes.cast(base + 4, ctypes.POINTER(wintypes.DWORD))[0]
            for i in range(n):
                raw = ctypes.string_at(base + 8 + i * SZ_BSS, SZ_BSS)
                e = WLAN_BSS_ENTRY.from_buffer_copy(raw)
                mac = _fmt_mac(e.dot11Bssid)
                if mac in seen:
                    continue
                seen.add(mac)
                freq = int(e.ulChCenterFrequency) // 1000  # кГц -> МГц
                basic, other = [], []
                for r in range(int(e.wlanRateSet.uRateSetLength)):
                    val = int(e.wlanRateSet.usRateSet[r])
                    txt = f"{(val & 0x7FFF) / 2:g}"
                    (basic if val & 0x8000 else other).append(txt)
                ssid_len = max(0, min(32, int(e.dot11Ssid.uSSIDLength)))
                out.append({
                    "ssid": _decode_ssid(bytes(e.dot11Ssid.ucSSID[:ssid_len])),
                    "bssid": mac,
                    "signal_pct": max(0, min(100, int(e.uLinkQuality))),
                    "dbm": int(e.lRssi),
                    "freq_mhz": freq or None,
                    "radio": _PHY.get(int(e.dot11BssPhyType),
                                      f"phy={int(e.dot11BssPhyType)}"),
                    "basic_rates": ", ".join(basic) or "—",
                    "other_rates": ", ".join(other) or "—",
                })
        finally:
            api.WlanFreeMemory(plist)
    return out


def _sleep_chunks(total: float, is_cancelled=None, step: float = 0.5):
    """Сон кусками: Стоп срабатывает максимум через step секунд."""
    end = time.time() + max(0.0, total)
    while True:
        if is_cancelled is not None and is_cancelled():
            raise ScanCancelled("остановлено пользователем")
        now = time.time()
        if now >= end:
            return
        time.sleep(min(step, end - now))


def _scan_wlanapi(scan_wait: float = 10.0, is_cancelled=None) -> list[dict]:
    api = _load_wlanapi()
    client = wintypes.HANDLE()
    negotiated = wintypes.DWORD()
    if api.WlanOpenHandle(2, None, ctypes.byref(negotiated),
                          ctypes.byref(client)) != ERROR_SUCCESS:
        raise OSError("WlanOpenHandle failed")
    try:
        guid = _pick_interface(api, client)
        if guid is None:
            raise OSError("Нет Wi-Fi интерфейсов")
        try:
            api.WlanScan(client, ctypes.byref(guid), None, None, None)
        except OSError:
            pass
        # Опрашиваем список пока не появится (быстрые машины — 2-3 с),
        # но не дольше scan_wait
        nets = []
        end = time.time() + max(3.0, scan_wait)
        while not nets and time.time() < end:
            _sleep_chunks(2.0, is_cancelled)
            nets = _avail_list(api, client, guid)
        rows: dict[str, dict] = {}
        by_ssid = {net["ssid"]: net for net in nets}
        connected_ssids = {net["ssid"] for net in nets if net["connected"]}
        for b in _bss_list_all(api, client, guid):
            net = by_ssid.get(b["ssid"], {})
            rows[b["bssid"]] = {
                "ssid": b["ssid"],
                "network_type": net.get("bss_type", "—"),
                "auth": net.get("auth", "—"),
                "encryption": net.get("encryption", "—"),
                **{k: v for k, v in b.items() if k != "ssid"},
            }
        for net in nets:
            # сеть видна, но BSS-деталей драйвер не отдал — строка-заглушка
            if net["ssid"] not in {r["ssid"] for r in rows.values()}:
                rows[f"SSID::{net['ssid']}"] = {
                    "ssid": net["ssid"], "bssid": "—",
                    "signal_pct": net["signal_pct"],
                    "dbm": signal_pct_to_dbm(net["signal_pct"]),
                    "channel": None, "freq_mhz": None,
                    "band": "—", "radio": "—",
                    "auth": net["auth"], "encryption": net["encryption"],
                    "network_type": net["bss_type"],
                    "basic_rates": "—", "other_rates": "—",
                }
        try:
            conn = _connected_bssid(api, client, guid)
        except OSError:
            conn = None
        for bssid, r in rows.items():
            r.setdefault("ssid", "—")
            r.setdefault("bssid", bssid if not bssid.startswith("SSID::") else "—")
            r["channel"] = freq_mhz_to_channel(r.get("freq_mhz"))
            r["band"] = freq_to_band(r.get("freq_mhz"))
            r.setdefault("radio", "—")
            r.setdefault("auth", "—")
            r.setdefault("encryption", "—")
            r.setdefault("network_type", "—")
            r.setdefault("basic_rates", "—")
            r.setdefault("other_rates", "—")
            r["vendor"] = oui_lookup(r.get("bssid", ""))
            r["security"] = security_short(r.get("auth", ""), r.get("encryption", ""))
            r["connected_here"] = ("Да" if (conn and r.get("bssid") == conn)
                                     or r.get("ssid") in connected_ssids else "Нет")
            sig = r.get("signal_pct")
            try:
                r["signal_bar"] = max(0, min(100, int(sig))) if sig is not None else 0
            except (TypeError, ValueError):
                r["signal_bar"] = 0
        out = sorted(rows.values(),
                     key=lambda x: (x.get("signal_pct") is None, -(x.get("signal_pct") or -1)))
        return out
    finally:
        try:
            api.WlanCloseHandle(client, None)
        except OSError:
            pass


# ---------------- netsh fallback ----------------

def _decode_netsh(raw: bytes) -> str:
    for enc in ("utf-8", "cp866", "cp1251", locale.getpreferredencoding(False)):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError, TypeError):
            continue
    return raw.decode("utf-8", errors="ignore")


def _run_netsh(*args: str) -> str:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run(
        ["netsh", *args],
        capture_output=True,
        timeout=30,
        creationflags=creationflags,
    )
    return _decode_netsh(p.stdout + b"\n" + p.stderr)


def get_interfaces() -> list[dict]:
    """Информация о Wi-Fi интерфейсах (подключённая сеть, свой MAC и т.д.)."""
    try:
        text = _run_netsh("wlan", "show", "interfaces")
    except Exception as e:
        return [{"error": str(e)}]
    interfaces: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        s = line.strip()
        if not s or ":" not in s:
            continue
        if re.match(r"^(Имя|Name)\s*:", s):
            if current:
                interfaces.append(current)
            current = {}
        if current is None:
            continue
        key, _, val = s.partition(":")
        current[key.strip()] = val.strip()
    if current:
        interfaces.append(current)
    return interfaces


def get_drivers() -> dict:
    """Возможности драйвера."""
    try:
        return {"raw": _run_netsh("wlan", "show", "drivers")}
    except Exception as e:
        return {"raw": f"Ошибка: {e}"}


def _parse_networks(text: str) -> list[dict]:
    """Парсит 'netsh wlan show networks mode=bssid' (RU и EN вывод)."""
    networks: list[dict] = []
    cur_ssid: dict | None = None
    cur_bssid: dict | None = None

    def flush_bssid():
        nonlocal cur_bssid
        if cur_ssid is not None and cur_bssid is not None:
            row = dict(cur_ssid)
            row.update(cur_bssid)
            networks.append(row)
        cur_bssid = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = re.match(r"^SSID\s*\d*\s*:\s*(.*)$", line, re.IGNORECASE)
        if m:
            flush_bssid()
            cur_ssid = {"ssid": m.group(1).strip() or "<скрытая сеть>"}
            continue
        if cur_ssid is None:
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        k = key.strip().lower()
        v = val.strip()

        def is_key(*variants: str) -> bool:
            for var in variants:
                if var in k:
                    return True
            return False

        if is_key("network type", "тип сети", "тип радиосети"):
            cur_ssid["network_type"] = v
        elif is_key("authentication", "проверка подлинности", "аутентификация", "проверка"):
            cur_ssid["auth"] = v
        elif is_key("encryption", "шифрование"):
            cur_ssid["encryption"] = v
        elif re.match(r"^bssid\s*\d*$", k):
            flush_bssid()
            cur_bssid = {"bssid": v.upper()}
        elif cur_bssid is not None:
            if is_key("signal", "сигнал"):
                mm = re.search(r"(\d+)", v)
                pct = int(mm.group(1)) if mm else None
                cur_bssid["signal_pct"] = pct
                cur_bssid["dbm"] = signal_pct_to_dbm(pct)
            elif is_key("radio type", "тип радио", "радио"):
                cur_bssid["radio"] = v
            elif is_key("channel", "канал"):
                mm = re.search(r"(\d+)", v)
                ch = int(mm.group(1)) if mm else None
                cur_bssid["channel"] = ch
                cur_bssid["freq_mhz"] = None
                cur_bssid["band"] = "—"
                if ch:
                    from .net_utils import channel_to_freq_mhz
                    freq = channel_to_freq_mhz(ch)
                    cur_bssid["freq_mhz"] = freq
                    cur_bssid["band"] = freq_to_band(freq)
            elif is_key("basic rates", "базовые скорости", "основные скорости"):
                cur_bssid["basic_rates"] = v
            elif is_key("other rates", "другие скорости", "прочие скорости", "дополнительные скорости"):
                cur_bssid["other_rates"] = v
    flush_bssid()

    for row in networks:
        row.setdefault("ssid", "—")
        row.setdefault("bssid", "—")
        row.setdefault("signal_pct", None)
        row.setdefault("dbm", signal_pct_to_dbm(row.get("signal_pct")))
        row.setdefault("channel", None)
        row.setdefault("freq_mhz", None)
        row.setdefault("band", "—")
        row.setdefault("radio", "—")
        row.setdefault("auth", "—")
        row.setdefault("encryption", "—")
        row.setdefault("network_type", "—")
        row.setdefault("basic_rates", "—")
        row.setdefault("other_rates", "—")
        row["vendor"] = oui_lookup(row.get("bssid", ""))
        row["security"] = security_short(row.get("auth", ""), row.get("encryption", ""))
        sig = row.get("signal_pct")
        try:
            row["signal_bar"] = max(0, min(100, int(sig))) if sig is not None else 0
        except (TypeError, ValueError):
            row["signal_bar"] = 0
    networks.sort(key=lambda r: (r.get("signal_pct") is None, -(r.get("signal_pct") or -1)))
    return networks


def _scan_netsh(passes: int = 2, pause_s: float = 3.0, is_cancelled=None) -> list[dict]:
    merged: dict[str, dict] = {}
    for i in range(max(1, passes)):
        if is_cancelled is not None and is_cancelled():
            raise ScanCancelled("остановлено пользователем")
        try:
            text = _run_netsh("wlan", "show", "networks", "mode=bssid")
        except Exception:
            text = ""
        for row in _parse_networks(text):
            bssid = (row.get("bssid") or "").upper()
            if not bssid or bssid == "—":
                continue
            prev = merged.get(bssid)
            cur_sig = row.get("signal_pct")
            if prev is None or (cur_sig is not None
                                and (prev.get("signal_pct") is None
                                     or cur_sig > prev.get("signal_pct"))):
                merged[bssid] = row
        if i < passes - 1:
            _sleep_chunks(max(0.0, pause_s), is_cancelled)
    return sorted(merged.values(),
                  key=lambda r: (r.get("signal_pct") is None, -(r.get("signal_pct") or -1)))


def scan_wifi(scan_wait: float = 10.0, is_cancelled=None) -> tuple[list[dict], str]:
    """Сканирование Wi-Fi.

    Основной путь — WLAN API с принудительным активным сканом
    (те же данные, что видит штатный список сетей Windows):
    настоящие дБм, частоты, все BSSID.
    При недоступности API — fallback на netsh.
    is_cancelled() -> bool: Стоп срабатывает в пределах ~0.5 с.
    """
    try:
        rows = _scan_wlanapi(scan_wait, is_cancelled)
        if rows:
            return rows, ""
    except ScanCancelled:
        raise
    except Exception:
        pass
    try:
        return _scan_netsh(is_cancelled=is_cancelled), "WLAN API недоступен — показан кэш netsh"
    except ScanCancelled:
        raise
    except Exception as e:
        return [], f"Wi-Fi сканирование не удалось: {e}"
