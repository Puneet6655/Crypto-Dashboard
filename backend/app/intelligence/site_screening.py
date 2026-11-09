"""Website checks for Scam & Early Warning, against every downloaded website list, plus two simple
warning signs (a name that imitates a genuine site, and international characters).

* Blacklists (CryptoScamDB, MetaMask, ScamSniffer, Polkadot{.js}, and the general
  Phishing.Database, weighted lower): each match is reported with the list's title and licence.
* Allowlists (CryptoScamDB whitelist, MetaMask whitelist): a site on one and on no blacklist is
  reported as known or reviewed. A blacklist match always wins over an allowlist.
* Shared hosting platforms (github.io, vercel.app...; Polkadot{.js} allow list plus our own):
  reported so the analyst knows the platform's reputation says nothing about the site.
* Tranco top 100,000: popularity as context only, never proof of safety. Ignored for sites on
  shared platforms, and a blacklist match wins.

Matching ignores "www." and the scheme, and covers the host, its parent domains (except shared
platforms) and single listed pages such as sites.google.com/view/fake-wallet.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from app.intelligence.index import IndicatorIndex, SiteHit
from app.intelligence.screening import METHOD_VERSION
from app.intelligence.sources import ALLOW, BLOCK, SOURCES
from app.schemas.investigation import Evidence, Finding, Indicator

BLACKLIST = "cryptoscamdb_blacklist"
WHITELIST = "cryptoscamdb_whitelist"
METAMASK_WHITELIST = "metamask_whitelist"
TRANCO = "tranco_top"
POLKADOT_ALLOW = "polkadot_allowlist"
# The newer lists, checked after CryptoScamDB in this order.
OTHER_SITE_LISTS = ("metamask_phishing", "scamsniffer_domains", "polkadot_phishing", "phishing_database",
                    METAMASK_WHITELIST)
NOT_RUN = "Not run: the {title} snapshot has not been downloaded yet (run: python -m app.intelligence refresh)"
NOT_SAFE_SITE = "Not being listed does not mean a website is safe."
# Shortest whitelisted name we compare against; shorter ones ("eos", "bity") match too much.
MIN_NAME = 5


def site_node_id(host: str) -> str:
    return f"web:domain:{host}"


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, giving up (returning limit + 1) once it must exceed limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _plain(host: str) -> str:
    """The host as a reader sees it, with accents stripped: xn--myetherwallt-leb.com -> myetherwallet.com."""
    try:
        shown = host.encode("ascii").decode("idna")
    except UnicodeError:
        shown = host
    return unicodedata.normalize("NFKD", shown).encode("ascii", "ignore").decode("ascii").lower()


def _name(host: str) -> str:
    """The host without its top-level domain: 'myetherwallet.github.io' -> 'myetherwallet.github'."""
    return host.rsplit(".", 1)[0]


def lookalike_of(host: str, genuine_hosts: list[str]) -> tuple[str, str] | None:
    """(genuine host, why) if `host` imitates one of `genuine_hosts`, else None."""
    mine = _name(_plain(host))
    for genuine in genuine_hosts:
        theirs = _name(genuine)
        if len(theirs) < MIN_NAME or genuine == host:
            continue
        if mine == theirs:
            return genuine, f"same name as {genuine} with a different ending or characters"
        # Short or common words ("ether", "status") would match many genuine names, so typo and
        # contains checks need longer names: 7+ letters for one typo, 11+ for two, 8+ to be contained.
        limit = 0 if len(theirs) < 7 else 1 if len(theirs) < 11 else 2
        if limit and _edit_distance(mine, theirs, limit) <= limit:
            return genuine, f"spelled within {limit} letter{'s' if limit > 1 else ''} of {genuine}"
        if "." not in theirs and len(theirs) >= 8 and theirs in mine:
            return genuine, f"contains the name of {genuine}"
    return None


def list_match_label(hit: SiteHit) -> str:
    """One line per list match for the graph details: 'MetaMask phishing blacklist (licence: ...)'."""
    source = SOURCES[hit.source_id]
    where = "" if hit.how == "exact" else f", as {hit.listed}"
    return f"{source.title}{where} (licence: {source.licence})"


@dataclass
class SiteScreening:
    host: str
    indicators: list[Indicator] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    source_timestamps: dict = field(default_factory=dict)
    blacklist_hit: SiteHit | None = None  # CryptoScamDB, which also reports payment addresses
    whitelist_hit: SiteHit | None = None  # CryptoScamDB
    block_hits: list[SiteHit] = field(default_factory=list)  # every blacklist that matched
    allow_hits: list[SiteHit] = field(default_factory=list)  # every allowlist that matched
    popular_hit: SiteHit | None = None  # Tranco, only when it counts (not a shared platform)
    shared_platform: str | None = None
    lookalike: tuple[str, str] | None = None
    lookalike_source: str | None = None  # the list the genuine name came from
    lists_checked: int = 0
    # evidence id per list, so the graph edges can cite it
    evidence_ids: dict[str, str] = field(default_factory=dict)

    @property
    def known_legitimate(self) -> bool:
        return bool(self.allow_hits) and not self.block_hits


def _how(hit: SiteHit) -> str:
    return {"exact": "", "parent": f" (its parent domain {hit.listed} is listed)",
            "page": f" (the page {hit.listed} is listed)"}[hit.how]


def screen_site(index: IndicatorIndex, host: str, value: str, first_finding: int = 1,
                first_evidence: int = 1) -> SiteScreening:
    """Check one website (host already normalised; value is the URL as typed, for page matches)."""
    out = SiteScreening(host=host)
    node = site_node_id(host)
    finding_no, evidence_no = first_finding, first_evidence

    def add_evidence(source_id: str) -> str:
        nonlocal evidence_no
        loaded = index.loaded(source_id)
        evidence_id = f"e{evidence_no}"
        evidence_no += 1
        parts = [loaded.snapshot_label(), f"sha256 {str(loaded.meta.get('sha256'))[:12]}"]
        if loaded.list_changed_label():
            parts.append(loaded.list_changed_label())
        excerpt = "; ".join(parts) + f". {loaded.source.note} Licence: {loaded.source.licence}."
        out.evidence.append(Evidence(id=evidence_id, source=loaded.source.title, source_url=loaded.source.url,
                                     retrieved_at=loaded.retrieved_at, method_version=METHOD_VERSION,
                                     excerpt=excerpt))
        out.source_timestamps[loaded.source.title] = loaded.retrieved_at
        out.evidence_ids[source_id] = evidence_id
        out.lists_checked += 1
        return evidence_id

    def add_finding(title: str, kind: str, entity_ids: list[str], evidence_ids: list[str]) -> None:
        nonlocal finding_no
        out.findings.append(Finding(id=f"f{finding_no}", title=title, kind=kind,
                                    entity_ids=entity_ids, evidence_ids=evidence_ids))
        finding_no += 1

    def indicator(rule: str, matched: bool | None, reason: str) -> None:
        out.indicators.append(Indicator(id="", rule=rule, matched=matched, reason=reason))

    # 1. CryptoScamDB blacklist (its own wording: it is old, and it reports payment addresses)
    black = index.loaded(BLACKLIST)
    if black is None:
        indicator("cryptoscamdb_site_blacklisted", None, NOT_RUN.format(title=SOURCES[BLACKLIST].title))
    else:
        evidence_id = add_evidence(BLACKLIST)
        hits = index.lookup_site(value, [BLACKLIST])
        snapshot = f"{black.source.title} {black.snapshot_label()}"
        if hits:
            hit = out.blacklist_hit = hits[0]
            out.block_hits.append(hit)
            category = f": {hit.record.family}" if hit.record.family else ""
            add_finding(f"Website is on the CryptoScamDB scam blacklist{category}{_how(hit)}", "rule_match",
                        [node], [evidence_id])
            reason = f"{hit.listed} is in the {snapshot}{category}."
            if hit.record.description:
                reason += f' Reporter\'s note: "{hit.record.description[:200]}"'
            indicator("cryptoscamdb_site_blacklisted", True, reason + f" Licence: {black.source.licence}.")
        else:
            indicator("cryptoscamdb_site_blacklisted", False,
                      f"Not in the {snapshot}. The list stopped updating in 2022. {NOT_SAFE_SITE}")

    # 2. CryptoScamDB whitelist
    white = index.loaded(WHITELIST)
    if white is None:
        indicator("cryptoscamdb_site_whitelisted", None, NOT_RUN.format(title=SOURCES[WHITELIST].title))
    else:
        evidence_id = add_evidence(WHITELIST)
        hits = index.lookup_site(value, [WHITELIST])
        snapshot = f"{white.source.title} {white.snapshot_label()}"
        if hits:
            hit = out.whitelist_hit = hits[0]
            out.allow_hits.append(hit)
            if hit.how == "parent":
                where = f"Its parent domain {hit.listed}"
            else:
                where = hit.listed
            indicator("cryptoscamdb_site_whitelisted", True,
                      f"{where} is in the {snapshot}. The list dates from 2021; check the site is still genuine.")
        else:
            indicator("cryptoscamdb_site_whitelisted", False, f"Not in the {snapshot}.")

    # 3. The newer blacklists and the MetaMask whitelist, one indicator each
    for source_id in OTHER_SITE_LISTS:
        source = SOURCES[source_id]
        rule = f"{source_id}_listed"
        loaded = index.loaded(source_id)
        if loaded is None:
            indicator(rule, None, NOT_RUN.format(title=source.title))
            continue
        evidence_id = add_evidence(source_id)
        hits = index.lookup_site(value, [source_id])
        snapshot = f"{source.title} {loaded.snapshot_label()}"
        if not hits:
            extra = f" {NOT_SAFE_SITE}" if source.site_role == BLOCK else ""
            indicator(rule, False, f"Not in the {snapshot}.{extra}")
            continue
        hit = hits[0]
        if source.site_role == ALLOW:
            out.allow_hits.append(hit)
            indicator(rule, True, f"{hit.listed} is in the {snapshot}{_how(hit)}. {source.note} "
                                  f"Licence: {source.licence}.")
            continue
        out.block_hits.append(hit)
        weight = "" if source.crypto_specific else " (a general phishing list, not crypto-specific: weighted lower)"
        add_finding(f"Website is {source.label}{_how(hit)}{weight}", "rule_match", [node], [evidence_id])
        indicator(rule, True, f"{hit.listed} is in the {snapshot}{_how(hit)}.{weight} Licence: {source.licence}.")

    # 4. Overall: which blacklists matched, and a known or reviewed site (blacklists win)
    block_ids = [s.id for s in SOURCES.values() if s.site_role == BLOCK]
    allow_ids = [s.id for s in SOURCES.values() if s.site_role == ALLOW]
    blocks_loaded = [i for i in block_ids if index.loaded(i)]
    if out.block_hits:
        crypto = [h for h in out.block_hits if SOURCES[h.source_id].crypto_specific]
        names = ", ".join(h.title for h in out.block_hits)
        if len(out.block_hits) > 1:
            add_finding(f"Website is on {len(out.block_hits)} of {len(blocks_loaded)} scam and phishing lists: {names}",
                        "rule_match", [node], [out.evidence_ids[h.source_id] for h in out.block_hits])
        strength = ("" if crypto else
                    " Only the general Phishing.Database matched, which has some false positives.")
        indicator("site_on_any_blacklist", True,
                  f"Listed by {len(out.block_hits)} of {len(blocks_loaded)} blacklists checked: {names}.{strength}")
    elif blocks_loaded:
        indicator("site_on_any_blacklist", False,
                  f"On none of the {len(blocks_loaded)} blacklists checked. {NOT_SAFE_SITE}")
    else:
        indicator("site_on_any_blacklist", None, "Not run: no blacklist has been downloaded yet.")

    allow_names = ", ".join(h.title for h in out.allow_hits)
    allow_evidence = [out.evidence_ids[h.source_id] for h in out.allow_hits]
    if out.known_legitimate:
        if out.whitelist_hit:
            title = f"Known legitimate site: on the {allow_names} and on no blacklist"
            if out.whitelist_hit.how == "parent":
                title += f" (via its parent domain {out.whitelist_hit.listed})"
        else:
            title = f"Reviewed site: on the {allow_names} (exempted from its look-alike check) and on no blacklist"
        add_finding(title, "rule_match", [node], allow_evidence)
        indicator("known_legitimate_site", True,
                  f"On {allow_names} and on none of the {len(blocks_loaded)} blacklists. Allowlists can be "
                  "out of date, and the site itself was not visited.")
    elif out.allow_hits:
        add_finding(f"Website is on the {allow_names} but also on a blacklist; the blacklist match wins",
                    "observation", [node], allow_evidence)
        indicator("known_legitimate_site", False,
                  f"On {allow_names}, but blacklisted by {', '.join(h.title for h in out.block_hits)}. "
                  "A blacklist match always wins over an allowlist.")
    elif any(index.loaded(i) for i in allow_ids):
        indicator("known_legitimate_site", False, "On no allowlist of genuine or reviewed sites.")
    else:
        indicator("known_legitimate_site", None, "Not run: no allowlist has been downloaded yet.")

    # 5. Shared hosting platform (anyone can publish there, so the platform vouches for nothing)
    out.shared_platform = index.shared_platform(host)
    if index.loaded(POLKADOT_ALLOW):
        add_evidence(POLKADOT_ALLOW)
    if out.shared_platform:
        busy = index.busy_parents.get(out.shared_platform) if out.shared_platform not in index.shared_hosts else None
        likely = f" (likely: {busy:,} blacklisted sites are published on it)" if busy else ""
        add_finding(f"Website is on the shared platform {out.shared_platform}{likely}, where anyone can publish a site",
                    "observation", [node], [out.evidence_ids[POLKADOT_ALLOW]] if POLKADOT_ALLOW in out.evidence_ids else [])
        indicator("shared_hosting_platform", True,
                  f"{host} is {'' if out.shared_platform == host else 'published on '}{out.shared_platform}{likely}. "
                  "Listings and popularity of the platform do not count for this site.")
    else:
        indicator("shared_hosting_platform", False, "Not on a known shared hosting platform.")

    # 6. Tranco popularity: context only, never proof of safety
    tranco = index.loaded(TRANCO)
    if tranco is None:
        indicator("tranco_popular_site", None, NOT_RUN.format(title=SOURCES[TRANCO].title))
    else:
        evidence_id = add_evidence(TRANCO)
        hits = index.lookup_site(value, [TRANCO])
        hit = hits[0] if hits and hits[0].how != "page" else None
        if hit is None:
            indicator("tranco_popular_site", False, f"Not among the {SOURCES[TRANCO].title.split(' ', 1)[1]}.")
        elif out.shared_platform:
            indicator("tranco_popular_site", False,
                      f"{hit.listed} is ranked {hit.rank:,} by Tranco, but it is the shared platform "
                      f"{out.shared_platform}: its popularity says nothing about this site.")
        else:
            out.popular_hit = hit
            where = "" if hit.how == "exact" else f" (its parent domain {hit.listed}; {host} itself is not ranked)"
            if out.block_hits:
                indicator("tranco_popular_site", True,
                          f"Ranked {hit.rank:,} by Tranco{where}, but blacklisted: the blacklist match wins. "
                          f"Licence: {tranco.source.licence}.")
            else:
                add_finding(f"Popular website{where}: Tranco rank {hit.rank:,}. Context only, not proof it is safe",
                            "observation", [node], [evidence_id])
                indicator("tranco_popular_site", True,
                          f"Ranked {hit.rank:,} of the top 100,000 by Tranco{where}. Popular sites can be "
                          f"compromised or host scams. Licence: {tranco.source.licence}.")

    # 7. Look-alike of a genuine site (CryptoScamDB whitelist names and MetaMask's look-alike targets)
    targets: list[tuple[str, str]] = []
    if white is not None:
        targets += [(name, WHITELIST) for name in white.records]
    metamask = index.loaded(METAMASK_WHITELIST)
    if metamask is not None and metamask.site_list is not None:
        targets += [(name, METAMASK_WHITELIST) for name in metamask.site_list.lookalike_targets]
    if not targets:
        indicator("lookalike_of_whitelisted_site", None, NOT_RUN.format(title=SOURCES[WHITELIST].title))
    elif out.allow_hits:
        indicator("lookalike_of_whitelisted_site", False, "Not run as a warning: the site itself is on an allowlist.")
    else:
        names = list(dict.fromkeys(name for name, _ in targets))
        out.lookalike = lookalike_of(host, names)
        if out.lookalike:
            genuine, why = out.lookalike
            out.lookalike_source = next(src for name, src in targets if name == genuine)
            add_finding(f"Website name imitates the genuine site {genuine}", "observation",
                        [node, site_node_id(genuine)], [out.evidence_ids[out.lookalike_source]])
            indicator("lookalike_of_whitelisted_site", True,
                      f"{host}: {why}. Look-alike names are a common phishing trick, "
                      "but some genuine sites match too, so treat this as a warning sign.")
        else:
            indicator("lookalike_of_whitelisted_site", False,
                      f"Not close to any of the {len(names)} genuine names compared. {NOT_SAFE_SITE}")

    # 8. International characters (punycode), which can hide look-alike letters
    if any(label.startswith("xn--") for label in host.split(".")):
        add_finding(f"Website name uses international characters (shown as {_plain(host)})", "observation",
                    [node], [])
        indicator("punycode_hostname", True,
                  f"{host} displays as {_plain(host)} after removing accents; such names can imitate real sites.")
    else:
        indicator("punycode_hostname", False, "Plain ASCII host name.")

    for number, item in enumerate(out.indicators, start=1):
        item.id = f"i{number}"
    return out
