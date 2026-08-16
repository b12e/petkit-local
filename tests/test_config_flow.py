"""Tests for discovery identification and secret parsing."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from custom_components.petkit_ble.config_flow import (
    InvalidSecret,
    _identify,
    _normalise_secret,
)


@dataclass
class FakeServiceInfo:
    """Stand-in for BluetoothServiceInfoBleak."""

    name: str = ""
    address: str = "AA:BB:CC:DD:EE:FF"
    service_data: dict[str, bytes] = field(default_factory=dict)


# --- model identification -----------------------------------------------


def test_identifies_eversweet_max_2_from_service_data():
    """Byte 5 == 247 is the Eversweet Max 2."""
    info = FakeServiceInfo(service_data={"x": bytes([0, 1, 2, 3, 4, 247])})
    identified = _identify(info)
    assert identified["alias"] == "CTW3"
    assert identified["model"] == "Eversweet Max 2"


def test_identifies_uv_variant():
    info = FakeServiceInfo(service_data={"x": bytes([0, 0, 0, 0, 0, 249])})
    assert _identify(info)["model"] == "Eversweet Max 2 (UV)"


def test_service_data_wins_over_name():
    info = FakeServiceInfo(
        name="Petkit_CTW3", service_data={"x": bytes([0, 0, 0, 0, 0, 247])}
    )
    assert _identify(info)["model"] == "Eversweet Max 2"


def test_falls_back_to_exact_local_name():
    """Proxies sometimes strip service data; the name still identifies it."""
    assert (
        _identify(FakeServiceInfo(name="Petkit_CTW3_100"))["model"] == "Eversweet Max 2"
    )


def test_unknown_ctw3_revision_assumed_compatible():
    identified = _identify(FakeServiceInfo(name="Petkit_CTW3_XYZ"))
    assert identified["alias"] == "CTW3"


def test_ignores_unrelated_devices():
    assert _identify(FakeServiceInfo(name="SomeOtherKettle")) is None
    assert _identify(FakeServiceInfo()) is None


def test_unknown_service_data_id_falls_through():
    """An unrecognised model id must not be reported as supported."""
    assert (
        _identify(FakeServiceInfo(service_data={"x": bytes([0, 0, 0, 0, 0, 99])}))
        is None
    )


# --- secret parsing -----------------------------------------------------


def test_blank_secret_means_claim():
    assert _normalise_secret(None) is None
    assert _normalise_secret("") is None
    assert _normalise_secret("   ") is None


@pytest.mark.parametrize(
    "raw",
    [
        "a1b2c3d4e5f60718",
        "A1B2C3D4E5F60718",
        "0xa1b2c3d4e5f60718",
        "a1:b2:c3:d4:e5:f6:07:18",
        "a1 b2 c3 d4 e5 f6 07 18",
        "a1-b2-c3-d4-e5-f6-07-18",
    ],
)
def test_accepts_common_secret_formats(raw):
    assert _normalise_secret(raw) == "a1b2c3d4e5f60718"


def test_accepts_six_byte_secret_from_the_api():
    """A real CTW3 secret is 6 bytes; the firmware wants 8, left-padded.

    Regression test: rejecting these made the cloud-secret path unusable on
    the exact device this integration was written for.
    """
    assert _normalise_secret("1a2b3c4d5e6f") == "00001a2b3c4d5e6f"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01", "0000000000000001"),
        ("1a2b3c4d5e6f", "00001a2b3c4d5e6f"),
        ("1a:2b:3c:4d:5e:6f", "00001a2b3c4d5e6f"),
        ("a1b2c3d4e5f60718", "a1b2c3d4e5f60718"),
    ],
)
def test_short_secrets_are_left_padded(raw, expected):
    assert _normalise_secret(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "nothex",
        "a1b2c3d4e5f6071812",  # longer than 8 bytes
        "a1b2c3d4e5f6071",  # odd number of hex digits
    ],
)
def test_rejects_bad_secrets(raw):
    with pytest.raises(InvalidSecret):
        _normalise_secret(raw)
