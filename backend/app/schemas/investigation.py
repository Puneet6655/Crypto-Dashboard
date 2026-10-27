"""The request/response contract between the browser and the API.

Every mode and method returns this same outer shape, so the React screen
can render any result. Chain-specific detail lives inside the nodes,
edges and evidence, not in new top-level fields.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.bitcoin import DataMode

Mode = Literal["scam_early_warning", "crypto_investigation", "x402"]
InputType = Literal["url_domain", "bitcoin_address", "bitcoin_txid", "x402_payment_id"]
Relation = Literal["observed", "reported", "inferred"]


class InvestigationRequest(BaseModel):
    """What the browser sends when the analyst presses Start Investigation."""
    mode: Mode
    input_type: InputType
    value: str = Field(min_length=1, max_length=200)
    method: str = Field(min_length=1, max_length=64)


class InvestigationInput(BaseModel):
    type: InputType
    value: str


class Finding(BaseModel):
    id: str
    title: str
    kind: Literal["observation", "rule_match", "model_output", "analyst_confirmed", "synthetic_example"]
    entity_ids: list[str]
    evidence_ids: list[str]


class Indicator(BaseModel):
    id: str
    rule: str
    matched: bool | None = Field(description="True, False, or None when the rule did not run")
    reason: str


class GraphNode(BaseModel):
    id: str
    type: Literal["address", "transaction", "domain", "service", "payment"]
    label: str
    details: dict = Field(default_factory=dict)


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: Relation
    label: str
    evidence_ids: list[str]


class GraphLimits(BaseModel):
    max_nodes: int


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool
    limits: GraphLimits


class Evidence(BaseModel):
    id: str
    source: str
    source_url: str
    retrieved_at: datetime
    method_version: str
    excerpt: str


class Scorecard(BaseModel):
    configured: bool


class InvestigationResult(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    investigation_id: str
    created_at: datetime
    input: InvestigationInput
    mode: Mode
    method: str
    data_mode: DataMode
    findings: list[Finding]
    indicators: list[Indicator]
    graph: Graph
    evidence: list[Evidence]
    source_timestamps: dict[str, datetime]
    limitations: list[str]
    uncertainty: dict[str, int | bool | str]
    scorecard: Scorecard
