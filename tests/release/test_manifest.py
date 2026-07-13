from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from unified_can_lin_host_tool.release.manifest import (
    load_verified_manifest,
    resolve_bundle_resource,
)


def _document(resources: dict | None = None) -> dict:
    return {
        "schemaVersion": 1,
        "bundleId": "bundle-001",
        "targetId": "fm33ht-as5pr",
        "projectId": "AS5PR",
        "version": "1.2.3",
        "source": {"commit": "abc123", "dirty": False, "toolchain": "gcc-13", "configHash": "cafe", "builtAt": "2026-07-13T00:00:00Z"},
        "memory": {"appStart": 0x7000, "appEnd": 0x1FFFF, "pageSize": 512, "flashDriverRam": 0x20001000, "flashDriverMaxSize": 8192},
        "normalization": {"start": 0x7000, "end": 0x1FFFF, "gapFill": 0xFF},
        "authentication": {"formatVersion": 1, "signPolicyId": "ed25519-v1", "keyId": "test-key"},
        "workflow": {"name": "can-ota", "version": 1},
        "resources": resources or {},
    }


def _bundle(tmp_path: Path, mutate: callable | None = None):
    root = tmp_path / "bundle"
    root.mkdir(parents=True)
    resources = {}
    for resource_id, (name, kind, data) in {
        "profile": ("profile.yaml", "profile", b"target: AS5PR\n"),
        "boot": ("boot.bin", "boot", b"boot-image"),
        "flash_driver": ("flash_driver.bin", "flash_driver", b"driver-image"),
    }.items():
        (root / name).write_bytes(data)
        resources[resource_id] = {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "kind": kind, "bundleId": "bundle-001", "targetId": "fm33ht-as5pr"}
    document = _document(resources)
    if mutate:
        mutate(document, root)
    raw = yaml.safe_dump(document, sort_keys=False).encode()
    private = Ed25519PrivateKey.generate()
    (root / "manifest.yaml").write_bytes(raw)
    (root / "manifest.sig").write_bytes(private.sign(raw))
    public = private.public_key()
    public_raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return root, document, public, public_raw


def test_valid_signature_schema_and_resources_are_immutable_and_readable(tmp_path):
    root, _, public, public_raw = _bundle(tmp_path)
    manifest = load_verified_manifest(root, public_raw)
    assert manifest.bundle_id == "bundle-001"
    assert manifest.target_id == "fm33ht-as5pr"
    assert manifest.profile == manifest.resources["profile"]
    assert manifest.authentication["signPolicyId"] == "ed25519-v1"
    assert manifest.workflow == {"name": "can-ota", "version": 1}
    assert manifest.abi["flashDriverRam"] == 0x20001000
    assert manifest.normalization["gapFill"] == 0xFF
    assert manifest.manifest_sha256 == hashlib.sha256(manifest.manifest_bytes).hexdigest()
    assert resolve_bundle_resource(manifest, "boot").read_bytes() == b"boot-image"
    with pytest.raises(TypeError):
        manifest.workflow["name"] = "changed"
    assert load_verified_manifest(root, public).bundle_id == "bundle-001"


@pytest.mark.parametrize("fault", ["manifest", "short_sig", "bad_sig", "wrong_key"])
def test_signature_fault_fails_before_yaml_parse_or_resource_access(tmp_path, monkeypatch, fault):
    root, _, _, public_raw = _bundle(tmp_path)
    if fault == "manifest":
        (root / "manifest.yaml").write_bytes((root / "manifest.yaml").read_bytes() + b" ")
    elif fault == "short_sig":
        (root / "manifest.sig").write_bytes(b"x" * 63)
    elif fault == "bad_sig":
        (root / "manifest.sig").write_bytes(b"x" * 64)
    else:
        public_raw = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(yaml, "safe_load", lambda *_: pytest.fail("signature failure parsed YAML"))
    with pytest.raises(ValueError, match="signature"):
        load_verified_manifest(root, public_raw)


def test_malformed_yaml_and_schema_faults_are_rejected(tmp_path):
    root, _, _, public_raw = _bundle(tmp_path)
    private = Ed25519PrivateKey.generate()
    public_raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    for raw in (b"resources: [", yaml.safe_dump({"schemaVersion": 1}).encode(), yaml.safe_dump({**_document(), "bundleId": 4}).encode()):
        (root / "manifest.yaml").write_bytes(raw)
        (root / "manifest.sig").write_bytes(private.sign(raw))
        with pytest.raises(ValueError):
            load_verified_manifest(root, public_raw)


@pytest.mark.parametrize("bad_path", ["/tmp/a", "C:/a", r"\\server\share\a", "../a", "x/../../a", "."])
def test_unsafe_resource_paths_are_rejected(tmp_path, bad_path):
    def mutate(doc, _): doc["resources"]["boot"]["path"] = bad_path
    root, _, _, public = _bundle(tmp_path, mutate)
    with pytest.raises(ValueError, match="resource path"):
        load_verified_manifest(root, public)


def test_directory_resource_is_rejected(tmp_path):
    def directory(doc, root):
        (root / "adir").mkdir()
        doc["resources"]["boot"]["path"] = "adir"
    root, _, _, public = _bundle(tmp_path / "d", directory)
    with pytest.raises(ValueError): load_verified_manifest(root, public)


def test_symlink_and_resolved_escape_are_rejected(tmp_path):
    outside = tmp_path / "outside.bin"; outside.write_bytes(b"boot-image")
    def link(doc, root):
        try:
            (root / "link.bin").symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"host cannot create symbolic links: {exc}")
        doc["resources"]["boot"]["path"] = "link.bin"
    root, _, _, public = _bundle(tmp_path / "s", link)
    with pytest.raises(ValueError): load_verified_manifest(root, public)


@pytest.mark.parametrize("fault", ["size", "hash", "missing", "kind", "cross_bundle"])
def test_resource_integrity_and_required_roles_are_enforced(tmp_path, fault):
    def mutate(doc, _):
        if fault == "size": doc["resources"]["boot"]["size"] += 1
        elif fault == "hash": doc["resources"]["boot"]["sha256"] = "0" * 64
        elif fault == "missing": del doc["resources"]["profile"]
        elif fault == "kind": doc["resources"]["boot"]["kind"] = "profile"
        else: doc["resources"]["boot"]["bundleId"] = "another-bundle"
    root, _, _, public = _bundle(tmp_path, mutate)
    with pytest.raises(ValueError): load_verified_manifest(root, public)


def test_unknown_resource_id_is_rejected(tmp_path):
    root, _, _, public = _bundle(tmp_path)
    manifest = load_verified_manifest(root, public)
    with pytest.raises(KeyError): resolve_bundle_resource(manifest, "other")
