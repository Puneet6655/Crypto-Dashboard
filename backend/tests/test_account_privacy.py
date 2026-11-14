"""Delete account, data export, sign-up consent and retention, against a real Postgres.

Skipped unless TEST_DATABASE_URL points at an EMPTY, throwaway database (every table in it is dropped first).
    TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/dashboard_test python -m pytest tests/test_account_privacy.py
"""
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import legal
from app.alerts import manual
from app.auth import accounts, passwords, privacy, throttle
from app.auth.deps import current_user
from app.db import PostgresStore
from app.main import app
from app.store import get_store
from app.workspace import cases, sources

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="needs TEST_DATABASE_URL (a throwaway Postgres database)")

client = TestClient(app)
PASSWORD = "a fine passphrase"
ADULT = date(1990, 5, 17).isoformat()


@pytest.fixture
def store():
    import psycopg

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    manual._ready.clear()
    db = PostgresStore(DATABASE_URL)
    app.dependency_overrides[get_store] = lambda: db
    app.dependency_overrides.pop(current_user, None)  # use real sign-in tokens
    throttle.signup_by_ip._failures.clear()
    yield db
    app.dependency_overrides.pop(get_store, None)
    db.close()


def signup(email="ana@example.test", **changes):
    body = {"email": email, "display_name": "Ana", "password": PASSWORD, "date_of_birth": ADULT,
            "user_type": "researcher", "organisation": "Uni Lab", "accept_terms": True, **changes}
    return client.post("/api/auth/signup", json=body)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_signup_needs_age_type_and_consent(store):
    assert signup(date_of_birth=None).status_code == 422
    assert signup(date_of_birth=date(date.today().year - 10, 1, 1).isoformat()).status_code == 422
    assert signup(user_type=None).status_code == 422
    assert signup(user_type="hacker").status_code == 422
    response = signup(accept_terms=False)
    assert response.status_code == 422 and "terms" in response.json()["detail"]
    response = signup()
    assert response.status_code == 201
    user = response.json()["user"]
    assert user["user_type"] == "researcher" and user["organisation"] == "Uni Lab"
    assert user["terms_version"] == legal.TERMS_VERSION and user["needs"] == []


def test_date_of_birth_is_never_stored(store):
    signup()
    with store.pool.connection() as conn:
        columns = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'app_users'").fetchall()}
        row = conn.execute("SELECT age_confirmed_at FROM app_users").fetchone()
    assert not any("birth" in c or c == "dob" for c in columns)
    assert row[0] is not None


def test_old_accounts_are_asked_to_complete_their_profile(store):
    accounts.create_user(store.pool, "old@example.test", "Old", passwords.hash_password(PASSWORD), "admin")
    token = client.post("/api/auth/login", json={"email": "old@example.test", "password": PASSWORD}).json()["token"]
    assert client.get("/api/auth/me", headers=auth(token)).json()["needs"] == ["age", "user_type", "terms"]
    bad = client.post("/api/auth/profile", headers=auth(token), json={"user_type": "student", "accept_terms": True})
    assert bad.status_code == 422  # no date of birth yet
    done = client.post("/api/auth/profile", headers=auth(token),
                       json={"date_of_birth": ADULT, "user_type": "student", "accept_terms": True})
    assert done.status_code == 200 and done.json()["needs"] == []
    with store.pool.connection() as conn:  # new terms: asked again, but not for the age
        conn.execute("UPDATE app_users SET terms_version = '2000-01-01'")
    accounts.forget_user(done.json()["id"])
    assert client.get("/api/auth/me", headers=auth(token)).json()["needs"] == ["terms"]
    again = client.post("/api/auth/profile", headers=auth(token), json={"user_type": "student", "accept_terms": True})
    assert again.status_code == 200 and again.json()["needs"] == []


def _make_data(store, user_id, email):
    own = cases.create_case(store.pool, user_id, "My case", "", "medium")
    cases.add_item(store.pool, own, user_id, {"item_type": "address", "value": "0x" + "a" * 40, "network": "ethereum",
                                              "label": None, "note": "mine", "source": None})
    manual.add(store.pool, "bitcoin", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", "watch", email)
    return own


def test_export_has_everything_but_secrets(store):
    body = signup().json()
    _make_data(store, body["user"]["id"], "ana@example.test")
    response = client.get("/api/auth/export", headers=auth(body["token"]))
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    data = response.json()
    assert data["profile"]["email"] == "ana@example.test"
    assert data["cases_you_created"][0]["items"][0]["note"] == "mine"
    assert data["watchlist_addresses_you_added"][0]["chain"] == "bitcoin"
    assert any(row["action"] == "user.signup" for row in data["audit_log"])
    text = response.text
    assert "password_hash" not in text and "token_hash" not in text and "scrypt$" not in text


def test_delete_account_removes_everything_of_theirs(store):
    ana = signup().json()
    ben = signup("ben@example.test").json()
    ana_id, ben_id = ana["user"]["id"], ben["user"]["id"]
    own_case = _make_data(store, ana_id, "ana@example.test")
    ben_case = cases.create_case(store.pool, ben_id, "Ben's case", "", "low")
    cases.add_item(store.pool, ben_case, ana_id, {"item_type": "address", "value": "0x" + "b" * 40,
                                                  "network": "ethereum", "label": None, "note": "", "source": None})
    cases.add_item(store.pool, ben_case, ben_id, {"item_type": "address", "value": "0x" + "c" * 40,
                                                  "network": "ethereum", "label": None, "note": "", "source": None})

    headers = auth(ana["token"])
    assert client.post("/api/auth/delete-account", headers=headers,
                       json={"password": "wrong password!", "confirm": "DELETE"}).status_code == 403
    assert client.post("/api/auth/delete-account", headers=headers,
                       json={"password": PASSWORD, "confirm": "yes"}).status_code == 422
    response = client.post("/api/auth/delete-account", headers=headers, json={"password": PASSWORD, "confirm": "DELETE"})
    assert response.status_code == 200
    assert response.json()["removed"] == {"cases": 1, "items_in_other_cases": 1, "watchlist_addresses": 1,
                                          "audit_rows_anonymised": 1}

    assert accounts.get_user(store.pool, ana_id) is None
    assert not cases.case_exists(store.pool, own_case)
    remaining = [i["value"] for i in cases.get_case(store.pool, ben_case)["items"]]
    assert remaining == ["0x" + "c" * 40]  # Ben's own item stays, Ana's is gone
    assert manual.list_all(store.pool) == []
    with store.pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM audit_log WHERE user_email = 'ana@example.test' "
                            "OR target = 'ana@example.test'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM audit_log WHERE action = 'user.deleted'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM app_sessions WHERE user_id = %s", (ana_id,)).fetchone()[0] == 0
    assert client.get("/api/auth/me", headers=headers).status_code == 401  # signed out everywhere
    assert client.post("/api/auth/login", json={"email": "ana@example.test", "password": PASSWORD}).status_code == 401


def test_last_admin_cannot_delete_themselves(store):
    body = signup().json()
    accounts.update_user(store.pool, body["user"]["id"], role="admin")
    response = client.post("/api/auth/delete-account", headers=auth(body["token"]),
                           json={"password": PASSWORD, "confirm": "DELETE"})
    assert response.status_code == 409
    assert accounts.get_user(store.pool, body["user"]["id"]) is not None


def test_retention_clean_up(store):
    viewer = signup().json()["user"]["id"]
    admin = signup("boss@example.test").json()["user"]["id"]
    accounts.update_user(store.pool, admin, role="admin")
    recent = signup("new@example.test").json()["user"]["id"]
    with store.pool.connection() as conn:
        conn.execute("UPDATE app_users SET created_at = now() - interval '3 years', last_login_at = NULL "
                     "WHERE id IN (%s, %s)", (viewer, admin))
        conn.execute("INSERT INTO audit_log (at, action) VALUES (now() - interval '400 days', 'old.thing')")
    removed = privacy.purge_old_data(store.pool, audit_days=365, inactive_account_days=730)
    assert removed["accounts"] == 1 and removed["audit_rows"] >= 1
    assert accounts.get_user(store.pool, viewer) is None
    assert accounts.get_user(store.pool, admin) is not None  # admins are never removed this way
    assert accounts.get_user(store.pool, recent) is not None


def test_sources_revision_refreshes_unedited_rows_only(store):
    sources.ensure_seeded(store.pool)
    with store.pool.connection() as conn:
        conn.execute("UPDATE data_sources SET licence = 'old text' WHERE name IN ('Ransomwhere', 'Tranco top sites list')")
        conn.execute("UPDATE data_sources SET updated_by = (SELECT min(id) FROM app_users) WHERE name = 'Tranco top sites list'")
        conn.execute("DELETE FROM app_meta WHERE key LIKE 'sources_revision:%'")
    signup()  # so updated_by above can point at a real user next time
    with store.pool.connection() as conn:
        uid = conn.execute("SELECT id FROM app_users").fetchone()[0]
        conn.execute("UPDATE data_sources SET updated_by = %s WHERE name = 'Tranco top sites list'", (uid,))
    rows = {r["name"]: r for r in sources.list_sources(store.pool)}
    assert "CC BY 4.0" in rows["Ransomwhere"]["licence"]          # refreshed
    assert rows["Tranco top sites list"]["licence"] == "old text"  # an admin edited it: kept
    assert "Google Fonts (Inter, JetBrains Mono)" in rows
