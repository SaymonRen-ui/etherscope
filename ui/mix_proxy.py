"""Вкладка BLE Proxy."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, ttk

import customtkinter as ctk

from scanner import ble as ble_mod
from scanner.net_utils import now_stamp
from ui.theme import (
    ACCENT, ACCENT2, BAD, BG, BRIGHT, BTN2_BG, BTN2_HOVER,
    CARD, CARD2, GOOD, MUTED, SIDEBAR, TEXT, WARN,
    _fmt, resource_path,
)


class ProxyTabMixin:
    """Миксин для ScannerApp. Логика не менялась, только переезд."""

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
