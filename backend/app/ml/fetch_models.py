"""Download the trained models on a server, where backend/models is not in git.

Run before the API starts: python -m app.ml.fetch_models
- MODELS_URL: link to a zip of the model files (registry.json at the top level of the zip).
- MODELS_TOKEN: optional. Sent as "Authorization: Bearer <token>", e.g. a GitHub token for a
  private release asset (use the asset's API URL, https://api.github.com/repos/<owner>/<repo>/releases/assets/<id>).
Does nothing when MODELS_URL is empty or the models are already there. Never stops the API from starting:
without models, lookups simply have no model score.
"""
import io
import logging
import os
import sys
import zipfile

import httpx

from app.config import get_settings

log = logging.getLogger("fetch_models")


def fetch(url: str, folder, token: str | None = None) -> bool:
    if (folder / "registry.json").exists():
        log.info("Models already in %s", folder)
        return True
    headers = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    log.info("Downloading models zip")
    with httpx.Client(follow_redirects=True, timeout=300) as client:
        response = client.get(url, headers=headers)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        # Accept a zip whose files sit inside one top-level folder too.
        prefix = ""
        if "registry.json" not in names:
            nested = [n for n in names if n.endswith("/registry.json")]
            if not nested:
                raise ValueError("The models zip has no registry.json")
            prefix = nested[0][: -len("registry.json")]
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            relative = name[len(prefix):]
            target = (folder / relative).resolve()
            if folder.resolve() not in target.parents:  # skip paths that escape the folder
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    log.info("Models saved to %s (%d MB)", folder, len(response.content) // 1_000_000)
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    url = os.getenv("MODELS_URL", "").strip()
    if not url:
        log.info("MODELS_URL is not set; skipping model download")
        return 0
    try:
        fetch(url, get_settings().models_dir, os.getenv("MODELS_TOKEN") or None)
    except Exception as exc:  # the API still starts, just without model scores
        log.error("Model download failed: %s", exc.__class__.__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
