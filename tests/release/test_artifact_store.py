import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from unified_can_lin_host_tool.release.artifact_store import ArtifactStore
from unified_can_lin_host_tool.release.as5pr_signer import As5prSignPolicy, sign_as5pr
from unified_can_lin_host_tool.release.inspector import InspectionContext, inspect_artifact
from unified_can_lin_host_tool.release.manifest import load_verified_manifest

KEY = bytes(range(32))


def test_store_module_exposes_content_addressed_store(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    assert store.root == (tmp_path / "store").resolve()


@pytest.mark.parametrize("unsafe", ["../escape", "/absolute", "C:/escape", "\\\\server\\share"])
def test_store_rejects_unsafe_identifiers(tmp_path: Path, unsafe: str) -> None:
    store = ArtifactStore(tmp_path / "store")
    with pytest.raises(ValueError):
        store.load_inspected(unsafe)


def _policy(tmp_path: Path) -> As5prSignPolicy:
    root = tmp_path / "bundle"
    root.mkdir()
    resources = {}
    for name in ("profile", "boot", "flash_driver"):
        payload = name.encode()
        (root / f"{name}.bin").write_bytes(payload)
        resources[name] = {"path": f"{name}.bin", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "kind": name, "bundleId": "bundle-1", "targetId": "fm33ht-as5pr"}
    document = {"schemaVersion": 1, "bundleId": "bundle-1", "targetId": "fm33ht-as5pr", "projectId": "AS5PR", "version": "1", "source": {"commit": "x", "dirty": False, "toolchain": "x", "configHash": "x", "builtAt": "x"}, "memory": {"appStart": 0x7000, "appEnd": 0x7004, "pageSize": 512, "flashDriverRam": 0x20001000, "flashDriverMaxSize": 8192}, "normalization": {"start": 0x7000, "end": 0x7004, "gapFill": 0xFF}, "authentication": {"formatVersion": 0, "signPolicyId": "hmac-v1", "keyId": "dev", "magic": 0xA5A5A5A5}, "workflow": {"name": "can-ota", "version": 1}, "resources": resources}
    raw = yaml.safe_dump(document, sort_keys=False).encode()
    private = Ed25519PrivateKey.generate()
    (root / "manifest.yaml").write_bytes(raw)
    (root / "manifest.sig").write_bytes(private.sign(raw))
    return As5prSignPolicy.from_verified_manifest(load_verified_manifest(root, private.public_key()))


def _artifacts(tmp_path: Path):
    source = tmp_path / "app.bin"
    source.write_bytes(b"\x01\x02\xff\x04")
    context = InspectionContext(0x41503541, "bundle-1", "as5pr", "1", b"\0" * 32, "hmac-v1", 0x7000, 0x7004, 0xFF)
    inspected = inspect_artifact(source, context)
    policy = _policy(tmp_path)
    return inspected, sign_as5pr(inspected, policy, KEY), policy


def test_inspected_and_signed_round_trip_recomputes_ids(tmp_path: Path) -> None:
    inspected, signed, policy = _artifacts(tmp_path)
    store = ArtifactStore(tmp_path / "store", sign_policy=policy, verification_key=KEY)
    assert store.put_inspected(inspected) == inspected.artifact_id
    assert store.load_inspected(inspected.artifact_id).artifact_id == inspected.artifact_id
    assert store.put_signed(signed) == signed.signed_artifact_id
    assert store.load_signed(signed.signed_artifact_id).signed_artifact_id == signed.signed_artifact_id
    assert store.put_signed(signed) == signed.signed_artifact_id


@pytest.mark.parametrize("field,value", [("artifactId", "0" * 64), ("sourceSha256", "1" * 64), ("normalizedSha256", "2" * 64), ("segments", [])])
def test_inspected_metadata_tampering_is_rejected(tmp_path: Path, field: str, value: object) -> None:
    inspected, _, _ = _artifacts(tmp_path)
    store = ArtifactStore(tmp_path / "store")
    store.put_inspected(inspected)
    metadata_path = store.root / "inspected" / inspected.artifact_id / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata[field] = value
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load_inspected(inspected.artifact_id)


@pytest.mark.parametrize("field,value", [("signedArtifactId", "0" * 64), ("signedSha256", "1" * 64), ("authSha256", "2" * 64), ("manifestBundleSha256", "3" * 64)])
def test_signed_metadata_tampering_is_rejected(tmp_path: Path, field: str, value: str) -> None:
    _, signed, policy = _artifacts(tmp_path)
    store = ArtifactStore(tmp_path / "store", sign_policy=policy, verification_key=KEY)
    store.put_signed(signed)
    metadata_path = store.root / "signed" / signed.signed_artifact_id / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata[field] = value
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load_signed(signed.signed_artifact_id)


def test_changed_source_and_signed_file_are_rejected(tmp_path: Path) -> None:
    inspected, signed, policy = _artifacts(tmp_path)
    store = ArtifactStore(tmp_path / "store", sign_policy=policy, verification_key=KEY)
    store.put_inspected(inspected)
    source = store.root / "inspected" / inspected.artifact_id / "source.bin"
    source.write_bytes(b"changed")
    with pytest.raises(ValueError):
        store.load_inspected(inspected.artifact_id)
    store.put_signed(signed)
    binary = store.root / "signed" / signed.signed_artifact_id / "signed.bin"
    binary.write_bytes(binary.read_bytes()[:-1] + b"X")
    with pytest.raises(ValueError):
        store.load_signed(signed.signed_artifact_id)

def test_put_signed_requires_verification_context(tmp_path: Path) -> None:
    _, signed, _ = _artifacts(tmp_path)
    with pytest.raises(ValueError, match="verification"):
        ArtifactStore(tmp_path / "store").put_signed(signed)


def test_put_inspected_rejects_source_changed_after_inspection(tmp_path: Path) -> None:
    inspected, _, _ = _artifacts(tmp_path)
    inspected.source_path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path / "store").put_inspected(inspected)


def test_put_inspected_rejects_forged_object_fields(tmp_path: Path) -> None:
    inspected, _, _ = _artifacts(tmp_path)
    forged = replace(inspected, artifact_id="0" * 64)
    with pytest.raises(ValueError, match="recomputed"):
        ArtifactStore(tmp_path / "store").put_inspected(forged)


@pytest.mark.parametrize("fault", ["malformed", "missing", "unknown", "path"])
def test_metadata_schema_and_path_faults_are_rejected(tmp_path: Path, fault: str) -> None:
    inspected, _, _ = _artifacts(tmp_path)
    store = ArtifactStore(tmp_path / "store")
    store.put_inspected(inspected)
    metadata_path = store.root / "inspected" / inspected.artifact_id / "metadata.json"
    if fault == "malformed":
        metadata_path.write_text("{", encoding="utf-8")
    else:
        metadata = json.loads(metadata_path.read_text())
        if fault == "missing":
            del metadata["segments"]
        elif fault == "unknown":
            metadata["unexpected"] = True
        else:
            metadata["source"] = "../outside.bin"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load_inspected(inspected.artifact_id)
