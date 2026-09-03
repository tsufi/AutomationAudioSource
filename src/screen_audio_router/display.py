"""Read-only physical display discovery for DRM/KDE Wayland systems."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"
_CONNECTOR = re.compile(r"card\d+-(?:DP|HDMI-A|eDP|DVI-[AID]|VGA|DSI)-\d+$")
_INVALID_NUMERIC_SERIALS = {0, 0xFFFFFFFF, 0x01010101, 0x01000100}
_INVALID_TEXT_SERIALS = {"", "0", "00000000", "ffffffff", "unknown", "none", "n/a"}


class DisplayIdentityKind(StrEnum):
    EDID_SERIAL = "edid_serial"
    EDID_FINGERPRINT = "edid_fingerprint"
    CONNECTOR_FALLBACK = "connector_fallback"


@dataclass(frozen=True, slots=True)
class EdidIdentity:
    manufacturer: str
    product_id: str
    model: str | None
    serial: str | None
    fingerprint: str
    identity_kind: DisplayIdentityKind
    stable_id: str


@dataclass(frozen=True, slots=True)
class DisplayDevice:
    stable_id: str
    identity_kind: DisplayIdentityKind
    connector: str
    connected: bool
    friendly_name: str
    manufacturer: str | None = None
    model: str | None = None
    product_id: str | None = None
    serial: str | None = None
    edid_readable: bool = False


def parse_edid(data: bytes) -> EdidIdentity | None:
    """Return identity data only when every declared EDID block is valid."""
    if len(data) < 128 or data[:8] != EDID_HEADER:
        return None
    declared_length = (1 + data[126]) * 128
    if len(data) < declared_length:
        return None
    declared = data[:declared_length]
    if any(sum(declared[offset : offset + 128]) % 256 for offset in range(0, declared_length, 128)):
        return None
    manufacturer = _manufacturer(data[8:10])
    if manufacturer is None:
        return None
    product_id = f"{int.from_bytes(data[10:12], 'little'):04x}"
    model = _normalize_descriptor(_descriptor(data, 0xFC))
    text_serial = _trusted_text_serial(_descriptor(data, 0xFF))
    numeric = int.from_bytes(data[12:16], "little")
    serial = text_serial or (str(numeric) if numeric not in _INVALID_NUMERIC_SERIALS else None)
    fingerprint = hashlib.sha256(declared).hexdigest()
    if serial:
        material = f"{manufacturer.casefold()}\x1f{product_id}\x1f{serial.casefold()}"
        kind = DisplayIdentityKind.EDID_SERIAL
    else:
        material = fingerprint
        kind = DisplayIdentityKind.EDID_FINGERPRINT
    digest = hashlib.sha256(material.encode()).hexdigest()[:24]
    return EdidIdentity(
        manufacturer, product_id, model, serial, fingerprint, kind, f"{kind.value}:{digest}"
    )


class DisplayDiscovery:
    """Enumerate connected DRM connectors without modifying system state."""

    def __init__(self, drm_root: Path = Path("/sys/class/drm")) -> None:
        self.drm_root = drm_root

    def discover(self) -> tuple[DisplayDevice, ...]:
        devices: list[DisplayDevice] = []
        for status_path in sorted(self.drm_root.glob("card*-*/status")):
            if not _CONNECTOR.fullmatch(status_path.parent.name):
                continue
            try:
                connected = status_path.read_text(encoding="ascii").strip() == "connected"
            except OSError:
                continue
            if not connected:
                continue
            connector = status_path.parent.name.split("-", 1)[1]
            readable = True
            try:
                data = (status_path.parent / "edid").read_bytes()
            except OSError:
                data, readable = b"", False
            devices.append(_device(connector, parse_edid(data), readable))
        collisions = {
            device.stable_id
            for device in devices
            if device.identity_kind is not DisplayIdentityKind.CONNECTOR_FALLBACK
            and sum(other.stable_id == device.stable_id for other in devices) > 1
        }
        return tuple(
            _fallback(device.connector, edid_readable=device.edid_readable)
            if device.stable_id in collisions
            else device
            for device in devices
        )


def _device(connector: str, identity: EdidIdentity | None, readable: bool) -> DisplayDevice:
    if identity is None:
        return _fallback(connector, edid_readable=readable)
    friendly = identity.model or f"{identity.manufacturer} {identity.product_id}"
    return DisplayDevice(
        identity.stable_id,
        identity.identity_kind,
        connector,
        True,
        friendly,
        identity.manufacturer,
        identity.model,
        identity.product_id,
        identity.serial,
        readable,
    )


def _fallback(connector: str, *, edid_readable: bool) -> DisplayDevice:
    return DisplayDevice(
        f"connector:{connector}",
        DisplayIdentityKind.CONNECTOR_FALLBACK,
        connector,
        True,
        connector,
        edid_readable=edid_readable,
    )


def _manufacturer(raw: bytes) -> str | None:
    if len(raw) != 2:
        return None
    code = int.from_bytes(raw, "big")
    values = tuple((code >> shift) & 31 for shift in (10, 5, 0))
    return (
        "".join(chr(value + 64) for value in values)
        if all(1 <= value <= 26 for value in values)
        else None
    )


def _descriptor(data: bytes, tag: int) -> str | None:
    for offset in range(54, 126, 18):
        block = data[offset : offset + 18]
        if block[:5] == bytes((0, 0, 0, tag, 0)):
            return block[5:18].decode("ascii", "ignore")
    return None


def _normalize_descriptor(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.replace("\x00", "").split()).strip()
    return normalized or None


def _trusted_text_serial(value: str | None) -> str | None:
    normalized = _normalize_descriptor(value)
    return (
        None if normalized is None or normalized.casefold() in _INVALID_TEXT_SERIALS else normalized
    )
