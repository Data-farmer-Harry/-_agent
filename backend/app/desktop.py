from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def attach_desktop_frontend(app: FastAPI, frontend_dir: Path | None = None) -> bool:
    """Serve the packaged Vite application from the local backend.

    Development remains unchanged because the mount is enabled only when the
    Electron launcher supplies ``MATTERLAB_DESKTOP_FRONTEND_DIR`` or a caller
    passes an explicit directory.
    """

    configured = str(frontend_dir or os.getenv("MATTERLAB_DESKTOP_FRONTEND_DIR", "")).strip()
    if not configured or getattr(app.state, "desktop_frontend_attached", False):
        return False

    root = Path(configured).expanduser().resolve()
    index_file = root / "index.html"
    assets_dir = root / "assets"
    if not index_file.is_file():
        raise RuntimeError(f"Desktop frontend is missing index.html: {index_file}")

    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="desktop-assets")

    @app.get("/{requested_path:path}", include_in_schema=False)
    def desktop_spa(requested_path: str) -> FileResponse:
        if requested_path == "api" or requested_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")
        candidate = (root / requested_path).resolve()
        if requested_path and _is_within(candidate, root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)

    app.state.desktop_frontend_attached = True
    return True
