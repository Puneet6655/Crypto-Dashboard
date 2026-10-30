"""Cases. Everyone signed in can read them; analysts and admins create and fill them; only admins delete them."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.auth.accounts import CurrentUser
from app.auth.deps import require_admin, require_analyst, require_viewer
from app.db import NullStore
from app.store import get_store
from app.workspace import audit, cases
from app.workspace.database import pool_of

router = APIRouter(prefix="/api/cases", tags=["cases"])

ItemType = Literal["address", "transaction", "domain", "payment", "other"]


class CaseItemIn(BaseModel):
    item_type: ItemType
    value: str = Field(max_length=300)
    network: str = Field("bitcoin", max_length=40)
    label: str | None = Field(None, max_length=200)
    note: str = Field("", max_length=2000)
    source: str | None = Field(None, max_length=200)


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=4000)
    priority: Literal["low", "medium", "high"] = "medium"
    items: list[CaseItemIn] = Field(default_factory=list, max_length=200)


class CaseUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=4000)
    status: Literal["open", "closed"] | None = None
    priority: Literal["low", "medium", "high"] | None = None


class NoteUpdate(BaseModel):
    note: str = Field(max_length=2000)


def _checked_item(item: CaseItemIn) -> dict:
    value = cases.clean_value(item.item_type, item.value)
    problem = cases.value_problem(item.item_type, value)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    return {**item.model_dump(), "value": value}


@router.get("")
async def list_cases(_: CurrentUser = Depends(require_viewer), store: NullStore = Depends(get_store)) -> list[dict]:
    return await run_in_threadpool(cases.list_cases, pool_of(store))


@router.post("", status_code=201)
def create_case(body: CaseCreate, user: CurrentUser = Depends(require_analyst),
                store: NullStore = Depends(get_store)) -> dict:
    pool = pool_of(store)
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="Give the case a title.")
    items = [_checked_item(item) for item in body.items]
    case_id = cases.create_case(pool, user.id, body.title, body.description, body.priority)
    for item in items:
        cases.add_item(pool, case_id, user.id, item)
    audit.record(pool, user, "case.created", f"case {case_id}", {"title": body.title.strip(), "items": len(items)})
    return cases.get_case(pool, case_id)


@router.get("/{case_id}")
def get_case(case_id: int, _: CurrentUser = Depends(require_viewer), store: NullStore = Depends(get_store)) -> dict:
    case = cases.get_case(pool_of(store), case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="No case with that ID.")
    return case


@router.patch("/{case_id}")
def update_case(case_id: int, body: CaseUpdate, user: CurrentUser = Depends(require_analyst),
                store: NullStore = Depends(get_store)) -> dict:
    pool = pool_of(store)
    changes = body.model_dump(exclude_none=True)
    if not cases.update_case(pool, case_id, changes):
        raise HTTPException(status_code=404, detail="No case with that ID.")
    if "status" in changes:
        audit.record(pool, user, f"case.{'closed' if changes['status'] == 'closed' else 'reopened'}", f"case {case_id}")
    return cases.get_case(pool, case_id)


@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: int, admin: CurrentUser = Depends(require_admin), store: NullStore = Depends(get_store)) -> None:
    pool = pool_of(store)
    deleted = cases.delete_case(pool, case_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="No case with that ID.")
    audit.record(pool, admin, "case.deleted", f"case {case_id}", {"title": deleted["title"]})


@router.post("/{case_id}/items")
def add_item(case_id: int, body: CaseItemIn, user: CurrentUser = Depends(require_analyst),
             store: NullStore = Depends(get_store)) -> dict:
    pool = pool_of(store)
    if not cases.case_exists(pool, case_id):
        raise HTTPException(status_code=404, detail="No case with that ID.")
    item, created = cases.add_item(pool, case_id, user.id, _checked_item(body))
    return {"item": item, "created": created}


@router.patch("/{case_id}/items/{item_id}", status_code=204)
def update_item(case_id: int, item_id: int, body: NoteUpdate, _: CurrentUser = Depends(require_analyst),
                store: NullStore = Depends(get_store)) -> None:
    if not cases.update_item_note(pool_of(store), case_id, item_id, body.note):
        raise HTTPException(status_code=404, detail="That item is not in this case.")


@router.delete("/{case_id}/items/{item_id}", status_code=204)
def remove_item(case_id: int, item_id: int, user: CurrentUser = Depends(require_analyst),
                store: NullStore = Depends(get_store)) -> None:
    pool = pool_of(store)
    removed = cases.remove_item(pool, case_id, item_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="That item is not in this case.")
    audit.record(pool, user, "case.item_removed", f"case {case_id}", removed)
