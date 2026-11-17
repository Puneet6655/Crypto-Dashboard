"""Offline tests for the authority red-flag rule packs (app/redflags). All transactions are invented."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.features.wallet_window import TxRecord
from app.main import app
from app.redflags import packs as packs_module
from app.redflags.engine import build_report, evaluate, render, summarise
from app.redflags.expr import Condition, ExpressionError, MissingFacts
from app.redflags.facts import CATALOG, bitcoin_facts
from app.redflags.packs import PackError, PackRegistry, get_pack_registry, parse_pack

client = TestClient(app)
T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
SUBJECT = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"  # a well-formed address; its history here is invented
MIXED = "bc1qmixedcounterpartyxxxxxxxxxxxxxxxxxx"

MINI_PACK = """
id: test_pack
title: Test pack
authority: Test Authority
document: {title: Test indicators, url: "https://example.org/indicators"}
licence: Public domain
rules:
  - id: pass_through
    title: Money passes straight through
    citation: para 1
    severity: high
    weight: 3
    when: pass_through_ratio >= 0.9 and median_hours_to_spend <= 24
    explain: "Sent on {pass_through_ratio:.0%} after {median_hours_to_spend:.1f} h"
  - id: busy
    title: Very busy
    citation: para 2
    when: tx_count >= 1000
  - id: needs_price
    title: Near a threshold
    citation: para 3
    when: usd_near_reporting_threshold_count >= 1
"""


def tx(txid, height, inputs, outputs, fee=1_000, hours=0):
    value_in = sum(v for _, v in inputs)
    return TxRecord(txid, height, T0 + timedelta(hours=hours), fee, value_in, tuple(inputs), tuple(outputs))


def pass_through_history():
    """12 small payments in from different payers, then everything out in one payment two blocks later."""
    txs = [tx(f"in{i}", 1000 + i, [(f"bc1qpayer{i:02d}", 1_001_000)], [(SUBJECT, 1_000_000)], hours=i)
           for i in range(12)]
    txs.append(tx("out", 1013, [(SUBJECT, 12_000_000)], [(MIXED, 11_990_000)], fee=10_000, hours=13))
    return txs


class FakeIndex:
    """Stands in for IndicatorIndex: one ransomware-listed counterparty, all three lists 'downloaded'."""

    def __init__(self, listed=None):
        self.listed = listed or {}

    def loaded(self, source_id):
        return SimpleNamespace(source=SimpleNamespace(title=source_id.upper(), url=f"https://lists/{source_id}"),
                               snapshot_label=lambda: "snapshot of 2026-11-16", retrieved_at=T0)

    def lookup(self, address, source_ids=None):
        hits = [SimpleNamespace(family="TestLocker") for s in (source_ids or []) if self.listed.get(address) == s]
        return hits


# Condition language -------------------------------------------------------------------------------

def test_condition_reads_facts_and_chains_comparisons():
    c = Condition("0.95 <= ratio <= 1.05 and not (n < 3) or max(a, b) > 10")
    assert c.names == {"ratio", "n", "a", "b"}
    assert c.evaluate({"ratio": 1.0, "n": 5, "a": 0, "b": 0}) is True
    assert c.evaluate({"ratio": 2.0, "n": 5, "a": 11, "b": 0}) is True
    assert c.evaluate({"ratio": 2.0, "n": 5, "a": 1, "b": 0}) is False


@pytest.mark.parametrize("text", ["__import__('os')", "a.b > 1", "x[0] > 1", "'text' == x", "open(f)",
                                  "lambda: 1", "x ** 2 > 1", "X > 1", "min > 1", ""])
def test_condition_rejects_anything_outside_the_language(text):
    with pytest.raises(ExpressionError):
        Condition(text)


def test_unknown_fact_value_means_not_checked_not_false():
    with pytest.raises(MissingFacts) as err:
        Condition("a > 1 and b > 1").evaluate({"a": 5, "b": None})
    assert err.value.names == {"b"}


# Packs ---------------------------------------------------------------------------------------------

def test_every_built_in_pack_loads(tmp_path):
    registry = PackRegistry(tmp_path / "settings.json")
    assert registry.errors == []
    assert {"fatf_va_red_flags_2020", "fincen_ransomware_2021", "fincen_pig_butchering_2023",
            "austrac_digital_currency_2022", "ofac_virtual_currency_2021"} <= set(registry.packs)
    assert "_TEMPLATE" not in " ".join(registry.packs)
    for loaded in registry.packs.values():
        assert loaded.pack.document.url.startswith("https://")
        assert all(r.citation for r in loaded.pack.rules)


def test_template_is_a_valid_pack():
    text = (packs_module.BUILTIN_DIR / "_TEMPLATE.yaml").read_text(encoding="utf-8")
    assert parse_pack(text).id == "my_authority_topic_2026"


def test_bad_pack_lists_every_problem_in_plain_words():
    bad = MINI_PACK.replace("pass_through_ratio >= 0.9", "pass_through_ratoi >= 0.9").replace(
        "tx_count >= 1000", "tx_count.real > 1").replace("severity: high", "severity: huge")
    with pytest.raises(PackError) as err:
        parse_pack(bad)
    text = str(err.value)
    assert "pass_through_ratoi" in text and "tx_count.real" in text and "severity" in text


def test_settings_switch_packs_and_weights(tmp_path, monkeypatch):
    registry = PackRegistry(tmp_path / "settings.json")
    assert registry.setting("fatf_va_red_flags_2020") == {"enabled": True, "weight": 1.0}
    registry.update_setting("fatf_va_red_flags_2020", enabled=False, weight=2)
    assert registry.setting("fatf_va_red_flags_2020") == {"enabled": False, "weight": 2.0}
    assert all(p.pack.id != "fatf_va_red_flags_2020" for p, _ in registry.enabled("bitcoin"))
    monkeypatch.setenv("RED_FLAG_PACKS_DISABLED", "ofac_virtual_currency_2021")
    assert registry.setting("ofac_virtual_currency_2021")["enabled"] is False


def test_extra_folder_and_upload_folder(tmp_path, monkeypatch):
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "test_pack.yaml").write_text(MINI_PACK, encoding="utf-8")
    (extra / "broken.yaml").write_text("id: [not, a, pack", encoding="utf-8")
    monkeypatch.setenv("RULE_PACKS_DIR", str(extra))
    monkeypatch.setattr(packs_module, "UPLOAD_DIR", tmp_path / "uploads")
    registry = PackRegistry(tmp_path / "settings.json")
    assert registry.packs["test_pack"].origin == "folder"
    assert [e["file"] for e in registry.errors] == ["broken.yaml"]
    with pytest.raises(PackError):  # an uploaded pack cannot replace a pack from a folder
        registry.save_upload(MINI_PACK)
    uploaded = registry.save_upload(MINI_PACK.replace("id: test_pack", "id: test_pack_two"))
    assert uploaded.origin == "uploaded" and (tmp_path / "uploads" / "test_pack_two.yaml").exists()
    registry.delete_upload("test_pack_two")
    assert "test_pack_two" not in registry.packs


# Facts and engine ----------------------------------------------------------------------------------

def test_bitcoin_facts_leave_change_out_and_find_pass_through():
    bundle = bitcoin_facts(SUBJECT, pass_through_history(), FakeIndex({MIXED: "ransomwhere"}), frozenset())
    f = bundle.facts
    assert set(f) == set(CATALOG)
    assert (f["received_tx_count"], f["sent_tx_count"], f["payers"], f["payees"]) == (12, 1, 12, 1)
    assert f["received_btc"] == pytest.approx(0.12) and f["sent_btc"] == pytest.approx(0.12)  # fee counts as spent
    assert f["pass_through_ratio"] == pytest.approx(1.0)
    assert f["median_hours_to_spend"] == pytest.approx(2 / 6)  # two blocks after the last payment in
    assert f["largest_send_vs_median_receive"] == pytest.approx(12)
    assert f["ransomware_counterparties"] == 1 and f["sanctioned_counterparties"] == 0
    assert f["on_ransomware_list"] == 0 and f["illicit_label_counterparties"] == 0
    assert f["usd_near_reporting_threshold_count"] is None
    assert bundle.listed_counterparties["ransomware"] == [(MIXED, "TestLocker")]


def test_change_back_to_itself_is_not_a_receipt():
    txs = [tx("a", 10, [("bc1qpayer", 100_001_000)], [(SUBJECT, 100_000_000)]),
           tx("b", 20, [(SUBJECT, 100_000_000)], [("bc1qshop", 30_000_000), (SUBJECT, 69_999_000)])]
    f = bitcoin_facts(SUBJECT, txs).facts
    assert f["received_tx_count"] == 1 and f["received_btc"] == 1.0
    assert f["sent_btc"] == pytest.approx(0.30001)  # paid out less change; the fee counts as spent
    assert f["on_sanctions_list"] is None  # no index given: list facts unknown, not "no"


def test_coinjoin_and_peel_shapes():
    equal = [(f"bc1qmix{i}", 1_000_000) for i in range(5)]
    txs = [tx("cj", 10, [(SUBJECT, 5_100_000)], equal + [(SUBJECT, 90_000)]),
           tx("peel", 11, [(SUBJECT, 90_000)], [("bc1qsmall", 5_000), ("bc1qrest", 84_000)])]
    f = bitcoin_facts(SUBJECT, txs).facts
    assert f["coinjoin_like_tx_count"] == 1
    assert f["peel_like_send_share"] == 0.5


def test_engine_states_and_explanations(tmp_path):
    registry = PackRegistry.trial(parse_pack(MINI_PACK))
    facts = bitcoin_facts(SUBJECT, pass_through_history()).facts
    results = {r["rule_id"]: r for r in evaluate(registry, "bitcoin", facts)}
    assert results["pass_through"]["status"] == "matched"
    assert results["pass_through"]["reason"] == "Sent on 100% after 0.3 h"
    assert results["busy"]["status"] == "clear" and "tx count 13" in results["busy"]["reason"]
    assert results["needs_price"]["status"] == "not_checked"
    assert "price feed" in results["needs_price"]["reason"]
    summary = summarise(list(results.values()))
    assert (summary["matched"], summary["clear"], summary["not_checked"]) == (1, 1, 1)
    assert summary["highest_severity"] == "high" and summary["weighted_total"] == 3


def test_render_handles_unknown_values_and_bad_specs():
    assert render("{a:.2f} and {b} and {c:.0%}", {"a": 1.234, "b": None, "c": "x"}) == "1.23 and unknown and x"


def test_report_has_citable_findings_and_evidence(tmp_path):
    registry = PackRegistry(tmp_path / "settings.json")
    bundle = bitcoin_facts(SUBJECT, pass_through_history(), FakeIndex({MIXED: "ransomwhere"}), frozenset())
    report = build_report(registry, "bitcoin", "mainnet", SUBJECT, bundle, data_source="test")
    matched = {r["key"] for r in report["results"] if r["status"] == "matched"}
    assert "fincen_ransomware_2021.ransomware_counterparty" in matched
    assert "fincen_ransomware_2021.ransomware_address" not in matched  # the address itself is not listed
    assert "fatf_va_red_flags_2020.quick_pass_through" in matched
    evidence_ids = {e["id"] for e in report["evidence"]}
    for finding in report["findings"]:
        assert finding["kind"] == "rule_match" and set(finding["evidence_ids"]) <= evidence_ids
    ransomware = next(f for f in report["findings"] if "ransomware payment address" in f["title"])
    assert f"bitcoin:mainnet:address:{MIXED}" in ransomware["entity_ids"]
    assert report["flagged_nodes"][f"bitcoin:mainnet:address:{MIXED}"]
    assert any(e["source_url"].startswith("https://www.fincen.gov") for e in report["evidence"])
    assert report["disclaimer"] in report["limitations"]


# API -----------------------------------------------------------------------------------------------

@pytest.fixture
def temp_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(packs_module, "UPLOAD_DIR", tmp_path / "uploads")
    registry = PackRegistry(tmp_path / "settings.json")
    app.dependency_overrides[get_pack_registry] = lambda: registry
    yield registry
    app.dependency_overrides.pop(get_pack_registry, None)


def test_api_lists_facts_and_packs(temp_registry):
    facts = client.get("/api/red-flags/facts").json()
    assert {"name", "unit", "description", "chains"} <= set(facts[0])
    body = client.get("/api/red-flags/packs").json()
    assert body["errors"] == [] and len(body["packs"]) >= 5
    source = client.get("/api/red-flags/packs/ofac_virtual_currency_2021/source").json()
    assert "sanctioned_address" in source["yaml"]


def test_api_validate_and_try_a_pack(temp_registry):
    bad = client.post("/api/red-flags/packs/validate", json={"yaml": "id: x\ntitle: y"}).json()
    assert bad["valid"] is False and bad["errors"]
    good = client.post("/api/red-flags/packs/validate", json={
        "yaml": MINI_PACK, "facts": {"pass_through_ratio": 0.97, "median_hours_to_spend": 2, "tx_count": 5,
                                     "made_up": 1}}).json()
    assert good["valid"] is True and good["unknown_facts"] == ["made_up"]
    assert good["trial_summary"]["matched"] == 1


def test_api_admin_changes_and_uploads(temp_registry, signed_in_as):
    assert client.patch("/api/red-flags/packs/fatf_va_red_flags_2020", json={"enabled": False}).json()["enabled"] is False
    assert client.post("/api/red-flags/packs", json={"yaml": MINI_PACK}).status_code == 200
    assert client.post("/api/red-flags/packs", json={"yaml": "id: [broken"}).status_code == 422
    assert client.delete("/api/red-flags/packs/ofac_virtual_currency_2021").status_code == 404
    signed_in_as("analyst")
    assert client.patch("/api/red-flags/packs/test_pack", json={"enabled": False}).status_code == 403
    assert client.post("/api/red-flags/packs", json={"yaml": MINI_PACK}).status_code == 403
    assert client.delete("/api/red-flags/packs/test_pack").status_code == 403


def test_api_checks_a_bitcoin_address(temp_registry, monkeypatch):
    async def fake_fetch(provider, address):
        return pass_through_history()

    monkeypatch.setattr("app.redflags.service.fetch_window_transactions", fake_fetch)
    monkeypatch.setattr("app.redflags.service._known_illicit", lambda: None)
    from app.intelligence.index import get_indicator_index

    app.dependency_overrides[get_indicator_index] = lambda: FakeIndex({MIXED: "ransomwhere"})
    try:
        body = client.post("/api/red-flags/bitcoin", json={"address": SUBJECT}).json()
    finally:
        app.dependency_overrides.pop(get_indicator_index, None)
    assert body["summary"]["matched"] >= 2 and body["summary"]["highest_severity"] == "high"
    label_rule = next(r for r in body["results"] if r["key"] == "fatf_va_red_flags_2020.illicit_counterparty")
    assert label_rule["status"] == "not_checked"  # no known-label table: unknown, not clear
    assert client.post("/api/red-flags/bitcoin", json={"address": "not-an-address-at-all-xxxxx"}).status_code == 422


def test_authority_rules_method_adds_flags_to_the_lookup(temp_registry, monkeypatch):
    import asyncio

    from app.redflags.investigation import investigate_with_red_flags
    from app.schemas.investigation import (Graph, GraphLimits, GraphNode, InvestigationInput,
                                           InvestigationRequest, InvestigationResult, Scorecard)

    async def fake_fetch(provider, address):
        return pass_through_history()

    monkeypatch.setattr("app.redflags.service.fetch_window_transactions", fake_fetch)
    monkeypatch.setattr("app.redflags.service._known_illicit", lambda: None)
    monkeypatch.setattr("app.redflags.service.get_pack_registry", lambda: temp_registry)
    request = InvestigationRequest(mode="crypto_investigation", input_type="bitcoin_address", value=SUBJECT,
                                   method="authority_rules")
    subject_node = f"bitcoin:mainnet:address:{SUBJECT}"
    mixed_node = f"bitcoin:mainnet:address:{MIXED}"

    async def lookup(live):
        assert live.method == "live_lookup"
        return InvestigationResult(
            investigation_id="inv_test", created_at=T0, input=InvestigationInput(type="bitcoin_address", value=SUBJECT),
            mode="crypto_investigation", method="live_lookup", data_mode="live", findings=[], indicators=[],
            graph=Graph(nodes=[GraphNode(id=subject_node, type="address", label="subject"),
                               GraphNode(id=mixed_node, type="address", label="other")],
                        edges=[], truncated=False, limits=GraphLimits(max_nodes=50)),
            evidence=[], source_timestamps={}, limitations=[], uncertainty={}, scorecard=Scorecard(configured=False))

    result = asyncio.run(investigate_with_red_flags(request, None, 50, FakeIndex({MIXED: "ransomwhere"}), lookup))
    assert result.method == "authority_rules"
    assert any(f.id.startswith("rff") for f in result.findings)
    nodes = {n.id: n for n in result.graph.nodes}
    assert nodes[subject_node].details["red_flags"] and nodes[mixed_node].details["red_flags"]
    assert result.explanation["red_flags"]["summary"]["matched"] >= 2


def test_command_line_validates_and_tries_a_pack(tmp_path, capsys):
    from app.redflags.__main__ import main

    pack = tmp_path / "test_pack.yaml"
    pack.write_text(MINI_PACK, encoding="utf-8")
    facts = tmp_path / "facts.json"
    facts.write_text('{"pass_through_ratio": 0.99, "median_hours_to_spend": 1, "tx_count": 3}', encoding="utf-8")
    assert main(["validate", str(pack), "--facts", str(facts)]) == 0
    out = capsys.readouterr().out
    assert "Valid: test_pack" in out and "[matched    ] pass_through" in out and "not_checked" in out
    pack.write_text(MINI_PACK.replace("tx_count >= 1000", "tx_count.real > 1"), encoding="utf-8")
    assert main(["validate", str(pack)]) == 1
    assert main(["facts"]) == 0
