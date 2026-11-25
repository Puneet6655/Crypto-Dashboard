"""AI analyst (app/analyst): fact sheet, answer checks, and the routes with a stand-in for Ollama."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.analyst import ollama
from app.analyst.checks import check_answer
from app.analyst.context import build_facts, find_in_case
from app.analyst.settings import analyst_settings
from app.main import app

SUBJECT = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
OTHER = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
TXID = "a" * 64


def make_result(score: float | None = 0.95):
    explanation = None
    if score is not None:
        explanation = {
            "score": score, "threshold": 0.914, "flagged": score >= 0.914, "note": "Live score. Not proof of a crime.",
            "model": {"id": "xgb_v2", "name": "XGBoost v2"},
            "explanation": {"features": [
                {"feature": "exposure_illicit_out", "value": 3.0, "contribution": 1.2, "pushes": "towards illicit"},
                {"feature": "fees_mean", "value": 0.0001, "contribution": -0.3, "pushes": "towards licit"},
            ], "note": "Explains the model."},
            "live": {"transactions_in_window": 12, "counterparties": {"payers": 3, "payees": 4},
                     "known_label_counterparties": {"illicit": [OTHER], "licit_count": 1}},
        }
    return {
        "investigation_id": "inv-1", "input": {"type": "bitcoin_address", "value": SUBJECT},
        "mode": "crypto_investigation", "method": "live_lookup", "data_mode": "live",
        "findings": [{"id": "f1", "kind": "observation", "title": "12 confirmed transactions",
                      "entity_ids": [], "evidence_ids": ["e1"]}],
        "indicators": [{"id": "i1", "rule": "OFAC SDN list", "matched": True, "reason": "counterparty listed"},
                       {"id": "i2", "rule": "Ransomwhere", "matched": False, "reason": "no match"}],
        "graph": {"nodes": [
            {"id": f"bitcoin:mainnet:address:{SUBJECT}", "type": "address", "label": "bc1qxy…0wlh",
             "details": {"balance_sats": 150_000_000}},
            {"id": f"bitcoin:mainnet:tx:{TXID}", "type": "transaction", "label": "tx aaaa…",
             "details": {"block_height": 900000, "total_transferred_sats": 250_000_000, "fee_sats": 1200}},
            {"id": f"bitcoin:mainnet:address:{OTHER}", "type": "address", "label": "1Boat…",
             "details": {"listed_in": ["OFAC SDN"]}},
        ], "edges": [
            {"id": "e-0", "source": f"bitcoin:mainnet:address:{SUBJECT}", "target": f"bitcoin:mainnet:tx:{TXID}",
             "relation": "observed", "label": "input, 2.50001200 BTC (250,001,200 sats)", "evidence_ids": ["e2"]},
            {"id": "e-1", "source": f"bitcoin:mainnet:tx:{TXID}", "target": f"bitcoin:mainnet:address:{OTHER}",
             "relation": "observed", "label": "output, 2.5 BTC (250,000,000 sats)", "evidence_ids": ["e2"]},
        ], "truncated": False, "limits": {"max_nodes": 250}},
        "evidence": [{"id": "e1", "source": "mempool.space address summary"}],
        "limitations": ["Newest 100 transactions only"], "uncertainty": {}, "explanation": explanation,
    }


def test_fact_sheet_has_the_score_rules_and_flagged_counterparty_first():
    facts = build_facts(make_result())
    assert facts["subject"]["value"] == SUBJECT
    assert facts["model_score"]["score"] == 0.95
    assert facts["model_score"]["verdict"].startswith("at or above")
    assert facts["model_score"]["top_shap_features"][0]["feature"] == "exposure_illicit_out"
    assert facts["rules_matched"] == [{"rule": "OFAC SDN list", "reason": "counterparty listed"}]
    assert facts["rules_clear_count"] == 1
    assert facts["counterparties"][0]["address"] == OTHER
    assert facts["counterparties"][0]["listed_in"] == ["OFAC SDN"]
    assert facts["transactions"][0]["txid"] == TXID
    assert facts["transactions"][0]["subject_side"] == "subject paid into it"


def test_fact_sheet_spells_out_who_paid_whom():
    result = make_result()
    [cp] = build_facts(result)["counterparties"]
    assert cp["money_flow_with_subject"] == "was paid by the subject"
    assert cp["links"][0].startswith("was paid by the subject: output, 2.5 BTC")
    # Turn the payment round: OTHER pays into the transaction and the subject receives from it.
    tx, subject, other = (f"bitcoin:mainnet:{k}" for k in (f"tx:{TXID}", f"address:{SUBJECT}", f"address:{OTHER}"))
    result["graph"]["edges"] = [
        {"id": "a", "source": other, "target": tx, "label": "input, 2.5 BTC"},
        {"id": "b", "source": tx, "target": subject, "label": "output, 2.4 BTC"},
    ]
    facts = build_facts(result)
    assert facts["counterparties"][0]["money_flow_with_subject"] == "paid the subject"
    assert facts["transactions"][0]["subject_side"] == "it paid the subject"


def test_fact_sheet_reads_direct_links_from_expand_node():
    result = make_result()
    third = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
    subject, other = f"bitcoin:mainnet:address:{SUBJECT}", f"bitcoin:mainnet:address:{OTHER}"
    result["graph"]["nodes"].append({"id": f"bitcoin:mainnet:address:{third}", "type": "address", "label": "bc1qar..."})
    result["graph"]["edges"] = [
        {"id": f"expand:{subject}>{other}", "source": subject, "target": other, "label": "paid 1 BTC, 1 tx",
         "first_time": "2026-03-01T00:00:00.000Z", "last_time": "2026-03-03T00:00:00.000Z"},
        {"id": f"expand:{other}>bitcoin:mainnet:address:{third}", "source": other,
         "target": f"bitcoin:mainnet:address:{third}", "label": "paid 0.4 BTC, 2 txs"},
    ]
    facts = build_facts(result)
    by_address = {c["address"]: c for c in facts["counterparties"]}
    assert by_address[OTHER]["money_flow_with_subject"] == "was paid by the subject"
    assert by_address[OTHER]["links"][0] == ("was paid by the subject: paid 1 BTC, 1 tx, 2026-03-01 to 2026-03-03"
                                             " (direct link added by expanding the graph)")
    assert by_address[third]["money_flow_with_subject"] == "no direct payment"
    [link] = by_address[third]["links"]
    assert link.startswith("received (the subject is not in this link): paid 0.4 BTC, 2 txs from 1Boat")
    assert facts["graph_size"]["links_added_by_expand_node"] == 2


def test_fact_sheet_counts_list_matches_and_explains_features():
    result = make_result()
    result["explanation"]["explanation"]["features"].insert(
        0, {"feature": "blocks_btwn_txs_min", "value": 0.0, "contribution": 0.5, "pushes": "towards illicit"})
    facts = build_facts(result)
    assert facts["list_matches"]["counterparties_on_a_list"] == 1
    assert facts["list_matches"]["counterparties_in_graph"] == 1
    [gap, unknown, _] = facts["model_score"]["top_shap_features"]
    assert gap["meaning"].startswith("Smallest single blocks between one of its transactions")
    assert "not the typical pattern" in gap["meaning"]
    assert unknown["meaning"] == "not described in the data"  # exposure_illicit_out is not in the glossary


def test_every_model_feature_has_a_meaning():
    from app.analyst.features import meaning
    from app.features.wallet_window import (ADDRESS_FEATURE_NAMES, EXPOSURE_FEATURE_NAMES, FEATURE_NAMES,
                                            NETWORK_FEATURE_NAMES)

    names = FEATURE_NAMES + NETWORK_FEATURE_NAMES + ADDRESS_FEATURE_NAMES + EXPOSURE_FEATURE_NAMES
    assert [n for n in names if not meaning(n)] == []


def test_fact_sheet_without_a_score_says_so():
    assert "No model score" in build_facts(make_result(score=None))["model_score"]


def test_find_in_case_returns_links():
    [match] = find_in_case(make_result(), OTHER[:10])
    assert match["value"] == OTHER
    assert match["links"][0].startswith(f"{OTHER} was paid by transaction {TXID}: output")
    assert find_in_case(make_result(), "ab") == []  # too short to search


def test_find_in_case_names_explorer_groups_and_directions():
    result = make_result()
    group = f"summary:more:bitcoin:mainnet:address:{OTHER}"
    other = f"bitcoin:mainnet:address:{OTHER}"
    result["graph"]["nodes"].append({"id": group, "type": "service", "label": "673 more", "details": {}})
    result["graph"]["edges"] += [
        {"id": "g1", "source": other, "target": group, "label": "paid 152.75 BTC, 9 txs"},
        {"id": "g2", "source": group, "target": other, "label": "paid 129.83 BTC, 7 txs"},
    ]
    [address, grp] = find_in_case(result, OTHER)
    assert f"{OTHER} paid group '673 more': paid 152.75 BTC, 9 txs" in address["links"]
    assert f"{OTHER} was paid by group '673 more': paid 129.83 BTC, 7 txs" in address["links"]
    assert grp["value"] == "group '673 more'"  # never mistaken for the address itself
    facts = build_facts(result)
    assert [c["address"] for c in facts["counterparties"]] == [OTHER]
    assert facts["other_entities"][0]["label"] == "673 more"


def test_checks_pass_real_facts_and_flag_invented_ones():
    facts = [build_facts(make_result())]
    good = f"It paid {OTHER} 2.5 BTC in tx {TXID}, about 250,000,000 sats. Score 0.95."
    assert check_answer(good, facts)["unverified"] == []
    bad = "It also paid 3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy 7.77 BTC in tx " + "b" * 64
    kinds = sorted(u["kind"] for u in check_answer(bad, facts)["unverified"])
    assert kinds == ["amount", "bitcoin address", "transaction id"]


def test_checks_accept_rounded_amounts():
    facts = [{"amount": "1.23456789 BTC"}]
    assert check_answer("about 1.23 BTC", facts)["unverified"] == []


@pytest.fixture
def fake_ollama(monkeypatch):
    """Replace Ollama with a list of scripted replies; records every request."""
    calls = []
    script = []

    async def chat(settings, messages, tools=None, client=None):
        calls.append({"messages": messages, "tools": tools})
        return script.pop(0)

    monkeypatch.setattr(ollama, "chat", chat)
    return calls, script


def test_explain_returns_the_answer_and_its_checks(fake_ollama):
    calls, script = fake_ollama
    script.append({"message": {"content": f"<think>hmm</think>Score 0.95, above 0.914. Paid {OTHER}."},
                   "eval_count": 40, "eval_duration": 1_000_000_000})
    response = TestClient(app).post("/api/analyst/explain", json={"investigation": make_result()})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == f"Score 0.95, above 0.914. Paid {OTHER}."
    assert body["checks"]["unverified"] == []
    assert body["tokens_per_second"] == 40.0
    assert "CASE FACTS" in calls[0]["messages"][1]["content"]


def test_chat_runs_a_tool_then_answers(fake_ollama):
    calls, script = fake_ollama
    script.append({"message": {"content": "", "tool_calls": [
        {"function": {"name": "find_in_case", "arguments": {"query": OTHER}}}]}})
    script.append({"message": {"content": f"{OTHER} received 2.5 BTC and is on the OFAC SDN list."}})
    response = TestClient(app).post("/api/analyst/chat", json={
        "investigation": make_result(), "messages": [{"role": "user", "content": "Who got the money?"}]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tools_used"] == [{"name": "find_in_case", "arguments": {"query": OTHER}, "ok": True}]
    assert body["checks"]["unverified"] == []
    tool_message = calls[1]["messages"][-1]
    assert tool_message["role"] == "tool" and OTHER in tool_message["content"]


def test_chat_rejects_a_conversation_that_does_not_end_with_a_question():
    response = TestClient(app).post("/api/analyst/chat", json={
        "investigation": make_result(), "messages": [{"role": "assistant", "content": "hi"}]})
    assert response.status_code == 422


def test_viewers_cannot_use_it(signed_in_as):
    signed_in_as("viewer")
    response = TestClient(app).post("/api/analyst/explain", json={"investigation": make_result()})
    assert response.status_code == 403


def test_ollama_down_is_a_readable_503(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:9")  # nothing listens there
    response = TestClient(app).post("/api/analyst/explain", json={"investigation": make_result()})
    assert response.status_code == 503
    assert "Ollama is not running" in response.json()["detail"]


@pytest.mark.anyio
async def test_status_reports_a_missing_model(monkeypatch):
    monkeypatch.setenv("ANALYST_MODEL", "qwen3:8b")

    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        info = await ollama.status(analyst_settings(), client)
    assert info["ollama_running"] and not info["model_ready"]
    assert "ollama pull qwen3:8b" in info["detail"]


def test_tool_rejects_a_bad_address():
    import asyncio

    from app.analyst.tools import run_tool

    out = asyncio.run(run_tool("get_model_score", {"address": "nope"}, make_result(), None, None))
    assert "not a full mainnet Bitcoin address" in out["error"]


@pytest.mark.anyio
async def test_chat_always_sends_think_and_drops_it_for_models_without_it(monkeypatch):
    monkeypatch.setenv("ANALYST_THINK", "false")
    bodies = []

    def handler(request):
        import json

        body = json.loads(request.content)
        bodies.append(body)
        if "think" in body:
            return httpx.Response(400, json={"error": "\"qwen2.5:7b\" does not support thinking"})
        return httpx.Response(200, json={"message": {"content": "ok"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reply = await ollama.chat(analyst_settings(), [{"role": "user", "content": "hi"}], client=client)
    assert bodies[0]["think"] is False and "think" not in bodies[1]
    assert reply["message"]["content"] == "ok"
