"""Rule packs: authority red-flag heuristics written as YAML files, loaded without code changes.

Where packs come from (later folders win when two packs share an id):
1. backend/rule_packs/*.yaml      packs that ship with the dashboard (in git)
2. RULE_PACKS_DIR                 an extra folder set in the environment (e.g. a mounted volume)
3. backend/data/rule_packs_uploaded/  packs an admin uploaded on the Rule packs page (not in git)

Files whose name starts with "_" are templates and never loaded.

Which packs are on, and their weights, are saved in backend/data/red_flag_settings.json (an admin
changes them on the Rule packs page). RED_FLAG_PACKS_DISABLED (comma separated pack ids) switches
packs off by default, which is handy on a server whose disk is reset on every deploy.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.redflags.expr import Condition, ExpressionError
from app.redflags.facts import CATALOG

BACKEND_DIR = Path(__file__).resolve().parents[2]
BUILTIN_DIR = BACKEND_DIR / "rule_packs"
UPLOAD_DIR = BACKEND_DIR / "data" / "rule_packs_uploaded"
SETTINGS_FILE = BACKEND_DIR / "data" / "red_flag_settings.json"
MAX_PACK_BYTES = 200_000
MAX_RULES = 200
ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
Severity = Literal["info", "low", "medium", "high", "critical"]
SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
CHAINS = ("bitcoin",)  # chains with facts today; more come with their fact builders


class PackError(ValueError):
    """The pack file is not valid. The message lists every problem found."""


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=300)
    url: str = Field(pattern=r"^https://", max_length=500)
    published: str | date | None = None
    reference: str | None = Field(default=None, max_length=200, description="Report number, e.g. FIN-2021-A004")


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str = Field(min_length=3, max_length=160)
    when: str = Field(description="Condition over fact names, e.g. 'pass_through_ratio >= 0.9 and tx_count >= 10'")
    severity: Severity = "medium"
    weight: float = Field(default=1.0, ge=0, le=100)
    citation: str = Field(min_length=2, max_length=300, description="Where in the document: section, paragraph or page")
    explain: str | None = Field(default=None, max_length=400,
                                description="Sentence shown when it fires; {fact_name} or {fact_name:.2f} inserts a value")
    advice: str | None = Field(default=None, max_length=400, description="What an analyst should look at next")
    chains: list[str] | None = None
    alert: bool = Field(default=False, description="Planned: raise a Live alert when it fires")

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not ID.match(value):
            raise ValueError("ids are lower case letters, digits and _, starting with a letter (2-64 characters)")
        return value


class Pack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str = Field(min_length=3, max_length=200)
    authority: str = Field(min_length=2, max_length=200)
    jurisdiction: str = Field(default="International", max_length=100)
    document: Document
    licence: str = Field(min_length=3, max_length=400, description="Terms for the source document and this pack")
    version: str | int = 1
    summary: str | None = Field(default=None, max_length=1000)
    chains: list[str] = Field(default_factory=lambda: ["bitcoin"])
    enabled_by_default: bool = True
    weight: float = Field(default=1.0, ge=0, le=10)
    rules: list[Rule] = Field(min_length=1, max_length=MAX_RULES)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return Rule._id(value)

    @model_validator(mode="after")
    def _rules(self) -> "Pack":
        problems = rule_problems([r.model_dump() for r in self.rules], self.chains)
        if problems:
            raise ValueError("; ".join(problems))
        return self


def rule_problems(rules: list, pack_chains: list | None) -> list[str]:
    """Problems in the rules' ids, chains, conditions and explain texts (checked on the raw YAML too,
    so a pack with other mistakes still gets these reported in the same message)."""
    from string import Formatter

    problems, seen = [], set()
    for number, rule in enumerate(rules if isinstance(rules, list) else []):
        if not isinstance(rule, dict):
            continue
        name = rule.get("id") or f"#{number + 1}"
        if name in seen:
            problems.append(f"rule '{name}': the id is used twice")
        seen.add(name)
        for chain in (rule.get("chains") or pack_chains or ["bitcoin"]):
            if chain not in CHAINS:
                problems.append(f"rule '{name}': chain '{chain}' has no facts yet (available: {', '.join(CHAINS)})")
        try:
            condition = Condition(str(rule.get("when", "")))
        except ExpressionError as exc:
            problems.append(f"rule '{name}': {exc}")
        else:
            unknown = sorted(condition.names - set(CATALOG))
            if unknown:
                problems.append(f"rule '{name}': unknown fact(s) {', '.join(unknown)} "
                                "(see GET /api/red-flags/facts for the list)")
        if rule.get("explain"):
            try:
                fields = [f for _, f, _, _ in Formatter().parse(str(rule["explain"])) if f is not None]
            except ValueError as exc:
                problems.append(f"rule '{name}': explain text: {exc}")
                fields = []
            bad = [f for f in fields if f not in CATALOG]
            if bad:
                problems.append(f"rule '{name}': explain text uses {', '.join(bad)}, which is not a fact name")
    return problems


class LoadedPack(BaseModel):
    pack: Pack
    origin: Literal["builtin", "folder", "uploaded"]
    file: str
    conditions: dict = Field(default_factory=dict, exclude=True)  # rule id -> Condition


def parse_pack(text: str, file_name: str = "pack.yaml") -> Pack:
    """Read and check one pack. Raises PackError with every problem in plain words."""
    if len(text.encode("utf-8")) > MAX_PACK_BYTES:
        raise PackError(f"{file_name}: the file is larger than {MAX_PACK_BYTES // 1000} KB.")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PackError(f"{file_name}: not valid YAML ({exc})") from None
    if not isinstance(data, dict):
        raise PackError(f"{file_name}: a pack is a YAML mapping with id, title, authority, document, licence and rules.")
    try:
        return Pack.model_validate(data)
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"]) or "pack"
            message = err["msg"].removeprefix("Value error, ")
            lines.append(f"{where}: {message}")
        # Field errors stop pydantic before the rule checks run, so run them here as well.
        extra = rule_problems(data.get("rules"), data.get("chains"))
        lines += [p for p in extra if not any(p in line for line in lines)]
        raise PackError(f"{file_name}: " + "; ".join(lines)) from None


def _load(path: Path, origin: str) -> LoadedPack:
    pack = parse_pack(path.read_text(encoding="utf-8"), path.name)
    return LoadedPack(pack=pack, origin=origin, file=path.name,
                      conditions={r.id: Condition(r.when) for r in pack.rules})


def _folders() -> list[tuple[Path, str]]:
    folders = [(BUILTIN_DIR, "builtin")]
    extra = os.getenv("RULE_PACKS_DIR")
    if extra:
        folders.append((Path(extra), "folder"))
    folders.append((UPLOAD_DIR, "uploaded"))
    return folders


class PackRegistry:
    """All packs found on disk, plus the on/off and weight settings. Thread-safe reloads."""

    def __init__(self, settings_file: Path = SETTINGS_FILE):
        self.settings_file = settings_file
        self.packs: dict[str, LoadedPack] = {}
        self.errors: list[dict] = []  # files that failed to load: {file, origin, error}
        self._lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        packs, errors = {}, []
        for folder, origin in _folders():
            if not folder.is_dir():
                continue
            for path in sorted([*folder.glob("*.yaml"), *folder.glob("*.yml")]):
                if path.name.startswith("_"):
                    continue
                try:
                    loaded = _load(path, origin)
                except (PackError, OSError) as exc:
                    errors.append({"file": path.name, "origin": origin, "error": str(exc)})
                    continue
                packs[loaded.pack.id] = loaded
        with self._lock:
            self.packs, self.errors = packs, errors

    @classmethod
    def trial(cls, pack: Pack) -> "PackRegistry":
        """A registry holding only this pack, switched on: to try a pack before it is saved."""
        registry = cls.__new__(cls)
        registry.settings_file, registry.errors, registry._lock = None, [], threading.Lock()
        registry.packs = {pack.id: LoadedPack(pack=pack, origin="uploaded", file="trial.yaml",
                                              conditions={r.id: Condition(r.when) for r in pack.rules})}
        registry.setting = lambda _id: {"enabled": True, "weight": pack.weight}
        return registry

    # Settings ------------------------------------------------------------------------------------
    def _read_settings(self) -> dict:
        try:
            return json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def setting(self, pack_id: str) -> dict:
        loaded = self.packs[pack_id]
        disabled_env = {p.strip() for p in os.getenv("RED_FLAG_PACKS_DISABLED", "").split(",") if p.strip()}
        saved = self._read_settings().get(pack_id, {})
        enabled = saved.get("enabled", loaded.pack.enabled_by_default and pack_id not in disabled_env)
        weight = saved.get("weight", loaded.pack.weight)
        return {"enabled": bool(enabled), "weight": float(weight)}

    def update_setting(self, pack_id: str, enabled: bool | None = None, weight: float | None = None) -> dict:
        if pack_id not in self.packs:
            raise KeyError(pack_id)
        with self._lock:
            settings = self._read_settings()
            entry = settings.setdefault(pack_id, {})
            if enabled is not None:
                entry["enabled"] = bool(enabled)
            if weight is not None:
                entry["weight"] = float(weight)
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            self.settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return self.setting(pack_id)

    def enabled(self, chain: str) -> list[tuple[LoadedPack, float]]:
        """(pack, weight) for every switched-on pack that covers the chain."""
        out = []
        for pack_id, loaded in self.packs.items():
            s = self.setting(pack_id)
            if s["enabled"] and any(chain in (r.chains or loaded.pack.chains) for r in loaded.pack.rules):
                out.append((loaded, s["weight"]))
        return out

    # Uploads -------------------------------------------------------------------------------------
    def save_upload(self, text: str) -> LoadedPack:
        pack = parse_pack(text, "uploaded pack")
        existing = self.packs.get(pack.id)
        if existing and existing.origin != "uploaded":
            raise PackError(f"A {existing.origin} pack already uses the id '{pack.id}'. Pick another id.")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (UPLOAD_DIR / f"{pack.id}.yaml").write_text(text, encoding="utf-8")
        self.reload()
        return self.packs[pack.id]

    def delete_upload(self, pack_id: str) -> None:
        loaded = self.packs.get(pack_id)
        if loaded is None or loaded.origin != "uploaded":
            raise KeyError(pack_id)
        (UPLOAD_DIR / loaded.file).unlink(missing_ok=True)
        self.reload()


_registry: PackRegistry | None = None


def get_pack_registry() -> PackRegistry:
    global _registry
    if _registry is None:
        _registry = PackRegistry()
    return _registry
