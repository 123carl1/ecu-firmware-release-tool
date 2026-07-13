"""会话级内容寻址存储；JSON 只作传输，加载时复算全部身份。"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import tempfile
from typing import Any

from .as5pr_signer import As5prSignPolicy, SignedArtifact, verify_as5pr
from .inspector import InspectionContext, InspectedArtifact, inspect_artifact, revalidate_source


_ID = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = "release-artifact-store-v1"


class ArtifactStore:
    def __init__(self, root: Path, *, sign_policy: As5prSignPolicy | None = None,
                 verification_key: bytes | None = None) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._sign_policy = sign_policy
        self._verification_key = verification_key

    def _entry(self, kind: str, identifier: str) -> Path:
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise ValueError("artifact identifier must be 64 lowercase hexadecimal characters")
        entry = self.root / kind / identifier
        self._assert_within_root(entry, require_exists=False)
        return entry

    def _assert_within_root(self, path: Path, *, require_exists: bool) -> Path:
        candidate = Path(path)
        if candidate.is_absolute() and self.root not in (candidate, *candidate.parents):
            raise ValueError("path escapes artifact store")
        current = self.root
        relative = candidate.relative_to(self.root)
        for part in relative.parts:
            if part in ("", ".", ".."):
                raise ValueError("unsafe store path")
            current = current / part
            if current.is_symlink():
                raise ValueError("symbolic links are forbidden in artifact store")
        try:
            resolved = candidate.resolve(strict=require_exists)
            resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise ValueError("path escapes artifact store") from exc
        return resolved

    @staticmethod
    def _relative_file(value: Any) -> PurePosixPath:
        if not isinstance(value, str) or not value:
            raise ValueError("stored file reference must be a non-empty relative path")
        posix = PurePosixPath(value)
        windows = PureWindowsPath(value)
        if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
            raise ValueError("unsafe stored file reference")
        return posix

    def _metadata_path(self, entry: Path) -> Path:
        return self._assert_within_root(entry / "metadata.json", require_exists=True)

    @staticmethod
    def _hex(value: bytes) -> str:
        return value.hex()

    @staticmethod
    def _decode_hash(value: Any, field: str) -> bytes:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"{field} must be a lowercase SHA-256")
        return bytes.fromhex(value)

    @staticmethod
    def _context(artifact: InspectedArtifact) -> dict[str, Any]:
        identity = artifact.identity
        return {
            "targetId": identity.target_id, "bundleId": identity.bundle_id,
            "profileId": identity.profile_id, "profileVersion": identity.profile_version,
            "profileSha256": identity.profile_sha256.hex(),
            "signPolicyId": identity.sign_policy_id,
            "normalizationStart": identity.normalization_start,
            "normalizationEnd": identity.normalization_end, "gapFill": identity.gap_fill,
        }

    @classmethod
    def _inspected_metadata(cls, artifact: InspectedArtifact, source_name: str) -> dict[str, Any]:
        return {
            "schema": _SCHEMA, "type": "inspected", "artifactId": artifact.artifact_id,
            "source": source_name, "sourceSha256": cls._hex(artifact.source_file_sha256),
            "context": cls._context(artifact),
            "segments": [{"address": s.address, "length": len(s.data),
                          "sha256": hashlib.sha256(s.data).hexdigest()} for s in artifact.segments],
            "normalizedSha256": hashlib.sha256(artifact.normalized_payload).hexdigest(),
        }

    @classmethod
    def _signed_metadata(cls, signed: SignedArtifact, source_name: str) -> dict[str, Any]:
        result = cls._inspected_metadata(signed.artifact, source_name)
        result.update({"type": "signed", "signedArtifactId": signed.signed_artifact_id,
                       "signed": "signed.bin",
                       "signedSha256": signed.signed_file_sha256.hex(),
                       "authSha256": signed.auth_block_sha256.hex(),
                       "manifestBundleSha256": signed.manifest_bundle_sha256.hex()})
        return result

    @staticmethod
    def _json_bytes(metadata: dict[str, Any]) -> bytes:
        return (json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")) + "\n").encode("utf-8")

    def _put(self, entry: Path, metadata: dict[str, Any], files: dict[str, bytes]) -> str:
        entry.parent.mkdir(parents=True, exist_ok=True)
        stage: Path | None = Path(tempfile.mkdtemp(prefix=f".{entry.name}.", dir=entry.parent))
        try:
            for name, payload in files.items():
                target = stage / name
                with target.open("wb") as stream:
                    stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            with (stage / "metadata.json").open("wb") as stream:
                stream.write(self._json_bytes(metadata)); stream.flush(); os.fsync(stream.fileno())
            try:
                os.replace(stage, entry)
                stage = None
            except OSError:
                if not entry.is_dir():
                    raise
                if (entry / "metadata.json").read_bytes() != self._json_bytes(metadata):
                    raise ValueError("content-addressed identifier already contains different metadata")
                for name, payload in files.items():
                    if (entry / name).read_bytes() != payload:
                        raise ValueError("content-addressed identifier already contains different content")
            return entry.name
        finally:
            if stage is not None and stage.exists():
                shutil.rmtree(stage)

    def put_inspected(self, artifact: InspectedArtifact) -> str:
        try:
            revalidate_source(artifact)
            identity = artifact.identity
            recomputed = inspect_artifact(artifact.source_path, InspectionContext(
                target_id=identity.target_id, bundle_id=identity.bundle_id,
                profile_id=identity.profile_id, profile_version=identity.profile_version,
                profile_sha256=identity.profile_sha256,
                sign_policy_id=identity.sign_policy_id,
                normalization_start=identity.normalization_start,
                normalization_end=identity.normalization_end, gap_fill=identity.gap_fill))
        except Exception as exc:
            raise ValueError("inspected artifact cannot be reproduced from source") from exc
        if recomputed != artifact:
            raise ValueError("inspected artifact fields do not match recomputed source")
        source = artifact.source_path.read_bytes()
        suffix = artifact.source_path.suffix.lower()
        source_name = "source" + (suffix if suffix in (".bin", ".hex", ".s19", ".srec") else ".bin")
        metadata = self._inspected_metadata(artifact, source_name)
        return self._put(self._entry("inspected", artifact.artifact_id), metadata,
                         {source_name: source})

    def put_signed(self, signed: SignedArtifact) -> str:
        if self._sign_policy is None or self._verification_key is None:
            raise ValueError("signed artifact storage requires verification context")
        verify_as5pr(signed, self._sign_policy, self._verification_key)
        try:
            revalidate_source(signed.artifact)
        except Exception as exc:
            raise ValueError("signed artifact source changed before storage") from exc
        source = signed.artifact.source_path.read_bytes()
        suffix = signed.artifact.source_path.suffix.lower()
        source_name = "source" + (suffix if suffix in (".bin", ".hex", ".s19", ".srec") else ".bin")
        metadata = self._signed_metadata(signed, source_name)
        return self._put(self._entry("signed", signed.signed_artifact_id), metadata,
                         {source_name: source, "signed.bin": signed.signed_bytes})

    def _read_metadata(self, entry: Path, expected_type: str) -> dict[str, Any]:
        try:
            data = json.loads(self._metadata_path(entry).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("artifact metadata is malformed or unavailable") from exc
        if not isinstance(data, dict) or data.get("schema") != _SCHEMA or data.get("type") != expected_type:
            raise ValueError("unsupported artifact metadata schema or type")
        return data

    def _load_inspected_from(self, entry: Path, metadata: dict[str, Any]) -> InspectedArtifact:
        required = {"schema", "type", "artifactId", "source", "sourceSha256", "context",
                    "segments", "normalizedSha256"}
        signed_fields = {"signedArtifactId", "signed", "signedSha256", "authSha256",
                         "manifestBundleSha256"}
        allowed = required if metadata["type"] == "inspected" else required | signed_fields
        if set(metadata) != allowed:
            raise ValueError("artifact metadata has missing or unknown fields")
        source_ref = self._relative_file(metadata["source"])
        source_path = self._assert_within_root(entry.joinpath(*source_ref.parts), require_exists=True)
        context = metadata["context"]
        if not isinstance(context, dict) or set(context) != {"targetId", "bundleId", "profileId",
                "profileVersion", "profileSha256", "signPolicyId", "normalizationStart",
                "normalizationEnd", "gapFill"}:
            raise ValueError("invalid inspection context")
        try:
            inspected = inspect_artifact(source_path, InspectionContext(
                target_id=context["targetId"], bundle_id=context["bundleId"],
                profile_id=context["profileId"], profile_version=context["profileVersion"],
                profile_sha256=self._decode_hash(context["profileSha256"], "profileSha256"),
                sign_policy_id=context["signPolicyId"], normalization_start=context["normalizationStart"],
                normalization_end=context["normalizationEnd"], gap_fill=context["gapFill"]))
        except Exception as exc:
            raise ValueError("stored source cannot reproduce inspected artifact") from exc
        expected = self._inspected_metadata(inspected, metadata["source"])
        expected["type"] = metadata["type"]
        for field in required:
            if metadata[field] != expected[field]:
                raise ValueError(f"stored {field} does not match recomputed artifact")
        return inspected

    def load_inspected(self, artifact_id: str) -> InspectedArtifact:
        entry = self._entry("inspected", artifact_id)
        metadata = self._read_metadata(entry, "inspected")
        artifact = self._load_inspected_from(entry, metadata)
        if artifact.artifact_id != artifact_id:
            raise ValueError("requested ArtifactId does not match stored content")
        return artifact

    def load_signed(self, signed_artifact_id: str) -> SignedArtifact:
        if self._sign_policy is None or self._verification_key is None:
            raise ValueError("signed artifact loading requires verified sign policy and verification key")
        entry = self._entry("signed", signed_artifact_id)
        metadata = self._read_metadata(entry, "signed")
        required = {"signedArtifactId", "signed", "signedSha256", "authSha256",
                    "manifestBundleSha256"}
        if not required.issubset(metadata):
            raise ValueError("signed artifact metadata lacks required fields")
        artifact = self._load_inspected_from(entry, metadata)
        signed_ref = self._relative_file(metadata["signed"])
        payload = self._assert_within_root(entry.joinpath(*signed_ref.parts), require_exists=True).read_bytes()
        auth = payload[-48:] if len(payload) >= 48 else b""
        signed = SignedArtifact(artifact, payload, hashlib.sha256(payload).digest(), auth,
                                hashlib.sha256(auth).digest(),
                                self._decode_hash(metadata["manifestBundleSha256"], "manifestBundleSha256"),
                                metadata["signedArtifactId"])
        verify_as5pr(signed, self._sign_policy, self._verification_key)
        expected = self._signed_metadata(signed, metadata["source"])
        for field in required:
            if metadata[field] != expected[field]:
                raise ValueError(f"stored {field} does not match recomputed signed artifact")
        if signed.signed_artifact_id != signed_artifact_id:
            raise ValueError("requested SignedArtifactId does not match stored content")
        return signed

