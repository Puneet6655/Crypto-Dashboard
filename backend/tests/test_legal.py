"""Legal and account rules that need no database: age check, user types, public legal info, sources list."""
from datetime import date

from fastapi.testclient import TestClient

from app import legal
from app.main import app
from app.workspace import sources

client = TestClient(app)


def test_minimum_age_is_eighteen_on_the_birthday():
    today = date(2026, 11, 14)
    assert legal.age_problem(date(2008, 11, 14), today) is None           # 18 today
    assert legal.age_problem(date(2008, 11, 15), today) is not None       # 18 tomorrow
    assert "18" in legal.age_problem(date(2015, 1, 1), today)


def test_leap_day_birthday_counts_from_first_of_march():
    assert legal.age_on(date(2008, 2, 29), date(2026, 2, 28)) == 17
    assert legal.age_on(date(2008, 2, 29), date(2026, 3, 1)) == 18


def test_impossible_dates_of_birth_are_refused():
    today = date(2026, 11, 14)
    assert legal.age_problem(None, today) is not None
    assert legal.age_problem(date(2027, 1, 1), today) is not None
    assert legal.age_problem(date(1890, 1, 1), today) is not None


def test_user_types():
    for kind in ("researcher", "student", "organisation", "law_enforcement", "other"):
        assert legal.user_type_problem(kind) is None
    assert legal.user_type_problem("admin") is not None
    assert legal.user_type_problem(None) is not None
    assert legal.clean_organisation("organisation", "  Acme Ltd  ") == "Acme Ltd"
    assert legal.clean_organisation("student", "Acme Ltd") is None  # only asked of some types
    assert legal.clean_organisation("law_enforcement", "") is None


def test_legal_info_is_public(signed_out, monkeypatch):
    monkeypatch.setenv("LEGAL_OPERATOR_NAME", "Test Operator")
    monkeypatch.setenv("LEGAL_CONTACT_EMAIL", "privacy@example.test")
    monkeypatch.setenv("INACTIVE_ACCOUNT_DAYS", "365")
    body = client.get("/api/auth/legal").json()
    assert body["operator_name"] == "Test Operator"
    assert body["configured"] is True
    assert body["min_age"] == 18
    assert body["terms_version"] == legal.TERMS_VERSION
    assert body["retention"]["inactive_account_days"] == 365


def test_legal_info_says_when_operator_is_not_set(signed_out, monkeypatch):
    monkeypatch.delenv("LEGAL_OPERATOR_NAME", raising=False)
    monkeypatch.delenv("LEGAL_CONTACT_EMAIL", raising=False)
    assert client.get("/api/auth/legal").json()["configured"] is False


def test_signup_config_lists_age_and_user_types(signed_out):
    config = client.get("/api/auth/config").json()
    assert config["min_age"] == 18
    assert set(config["user_types"]) == set(legal.USER_TYPES)


def test_own_data_routes_need_sign_in(signed_out):
    assert client.get("/api/auth/export").status_code in (401, 503)
    response = client.post("/api/auth/delete-account", json={"password": "x", "confirm": "DELETE"})
    assert response.status_code in (401, 503)
    assert client.post("/api/auth/profile", json={"accept_terms": True}).status_code in (401, 503)


def test_admin_delete_user_is_admin_only(signed_in_as):
    signed_in_as("analyst")
    assert client.delete("/api/admin/users/5").status_code == 403


def test_every_source_has_licence_and_link():
    for source in sources.DEFAULT_SOURCES:
        assert source["licence"].strip(), source["name"]
        assert source["url"].startswith("https://"), source["name"]
        assert source["category"] in sources.CATEGORIES, source["name"]
        assert len(source["licence"]) <= 500, source["name"]  # the edit form's limit


def test_sources_added_later_are_all_defaults():
    names = {s["name"] for s in sources.DEFAULT_SOURCES}
    assert len(names) == len(sources.DEFAULT_SOURCES)
    assert set(sources.ADDED_LATER) <= names


def test_sources_carry_the_licence_terms_we_rely_on():
    by_name = {s["name"]: s for s in sources.DEFAULT_SOURCES}
    assert "Powered by Etherscan.io APIs" in by_name["Etherscan API V2"]["licence"]
    assert "no ML training" in by_name["Etherscan API V2"]["licence"]
    assert "CC BY 4.0" in by_name["Ransomwhere"]["licence"]
    assert "Business Source License" in by_name["Dune cex.addresses"]["licence"]
    assert "CC BY-NC-ND 4.0" in by_name["Elliptic2 dataset"]["licence"]
    assert "Weber" in by_name["Elliptic++ dataset"]["citation"]  # the original Elliptic Data Set is credited
    for name in ("mempool.space REST API", "Blockscout PRO API", "Alchemy API", "CryptoScamDB",
                 "MetaMask eth-phishing-detect", "ScamSniffer scam database", "Polkadot{.js} phishing list",
                 "Phishing.Database", "Tranco top sites list", "Google BigQuery public dataset crypto_bitcoin",
                 "Google Fonts (Inter, JetBrains Mono)"):
        assert name in by_name, name
