"""Command line for rule-pack authors.

    python -m app.redflags facts                          list every fact with its unit and meaning
    python -m app.redflags list                           the packs found and any that failed to load
    python -m app.redflags validate PACK.yaml             check a pack file
    python -m app.redflags validate PACK.yaml --facts F   ...and show what each rule says for the facts
                                                          in the JSON file F, e.g. {"tx_count": 40}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.redflags.engine import evaluate, summarise
from app.redflags.facts import CATALOG
from app.redflags.packs import PackError, PackRegistry, parse_pack


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.redflags")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("facts")
    sub.add_parser("list")
    check = sub.add_parser("validate")
    check.add_argument("pack")
    check.add_argument("--facts")
    args = parser.parse_args(argv)

    if args.command == "facts":
        for f in CATALOG.values():
            print(f"{f.name:<38} {f.unit:<14} {f.description}")
        return 0
    if args.command == "list":
        registry = PackRegistry()
        for p in registry.packs.values():
            s = registry.setting(p.pack.id)
            print(f"{p.pack.id:<34} {p.origin:<9} {'on ' if s['enabled'] else 'off'} {len(p.pack.rules):>3} rules  {p.pack.title}")
        for e in registry.errors:
            print(f"FAILED {e['file']} ({e['origin']}): {e['error']}")
        return 1 if registry.errors else 0

    path = Path(args.pack)
    try:
        pack = parse_pack(path.read_text(encoding="utf-8"), path.name)
    except PackError as exc:
        print("Not valid:")
        for problem in str(exc).split("; "):
            print("  -", problem)
        return 1
    print(f"Valid: {pack.id} ({pack.title}), {len(pack.rules)} rules")
    if args.facts:
        given = json.loads(Path(args.facts).read_text(encoding="utf-8"))
        unknown = sorted(set(given) - set(CATALOG))
        if unknown:
            print("Ignored, not fact names:", ", ".join(unknown))
        facts = {name: given.get(name) for name in CATALOG}
        results = evaluate(PackRegistry.trial(pack), pack.chains[0], facts)
        for r in results:
            print(f"  [{r['status']:<11}] {r['rule_id']}: {r['reason']}")
        print("Summary:", summarise(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
