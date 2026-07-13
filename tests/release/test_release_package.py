import hashlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from unified_can_lin_host_tool.release.package import (
    ReleaseResource,
    ResourceKind,
    encode_release_package,
    load_verified_release_package,
    write_release_package,
)
from unified_can_lin_host_tool.release.project_config import ProjectCode, get_project_config


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _keys() -> tuple[Ed25519PrivateKey, bytes]:
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, public


def _resources() -> tuple[ReleaseResource, ReleaseResource, ReleaseResource]:
    config = get_project_config(ProjectCode.AS5PR)
    return (
        ReleaseResource(ResourceKind.BOOT, config.project_code, 0, 0, b"BOOT1"),
        ReleaseResource(
            ResourceKind.APP,
            config.authentication.app_target_id,
            config.memory.app_start,
            config.authentication.app_version,
            b"APP",
        ),
        ReleaseResource(
            ResourceKind.FLASH_DRIVER,
            config.authentication.flash_driver_target_id,
            config.memory.flash_driver_ram,
            config.authentication.flash_driver_version,
            b"FD123",
        ),
    )


def _valid_bytes() -> tuple[bytes, bytes]:
    private, public = _keys()
    payload = encode_release_package(
        _resources(),
        get_project_config(ProjectCode.AS5PR),
        build_id=bytes(range(32, 64)),
        build_commit=COMMIT,
        build_timestamp=1_784_000_000,
        key_id=7,
        private_key=private,
    )
    return payload, public


def test_signed_package_round_trip_has_stable_content_id(tmp_path: Path) -> None:
    payload, public = _valid_bytes()
    path = tmp_path / "as5pr.erel"
    path.write_bytes(payload)

    loaded = load_verified_release_package(path, ProjectCode.AS5PR, {7: public})

    assert loaded.release_set_id == hashlib.sha256(payload).hexdigest()
    assert loaded.build_commit == COMMIT
    assert tuple(item.kind for item in loaded.resources) == tuple(ResourceKind)
    assert tuple(item.content for item in loaded.resources) == tuple(
        item.content for item in _resources()
    )


def test_key_id_is_covered_by_signature(tmp_path: Path) -> None:
    payload, public = _valid_bytes()
    damaged = bytearray(payload)
    damaged[-68] ^= 1
    path = tmp_path / "wrong-key-id.erel"
    path.write_bytes(damaged)

    with pytest.raises(ValueError, match="unknown signing key|signature verification failed"):
        load_verified_release_package(path, ProjectCode.AS5PR, {7: public, 6: public})


@pytest.mark.parametrize("mutation", ["trailing", "padding", "resource_hash", "project"])
def test_noncanonical_or_modified_package_is_rejected(tmp_path: Path, mutation: str) -> None:
    payload, public = _valid_bytes()
    damaged = bytearray(payload)
    if mutation == "trailing":
        damaged += b"\x00"
    elif mutation == "padding":
        damaged[305] = 1
    elif mutation == "resource_hash":
        damaged[300] ^= 1
    else:
        damaged[12] ^= 1
    path = tmp_path / "damaged.erel"
    path.write_bytes(damaged)

    with pytest.raises(ValueError):
        load_verified_release_package(path, ProjectCode.AS5PR, {7: public})


def test_selected_project_must_match_package(tmp_path: Path) -> None:
    payload, public = _valid_bytes()
    path = tmp_path / "wrong-project.erel"
    path.write_bytes(payload)

    with pytest.raises(ValueError, match="selected project"):
        load_verified_release_package(path, ProjectCode.E68, {7: public})


def test_verified_writer_atomically_replaces_target(tmp_path: Path) -> None:
    payload, public = _valid_bytes()
    path = tmp_path / "release.erel"
    path.write_bytes(b"old")

    loaded = write_release_package(path, payload, ProjectCode.AS5PR, {7: public})

    assert path.read_bytes() == payload
    assert loaded.release_set_id == hashlib.sha256(payload).hexdigest()
    assert list(tmp_path.glob("*.tmp")) == []
