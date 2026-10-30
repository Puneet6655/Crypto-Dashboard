"""Command line for the indicator snapshots. Run from backend/:

    ..\\.venv\\Scripts\\python.exe -m app.intelligence refresh          # download stale or missing lists
    ..\\.venv\\Scripts\\python.exe -m app.intelligence refresh --all    # download every list now
    ..\\.venv\\Scripts\\python.exe -m app.intelligence status
    ..\\.venv\\Scripts\\python.exe -m app.intelligence check <address>
"""
import argparse
import logging

from app.config import get_settings
from app.intelligence import snapshots
from app.intelligence.index import IndicatorIndex


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.intelligence")
    commands = parser.add_subparsers(dest="command", required=True)
    refresh = commands.add_parser("refresh", help="download the indicator lists")
    refresh.add_argument("--all", action="store_true", help="download every list, not only stale ones")
    refresh.add_argument("--force", action="store_true", help="replace a list even if it shrank by half")
    commands.add_parser("status", help="show the saved snapshots")
    check = commands.add_parser("check", help="look up one address in the saved lists")
    check.add_argument("address")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    folder = settings.indicators_dir

    if args.command == "refresh":
        ids = None if args.all else snapshots.stale_sources(folder, settings.indicators_max_age_hours)
        if ids == []:
            print(f"All lists are younger than {settings.indicators_max_age_hours:g} hours. Use --all to download anyway.")
            return 0
        results = snapshots.refresh(folder, ids, force=args.force)
        for r in results:
            print(f"{r.source_id:12} {r.status:9} {r.detail}")
        print(f"Saved in {folder}")
        return 0 if all(r.status in ("updated", "unchanged") for r in results) else 1

    index = IndicatorIndex(folder)
    if args.command == "status":
        for s in index.status(settings.indicators_max_age_hours):
            count = f"{s['record_count']:,}" if s["record_count"] is not None else "-"
            print(f"{s['source_id']:12} loaded={s['loaded']!s:5} addresses={count:>7} "
                  f"retrieved={s['retrieved_at'] or '-'} list_updated={s['list_updated_at'] or '-'}")
        return 0

    hits = index.lookup(args.address)
    if not hits:
        print(f"No match in {len(index.lists)} loaded lists. That does not mean the address is safe.")
    for h in hits:
        print(f"MATCH {h.source_id}: {h.title}" + (f", family {h.family}" if h.family else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
