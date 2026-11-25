"""AI analyst with Claude through the Claude CLI (app/analyst/claude_cli.py). The CLI is replaced by a fake process."""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.analyst import claude_cli
from app.analyst.settings import analyst_settings
from app.main import app
from tests.test_analyst import OTHER, make_result


def cli_json(result: str, *, is_error: bool = False, model: str = "claude-sonnet-5-5") -> str:
    return json.dumps({
        "type": "result", "is_error": is_error, "result": result, "duration_api_ms": 2000,
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5000, "cache_read_input_tokens": 0,
                  "output_tokens": 100},
        "modelUsage": {model: {}} if not is_error else {},
    })


@pytest.fixture
def fake_cli(monkeypatch):
    """Pretend `claude` is installed; every run is recorded and answered from `replies` (stdout, returncode)."""
    runs, replies = [], []

    def run(args, stdin, timeout, cwd=None):
        runs.append({"args": args, "stdin": stdin, "timeout": timeout, "cwd": cwd})
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        stdout, code = reply
        return subprocess.CompletedProcess(args, code, stdout=stdout, stderr="")

    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "C:/fake/claude.cmd")
    monkeypatch.setattr(claude_cli, "_run", run)
    claude_cli._status_cache.clear()
    return runs, replies


def test_explain_with_claude_sends_everything_by_stdin_and_runs_without_tools(fake_cli):
    runs, replies = fake_cli
    replies.append((cli_json(f"Score 0.95, above 0.914. **{OTHER}** was paid by the subject."), 0))
    response = TestClient(app).post("/api/analyst/explain", json={"investigation": make_result(), "provider": "claude_cli"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "claude_cli" and body["model"] == "claude-sonnet-5-5"
    assert body["checks"]["unverified"] == []
    assert body["output_tokens"] == 100 and body["prompt_tokens"] == 5010 and body["tokens_per_second"] == 50.0

    [run] = runs
    args = run["args"]
    assert args[args.index("--tools") + 1] == ""  # no tools at all
    assert args[args.index("--max-turns") + 1] == "1"
    assert "--no-session-persistence" in args and "--strict-mcp-config" in args
    assert args[args.index("--model") + 1] == analyst_settings().claude_model
    assert "CASE FACTS" in run["stdin"] and "RULES" in run["stdin"]  # the facts and rules go by stdin...
    assert not any("CASE FACTS" in a or OTHER in a for a in args)  # ...never on the command line
    assert run["cwd"] and "analyst-claude-" in run["cwd"]  # an empty temporary folder


def test_chat_with_claude_includes_the_conversation_and_flags_invented_items(fake_cli):
    runs, replies = fake_cli
    replies.append((cli_json("It also paid 3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy 7.77 BTC."), 0))
    response = TestClient(app).post("/api/analyst/chat", json={
        "investigation": make_result(), "provider": "claude_cli",
        "messages": [{"role": "user", "content": "Who got the money?"}]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tools_used"] == []
    assert sorted(u["kind"] for u in body["checks"]["unverified"]) == ["amount", "bitcoin address"]
    assert "INVESTIGATOR:\nWho got the money?" in runs[0]["stdin"]


def test_ollama_stays_the_default(fake_cli, monkeypatch):
    from app.analyst import ollama

    async def chat(settings, messages, tools=None, client=None):
        return {"message": {"content": "local answer"}}

    monkeypatch.setattr(ollama, "chat", chat)
    body = TestClient(app).post("/api/analyst/explain", json={"investigation": make_result()}).json()
    assert body["provider"] == "ollama" and body["answer"] == "local answer"
    assert fake_cli[0] == []  # the Claude CLI was not run


def test_provider_setting_switches_the_default(fake_cli, monkeypatch):
    monkeypatch.setenv("ANALYST_PROVIDER", "claude_cli")
    fake_cli[1].append((cli_json("ok"), 0))
    body = TestClient(app).post("/api/analyst/explain", json={"investigation": make_result()}).json()
    assert body["provider"] == "claude_cli"


@pytest.mark.parametrize("stdout, code", [
    (cli_json("Failed to authenticate: OAuth session expired and could not be refreshed", is_error=True), 0),
    ("", 1),
])
def test_not_signed_in_is_a_readable_503(fake_cli, stdout, code, monkeypatch):
    if not stdout:  # older CLIs print the problem to stderr instead of JSON
        def run(args, stdin, timeout, cwd=None):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Not logged in. Please run /login")
        monkeypatch.setattr(claude_cli, "_run", run)
    else:
        fake_cli[1].append((stdout, code))
    response = TestClient(app).post("/api/analyst/explain", json={"investigation": make_result(), "provider": "claude_cli"})
    assert response.status_code == 503
    assert "not signed in" in response.json()["detail"]


def test_not_installed_is_a_readable_503(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: None)
    response = TestClient(app).post("/api/analyst/explain", json={"investigation": make_result(), "provider": "claude_cli"})
    assert response.status_code == 503
    assert "not installed" in response.json()["detail"]


def test_timeout_is_a_readable_503(fake_cli):
    fake_cli[1].append(subprocess.TimeoutExpired("claude", 120))
    response = TestClient(app).post("/api/analyst/explain", json={"investigation": make_result(), "provider": "claude_cli"})
    assert response.status_code == 503
    assert "did not answer within" in response.json()["detail"]


def test_unknown_provider_is_rejected():
    response = TestClient(app).post("/api/analyst/explain", json={"investigation": make_result(), "provider": "gpt"})
    assert response.status_code == 422


def test_status_checks_claude_without_spending_tokens(fake_cli):
    runs, replies = fake_cli
    replies += [("2.1.310 (Claude Code)\n", 0), (json.dumps({"loggedIn": True, "email": "someone@example.com"}), 0)]
    info = TestClient(app).get("/api/analyst/status").json()
    claude = info["claude_cli"]
    assert claude["ready"] and claude["signed_in"] and claude["version"] == "2.1.310 (Claude Code)"
    assert "email" not in json.dumps(claude)  # the account is not passed on
    assert [r["args"][1:] for r in runs] == [["--version"], ["auth", "status"]]  # no -p call, so no tokens
    TestClient(app).get("/api/analyst/status")
    assert len(runs) == 2  # cached for a minute


def test_claude_gets_the_whole_case_and_a_full_briefing(fake_cli):
    runs, replies = fake_cli
    result = make_result()
    tx = result["graph"]["nodes"][1]["id"]
    for i in range(60):  # far more counterparties than the local model's 25
        address = f"bitcoin:mainnet:address:bc1qtest{i:034d}"
        result["graph"]["nodes"].append({"id": address, "type": "address", "label": f"c{i}"})
        result["graph"]["edges"].append({"id": f"x{i}", "source": tx, "target": address, "label": "output, 0.1 BTC"})
    replies.append((cli_json("ok"), 0))
    TestClient(app).post("/api/analyst/explain", json={"investigation": result, "provider": "claude_cli"})
    stdin = runs[0]["stdin"]
    assert f"bc1qtest{59:034d}" in stdin  # nothing cut
    assert "There is no length limit" in stdin and "under 220 words" not in stdin
    assert "Be thorough" in stdin


def test_local_model_keeps_the_compact_sheet():
    from app.analyst.context import FULL, LOCAL, build_facts

    result = make_result()
    tx = result["graph"]["nodes"][1]["id"]
    for i in range(60):
        address = f"bitcoin:mainnet:address:bc1qtest{i:034d}"
        result["graph"]["nodes"].append({"id": address, "type": "address", "label": f"c{i}"})
        result["graph"]["edges"].append({"id": f"x{i}", "source": tx, "target": address, "label": "output, 0.1 BTC"})
    assert len(build_facts(result, LOCAL)["counterparties"]) == 25
    assert len(build_facts(result, FULL)["counterparties"]) == 61
