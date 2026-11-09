"""Pydantic models for Solana lookups (Alchemy's Solana JSON-RPC).

Amounts in lamports (1 SOL = 1,000,000,000 lamports) and raw token units are strings, like the
Ethereum models, because they can exceed what JavaScript numbers hold exactly.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.bitcoin import Provenance


class SolAddressSummary(BaseModel):
    entity_id: str = Field(description="solana:mainnet:address:<base58>")
    network: str
    address: str
    balance_lamports: str
    exists: bool = Field(description="False when the account has never been created (or was closed)")
    owner_program: str | None = Field(description="Program that owns the account; the System Program for wallets")
    executable: bool = Field(description="True for a program (smart contract) account")
    provenance: Provenance


class SolSignature(BaseModel):
    """One transaction an address took part in (getSignaturesForAddress)."""
    signature: str
    slot: int
    block_time: datetime | None
    failed: bool


class SolTransfer(BaseModel):
    """SOL or an SPL token moved by one instruction. For tokens, from/to are the wallets that own the
    token accounts when the transaction shows them, otherwise the token accounts themselves."""
    kind: Literal["sol", "token"]
    from_address: str
    to_address: str
    amount_raw: str = Field(description="Lamports for SOL; smallest token unit for tokens")
    mint: str | None = None
    decimals: int | None = None


class SolTransaction(BaseModel):
    entity_id: str
    network: str
    signature: str
    status: Literal["success", "failed"]
    slot: int | None
    block_time: datetime | None
    fee_lamports: int | None
    signers: list[str]
    programs: list[str] = Field(description="Programs the transaction called, by name when Alchemy knows it")
    transfers: list[SolTransfer]
    balance_changes: dict[str, int] = Field(default_factory=dict,
                                            description="Address -> change in lamports (fees included)")
    provenance: Provenance


class SolTokenHolding(BaseModel):
    mint: str
    token_account: str
    amount_raw: str
    amount: str | None
    decimals: int | None


class SolHoldings(BaseModel):
    address: str
    balance_lamports: str
    tokens: list[SolTokenHolding] = Field(description="SPL token accounts with a balance above zero")
    provenance: Provenance
