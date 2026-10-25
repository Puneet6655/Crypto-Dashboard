"""Pydantic models: the shapes our API promises to return.

Our own field names are stable even if the provider changes its JSON.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DataMode = Literal["live", "recorded", "synthetic"]


class Provenance(BaseModel):
    """Where a piece of data came from and when we fetched it."""
    provider: str = Field(description="Provider adapter name, e.g. mempool.space")
    source_url: str = Field(description="Exact URL the backend requested")
    retrieved_at: datetime = Field(description="UTC time the backend received the response")
    data_mode: DataMode


class ActivityTotals(BaseModel):
    """Counts and amounts for one status (confirmed or unconfirmed). Amounts are integer satoshis."""
    tx_count: int
    funded_sats: int
    spent_sats: int
    balance_sats: int


class AddressSummary(BaseModel):
    entity_id: str = Field(description="Network-scoped ID: bitcoin:<network>:address:<address>")
    network: str
    address: str
    confirmed: ActivityTotals
    unconfirmed: ActivityTotals
    provenance: Provenance


class TxEndpoint(BaseModel):
    """One input or output of a transaction."""
    address: str | None = Field(description="None when the script has no standard address (e.g. OP_RETURN, coinbase)")
    value_sats: int | None


class TransactionSummary(BaseModel):
    entity_id: str
    network: str
    txid: str
    status: Literal["confirmed", "unconfirmed"]
    block_height: int | None
    block_time: datetime | None
    fee_sats: int | None
    inputs: list[TxEndpoint]
    outputs: list[TxEndpoint]
    provenance: Provenance


class ProviderHealth(BaseModel):
    provider: str
    reachable: bool
    tip_height: int | None
    checked_at: datetime
    detail: str