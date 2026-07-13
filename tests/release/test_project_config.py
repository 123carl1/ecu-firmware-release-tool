from dataclasses import replace
import hashlib

import pytest

from unified_can_lin_host_tool.release.project_config import (
    PROJECT_RELEASE_CONFIG_DOMAIN,
    ProjectCode,
    canonical_config_bytes,
    compute_config_digest,
    get_project_config,
)


def test_as5pr_config_uses_distinct_resource_target_ids() -> None:
    config = get_project_config(ProjectCode.AS5PR)

    assert config.project_code == 0x41503541
    assert config.ecu_target_id == 0x41503541
    assert config.authentication.app_target_id == 0x41503541
    assert config.authentication.flash_driver_target_id == 0x46503541
    assert config.real_flash_enabled is True
    assert config.resource_files.app_elf == "dau_fm33.elf"
    assert config.resource_files.app_bin == "dau_fm33.bin"


def test_e68_real_flash_is_disabled_until_identity_contract_exists() -> None:
    config = get_project_config(ProjectCode.E68)

    assert config.real_flash_enabled is False
    assert config.project_code == 0


def test_config_encoding_is_stable_ascii_json_with_domain_separator() -> None:
    config = get_project_config(ProjectCode.AS5PR)

    encoded = canonical_config_bytes(config)

    assert encoded.startswith(PROJECT_RELEASE_CONFIG_DOMAIN + b'{"authentication":')
    assert encoded.endswith(b'}')
    assert b"\n" not in encoded
    assert encoded == canonical_config_bytes(config)
    assert compute_config_digest(config) == hashlib.sha256(encoded).digest()
    assert compute_config_digest(config).hex() == (
        "dd476644a1ddebede079f886784fdb26ccbae6c52ec01c507726118a013e1ac4"
    )


def test_numeric_config_field_rejects_bool() -> None:
    config = get_project_config(ProjectCode.AS5PR)

    with pytest.raises(ValueError, match="project_code must be a u32"):
        replace(config, project_code=True)


def test_unknown_project_selection_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported project"):
        get_project_config("OTHER")  # type: ignore[arg-type]
