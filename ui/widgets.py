"""Общие виджеты."""

from __future__ import annotations

import customtkinter as ctk

from ui.theme import CARD, MUTED, TEXT


class StatCard(ctk.CTkFrame):
    def __init__(self, master, label: str, **kwargs):
        super().__init__(master, fg_color=CARD, corner_radius=14, **kwargs)
        self.value_lbl = ctk.CTkLabel(self, text="—", font=("Segoe UI", 22, "bold"), text_color=TEXT)
        self.value_lbl.pack(padx=14, pady=(12, 0), anchor="w")
        ctk.CTkLabel(self, text=label, font=("Segoe UI", 13), text_color=MUTED).pack(
            padx=14, pady=(0, 12), anchor="w")

    def set(self, value: str, color: str = TEXT):
        self.value_lbl.configure(text=value, text_color=color)
