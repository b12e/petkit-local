# PetKit BLE (local)

[![Validate](https://github.com/b12e/petkit-local/actions/workflows/validate.yml/badge.svg)](https://github.com/b12e/petkit-local/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

A Home Assistant integration that talks to PetKit Eversweet fountains **directly over
Bluetooth**. No PetKit account, no cloud round-trip, no second PetKit device acting as
a relay.

Built for the **Eversweet Max 2 (CTW3)**.

## Why this exists

The existing cloud integration reaches these fountains over Bluetooth *through PetKit's
servers*: it asks the cloud to have another PetKit WiFi device in your house relay
base64-encoded BLE frames to the fountain. A command travels to PetKit and back to reach
a device that is often in the same room.

This integration speaks the same protocol straight to the fountain's GATT service.

## Supported devices

| Model | Status |
| --- | --- |
| Eversweet Max 2 / CTW3 (incl. UV and 100 variants) | Supported |
| CTW2, W5, W4X | Discovered but not enabled - the CTW3 command set is assumed and untested |
| Yumshare feeders (D4SH), litter boxes, W7H | **Not possible.** Their Bluetooth radio only does WiFi provisioning and firmware updates; all control is cloud/MQTT |

## Installation

### HACS

Add this repository as a custom repository in HACS (HACS → Integrations → the three-dot
menu → Custom repositories), category **Integration**, then install **PetKit BLE (local)**
and restart Home Assistant.

### Manual

Copy `custom_components/petkit_ble` into your Home Assistant `config/custom_components/`
directory and restart.

Either way, the fountain should then be discovered automatically if it is in range of a
Bluetooth adapter or an ESPHome Bluetooth proxy. If it is not, add it from
**Settings → Devices & services → Add integration → PetKit BLE**.

## The secret, and what setup does to the PetKit app

Every fountain holds an 8-byte secret that is written when the device is bound and checked
on each connection. You have two options.

**Leave the secret blank (default).** Home Assistant derives its own key and claims the
fountain with it. Nothing needs the cloud, ever. The trade-off: the official PetKit app
loses its own claim, and the fountain needs a power cycle before the app can reconnect.
Pick this if Home Assistant is going to be the only thing talking to the fountain.

**Paste PetKit's own secret.** Read it once from the account API - it is the `secret`
field on the water fountain object, which `pypetkitapi` exposes - and enter it as 16 hex
characters during setup. Both the app and Home Assistant then keep working. Re-binding the
fountain in the app rotates it, and the integration will re-claim on the next failure.

## Entities

**Controls** - power, pump run/pause, mode (normal / smart), light ring, light brightness,
do-not-disturb, child lock, proximity detection, smart and battery duty-cycle timings,
reset filter.

**Readings** - battery percent and voltage, supply voltage, filter remaining and estimated
days left, pump runtime today and total, water purified today and total, energy used,
pump state, signal strength.

**Problems** - water low, filter due, fault, battery low, pet detected, mains power,
do-not-disturb active.

Water and energy figures are computed locally using the same coefficients the PetKit app
applies, so they should track what the app shows rather than being an independent guess.

## Battery life

The cordless model runs on battery, and every poll is a full connect, handshake, read,
disconnect. The default 120-second interval is a compromise; raise it in the integration's
options if you care more about runtime than freshness. The fountain also pushes state
changes on its own (command 230), which arrive without a poll while connected.

## Protocol notes

Service `0000aaa0`, write to `0000aaa2`, notify on `0000aaa1`. Frames are:

```
FA FC FD | cmd | type | seq | len_lo | len_hi | payload | FB
```

Length is little-endian; integers inside payloads are big-endian. There is no checksum and
no encryption. [`protocol.py`](custom_components/petkit_ble/protocol.py) documents the
command set and every byte offset.

Two details cost me time, so they are worth stating plainly:

- The characteristic names are inverted from what you would guess. `CONTROL_UUID` (`aaa2`)
  is the **write** handle and `DATA_UUID` (`aaa1`) is the **notify** handle.
- In a command 230 push the settings block starts at byte **30**, not 26 - the app's parser
  skips bytes 26-29 - and the push omits the two proximity bytes entirely.

## Testing

```bash
python -m pytest tests/ -q
```

The suite runs the client against a simulated fountain that speaks the real wire protocol,
including fragmented notifications and the settings read-modify-write cycle.

## Credit

The characteristic direction, the service-data model table, and the trick of claiming the
device with your own secret all come from
[slespersen/PetkitW5BLEMQTT](https://github.com/slespersen/PetkitW5BLEMQTT). Byte offsets
were verified against the decompiled PetKit app; where the two disagreed, the app won.

## Disclaimer

Not affiliated with PetKit. Derived from static analysis of the shipped Android app for
interoperability. Claiming a device with your own secret changes state on the hardware -
read the section above before you set it up.
