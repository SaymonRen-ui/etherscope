"""Стандартные Bluetooth SIG UUID (16-бит) + разбор известных значений GATT."""

from __future__ import annotations

BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"

SERVICES = {
    0x1800: "Generic Access", 0x1801: "Generic Attribute",
    0x1802: "Immediate Alert", 0x1803: "Link Loss", 0x1804: "Tx Power",
    0x1805: "Current Time", 0x1806: "Reference Time Update",
    0x1807: "Next DST Change", 0x1808: "Glucose", 0x1809: "Health Thermometer",
    0x180A: "Device Information", 0x180D: "Heart Rate",
    0x180E: "Human Interface Device", 0x180F: "Battery",
    0x1810: "Blood Pressure", 0x1812: "Human Interface Device",
    0x1813: "Scan Parameters", 0x1814: "Running Speed and Cadence",
    0x1815: "Automation IO", 0x1816: "Cycling Speed and Cadence",
    0x1818: "Cycling Power", 0x1819: "Location and Navigation",
    0x181A: "Environmental Sensing", 0x181B: "Body Composition",
    0x181C: "User Data", 0x181D: "Weight Scale", 0x181E: "Bond Management",
    0x1820: "Internet Protocol Support", 0x1821: "Indoor Positioning",
    0x1822: "Pulse Oximeter", 0x1823: "HTTP Proxy",
    0x1824: "Transport Discovery", 0x1825: "Object Transfer",
    0x1826: "Fitness Machine", 0x1827: "Mesh Provisioning",
    0x1828: "Mesh Proxy", 0x183B: "Binary Sensor", 0x183E: "Emergency Configuration",
}

CHARS = {
    0x2A00: "Device Name", 0x2A01: "Appearance",
    0x2A02: "Peripheral Privacy Flag", 0x2A03: "Reconnection Address",
    0x2A04: "Preferred Connection Parameters", 0x2A05: "Service Changed",
    0x2A06: "Alert Level", 0x2A07: "Tx Power Level",
    0x2A08: "Date Time", 0x2A0B: "Time with DST", 0x2A0C: "Time Accuracy",
    0x2A0D: "Time Source", 0x2A0E: "Reference Time Information",
    0x2A0F: "Time Update Control Point", 0x2A10: "Time Update State",
    0x2A11: "Glucose Measurement", 0x2A19: "Battery Level",
    0x2A1C: "Temperature Measurement", 0x2A23: "System ID",
    0x2A24: "Model Number String", 0x2A25: "Serial Number String",
    0x2A26: "Firmware Revision String", 0x2A27: "Hardware Revision String",
    0x2A28: "Software Revision String", 0x2A29: "Manufacturer Name String",
    0x2A2A: "IEEE 11073 Certificate", 0x2A2B: "Current Time",
    0x2A35: "Blood Pressure Measurement", 0x2A37: "Heart Rate Measurement",
    0x2A38: "Body Sensor Location", 0x2A39: "Heart Rate Control Point",
    0x2A44: "Alert Notification Control Point", 0x2A45: "Unread Alert Status",
    0x2A46: "New Alert", 0x2A47: "Supported New Alert Category",
    0x2A48: "Supported Unread Alert Category",
    0x2A4A: "HID Information", 0x2A4B: "Report Map",
    0x2A4D: "Report", 0x2A4E: "Protocol Mode",
    0x2A4F: "Scan Interval Window", 0x2A50: "PnP ID",
    0x2A6D: "Pressure", 0x2A6E: "Temperature", 0x2A6F: "Humidity",
    0x2AA6: "Central Address Resolution", 0x2AB6: "LE GATT Security Levels",
    0x2ACC: "Fitness Machine Feature",
}

DESCS = {
    0x2900: "Characteristic Extended Properties",
    0x2901: "Characteristic User Description",
    0x2902: "Client Characteristic Configuration (CCCD)",
    0x2903: "Server Characteristic Configuration",
    0x2904: "Characteristic Presentation Format",
    0x2905: "Characteristic Aggregate Format",
    0x2906: "Valid Range", 0x2907: "External Report Reference",
    0x2908: "Report Reference",
    0x290C: "Environmental Sensing Measurement",
    0x290D: "Environmental Sensing Trigger Setting",
}


def short16(uuid_str: str) -> int | None:
    """16-битный номер для стандартных SIG UUID, иначе None."""
    try:
        u = uuid_str.lower()
    except (AttributeError, TypeError):
        return None
    if u.endswith(BASE_SUFFIX) and u.startswith("0000"):
        try:
            return int(u[4:8], 16)
        except ValueError:
            return None
    return None


def uuid_name(uuid_str: str) -> str:
    """Человекочитаемое имя UUID (custom для вендорных 128-битных)."""
    s = short16(uuid_str)
    if s is None:
        return "custom / vendor"
    for table in (SERVICES, CHARS, DESCS):
        if s in table:
            return table[s]
    return f"reserved 0x{s:04X}"


def fmt_data(data: bytes | bytearray | None, limit: int = 64) -> str:
    """HEX + ASCII одной строкой."""
    if not data:
        return "<пусто>"
    b = bytes(data)
    shown = b[:limit]
    hexs = " ".join(f"{x:02X}" for x in shown)
    asc = "".join(chr(x) if 32 <= x < 127 else "." for x in shown)
    tail = f" …(+{len(b) - limit} байт)" if len(b) > limit else ""
    return f"{hexs}  |{asc}|{tail}"


def parse_value(uuid_str: str, data: bytes | bytearray) -> str | None:
    """Разбор известных характеристик/дескрипторов. None если неизвестно."""
    s = short16(uuid_str)
    if s is None or not data:
        return None
    b = bytes(data)
    try:
        if s == 0x2A19 and len(b) >= 1:  # Battery Level
            return f"{b[0]} %"
        if s in (0x2A00, 0x2A24, 0x2A25, 0x2A26, 0x2A27,
                 0x2A28, 0x2A29, 0x2901):
            return b.decode("utf-8", errors="replace").strip()
        if s == 0x2A07 and len(b) >= 1:  # Tx Power
            v = b[0] if b[0] < 128 else b[0] - 256
            return f"{v} дБм"
        if s == 0x2A01 and len(b) >= 2:  # Appearance
            return f"0x{int.from_bytes(b[:2], 'little'):04X}"
        if s == 0x2A04 and len(b) >= 8:  # Preferred Connection Parameters
            vals = [int.from_bytes(b[i:i + 2], "little") for i in range(0, 8, 2)]
            return (f"min_int={vals[0] * 1.25:.2f}мс max_int={vals[1] * 1.25:.2f}мс "
                    f"latency={vals[2]} timeout={vals[3] * 10}мс")
        if s == 0x2A05 and len(b) >= 4:  # Service Changed
            st = int.from_bytes(b[:2], "little")
            en = int.from_bytes(b[2:4], "little")
            return f"handles 0x{st:04X}–0x{en:04X}"
        if s == 0x2A23 and len(b) >= 8:  # System ID
            return b.hex(":")
        if s == 0x2A2B and len(b) >= 10:  # Current Time
            y = int.from_bytes(b[:2], "little")
            return f"{y:04d}-{b[2]:02d}-{b[3]:02d} {b[4]:02d}:{b[5]:02d}:{b[6]:02d}"
        if s == 0x2A50 and len(b) >= 7:  # PnP ID
            vid = int.from_bytes(b[1:3], "little")
            pid = int.from_bytes(b[3:5], "little")
            ver = int.from_bytes(b[5:7], "little")
            return f"src={b[0]} VID=0x{vid:04X} PID=0x{pid:04X} ver=0x{ver:04X}"
        if s == 0x2A4A and len(b) >= 4:  # HID Information
            return (f"bcdHID=0x{int.from_bytes(b[:2], 'little'):04X} "
                    f"country={b[2]} flags=0x{b[3]:02X}")
        if s == 0x2902 and len(b) >= 2:  # CCCD
            v = int.from_bytes(b[:2], "little")
            on = []
            if v & 0x0001:
                on.append("notify ВКЛ")
            if v & 0x0002:
                on.append("indicate ВКЛ")
            if v & 0x0004:
                on.append("broadcast ВКЛ")
            return ", ".join(on) if on else "всё ВЫКЛ"
        if s == 0x2904 and len(b) >= 7:  # Presentation Format
            return (f"format={b[0]} exp={b[1] if b[1] < 128 else b[1] - 256} "
                    f"unit=0x{int.from_bytes(b[2:4], 'little'):04X}")
    except (IndexError, ValueError):
        return None
    return None
