"""The Sources page. Everyone signed in reads it; only admins change it (each change goes in the audit log)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.auth.accounts import CurrentUser
from app.auth.deps import require_admin, require_viewer
from app.db import NullStore
from app.store import get_store
from app.workspace import audit, sources
from app.workspace.database import pool_of

router = APIRouter(prefix="/api/sources", tags=["sources"])


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    url: str = Field("", max_length=500)
    used_for: str = Field("", max_length=1000)
    licence: str = Field("", max_length=500)
    citation: str = Field("", max_length=1000)


class MoveRequest(BaseModel):
    direction: Literal["up", "down"]


def _clean(body: SourceIn) -> dict:
    fields = {k: v.strip() for k, v in body.model_dump().items()}
    if not fields["name"] or not fields["category"]:
        raise HTTPException(status_code=422, detail="A source needs a name and a category.")
    if fields["url"] and not fields["url"].startswith(("https://", "http://")):
        raise HTTPException(status_code=422, detail="The link must start with https:// or http://.")
    return fields


@router.get("")
async def list_sources(_: CurrentUser = Depends(require_viewer), store: NullStore = Depends(get_store)) -> dict:
    if not store.enabled:
        # Without a database, show the built-in list read-only.
        return {"editable": False, "categories": sources.CATEGORIES,
                "sources": [{**s, "id": i, "updated_at": None, "updated_by_name": None}
                            for i, s in enumerate(sources.DEFAULT_SOURCES, start=1)]}
    rows = await run_in_threadpool(sources.list_sources, pool_of(store))
    return {"editable": True, "categories": sources.CATEGORIES, "sources": rows}


@router.post("", status_code=201)
def create_source(body: SourceIn, admin: CurrentUser = Depends(require_admin), store: NullStore = Depends(get_store)) -> dict:
    pool = pool_of(store)
    fields = _clean(body)
    source_id = sources.create_source(pool, admin.id, fields)
    audit.record(pool, admin, "source.added", fields["name"], fields)
    return {"id": source_id}


@router.put("/{source_id}", status_code=204)
def update_source(source_id: int, body: SourceIn, admin: CurrentUser = Depends(require_admin),
                  store: NullStore = Depends(get_store)) -> None:
    pool = pool_of(store)
    fields = _clean(body)
    before = sources.update_source(pool, source_id, admin.id, fields)
    if before is None:
        raise HTTPException(status_code=404, detail="No source with that ID.")
    changed = {k: {"from": before[k], "to": v} for k, v in fields.items() if before[k] != v}
    audit.record(pool, admin, "source.edited", fields["name"], changed)


@router.post("/{source_id}/move", status_code=204)
def move_source(source_id: int, body: MoveRequest, _: CurrentUser = Depends(require_admin),
                store: NullStore = Depends(get_store)) -> None:
    if not sources.move_source(pool_of(store), source_id, -1 if body.direction == "up" else 1):
        raise HTTPException(status_code=404, detail="No source with that ID.")


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: int, admin: CurrentUser = Depends(require_admin), store: NullStore = Depends(get_store)) -> None:
    pool = pool_of(store)
    deleted = sources.delete_source(pool, source_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="No source with that ID.")
    audit.record(pool, admin, "source.removed", deleted["name"])
