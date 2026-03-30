# -*- coding: utf-8 -*-
"""CRC-64 hash compatible with ChainThink's crc64.js.

Uses crcmod with the correct parameters to match the JS implementation:
  - Polynomial: CRC-64/ECMA-182 (0x142F0E1EBA9EA3693)
  - initCrc: 0
  - xorOut: 0xFFFFFFFFFFFFFFFF
  - rev: True (reflected)
"""

import crcmod

_CHAINTHINK_CRC64 = crcmod.mkCrcFun(
    0x142F0E1EBA9EA3693,
    initCrc=0,
    rev=True,
    xorOut=0xFFFFFFFFFFFFFFFF,
)


def compute_crc64_file(file_path: str) -> str:
    """Compute CRC64 hash of a file, compatible with ChainThink's crc64.js.

    Returns the hash as a decimal string.
    """
    with open(file_path, "rb") as f:
        data = f.read()
    return str(_CHAINTHINK_CRC64(data))
