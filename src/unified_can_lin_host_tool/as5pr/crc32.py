AS5PR_CRC32_INIT = 0xFFFFFFFF

_CRC32_REFLECTED_POLY = 0xEDB88320


def _build_crc32_table() -> tuple[int, ...]:
    table: list[int] = []
    for value in range(256):
        crc = value
        for _ in range(8):
            if crc & 0x01:
                crc = (crc >> 1) ^ _CRC32_REFLECTED_POLY
            else:
                crc >>= 1
        table.append(crc & 0xFFFFFFFF)
    return tuple(table)


TABLE = _build_crc32_table()


def as5pr_crc32_update(current_crc: int, data: bytes) -> int:
    crc = current_crc & 0xFFFFFFFF
    for value in data:
        crc = ((crc >> 8) & 0x00FFFFFF) ^ TABLE[(crc ^ value) & 0xFF]
        crc &= 0xFFFFFFFF
    return crc


def as5pr_crc32(data: bytes) -> int:
    return as5pr_crc32_update(AS5PR_CRC32_INIT, data)
