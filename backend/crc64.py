# -*- coding: utf-8 -*-
"""
CRC-64/ECMA-182 pure Python implementation.

Compatible with the crc64-ecma182 npm package used by ChainThink backend.
Reference: https://www.npmjs.com/package/crc64-ecma182
"""

# CRC-64/ECMA-182 polynomial
POLY = 0x42F0E1EBA9EA3693

# Pre-computed lookup table
_TABLE = None


def _build_table():
    table = []
    for i in range(256):
        crc = i << 56
        for _ in range(8):
            if crc & 0x8000000000000000:
                crc = ((crc << 1) ^ POLY) & 0xFFFFFFFFFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFFFFFFFFFF
        table.append(crc)
    return table


def crc64(data: bytes) -> int:
    """Compute CRC-64/ECMA-182 checksum of bytes, return as unsigned 64-bit int."""
    global _TABLE
    if _TABLE is None:
        _TABLE = _build_table()

    crc = 0x0000000000000000
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFFFFFFFFFF) ^ _TABLE[((crc >> 56) ^ byte) & 0xFF]
    return crc


def crc64_hex(data: bytes) -> str:
    """Compute CRC-64/ECMA-182 and return as hex string (lowercase, no prefix)."""
    return format(crc64(data), '016x')


def crc64_file(file_path: str) -> str:
    """Compute CRC-64/ECMA-182 of a file, return as decimal string (matches JS output)."""
    with open(file_path, 'rb') as f:
        data = f.read()
    return str(crc64(data))


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python crc64.py <file_path>")
        sys.exit(1)
    print(crc64_file(sys.argv[1]))
