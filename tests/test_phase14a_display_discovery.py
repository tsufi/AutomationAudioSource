from pathlib import Path

import pytest

from screen_audio_router.display import DisplayDiscovery, DisplayIdentityKind, parse_edid


def manufacturer_code(name):
    return ((ord(name[0]) - 64) << 10) | ((ord(name[1]) - 64) << 5) | (ord(name[2]) - 64)


def edid(
    *,
    manufacturer="SAM",
    product=0x1234,
    serial=123,
    name="Odyssey",
    text_serial=None,
    extensions=0,
):
    block = bytearray(128 * (extensions + 1))
    block[:8] = b"\0\xff\xff\xff\xff\xff\xff\0"
    block[8:10] = manufacturer_code(manufacturer).to_bytes(2, "big")
    block[10:12] = product.to_bytes(2, "little")
    block[12:16] = serial.to_bytes(4, "little")
    descriptors = [(0xFC, name)]
    if text_serial is not None:
        descriptors.append((0xFF, text_serial))
    for offset, (tag, value) in zip((54, 72), descriptors, strict=False):
        block[offset : offset + 5] = bytes((0, 0, 0, tag, 0))
        encoded = value.encode()[:13]
        block[offset + 5 : offset + 5 + len(encoded)] = encoded
    block[126] = extensions
    for offset in range(0, len(block), 128):
        block[offset + 127] = (-sum(block[offset : offset + 127])) % 256
    return bytes(block)


def connector(root, name, status="connected", data=None):
    path = root / f"card1-{name}"
    path.mkdir()
    (path / "status").write_text(status)
    if data is not None:
        (path / "edid").write_bytes(data)
    return path


def test_valid_edid_parses_identity_fields_and_friendly_name(tmp_path):
    connector(tmp_path, "DP-2", data=edid(text_serial="ABC 123"))
    device = DisplayDiscovery(tmp_path).discover()[0]
    assert (device.manufacturer, device.product_id, device.model, device.serial) == (
        "SAM",
        "1234",
        "Odyssey",
        "ABC 123",
    )
    assert device.friendly_name == "Odyssey"
    assert device.identity_kind is DisplayIdentityKind.EDID_SERIAL


def test_connected_discovered_disconnected_and_unrelated_excluded(tmp_path):
    connector(tmp_path, "DP-1", data=edid())
    connector(tmp_path, "DP-2", "disconnected", edid())
    unrelated = tmp_path / "card1-render-1"
    unrelated.mkdir()
    (unrelated / "status").write_text("connected")
    assert [item.connector for item in DisplayDiscovery(tmp_path).discover()] == ["DP-1"]


@pytest.mark.parametrize("data,readable", [(None, False), (b"bad", True), (b"", True)])
def test_missing_short_or_malformed_edid_falls_back(tmp_path, data, readable):
    connector(tmp_path, "DP-3", data=data)
    device = DisplayDiscovery(tmp_path).discover()[0]
    assert device.identity_kind is DisplayIdentityKind.CONNECTOR_FALLBACK
    assert device.friendly_name == "DP-3"
    assert device.edid_readable is readable


def test_edid_read_oserror_keeps_connected_display(monkeypatch, tmp_path):
    path = connector(tmp_path, "DP-1", data=edid()) / "edid"
    original = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (_ for _ in ()).throw(OSError("no")) if self == path else original(self),
    )
    assert (
        DisplayDiscovery(tmp_path).discover()[0].identity_kind
        is DisplayIdentityKind.CONNECTOR_FALLBACK
    )


def test_bad_checksum_and_missing_declared_extension_rejected():
    damaged = bytearray(edid())
    damaged[20] ^= 1
    assert parse_edid(bytes(damaged)) is None
    missing = bytearray(edid())
    missing[126] = 1
    missing[127] = (-sum(missing[:127])) % 256
    assert parse_edid(bytes(missing)) is None


@pytest.mark.parametrize("serial", [0, 0xFFFFFFFF, 0x01010101, 0x01000100])
def test_obvious_numeric_serial_placeholders_use_fingerprint(serial):
    identity = parse_edid(edid(serial=serial))
    assert identity.serial is None
    assert identity.identity_kind is DisplayIdentityKind.EDID_FINGERPRINT


@pytest.mark.parametrize("serial", ["", "00000000", "UNKNOWN", "N/A"])
def test_obvious_text_serial_placeholders_are_not_trusted(serial):
    identity = parse_edid(edid(serial=0, text_serial=serial))
    assert identity.serial is None


def test_stable_id_is_deterministic_and_connector_independent(tmp_path):
    data = edid(text_serial="unique")
    connector(tmp_path, "DP-1", data=data)
    first = DisplayDiscovery(tmp_path).discover()[0]
    other = tmp_path / "other"
    other.mkdir()
    connector(other, "DP-9", data=data)
    second = DisplayDiscovery(other).discover()[0]
    assert first.stable_id == second.stable_id
    assert first.connector != second.connector


def test_same_model_different_serials_are_distinct(tmp_path):
    connector(tmp_path, "DP-1", data=edid(serial=1))
    connector(tmp_path, "DP-2", data=edid(serial=2))
    devices = DisplayDiscovery(tmp_path).discover()
    assert len({item.stable_id for item in devices}) == 2
    assert all(item.identity_kind is DisplayIdentityKind.EDID_SERIAL for item in devices)


def test_byte_identical_edids_without_serial_collision_falls_back(tmp_path):
    data = edid(serial=0)
    connector(tmp_path, "DP-1", data=data)
    connector(tmp_path, "DP-2", data=data)
    devices = DisplayDiscovery(tmp_path).discover()
    assert [item.identity_kind for item in devices] == [
        DisplayIdentityKind.CONNECTOR_FALLBACK,
        DisplayIdentityKind.CONNECTOR_FALLBACK,
    ]
    assert len({item.stable_id for item in devices}) == 2


def test_noncolliding_fingerprint_is_stable(tmp_path):
    connector(tmp_path, "HDMI-A-1", data=edid(serial=0))
    first = DisplayDiscovery(tmp_path).discover()
    second = DisplayDiscovery(tmp_path).discover()
    assert first == second
    assert first[0].identity_kind is DisplayIdentityKind.EDID_FINGERPRINT


def test_one_bad_display_does_not_hide_healthy_display_and_order_is_deterministic(tmp_path):
    connector(tmp_path, "DP-3", data=b"broken")
    connector(tmp_path, "DP-1", data=edid())
    devices = DisplayDiscovery(tmp_path).discover()
    assert [item.connector for item in devices] == ["DP-1", "DP-3"]
    assert devices[0].identity_kind is DisplayIdentityKind.EDID_SERIAL
    assert devices[1].identity_kind is DisplayIdentityKind.CONNECTOR_FALLBACK


def test_friendly_name_falls_back_to_manufacturer_product_then_connector(tmp_path):
    connector(tmp_path, "DP-1", data=edid(name=""))
    connector(tmp_path, "DP-2", data=b"bad")
    devices = DisplayDiscovery(tmp_path).discover()
    assert devices[0].friendly_name == "SAM 1234"
    assert devices[1].friendly_name == "DP-2"


def test_module_has_no_config_audio_routing_x11_or_random_dependencies():
    source = Path(
        __import__("screen_audio_router.display", fromlist=["DisplayDiscovery"]).__file__
    ).read_text()
    for forbidden in (
        "screen_audio_router.config",
        "screen_audio_router.audio",
        "screen_audio_router.router",
        "xrandr",
        "Xlib",
        "uuid4",
        "random",
    ):
        assert forbidden not in source


def test_production_modules_do_not_import_discovery():
    root = Path(__file__).parents[1] / "src" / "screen_audio_router"
    phase14_modules = {"display.py", "display_mapping.py"}
    consumers = [
        path
        for path in root.rglob("*.py")
        if path.name not in phase14_modules and "screen_audio_router.display" in path.read_text()
    ]
    assert {path.relative_to(root).as_posix() for path in consumers} <= {
        "diagnostics.py",
        "display_runtime.py",
        "gui/main_window.py",
        "gui/settings_window.py",
    }
