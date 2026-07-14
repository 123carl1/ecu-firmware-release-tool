from __future__ import annotations

from dataclasses import dataclass
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from unified_can_lin_host_tool.update.errors import UpdateNetworkError, UpdateSecurityError
from unified_can_lin_host_tool.update.github_release import GitHubReleaseSource


@dataclass(frozen=True)
class _Release:
    tag: str
    locator: bytes
    raw: bytes
    signature: bytes
    keys: dict[str, bytes]


def _make_release(private_key: Ed25519PrivateKey, public_key: bytes, version: str) -> _Release:
    tag = f"v{version}"
    payload = {
        "schemaVersion": 1,
        "repository": "o/ecu-firmware-release-tool",
        "version": version,
        "tag": tag,
        "commit": "01" * 20,
        "generatedAt": "2026-07-14T12:00:00Z",
        "channel": "stable",
        "releaseNotes": "安全更新。",
        "installer": {
            "name": f"EcuReleaseTool_Setup_{version}.exe",
            "size": 123,
            "sha256": "ab" * 32,
        },
        "keyId": "test-v1",
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return _Release(
        tag=tag,
        locator=json.dumps({"tag": tag}).encode(),
        raw=raw,
        signature=private_key.sign(raw),
        keys={"test-v1": public_key},
    )


@pytest.fixture
def releases():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return (
        _make_release(private_key, public_key, "0.2.1"),
        _make_release(private_key, public_key, "0.2.2"),
    )


class _FakeHttp:
    def __init__(self):
        self.responses: dict[str, list[bytes | Exception]] = {}
        self.calls: list[tuple[str, int, bool]] = []

    def queue(self, url: str, *responses: bytes | Exception):
        self.responses[url] = list(responses)

    def read_bytes(self, url, *, max_bytes, connect_timeout_s=5.0, read_timeout_s=15.0, no_cache=False):
        self.calls.append((url, max_bytes, no_cache))
        queued = self.responses[url]
        response = queued.pop(0) if len(queued) > 1 else queued[0]
        if isinstance(response, Exception):
            raise response
        return response


def _latest_url():
    return "https://github.com/o/ecu-firmware-release-tool/releases/latest/download/update.json"


def _tag_url(tag: str, suffix: str = ""):
    return f"https://github.com/o/ecu-firmware-release-tool/releases/download/{tag}/update.json{suffix}"


def test_latest_is_only_a_locator_and_tag_resources_are_paired(releases):
    release_1, _ = releases
    http = _FakeHttp()
    http.queue(_latest_url(), release_1.locator)
    http.queue(_tag_url(release_1.tag), release_1.raw)
    http.queue(_tag_url(release_1.tag, ".sig"), release_1.signature)

    info = GitHubReleaseSource("o/ecu-firmware-release-tool", http).fetch(release_1.keys)

    assert info.tag == "v0.2.1"
    assert all("latest/download/update.json.sig" not in url for url, _, _ in http.calls)
    assert http.calls == [
        (_latest_url(), 64 * 1024, True),
        (_tag_url("v0.2.1"), 64 * 1024, False),
        (_tag_url("v0.2.1", ".sig"), 64, False),
    ]


def test_mismatched_pair_relocates_once_then_stops(releases):
    release_1, release_2 = releases
    http = _FakeHttp()
    http.queue(_latest_url(), release_1.locator, release_2.locator)
    http.queue(_tag_url(release_1.tag), release_1.raw)
    http.queue(_tag_url(release_1.tag, ".sig"), release_2.signature)
    http.queue(_tag_url(release_2.tag), release_2.raw)
    http.queue(_tag_url(release_2.tag, ".sig"), b"x" * 64)

    with pytest.raises(UpdateSecurityError):
        GitHubReleaseSource("o/ecu-firmware-release-tool", http).fetch(release_1.keys)

    assert [url for url, _, _ in http.calls].count(_latest_url()) == 2


def test_stale_locator_cache_is_retried_once_with_no_cache(releases):
    release_1, release_2 = releases
    http = _FakeHttp()
    http.queue(_latest_url(), release_1.locator, release_2.locator)
    http.queue(_tag_url(release_1.tag), release_1.raw)
    http.queue(_tag_url(release_1.tag, ".sig"), b"x" * 64)
    http.queue(_tag_url(release_2.tag), release_2.raw)
    http.queue(_tag_url(release_2.tag, ".sig"), release_2.signature)

    info = GitHubReleaseSource("o/ecu-firmware-release-tool", http).fetch(release_2.keys)

    assert info.tag == "v0.2.2"
    assert [no_cache for url, _, no_cache in http.calls if url == _latest_url()] == [True, True]


def test_signed_tag_must_match_the_located_tag(releases):
    release_1, release_2 = releases
    http = _FakeHttp()
    http.queue(_latest_url(), release_1.locator)
    http.queue(_tag_url(release_1.tag), release_2.raw)
    http.queue(_tag_url(release_1.tag, ".sig"), release_2.signature)

    with pytest.raises(UpdateSecurityError, match="配对"):
        GitHubReleaseSource("o/ecu-firmware-release-tool", http).fetch(release_1.keys)

    assert [url for url, _, _ in http.calls].count(_latest_url()) == 2


def test_network_failure_is_not_retried(releases):
    release_1, _ = releases
    http = _FakeHttp()
    http.queue(_latest_url(), UpdateNetworkError("超时"))

    with pytest.raises(UpdateNetworkError, match="超时"):
        GitHubReleaseSource("o/ecu-firmware-release-tool", http).fetch(release_1.keys)

    assert len(http.calls) == 1


@pytest.mark.parametrize(
    "repository",
    [
        "https://github.com/o/ecu-firmware-release-tool",
        "o/another-repository",
        "o/sub/ecu-firmware-release-tool",
        "../ecu-firmware-release-tool",
    ],
)
def test_repository_is_a_fixed_github_identity_not_an_arbitrary_url(repository):
    with pytest.raises(UpdateSecurityError):
        GitHubReleaseSource(repository, _FakeHttp())
