"""Вкладки Wi-Fi / Bluetooth / BLE."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, ttk

import customtkinter as ctk

from scanner import ble as ble_mod
from scanner import bt_classic as bt_mod
from scanner import wifi as wifi_mod
from scanner.net_utils import export_csv, export_json, now_stamp
from ui.widgets import StatCard
from ui.theme import (
    ACCENT, ACCENT2, BAD, BG, BRIGHT, BTN2_BG, BTN2_HOVER,
    CARD, CARD2, GOOD, MUTED, SIDEBAR, TEXT, WARN,
    _fmt, resource_path,
)

WIFI_COLS = [
    ("ssid", "SSID", 170), ("bssid", "BSSID", 125), ("signal_pct", "Сигн.%", 60),
    ("dbm", "дБм", 55), ("channel", "Кан.", 50), ("freq_mhz", "МГц", 65),
    ("band", "Диапазон", 75), ("security", "Защита", 210),
    ("radio", "Radio", 85), ("vendor", "Оборудование", 130),
]
BT_COLS = [
    ("name", "Имя", 210), ("address", "Адрес", 125), ("major", "Класс", 130),
    ("minor", "Подкласс", 130), ("connected", "Подкл.", 60),
    ("remembered", "Запомн.", 70), ("authenticated", "Аутент.", 70),
    ("last_seen", "Визит", 140),
]
BLE_COLS = [
    ("name", "Имя", 190), ("address", "Адрес", 130), ("rssi", "RSSI", 60),
    ("tx_power", "TX", 55), ("company", "Компания", 140),
    ("service_uuids_count", "Срв.", 50), ("count", "Пак.", 55),
    ("last_seen", "Время", 80),
]



class RadioTabMixin:
    """Миксин для ScannerApp. Логика не менялась, только переезд."""

    def _build_radio_tab(self, key: str, title: str, cols, with_band_filter=False,
                         extra_buttons=None):
        tab = self.tabs.tab(title)
        # Карточки статистики
        cards = ctk.CTkFrame(tab, fg_color="transparent")
        cards.pack(fill="x", padx=10, pady=(10, 4))
        labels = {"wifi": ["Сетей найдено", "Лучший сигнал", "Средний сигнал", "Открытых"],
                  "bt": ["Устройств", "Подключено", "Сопряжено", "Аудио/периферия"],
                  "ble": ["Устройств", "Лучший RSSI", "Всего пакетов", "Компаний"]}[key]
        self.stat_cards[key] = []
        for i, lab in enumerate(labels):
            c = StatCard(cards, lab)
            c.pack(side="left", fill="x", expand=True, padx=(0, 10) if i < 3 else (0, 0))
            self.stat_cards[key].append(c)

        # Фильтры
        filt = ctk.CTkFrame(tab, fg_color="transparent")
        filt.pack(fill="x", padx=10, pady=4)
        if with_band_filter:
            self.band_seg = ctk.CTkSegmentedButton(filt, values=["Все", "2.4 ГГц", "5 ГГц", "6 ГГц"],
                                                   fg_color=CARD, selected_color=ACCENT2,
                                                   command=lambda _: self.refresh_table())
            self.band_seg.pack(side="left", padx=(0, 8))
            self.band_seg.set("Все")
            self.sec_seg = ctk.CTkSegmentedButton(filt, values=["Все", "Открытые", "Защищённые"],
                                                  fg_color=CARD, selected_color=ACCENT2,
                                                  command=lambda _: self.refresh_table())
            self.sec_seg.pack(side="left")
            self.sec_seg.set("Все")
        if extra_buttons:
            for txt, cmd in extra_buttons:
                ctk.CTkButton(filt, text=txt, height=30, fg_color=BTN2_BG,
                              hover_color=BTN2_HOVER, command=cmd).pack(side="right", padx=4)
        ctk.CTkButton(filt, text="В TXT", width=80, height=30, fg_color=BTN2_BG,
                      hover_color=BTN2_HOVER,
                      command=lambda k=key: self.save_txt(k)).pack(side="right", padx=4)
        ctk.CTkButton(filt, text="Копировать", width=110, height=30, fg_color=BTN2_BG,
                      hover_color=BTN2_HOVER,
                      command=lambda k=key: self.copy_details(k)).pack(side="right", padx=4)

        # Таблица
        tframe = ctk.CTkFrame(tab, fg_color=CARD, corner_radius=12)
        tframe.pack(fill="both", expand=True, padx=10, pady=6)
        tree = ttk.Treeview(tframe, columns=[c[0] for c in cols], show="headings",
                            style="Dark.Treeview")
        for cid, heading, w in cols:
            tree.heading(cid, text=heading)
            tree.column(cid, width=w, anchor="w" if cid in ("ssid", "name") else "center")
        vsb = ttk.Scrollbar(tframe, orient="vertical", command=tree.yview,
                              style="Dark.Vertical.TScrollbar")
        tree.configure(yscrollcommand=vsb.set)
        # порядок важен: ползунок первым, иначе expand дерева съест всю ширину
        vsb.pack(side="right", fill="y", padx=(0, 6), pady=10)
        tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        tree.bind("<<TreeviewSelect>>", lambda e, k=key: self.show_details(k))
        for tag, color in self._row_tags.items():
            tree.tag_configure(tag, foreground=color)
        tree.tag_configure("connected", background="#1e3a5f",
                           foreground="#ffffff")
        self.trees[key] = tree

        # Нижняя панель: детали + мини-график
        bottom = ctk.CTkFrame(tab, fg_color="transparent")
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        det = ctk.CTkTextbox(bottom, fg_color=CARD, text_color=BRIGHT,
                             font=("Consolas", 12), height=130)
        det.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.details[key] = det
        chart = ctk.CTkLabel(bottom, text="", fg_color=CARD, corner_radius=12,
                             width=300, height=130)
        chart.pack(side="right")
        self.charts[key] = chart

    def _job_wifi(self):
        gen = self._scan_gen
        cancelled = lambda: gen != self._scan_gen
        try:
            rows, note = wifi_mod.scan_wifi(is_cancelled=cancelled)
        except wifi_mod.ScanCancelled:
            return  # Стоп уже завершил UI
        except Exception as e:
            if gen != self._scan_gen:
                return
            self.q.put(("wifi", [], f"Wi-Fi ошибка: {e}"))
            return
        if gen != self._scan_gen:
            return
        msg = f"Wi-Fi: найдено BSSID: {len(rows)}" + (f" ({note})" if note else "")
        self.q.put(("wifi", rows, msg))

    def _job_bt(self):
        gen = self._scan_gen
        try:
            rows, note = bt_mod.scan_classic(self._duration())
        except Exception as e:
            if gen != self._scan_gen:
                return
            self.q.put(("bt", [], f"Bluetooth ошибка: {e}"))
            return
        if gen != self._scan_gen:
            return
        msg = f"Bluetooth: устройств: {len(rows)}" + (f". {note}" if note else "")
        self.q.put(("bt", rows, msg))

    def _job_ble(self):
        gen = self._scan_gen
        try:
            rows, note = ble_mod.scan_ble(self._duration(),
                                          on_update=lambda e: self.q.put(("ble_live", e)))
        except Exception as e:
            if gen != self._scan_gen:
                return
            self.q.put(("ble", [], f"BLE ошибка: {e}"))
            return
        if gen != self._scan_gen:
            return
        msg = f"BLE: устройств: {len(rows)}" + (f". {note}" if note else "")
        self.q.put(("ble", rows, msg))

    def _filtered(self, key: str) -> list[dict]:
        rows = self.data.get(key, [])
        q = self.search_var.get().strip().lower()
        if key == "wifi":
            band = self.band_seg.get()
            sec = self.sec_seg.get()
            out = []
            for r in rows:
                if band != "Все" and r.get("band") != band:
                    continue
                s = (r.get("security") or "")
                if sec == "Открытые" and "Открыт" not in s and "Открытая" not in s:
                    continue
                if sec == "Защищённые" and ("Открыт" in s or "Открытая" in s):
                    continue
                if q and q not in " ".join(str(v) for v in r.values()).lower():
                    continue
                out.append(r)
            return out
        if q:
            return [r for r in rows
                    if q in " ".join(str(v) for v in r.values()).lower()]
        return rows

    def refresh_table(self, key: str | None = None, update_stats: bool = True):
        key = key or (self.current_tab if self.current_tab != "log" else "wifi")
        if key not in self.trees:
            return
        tree = self.trees[key]
        tree.delete(*tree.get_children())
        rows = self._filtered(key)
        cols = {"wifi": WIFI_COLS, "bt": BT_COLS, "ble": BLE_COLS}[key]
        for i, r in enumerate(rows):
            vals = [self._cell(key, cid, r.get(cid)) for cid, _, _ in cols]
            tree.insert("", "end", iid=str(i), values=vals,
                        tags=self._row_tag(key, r))
        if update_stats:
            self._update_stats(key, rows)
            self._draw_chart(key, rows)

    def _row_tag(key: str, r: dict) -> tuple:
        """Теги строки: цвет по силе сигнала + подсветка подключения."""
        tags = []
        if key == "wifi":
            sig = r.get("signal_pct")
            if isinstance(sig, (int, float)):
                tags.append("sig_good" if sig >= 70
                            else "sig_mid" if sig >= 40 else "sig_bad")
            if r.get("connected_here") == "Да":
                tags.append("connected")
        elif key == "ble":
            rssi = r.get("rssi")
            if isinstance(rssi, (int, float)):
                tags.append("sig_good" if rssi >= -60
                            else "sig_mid" if rssi >= -75 else "sig_bad")
        elif key == "bt":
            if r.get("connected") == "Да":
                tags.append("connected")
        return tuple(tags)

    def _cell(key: str, cid: str, v) -> str:
        if v is None or v == "":
            return "—"
        if cid == "dbm" and v is not None:
            return f"{v}"
        if cid == "freq_mhz" and v:
            return f"{v}"
        if cid == "signal_pct" and isinstance(v, (int, float)):
            return f"{v}%"
        s = str(v)
        return s if len(s) <= 60 else s[:57] + "…"

    def _update_stats(self, key: str, rows: list[dict]):
        cards = self.stat_cards[key]
        if key == "wifi":
            sigs = [r["signal_pct"] for r in rows if isinstance(r.get("signal_pct"), (int, float))]
            best = f"{max(sigs)}%" if sigs else "—"
            avg = f"{sum(sigs) / len(sigs):.0f}%" if sigs else "—"
            opened = sum(1 for r in rows
                         if "Открыт" in (r.get("security") or "") or "Открытая" in (r.get("security") or ""))
            cards[0].set(str(len(rows)), ACCENT)
            cards[1].set(best, GOOD)
            cards[2].set(avg, TEXT)
            cards[3].set(str(opened), WARN if opened else GOOD)
        elif key == "bt":
            conn = sum(1 for r in rows if r.get("connected") == "Да")
            rem = sum(1 for r in rows if r.get("remembered") == "Да")
            av = sum(1 for r in rows if (r.get("major") or "") in ("Аудио/видео", "Периферия (HID)"))
            cards[0].set(str(len(rows)), ACCENT)
            cards[1].set(str(conn), GOOD)
            cards[2].set(str(rem), TEXT)
            cards[3].set(str(av), ACCENT2)
        else:
            rssis = [r["rssi"] for r in rows if isinstance(r.get("rssi"), (int, float))]
            best = f"{max(rssis)} дБм" if rssis else "—"
            pkts = sum(int(r.get("count") or 0) for r in rows)
            comps = len({r.get("company") for r in rows if r.get("company") not in (None, "—", "")})
            cards[0].set(str(len(rows)), ACCENT)
            cards[1].set(best, GOOD)
            cards[2].set(str(pkts), TEXT)
            cards[3].set(str(comps), ACCENT2)

    def _draw_chart(self, key: str, rows: list[dict]):
        lbl = self.charts[key]
        if key == "wifi":
            from collections import Counter
            cnt = Counter(r.get("channel") for r in rows if r.get("channel"))
            if not cnt:
                lbl.configure(text="Нет данных\nдля графика")
                return
            mx = max(cnt.values())
            lines = []
            for ch in sorted(cnt):
                bar = "█" * max(1, int(cnt[ch] / mx * 14))
                lines.append(f"ch{ch:>3} {bar} {cnt[ch]}")
            lbl.configure(text="Каналы:\n" + "\n".join(lines[:7]),
                          font=("Consolas", 11), text_color=ACCENT)
        elif key == "ble":
            top = [r for r in rows if isinstance(r.get("rssi"), (int, float))][:6]
            if not top:
                lbl.configure(text="Нет данных\nдля графика")
                return
            lo = min(r["rssi"] for r in top)
            lines = []
            for r in top:
                w = int((r["rssi"] - lo + 1) / (max(r["rssi"] for r in top) - lo + 1) * 12) + 1
                name = (r.get("name") or "?")[:12]
                lines.append(f"{name:<12} {'█' * w} {r['rssi']}")
            lbl.configure(text="Топ RSSI:\n" + "\n".join(lines),
                          font=("Consolas", 11), text_color=ACCENT)
        else:
            aud = sum(1 for r in rows if (r.get("major") or "") == "Аудио/видео")
            comp = sum(1 for r in rows if (r.get("major") or "") == "Компьютер")
            phone = sum(1 for r in rows if (r.get("major") or "") == "Телефон")
            lbl.configure(text=f"Профили:\nАудио: {aud}\nПК: {comp}\nТелефоны: {phone}",
                          font=("Consolas", 11), text_color=ACCENT)

    def _selected_row(self, key: str) -> dict | None:
        tree = self.trees[key]
        sel = tree.selection()
        if not sel:
            return None
        try:
            idx = int(sel[0])
        except ValueError:
            return None
        rows = self._filtered(key)
        return rows[idx] if 0 <= idx < len(rows) else None

    def show_details(self, key: str):
        r = self._selected_row(key)
        box = self.details[key]
        box.delete("1.0", "end")
        if not r:
            return
        lines = [f"=== {self.TITLES[key]} — детали ==="]
        order = {"wifi": ["ssid", "bssid", "vendor", "signal_pct", "dbm", "channel",
                          "freq_mhz", "band", "radio", "auth", "encryption",
                          "security", "network_type", "connected_here",
                          "basic_rates", "other_rates"],
                 "bt": ["name", "address", "cod", "major", "minor", "services",
                        "connected", "remembered", "authenticated", "last_seen", "last_used"],
                 "ble": ["name", "address", "rssi", "tx_power", "connectable",
                         "local_name", "company", "manufacturer_hex",
                         "service_uuids", "service_data", "count",
                         "first_seen", "last_seen"]}[key]
        for k in order:
            if k in r:
                lines.append(f"{k:>16}: {_fmt(r[k])}")
        for k in sorted(set(r) - set(order) - {"rssi_history"}):
            lines.append(f"{k:>16}: {_fmt(r[k])}")
        if key == "wifi":
            lines.append("")
            lines.append("(Оборудование = производитель железа точки по MAC, не провайдер)")
        box.insert("end", "\n".join(lines))

    def show_radios(self):
        radios, err = bt_mod.get_radios()
        box = self.details["bt"]
        box.delete("1.0", "end")
        if err and not radios:
            box.insert("end", f"Радиомодуль: {err}")
            self.log(err)
            return
        lines = ["=== Локальные BT-радиомодули ==="]
        for r in radios:
            lines.append(f"{r.get('name')}  {r.get('address')}  cod={r.get('cod')}")
        if err:
            lines.append(err)
        box.insert("end", "\n".join(lines))
        self.log(f"Радиомодулей: {len(radios)}")

    def show_my_adapter(self):
        infos = wifi_mod.get_interfaces()
        box = self.details["wifi"]
        box.delete("1.0", "end")
        lines = ["=== Мой Wi-Fi адаптер ==="]
        for info in infos:
            for k, v in info.items():
                lines.append(f"{k}: {v}")
            lines.append("-" * 40)
        box.insert("end", "\n".join(lines) or "Нет данных")
        self.log("Показана информация о своём адаптере")

    def read_gatt_selected(self):
        r = self._selected_row("ble")
        if not r:
            self.log("Выберите BLE-устройство в таблице")
            return
        addr = r.get("address", "")
        self.log(f"GATT-подключение к {addr}…")
        threading.Thread(target=self._job_gatt, args=(addr,), daemon=True).start()

    def _job_gatt(self, addr: str):
        info, err = ble_mod.read_gatt(addr, timeout=20.0)
        if err:
            self.q.put(("log", err))
            return
        lines = [f"=== GATT {addr} — глубокий разбор ==="]
        for k in ("name", "manufacturer", "model", "connected", "mtu"):
            if info.get(k) is not None:
                lines.append(f"{k}: {info[k]}")
        for svc in info.get("services", []):
            lines.append(f"\n[SVC] {svc.get('name', '')}  {svc['uuid']}")
            for ch in svc["characteristics"]:
                h = ch.get("handle")
                htxt = f"h=0x{h:04X}" if isinstance(h, int) else "h=?"
                lines.append(f"  [CHR] {ch.get('name', '')}  {ch['uuid']}  "
                             f"{htxt}  [{', '.join(ch['properties'])}]")
                if "parsed" in ch:
                    lines.append(f"        = {ch['parsed']}")
                if "hex" in ch:
                    lines.append(f"        {ch['hex']}")
                for d in ch.get("descriptors", []):
                    dh = d.get("handle")
                    dhtxt = f"h=0x{dh:04X}" if isinstance(dh, int) else "h=?"
                    lines.append(f"    [DSC] {d.get('name', '')}  {d['uuid']}  {dhtxt}")
                    if "parsed" in d:
                        lines.append(f"        = {d['parsed']}")
                    if "hex" in d:
                        lines.append(f"        {d['hex']}")
        text = "\n".join(lines)
        self.q.put(("gatt_text", text, f"GATT {addr}: сервисов: {len(info.get('services', []))}"))

    def _rows_as_text(self, key: str) -> str:
        cols = {"wifi": WIFI_COLS, "bt": BT_COLS, "ble": BLE_COLS}[key]
        rows = self._filtered(key)
        head = "\t".join(h for _, h, _ in cols)
        body = ["\t".join(str(r.get(cid, "—")) for cid, _, _ in cols) for r in rows]
        return (f"EtherScope — {self.TITLES[key]}\nВремя: {now_stamp()}\n"
                f"Строк: {len(rows)}\n\n{head}\n" + "\n".join(body))

    def copy_details(self, key: str):
        try:
            text = self.details[key].get("1.0", "end").strip()
        except Exception:
            text = ""
        if not text:
            text = self._rows_as_text(key)
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.log(f"Скопировано в буфер ({key}, {len(text)} символов)")
        except Exception as e:
            self.log(f"Копирование не удалось: {e}")

    def save_txt(self, key: str):
        rows = self._filtered(key)
        if not rows:
            self.log("Нечего сохранять — таблица пуста")
            return
        try:
            text = self.details[key].get("1.0", "end").strip()
        except Exception:
            text = ""
        report = self._rows_as_text(key)
        if text:
            report += f"\n\n--- Детали выбранного ---\n{text}\n"
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Text", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
            self.log(f"TXT сохранён ({key}, {len(rows)} строк) -> {path}")
        except Exception as e:
            self.log(f"Сохранение не удалось: {e}")
