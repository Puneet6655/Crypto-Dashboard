"""Pydantic models for Ethereum lookups.

Amounts in wei and raw token units are strings: they often exceed 2^53, which JavaScript
numbers cannot hold exactly. Addresses and hashes are lower case.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.bitcoin import Provenance


class EthAddressSummary(BaseModel):
    entity_id: str = Field(description="ethereum:<network>:address:<0x...>")
    network: str
    chain_id: int
    address: str
    balance_wei: str
    sent_tx_count: int = Field(description="Transactions this address has sent (its nonce)")
    provenance: Provenance


class EthTokenTransfer(BaseModel):
    """One ERC-20 Transfer event."""
    tx_hash: str
    block_number: int | None
    block_time: datetime | None
    token_contract: str
    token_symbol: str | None = Field(description="None when only the raw event was seen")
    token_decimals: int | None
    from_address: str
    to_address: str
    value_raw: str = Field(description="Integer amount in the token's smallest unit")


class EthTransaction(BaseModel):
    entity_id: str
    network: str
    chain_id: int
    tx_hash: str
    status: Literal["success", "failed", "pending", "unknown"] = Field(
        description="unknown when the source lists transfers without receipts (Alchemy)")
    block_number: int | None
    block_time: datetime | None
    from_address: str
    to_address: str | None = Field(description="None for a contract creation")
    contract_created: str | None
    value_wei: str
    fee_wei: str | None
    method: str | None = Field(description="Called function name or 4-byte selector, when known")
    token_transfers: list[EthTokenTransfer] = Field(default_factory=list)
    provenance: Provenance


class EthInternalTransfer(BaseModel):
    """ETH moved by a contract during a transaction (a trace), not by the transaction itself."""
    tx_hash: str
    block_number: int | None
    block_time: datetime | None
    from_address: str
    to_address: str | None
    contract_created: str | None
    value_wei: str
    kind: str | None = Field(description="call, create, ...")
    failed: bool


class EthNftTransfer(BaseModel):
    """One ERC-721 or ERC-1155 transfer."""
    tx_hash: str
    block_number: int | None
    block_time: datetime | None
    standard: Literal["ERC-721", "ERC-1155"]
    token_contract: str
    token_name: str | None
    token_symbol: str | None
    token_id: str
    quantity: str = Field(description="1 for ERC-721")
    from_address: str
    to_address: str


class EthTokenHolding(BaseModel):
    """One ERC-20 balance held by an address now (Alchemy Token API), with a USD price when Alchemy has one."""
    token_contract: str
    token_symbol: str | None
    token_name: str | None
    token_decimals: int | None
    balance_raw: str = Field(description="Integer amount in the token's smallest unit")
    balance: str | None = Field(description="Human-readable amount, when decimals are known")
    price_usd: float | None
    value_usd: float | None = Field(description="None when Alchemy has no price (often spam or tiny tokens)")


class EthHoldings(BaseModel):
    address: str
    chain: str
    eth_balance_wei: str
    eth_price_usd: float | None
    eth_value_usd: float | None
    tokens: list[EthTokenHolding]
    tokens_total_usd: float = Field(description="Sum over tokens that have a price")
    tokens_not_shown: int = Field(description="Tokens left out (beyond the per-lookup limit)")
    provenance: Provenance


class EthNftHolding(BaseModel):
    """One NFT held by an address now (Alchemy NFT API)."""
    token_contract: str
    collection: str | None
    standard: str | None
    token_id: str
    name: str | None
    image_url: str | None
    quantity: str
    is_spam: bool | None = Field(description="Alchemy's spam flag for the collection; None when not given")
    floor_price_eth: float | None


class EthNftHoldings(BaseModel):
    address: str
    chain: str
    total_count: int | None = Field(description="NFTs the address holds in all")
    nfts: list[EthNftHolding] = Field(description="The newest holdings, up to 100 (one call)")
    spam_count: int
    provenance: Provenance
