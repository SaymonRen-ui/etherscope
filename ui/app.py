"""EtherScope — современный анализатор Wi-Fi / Bluetooth / BLE (CustomTkinter)."""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

import customtkinter as ctk

from scanner import ble as ble_mod
from scanner import bt_classic as bt_mod
from scanner import wifi as wifi_mod
from scanner.net_utils import export_csv, export_json, now_stamp
from ui.mix_clip import ClipMixin
from ui.mix_lab import LabTabMixin
from ui.mix_proxy import ProxyTabMixin
from ui.mix_radio import BLE_COLS, BT_COLS, WIFI_COLS, RadioTabMixin
from ui.widgets import StatCard
from ui.theme import (
    ACCENT, ACCENT2, BAD, BG, BRIGHT, BTN2_BG, BTN2_HOVER,
    CARD, CARD2, GOOD, MUTED, SIDEBAR, TEXT, WARN,
    _fmt, resource_path,
)

# --- Палитра ---

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class ScannerApp(RadioTabMixin, LabTabMixin, ProxyTabMixin, ClipMixin, ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("EtherScope — анализатор Wi-Fi / Bluetooth / BLE")
        self.geometry("1320x840")
        self.minsize(1080, 680)
        self.configure(fg_color=BG)
        try:
            # Иконка окна/таскбара: ресайз из icon.ico через LANCZOS —
            # прямой PhotoImage(PNG) Tk давит до 16px грязно.
            from PIL import Image as _PILImage, ImageTk as _PILImageTk
            with _PILImage.open(resource_path("assets", "icon.ico")) as _src:
                _src = _src.convert("RGBA")
                _icons = [
                    _PILImageTk.PhotoImage(_src.resize((s, s), _PILImage.LANCZOS))
                    for s in (16, 32)
                ]
            self.iconphoto(True, *_icons)
            self._icon_ref = _icons
        except Exception:
            pass
        self._dark_titlebar()

        self.data: dict[str, list[dict]] = {"wifi": [], "bt": [], "ble": []}
        self.live_ble: dict[str, dict] = {}
        self.q: queue.Queue = queue.Queue()
        self.scanning = False
        # Поколение скана: Стоп инвалидирует зависший воркер —
        # его поздний результат игнорируется
        self._scan_gen = 0
        self.current_tab = "wifi"
        self.trees: dict[str, ttk.Treeview] = {}
        self.details: dict[str, ctk.CTkTextbox] = {}
        self.charts: dict[str, object] = {}
        self.stat_cards: dict[str, list[StatCard]] = {}
        self.lab_session = None
        self.lab_struct: list[dict] = []
        self.lab_handles: dict[int, dict] = {}
        self.lab_connected = False
        self.proxy_bridge = None
        self.proxy_services: list[dict] = []

        self._build_style()
        self._build_layout()
        self.after(200, self._poll_queue)
        self._install_clipboard_keys()
        self.log("Готово. Выберите раздел и нажмите «Сканировать».")

    # ---------- Каркас ----------
    def _dark_titlebar(self):
        """Тёмная полоса заголовка в цвет темы через DWM.

        Свою полосу не рисуем сознательно: кастомный title bar ломает
        Aero Snap, тени и кнопки панели задач. DWM-вариант выглядит
        как родной, но в цветах темы. При любой ошибке молча пропускаем.
        """
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            hwnd = user32.GetParent(self.winfo_id())  # внешний HWND окна Tk
            dwm = ctypes.windll.dwmapi
            dwm.DwmSetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            dwm.DwmSetWindowAttribute.restype = ctypes.HRESULT
            size = ctypes.sizeof(ctypes.c_int)
            on = ctypes.c_int(1)
            for attr in (20, 19):  # IMMERSIVE_DARK_MODE: новые и старые билды
                try:
                    dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), size)
                except OSError:
                    pass
            try:  # Windows 11: цвет caption и рамки (COLORREF 0x00BBGGRR)
                cap = ctypes.c_int(0x2A170F)      # #0f172a — цвет сайдбара
                dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), size)
                border = ctypes.c_int(0xEED322)   # #22d3ee — акцент
                dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), size)
            except OSError:
                pass
        except Exception:
            pass

    def _build_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Dark.Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, rowheight=28, borderwidth=0,
                     font=("Segoe UI", 11))
        st.configure("Dark.Treeview.Heading", background=CARD2, foreground=ACCENT,
                     font=("Segoe UI", 10, "bold"), borderwidth=0)
        st.map("Dark.Treeview", background=[("selected", "#23406b")],
               foreground=[("selected", "#ffffff")])
        # цвета строк по силе сигнала (теги настраиваются на каждом дереве)
        self._row_tags = {"sig_good": GOOD, "sig_mid": WARN, "sig_bad": BAD}
        # тёмные скроллбары в стиле темы
        st.configure("Dark.Vertical.TScrollbar", background="#2a3d68",
                     troughcolor=BG, bordercolor=BG, arrowcolor=ACCENT,
                     arrowsize=12, gripcount=0, relief="flat",
                     borderwidth=0, width=14)
        st.map("Dark.Vertical.TScrollbar",
               background=[("active", "#3a548c"), ("pressed", ACCENT)],
               arrowcolor=[("pressed", "#06202a")])
        st.configure("Dark.Horizontal.TScrollbar", background="#2a3d68",
                     troughcolor=BG, bordercolor=BG, arrowcolor=ACCENT,
                     arrowsize=12, gripcount=0, relief="flat",
                     borderwidth=0, width=14)
        st.map("Dark.Horizontal.TScrollbar",
               background=[("active", "#3a548c"), ("pressed", ACCENT)],
               arrowcolor=[("pressed", "#06202a")])

    def _build_layout(self):
        # Сайдбар
        side = ctk.CTkFrame(self, fg_color=SIDEBAR, corner_radius=0, width=230)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        try:
            from PIL import Image as _PILImage
            _logo = ctk.CTkImage(
                light_image=_PILImage.open(resource_path("assets", "icon.png")),
                size=(30, 30))
            ctk.CTkLabel(side, text=" EtherScope", image=_logo, compound="left",
                         font=("Segoe UI", 20, "bold"),
                         text_color=ACCENT).pack(padx=18, pady=(20, 2), anchor="w")
            self._logo_ref = _logo
        except Exception:
            ctk.CTkLabel(side, text="◉ EtherScope", font=("Segoe UI", 20, "bold"),
                         text_color=ACCENT).pack(padx=18, pady=(20, 2), anchor="w")
        ctk.CTkLabel(side, text="Wi-Fi • Bluetooth • BLE", font=("Segoe UI", 12),
                     text_color=MUTED).pack(padx=18, pady=(0, 16), anchor="w")

        self.nav_btns: dict[str, ctk.CTkButton] = {}
        for key, title in (("wifi", "  Wi-Fi сети"),
                           ("bt", "  Bluetooth"),
                           ("ble", "  BLE-устройства"),
                           ("lab", "  BLE Lab"),
                           ("proxy", "  BLE Proxy"),
                           ("log", "  Журнал")):
            b = ctk.CTkButton(side, text=title, anchor="w", font=("Segoe UI", 14),
                              fg_color="transparent", hover_color=CARD2,
                              text_color=TEXT, height=42,
                              command=lambda k=key: self.show_tab(k))
            b.pack(padx=12, pady=3, fill="x")
            self.nav_btns[key] = b

        ctk.CTkLabel(side, text="Длительность (BLE/BT), с",
                     font=("Segoe UI", 12), text_color=MUTED).pack(
                         padx=18, pady=(18, 4), anchor="w")
        self.dur_var = ctk.StringVar(value="10")
        ctk.CTkEntry(side, textvariable=self.dur_var, height=34,
                     fg_color=CARD, border_color=CARD2).pack(padx=14, fill="x")

        self.auto_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(side, text="Автоповтор", variable=self.auto_var,
                      font=("Segoe UI", 12)).pack(padx=18, pady=(12, 2), anchor="w")
        ctk.CTkLabel(side, text="Интервал повтора, с",
                     font=("Segoe UI", 12), text_color=MUTED).pack(
                         padx=18, pady=(6, 4), anchor="w")
        self.interval_var = ctk.StringVar(value="30")
        ctk.CTkEntry(side, textvariable=self.interval_var, height=34,
                     fg_color=CARD, border_color=CARD2).pack(padx=14, fill="x")

        ctk.CTkButton(side, text="Экспорт CSV", height=34, fg_color=BTN2_BG,
                      hover_color=BTN2_HOVER,
                      command=lambda: self.export("csv")).pack(
                          padx=14, pady=(18, 6), fill="x")
        ctk.CTkButton(side, text="Экспорт JSON", height=34, fg_color=BTN2_BG,
                      hover_color=BTN2_HOVER,
                      command=lambda: self.export("json")).pack(padx=14, fill="x")

        self.side_status = ctk.CTkLabel(side, text="● idle", font=("Segoe UI", 12),
                                        text_color=MUTED, wraplength=200,
                                        justify="left")
        self.side_status.pack(padx=18, pady=18, anchor="w", side="bottom")

        # Главная область
        main = ctk.CTkFrame(self, fg_color=BG)
        main.pack(side="left", fill="both", expand=True)

        # Шапка
        head = ctk.CTkFrame(main, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 6))
        self.title_lbl = ctk.CTkLabel(head, text="Wi-Fi сети",
                                      font=("Segoe UI", 22, "bold"), text_color=TEXT)
        self.title_lbl.pack(side="left")
        self.scan_btn = ctk.CTkButton(head, text="▶  Сканировать", height=38,
                                      width=170, font=("Segoe UI", 14, "bold"),
                                      fg_color=ACCENT, text_color="#06202a",
                                      hover_color="#67e8f9", command=self.start_scan)
        self.scan_btn.pack(side="right", padx=(8, 0))
        self.stop_btn = ctk.CTkButton(head, text="■ Стоп", height=38, width=100,
                                      font=("Segoe UI", 14),
                                      fg_color=BTN2_BG, hover_color=BTN2_HOVER,
                                      command=self.stop_scan, state="disabled")
        self.stop_btn.pack(side="right")
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_table())
        ctk.CTkEntry(head, textvariable=self.search_var, placeholder_text="Поиск…",
                     height=38, width=220, fg_color=CARD,
                     border_color=CARD2).pack(side="right", padx=8)

        self.progress = ctk.CTkProgressBar(main, height=6, fg_color=BTN2_BG,
                                           progress_color=ACCENT)
        self.progress.pack(fill="x", padx=18, pady=(0, 8))
        self.progress.set(0)

        # Вкладки
        self.tabs = ctk.CTkTabview(main, fg_color=BG, segmented_button_fg_color=CARD,
                                   segmented_button_selected_color=BTN2_BG,
                                   text_color=TEXT)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        for name, title in (("wifi", "Wi-Fi"), ("bt", "Bluetooth"),
                            ("ble", "BLE"), ("lab", "BLE Lab"),
                            ("proxy", "BLE Proxy"), ("log", "Журнал")):
            self.tabs.add(title)
        self.tabs.set("Wi-Fi")

        self._build_radio_tab("wifi", "Wi-Fi", WIFI_COLS, with_band_filter=True)
        self._build_radio_tab("bt", "Bluetooth", BT_COLS, extra_buttons=[
            ("Мой радиомодуль", self.show_radios), ("Мой адаптер (Wi-Fi)", self.show_my_adapter)])
        self._build_radio_tab("ble", "BLE", BLE_COLS, extra_buttons=[
            ("Читать GATT", self.read_gatt_selected)])
        self._build_lab_tab()
        self._build_proxy_tab()

        log_box = ctk.CTkTextbox(self.tabs.tab("Журнал"), fg_color=CARD,
                                 text_color=BRIGHT, font=("Consolas", 12))
        log_box.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_box = log_box
        self._highlight_nav("wifi")

    # ---------- Навигация / журнал ----------
    TITLES = {"wifi": "Wi-Fi сети", "bt": "Bluetooth (Classic)",
              "ble": "Bluetooth LE", "lab": "BLE Lab — работа с устройством",
              "proxy": "BLE Proxy — мост телефон-ПК-устройство",
              "log": "Журнал"}

    def show_tab(self, key: str):
        self.current_tab = key
        self.tabs.set({"wifi": "Wi-Fi", "bt": "Bluetooth",
                       "ble": "BLE", "lab": "BLE Lab", "proxy": "BLE Proxy",
                       "log": "Журнал"}[key])
        self.title_lbl.configure(text=self.TITLES[key])
        self._highlight_nav(key)

    def _highlight_nav(self, key: str):
        for k, b in self.nav_btns.items():
            b.configure(fg_color=CARD2 if k == key else "transparent",
                        text_color=ACCENT if k == key else TEXT)

    def log(self, msg: str):
        line = f"[{now_stamp()}] {msg}\n"
        try:
            self.log_box.insert("end", line)
            self.log_box.see("end")
        except Exception:
            pass
        try:
            self.side_status.configure(text=f"● {msg[:60]}")
        except Exception:
            pass

    # ---------- Сканирование ----------
    def _duration(self) -> float:
        try:
            return max(3.0, min(120.0, float(self.dur_var.get().replace(",", "."))))
        except (ValueError, AttributeError):
            return 10.0

    def start_scan(self):
        if self.scanning:
            return
        key = self.current_tab if self.current_tab in ("wifi", "bt", "ble") else "wifi"
        if self.current_tab not in ("wifi", "bt", "ble"):
            self.show_tab("wifi")
        self.scanning = True
        self.scan_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.log(f"Сканирование {self.TITLES[key]}…")
        self._stop_flag = False
        self._scan_gen += 1
        if key == "wifi":
            threading.Thread(target=self._job_wifi, daemon=True).start()
        elif key == "bt":
            threading.Thread(target=self._job_bt, daemon=True).start()
        else:
            self.live_ble = {}
            threading.Thread(target=self._job_ble, daemon=True).start()

    def stop_scan(self):
        # Инвалидируем текущее поколение: поздний ответ зависшего
        # воркера будет проигнорирован
        self._scan_gen += 1
        self._stop_flag = True
        self._finish("Остановлено пользователем")

    def _finish(self, msg: str = ""):
        self.scanning = False
        self.progress.stop()
        self.progress.set(1)
        self.scan_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if msg:
            self.log(msg)
        if self.auto_var.get() and not getattr(self, "_stop_flag", False):
            try:
                interval = max(5, int(float(self.interval_var.get())))
            except ValueError:
                interval = 30
            self.log(f"Автоповтор через {interval} с…")
            self.after(interval * 1000, lambda: self.start_scan() if not self.scanning else None)
        self._stop_flag = False

    def _poll_queue(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._finish(item[1])
                elif kind == "gatt_text":
                    _, text, msg = item
                    try:
                        box = self.details["ble"]
                        box.delete("1.0", "end")
                        box.insert("end", text)
                    except Exception as e:
                        self.log(f"GATT показ: {e}")
                    self.log(msg)
                elif kind == "lab_status":
                    _, msg, connected = item
                    self.lab_connected = bool(connected)
                    try:
                        self.lab_status_lbl.configure(
                            text=f"● {msg}",
                            text_color=GOOD if connected else MUTED)
                    except Exception:
                        pass
                    self.log(f"BLE Lab: {msg}")
                elif kind == "lab_services":
                    self._lab_refresh_tree(item[1])
                elif kind == "lab_log":
                    self._lab_log(item[1])
                elif kind == "proxy_log":
                    self._proxy_log(item[1])
                elif kind == "proxy_services":
                    self._proxy_refresh_services(item[1])
                elif kind == "proxy_status":
                    _, (which, text) = item
                    try:
                        if which == "adv":
                            self.proxy_adv_lbl.configure(text=f"Реклама: {text}",
                                                         text_color=ACCENT)
                        elif which == "phone":
                            self.proxy_phone_lbl.configure(
                                text=f"Телефон: {text[:70]}", text_color=GOOD)
                        else:
                            self.proxy_main_lbl.configure(text=f"● {text[:80]}",
                                                          text_color=TEXT)
                    except Exception:
                        pass
                    self.log(f"Proxy: {text}")
                elif kind == "ble_live":
                    entry = item[1]
                    self.live_ble[entry["address"]] = entry
                    if self.current_tab == "ble" or True:
                        self.data["ble"] = sorted(
                            self.live_ble.values(),
                            key=lambda e: (e.get("rssi") is None, -(e.get("rssi") or -999)))
                        if not self.scanning or True:
                            self.refresh_table("ble", update_stats=False)
                else:
                    key, rows, msg = item
                    self.data[key] = rows
                    self.refresh_table(key)
                    self._finish(msg)
        except queue.Empty:
            pass
        self.after(250, self._poll_queue)

    # ---------- BLE Lab: логика ----------
    def _lab(self):
        if self.lab_session is None:
            from scanner.ble_lab import LabSession
            self.lab_session = LabSession(self._lab_notify_cb)
        return self.lab_session

    # ---------- Глобальные Ctrl+C/X/V/A (включая русскую раскладку) ----------
    _CLIP_KEYS = {
        "c": "copy", "cyrillic_es": "copy",
        "x": "cut", "cyrillic_che": "cut",
        "v": "paste", "cyrillic_em": "paste",
        "a": "select_all", "cyrillic_ef": "select_all",
    }

    def export(self, fmt: str):
        if self.current_tab == "lab":
            self._lab_save_txt()
            return
        if self.current_tab == "proxy":
            self._proxy_save_txt()
            return
        key = self.current_tab if self.current_tab != "log" else "wifi"
        rows = self._filtered(key)
        if not rows:
            self.log("Нечего экспортировать — таблица пуста")
            return
        ext = ".csv" if fmt == "csv" else ".json"
        path = filedialog.asksaveasfilename(defaultextension=ext, filetypes=[
            ("CSV", "*.csv"), ("JSON", "*.json")][0 if fmt == "csv" else 1:])
        if not path:
            return
        try:
            clean = [{k: v for k, v in r.items() if k != "rssi_history"} for r in rows]
            if fmt == "csv":
                export_csv(clean, path)
            else:
                export_json(clean, path)
            self.log(f"Экспортировано {len(rows)} строк ({key}) -> {path}")
        except Exception as e:
            self.log(f"Экспорт не удался: {e}")
