from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import scripts.release_signing as release_signing


def _pem(private_key: Ed25519PrivateKey) -> str:
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def test_generate_release_key_writes_independent_key_pair(tmp_path, monkeypatch):
    private_output = tmp_path / "keys" / "release-v1.pem"
    public_output = tmp_path / "release_public_keys.json"
    monkeypatch.setattr(release_signing, "_harden_windows_private_key", lambda path: None)

    release_signing.generate_release_key(private_output, public_output, "release-v1")

    loaded_private = serialization.load_pem_private_key(private_output.read_bytes(), password=None)
    expected_public = loaded_private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ).hex()
    assert json.loads(public_output.read_text(encoding="utf-8")) == {"release-v1": expected_public}
    assert "DEVELOPMENT_PACKAGE_PRIVATE_SEED" not in private_output.read_text(encoding="ascii")


def test_generate_release_key_refuses_to_overwrite_private_key(tmp_path, monkeypatch):
    private_output = tmp_path / "release-v1.pem"
    private_output.write_text("existing-secret", encoding="utf-8")
    public_output = tmp_path / "release_public_keys.json"
    monkeypatch.setattr(release_signing, "_harden_windows_private_key", lambda path: None)

    with pytest.raises(FileExistsError, match="覆盖"):
        release_signing.generate_release_key(private_output, public_output, "release-v1")

    assert private_output.read_text(encoding="utf-8") == "existing-secret"
    assert not public_output.exists()


def test_acl_failure_deletes_new_private_key(tmp_path, monkeypatch):
    private_output = tmp_path / "release-v1.pem"
    public_output = tmp_path / "release_public_keys.json"

    def fail_acl(path: Path) -> None:
        raise subprocess.CalledProcessError(5, ["icacls", str(path)])

    monkeypatch.setattr(release_signing, "_harden_windows_private_key", fail_acl)

    with pytest.raises(RuntimeError, match="权限"):
        release_signing.generate_release_key(private_output, public_output, "release-v1")

    assert not private_output.exists()
    assert not public_output.exists()


def test_load_signing_key_only_reads_environment(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setenv("UPDATE_SIGNING_KEY_PEM", _pem(private_key))

    loaded = release_signing.load_signing_key_from_environment()

    assert loaded.private_bytes_raw() == private_key.private_bytes_raw()


def test_invalid_or_missing_environment_key_is_rejected(monkeypatch):
    monkeypatch.delenv("UPDATE_SIGNING_KEY_PEM", raising=False)
    with pytest.raises(RuntimeError, match="UPDATE_SIGNING_KEY_PEM"):
        release_signing.load_signing_key_from_environment()

    monkeypatch.setenv("UPDATE_SIGNING_KEY_PEM", "not-a-private-key")
    with pytest.raises(RuntimeError, match="Ed25519"):
        release_signing.load_signing_key_from_environment()


def test_assert_public_key_matches_selected_key(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_keys_path = tmp_path / "keys.json"
    public_keys_path.write_text(json.dumps({"release-v1": public_raw.hex()}), encoding="utf-8")

    release_signing.assert_public_key_matches(private_key, public_keys_path, "release-v1")

    with pytest.raises(RuntimeError, match="不匹配"):
        release_signing.assert_public_key_matches(Ed25519PrivateKey.generate(), public_keys_path, "release-v1")


def test_assert_public_key_rejects_duplicate_key_id(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    public_hex = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ).hex()
    public_keys_path = tmp_path / "keys.json"
    public_keys_path.write_text(
        f'{{"release-v1":"{public_hex}","release-v1":"{public_hex}"}}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="重复 keyId"):
        release_signing.assert_public_key_matches(private_key, public_keys_path, "release-v1")


def test_assert_public_key_rejects_more_than_four_keys(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    public_hex = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ).hex()
    public_keys_path = tmp_path / "keys.json"
    public_keys_path.write_text(
        json.dumps(
            {
                "release-v1": public_hex,
                "release-v2": "01" * 32,
                "release-v3": "02" * 32,
                "release-v4": "03" * 32,
                "release-v5": "04" * 32,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="4"):
        release_signing.assert_public_key_matches(private_key, public_keys_path, "release-v1")


@pytest.mark.parametrize(
    ("invalid_document", "message"),
    [
        (lambda public_hex: {"release-v1": public_hex, "release-v2": public_hex}, "重复公钥"),
        (lambda public_hex: {"release-v1": public_hex, "Invalid-Key": "01" * 32}, "keyId"),
        (lambda public_hex: {"release-v1": public_hex.upper()}, "小写十六进制"),
        (lambda public_hex: {"release-v1": {"publicKey": public_hex}}, "小写十六进制"),
    ],
    ids=["duplicate-public-key", "invalid-key-id", "uppercase-hex", "unknown-structure"],
)
def test_assert_public_key_uses_client_strict_parser(tmp_path, invalid_document, message):
    private_key = Ed25519PrivateKey.generate()
    public_hex = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ).hex()
    public_keys_path = tmp_path / "keys.json"
    public_keys_path.write_text(json.dumps(invalid_document(public_hex)), encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        release_signing.assert_public_key_matches(private_key, public_keys_path, "release-v1")


def test_sign_update_file_writes_raw_64_byte_signature(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setenv("UPDATE_SIGNING_KEY_PEM", _pem(private_key))
    input_path = tmp_path / "update.json"
    signature_path = tmp_path / "update.json.sig"
    raw = b'{"schemaVersion":1}'
    input_path.write_bytes(raw)

    release_signing.sign_update_file(input_path, signature_path)

    signature = signature_path.read_bytes()
    assert len(signature) == 64
    private_key.public_key().verify(signature, raw)


def test_sign_cli_has_no_private_key_argument():
    parser = release_signing.build_argument_parser()
    sign_parser = next(
        action.choices["sign"]
        for action in parser._actions
        if getattr(action, "choices", None) and "sign" in action.choices
    )
    option_strings = {option for action in sign_parser._actions for option in action.option_strings}
    assert "--private-key" not in option_strings
    assert "--private-key-pem" not in option_strings
