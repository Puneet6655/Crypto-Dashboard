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

# Ethereum: 0x plus 20 bytes (address) or 32 bytes (transaction hash) in hexadecimal.
_ETH_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ETH_TX_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")


def is_ethereum_address(value: str) -> bool:
    """Return True if value looks like an Ethereum-format address (checksum case is not checked)."""
    return bool(_ETH_ADDRESS.match(value.strip()))


def is_ethereum_tx_hash(value: str) -> bool:
    """Return True if value looks like an Ethereum transaction hash (0x + 64 hex characters)."""
    return bool(_ETH_TX_HASH.match(value.strip()))


def normalize_hex(value: str) -> str:
    """Ethereum addresses and hashes in one spelling: trimmed, lower case."""
    return value.strip().lower()


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58_length(value: str) -> int | None:
    """Number of bytes a base58 string decodes to, or None if it is not base58."""
    number = 0
    for char in value:
        digit = _B58.find(char)
        if digit < 0:
            return None
        number = number * 58 + digit
    leading_zeros = len(value) - len(value.lstrip("1"))
    return leading_zeros + (number.bit_length() + 7) // 8


def is_solana_address(value: str) -> bool:
    """A Solana address (public key): base58 that decodes to 32 bytes, 32 to 44 characters. Case-sensitive."""
    value = value.strip()
    return 32 <= len(value) <= 44 and _base58_length(value) == 32


def is_solana_signature(value: str) -> bool:
    """A Solana transaction signature: base58 that decodes to 64 bytes (usually 87 or 88 characters)."""
    value = value.strip()
    return 64 <= len(value) <= 90 and _base58_length(value) == 64
