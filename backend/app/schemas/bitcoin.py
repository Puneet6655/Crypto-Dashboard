"""Pydantic models: the shapes our API promises to return.

Our own field names are stable even if the provider changes its JSON.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field

SATS_PER_BTC = 100_000_000


def format_btc(sats: int | None) -> str:
    """Readable amount in BTC with sats after it, e.g. '0.0012 BTC (120,000 sats)'.

    Uses exact integer maths, so no floating-point rounding can creep in.
    """
    if sats is None:
        return "unknown amount"
    sign = "-" if sats < 0 else ""
    whole, frac = divmod(abs(sats), SATS_PER_BTC)
    frac_text = f"{frac:08d}".rstrip("0")
    btc = f"{sign}{whole:,}" + (f".{frac_text}" if frac_text else "")
    return f"{btc} BTC ({sats:,} {'sat' if abs(sats) == 1 else 'sats'})"

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

    @computed_field(description="Current balance in satoshis: confirmed plus unconfirmed (pending) change")
    @property
    def balance_sats(self) -> int:
        return self.confirmed.balance_sats + self.unconfirmed.balance_sats


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
    weight: int | None = Field(default=None, description="Weight units (vsize = (weight + 3) // 4); None if unknown")
    inputs: list[TxEndpoint]
    outputs: list[TxEndpoint]
    provenance: Provenance

    @computed_field(description="Total amount transferred: the sum of all outputs, in satoshis (the fee is not included)")
    @property
    def total_output_sats(self) -> int:
        return sum(o.value_sats or 0 for o in self.outputs)

    @computed_field(description="Sum of all inputs in satoshis; None for a coinbase (newly mined) transaction")
    @property
    def total_input_sats(self) -> int | None:
        values = [i.value_sats for i in self.inputs if i.value_sats is not None]
        return sum(values) if values else None


class ProviderHealth(BaseModel):
    provider: str
    reachable: bool
    tip_height: int | None
    checked_at: datetime
    detail: str