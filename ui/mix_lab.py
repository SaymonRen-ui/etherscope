"""Вкладка BLE Lab."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, ttk

import customtkinter as ctk

from scanner.net_utils import now_stamp
from ui.theme import (
    ACCENT, ACCENT2, BAD, BG, BRIGHT, BTN2_BG, BTN2_HOVER,
    CARD, CARD2, GOOD, MUTED, SIDEBAR, TEXT, WARN,
    _fmt, resource_path,
)


class LabTabMixin:
    """Миксин для ScannerApp. Логика не менялась, только переезд."""

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
