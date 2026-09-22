"""BLE Lab: живая работа с устройством (connect / read / write / notify).

Свой поток с asyncio-циклом: UI не блокируется, подписки прилетают
в фоне. Все операции — через очередь команд, результаты — наружу.
"""

from __future__ import annotations

import asyncio
import threading


class LabSession:
    """Одно подключение к BLE-устройству."""

    def __init__(self, on_notify):
        """
        on_notify: callable(handle:int, data:bytes) — вызывается из
        потока цикла; должен быть потокобезопасным (очередь).
        """
        self._on_notify = on_notify
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client = None
        self.addr: str | None = None
        self._subscribed: set[int] = set()

    # --- цикл ---
    def _ensure_loop(self):
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        daemon=True, name="ble-lab-loop")
        self._thread.start()

    def _call(self, coro, timeout: float = 30.0):
        self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout)

    # --- подключение ---
    def connect(self, address: str, timeout: float = 20.0) -> tuple[list[dict], str]:
        return self._call(self._c_connect(address.strip()), timeout + 10)

    async def _c_connect(self, address: str):
        from bleak import BleakClient

        from .ble import describe_services

        await self._c_disconnect()
        client = BleakClient(address, timeout=15.0)
        await client.connect()
        self._client = client
        self.addr = address
        self._subscribed = set()
        return describe_services(client.services), ""

    def disconnect(self) -> str:
        try:
            self._call(self._c_disconnect(), 15.0)
        except Exception as e:
            return f"Отключение с ошибкой: {e}"
        return "Отключено"

    async def _c_disconnect(self):
        client = self._client
        self._client = None
        self.addr = None
        self._subscribed = set()
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        c = self._client
        try:
            return bool(c is not None and c.is_connected)
        except Exception:
            return False

    # --- операции ---
    def _char_obj(self, handle: int):
        for svc in self._client.services:
            for ch in svc.characteristics:
                if getattr(ch, "handle", None) == handle:
                    return ch
        raise KeyError(f"handle 0x{handle:04X} не найден")

    def read(self, handle: int, timeout: float = 15.0) -> tuple[bytes, str]:
        return self._call(self._c_read(handle), timeout)

    async def _c_read(self, handle: int):
        raw = bytes(await self._client.read_gatt_char(self._char_obj(handle)))
        return raw, ""

    def write(self, handle: int, data: bytes, response: bool,
              timeout: float = 15.0) -> str:
        return self._call(self._c_write(handle, bytes(data), response), timeout)

    async def _c_write(self, handle: int, data: bytes, response: bool):
        await self._client.write_gatt_char(self._char_obj(handle), data,
                                           response=response)
        return "OK"

    def subscribe(self, handle: int, timeout: float = 15.0) -> str:
        return self._call(self._c_sub(handle), timeout)

    async def _c_sub(self, handle: int):
        def _cb(sender, data: bytearray):
            try:
                self._on_notify(int(getattr(sender, "handle", handle)), bytes(data))
            except Exception:
                pass

        await self._client.start_notify(self._char_obj(handle), _cb)
        self._subscribed.add(handle)
        return "OK"

    def unsubscribe(self, handle: int, timeout: float = 15.0) -> str:
        return self._call(self._c_unsub(handle), timeout)

    async def _c_unsub(self, handle: int):
        try:
            await self._client.stop_notify(self._char_obj(handle))
        finally:
            self._subscribed.discard(handle)
        return "OK"

    def subscribed_handles(self) -> set[int]:
        return set(self._subscribed)
