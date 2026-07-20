"""ULID generation for engine-assigned entity ids (spec §4: "req_01J...", "prop_01J...")."""

from __future__ import annotations

import os
import time

_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_crockford(value: int, length: int) -> str:
    chars = ["0"] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_ulid() -> str:
    """A 26-character Crockford-base32 ULID: 48-bit ms timestamp + 80-bit randomness."""
    timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode_crockford(timestamp_ms, 10) + _encode_crockford(randomness, 16)


def new_id(prefix: str) -> str:
    """e.g. new_id("req") -> "req_01JABC...": the engine-assigned id shape used throughout §4."""
    return f"{prefix}_{new_ulid()}"
