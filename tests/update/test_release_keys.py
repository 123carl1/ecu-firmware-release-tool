from __future__ import annotations

from unified_can_lin_host_tool.update.release_keys import load_release_public_keys


def test_packaged_release_public_keys_are_valid():
    keys = load_release_public_keys()

    assert set(keys) == {"release-v1"}
    assert isinstance(keys["release-v1"], bytes)
    assert len(keys["release-v1"]) == 32
    assert len(keys) <= 4
