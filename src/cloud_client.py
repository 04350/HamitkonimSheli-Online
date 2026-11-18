# -*- coding: utf-8 -*-
"""Small, dependency-free HTTPS client for the hybrid recipe-processing service."""
from __future__ import annotations
import io, json, mimetypes, os, uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_CLOUD_API_URL = os.environ.get("RECIPE_CLOUD_API_URL", "").strip().rstrip("/")
_API_BASE_URL = DEFAULT_CLOUD_API_URL
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 100 * 1024 * 1024

class CloudServiceError(RuntimeError):
    """Transport, configuration, or response error from the processing service."""

def set_api_base_url(value: str | None) -> str:
    global _API_BASE_URL
    candidate = (value or "").strip().rstrip("/")
    if not candidate:
        _API_BASE_URL = ""
        return _API_BASE_URL
    parsed = urlsplit(candidate)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if parsed.scheme != "https" and not local_http:
        raise ValueError("כתובת שירות העיבוד חייבת להתחיל ב-https:// (למעט בדיקות מקומיות).")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("כתובת שירות העיבוד אינה תקינה.")
    _API_BASE_URL = candidate
    return _API_BASE_URL

def get_api_base_url() -> str:
    return _API_BASE_URL

def _multipart_body(data: bytes, filename: str, content_type: str):
    boundary = "----RecipeLibrary" + uuid.uuid4().hex
    marker = boundary.encode("ascii")
    safe_name = Path(filename).name.replace('"', "").replace("\r", "").replace("\n", "")
    body = (b"--" + marker + b"\r\n"
        + f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'.encode("utf-8")
        + f"Content-Type: {content_type}\r\n\r\n".encode("ascii")
        + data + b"\r\n--" + marker + b"--\r\n")
    return body, boundary

def _post_file(route, data, filename, content_type, timeout=90):
    if not _API_BASE_URL:
        raise CloudServiceError("לא הוגדרה כתובת שרת. פתחו הגדרות והזינו כתובת שירות עיבוד.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise CloudServiceError(
            f"הקובץ גדול מדי לשליחה לשירות המקוון (המגבלה היא {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)."
        )
    body, boundary = _multipart_body(data, filename, content_type)
    req = Request(_API_BASE_URL + route, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json, application/pdf",
        "User-Agent": "RecipeLibrary-Hybrid/1.0"}, method="POST")
    try:
        with urlopen(req, timeout=max(10, int(timeout))) as response:
            headers = getattr(response, "headers", {})
            content_length = headers.get("Content-Length") if headers is not None else None
            if content_length:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise CloudServiceError("תשובת השרת גדולה מדי להורדה.")
                except ValueError:
                    pass
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise CloudServiceError("תשובת השרת חורגת ממגבלת 100MB.")
            return payload
    except CloudServiceError:
        raise
    except HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise CloudServiceError(f"השרת החזיר שגיאה ({exc.code}): {detail or exc.reason}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CloudServiceError(f"לא ניתן להתחבר לשרת העיבוד: {exc}") from exc

def cloud_health_check(timeout=12):
    if not _API_BASE_URL:
        raise CloudServiceError("לא הוגדרה כתובת שרת העיבוד.")
    req = Request(_API_BASE_URL + "/health", headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=max(5, int(timeout))) as response:
            raw = response.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise CloudServiceError("תשובת בדיקת השרת גדולה מהצפוי.")
        data = json.loads(raw.decode("utf-8"))
    except CloudServiceError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise CloudServiceError(f"בדיקת השרת נכשלה: {exc}") from exc
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise CloudServiceError("שרת העיבוד אינו מוכן.")
    return data

def cloud_ocr_image(image):
    buffer=io.BytesIO()
    try:
        image.save(buffer, format="PNG", optimize=True)
        payload=buffer.getvalue()
    finally:
        buffer.close()
    raw=_post_file("/api/v1/ocr/image", payload, "scan.png", "image/png", timeout=90)
    try:
        result=json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CloudServiceError("שרת ה-OCR החזיר תשובה לא תקינה.") from exc
    if not isinstance(result, dict) or "text" not in result:
        raise CloudServiceError("תשובת ה-OCR חסרה.")
    return result

def cloud_convert_office(source: Path, timeout=180):
    source = Path(source)
    if not source.is_file():
        raise CloudServiceError("קובץ המסמך לא נמצא.")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise CloudServiceError(f"לא ניתן לקרוא את פרטי הקובץ: {exc}") from exc
    if size > MAX_UPLOAD_BYTES:
        raise CloudServiceError(
            f"המסמך גדול מדי לשליחה לשירות המקוון (המגבלה היא {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)."
        )
    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise CloudServiceError(f"לא ניתן לקרוא את המסמך: {exc}") from exc
    raw = _post_file("/api/v1/office/convert", data, source.name, content_type, timeout=timeout)
    if not raw.startswith(b"%PDF"):
        raise CloudServiceError("שירות ההמרה לא החזיר מסמך PDF תקין.")
    return raw
