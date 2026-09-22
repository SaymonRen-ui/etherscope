"""Горячие клавиши копирования."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

import customtkinter as ctk


class ClipMixin:
    """Миксин для ScannerApp. Логика не менялась, только переезд."""

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
