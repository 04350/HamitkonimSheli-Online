# -*- coding: utf-8 -*-
"""Stateless processing API for the local-first Recipe Library desktop app."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque

import pytesseract
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from starlette.background import BackgroundTask
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, UnidentifiedImageError

from recipe_scanner import recognize_image

APP_VERSION = "1.0.0"
MAX_UPLOAD_BYTES = max(1, int(os.getenv("MAX_UPLOAD_MB", "20"))) * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
ALLOWED_OFFICE = {
    ".doc", ".docx", ".docm", ".odt", ".rtf",
    ".xls", ".xlsx", ".xlsm", ".ods",
    ".ppt", ".pptx", ".pptm", ".odp",
}
_RATE: dict[str, Deque[float]] = defaultdict(deque)

app = FastAPI(
    title="ספריית המתכונים שלי — שירות עיבוד",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )
    now = time.monotonic()
    recent = _RATE[client_ip]
    while recent and now - recent[0] > 60:
        recent.popleft()
    if len(recent) >= 50:
        return JSONResponse(
            status_code=429,
            content={"detail": "יותר מדי פעולות בפרק זמן קצר. נסו שוב בעוד דקה."},
        )
    recent.append(now)
    return await call_next(request)


@app.get("/")
def root():
    return {
        "name": "ספריית המתכונים שלי — שירות עיבוד",
        "status": "ok",
        "health": "/health",
    }


@app.get("/health")
def health():
    try:
        languages = set(pytesseract.get_languages(config=""))
        ocr_ready = {"heb", "eng"}.issubset(languages)
    except Exception:
        languages = set()
        ocr_ready = False
    office_ready = bool(shutil.which("libreoffice") or shutil.which("soffice"))
    return {
        "status": "ok",
        "version": APP_VERSION,
        "ocr": ocr_ready,
        "languages": sorted(languages),
        "office": office_ready,
    }


def read_upload(file: UploadFile) -> bytes:
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="הקובץ ריק.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"גודל הקובץ חורג ממגבלת {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
        )
    return data


@app.post("/api/v1/ocr/image")
def ocr_image(file: UploadFile = File(...)):
    data = read_upload(file)
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="התמונה גדולה מדי לעיבוד.")
            rgb = source.convert("RGB")
            try:
                result = recognize_image(rgb, pytesseract)
            finally:
                rgb.close()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(
            status_code=400,
            detail="הקובץ אינו תמונה תקינה או שנתוני התמונה פגומים.",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"שירות זיהוי הטקסט נכשל: {exc}") from exc
    return {
        "text": str(result.get("text") or ""),
        "title": str(result.get("title") or ""),
        "confidence": float(result.get("confidence") or 0),
        "lines": result.get("lines") or [],
        "engine": "tesseract-heb-eng",
    }


@app.post("/api/v1/office/convert")
def office_convert(file: UploadFile = File(...)):
    data = read_upload(file)
    original_name = Path(file.filename or "document").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_OFFICE:
        raise HTTPException(status_code=415, detail="סוג מסמך Office זה אינו נתמך להמרה.")
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise HTTPException(status_code=503, detail="מנוע המרת Office אינו זמין בשרת.")

    work_dir = Path(tempfile.mkdtemp(prefix="recipe-office-"))
    input_path = work_dir / ("input" + suffix)
    output_dir = work_dir / "output"
    profile_dir = work_dir / "profile"
    output_dir.mkdir()
    profile_dir.mkdir()
    output_path = output_dir / "input.pdf"
    input_path.write_bytes(data)
    command = [
        executable, "--headless", "--nologo", "--nodefault", "--nolockcheck",
        f"-env:UserInstallation={profile_dir.as_uri()}",
        "--convert-to", "pdf", "--outdir", str(output_dir), str(input_path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if not output_path.is_file() or output_path.stat().st_size < 100:
            detail = (completed.stderr or completed.stdout or "LibreOffice לא יצר PDF תקין.")[-700:]
            raise RuntimeError(detail)
        if output_path.stat().st_size > 100 * 1024 * 1024:
            raise RuntimeError("קובץ ה-PDF שנוצר גדול מדי.")
        return FileResponse(
            output_path,
            media_type="application/pdf",
            filename=f"{Path(original_name).stem}.pdf",
            background=BackgroundTask(
                shutil.rmtree, str(work_dir), ignore_errors=True
            ),
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=504, detail="המרת המסמך ארכה יותר מדי זמן.") from exc
    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"לא ניתן להמיר את מסמך Office: {exc}") from exc
