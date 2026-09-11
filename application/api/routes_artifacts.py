"""Artifact markdown/json/csv viewer / download (agentic-work style + ESS keys)."""

from __future__ import annotations

import html
import logging
import os
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from application.api.routes_auth import require_user_id
from application import utils
from application.viewer_html import (
    build_csv_viewer_page,
    build_json_viewer_page,
    build_markdown_viewer_page,
)

logger = logging.getLogger("routes_artifacts")

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

_MARKDOWN_EXTENSIONS = {".md", ".markdown"}
_JSON_EXTENSIONS = {".json"}
_CSV_EXTENSIONS = {".csv"}
_VIEWER_EXTENSIONS = _MARKDOWN_EXTENSIONS | _JSON_EXTENSIONS | _CSV_EXTENSIONS
_TEXT_VIEWER_MAX_BYTES = 8 * 1024 * 1024


def _safe_relative_path(file_path: str) -> str:
    raw = (file_path or "").strip().lstrip("/")
    if not raw:
        raise HTTPException(status_code=400, detail="File path is required")
    parts = [p for p in raw.replace("\\", "/").split("/") if p]
    if not parts or any(p == ".." for p in parts):
        raise HTTPException(status_code=400, detail="Invalid file path")
    return "/".join(parts)


def _normalize_artifact_rest(file_path: str, user_id: str) -> str:
    """Return path under the caller's artifact prefix (filename or nested rest).

    Accepts:
    - ``file.md`` / ``md/file.md``
    - ``{user}/…``
    - ``artifacts/{user}/…`` (agentic)
    - ``artifacts/{project}/{user}/…`` (ESS published markdown)
    """
    rest = _safe_relative_path(file_path)
    segment = utils.sanitize_user_path_segment(user_id)
    if not segment:
        raise HTTPException(status_code=400, detail="Invalid user session")

    parts = rest.split("/")
    if parts[0] == "artifacts":
        # artifacts/{project}/{user}/...
        if len(parts) >= 4 and parts[2] == segment:
            rest = "/".join(parts[3:])
            if not rest:
                raise HTTPException(status_code=400, detail="File path is required")
            return rest
        # artifacts/{user}/...
        if len(parts) >= 2 and parts[1] == segment:
            rest = "/".join(parts[2:])
            if not rest:
                raise HTTPException(status_code=400, detail="File path is required")
            return rest
        # artifacts/{file} flat → keep filename
        if len(parts) == 2:
            return parts[1]
        raise HTTPException(status_code=403, detail="Artifact access denied")

    if parts[0] == segment:
        rest = "/".join(parts[1:])
        if not rest:
            raise HTTPException(status_code=400, detail="File path is required")
        return rest
    return rest


def _project_name() -> str:
    name = (getattr(utils, "projectName", None) or "").strip()
    if name:
        return name
    try:
        cfg = utils.load_config()
        return str(cfg.get("projectName") or "").strip()
    except Exception:
        return ""


def _object_exists(bucket: str, key: str, client) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = (exc.response.get("Error") or {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound", "403", "AccessDenied"}:
            return False
        raise


def _s3_key_candidates(user_id: str, file_path: str) -> tuple[list[str], str]:
    """Prefer per-user keys; fall back to ESS project layout and flat legacy."""
    segment = utils.sanitize_user_path_segment(user_id)
    if not segment:
        raise HTTPException(status_code=400, detail="Invalid user session")
    rest = _normalize_artifact_rest(file_path, user_id)
    basename = os.path.basename(rest)
    project = _project_name()

    keys: list[str] = [
        f"artifacts/{segment}/{rest}",
    ]
    if project:
        keys.append(f"artifacts/{project}/{segment}/{rest}")
        # ESS published markdown often lives under …/md/
        if not rest.startswith("md/"):
            keys.append(f"artifacts/{project}/{segment}/md/{basename}")
        keys.append(f"artifacts/{project}/{segment}/{basename}")

    # Flat legacy (pre per-user upload): artifacts/{filename}
    flat = f"artifacts/{basename}"
    if flat not in keys:
        keys.append(flat)
    # Nested under artifacts without user (agent wrote artifacts/foo/bar.csv)
    if "/" in rest:
        nested_flat = f"artifacts/{rest}"
        if nested_flat not in keys:
            keys.append(nested_flat)

    # de-dupe preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered, basename


def _resolve_artifact_key(user_id: str, file_path: str) -> tuple[str, str]:
    bucket = utils.s3_bucket
    if not bucket:
        raise HTTPException(status_code=503, detail="S3 bucket is not configured")
    keys, basename = _s3_key_candidates(user_id, file_path)
    client = boto3.client("s3", region_name=utils.bedrock_region)
    for key in keys:
        if _object_exists(bucket, key, client):
            return key, basename
    return keys[0], basename


def _s3_key_for_user_artifact(user_id: str, file_path: str) -> tuple[str, str]:
    """Return (s3_key, basename) for the caller's artifact."""
    return _resolve_artifact_key(user_id, file_path)


def _read_s3_bytes(s3_key: str, *, max_bytes: int | None = None) -> bytes:
    bucket = utils.s3_bucket
    if not bucket:
        raise HTTPException(status_code=503, detail="S3 bucket is not configured")
    client = boto3.client("s3", region_name=utils.bedrock_region)
    try:
        obj = client.get_object(Bucket=bucket, Key=s3_key)
    except ClientError as exc:
        code = (exc.response.get("Error") or {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {s3_key}") from exc
        logger.exception("S3 get_object failed for %s", s3_key)
        raise HTTPException(status_code=502, detail="Failed to read artifact from S3") from exc

    body = obj["Body"]
    if max_bytes is None:
        return body.read()
    data = body.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Artifact too large to preview (max {max_bytes} bytes)",
        )
    return data


def _ext_of(name: str) -> str:
    return os.path.splitext((name or "").lower())[1]


def _is_viewer_name(name: str) -> bool:
    return _ext_of(name) in _VIEWER_EXTENSIONS


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _media_type_for_ext(ext: str) -> str:
    if ext in _JSON_EXTENSIONS:
        return "application/json; charset=utf-8"
    if ext in _CSV_EXTENSIONS:
        return "text/csv; charset=utf-8"
    return "text/markdown; charset=utf-8"


def _topbar_actions(user_id: str, file_path: str, s3_key: str) -> str:
    rest = _normalize_artifact_rest(file_path, user_id)
    encoded_rest = quote(rest, safe="/")
    download_href = f"/api/artifacts/download/{encoded_rest}"
    actions: list[str] = [
        f'<a class="action" href="{html.escape(download_href, quote=True)}">Download</a>',
    ]
    if utils.sharing_url:
        raw_url = f"{utils.sharing_url.rstrip('/')}/{quote(s3_key, safe='/')}"
        actions.append(
            f'<a class="action" href="{html.escape(raw_url, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">Raw</a>'
        )
    return "".join(actions)


@router.get("/view/{file_path:path}")
def view_artifact(file_path: str, request: Request):
    """Render an artifact markdown/json/csv file as HTML (new browser tab)."""
    user_id = require_user_id(request)
    s3_key, file_name = _s3_key_for_user_artifact(user_id, file_path)
    ext = _ext_of(file_name)
    if ext not in _VIEWER_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Viewer supports .md / .markdown / .json / .csv only",
        )

    data = _read_s3_bytes(s3_key, max_bytes=_TEXT_VIEWER_MAX_BYTES)
    text = _decode_text(data)

    actions = _topbar_actions(user_id, file_path, s3_key)
    if ext in _JSON_EXTENSIONS:
        page = build_json_viewer_page(file_name, text, topbar_right_html=actions)
    elif ext in _CSV_EXTENSIONS:
        page = build_csv_viewer_page(file_name, text, topbar_right_html=actions)
    else:
        page = build_markdown_viewer_page(file_name, text, topbar_right_html=actions)
    return HTMLResponse(content=page, media_type="text/html; charset=utf-8")


@router.get("/download/{file_path:path}")
def download_artifact(file_path: str, request: Request):
    """Download an artifact file (markdown/json/csv) as an attachment."""
    user_id = require_user_id(request)
    s3_key, file_name = _s3_key_for_user_artifact(user_id, file_path)
    ext = _ext_of(file_name)
    if not _is_viewer_name(file_name):
        raise HTTPException(
            status_code=400,
            detail="Download via this endpoint supports .md / .markdown / .json / .csv only",
        )

    data = _read_s3_bytes(s3_key)
    safe_name = file_name.replace('"', "")
    headers = {
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "Cache-Control": "no-store",
    }
    return Response(
        content=data,
        media_type=_media_type_for_ext(ext),
        headers=headers,
    )
