"""BLE сканер через bleak: рекламационные данные + опциональный GATT."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

# Bluetooth SIG Company IDs (популярные)
COMPANIES = {
    0x004C: "Apple", 0x0006: "Microsoft", 0x00E0: "Google",
    0x0075: "Samsung", 0x038F: "Xiaomi", 0x0499: "Ruuvi",
    0x0059: "Nordic", 0x0087: "Fitbit", 0x0157: "Huawei",
    0x02D0: "Tile", 0x004D: "Sony", 0x0002: "Intel",
    0x000F: "Broadcom", 0x001D: "Qualcomm", 0x0171: "Amazon",
    0x0131: "LG", 0x00A0: "Garmin", 0x00B0: "Jabra",
    0x05D0: "Dell", 0x02DA: "AstraZeneca",
}


def _mfg_hex(mfg: dict) -> str:
    parts = []
    for cid, data in (mfg or {}).items():
        try:
            parts.append(f"0x{cid:04X}:{(bytes(data).hex() or 'empty')}")
        except (TypeError, ValueError):
            parts.append(f"0x{cid:04X}:?")
    return "; ".join(parts) if parts else "—"


def _mfg_names(mfg: dict) -> str:
    names = [COMPANIES.get(cid, f"0x{cid:04X}") for cid in (mfg or {})]
    return ", ".join(names) if names else "—"


async def _scan_async(duration_s: float, on_update=None) -> list[dict]:
    from bleak import BleakScanner

    found: dict[str, dict] = {}

    def _cb(device, adv):
        try:
            addr = device.address or "?"
            mfg = dict(getattr(adv, "manufacturer_data", {}) or {})
            try:
                svc_data = {str(k): bytes(v).hex() for k, v in
                            (getattr(adv, "service_data", {}) or {}).items()}
            except (TypeError, ValueError):
                svc_data = {}
            name = (getattr(adv, "local_name", "") or device.name or "").strip() or "(без имени)"
            entry = found.get(addr)
            rssi = getattr(device, "rssi", None)
            if entry is None:
                entry = {"address": addr, "rssi_history": [], "count": 0,
                         "first_seen": datetime.now().strftime("%H:%M:%S")}
                found[addr] = entry
            entry.update({
                "name": name if name != "(без имени)" else entry.get("name", name),
                "rssi": rssi if rssi not in (None, 0) else entry.get("rssi"),
                "tx_power": getattr(adv, "tx_power", None),
                "connectable": getattr(adv, "connectable", None),
                "local_name": getattr(adv, "local_name", "") or "",
                "manufacturer_hex": _mfg_hex(mfg),
                "company": _mfg_names(mfg),
                "service_uuids": ", ".join(str(u) for u in (getattr(adv, "service_uuids", []) or [])) or "—",
                "service_uuids_count": len(getattr(adv, "service_uuids", []) or []),
                "service_data": "; ".join(f"{k}={v}" for k, v in svc_data.items()) or "—",
                "last_seen": datetime.now().strftime("%H:%M:%S"),
                "count": entry.get("count", 0) + 1,
            })
            if rssi not in (None, 0):
                entry["rssi_history"].append(int(rssi))
                entry["rssi_history"] = entry["rssi_history"][-50:]
            if on_update:
                try:
                    on_update(dict(entry))
                except Exception:
                    pass
        except Exception:
            pass

    scanner = BleakScanner(detection_callback=_cb, scanning_mode="active")
    await scanner.start()
    try:
        await asyncio.sleep(duration_s)
    finally:
        await scanner.stop()
    rows = sorted(found.values(),
                  key=lambda e: (e.get("rssi") is None, -(e.get("rssi") or -999)))
    return rows


def scan_ble(duration_s: float = 10.0, on_update=None) -> tuple[list[dict], str]:
    """Синхронная обёртка. Возвращает (устройства, сообщение)."""
    try:
        rows = asyncio.run(_scan_async(duration_s, on_update))
    except Exception as e:
        msg = str(e)
        if "Bluetooth" in msg or "bluetooth" in msg.lower():
            msg += " — проверьте, что Bluetooth включён и demographic разрешён доступ."
        return [], f"BLE-сканирование не удалось: {msg}"
    if not rows:
        return [], "BLE-устройства не найдены — поднесите устройство ближе / включите рекламу."
    return rows, ""


def describe_services(services) -> list[dict]:
    """Чистое описание сервисов/характеристик/дескрипторов (без IO).

    Общее для GATT-анализа и BLE Lab, чтобы дерево везде одинаковое.
    """
    from .uuids import uuid_name

    out = []
    for svc in services:
        chars = []
        for ch in svc.characteristics:
            descs = []
            for d in getattr(ch, "descriptors", []) or []:
                descs.append({"uuid": str(d.uuid), "name": uuid_name(str(d.uuid)),
                              "handle": getattr(d, "handle", None)})
            chars.append({"uuid": str(ch.uuid), "name": uuid_name(str(ch.uuid)),
                          "handle": getattr(ch, "handle", None),
                          "properties": list(getattr(ch, "properties", []) or []),
                          "descriptors": descs})
        out.append({"uuid": str(svc.uuid), "name": uuid_name(str(svc.uuid)),
                    "handle": getattr(svc, "handle", None),
                    "characteristics": chars})
    return out


async def _gatt_async(address: str, timeout: float = 20.0) -> dict:
    from bleak import BleakClient

    from .uuids import fmt_data, parse_value, short16

    info: dict = {"address": address, "services": []}
    async with BleakClient(address, timeout=timeout) as client:
        info["connected"] = bool(client.is_connected)
        try:
            info["mtu"] = getattr(client, "mtu_size", None)
        except Exception:
            pass
        struct = describe_services(client.services)
        for svc in struct:
            for ch in svc["characteristics"]:
                props = ch["properties"]
                if "read" in props:
                    try:
                        raw = bytes(await client.read_gatt_char(ch["uuid"]))
                        ch["hex"] = fmt_data(raw)
                        parsed = parse_value(ch["uuid"], raw)
                        if parsed:
                            ch["parsed"] = parsed
                    except Exception as e:
                        ch["hex"] = f"<не прочитано: {e}>"
                for d in ch["descriptors"]:
                    if d.get("handle") is None:
                        continue
                    try:
                        draw = bytes(await client.read_gatt_descriptor(int(d["handle"])))
                        d["hex"] = fmt_data(draw)
                        parsed = parse_value(d["uuid"], draw)
                        if parsed:
                            d["parsed"] = parsed
                    except Exception as e:
                        d["hex"] = f"<не прочитано: {e}>"
                s16 = (str(ch["uuid"]).split("-")[0].lower()
                       if short16(ch["uuid"]) is not None else "")
                if s16 == "2a00" and "parsed" in ch:
                    info["name"] = ch["parsed"]
                elif s16 == "2a29" and "parsed" in ch:
                    info["manufacturer"] = ch["parsed"]
                elif s16 == "2a24" and "parsed" in ch:
                    info["model"] = ch["parsed"]
        info["services"] = struct
    return info


def read_gatt(address: str, timeout: float = 15.0) -> tuple[dict | None, str]:
    try:
        info = asyncio.run(_gatt_async(address, timeout))
    except Exception as e:
        return None, f"GATT не удался: {e}"
    return info, ""


def ble_supported() -> tuple[bool, str]:
    try:
        import bleak  # noqa: F401
        return True, ""
    except ImportError:
        return False, "Пакет bleak не установлен (pip install bleak)"
