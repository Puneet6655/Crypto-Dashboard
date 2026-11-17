"""Authority red flags: rule packs (FATF, FinCEN, AUSTRAC, OFAC, ...) and checks against them.

Everyone signed in can read packs and check addresses. Admins switch packs on or off, change their
weights, and upload or remove their own packs. A pack can be validated (and tried on example facts)
before it is saved.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.auth.accounts import CurrentUser
from app.auth.deps import require_admin
from app.db import NullStore
from app.intelligence.index import IndicatorIndex, get_indicator_index
from app.providers.base import BitcoinProvider, ProviderError
from app.providers.registry import get_bitcoin_provider
from app.redflags.engine import evaluate, summarise
from app.redflags.facts import CATALOG
from app.redflags.packs import LoadedPack, PackError, PackRegistry, get_pack_registry, parse_pack
from app.redflags.service import bitcoin_red_flags
from app.store import get_store
from app.validation import is_bitcoin_address, normalize_address
from app.workspace import audit
from app.workspace.database import pool_of

router = APIRouter(prefix="/api/red-flags", tags=["red flags"])


def _audit(store: NullStore, admin: CurrentUser, action: str, target: str, detail: dict | None = None) -> None:
    """Log admin changes when the database is on (local runs without one still work)."""
    if getattr(store, "enabled", False):
        _audit(store, admin, action, target, detail)


def _pack_view(registry: PackRegistry, loaded: LoadedPack) -> dict:
    pack = loaded.pack
    return {
        **pack.model_dump(mode="json", exclude={"rules"}),
        "origin": loaded.origin, "file": loaded.file,
        "setting": registry.setting(pack.id),
        "rules": [r.model_dump(mode="json") for r in pack.rules],
    }


@router.get("/facts")
def list_facts() -> list[dict]:
    """Every fact a rule condition can use, with its unit and meaning."""
    return [{"name": f.name, "chains": list(f.chains), "unit": f.unit, "description": f.description}
            for f in CATALOG.values()]


@router.get("/packs")
def list_packs(registry: PackRegistry = Depends(get_pack_registry)) -> dict:
    return {"packs": [_pack_view(registry, p) for p in registry.packs.values()], "errors": registry.errors}


@router.get("/packs/{pack_id}/source")
def pack_source(pack_id: str, registry: PackRegistry = Depends(get_pack_registry)) -> dict:
    """The pack's YAML, to read or download as a starting point for a new pack."""
    from app.redflags.packs import BUILTIN_DIR, UPLOAD_DIR
    import os
    from pathlib import Path

    loaded = registry.packs.get(pack_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="No pack with that id.")
    folder = {"builtin": BUILTIN_DIR, "uploaded": UPLOAD_DIR}.get(loaded.origin) or Path(os.getenv("RULE_PACKS_DIR", ""))
    return {"id": pack_id, "file": loaded.file, "yaml": (folder / loaded.file).read_text(encoding="utf-8")}


class PackChange(BaseModel):
    enabled: bool | None = None
    weight: float | None = Field(default=None, ge=0, le=10)


@router.patch("/packs/{pack_id}")
def change_pack(pack_id: str, body: PackChange, admin: CurrentUser = Depends(require_admin),
                registry: PackRegistry = Depends(get_pack_registry), store: NullStore = Depends(get_store)) -> dict:
    try:
        setting = registry.update_setting(pack_id, body.enabled, body.weight)
    except KeyError:
        raise HTTPException(status_code=404, detail="No pack with that id.")
    _audit(store, admin, "red_flag_pack.changed", pack_id, body.model_dump(exclude_none=True))
    return setting


class PackText(BaseModel):
    yaml: str = Field(min_length=10, max_length=200_000)
    # Optional example facts to try the rules on, e.g. {"pass_through_ratio": 0.97, "median_hours_to_spend": 2}
    facts: dict[str, float | int | None] | None = None


@router.post("/packs/validate")
def validate_pack(body: PackText) -> dict:
    """Check a pack without saving it. With example facts, also show what each rule would say."""
    try:
        pack = parse_pack(body.yaml)
    except PackError as exc:
        return {"valid": False, "errors": str(exc).split("; ")}
    out = {"valid": True, "id": pack.id, "title": pack.title, "rules": len(pack.rules), "errors": []}
    if body.facts is not None:
        unknown = sorted(set(body.facts) - set(CATALOG))
        facts = {name: body.facts.get(name) for name in CATALOG}
        results = evaluate(PackRegistry.trial(pack), pack.chains[0], facts)
        out.update(trial_results=results, trial_summary=summarise(results), unknown_facts=unknown)
    return out


@router.post("/packs")
def upload_pack(body: PackText, admin: CurrentUser = Depends(require_admin),
                registry: PackRegistry = Depends(get_pack_registry), store: NullStore = Depends(get_store)) -> dict:
    try:
        loaded = registry.save_upload(body.yaml)
    except PackError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _audit(store, admin, "red_flag_pack.uploaded", loaded.pack.id,
                 {"title": loaded.pack.title, "rules": len(loaded.pack.rules)})
    return _pack_view(registry, loaded)


@router.delete("/packs/{pack_id}")
def delete_pack(pack_id: str, admin: CurrentUser = Depends(require_admin),
                registry: PackRegistry = Depends(get_pack_registry), store: NullStore = Depends(get_store)) -> dict:
    try:
        registry.delete_upload(pack_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Only packs uploaded here can be removed; built-in packs can be switched off.")
    _audit(store, admin, "red_flag_pack.removed", pack_id)
    return {"removed": pack_id}


@router.post("/reload")
async def reload_packs(admin: CurrentUser = Depends(require_admin),
                       registry: PackRegistry = Depends(get_pack_registry)) -> dict:
    """Read the pack folders again (after copying a file into RULE_PACKS_DIR, for example)."""
    await run_in_threadpool(registry.reload)
    return {"packs": sorted(registry.packs), "errors": registry.errors}


class AddressCheck(BaseModel):
    address: str = Field(min_length=20, max_length=100)


@router.post("/bitcoin")
async def check_bitcoin(body: AddressCheck, provider: BitcoinProvider = Depends(get_bitcoin_provider),
                        index: IndicatorIndex = Depends(get_indicator_index),
                        registry: PackRegistry = Depends(get_pack_registry)) -> dict:
    """Run every switched-on pack over one Bitcoin address (reads its history: up to 5 provider requests)."""
    if not is_bitcoin_address(body.address):
        raise HTTPException(status_code=422, detail="That does not look like a mainnet Bitcoin address.")
    try:
        return await bitcoin_red_flags(provider, normalize_address(body.address), index, registry)
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message, headers=exc.headers())
