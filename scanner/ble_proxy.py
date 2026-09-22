"""BLE Proxy (MITM-мост): телефон -> ПК(клон GATT) -> реальное устройство.

ПК публикует локальный GATT-сервис с теми же UUID/свойствами
(WinRT GattServiceProvider) и рекламирует его. Телефон считает ПК
устройством, а мост пересылает чтения/записи/нотификации настоящему
устройству (bleak) и пишет ВЕСЬ трафик в лог.

Условия работы (как в оригинальном MXW01-мосте):
- устройство без pairing/bonding (или Just Works);
- приложение на телефоне ищет устройство по сервису, а не по жёсткому MAC;
- пока ПК держит соединение, оригинал обычно перестаёт рекламироваться,
  и телефон видит только клон.
"""

from __future__ import annotations

import asyncio
import threading
import uuid as uuid_mod


def _props_to_winrt(bleak_props: list[str]):
    from winrt.windows.devices.bluetooth.genericattributeprofile import (
        GattCharacteristicProperties as P,
    )

    mapping = {
        "read": P.READ,
        "write-without-response": P.WRITE_WITHOUT_RESPONSE,
        "write": P.WRITE,
        "notify": P.NOTIFY,
        "indicate": P.INDICATE,
    }
    combined = None
    for name in bleak_props or []:
        flag = mapping.get(name)
        if flag is not None:
            combined = flag if combined is None else combined | flag
    return combined


def _make_buffer(data: bytes):
    from winrt.windows.storage.streams import DataWriter

    w = DataWriter()
    w.write_bytes(bytes(data))
    return w.detach_buffer()


def _sub_count(local) -> int:
    """Число подписанных клиентов. -1 если узнать не удалось."""
    try:
        return len(local.subscribed_clients)
    except Exception:
        pass
    try:
        return int(local.subscribed_clients.size)
    except Exception:
        pass
    try:
        return sum(1 for _ in local.subscribed_clients)
    except Exception:
        return -1


class ProxyBridge:
    """Один мост на один сервис. on_event(kind, text) — потокобезопасный."""

    def __init__(self, on_event):
        self._on_event = on_event
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client = None
        self._provider = None
        self._adv_token = None
        self._locals: dict[int, dict] = {}  # handle -> {...}
        self._char_objs: dict[int, object] = {}
        self.addr: str | None = None
        self.service_uuid: str | None = None
        self.running = False

    # ---------- цикл ----------
    def _ensure_loop(self):
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        daemon=True, name="ble-proxy-loop")
        self._thread.start()

    def _call(self, coro, timeout: float = 60.0):
        self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout)

    def _ev(self, kind: str, text: str):
        try:
            self._on_event(kind, text)
        except Exception:
            pass

    # ---------- старт/стоп ----------
    def start(self, address: str, service_uuid: str,
              timeout: float = 60.0) -> str:
        return self._call(self._c_start(address.strip(), service_uuid.strip()),
                          timeout)

    async def _await_op(self, op):
        return await op

    def _await_sync(self, op, timeout: float = 20.0):
        fut = asyncio.run_coroutine_threadsafe(self._await_op(op), self._loop)
        return fut.result(timeout)

    async def _c_start(self, address: str, service_uuid: str) -> str:
        from bleak import BleakClient

        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattLocalCharacteristicParameters,
            GattProtectionLevel,
            GattServiceProvider,
            GattServiceProviderAdvertisingParameters,
        )

        if self.running:
            return "Мост уже запущен"
        # 1. central: соединение с оригиналом
        self._ev("status", f"подключение к оригиналу {address}…")
        client = BleakClient(address, timeout=15.0)
        await client.connect()
        self._client = client
        self.addr = address
        self._ev("status", f"оригинал подключён: {address}")

        # 2. ищем сервис
        svc = None
        for s in client.services:
            if str(s.uuid).lower() == service_uuid.lower():
                svc = s
                break
        if svc is None:
            have = ", ".join(str(s.uuid) for s in client.services)
            await client.disconnect()
            self._client = None
            raise ValueError(f"Сервис {service_uuid} не найден. Есть: {have}")

        # 3. публикуем клон
        res = await GattServiceProvider.create_async(uuid_mod.UUID(service_uuid))
        if int(res.error) != 0:
            raise OSError(f"GattServiceProvider error: {res.error}")
        provider = res.service_provider
        self._provider = provider
        self.service_uuid = service_uuid

        # 4. клонируем характеристики
        cloned = 0
        for ch in svc.characteristics:
            props = list(getattr(ch, "properties", []) or [])
            win_props = _props_to_winrt(props)
            if win_props is None:
                self._ev("status", f"пропуск {ch.uuid} (нет релевантных свойств)")
                continue
            params = GattLocalCharacteristicParameters()
            params.characteristic_properties = win_props
            if "read" in props:
                params.read_protection_level = GattProtectionLevel.PLAIN
            if "write" in props or "write-without-response" in props:
                params.write_protection_level = GattProtectionLevel.PLAIN
            cres = await provider.service.create_characteristic_async(
                uuid_mod.UUID(str(ch.uuid)), params)
            if int(cres.error) != 0:
                self._ev("status", f"не создана {ch.uuid}: {cres.error}")
                continue
            local = cres.characteristic
            handle = int(getattr(ch, "handle", 0) or 0)
            info = {"local": local, "uuid": str(ch.uuid),
                    "props": props, "handle": handle, "tokens": []}
            if "read" in props:
                info["tokens"].append(
                    ("read", local.add_read_requested(self._on_phone_read)))
            if "write" in props or "write-without-response" in props:
                info["tokens"].append(
                    ("write", local.add_write_requested(self._on_phone_write)))
            info["tokens"].append(
                ("sub", local.add_subscribed_clients_changed(self._on_sub_changed)))
            self._locals[handle] = info
            self._char_objs[handle] = ch
            cloned += 1
        if cloned == 0:
            raise ValueError("Ни одна характеристика не склонирована")

        # 5. подписываемся на нотификации оригинала
        for handle, info in self._locals.items():
            if "notify" in info["props"] or "indicate" in info["props"]:
                try:
                    await client.start_notify(self._char_objs[handle],
                                              self._bleak_notify_cb)
                    self._ev("status", f"подписка на {info['uuid']}: OK")
                except Exception as e:
                    self._ev("status", f"подписка на {info['uuid']}: {e}")

        # 6. реклама
        adv = GattServiceProviderAdvertisingParameters()
        adv.is_connectable = True
        adv.is_discoverable = True
        self._adv_token = provider.add_advertisement_status_changed(
            self._on_adv_status)
        provider.start_advertising_with_parameters(adv)
        self.running = True
        self._ev("status", f"мост запущен: склонировано характеристик: {cloned}")
        return (f"Мост запущен. Сервис {service_uuid}, "
                f"характеристик: {cloned}. Подключайте телефон.")

    def stop(self, timeout: float = 30.0) -> str:
        try:
            return self._call(self._c_stop(), timeout)
        except Exception as e:
            return f"Остановка с ошибкой: {e}"

    async def _c_stop(self) -> str:
        if self._provider is not None:
            try:
                if self._adv_token is not None:
                    self._provider.remove_advertisement_status_changed(
                        self._adv_token)
            except Exception:
                pass
            try:
                self._provider.stop_advertising()
            except Exception:
                pass
        for info in self._locals.values():
            local = info["local"]
            for kind, tok in info.get("tokens", []):
                try:
                    if kind == "read":
                        local.remove_read_requested(tok)
                    elif kind == "write":
                        local.remove_write_requested(tok)
                    else:
                        local.remove_subscribed_clients_changed(tok)
                except Exception:
                    pass
        self._locals = {}
        self._char_objs = {}
        self._provider = None
        self._adv_token = None
        if self._client is not None:
            try:
                for ch in self._char_objs.values():
                    try:
                        await self._client.stop_notify(ch)
                    except Exception:
                        pass
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self.running = False
        self._ev("status", "мост остановлен")
        return "Мост остановлен"

    # ---------- события WinRT (чужие потоки) ----------
    def _on_adv_status(self, sender, args):
        try:
            st = int(self._provider.advertisement_status)
        except Exception:
            st = -1
        names = {0: "CREATED", 1: "STOPPED", 2: "STARTED",
                 3: "ABORTED", 4: "STARTED_WITHOUT_ALL_DATA"}
        self._ev("adv", f"реклама: {names.get(st, st)}")

    def _on_sub_changed(self, sender, args):
        n = _sub_count(sender)
        try:
            uid = str(sender.uuid)
        except Exception:
            uid = "?"
        self._ev("phone", f"подписки {uid}: клиентов {n}")

    def _find_local(self, sender) -> dict | None:
        try:
            uid = str(sender.uuid).lower()
        except Exception:
            return None
        for info in self._locals.values():
            if info["uuid"].lower() == uid:
                return info
        return None

    def _on_phone_read(self, sender, args):
        from scanner.uuids import fmt_data

        info = self._find_local(sender)
        uid = info["uuid"] if info else "?"
        try:
            deferral = args.get_deferral()
        except Exception:
            deferral = None
        try:
            req = self._await_sync(args.get_request_async(), 15.0)
            if req is None:
                return
            if info is None:
                # Неизвестная характеристика: отвечаем пустым, чтобы
                # телефон не висел до таймаута
                try:
                    req.respond_with_value(_make_buffer(b""))
                except Exception:
                    pass
                return
            data = self._call(self._c_forward_read(info), 20.0)
            req.respond_with_value(_make_buffer(data))
            self._ev("phone", f"READ {uid} -> {fmt_data(data)}")
        except Exception as e:
            self._ev("phone", f"READ {uid} ОШИБКА: {e}")
            try:
                req.respond_with_value(_make_buffer(b""))
            except Exception:
                pass
        finally:
            try:
                if deferral is not None:
                    deferral.complete()
            except Exception:
                pass

    async def _c_forward_read(self, info: dict) -> bytes:
        raw = bytes(await self._client.read_gatt_char(info["uuid"]))
        return raw

    def _on_phone_write(self, sender, args):
        from scanner.uuids import fmt_data

        info = self._find_local(sender)
        uid = info["uuid"] if info else "?"
        try:
            deferral = args.get_deferral()
        except Exception:
            deferral = None
        try:
            req = self._await_sync(args.get_request_async(), 15.0)
            if req is None or info is None:
                return
            data = bytes(req.value)
            with_resp = int(req.option) == 0
            self._ev("phone", f"WRITE {uid} ({'ответ' if with_resp else 'без ответа'}): "
                              f"{fmt_data(data)}")
            self._call(self._c_forward_write(info, data, with_resp), 20.0)
            if with_resp:
                req.respond()
        except Exception as e:
            self._ev("phone", f"WRITE {uid} ОШИБКА: {e}")
        finally:
            try:
                if deferral is not None:
                    deferral.complete()
            except Exception:
                pass

    async def _c_forward_write(self, info: dict, data: bytes, response: bool):
        await self._client.write_gatt_char(info["uuid"], bytes(data),
                                           response=response)

    # ---------- нотификации оригинала (поток цикла) ----------
    def _bleak_notify_cb(self, sender, data: bytearray):
        from scanner.uuids import fmt_data

        try:
            handle = int(getattr(sender, "handle", 0) or 0)
        except Exception:
            handle = 0
        info = self._locals.get(handle)
        raw = bytes(data)
        uid = info["uuid"] if info else str(getattr(sender, "uuid", "?"))
        self._ev("target", f"NOTIFY {uid}: {fmt_data(raw)}")
        if info is None:
            return
        subs = _sub_count(info["local"])
        if subs == 0:
            self._ev("phone", f"NOTIFY {uid}: нет подписчиков, отброшен")
            return
        buf = _make_buffer(raw)

        async def _notify():
            try:
                results = await info["local"].notify_value_async(buf)
                try:
                    sts = ",".join(str(int(r.status)) for r in results)
                except Exception:
                    sts = "?"
                self._ev("phone", f"NOTIFY {uid} доставлен ({subs} подп., {sts}): "
                                  f"{fmt_data(raw)}")
            except Exception as e:
                self._ev("phone", f"NOTIFY {uid} ошибка доставки: {e}")

        try:
            asyncio.run_coroutine_threadsafe(_notify(), self._loop)
        except Exception as e:
            self._ev("phone", f"NOTIFY {uid} не отправлен: {e}")
