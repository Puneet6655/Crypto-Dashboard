"""Offline tests for the indicator snapshots (download checks, matching) and investigation screening."""
import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.intelligence import snapshots
from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.intelligence.sources import SOURCES, SiteList, SnapshotRejected, clean_site
from app.main import app
from app.providers.bitcoin_mempool import MempoolSpaceProvider
from app.providers.registry import get_bitcoin_provider
from tests.fixtures.mempool_synthetic import SYNTHETIC_TXID
from tests.test_bitcoin_lookup import fake_mempool

LISTED = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"  # an output address of the synthetic transaction
RANSOMWHERE_EXPORT = {"result": [{
    "address": "bc1qransomexampleaddressxxxxxxxxxxxxxxxxxx", "blockchain": "bitcoin", "family": "Locky",
    "createdAt": "2021-07-08T06:43:08.978Z", "updatedAt": "2021-07-08T06:43:08.978Z", "balance": 1,
    "transactions": [{"hash": "c" * 64, "time": 1587839053, "amount": 1}],
}]}


# Small CryptoScamDB-style files (YAML lists like data/urls.yaml in each repository).
SCAM_ETH = "0x4cdc1cba0aeb5539f2e0ba158281e67e0e54a9b1"
SCAM_BTC = "1ScamBtcAddressxxxxxxxxxxxxxxxxx"
CSDB_BLACKLIST = """---
- name: myetherwallet.cam
  url: http://myetherwallet.cam
  category: Phishing
  subcategory: MyEtherWallet
  description: fake wallet login
  addresses:
    ETH:
    - "0x4CDC1CBA0AEB5539F2E0BA158281E67E0E54A9B1"
    BTC:
    - "1ScamBtcAddressxxxxxxxxxxxxxxxxx"
  reporter: CryptoScamDB
- name: medium.com/@fake/eth-giveaway-123
  url: https://medium.com/@fake/eth-giveaway-123
  category: Scamming
  subcategory: Trust-Trading
  reporter: CryptoScamDB
""" + "".join(f"- name: scam{i}.example\n  url: http://scam{i}.example\n  category: Phishing\n" for i in range(120))
CSDB_WHITELIST = """-
    name: MyEtherWallet
    url: 'https://myetherwallet.com'
-
    name: Ethereum
    url: 'https://ethereum.org'
-
    name: Medium
    url: 'https://medium.com'
""" + "".join(f"-\n    name: Site {i}\n    url: 'https://genuine{i}.example'\n" for i in range(10))


# Small copies of the newer website lists, in their real formats.
FILLER = [f"filler{i}.example" for i in range(1000)]
METAMASK_CONFIG = {
    "version": 2, "tolerance": 1, "fuzzylist": ["metamask.io", "myetherwallet.com"],
    "whitelist": ["metamask.io", "opensea.io", "conflict.example"] + [f"ok{i}.example" for i in range(10)],
    "blacklist": ["metamask-login.example", "WWW.Shouty.Example", "sites.google.com/view/fake-wallet/home"] + FILLER,
}
SCAMSNIFFER = (["drainer.example", "conflict.example", "pages.dev", "sites.google.com", "evil.github.io"]
               + [f"p{i}.freesites.example" for i in range(20)] + FILLER)
POLKADOT = {
    "allow": ["*.fleek.co", "github.io", "vercel.app", "pages.dev", "netlify.app", "web.app", "firebaseapp.com",
              "herokuapp.com", "surge.sh", "webflow.io", "weebly.com"],
    "deny": ["polka-scam.example", "drainer.example", "aav\u00e8.com", "trailing-dot.example."] + FILLER,
    "denySub": ["twitter.com/FakeAcala"],
}
PHISHING_DB = "# active phishing\nbank-login.example\n\ndrainer.example\n" + "\n".join(FILLER) + "\n"


def tranco_zip(rows: int = 12_000) -> bytes:
    lines = ["1,google.com", "2,github.io", "3,drainer.example", "4,uniswap.org", "5,www.example.org", "6,freesites.example"]
    lines += [f"{rank},site{rank}.example" for rank in range(7, rows + 1)]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo("top-1m.csv", (2026, 10, 31, 0, 0, 0)), "\r\n".join(lines) + "\r\n")
    return buffer.getvalue()


TRANCO_ZIP = tranco_zip()


def lists_api(xbt: list[str], ransomwhere: dict = RANSOMWHERE_EXPORT,
              scamsniffer: list = SCAMSNIFFER) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url:
            return httpx.Response(200, json=[{"commit": {"committer": {"date": "2026-10-29T07:13:51Z"}}}])
        if url.endswith("sanctioned_addresses_XBT.json"):
            return httpx.Response(200, json=xbt)
        if url.endswith("sanctioned_addresses_ETH.json"):
            return httpx.Response(200, json=["0x8589427373D6D84E98730D7795D8f6f8731FDA16"])
        if url.endswith("sanctioned_addresses_SOL.json"):
            return httpx.Response(200, json=["FuCC7GoYwt5TsNTjWL23Xx9UKCvC18chjMEFPL3vJDCC"])
        if url == SOURCES["ransomwhere"].url:
            return httpx.Response(200, json=ransomwhere)
        if url == SOURCES["cryptoscamdb_blacklist"].url:
            return httpx.Response(200, text=CSDB_BLACKLIST)
        if url == SOURCES["cryptoscamdb_whitelist"].url:
            return httpx.Response(200, text=CSDB_WHITELIST)
        if url == SOURCES["metamask_phishing"].url:
            return httpx.Response(200, json=METAMASK_CONFIG)
        if url == SOURCES["scamsniffer_domains"].url:
            return httpx.Response(200, json=scamsniffer)
        if url == SOURCES["polkadot_phishing"].url:
            return httpx.Response(200, json=POLKADOT)
        if url == SOURCES["phishing_database"].url:
            return httpx.Response(200, text=PHISHING_DB)
        if url == SOURCES["tranco_top"].url:
            return httpx.Response(200, content=TRANCO_ZIP)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def download(folder, xbt, **kwargs):
    with httpx.Client(transport=lists_api(xbt)) as client:
        return {r.source_id: r for r in snapshots.refresh(folder, client=client, **kwargs)}


def test_refresh_saves_snapshot_with_provenance_and_labels(tmp_path):
    results = download(tmp_path, [LISTED, "3Fake"])
    assert {r.status for r in results.values()} == {"updated"}
    meta = snapshots.read_meta(tmp_path, "ofac_xbt")
    assert meta["record_count"] == 2 and meta["list_updated_at"] == "2026-10-29T07:13:51Z"
    assert len(meta["sha256"]) == 64
    labels = (tmp_path / snapshots.LABELS_FILE).read_text(encoding="utf-8").splitlines()
    assert labels[0].startswith("address,source,label,family")
    assert any(line.startswith(f"{LISTED},ofac_xbt,sanctioned") for line in labels)
    assert any(",ransomwhere,ransomware,Locky,2020-04-25" in line for line in labels)
    # Downloading the same files again is reported as unchanged.
    assert download(tmp_path, [LISTED, "3Fake"])["ofac_xbt"].status == "unchanged"


def test_bad_or_shrunken_download_keeps_the_old_snapshot(tmp_path):
    download(tmp_path, [f"1Addr{i}" for i in range(10)])
    with httpx.Client(transport=lists_api({"not": "a list"})) as client:
        assert snapshots.refresh(tmp_path, ["ofac_xbt"], client=client)[0].status == "rejected"
    assert download(tmp_path, ["1Addr0"])["ofac_xbt"].status == "rejected"
    assert snapshots.read_meta(tmp_path, "ofac_xbt")["record_count"] == 10
    assert download(tmp_path, ["1Addr0"], force=True)["ofac_xbt"].status == "updated"


def test_matching_rules(tmp_path):
    download(tmp_path, [LISTED])
    index = IndicatorIndex(tmp_path)
    assert [h.source_id for h in index.lookup(LISTED)] == ["ofac_xbt"]
    assert index.lookup(LISTED.lower()) == []  # Base58 is case-sensitive
    assert index.lookup("BC1QRANSOMEXAMPLEADDRESSXXXXXXXXXXXXXXXXXX")[0].family == "Locky"  # bech32 is not
    assert index.lookup("0x8589427373d6d84e98730d7795d8f6f8731fda16")[0].source_id == "ofac_eth"
    assert index.lookup("1NotListed") == []


def test_investigation_screens_graph_addresses(tmp_path):
    download(tmp_path, [LISTED])
    index = IndicatorIndex(tmp_path)
    app.dependency_overrides[get_bitcoin_provider] = lambda: MempoolSpaceProvider(
        "https://mempool.test/api", "mainnet", 5, transport=httpx.MockTransport(fake_mempool))
    app.dependency_overrides[get_indicator_index] = lambda: index
    try:
        body = TestClient(app).post("/api/investigations", json={
            "mode": "crypto_investigation", "input_type": "bitcoin_txid", "value": SYNTHETIC_TXID,
            "method": "live_lookup",
        }).json()
    finally:
        app.dependency_overrides.pop(get_indicator_index, None)

    rules = {i["rule"]: i for i in body["indicators"]}
    assert rules["ofac_address_in_transaction"]["matched"] is True
    assert rules["ransomwhere_address_in_transaction"]["matched"] is False
    assert "does not mean an address is safe" in rules["ransomwhere_address_in_transaction"]["reason"]
    match = [f for f in body["findings"] if f["kind"] == "rule_match"][0]
    assert match["entity_ids"] == [f"bitcoin:mainnet:address:{LISTED}"]
    evidence = {e["id"]: e for e in body["evidence"]}
    assert evidence[match["evidence_ids"][0]]["source"] == SOURCES["ofac_xbt"].title
    node = next(n for n in body["graph"]["nodes"] if n["id"] == match["entity_ids"][0])
    assert node["details"]["listed_in"] == [SOURCES["ofac_xbt"].title]
    assert body["uncertainty"]["data_sources"] == "mempool.space"


def test_missing_snapshot_means_rule_not_run(tmp_path):
    app.dependency_overrides[get_bitcoin_provider] = lambda: MempoolSpaceProvider(
        "https://mempool.test/api", "mainnet", 5, transport=httpx.MockTransport(fake_mempool))
    app.dependency_overrides[get_indicator_index] = lambda: IndicatorIndex(tmp_path)
    try:
        body = TestClient(app).post("/api/investigations", json={
            "mode": "crypto_investigation", "input_type": "bitcoin_txid", "value": SYNTHETIC_TXID,
            "method": "live_lookup",
        })
    finally:
        app.dependency_overrides.pop(get_indicator_index, None)
    assert body.status_code == 200, body.text
    assert all(i["matched"] is None and i["reason"].startswith("Not run") for i in body.json()["indicators"])


def test_check_route(tmp_path):
    download(tmp_path, [LISTED])
    app.dependency_overrides[get_indicator_index] = lambda: IndicatorIndex(tmp_path)
    try:
        client = TestClient(app)
        hit = client.get(f"/api/indicators/check/{LISTED}").json()
        miss = client.get("/api/indicators/check/1NotListedAddressxxxxxxxxx").json()
        status = client.get("/api/indicators/status").json()
    finally:
        app.dependency_overrides.pop(get_indicator_index, None)
    assert hit["matched"] and hit["hits"][0]["source_id"] == "ofac_xbt"
    assert not miss["matched"] and len(miss["checked"]) == len(SOURCES)
    assert {s["source_id"] for s in status if s["loaded"]} == set(SOURCES)
    json.dumps(status)


def test_cryptoscamdb_lists_are_saved_but_kept_out_of_training_labels(tmp_path):
    results = download(tmp_path, [LISTED])
    assert results["cryptoscamdb_blacklist"].status == "updated"
    assert (tmp_path / "cryptoscamdb_blacklist.yaml").exists()
    meta = snapshots.read_meta(tmp_path, "cryptoscamdb_blacklist")
    assert meta["record_count"] == 122 and "No licence" in meta["licence"]
    labels = (tmp_path / snapshots.LABELS_FILE).read_text(encoding="utf-8")
    assert SCAM_BTC not in labels and "cryptoscamdb" not in labels


def test_site_matching_rules(tmp_path):
    download(tmp_path, [LISTED])
    index = IndicatorIndex(tmp_path)
    black = ["cryptoscamdb_blacklist"]
    hit = index.lookup_site("https://WWW.MyEtherWallet.cam/login", black)[0]
    assert (hit.how, hit.listed, hit.record.family) == ("exact", "myetherwallet.cam", "Phishing / MyEtherWallet")
    assert index.lookup_site("secure.myetherwallet.cam", black)[0].how == "parent"
    # A listed page on a shared host flags that page only, never the whole host.
    assert index.lookup_site("https://medium.com/@fake/eth-giveaway-123/", black)[0].how == "page"
    assert index.lookup_site("https://medium.com/@someone-else/post", black) == []
    assert index.lookup_site("myetherwallet.com", black) == []
    # Addresses reported for a scam site are found by address lookups too, in any letter case.
    assert [h.source_id for h in index.lookup(SCAM_ETH.upper().replace("0X", "0x"))] == ["cryptoscamdb_blacklist"]
    assert "myetherwallet.cam" in index.lookup(SCAM_BTC)[0].family


def run_rules(tmp_path, input_type, value, download_first=True):
    if download_first:
        download(tmp_path, [LISTED])
    app.dependency_overrides[get_indicator_index] = lambda: IndicatorIndex(tmp_path)
    try:
        return TestClient(app).post("/api/investigations", json={
            "mode": "scam_early_warning", "input_type": input_type, "value": value, "method": "rules_v1"})
    finally:
        app.dependency_overrides.pop(get_indicator_index, None)


def test_scam_check_of_a_blacklisted_site(tmp_path):
    response = run_rules(tmp_path, "url_domain", "http://myetherwallet.cam/")
    assert response.status_code == 200, response.text
    body = response.json()
    rules = {i["rule"]: i["matched"] for i in body["indicators"]}
    assert rules == {"cryptoscamdb_site_blacklisted": True, "cryptoscamdb_site_whitelisted": False,
                     "metamask_phishing_listed": False, "scamsniffer_domains_listed": False,
                     "polkadot_phishing_listed": False, "phishing_database_listed": False,
                     "metamask_whitelist_listed": False, "site_on_any_blacklist": True,
                     "known_legitimate_site": False, "shared_hosting_platform": False,
                     "tranco_popular_site": False, "lookalike_of_whitelisted_site": True,
                     "punycode_hostname": False}
    assert body["data_mode"] == "recorded" and body["input"]["value"] == "myetherwallet.cam"
    nodes = {n["id"]: n for n in body["graph"]["nodes"]}
    assert nodes["web:domain:myetherwallet.cam"]["details"]["category"] == "Phishing / MyEtherWallet"
    assert f"ethereum:mainnet:address:{SCAM_ETH}" in nodes and "web:domain:myetherwallet.com" in nodes
    assert {e["relation"] for e in body["graph"]["edges"]} == {"reported", "inferred"}
    assert any("2 payment addresses" in f["title"] for f in body["findings"])
    assert any("mid-2022" in item for item in body["limitations"])


def test_scam_check_of_genuine_and_unknown_sites(tmp_path):
    genuine = run_rules(tmp_path, "url_domain", "https://ethereum.org/en/").json()
    rules = {i["rule"]: i["matched"] for i in genuine["indicators"]}
    assert rules["cryptoscamdb_site_blacklisted"] is False and rules["cryptoscamdb_site_whitelisted"] is True
    assert rules["lookalike_of_whitelisted_site"] is False
    unknown = run_rules(tmp_path, "url_domain", "xn--myetherwallt-leb.com", download_first=False).json()
    rules = {i["rule"]: i for i in unknown["indicators"]}
    assert rules["cryptoscamdb_site_blacklisted"]["matched"] is False
    assert "does not mean a website is safe" in rules["cryptoscamdb_site_blacklisted"]["reason"]
    assert rules["lookalike_of_whitelisted_site"]["matched"] is True
    assert rules["punycode_hostname"]["matched"] is True
    plain = run_rules(tmp_path, "url_domain", "harmless-bakery.example", download_first=False).json()
    assert not any(i["matched"] for i in plain["indicators"])


def test_scam_check_without_lists_and_bad_input(tmp_path):
    body = run_rules(tmp_path, "url_domain", "example.com", download_first=False).json()
    not_run = [i for i in body["indicators"] if i["rule"] not in ("punycode_hostname", "shared_hosting_platform")]
    assert all(i["matched"] is None and i["reason"].startswith("Not run") for i in not_run)
    assert run_rules(tmp_path, "url_domain", "not a website", download_first=False).status_code == 422


def test_rules_check_of_a_bitcoin_address(tmp_path):
    body = run_rules(tmp_path, "bitcoin_address", LISTED).json()
    rules = {i["rule"]: i["matched"] for i in body["indicators"]}
    assert rules == {"ofac_exact_match": True, "ransomwhere_exact_match": False, "cryptoscamdb_exact_match": False}
    assert body["graph"]["nodes"][0]["details"]["listed_in"] == [SOURCES["ofac_xbt"].title]


# --- Newer website lists: MetaMask, ScamSniffer, Polkadot{.js}, Phishing.Database, Tranco ---

def test_website_list_parsers_read_each_format():
    metamask = SOURCES["metamask_phishing"].parse(METAMASK_CONFIG)
    assert isinstance(metamask, SiteList) and "shouty.example" in metamask.hosts
    assert metamask.pages == {"sites.google.com": ("/view/fake-wallet/home",)}
    assert "sites.google.com" not in metamask.hosts  # one listed page never lists the whole host
    whitelist = SOURCES["metamask_whitelist"].parse(METAMASK_CONFIG)
    assert "opensea.io" in whitelist.hosts and whitelist.lookalike_targets == ("metamask.io", "myetherwallet.com")
    assert len(SOURCES["scamsniffer_domains"].parse(SCAMSNIFFER)) == 1025
    polkadot = SOURCES["polkadot_phishing"].parse(POLKADOT)
    assert {"xn--aav-8la.com", "trailing-dot.example"} <= polkadot.hosts  # IDN to punycode, trailing dot dropped
    assert polkadot.pages == {"twitter.com": ("/FakeAcala",)}
    assert "fleek.co" in SOURCES["polkadot_allowlist"].parse(POLKADOT).hosts  # "*.fleek.co"
    phishing = SOURCES["phishing_database"].parse(SOURCES["phishing_database"].decode(PHISHING_DB.encode()))
    assert "bank-login.example" in phishing.hosts and len(phishing) == 1002  # comment and blank line skipped
    tranco = SOURCES["tranco_top"].parse(SOURCES["tranco_top"].decode(TRANCO_ZIP))
    assert tranco.ranks["google.com"] == 1 and tranco.ranks["example.org"] == 5
    assert clean_site("https://WWW.Example.com/Path/") == ("example.com", "/Path")


def test_website_parsers_reject_the_wrong_shape():
    for source_id, bad in [("metamask_phishing", {"blacklist": FILLER}), ("scamsniffer_domains", {"a": 1}),
                           ("scamsniffer_domains", ["only.example"]), ("polkadot_phishing", ["x"]),
                           ("phishing_database", "one.example\n"), ("tranco_top", b"rank,domain\n")]:
        with pytest.raises(SnapshotRejected):
            SOURCES[source_id].parse(bad)
    with pytest.raises(ValueError):
        SOURCES["tranco_top"].decode(b"not a zip")
    with pytest.raises(SnapshotRejected):  # a zip with far too few rows
        SOURCES["tranco_top"].parse(SOURCES["tranco_top"].decode(tranco_zip(200)))


def test_website_lists_download_once_per_file_and_stay_out_of_training(tmp_path):
    requests = []

    def counting(request):
        requests.append(str(request.url))
        return lists_api([LISTED]).handle_request(request)

    with httpx.Client(transport=httpx.MockTransport(counting)) as client:
        results = {r.source_id: r for r in snapshots.refresh(tmp_path, client=client)}
    assert all(r.status == "updated" for r in results.values()), results
    assert requests.count(SOURCES["metamask_phishing"].url) == 1  # shared by the blacklist and whitelist
    assert requests.count(SOURCES["polkadot_phishing"].url) == 1
    assert (tmp_path / "phishing_database.txt").exists() and (tmp_path / "tranco_top.zip").exists()
    meta = snapshots.read_meta(tmp_path, "tranco_top")
    assert meta["record_count"] == 12_000 and "non-commercial" in meta["licence"]
    assert snapshots.read_meta(tmp_path, "scamsniffer_domains")["licence"].startswith("GPL-3.0")
    assert not any(s.use_for_training for s in SOURCES.values() if s.chain == "web")
    labels = (tmp_path / snapshots.LABELS_FILE).read_text(encoding="utf-8")
    assert "drainer" not in labels and "metamask" not in labels


def test_shrunken_website_list_keeps_the_old_snapshot(tmp_path):
    download(tmp_path, [LISTED])
    with httpx.Client(transport=lists_api([LISTED], scamsniffer=FILLER[:400] * 3)) as client:
        assert snapshots.refresh(tmp_path, ["scamsniffer_domains"], client=client)[0].status == "rejected"
    smaller = [f"other{i}.example" for i in range(1001)]
    with httpx.Client(transport=lists_api([LISTED], scamsniffer=smaller)) as client:
        assert snapshots.refresh(tmp_path, ["scamsniffer_domains"], client=client)[0].status == "updated"
    assert snapshots.read_meta(tmp_path, "scamsniffer_domains")["record_count"] == 1001


def test_parent_domains_pages_and_shared_hosts(tmp_path):
    download(tmp_path, [LISTED])
    index = IndicatorIndex(tmp_path)
    hits = {h.source_id: h for h in index.lookup_site("https://a.b.Drainer.example/claim")}
    assert {"scamsniffer_domains", "polkadot_phishing", "phishing_database"} <= set(hits)
    assert hits["scamsniffer_domains"].how == "parent" and hits["scamsniffer_domains"].listed == "drainer.example"
    assert index.lookup_site("www.shouty.example", ["metamask_phishing"])[0].how == "exact"
    assert index.lookup_site("aav\u00e8.com", ["polkadot_phishing"])[0].listed == "xn--aav-8la.com"
    # A page listed on a shared host matches that page only.
    page = index.lookup_site("https://sites.google.com/view/fake-wallet/home?x=1", ["metamask_phishing"])
    assert page and page[0].how == "page"
    assert index.lookup_site("https://sites.google.com/view/my-bakery", ["metamask_phishing"]) == []
    # ScamSniffer lists the platforms pages.dev and sites.google.com themselves: ignored, or every
    # site on them would be flagged.
    assert index.lookup_site("pages.dev", ["scamsniffer_domains"]) == []
    assert index.lookup_site("my-bakery.pages.dev", ["scamsniffer_domains"]) == []
    assert index.lookup_site("https://sites.google.com/view/my-bakery", ["scamsniffer_domains"]) == []
    # 20 blacklisted subdomains make freesites.example a likely shared platform; its own listing would still count.
    assert index.shared_platform("mine.freesites.example") == "freesites.example"
    assert index.shared_platform("freesites.example") is None
    assert index.shared_platform("x.filler1.example") is None  # not popular, so not a platform
    assert index.lookup_site("x.evil.github.io", ["scamsniffer_domains"])[0].listed == "evil.github.io"
    assert index.shared_platform("evil.github.io") == "github.io" and index.shared_platform("fleek.co") == "fleek.co"
    assert index.shared_platform("uniswap.org") is None


def rules(body):
    return {i["rule"]: i for i in body["indicators"]}


def test_scam_check_reports_every_list_with_its_licence(tmp_path):
    body = run_rules(tmp_path, "url_domain", "https://login.drainer.example/").json()
    found = rules(body)
    assert found["scamsniffer_domains_listed"]["matched"] and "GPL-3.0" in found["scamsniffer_domains_listed"]["reason"]
    assert found["polkadot_phishing_listed"]["matched"] and "parent domain drainer.example" in found["polkadot_phishing_listed"]["reason"]
    assert "weighted lower" in found["phishing_database_listed"]["reason"]
    assert found["site_on_any_blacklist"]["matched"] and "3 of" in found["site_on_any_blacklist"]["reason"]
    # Tranco ranks drainer.example 3rd, but the blacklists win.
    assert found["tranco_popular_site"]["matched"] and "blacklist match wins" in found["tranco_popular_site"]["reason"]
    assert not any("Popular website" in f["title"] for f in body["findings"])
    assert any(f["title"].startswith("Website is on 3 of 5 scam and phishing lists") for f in body["findings"])
    node = body["graph"]["nodes"][0]["details"]
    assert len(node["blacklisted_by"]) == 3 and any("Apache-2.0" in line for line in node["blacklisted_by"])
    assert body["uncertainty"]["blacklisted"] is True
    assert any("Licence: GPL-3.0" in e["excerpt"] for e in body["evidence"])


def test_blacklist_beats_whitelist_and_known_sites(tmp_path):
    conflict = run_rules(tmp_path, "url_domain", "conflict.example").json()
    found = rules(conflict)
    assert found["metamask_whitelist_listed"]["matched"] and found["scamsniffer_domains_listed"]["matched"]
    assert found["known_legitimate_site"]["matched"] is False
    assert any("blacklist match wins" in f["title"] for f in conflict["findings"])
    assert conflict["graph"]["nodes"][0]["details"]["allowlist_overruled"]
    genuine = run_rules(tmp_path, "url_domain", "https://metamask.io", download_first=False).json()
    found = rules(genuine)
    assert found["known_legitimate_site"]["matched"] is True and found["site_on_any_blacklist"]["matched"] is False
    assert any(f["title"].startswith("Reviewed site") for f in genuine["findings"])
    assert found["lookalike_of_whitelisted_site"]["matched"] is False
    csdb = rules(run_rules(tmp_path, "url_domain", "ethereum.org", download_first=False).json())
    assert csdb["known_legitimate_site"]["matched"] is True
    # A look-alike of a MetaMask look-alike target.
    fake = rules(run_rules(tmp_path, "url_domain", "metamasks.io", download_first=False).json())
    assert fake["lookalike_of_whitelisted_site"]["matched"] is True


def test_tranco_is_context_only_and_ignores_shared_hosts(tmp_path):
    popular = run_rules(tmp_path, "url_domain", "uniswap.org").json()
    found = rules(popular)
    assert found["tranco_popular_site"]["matched"] is True and found["known_legitimate_site"]["matched"] is False
    assert any("Tranco rank 4" in f["title"] and "not proof" in f["title"] for f in popular["findings"])
    assert popular["graph"]["nodes"][0]["details"]["tranco_rank"] == 4
    sub = rules(run_rules(tmp_path, "url_domain", "mail.google.com", download_first=False).json())
    assert sub["tranco_popular_site"]["matched"] is True and "parent domain google.com" in sub["tranco_popular_site"]["reason"]
    # github.io is ranked 2nd, but a site published on it gets no credit for that.
    hosted = run_rules(tmp_path, "url_domain", "someone.github.io", download_first=False).json()
    found = rules(hosted)
    assert found["shared_hosting_platform"]["matched"] is True
    assert found["tranco_popular_site"]["matched"] is False
    assert hosted["graph"]["nodes"][0]["details"]["shared_platform"] == "github.io"
    google_site = rules(run_rules(tmp_path, "url_domain", "sites.google.com/view/x", download_first=False).json())
    assert google_site["tranco_popular_site"]["matched"] is False
    assert google_site["site_on_any_blacklist"]["matched"] is False
    builder = rules(run_rules(tmp_path, "url_domain", "my-shop.freesites.example", download_first=False).json())
    assert builder["tranco_popular_site"]["matched"] is False and "likely" in builder["shared_hosting_platform"]["reason"]
