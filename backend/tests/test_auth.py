"""Sign-in and roles, without a database: password hashing, who may call what, and safe failures."""
from fastapi.testclient import TestClient

from app.auth import passwords
from app.auth.throttle import Throttle
from app.main import app

client = TestClient(app)


def test_password_hash_round_trip():
    stored = passwords.hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert "correct horse battery" not in stored
    assert passwords.verify_password("correct horse battery", stored)
    assert not passwords.verify_password("correct horse battery!", stored)
    assert not passwords.needs_rehash(stored)


def test_same_password_hashes_differently_each_time():
    assert passwords.hash_password("same password 1") != passwords.hash_password("same password 1")


def test_malformed_hash_never_matches():
    assert not passwords.verify_password("anything", "")
    assert not passwords.verify_password("anything", "bcrypt$whatever")
    assert not passwords.verify_password("anything", "scrypt$1$2$3$!!$!!")


def test_password_rules():
    assert passwords.password_problem("short") is not None
    assert passwords.password_problem("aaaaaaaaaaaa") is not None
    assert passwords.password_problem(" leading space x") is not None
    assert passwords.password_problem("a fine passphrase") is None


def test_throttle_locks_after_too_many_failures():
    throttle = Throttle(max_failures=3, window_seconds=60)
    for _ in range(3):
        assert throttle.seconds_locked("k") == 0
        throttle.failed("k")
    assert throttle.seconds_locked("k") > 0
    throttle.succeeded("k")
    assert throttle.seconds_locked("k") == 0


def test_data_routes_need_sign_in(signed_out):
    # No database in tests, so the API says sign-in is unavailable rather than letting anyone in.
    for path in ("/api/bitcoin/providers", "/api/indicators/status", "/api/cases", "/api/sources",
                 "/api/status/database", "/api/auth/me"):
        response = client.get(path)
        assert response.status_code in (401, 503), path


def test_bad_token_is_refused(signed_out):
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code in (401, 503)


def test_health_and_login_config_stay_public(signed_out):
    assert client.get("/api/health").status_code == 200
    config = client.get("/api/auth/config").json()
    assert config["auth_required"] is True
    assert config["signup_enabled"] is False  # no database in tests


def test_viewer_cannot_do_admin_things(signed_in_as):
    signed_in_as("viewer")
    assert client.post("/api/sources", json={"name": "x", "category": "y"}).status_code == 403
    assert client.get("/api/admin/users").status_code == 403
    assert client.post("/api/cases", json={"title": "x"}).status_code == 403
    assert client.post("/api/bigquery/estimate", json={"start_date": "2026-10-01", "end_date": "2026-10-02"}).status_code == 403


def test_analyst_may_create_cases_but_not_edit_sources(signed_in_as):
    signed_in_as("analyst")
    # Allowed past the role check; fails only because tests have no database.
    assert client.post("/api/cases", json={"title": "x"}).status_code == 503
    assert client.post("/api/sources", json={"name": "x", "category": "y"}).status_code == 403
    assert client.delete("/api/cases/1").status_code == 403


def test_sources_are_listed_read_only_without_a_database(signed_in_as):
    signed_in_as("viewer")
    body = client.get("/api/sources").json()
    assert body["editable"] is False
    assert any(s["name"].startswith("mempool.space") for s in body["sources"])


def test_bigquery_run_is_not_built_yet():
    response = client.post("/api/bigquery/run")
    assert response.status_code == 501
