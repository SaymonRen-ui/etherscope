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

# --- Палитра ---
BG = "#0b1220"
SIDEBAR = "#0f172a"
CARD = "#151e32"
CARD2 = "#1a2440"
BTN2_BG = "#2a3d68"
BTN2_HOVER = "#3a548c"
BRIGHT = "#f1f5f9"
ACCENT = "#22d3ee"
ACCENT2 = "#8b5cf6"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"
GOOD = "#34d399"
WARN = "#fbbf24"
BAD = "#f87171"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

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


def _fmt(v) -> str:
    return "—" if v is None or v == "" else str(v)


def resource_path(*parts) -> str:
    """Путь к ресурсу: работает и из исходников, и из exe (MEIPASS)."""
    import os
    import sys
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


class StatCard(ctk.CTkFrame):
    def __init__(self, master, label: str, **kwargs):
        super().__init__(master, fg_color=CARD, corner_radius=14, **kwargs)
        self.value_lbl = ctk.CTkLabel(self, text="—", font=("Segoe UI", 22, "bold"), text_color=TEXT)
        self.value_lbl.pack(padx=14, pady=(12, 0), anchor="w")
        ctk.CTkLabel(self, text=label, font=("Segoe UI", 13), text_color=MUTED).pack(
            padx=14, pady=(0, 12), anchor="w")

    def set(self, value: str, color: str = TEXT):
        self.value_lbl.configure(text=value, text_color=color)


class ScannerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("EtherScope — анализатор Wi-Fi / Bluetooth / BLE")
        self.geometry("1320x840")
        self.minsize(1080, 680)
        self.configure(fg_color=BG)
        try:
            _icon = tk.PhotoImage(file=resource_path("assets", "icon.png"))
            self.iconphoto(True, _icon)
            self._icon_ref = _icon
        except Exception:
            pass
        self._dark_titlebar()

        self.data: dict[str, list[dict]] = {"wifi": [], "bt": [], "ble": []}
        self.live_ble: dict[str, dict] = {}
        self.q: queue.Queue = queue.Queue()
        self.scanning = False
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

    # ---------- BLE Lab ----------
    def _build_lab_tab(self):
        tab = self.tabs.tab("BLE Lab")
        # Панель подключения
        conn = ctk.CTkFrame(tab, fg_color="transparent")
        conn.pack(fill="x", padx=10, pady=(10, 4))
        self.lab_addr_var = ctk.StringVar()
        ctk.CTkEntry(conn, textvariable=self.lab_addr_var,
                     placeholder_text="Адрес BLE-устройства  AA:BB:CC:DD:EE:FF",
                     height=34, width=260, fg_color=CARD,
                     border_color=CARD2).pack(side="left", padx=(0, 8))
        ctk.CTkButton(conn, text="Взять из BLE", height=34, fg_color=BTN2_BG,
                      hover_color=BTN2_HOVER, command=self._lab_take).pack(
                          side="left", padx=4)
        self.lab_conn_btn = ctk.CTkButton(conn, text="Подключить", height=34,
                                          width=130, font=("Segoe UI", 13, "bold"),
                                          fg_color=ACCENT, text_color="#06202a",
                                          hover_color="#67e8f9",
                                          command=self._lab_connect)
        self.lab_conn_btn.pack(side="left", padx=4)
        ctk.CTkButton(conn, text="Отключить", height=34, fg_color=BTN2_BG,
                      hover_color=BTN2_HOVER,
                      command=self._lab_disconnect).pack(side="left", padx=4)
        self.lab_status_lbl = ctk.CTkLabel(conn, text="● не подключено",
                                           font=("Segoe UI", 12), text_color=MUTED)
        self.lab_status_lbl.pack(side="left", padx=12)

        # Тело: дерево + управление
        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=4)
        tframe = ctk.CTkFrame(body, fg_color=CARD, corner_radius=12)
        tframe.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.lab_tree = ttk.Treeview(tframe, columns=("item", "uuid", "props"),
                                     show="headings", style="Dark.Treeview")
        self.lab_tree.heading("item", text="Элемент")
        self.lab_tree.heading("uuid", text="UUID")
        self.lab_tree.heading("props", text="Свойства / handle")
        self.lab_tree.column("item", width=200)
        self.lab_tree.column("uuid", width=250)
        self.lab_tree.column("props", width=220)
        vsb = ttk.Scrollbar(tframe, orient="vertical",
                            command=self.lab_tree.yview,
                            style="Dark.Vertical.TScrollbar")
        self.lab_tree.configure(yscrollcommand=vsb.set)
        # порядок важен: ползунок первым, иначе expand дерева съест всю ширину
        vsb.pack(side="right", fill="y", padx=(0, 6), pady=10)
        self.lab_tree.pack(side="left", fill="both", expand=True,
                           padx=(10, 0), pady=10)
        self.lab_tree.bind("<<TreeviewSelect>>", lambda e: self._lab_show_char())

        right = ctk.CTkFrame(body, fg_color=CARD, corner_radius=12, width=380)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        self.lab_char_lbl = ctk.CTkLabel(right, text="Выберите характеристику",
                                         font=("Segoe UI", 12), text_color=TEXT,
                                         wraplength=340, justify="left")
        self.lab_char_lbl.pack(padx=12, pady=(12, 4), anchor="w")
        self.lab_fmt = ctk.CTkSegmentedButton(right, values=["HEX", "Текст"],
                                              fg_color=BTN2_BG,
                                              selected_color=ACCENT2)
        self.lab_fmt.pack(padx=12, pady=4, anchor="w")
        self.lab_fmt.set("HEX")
        self.lab_data_var = ctk.StringVar()
        ctk.CTkEntry(right, textvariable=self.lab_data_var,
                     placeholder_text="Данные: HEX '01 03 FF' или текст",
                     height=34, fg_color=BG,
                     border_color=CARD2).pack(padx=12, pady=4, fill="x")
        btns = ctk.CTkFrame(right, fg_color="transparent")
        btns.pack(padx=12, pady=4, fill="x")
        for txt, cmd in (("Читать", self._lab_read),
                         ("Записать", lambda: self._lab_write(True)),
                         ("Записать б/о", lambda: self._lab_write(False))):
            ctk.CTkButton(btns, text=txt, height=32, fg_color=BTN2_BG,
                          hover_color=BTN2_HOVER, command=cmd).pack(
                              side="left", padx=2, fill="x", expand=True)
        btns2 = ctk.CTkFrame(right, fg_color="transparent")
        btns2.pack(padx=12, pady=(0, 4), fill="x")
        for txt, cmd in (("Подписаться", self._lab_sub),
                         ("Отписаться", self._lab_unsub)):
            ctk.CTkButton(btns2, text=txt, height=32, fg_color=BTN2_BG,
                          hover_color=BTN2_HOVER, command=cmd).pack(
                              side="left", padx=2, fill="x", expand=True)
        ctk.CTkLabel(right, text="Сессия (всё пишется с метками времени):",
                     font=("Segoe UI", 12), text_color=MUTED).pack(
                         padx=12, pady=(8, 0), anchor="w")
        self.lab_log_box = ctk.CTkTextbox(right, fg_color=BG, text_color=BRIGHT,
                                          font=("Consolas", 12))
        self.lab_log_box.pack(padx=12, pady=4, fill="both", expand=True)
        btns3 = ctk.CTkFrame(right, fg_color="transparent")
        btns3.pack(padx=12, pady=(0, 12), fill="x")
        for txt, cmd in (("Копировать", self._lab_copy_log),
                         ("В TXT", self._lab_save_txt),
                         ("Очистить", self._lab_clear_log)):
            ctk.CTkButton(btns3, text=txt, height=30, fg_color=BTN2_BG,
                          hover_color=BTN2_HOVER, command=cmd).pack(
                              side="left", padx=2, fill="x", expand=True)
        ctk.CTkLabel(tab, wraplength=1100, justify="left",
                     font=("Segoe UI", 12), text_color=MUTED,
                     text="Подсказка: трафик официального приложения с телефона перехватывается "
                          "не здесь — ПК не умеет быть BLE-устройством. Рабочие способы "
                          "(Android HCI snoop, nRF Sniffer) описаны в README, а найденные "
                          "команды можно воспроизвести и изучить в этой вкладке.").pack(
                              padx=12, pady=(0, 10), anchor="w")

    # ---------- BLE Proxy ----------
    def _build_proxy_tab(self):
        tab = self.tabs.tab("BLE Proxy")
        # Шаг 1: цель
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(10, 4))
        self.proxy_addr_var = ctk.StringVar()
        ctk.CTkEntry(top, textvariable=self.proxy_addr_var,
                     placeholder_text="Адрес оригинала  AA:BB:CC:DD:EE:FF",
                     height=34, width=250, fg_color=CARD,
                     border_color=CARD2).pack(side="left", padx=(0, 8))
        ctk.CTkButton(top, text="Взять из BLE", height=34, fg_color=BTN2_BG,
                      hover_color=BTN2_HOVER, command=self._proxy_take).pack(
                          side="left", padx=4)
        ctk.CTkButton(top, text="Прочитать GATT", height=34, fg_color=BTN2_BG,
                      hover_color=BTN2_HOVER,
                      command=self._proxy_read_gatt).pack(side="left", padx=4)
        self.proxy_main_lbl = ctk.CTkLabel(top, text="● мост остановлен",
                                           font=("Segoe UI", 12), text_color=MUTED)
        self.proxy_main_lbl.pack(side="left", padx=12)

        # Шаг 2: сервис + старт
        mid = ctk.CTkFrame(tab, fg_color="transparent")
        mid.pack(fill="x", padx=10, pady=4)
        self.proxy_svc_var = ctk.StringVar()
        ctk.CTkEntry(mid, textvariable=self.proxy_svc_var,
                     placeholder_text="UUID сервиса для клонирования",
                     height=34, width=320, fg_color=CARD,
                     border_color=CARD2).pack(side="left", padx=(0, 8))
        ctk.CTkButton(mid, text="Старт моста", height=34, width=140,
                      font=("Segoe UI", 13, "bold"),
                      fg_color=ACCENT, text_color="#06202a",
                      hover_color="#67e8f9",
                      command=self._proxy_start).pack(side="left", padx=4)
        ctk.CTkButton(mid, text="Стоп", height=34, width=100, fg_color=BTN2_BG,
                      hover_color=BTN2_HOVER,
                      command=self._proxy_stop).pack(side="left", padx=4)
        self.proxy_adv_lbl = ctk.CTkLabel(mid, text="Реклама: —",
                                          font=("Segoe UI", 12), text_color=MUTED)
        self.proxy_adv_lbl.pack(side="left", padx=12)
        self.proxy_phone_lbl = ctk.CTkLabel(mid, text="Телефон: —",
                                            font=("Segoe UI", 12), text_color=MUTED)
        self.proxy_phone_lbl.pack(side="left", padx=12)

        # Сервисы цели + лог
        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=4)
        svc_frame = ctk.CTkFrame(body, fg_color=CARD, corner_radius=12, width=300)
        svc_frame.pack(side="left", fill="y", padx=(0, 10))
        svc_frame.pack_propagate(False)
        ctk.CTkLabel(svc_frame, text="Сервисы оригинала:",
                     font=("Segoe UI", 12), text_color=MUTED).pack(
                         padx=12, pady=(10, 4), anchor="w")
        svc_row = ctk.CTkFrame(svc_frame, fg_color="transparent")
        svc_row.pack(padx=12, pady=(0, 4), fill="both", expand=True)
        self.proxy_svc_list = tk.Listbox(svc_row, bg=CARD, fg=TEXT,
                                           selectbackground="#23406b",
                                           selectforeground="#ffffff",
                                           font=("Consolas", 11),
                                           activestyle="none", borderwidth=0,
                                           highlightthickness=0)
        svc_vsb = ttk.Scrollbar(svc_row, orient="vertical",
                                command=self.proxy_svc_list.yview,
                                style="Dark.Vertical.TScrollbar")
        self.proxy_svc_list.configure(yscrollcommand=svc_vsb.set)
        # порядок важен: ползунок первым, иначе expand списка съест всю ширину
        svc_vsb.pack(side="right", fill="y")
        self.proxy_svc_list.pack(side="left", fill="both", expand=True)
        self.proxy_svc_list.bind("<<ListboxSelect>>",
                                 lambda e: self._proxy_svc_selected())
        ctk.CTkLabel(svc_frame, text="Клик по строке подставляет UUID",
                     font=("Segoe UI", 11), text_color=MUTED).pack(
                         padx=12, pady=(0, 12), anchor="w")

        right = ctk.CTkFrame(body, fg_color=CARD, corner_radius=12)
        right.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(right, text="Трафик моста (все команды с метками):",
                     font=("Segoe UI", 12), text_color=MUTED).pack(
                         padx=12, pady=(10, 0), anchor="w")
        self.proxy_log_box = ctk.CTkTextbox(right, fg_color=BG, text_color=BRIGHT,
                                            font=("Consolas", 12))
        self.proxy_log_box.pack(padx=12, pady=4, fill="both", expand=True)
        btns = ctk.CTkFrame(right, fg_color="transparent")
        btns.pack(padx=12, pady=(0, 12), fill="x")
        for txt, cmd in (("Копировать", self._proxy_copy_log),
                         ("В TXT", self._proxy_save_txt),
                         ("Очистить", self._proxy_clear_log)):
            ctk.CTkButton(btns, text=txt, height=30, fg_color=BTN2_BG,
                          hover_color=BTN2_HOVER, command=cmd).pack(
                              side="left", padx=2, fill="x", expand=True)
        ctk.CTkLabel(tab, wraplength=1100, justify="left",
                     font=("Segoe UI", 12), text_color=MUTED,
                     text="Как пользоваться: 1) «Взять из BLE» и «Прочитать GATT»; "
                          "2) выбрать сервис и «Старт моста» — ПК станет клоном; "
                          "3) открыть приложение на телефоне — оно подключится к ПК "
                          "(оригинал в это время занят мостом и не рекламируется); "
                          "4) команды появятся в логе. Условия: без pairing/bonding, "
                          "приложение ищет по сервису.").pack(
                              padx=12, pady=(0, 10), anchor="w")

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
        if key == "wifi":
            threading.Thread(target=self._job_wifi, daemon=True).start()
        elif key == "bt":
            threading.Thread(target=self._job_bt, daemon=True).start()
        else:
            self.live_ble = {}
            threading.Thread(target=self._job_ble, daemon=True).start()

    def stop_scan(self):
        self._stop_flag = True
        self.log("Остановка… (дождитесь завершения текущего прохода)")

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

    def _job_wifi(self):
        try:
            rows, note = wifi_mod.scan_wifi()
            msg = f"Wi-Fi: найдено BSSID: {len(rows)}" + (f" ({note})" if note else "")
            self.q.put(("wifi", rows, msg))
        except Exception as e:
            self.q.put(("wifi", [], f"Wi-Fi ошибка: {e}"))

    def _job_bt(self):
        try:
            rows, note = bt_mod.scan_classic(self._duration())
            msg = f"Bluetooth: устройств: {len(rows)}" + (f". {note}" if note else "")
            self.q.put(("bt", rows, msg))
        except Exception as e:
            self.q.put(("bt", [], f"Bluetooth ошибка: {e}"))

    def _job_ble(self):
        try:
            rows, note = ble_mod.scan_ble(self._duration(),
                                          on_update=lambda e: self.q.put(("ble_live", e)))
            msg = f"BLE: устройств: {len(rows)}" + (f". {note}" if note else "")
            self.q.put(("ble", rows, msg))
        except Exception as e:
            self.q.put(("ble", [], f"BLE ошибка: {e}"))

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

    # ---------- Таблица / фильтры / статистика ----------
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

    @staticmethod
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

    @staticmethod
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

    # ---------- Мини-графики (Canvas внутри CTkLabel невозможен — рисуем текстом) ----------
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

    # ---------- Детали ----------
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

    # ---------- Доп. действия ----------
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

    # ---------- Копирование и TXT-отчёты ----------
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

    # ---------- BLE Lab: логика ----------
    def _lab(self):
        if self.lab_session is None:
            from scanner.ble_lab import LabSession
            self.lab_session = LabSession(self._lab_notify_cb)
        return self.lab_session

    def _lab_notify_cb(self, handle: int, data: bytes):
        from scanner.uuids import fmt_data, parse_value
        entry = self.lab_handles.get(handle, {})
        nm = entry.get("name", "")
        line = (f"[{now_stamp()}] NOTIFY h=0x{handle:04X} {entry.get('uuid', '')} "
                f"{nm}: {fmt_data(data)}")
        parsed = parse_value(entry.get("uuid", ""), data)
        if parsed:
            line += f"  => {parsed}"
        self.q.put(("lab_log", line))

    def _lab_log(self, text: str):
        try:
            self.lab_log_box.insert("end", text + "\n")
            self.lab_log_box.see("end")
        except Exception:
            pass

    def _lab_take(self):
        r = self._selected_row("ble")
        if not r:
            self.log("BLE Lab: сначала выберите устройство на вкладке BLE")
            return
        self.lab_addr_var.set(r.get("address", ""))
        self.log(f"BLE Lab: адрес {r.get('address', '')} подставлен")

    def _lab_connect(self):
        addr = (self.lab_addr_var.get() or "").strip()
        if not addr:
            self.log("BLE Lab: введите адрес устройства")
            return
        self.q.put(("lab_status", f"подключение к {addr}…", False))
        threading.Thread(target=self._lab_job_connect, args=(addr,),
                         daemon=True).start()

    def _lab_job_connect(self, addr: str):
        try:
            struct, _err = self._lab().connect(addr)
        except Exception as e:
            self.q.put(("lab_status", f"не удалось: {e}", False))
            return
        n_chr = sum(len(s["characteristics"]) for s in struct)
        self.q.put(("lab_services", struct))
        self.q.put(("lab_status", f"подключено {addr} (сервисов: {len(struct)}, "
                                  f"характеристик: {n_chr})", True))

    def _lab_disconnect(self):
        threading.Thread(target=self._lab_job_disconnect, daemon=True).start()

    def _lab_job_disconnect(self):
        try:
            msg = self._lab().disconnect()
        except Exception as e:
            msg = f"ошибка: {e}"
        self.q.put(("lab_status", msg, False))

    def _lab_refresh_tree(self, struct: list[dict]):
        tree = self.lab_tree
        tree.delete(*tree.get_children())
        self.lab_struct = struct
        self.lab_handles = {}
        for i, svc in enumerate(struct):
            tree.insert("", "end", iid=f"s{i}",
                        values=(f"SVC: {svc['name']}", svc["uuid"], ""))
            for ch in svc["characteristics"]:
                h = ch.get("handle")
                htxt = f"h=0x{h:04X}" if isinstance(h, int) else "h=?"
                iid = f"c{h}" if isinstance(h, int) else f"c{i}_{ch['uuid']}"
                tree.insert(f"s{i}", "end", iid=iid,
                            values=(f"CHR: {ch['name']}", ch["uuid"],
                                    f"{htxt} {','.join(ch['properties'])}"))
                if isinstance(h, int):
                    self.lab_handles[h] = ch
        self._lab_log(f"[{now_stamp()}] GATT: сервисов {len(struct)}, "
                      f"характеристик {len(self.lab_handles)}")

    def _lab_selected(self) -> tuple[int | None, dict | None]:
        sel = self.lab_tree.selection()
        if not sel or not sel[0].startswith("c"):
            self.log("BLE Lab: выберите характеристику в дереве")
            return None, None
        try:
            handle = int(sel[0][1:])
        except ValueError:
            self.log("BLE Lab: не удалось определить handle")
            return None, None
        entry = self.lab_handles.get(handle)
        if entry is None:
            self.log("BLE Lab: характеристика не найдена (переподключитесь)")
            return None, None
        return handle, entry

    def _lab_show_char(self):
        handle, entry = self._lab_selected_quiet()
        if entry is None:
            return
        desc = "\n".join(f"    {d['name']} ({d['uuid']})" for d in entry["descriptors"])
        self.lab_char_lbl.configure(
            text=f"{entry['name']}\n{entry['uuid']}\nh=0x{handle:04X} "
                 f"[{', '.join(entry['properties'])}]"
                 + (f"\nДескрипторы:\n{desc}" if desc else ""))

    def _lab_selected_quiet(self) -> tuple[int | None, dict | None]:
        sel = self.lab_tree.selection()
        if not sel or not sel[0].startswith("c"):
            return None, None
        try:
            handle = int(sel[0][1:])
        except ValueError:
            return None, None
        return handle, self.lab_handles.get(handle)

    def _lab_data(self) -> bytes | None:
        s = (self.lab_data_var.get() or "")
        if self.lab_fmt.get() == "HEX":
            try:
                return bytes.fromhex("".join(s.split()))
            except ValueError:
                self.log("BLE Lab: HEX разобран с ошибкой (пример: '01 03 FF')")
                return None
        return s.encode("utf-8")

    def _lab_read(self):
        handle, entry = self._lab_selected()
        if handle is None:
            return
        if "read" not in entry["properties"]:
            self.log("BLE Lab: у характеристики нет свойства read")
            return
        threading.Thread(target=self._lab_job_read,
                         args=(handle, entry), daemon=True).start()

    def _lab_job_read(self, handle: int, entry: dict):
        from scanner.uuids import fmt_data, parse_value
        try:
            raw, _e = self._lab().read(handle)
        except Exception as e:
            self.q.put(("lab_log", f"[{now_stamp()}] READ h=0x{handle:04X} ОШИБКА: {e}"))
            return
        line = (f"[{now_stamp()}] READ h=0x{handle:04X} {entry['uuid']} "
                f"{entry['name']}: {fmt_data(raw)}")
        parsed = parse_value(entry["uuid"], raw)
        if parsed:
            line += f"  => {parsed}"
        self.q.put(("lab_log", line))

    def _lab_write(self, response: bool):
        handle, entry = self._lab_selected()
        if handle is None:
            return
        need = "write" if response else "write-without-response"
        if need not in entry["properties"]:
            self.log(f"BLE Lab: у характеристики нет свойства {need}")
            return
        data = self._lab_data()
        if data is None:
            return
        threading.Thread(target=self._lab_job_write,
                         args=(handle, entry, data, response),
                         daemon=True).start()

    def _lab_job_write(self, handle: int, entry: dict, data: bytes, response: bool):
        from scanner.uuids import fmt_data
        mode = "с ответом" if response else "без ответа"
        try:
            self._lab().write(handle, data, response)
        except Exception as e:
            self.q.put(("lab_log", f"[{now_stamp()}] WRITE h=0x{handle:04X} "
                                   f"({mode}) ОШИБКА: {e}"))
            return
        self.q.put(("lab_log", f"[{now_stamp()}] WRITE h=0x{handle:04X} "
                               f"{entry['uuid']} ({mode}): {fmt_data(data)} -> OK"))

    def _lab_sub(self):
        handle, entry = self._lab_selected()
        if handle is None:
            return
        if "notify" not in entry["properties"] and "indicate" not in entry["properties"]:
            self.log("BLE Lab: у характеристики нет notify/indicate")
            return
        threading.Thread(target=self._lab_job_sub,
                         args=(handle, entry), daemon=True).start()

    def _lab_job_sub(self, handle: int, entry: dict):
        try:
            self._lab().subscribe(handle)
        except Exception as e:
            self.q.put(("lab_log", f"[{now_stamp()}] SUBSCRIBE h=0x{handle:04X} "
                                   f"ОШИБКА: {e}"))
            return
        self.q.put(("lab_log", f"[{now_stamp()}] SUBSCRIBE h=0x{handle:04X} "
                               f"{entry['uuid']} -> OK (данные появятся ниже)"))

    def _lab_unsub(self):
        handle, entry = self._lab_selected()
        if handle is None:
            return
        threading.Thread(target=self._lab_job_unsub, args=(handle,),
                         daemon=True).start()

    def _lab_job_unsub(self, handle: int):
        try:
            self._lab().unsubscribe(handle)
        except Exception as e:
            self.q.put(("lab_log", f"[{now_stamp()}] UNSUBSCRIBE h=0x{handle:04X} "
                                   f"ОШИБКА: {e}"))
            return
        self.q.put(("lab_log", f"[{now_stamp()}] UNSUBSCRIBE h=0x{handle:04X} -> OK"))

    def _lab_copy_log(self):
        try:
            text = self.lab_log_box.get("1.0", "end").strip()
        except Exception:
            text = ""
        if not text:
            self.log("BLE Lab: сессия пуста")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.log(f"BLE Lab: сессия скопирована ({len(text)} символов)")
        except Exception as e:
            self.log(f"BLE Lab копирование: {e}")

    def _lab_save_txt(self):
        try:
            text = self.lab_log_box.get("1.0", "end").strip()
        except Exception:
            text = ""
        if not text:
            self.log("BLE Lab: сессия пуста")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Text", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"EtherScope BLE Lab — сессия\nВремя: {now_stamp()}\n"
                        f"Устройство: {self.lab_addr_var.get()}\n\n{text}\n")
            self.log(f"BLE Lab: сессия сохранена -> {path}")
        except Exception as e:
            self.log(f"BLE Lab сохранение: {e}")

    def _lab_clear_log(self):
        try:
            self.lab_log_box.delete("1.0", "end")
        except Exception:
            pass

    def _proxy_bridge(self):
        if self.proxy_bridge is None:
            from scanner.ble_proxy import ProxyBridge
            self.proxy_bridge = ProxyBridge(self._proxy_event)
        return self.proxy_bridge

    def _proxy_event(self, kind: str, text: str):
        line = f"[{now_stamp()}] {text}"
        if kind in ("status", "adv", "phone", "target"):
            self.q.put(("proxy_status", (kind, text)))
        self.q.put(("proxy_log", line))

    def _proxy_log(self, text: str):
        try:
            self.proxy_log_box.insert("end", text + "\n")
            self.proxy_log_box.see("end")
        except Exception:
            pass

    def _proxy_take(self):
        r = self._selected_row("ble")
        if not r:
            self.log("Proxy: сначала выберите устройство на вкладке BLE")
            return
        self.proxy_addr_var.set(r.get("address", ""))
        self.log(f"Proxy: адрес {r.get('address', '')} подставлен")

    def _proxy_read_gatt(self):
        addr = (self.proxy_addr_var.get() or "").strip()
        if not addr:
            self.log("Proxy: введите адрес оригинала")
            return
        self.log(f"Proxy: читаю GATT {addr}…")
        threading.Thread(target=self._proxy_job_read, args=(addr,),
                         daemon=True).start()

    def _proxy_job_read(self, addr: str):
        info, err = ble_mod.read_gatt(addr, timeout=20.0)
        if err:
            self.q.put(("proxy_log", f"[{now_stamp()}] GATT: {err}"))
            return
        self.q.put(("proxy_services", info.get("services", [])))
        self.q.put(("proxy_log", f"[{now_stamp()}] GATT {addr}: сервисов: "
                                 f"{len(info.get('services', []))}"))

    def _proxy_refresh_services(self, struct: list[dict]):
        self.proxy_services = struct
        try:
            box = self.proxy_svc_list
            box.delete(0, "end")
            for s in struct:
                box.insert("end", f"{s.get('name', '')}  |  {s['uuid']}")
        except Exception:
            pass

    def _proxy_svc_selected(self):
        try:
            sel = self.proxy_svc_list.curselection()
        except Exception:
            return
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.proxy_services):
            uid = self.proxy_services[idx]["uuid"]
            self.proxy_svc_var.set(uid)
            self.log(f"Proxy: сервис {uid} подставлен")

    def _proxy_start(self):
        addr = (self.proxy_addr_var.get() or "").strip()
        svc = (self.proxy_svc_var.get() or "").strip()
        if not addr or not svc:
            self.log("Proxy: нужны адрес и UUID сервиса")
            return
        self._proxy_log(f"[{now_stamp()}] старт моста {addr} / {svc}…")
        threading.Thread(target=self._proxy_job_start, args=(addr, svc),
                         daemon=True).start()

    def _proxy_job_start(self, addr: str, svc: str):
        try:
            msg = self._proxy_bridge().start(addr, svc, timeout=90.0)
        except Exception as e:
            self.q.put(("proxy_log", f"[{now_stamp()}] старт НЕ удался: {e}"))
            return
        self.q.put(("proxy_log", f"[{now_stamp()}] {msg}"))

    def _proxy_stop(self):
        threading.Thread(target=self._proxy_job_stop, daemon=True).start()

    def _proxy_job_stop(self):
        try:
            msg = self._proxy_bridge().stop()
        except Exception as e:
            msg = f"ошибка: {e}"
        self.q.put(("proxy_log", f"[{now_stamp()}] {msg}"))

    def _proxy_copy_log(self):
        try:
            text = self.proxy_log_box.get("1.0", "end").strip()
        except Exception:
            text = ""
        if not text:
            self.log("Proxy: лог пуст")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.log(f"Proxy: лог скопирован ({len(text)} символов)")
        except Exception as e:
            self.log(f"Proxy копирование: {e}")

    def _proxy_save_txt(self):
        try:
            text = self.proxy_log_box.get("1.0", "end").strip()
        except Exception:
            text = ""
        if not text:
            self.log("Proxy: лог пуст")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Text", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"EtherScope BLE Proxy — трафик моста\nВремя: {now_stamp()}\n"
                        f"Оригинал: {self.proxy_addr_var.get()}\n"
                        f"Сервис: {self.proxy_svc_var.get()}\n\n{text}\n")
            self.log(f"Proxy: лог сохранён -> {path}")
        except Exception as e:
            self.log(f"Proxy сохранение: {e}")

    def _proxy_clear_log(self):
        try:
            self.proxy_log_box.delete("1.0", "end")
        except Exception:
            pass

    # ---------- Глобальные Ctrl+C/X/V/A (включая русскую раскладку) ----------
    _CLIP_KEYS = {
        "c": "copy", "cyrillic_es": "copy",
        "x": "cut", "cyrillic_che": "cut",
        "v": "paste", "cyrillic_em": "paste",
        "a": "select_all", "cyrillic_ef": "select_all",
    }

    def _install_clipboard_keys(self):
        self.bind_all("<Control-KeyPress>", self._clip_action, add="+")

    def _clip_action(self, event):
        try:
            action = self._CLIP_KEYS.get((event.keysym or "").lower())
        except Exception:
            return None
        if action is None:
            return None
        try:
            w = self.focus_get()
        except Exception:
            return None
        if w is None:
            return None
        try:
            if isinstance(w, ttk.Treeview):
                return self._clip_tree(w, action)
            if isinstance(w, tk.Listbox):
                return self._clip_listbox(w, action)
            if isinstance(w, tk.Text):
                return self._clip_text(w, action)
            if isinstance(w, tk.Entry):
                return self._clip_entry(w, action)
        except Exception:
            return None
        return None

    @staticmethod
    def _to_clipboard(widget, text: str) -> bool:
        try:
            widget.clipboard_clear()
            widget.clipboard_append(text)
            return True
        except Exception:
            return False

    def _clip_entry(self, w: tk.Entry, action: str):
        if action == "select_all":
            w.selection_range(0, "end")
            w.icursor("end")
            return "break"
        if action == "copy":
            if w.selection_present():
                self._to_clipboard(w, w.selection_get())
            return "break"
        if action == "cut":
            if w.selection_present():
                self._to_clipboard(w, w.selection_get())
                w.delete("sel.first", "sel.last")
            return "break"
        if action == "paste":
            try:
                data = w.clipboard_get()
            except Exception:
                return "break"
            try:
                if w.selection_present():
                    w.delete("sel.first", "sel.last")
            except Exception:
                pass
            w.insert("insert", data)
            return "break"
        return None

    def _clip_text(self, w: tk.Text, action: str):
        if action == "select_all":
            w.tag_add("sel", "1.0", "end-1c")
            w.mark_set("insert", "1.0")
            w.see("insert")
            return "break"
        if action in ("copy", "cut"):
            try:
                text = w.get("sel.first", "sel.last")
            except Exception:
                return "break"
            self._to_clipboard(w, text)
            if action == "cut":
                try:
                    if str(w.cget("state")) == "normal":
                        w.delete("sel.first", "sel.last")
                except Exception:
                    pass
            return "break"
        if action == "paste":
            try:
                data = w.clipboard_get()
            except Exception:
                return "break"
            try:
                if str(w.cget("state")) != "normal":
                    return "break"
                try:
                    w.delete("sel.first", "sel.last")
                except Exception:
                    pass
                w.insert("insert", data)
                w.see("insert")
            except Exception:
                pass
            return "break"
        return None

    def _clip_tree(self, w: ttk.Treeview, action: str):
        if action == "select_all":
            try:
                w.selection_set(w.get_children(""))
            except Exception:
                pass
            return "break"
        if action == "copy":
            lines = []
            for iid in w.selection():
                try:
                    lines.append("\t".join(str(v) for v in w.item(iid, "values")))
                except Exception:
                    pass
            if lines:
                self._to_clipboard(w, "\n".join(lines))
            return "break"
        if action in ("cut", "paste"):
            return "break"
        return None

    def _clip_listbox(self, w: tk.Listbox, action: str):
        if action == "select_all":
            try:
                w.selection_set(0, "end")
            except Exception:
                pass
            return "break"
        if action == "copy":
            try:
                idxs = w.curselection()
                lines = [w.get(i) for i in idxs]
            except Exception:
                lines = []
            if lines:
                self._to_clipboard(w, "\n".join(lines))
            return "break"
        if action in ("cut", "paste"):
            return "break"
        return None

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
