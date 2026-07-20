"""Spec §9: the minimal one-page approval UI, served at the app root."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

_INDEX_PATH = Path(__file__).parent / "static" / "index.html"


@router.get("/")
def approval_ui() -> FileResponse:
    return FileResponse(_INDEX_PATH)
