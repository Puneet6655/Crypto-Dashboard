"""Format checks for Bitcoin identifiers.

These are format checks only. They catch typos early and stop us sending junk
to the provider. They do not verify checksums, so the provider still has the
final say on whether an address exists.
"""
import re

# Legacy (1...) and script (3...) addresses: Base58 characters, 26-35 long.
_BASE58_ADDRESS = re.compile(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$")
# SegWit (bc1q...) and Taproot (bc1p...) addresses: lower-case bech32 characters.
_BECH32_ADDRESS = re.compile(r"^bc1[qp][02-9ac-hj-np-z]{38,58}$")
# A transaction ID is 32 bytes written as 64 hexadecimal characters.
_TXID = re.compile(r"^[0-9a-fA-F]{64}$")


def is_bitcoin_address(value: str) -> bool:
    """Return True if value looks like a mainnet Bitcoin address."""
    candidate = value.strip()
    # bech32 addresses may be written in upper case; compare in lower case.
    return bool(_BASE58_ADDRESS.match(candidate) or _BECH32_ADDRESS.match(candidate.lower()))


def is_txid(value: str) -> bool:
    """Return True if value looks like a Bitcoin transaction ID."""
    return bool(_TXID.match(value.strip()))


def normalize_address(value: str) -> str:
    """Trim spaces and lower-case bech32 addresses so one address has one spelling."""
    candidate = value.strip()
    return candidate.lower() if candidate.lower().startswith("bc1") else candidate