_MASK = bytes([0xF0, 0x45, 0x53, 0x73])


def calc_e68_level1_key(seed: bytes) -> bytes:
    mixed = _mix_seed(seed)
    return bytes(
        [
            ((mixed[0] & 0x0F) << 4) | (mixed[1] & 0xF0),
            ((mixed[1] & 0x0F) << 4) | ((mixed[2] & 0xF0) >> 4),
            (mixed[2] & 0xF0) | ((mixed[3] & 0xF0) >> 4),
            ((mixed[3] & 0x0F) << 4) | (mixed[0] & 0x0F),
        ]
    )


def calc_e68_fbl_key(seed: bytes) -> bytes:
    mixed = _mix_seed(seed)
    return bytes(
        [
            ((mixed[0] & 0x0F) << 4) | (mixed[1] & 0x0F),
            ((mixed[1] & 0xF0) >> 4) | ((mixed[2] & 0x0F) << 4),
            ((mixed[2] & 0xF0) >> 4) | (mixed[3] & 0xF0),
            (mixed[3] & 0x0F) | ((mixed[0] & 0xF0) >> 4),
        ]
    )


def _mix_seed(seed: bytes) -> list[int]:
    if len(seed) != 4:
        raise ValueError("E68 seed must be 4 bytes")
    return [seed_value ^ mask for seed_value, mask in zip(seed, _MASK, strict=True)]

