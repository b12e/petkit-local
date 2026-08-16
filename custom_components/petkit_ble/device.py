"""BLE client for PetKit fountains."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from . import protocol as p
from .const import (
    COMMAND_TIMEOUT,
    CONNECT_TIMEOUT,
    IDLE_DISCONNECT_DELAY,
    KEEP_ALIVE_ALWAYS,
    KEEP_ALIVE_AUTO,
    KEEP_ALIVE_NEVER,
    MODE_NORMAL,
    NOTIFY_UUID,
    RECONNECT_DELAY,
    WRITE_UUID,
)

_LOGGER = logging.getLogger(__name__)


def _prefers_write_response(client: Any) -> bool:
    """Decide how to write to the control characteristic.

    The app writes with a response, so that is the default. Some stacks only
    expose write-without-response on this handle; fall back when that is all
    the characteristic advertises.
    """
    try:
        char = client.services.get_characteristic(WRITE_UUID)
    except (AttributeError, TypeError):
        return True
    if char is None:
        return True
    properties = set(getattr(char, "properties", ()) or ())
    if "write" in properties:
        return True
    return "write-without-response" not in properties


class PetkitAuthError(Exception):
    """The fountain rejected our secret."""


class PetkitConnectionError(Exception):
    """The fountain could not be reached."""


class PetkitFountain:
    """Talks to one fountain over GATT.

    The link is normally held open (see `hold_link`), which keeps commands
    responsive and the cmd 230 push channel live. Buffered visit history is
    drained on every poll, so nothing is lost while disconnected.
    """

    def __init__(
        self,
        address: str,
        alias: str,
        secret: bytes | None = None,
        device_id: int | None = None,
        keep_alive: str = KEEP_ALIVE_AUTO,
    ) -> None:
        self.address = address
        self.alias = alias
        self.secret = secret
        self.device_id = device_id
        self.keep_alive = keep_alive
        self.serial: str | None = None
        self.firmware: str | None = None
        self.hardware: int | None = None

        self.state: dict[str, Any] = {}
        self.settings = p.FountainSettings()
        self._settings_known = False

        self._client: BleakClientWithServiceCache | None = None
        self._decoder = p.FrameDecoder()
        self._waiters: dict[int, asyncio.Future[p.Frame]] = {}
        self._seq = 0
        self._lock = asyncio.Lock()
        self._authenticated = False
        self._push_callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._history_callbacks: list[Callable[[list[p.WorkRecord]], None]] = []
        self._stream_chunks: dict[int, bytes] = {}
        self._stream_total = 0
        self._stream_frames_seen = 0
        self._records_this_sync = 0
        self._idle_task: asyncio.Task | None = None
        # Written with a response by default, matching the app. Downgraded only
        # if the characteristic turns out not to support it.
        self._write_response = True

    # --- public API -----------------------------------------------------

    def register_push_callback(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback for unsolicited state pushes (cmd 230)."""
        self._push_callbacks.append(cb)

    def register_history_callback(
        self, cb: Callable[[list[p.WorkRecord]], None]
    ) -> None:
        """Register a callback for buffered visit records (cmd 212)."""
        self._history_callbacks.append(cb)

    @property
    def claimed(self) -> bool:
        """Whether we hold a secret this fountain has accepted."""
        return self.secret is not None

    async def async_poll(self, ble_device: BLEDevice) -> dict[str, Any]:
        """Refresh state and return it."""

        async def _poll() -> dict[str, Any]:
            await self._sync_history()
            status = await self._request(p.CMD_STATUS)
            self.state.update(p.parse_status(status.payload))

            try:
                settings = await self._request(p.CMD_SETTINGS)
                self._apply_settings(p.parse_settings(settings.payload))
            except (TimeoutError, p.ProtocolError) as err:
                # Non-fatal: status alone still yields a usable device.
                _LOGGER.debug("%s: settings read failed: %s", self.address, err)

            self._add_derived()
            return dict(self.state)

        return await self._with_link(ble_device, _poll)

    async def async_send(
        self, ble_device: BLEDevice, cmd: int, payload: bytes = b""
    ) -> None:
        """Run a single command, then refresh status so the UI settles."""

        async def _send() -> None:
            await self._request(cmd, payload)
            status = await self._request(p.CMD_STATUS)
            self.state.update(p.parse_status(status.payload))
            self._add_derived()

        await self._with_link(ble_device, _send)

    # --- high-level commands -------------------------------------------

    async def async_set_power(self, ble_device: BLEDevice, on: bool) -> None:
        mode = self.state.get("mode") or MODE_NORMAL
        await self.async_send(
            ble_device, p.CMD_SET_MODE, p.mode_payload(on, mode, p.SELECTOR_POWER_MODE)
        )

    async def async_set_mode(self, ble_device: BLEDevice, mode: int) -> None:
        await self.async_send(
            ble_device,
            p.CMD_SET_MODE,
            p.mode_payload(True, mode, p.SELECTOR_POWER_MODE),
        )

    async def async_set_paused(self, ble_device: BLEDevice, paused: bool) -> None:
        await self.async_send(
            ble_device,
            p.CMD_SET_MODE,
            p.mode_payload(True, 0 if paused else 1, p.SELECTOR_RUN_PAUSE),
        )

    async def async_reset_filter(self, ble_device: BLEDevice) -> None:
        await self.async_send(ble_device, p.CMD_RESET_FILTER)

    async def async_update_settings(
        self, ble_device: BLEDevice, **changes: int
    ) -> None:
        """Change one or more settings.

        The firmware only accepts the whole 12-byte block, so unknown fields
        must be read back before anything can be written.
        """
        if not self._settings_known:
            await self.async_poll(ble_device)
        if not self._settings_known:
            raise PetkitConnectionError("settings unavailable; cannot write block")

        for key, value in changes.items():
            if not hasattr(self.settings, key):
                raise ValueError(f"unknown setting {key!r}")
            setattr(self.settings, key, value)

        await self.async_send(
            ble_device, p.CMD_SET_SETTINGS, p.settings_payload(self.settings)
        )

    # --- session -------------------------------------------------------

    async def _with_link(
        self, ble_device: BLEDevice, action: Callable[[], Any], attempts: int = 2
    ) -> Any:
        """Run `action` over a live, authenticated link.

        A held-open connection can be dropped by the fountain, by the proxy, or
        by the phone app taking the slot, and we only find out when a write
        fails. Tear down fully and rebuild once rather than surfacing that as an
        error to the user.
        """
        async with self._lock:
            self._cancel_idle_disconnect()
            last: Exception | None = None

            for attempt in range(attempts):
                try:
                    await self._connect(ble_device)
                    await self._ensure_session()
                    result = await action()
                except (PetkitConnectionError, BleakError, TimeoutError) as err:
                    last = err
                    _LOGGER.debug(
                        "%s: attempt %s/%s failed: %s",
                        self.address,
                        attempt + 1,
                        attempts,
                        err,
                    )
                    await self._teardown()
                    if attempt + 1 < attempts:
                        await asyncio.sleep(RECONNECT_DELAY)
                    continue
                else:
                    self._schedule_idle_disconnect()
                    return result

            assert last is not None
            if isinstance(last, PetkitConnectionError):
                raise last
            raise PetkitConnectionError(str(last)) from last

    async def _connect(self, ble_device: BLEDevice) -> None:
        if self._client is not None and self._client.is_connected:
            return

        self._decoder.reset()
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.address,
                self._on_disconnect,
                timeout=CONNECT_TIMEOUT,
            )
            self._write_response = _prefers_write_response(client)
            await client.start_notify(NOTIFY_UUID, self._on_notify)
        except (BleakError, TimeoutError) as err:
            raise PetkitConnectionError(
                f"cannot connect to {self.address}: {err}"
            ) from err
        self._client = client

    @property
    def hold_link(self) -> bool:
        """Whether to keep the connection open between operations.

        On auto this mirrors the fountain's own power source: a unit on mains
        stays connected, so commands are instant and cmd 230 pushes arrive
        live, while a unit that has fallen back to battery gets the link
        dropped between polls.
        """
        if self.keep_alive == KEEP_ALIVE_ALWAYS:
            return True
        if self.keep_alive == KEEP_ALIVE_NEVER:
            return False
        # Assume mains until the fountain tells us otherwise; a first poll on a
        # dropped link is cheap, a first poll that drains a battery is not.
        return bool(self.state.get("electric_status", 1))

    def _schedule_idle_disconnect(self) -> None:
        """Drop the link after a quiet spell, unless we are holding it open."""
        self._cancel_idle_disconnect()
        if self.hold_link:
            return
        self._idle_task = asyncio.create_task(self._idle_disconnect())

    def _cancel_idle_disconnect(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None

    async def _idle_disconnect(self) -> None:
        try:
            await asyncio.sleep(IDLE_DISCONNECT_DELAY)
        except asyncio.CancelledError:
            return
        async with self._lock:
            await self._teardown()

    async def async_disconnect(self) -> None:
        """Close the link; used when the config entry unloads."""
        self._cancel_idle_disconnect()
        async with self._lock:
            await self._teardown()

    async def _teardown(self) -> None:
        """Fully release the connection so the next attempt starts clean."""
        client, self._client = self._client, None
        self._authenticated = False
        self._decoder.reset()
        self._stream_chunks.clear()
        self._stream_total = 0
        if client is None:
            return
        try:
            if client.is_connected:
                await client.stop_notify(NOTIFY_UUID)
                await client.disconnect()
        except (BleakError, TimeoutError, EOFError, AttributeError) as err:
            _LOGGER.debug("%s: disconnect failed: %s", self.address, err)

    def _on_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        self._authenticated = False
        for future in self._waiters.values():
            if not future.done():
                future.set_exception(PetkitConnectionError("disconnected"))
        self._waiters.clear()

    async def _ensure_session(self) -> None:
        """Replay the handshake the firmware expects on every connection."""
        if self._authenticated:
            return

        identity = await self._request(p.CMD_IDENTITY)
        info = p.parse_identity(identity.payload)
        self.device_id = info["device_id"]
        if "serial" in info:
            self.serial = info["serial"]

        if self.secret is None:
            # First run: claim the device with a secret we derive ourselves.
            self.secret = p.derive_secret(self.device_id)
            await self._claim()

        try:
            await self._authenticate()
        except PetkitAuthError:
            # A stored secret can go stale if the device was re-bound in the
            # official app. Re-claim once before giving up.
            _LOGGER.warning("%s: secret rejected, re-claiming device", self.address)
            self.secret = p.derive_secret(self.device_id)
            await self._claim()
            await self._authenticate()

        await self._request(p.CMD_SET_TIME, p.time_payload())

        try:
            info_frame = await self._request(p.CMD_DEVICE_INFO)
            parsed = p.parse_device_info(info_frame.payload)
            self.hardware = parsed["hardware"]
            self.firmware = parsed["firmware_version"]
        except (TimeoutError, p.ProtocolError) as err:
            _LOGGER.debug("%s: version read failed: %s", self.address, err)

        self._authenticated = True

    async def _sync_history(self) -> None:
        """Ask the fountain to upload the visits it recorded while we were away.

        The transfer is driven by the device: it sends chunks, then asks which
        arrived (cmd 67) and finally signals the end (cmd 69). Both are handled
        in the notification path, so this only kicks it off. Failures are
        non-fatal - a poll is still useful without history.
        """
        self._stream_chunks.clear()
        self._stream_total = 0
        self._stream_frames_seen = 0
        self._records_this_sync = 0
        try:
            frame = await self._request(p.CMD_HISTORY)
        except (TimeoutError, PetkitConnectionError, p.ProtocolError) as err:
            _LOGGER.debug("%s: history sync failed: %s", self.address, err)
            return

        # Chunks arrive asynchronously afterwards; the outcome is logged when
        # the device closes the stream, so nothing is blocked waiting here.
        _LOGGER.debug(
            "%s: history sync requested, cmd 212 replied %s",
            self.address,
            frame.payload.hex() or "<empty>",
        )

    async def _claim(self) -> None:
        assert self.device_id is not None and self.secret is not None
        frame = await self._request(
            p.CMD_INIT_DEVICE, p.init_device_payload(self.device_id, self.secret)
        )
        if not frame.payload or frame.payload[0] != 1:
            raise PetkitAuthError("device refused cmd 73 (claim)")

    async def _authenticate(self) -> None:
        assert self.secret is not None
        frame = await self._request(p.CMD_SECURITY_CHECK, p.secret_payload(self.secret))
        if not frame.payload or frame.payload[0] != 1:
            raise PetkitAuthError("device refused the secret")

    # --- transport ------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        return self._seq

    async def _request(
        self, cmd: int, payload: bytes = b"", timeout: float = COMMAND_TIMEOUT
    ) -> p.Frame:
        """Write a command and wait for the response carrying the same cmd."""
        if self._client is None or not self._client.is_connected:
            raise PetkitConnectionError("not connected")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[p.Frame] = loop.create_future()
        self._waiters[cmd] = future

        frame = p.build_frame(cmd, payload, seq=self._next_seq())
        try:
            await self._client.write_gatt_char(
                WRITE_UUID, frame, response=self._write_response
            )
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            _LOGGER.debug("%s: cmd %s timed out", self.address, cmd)
            raise
        except BleakError as err:
            raise PetkitConnectionError(f"write failed: {err}") from err
        finally:
            self._waiters.pop(cmd, None)

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        for frame in self._decoder.feed(bytes(data)):
            self._handle_frame(frame)

    def _handle_frame(self, frame: p.Frame) -> None:
        # Bulk history chunks: buffer by index, acknowledged separately.
        if frame.is_stream and frame.cmd in p.STREAM_DATA_CMDS:
            self._stream_chunks[frame.index] = frame.payload
            self._stream_total = frame.total or self._stream_total
            self._stream_frames_seen += 1
            return

        if frame.type == p.TYPE_REQUEST:
            # Device-initiated. These are requests to us, not replies.
            if frame.cmd == p.CMD_STREAM_ACK:
                self._handle_stream_ack(frame)
                return
            if frame.cmd == p.CMD_STREAM_END:
                self._handle_stream_end(frame)
                return

        if frame.cmd == p.CMD_PUSH_STATUS and frame.type != p.TYPE_RESPONSE:
            self._handle_push(frame)
            return

        future = self._waiters.get(frame.cmd)
        if future is not None and not future.done():
            future.set_result(frame)
        else:
            _LOGGER.debug("%s: unsolicited cmd %s", self.address, frame.cmd)

    def _handle_stream_ack(self, frame: p.Frame) -> None:
        """Tell the device which chunks arrived, and drain a complete window.

        The bitmap doubles as the retransmit request: chunks we omit get sent
        again, so the window is only consumed once it is whole.
        """
        received = set(self._stream_chunks)
        ack = p.build_frame(
            p.CMD_STREAM_ACK,
            p.stream_ack_payload(received),
            seq=frame.seq,
            msg_type=p.TYPE_RESPONSE,
        )

        if p.stream_is_complete(received, self._stream_total):
            payload = b"".join(
                self._stream_chunks[i] for i in sorted(self._stream_chunks)
            )
            self._stream_chunks.clear()
            self._emit_records(payload)

        asyncio.create_task(self._write_quietly(ack))

    def _handle_stream_end(self, frame: p.Frame) -> None:
        """Acknowledge the end of the transfer and flush anything left."""
        if self._stream_chunks:
            payload = b"".join(
                self._stream_chunks[i] for i in sorted(self._stream_chunks)
            )
            self._stream_chunks.clear()
            self._emit_records(payload)

        _LOGGER.debug(
            "%s: history sync done - %s stream frame(s), %s record(s)",
            self.address,
            self._stream_frames_seen,
            self._records_this_sync,
        )
        self._stream_total = 0
        ack = p.build_frame(
            p.CMD_STREAM_END, b"", seq=frame.seq, msg_type=p.TYPE_RESPONSE
        )
        asyncio.create_task(self._write_quietly(ack))

    def _emit_records(self, payload: bytes) -> None:
        """Hand decoded visit records to whoever is listening."""
        records = p.parse_work_records(payload)
        self._records_this_sync += len(records)
        if not records:
            _LOGGER.debug(
                "%s: history chunk held no records (%s bytes)",
                self.address,
                len(payload),
            )
            return
        _LOGGER.debug(
            "%s: %s history record(s), first raw_time=%s stay=%ss",
            self.address,
            len(records),
            records[0].raw_time,
            records[0].stay_seconds,
        )
        for callback in self._history_callbacks:
            callback(records)

    def _handle_push(self, frame: p.Frame) -> None:
        """Device volunteered a state change; parse it and acknowledge."""
        try:
            self.state.update(p.parse_push_status(frame.payload))
        except p.ProtocolError as err:
            _LOGGER.debug("%s: bad push payload: %s", self.address, err)
            return

        self._apply_settings(self.state, authoritative=False)
        self._add_derived()

        if self._client is not None and self._client.is_connected:
            ack = p.build_frame(
                p.CMD_PUSH_STATUS,
                b"\x01",
                seq=self._next_seq(),
                msg_type=p.TYPE_RESPONSE,
            )
            asyncio.create_task(self._write_quietly(ack))

        for callback in self._push_callbacks:
            callback(dict(self.state))

    async def _write_quietly(self, frame: bytes) -> None:
        try:
            if self._client is not None and self._client.is_connected:
                await self._client.write_gatt_char(WRITE_UUID, frame, response=False)
        except (BleakError, TimeoutError) as err:
            _LOGGER.debug("%s: ack write failed: %s", self.address, err)

    # --- state helpers --------------------------------------------------

    def _apply_settings(
        self, values: dict[str, Any], authoritative: bool = True
    ) -> None:
        """Mirror parsed settings into the cached block and the state dict.

        Only a cmd 211 read is authoritative: the cmd 230 push omits the two
        proximity switches, so treating it as complete would zero them on the
        next write.
        """
        for key in self.settings.as_dict():
            if values.get(key) is not None:
                setattr(self.settings, key, int(values[key]))
        if authoritative:
            self._settings_known = True
        self.state.update(self.settings.as_dict())

    def _add_derived(self) -> None:
        """Recompute the figures the cloud would normally hand back."""
        runtime = self.state.get("pump_runtime")
        runtime_today = self.state.get("pump_runtime_today")

        if runtime is not None:
            self.state["water_purified"] = round(
                p.water_purified_litres(self.alias, runtime), 2
            )
            self.state["energy_consumed"] = round(p.energy_kwh(self.alias, runtime), 4)
        if runtime_today is not None:
            self.state["water_purified_today"] = round(
                p.water_purified_litres(self.alias, runtime_today), 2
            )

        filter_percent = self.state.get("filter_percent")
        if filter_percent is not None:
            self.state["filter_days_left"] = p.filter_days_left(
                filter_percent,
                self.state.get("mode", 1),
                self.settings.smart_working_time,
                self.settings.smart_sleep_time,
            )
