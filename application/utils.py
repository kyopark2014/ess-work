import logging
import sys
import json
import traceback
import boto3
import os
from contextlib import contextmanager
from urllib import parse

logging.basicConfig(
    level=logging.INFO,  # Default to INFO level
    format='%(filename)s:%(lineno)d | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("utils")

script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, "config.json")
favorite_tools_path = os.path.join(script_dir, "favorite_tools.json")


# ECS: /mnt/app-data (prefix app-data/) for tasks.db, graph, settings.
# Runtime: /mnt/workspace (prefix agentcore-sessions/) for skills/artifacts/checkpoints.
def _default_session_storage_dir() -> str:
    """Prefer ECS app-data mount, then Runtime workspace, then local fallback."""
    for candidate in ("/mnt/app-data", "/mnt/workspace"):
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(script_dir, ".session_storage")


SESSION_STORAGE_DIR = os.environ.get("SESSION_STORAGE_DIR") or _default_session_storage_dir()

# S3 Files FS prefix for Runtime workspace → s3://{bucket}/agentcore-sessions/
S3_FILES_SESSION_PREFIX = "agentcore-sessions"


def sanitize_user_path_segment(user_id: str | None) -> str | None:
    """Return a safe single path segment for per-user workspace folders, or None."""
    if not user_id:
        return None
    # Collapse path separators so user_id cannot escape the intended prefix.
    segment = (
        str(user_id)
        .strip()
        .replace("/", "_")
        .replace("\\", "_")
        .replace("..", "_")
    )
    return segment or None


def get_user_artifacts_dir(user_id: str | None) -> str:
    """Logical path for user artifacts (Runtime /mnt/workspace when present)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    root = "/mnt/workspace" if os.path.isdir("/mnt/workspace") else SESSION_STORAGE_DIR
    return os.path.join(root, segment, "artifacts")


def ensure_user_artifacts_dir(user_id: str | None) -> str:
    """Create artifacts dir under Runtime workspace when available; skip on ECS app-data."""
    artifacts_dir = get_user_artifacts_dir(user_id)
    if not os.path.isdir("/mnt/workspace") and os.path.isdir("/mnt/app-data"):
        return artifacts_dir
    os.makedirs(artifacts_dir, exist_ok=True)
    logger.info("user artifacts dir ready: %s", artifacts_dir)
    return artifacts_dir


def get_user_skills_dir(user_id: str | None) -> str:
    """Logical path for user skills (Runtime /mnt/workspace only).

    Web UI discovers skill-creator skills via S3
    (``agentcore-sessions/{user}/skills/``), not under app-data.
    """
    segment = sanitize_user_path_segment(user_id) or "default"
    root = "/mnt/workspace" if os.path.isdir("/mnt/workspace") else SESSION_STORAGE_DIR
    return os.path.join(root, segment, "skills")


def ensure_user_skills_dir(user_id: str | None) -> str:
    """Create user skills dir under the Runtime workspace mount when available."""
    skills_dir = get_user_skills_dir(user_id)
    # ECS mounts app-data only — do not create a misleading skills/ tree there.
    if not os.path.isdir("/mnt/workspace") and os.path.isdir("/mnt/app-data"):
        return skills_dir
    os.makedirs(skills_dir, exist_ok=True)
    logger.info("user skills dir ready: %s", skills_dir)
    return skills_dir


def get_user_graph_dir(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/graph (does not create)."""
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        segment = "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "graph")


def ensure_user_graph_dir(user_id: str | None) -> str:
    """Create session graph workspace: corpus/ + out/ (shared extract+publish).

    Returns the graph root: {SESSION_STORAGE_DIR}/{user_id}/graph
    """
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        raise ValueError(
            "Invalid user_id for graph path; expected a plain user id, "
            "not a signed session cookie"
        )
    graph_dir = os.path.join(SESSION_STORAGE_DIR, segment, "graph")
    for name in ("corpus", "out"):
        os.makedirs(os.path.join(graph_dir, name), exist_ok=True)
    logger.info("user graph dir ready: %s", graph_dir)
    return graph_dir


def user_graph_html_path(user_id: str | None) -> str:
    """Published HTML: {SESSION_STORAGE_DIR}/{user_id}/graph/out/graph.html"""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "graph", "out", "graph.html")

def get_user_ess_dir(user_id: str | None) -> str:
    """Per-user ESS root: ``{SESSION_STORAGE_DIR}/{user_id}/ess``."""
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        segment = "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "ess")


def _ensure_ess_on_path() -> str:
    """Put ``ess-work/ess`` on ``sys.path`` so ``doc_list`` is importable."""
    ess_pkg = os.path.join(os.path.dirname(script_dir), "ess")
    if ess_pkg not in sys.path:
        sys.path.insert(0, ess_pkg)
    return ess_pkg


def ensure_user_ess_dir(user_id: str | None) -> str:
    """Create ``{user}/ess``, ``regulations/``, ``projects/``, ``out/``, … and return ESS root."""
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        raise ValueError(
            "Invalid user_id for ess path; expected a plain user id, "
            "not a signed session cookie"
        )
    ess_dir = os.path.join(SESSION_STORAGE_DIR, segment, "ess")
    for name in (
        "",
        "regulations",
        "projects",
        "drawings",
        "test_cases",
        "out",
        os.path.join("out", "converted"),
        os.path.join("out", "converted", ".pdf_pages"),
    ):
        os.makedirs(os.path.join(ess_dir, name) if name else ess_dir, exist_ok=True)
    try:
        _ensure_ess_on_path()
        from doc_list import (
            DRAWINGS,
            PROJECTS,
            TEST_CASES,
            doc_list_path,
            empty_doc_list,
            migrate_raw_to_docs,
            save_doc_list,
            sync_doc_list_with_filesystem,
        )

        migrate_raw_to_docs(ess_dir)
        if not doc_list_path(ess_dir).is_file():
            docs = os.path.join(ess_dir, "regulations")
            has_files = os.path.isdir(docs) and any(
                os.path.isfile(os.path.join(docs, n)) for n in os.listdir(docs)
            )
            if has_files:
                sync_doc_list_with_filesystem(ess_dir, user_id=segment)
            else:
                save_doc_list(ess_dir, empty_doc_list(user_id=segment))
        if not doc_list_path(ess_dir, PROJECTS).is_file():
            projects = os.path.join(ess_dir, "projects")
            has_projects = os.path.isdir(projects) and any(
                os.path.isfile(os.path.join(projects, n)) for n in os.listdir(projects)
            )
            if has_projects:
                sync_doc_list_with_filesystem(
                    ess_dir, user_id=segment, registry=PROJECTS
                )
            else:
                save_doc_list(
                    ess_dir, empty_doc_list(user_id=segment), registry=PROJECTS
                )
        if not doc_list_path(ess_dir, DRAWINGS).is_file():
            drawings = os.path.join(ess_dir, "drawings")
            has_drawings = os.path.isdir(drawings) and any(
                os.path.isfile(os.path.join(drawings, n)) for n in os.listdir(drawings)
            )
            if has_drawings:
                sync_doc_list_with_filesystem(
                    ess_dir, user_id=segment, registry=DRAWINGS
                )
            else:
                save_doc_list(
                    ess_dir, empty_doc_list(user_id=segment), registry=DRAWINGS
                )
        if not doc_list_path(ess_dir, TEST_CASES).is_file():
            test_cases = os.path.join(ess_dir, "test_cases")
            has_tc = os.path.isdir(test_cases) and any(
                os.path.isfile(os.path.join(test_cases, n)) for n in os.listdir(test_cases)
            )
            if has_tc:
                sync_doc_list_with_filesystem(
                    ess_dir, user_id=segment, registry=TEST_CASES
                )
            else:
                save_doc_list(
                    ess_dir, empty_doc_list(user_id=segment), registry=TEST_CASES
                )
    except Exception:
        logger.debug("ess doc_list ensure skipped", exc_info=True)
    logger.debug("user ess dir ready: %s", ess_dir)
    return ess_dir


def ess_converted_dir(user_id: str | None = None) -> str:
    """``{SESSION_STORAGE}/{user}/ess/out/converted``."""
    return os.path.join(ess_out_dir(user_id), "converted")


def ess_docs_dir(user_id: str | None = None) -> str:
    """``{SESSION_STORAGE}/{user}/ess/regulations`` (legacy: ``docs``, ``raw``)."""
    ess = get_user_ess_dir(user_id)
    docs = os.path.join(ess, "regulations")
    legacy_docs = os.path.join(ess, "docs")
    legacy_raw = os.path.join(ess, "raw")
    if not os.path.isdir(docs) and (
        os.path.isdir(legacy_docs) or os.path.isdir(legacy_raw)
    ):
        try:
            _ensure_ess_on_path()
            from doc_list import migrate_raw_to_docs

            migrate_raw_to_docs(ess)
        except Exception:
            pass
    return docs


def ess_raw_dir(user_id: str | None = None) -> str:
    """Deprecated alias for :func:`ess_docs_dir`."""
    return ess_docs_dir(user_id)


def ess_out_dir(user_id: str | None = None) -> str:
    return os.path.join(get_user_ess_dir(user_id), "out")


def ess_doc_list_path(user_id: str | None = None) -> str:
    return os.path.join(get_user_ess_dir(user_id), "regulations_list.json")


def ess_projects_dir(user_id: str | None = None) -> str:
    """``{SESSION_STORAGE}/{user}/ess/projects``."""
    return os.path.join(get_user_ess_dir(user_id), "projects")


def ess_project_list_path(user_id: str | None = None) -> str:
    return os.path.join(get_user_ess_dir(user_id), "project_list.json")


def ess_drawings_dir(user_id: str | None = None) -> str:
    """``{SESSION_STORAGE}/{user}/ess/drawings``."""
    return os.path.join(get_user_ess_dir(user_id), "drawings")


def ess_drawings_list_path(user_id: str | None = None) -> str:
    return os.path.join(get_user_ess_dir(user_id), "drawings_list.json")


def ess_test_cases_dir(user_id: str | None = None) -> str:
    """``{SESSION_STORAGE}/{user}/ess/test_cases``."""
    return os.path.join(get_user_ess_dir(user_id), "test_cases")


def ess_test_cases_list_path(user_id: str | None = None) -> str:
    return os.path.join(get_user_ess_dir(user_id), "test_cases_list.json")


def _ess_docs_dest_path(docs_dir: str, filename: str) -> tuple[str, str, str]:
    """Return ``(dest_path, sanitized_name, original_basename)``.

    Sanitizes at upload time (spaces → ``_``, unsafe chars stripped).
    """
    original = os.path.basename((filename or "").strip()) or "upload.bin"
    original = original.replace("\x00", "_") or "upload.bin"
    try:
        _ensure_ess_on_path()
        from doc_list import sanitize_ess_filename

        safe = sanitize_ess_filename(original)
    except Exception:
        safe = original.replace(" ", "_")
        safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in safe)
        while "__" in safe:
            safe = safe.replace("__", "_")
        stem, ext = os.path.splitext(safe)
        safe = f"{stem.strip('._-') or 'document'}{ext.lower()}"
    return os.path.join(docs_dir, safe), safe, original


def save_ess_doc_upload(
    filename: str,
    data: bytes,
    *,
    user_id: str | None = None,
) -> dict[str, object]:
    """Sanitize filename, write into ``{user}/ess/regulations``, update regulations_list."""
    if data is None or len(data) == 0:
        raise ValueError("저장할 파일이 없습니다.")

    ess = ensure_user_ess_dir(user_id)
    docs = os.path.join(ess, "regulations")
    os.makedirs(docs, exist_ok=True)
    dest, safe_name, original_name = _ess_docs_dest_path(docs, filename)
    overwritten = os.path.isfile(dest)
    with open(dest, "wb") as f:
        f.write(data)

    segment = sanitize_user_path_segment(user_id) or "default"
    try:
        _ensure_ess_on_path()
        from doc_list import upsert_document

        upsert_document(
            ess,
            filename=safe_name,
            source_path=os.path.abspath(dest),
            bytes_size=len(data),
            status="uploaded",
            user_id=segment,
            extra={
                "original_filename": original_name,
                "sanitized": original_name != safe_name,
            },
        )
    except Exception:
        logger.exception("Failed to update ess doc_list after upload")

    logger.info(
        "ess docs upload user=%s → %s (original=%s, %s bytes%s)",
        segment,
        dest,
        original_name,
        len(data),
        ", overwrite" if overwritten else "",
    )
    return {
        "ess_dir": ess,
        "docs_dir": docs,
        "raw_dir": docs,  # backward-compatible key
        "saved": {
            "name": safe_name,
            "original_filename": original_name,
            "sanitized": original_name != safe_name,
            "path": dest,
            "bytes": len(data),
            "overwritten": overwritten,
        },
        "count": 1,
        "doc_list": ess_doc_list_path(user_id),
    }


def save_ess_raw_upload(
    filename: str,
    data: bytes,
    *,
    user_id: str | None = None,
) -> dict[str, object]:
    """Deprecated alias for :func:`save_ess_doc_upload`."""
    return save_ess_doc_upload(filename, data, user_id=user_id)


def save_ess_project_upload(
    filename: str,
    data: bytes,
    *,
    user_id: str | None = None,
) -> dict[str, object]:
    """Sanitize filename, write into ``{user}/ess/projects``, update project_list."""
    if data is None or len(data) == 0:
        raise ValueError("저장할 파일이 없습니다.")

    ess = ensure_user_ess_dir(user_id)
    projects = os.path.join(ess, "projects")
    os.makedirs(projects, exist_ok=True)
    dest, safe_name, original_name = _ess_docs_dest_path(projects, filename)
    overwritten = os.path.isfile(dest)
    with open(dest, "wb") as f:
        f.write(data)

    segment = sanitize_user_path_segment(user_id) or "default"
    try:
        _ensure_ess_on_path()
        from doc_list import PROJECTS, upsert_document

        upsert_document(
            ess,
            filename=safe_name,
            source_path=os.path.abspath(dest),
            bytes_size=len(data),
            status="uploaded",
            user_id=segment,
            extra={
                "original_filename": original_name,
                "sanitized": original_name != safe_name,
            },
            registry=PROJECTS,
        )
    except Exception:
        logger.exception("Failed to update ess project_list after upload")

    logger.info(
        "ess projects upload user=%s → %s (original=%s, %s bytes%s)",
        segment,
        dest,
        original_name,
        len(data),
        ", overwrite" if overwritten else "",
    )
    return {
        "ess_dir": ess,
        "projects_dir": projects,
        "docs_dir": projects,
        "raw_dir": projects,
        "saved": {
            "name": safe_name,
            "original_filename": original_name,
            "sanitized": original_name != safe_name,
            "path": dest,
            "bytes": len(data),
            "overwritten": overwritten,
        },
        "count": 1,
        "doc_list": ess_project_list_path(user_id),
        "project_list": ess_project_list_path(user_id),
    }


def save_ess_drawing_upload(
    filename: str,
    data: bytes,
    *,
    user_id: str | None = None,
) -> dict[str, object]:
    """Sanitize filename, write into ``{user}/ess/drawings``, update drawings_list."""
    if data is None or len(data) == 0:
        raise ValueError("저장할 파일이 없습니다.")

    ess = ensure_user_ess_dir(user_id)
    drawings = os.path.join(ess, "drawings")
    os.makedirs(drawings, exist_ok=True)
    dest, safe_name, original_name = _ess_docs_dest_path(drawings, filename)
    overwritten = os.path.isfile(dest)
    with open(dest, "wb") as f:
        f.write(data)

    segment = sanitize_user_path_segment(user_id) or "default"
    try:
        _ensure_ess_on_path()
        from doc_list import DRAWINGS, upsert_document

        upsert_document(
            ess,
            filename=safe_name,
            source_path=os.path.abspath(dest),
            bytes_size=len(data),
            status="uploaded",
            user_id=segment,
            extra={
                "original_filename": original_name,
                "sanitized": original_name != safe_name,
            },
            registry=DRAWINGS,
        )
    except Exception:
        logger.exception("Failed to update ess drawings_list after upload")

    logger.info(
        "ess drawings upload user=%s → %s (original=%s, %s bytes%s)",
        segment,
        dest,
        original_name,
        len(data),
        ", overwrite" if overwritten else "",
    )
    return {
        "ess_dir": ess,
        "drawings_dir": drawings,
        "docs_dir": drawings,
        "raw_dir": drawings,
        "saved": {
            "name": safe_name,
            "original_filename": original_name,
            "sanitized": original_name != safe_name,
            "path": dest,
            "bytes": len(data),
            "overwritten": overwritten,
        },
        "count": 1,
        "doc_list": ess_drawings_list_path(user_id),
        "drawings_list": ess_drawings_list_path(user_id),
    }


def save_ess_testcase(
    xlsx_path: str,
    *,
    user_id: str | None = None,
    cases_json_path: str | None = None,
    title: str | None = None,
    standard: str | None = None,
    source_md: str | None = None,
    rows: int | None = None,
    filename: str | None = None,
) -> dict[str, object]:
    """Copy a generated test-case xlsx into ``{user}/ess/test_cases`` and update list.

    Also copies optional cases JSON as ``{stem}.json`` sidecar and upserts
    ``test_cases_list.json`` (same shape as ``project_list.json``).
    """
    src = os.path.abspath(os.path.expanduser(xlsx_path or ""))
    if not src or not os.path.isfile(src):
        raise ValueError(f"테스트케이스 파일이 없습니다: {xlsx_path}")

    ess = ensure_user_ess_dir(user_id)
    tc_dir = os.path.join(ess, "test_cases")
    os.makedirs(tc_dir, exist_ok=True)

    preferred = filename or os.path.basename(src)
    dest, safe_name, original_name = _ess_docs_dest_path(tc_dir, preferred)
    if not safe_name.lower().endswith(".xlsx"):
        safe_name = f"{os.path.splitext(safe_name)[0]}.xlsx"
        dest = os.path.join(tc_dir, safe_name)
    overwritten = os.path.isfile(dest)

    with open(src, "rb") as f:
        data = f.read()
    if not data:
        raise ValueError("저장할 파일이 비어 있습니다.")
    with open(dest, "wb") as f:
        f.write(data)

    stem = os.path.splitext(safe_name)[0]
    json_dest: str | None = None
    meta_title = title
    meta_standard = standard
    meta_source_md = source_md
    meta_rows = rows

    cases_src = cases_json_path
    if cases_src:
        cases_src = os.path.abspath(os.path.expanduser(cases_src))
    if cases_src and os.path.isfile(cases_src):
        try:
            import json as _json

            with open(cases_src, encoding="utf-8") as cf:
                payload = _json.load(cf)
            if isinstance(payload, dict):
                meta_title = meta_title or payload.get("title")
                meta_standard = meta_standard or payload.get("standard")
                meta_source_md = meta_source_md or payload.get("source_md")
                cases = payload.get("cases")
                if meta_rows is None and isinstance(cases, list):
                    meta_rows = len(cases)
        except Exception:
            logger.debug("cases json metadata parse skipped", exc_info=True)
        json_dest = os.path.join(tc_dir, f"{stem}.json")
        try:
            with open(cases_src, "rb") as f:
                json_bytes = f.read()
            with open(json_dest, "wb") as f:
                f.write(json_bytes)
        except OSError:
            logger.exception("Failed to copy cases json sidecar")
            json_dest = None

    segment = sanitize_user_path_segment(user_id) or "default"
    extra: dict[str, object] = {
        "original_filename": original_name,
        "sanitized": original_name != safe_name,
    }
    if meta_title:
        extra["title"] = str(meta_title)
    if meta_standard:
        extra["standard"] = str(meta_standard)
    if meta_source_md:
        extra["source_md"] = str(meta_source_md)
    if meta_rows is not None:
        extra["rows"] = int(meta_rows)

    try:
        _ensure_ess_on_path()
        from doc_list import TEST_CASES, upsert_document

        upsert_document(
            ess,
            filename=safe_name,
            source_path=os.path.abspath(dest),
            bytes_size=len(data),
            status="saved",
            user_id=segment,
            json_path=os.path.abspath(json_dest) if json_dest else None,
            extra=extra,
            registry=TEST_CASES,
        )
    except Exception:
        logger.exception("Failed to update ess test_cases_list after save")

    logger.info(
        "ess test_cases save user=%s → %s (original=%s, %s bytes%s)",
        segment,
        dest,
        original_name,
        len(data),
        ", overwrite" if overwritten else "",
    )
    return {
        "ess_dir": ess,
        "test_cases_dir": tc_dir,
        "saved": {
            "name": safe_name,
            "original_filename": original_name,
            "sanitized": original_name != safe_name,
            "path": dest,
            "json_path": json_dest,
            "bytes": len(data),
            "overwritten": overwritten,
            "title": meta_title,
            "standard": meta_standard,
            "source_md": meta_source_md,
            "rows": meta_rows,
        },
        "count": 1,
        "test_cases_list": ess_test_cases_list_path(user_id),
    }


def list_ess_doc_files(user_id: str | None = None) -> list[dict[str, object]]:
    """List files currently under the user's ``ess/regulations``."""
    docs = ess_docs_dir(user_id)
    if not os.path.isdir(docs):
        return []
    out: list[dict[str, object]] = []
    try:
        names = sorted(os.listdir(docs))
    except OSError:
        return []
    for name in names:
        path = os.path.join(docs, name)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        out.append({"name": name, "path": path, "bytes": size, "mtime": mtime})
    return out


def list_ess_project_files(user_id: str | None = None) -> list[dict[str, object]]:
    """List files currently under the user's ``ess/projects``."""
    projects = ess_projects_dir(user_id)
    if not os.path.isdir(projects):
        return []
    out: list[dict[str, object]] = []
    try:
        names = sorted(os.listdir(projects))
    except OSError:
        return []
    for name in names:
        path = os.path.join(projects, name)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        out.append({"name": name, "path": path, "bytes": size, "mtime": mtime})
    return out


def list_ess_drawing_files(user_id: str | None = None) -> list[dict[str, object]]:
    """List files currently under the user's ``ess/drawings``."""
    drawings = ess_drawings_dir(user_id)
    if not os.path.isdir(drawings):
        return []
    out: list[dict[str, object]] = []
    try:
        names = sorted(os.listdir(drawings))
    except OSError:
        return []
    for name in names:
        path = os.path.join(drawings, name)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        out.append({"name": name, "path": path, "bytes": size, "mtime": mtime})
    return out


def list_ess_raw_files(user_id: str | None = None) -> list[dict[str, object]]:
    """Deprecated alias for :func:`list_ess_doc_files`."""
    return list_ess_doc_files(user_id)


def is_ess_foundation_model_parser_enabled(user_id: str | None) -> bool:
    """True when ESS Foundation Model Parser is on (default True)."""
    return bool(
        load_user_settings(user_id).get(
            "ess_foundation_model_parser_enabled", True
        )
    )


def set_ess_foundation_model_parser_enabled(
    enabled: bool, *, user_id: str | None = None
) -> bool:
    settings = save_user_settings(
        user_id, ess_foundation_model_parser_enabled=bool(enabled)
    )
    return bool(settings.get("ess_foundation_model_parser_enabled", True))


# Extract caches are not needed for Runtime recall_graph_memory.
_GRAPH_MIRROR_SKIP_DIR_NAMES = frozenset({"cache", "graphify-out"})
_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
)


@contextmanager
def _without_env_proxies():
    """Drop HTTP(S)_PROXY for the block (Cursor agent proxies break local boto3)."""
    saved = {key: os.environ.pop(key, None) for key in _PROXY_ENV_KEYS}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def sync_user_graph_to_runtime_storage(user_id: str | None) -> dict[str, int]:
    """Mirror ECS/local graph → S3 agentcore-sessions for AgentCore Runtime.

    Knowledge graphs live on app-data (``SESSION_STORAGE_DIR`` / ``app-data/``).
    AgentCore Runtime only mounts ``agentcore-sessions/`` at ``/mnt/workspace``,
    so ``recall_graph_memory`` cannot see app-data. After each successful
    pipeline/publish, upload ``{user}/graph/`` to
    ``s3://{bucket}/agentcore-sessions/{user}/graph/`` so Runtime can read
    ``/mnt/workspace/{user}/graph/out/graph.json``.

    Returns counts: ``{"uploaded": N, "deleted": M}``. Missing graph or S3
    config → empty counts (logged, not raised).
    """
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        return {"uploaded": 0, "deleted": 0}

    graph_root = get_user_graph_dir(user_id)
    graph_json = os.path.join(graph_root, "out", "graph.json")
    if not os.path.isfile(graph_json):
        logger.info(
            "skip graph→runtime mirror: no graph.json for %s at %s",
            segment,
            graph_json,
        )
        return {"uploaded": 0, "deleted": 0}

    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    bucket = (cfg.get("s3_bucket") if isinstance(cfg, dict) else None) or s3_bucket
    region = (cfg.get("region") if isinstance(cfg, dict) else None) or bedrock_region
    if not bucket:
        logger.warning("skip graph→runtime mirror: s3_bucket not configured")
        return {"uploaded": 0, "deleted": 0}

    dest_prefix = f"{S3_FILES_SESSION_PREFIX}/{segment}/graph/"
    local_files: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(graph_root):
        dirnames[:] = [d for d in dirnames if d not in _GRAPH_MIRROR_SKIP_DIR_NAMES]
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, graph_root).replace(os.sep, "/")
            local_files[rel] = abs_path

    if not local_files:
        return {"uploaded": 0, "deleted": 0}

    uploaded = 0
    failed = 0
    deleted = 0
    # Local uvicorn often inherits Cursor's ephemeral HTTP(S)_PROXY
    # (127.0.0.1:61xxx). That proxy dies with the agent session and breaks
    # every boto3 upload — clear env proxies for this sync only.
    with _without_env_proxies():
        s3 = boto3.client("s3", region_name=region)
        for rel, abs_path in sorted(local_files.items()):
            key = f"{dest_prefix}{rel}"
            try:
                s3.upload_file(abs_path, bucket, key)
                uploaded += 1
            except Exception as e:
                failed += 1
                logger.warning("graph mirror upload failed %s: %s", key, e)
        if failed:
            logger.warning(
                "graph→runtime mirror incomplete user=%s uploaded=%s failed=%s",
                segment,
                uploaded,
                failed,
            )

        try:
            paginator = s3.get_paginator("list_objects_v2")
            remote_keys: list[str] = []
            for page in paginator.paginate(Bucket=bucket, Prefix=dest_prefix):
                for obj in page.get("Contents") or []:
                    key = obj.get("Key") or ""
                    if key and not key.endswith("/"):
                        remote_keys.append(key)
            keep = {f"{dest_prefix}{rel}" for rel in local_files}
            stale = [key for key in remote_keys if key not in keep]
            for key in stale:
                try:
                    s3.delete_object(Bucket=bucket, Key=key)
                    deleted += 1
                except Exception as e:
                    logger.warning("graph mirror delete failed %s: %s", key, e)
        except Exception as e:
            logger.warning("graph mirror list/delete skipped for %s: %s", segment, e)

    logger.info(
        "Mirrored graph → runtime storage user=%s uploaded=%s deleted=%s prefix=s3://%s/%s",
        segment,
        uploaded,
        deleted,
        bucket,
        dest_prefix,
    )
    return {"uploaded": uploaded, "deleted": deleted}

GRAPH_PATTERNS = ("pattern1", "pattern2", "pattern3")
DEFAULT_GRAPH_PATTERN = "pattern1"

_DEFAULT_USER_SETTINGS: dict[str, object] = {
    "knowledge_graph_enabled": True,
    "graph_pattern": DEFAULT_GRAPH_PATTERN,
    "foundation_model_parser_enabled": False,
    # ESS Configure: Foundation Model Parser (default On).
    "ess_foundation_model_parser_enabled": True,
}


def normalize_graph_pattern(value: object | None) -> str:
    raw = str(value or "").strip().lower().replace(" ", "").replace("_", "")
    aliases = {
        "pattern1": "pattern1",
        "p1": "pattern1",
        "1": "pattern1",
        "forceatlas": "pattern1",
        "pattern2": "pattern2",
        "p2": "pattern2",
        "2": "pattern2",
        "neo4j": "pattern2",
        "neo4jexplore": "pattern2",
        "pattern3": "pattern3",
        "p3": "pattern3",
        "3": "pattern3",
        "holistic": "pattern3",
        "holisticview": "pattern3",
    }
    return aliases.get(raw, DEFAULT_GRAPH_PATTERN)


def get_user_db_path(user_id: str | None) -> str:
    """Durable per-user tasks/messages DB: {SESSION_STORAGE_DIR}/{user_id}/{user_id}.db."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, f"{segment}.db")


def get_user_settings_path(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/settings.json (does not create)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "settings.json")


def _normalize_string_list(value: object) -> list[str]:
    """Return a cleaned list of non-empty strings (stable order, no duplicates)."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def load_user_settings(user_id: str | None) -> dict[str, object]:
    """Load per-user UI/feature settings. Missing file → defaults (KG on).

    ``skills`` / ``mcp_servers`` are omitted until the user has saved them so
    callers can fall back to favorite_tools.json.
    """
    settings = dict(_DEFAULT_USER_SETTINGS)
    path = get_user_settings_path(user_id)
    if not os.path.isfile(path):
        return settings
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            if "knowledge_graph_enabled" in raw:
                settings["knowledge_graph_enabled"] = bool(raw["knowledge_graph_enabled"])
            if "graph_pattern" in raw:
                settings["graph_pattern"] = normalize_graph_pattern(raw.get("graph_pattern"))
            if "foundation_model_parser_enabled" in raw:
                settings["foundation_model_parser_enabled"] = bool(
                    raw["foundation_model_parser_enabled"]
                )
            if "ess_foundation_model_parser_enabled" in raw:
                settings["ess_foundation_model_parser_enabled"] = bool(
                    raw["ess_foundation_model_parser_enabled"]
                )
            if "skills" in raw:
                settings["skills"] = _normalize_string_list(raw.get("skills"))
            if "mcp_servers" in raw:
                settings["mcp_servers"] = _normalize_string_list(raw.get("mcp_servers"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load user settings %s: %s", path, e)
    return settings


def save_user_settings(user_id: str | None, **updates: object) -> dict[str, object]:
    """Merge updates into per-user settings.json and return the full settings."""
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        raise ValueError(
            "Invalid user_id for settings path; expected a plain user id, "
            "not a signed session cookie"
        )
    user_dir = os.path.join(SESSION_STORAGE_DIR, segment)
    os.makedirs(user_dir, exist_ok=True)
    settings = load_user_settings(user_id)
    for key, value in updates.items():
        if key == "knowledge_graph_enabled":
            settings[key] = bool(value)
        elif key == "graph_pattern":
            settings[key] = normalize_graph_pattern(value)
        elif key == "foundation_model_parser_enabled":
            settings[key] = bool(value)
        elif key == "ess_foundation_model_parser_enabled":
            settings[key] = bool(value)
        elif key == "skills":
            settings[key] = _normalize_string_list(value)
        elif key == "mcp_servers":
            settings[key] = _normalize_string_list(value)
    path = get_user_settings_path(user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    logger.info("user settings saved: %s -> %s", path, settings)
    return settings


def is_knowledge_graph_enabled(user_id: str | None) -> bool:
    """True when Knowledge Graph feature is on (default)."""
    return bool(load_user_settings(user_id).get("knowledge_graph_enabled", True))


def is_foundation_model_parser_enabled(user_id: str | None) -> bool:
    """True when Wiki Sync uses multimodal PDF→images→LLM (default: False)."""
    return bool(
        load_user_settings(user_id).get("foundation_model_parser_enabled", False)
    )


def set_foundation_model_parser_enabled(
    enabled: bool, *, user_id: str | None
) -> bool:
    """Persist Foundation Model Parser toggle; returns the stored value."""
    settings = save_user_settings(
        user_id, foundation_model_parser_enabled=bool(enabled)
    )
    return bool(settings.get("foundation_model_parser_enabled", False))





def is_hybrid_graph_search_enabled() -> bool:
    """True when config.json hybrid_graph_search is enable (embedding vector search)."""
    cfg = load_config() or {}
    raw = str(cfg.get("hybrid_graph_search") or "").strip().lower()
    return raw in {"enable", "enabled", "on", "true", "1", "yes"}


def get_graph_pattern(user_id: str | None) -> str:
    """Selected Knowledge Graph HTML pattern (pattern1|pattern2|pattern3)."""
    return normalize_graph_pattern(
        load_user_settings(user_id).get("graph_pattern", DEFAULT_GRAPH_PATTERN)
    )



def get_user_skills_list_path(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/skills.list (does not create)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "skills.list")


def _list_skill_dir_names(skills_dir: str) -> list[str]:
    """Return subdirectory names that contain SKILL.md."""
    if not os.path.isdir(skills_dir):
        return []
    names: list[str] = []
    try:
        entries = sorted(os.listdir(skills_dir))
    except OSError as e:
        logger.warning("Failed to list skills directory %s: %s", skills_dir, e)
        return []
    for entry in entries:
        if os.path.isfile(os.path.join(skills_dir, entry, "SKILL.md")):
            names.append(entry)
    return names


def _list_user_skill_names_from_s3(user_id: str | None) -> list[str]:
    """List skill-creator skill dirs under s3://{bucket}/agentcore-sessions/{user}/skills/.

    ECS mounts app-data only; user skills always come from this S3 prefix.
    Only directories that contain SKILL.md are included.
    """
    if not user_id:
        return []
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        return []
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    bucket = (cfg.get("s3_bucket") if isinstance(cfg, dict) else None) or globals().get(
        "s3_bucket"
    )
    region = (cfg.get("region") if isinstance(cfg, dict) else None) or globals().get(
        "bedrock_region", "us-west-2"
    )
    if not bucket:
        # Fall back to local workspace mount when present (local/runtime).
        return _list_skill_dir_names(get_user_skills_dir(user_id))

    prefix = f"{S3_FILES_SESSION_PREFIX}/{segment}/skills/"
    try:
        s3 = boto3.client("s3", region_name=region)
        paginator = s3.get_paginator("list_objects_v2")
        names: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
            for entry in page.get("CommonPrefixes") or []:
                child = (entry.get("Prefix") or "").rstrip("/")
                name = child.rsplit("/", 1)[-1] if child else ""
                if name:
                    names.append(name)

        confirmed: list[str] = []
        for name in sorted(names):
            key = f"{prefix}{name}/SKILL.md"
            try:
                s3.head_object(Bucket=bucket, Key=key)
            except Exception:
                continue
            confirmed.append(name)
            logger.info("Skill discovered (s3): %s", name)
        return confirmed
    except Exception as e:
        logger.warning("Failed to list user skills from S3 for %s: %s", user_id, e)
        return _list_skill_dir_names(get_user_skills_dir(user_id))


def _load_skills_list_file(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    except FileNotFoundError:
        return []
    except OSError as e:
        logger.warning("Failed to read skills.list %s: %s", path, e)
        return []


def _seed_skill_names(user_id: str | None) -> list[str]:
    """Builtin application/skills.list + skill-creator skills from S3 session prefix."""
    default_path = os.path.join(script_dir, "skills.list")
    builtin = _load_skills_list_file(default_path)
    user_skills = _list_user_skill_names_from_s3(user_id)
    merged: list[str] = []
    seen: set[str] = set()
    for name in builtin + user_skills:
        if name not in seen:
            merged.append(name)
            seen.add(name)
    return merged


def write_user_skills_list(user_id: str | None, names: list[str] | None = None) -> str:
    """Write {SESSION_STORAGE_DIR}/{user_id}/skills.list and return its path."""
    path = get_user_skills_list_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    merged = names if names is not None else _seed_skill_names(user_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(merged) + ("\n" if merged else ""))
    logger.info(
        "wrote user skills.list (%d skills) -> %s",
        len(merged),
        path,
    )
    return path


def update_user_skills_list(user_id: str | None) -> str:
    """Rewrite per-user skills.list from application/skills.list + S3 user skills."""
    return write_user_skills_list(user_id)


def ensure_user_skills_list(user_id: str | None) -> str:
    """Sync skills.list to builtins + S3 agentcore-sessions/{user}/skills/.

    ECS mounts app-data only; user-created skills are listed via S3 API, not the
    local mount. Builtin names come from ``application/skills.list``.
    """
    path = get_user_skills_list_path(user_id)
    desired = _seed_skill_names(user_id)
    existing = _load_skills_list_file(path) if os.path.isfile(path) else []
    if existing == desired:
        logger.info(
            "user skills.list up to date (%d skills) -> %s",
            len(existing),
            path,
        )
        return path
    return write_user_skills_list(user_id, desired)


def load_config():
    config = None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        config = {}
        config['projectName'] = "agentcore"

        session = boto3.Session()
        bedrock_region = session.region_name
        config['region'] = bedrock_region
        
        sts = boto3.client("sts")
        accountId = sts.get_caller_identity()["Account"]
        config['accountId'] = accountId
        
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    return config


def load_favorite_tools() -> dict[str, list[str]]:
    fallback = {"MCP": [], "SKILL": []}
    try:
        with open(favorite_tools_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("favorite_tools.json not found: %s", favorite_tools_path)
        return fallback
    except Exception as e:
        logger.warning("Failed to load favorite_tools.json: %s", e)
        return fallback

    if not isinstance(data, dict):
        return fallback

    favorites: dict[str, list[str]] = {}
    for key in ("MCP", "SKILL"):
        values = data.get(key, [])
        if isinstance(values, list):
            favorites[key] = [v for v in values if isinstance(v, str) and v.strip()]
        else:
            favorites[key] = []
    return favorites


def get_initial_tool_defaults() -> tuple[list[str], list[str]]:
    favorite_tools = load_favorite_tools()
    default_skills = favorite_tools.get("SKILL") or []
    default_mcp_servers = favorite_tools.get("MCP") or []
    return default_skills, default_mcp_servers


def get_user_tool_defaults(user_id: str | None) -> tuple[list[str], list[str]]:
    """Per-user skill/MCP defaults from settings.json, else favorite_tools.json."""
    fav_skills, fav_mcp = get_initial_tool_defaults()
    settings = load_user_settings(user_id)
    skills = settings.get("skills")
    mcp_servers = settings.get("mcp_servers")
    return (
        list(skills) if isinstance(skills, list) else fav_skills,
        list(mcp_servers) if isinstance(mcp_servers, list) else fav_mcp,
    )


def save_user_tool_defaults(
    user_id: str | None,
    *,
    skills: list[str] | None = None,
    mcp_servers: list[str] | None = None,
) -> dict[str, object]:
    """Persist the user's last skill/MCP selection into settings.json."""
    updates: dict[str, object] = {}
    if skills is not None:
        updates["skills"] = skills
    if mcp_servers is not None:
        updates["mcp_servers"] = mcp_servers
    if not updates:
        return load_user_settings(user_id)
    return save_user_settings(user_id, **updates)

config = load_config()

bedrock_region = config['region']
projectName = config['projectName']
accountId = config['accountId']

s3_bucket = config.get('s3_bucket')
s3_prefix = "docs"
s3_image_prefix = "images"
sharing_url = config.get('sharing_url', '')
knowledge_base_id = config.get('knowledge_base_id')
data_source_id = config.get('data_source_id')


def get_contents_type(file_name: str) -> str:
    lower = file_name.lower()
    if lower.endswith((".jpg", ".jpeg")):
        content_type = "image/jpeg"
    elif lower.endswith(".png"):
        content_type = "image/png"
    elif lower.endswith(".webp"):
        content_type = "image/webp"
    elif lower.endswith(".gif"):
        content_type = "image/gif"
    elif lower.endswith(".pdf"):
        content_type = "application/pdf"
    elif lower.endswith(".txt"):
        content_type = "text/plain"
    elif lower.endswith(".csv"):
        content_type = "text/csv"
    elif lower.endswith((".ppt", ".pptx")):
        content_type = "application/vnd.ms-powerpoint"
    elif lower.endswith((".doc", ".docx")):
        content_type = "application/msword"
    elif lower.endswith(".xls"):
        content_type = "application/vnd.ms-excel"
    elif lower.endswith(".py"):
        content_type = "text/x-python"
    elif lower.endswith(".js"):
        content_type = "application/javascript"
    elif lower.endswith(".md"):
        content_type = "text/markdown"
    elif lower.endswith((".html", ".htm")):
        content_type = "text/html; charset=utf-8"
    else:
        content_type = "no info"
    return content_type


def _sanitize_s3_user_segment(user_id: str | None) -> str | None:
    """Return a safe single path segment for per-user S3 folders, or None."""
    return sanitize_user_path_segment(user_id)


def upload_to_s3(
    file_bytes: bytes,
    file_name: str,
    user_id: str | None = None,
) -> dict | None:
    """Upload a file to S3 under docs/ (or images/) and return upload metadata.

    When ``user_id`` is provided, the object key becomes
    ``{prefix}/{user_id}/{file_name}`` so each user has a separate folder.
    """
    if not s3_bucket:
        logger.error("s3_bucket is not configured")
        return None

    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        content_type = get_contents_type(file_name)
        logger.info("content_type: %s", content_type)

        if content_type.startswith("image/"):
            prefix = s3_image_prefix
        else:
            prefix = s3_prefix

        user_segment = _sanitize_s3_user_segment(user_id)
        if user_segment:
            s3_key = f"{prefix}/{user_segment}/{file_name}"
            relative_url_path = f"{prefix}/{parse.quote(user_segment)}/{parse.quote(file_name)}"
        else:
            s3_key = f"{prefix}/{file_name}"
            relative_url_path = f"{prefix}/{parse.quote(file_name)}"
        user_meta = {"content_type": content_type}

        put_params = {
            "Bucket": s3_bucket,
            "Key": s3_key,
            "Metadata": user_meta,
            "Body": file_bytes,
            "CacheControl": "no-cache, max-age=0, must-revalidate",
        }
        if content_type != "no info":
            put_params["ContentType"] = content_type
        if content_type == "application/pdf":
            put_params["ContentDisposition"] = "inline"

        response = s3_client.put_object(**put_params)
        logger.info("upload response: %s", response)

        url = None
        if sharing_url:
            url = f"{sharing_url.rstrip('/')}/{relative_url_path}"

        return {
            "file_name": file_name,
            "s3_key": s3_key,
            "content_type": content_type,
            "url": url,
        }
    except Exception:
        logger.error("Error uploading to S3: %s", traceback.format_exc())
        return None





def _s3_client_for_presign():
    """S3 client for browser-safe regional, virtual-hostedpresigned URLs.

    Global ``*.s3.amazonaws.com`` hosts often 307-redirect to the region
    endpoint; browsers then fail the signed PUT (403/CORS) and our API never
    sees ``/load/complete``. Prefer virtual-hosted
    ``https://{bucket}.s3.{region}.amazonaws.com/...``.
    """
    from botocore.config import Config

    region = bedrock_region or "us-west-2"
    return boto3.client(
        service_name="s3",
        region_name=region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    )


def session_upload_s3_key(file_name: str, user_id: str | None = None) -> str:
    """Build ``agentcore-sessions/{user}/upload/{file}`` object key."""
    segment = _sanitize_s3_user_segment(user_id) or "default"
    safe_name = os.path.basename(file_name or "").strip() or "upload.bin"
    return f"{S3_FILES_SESSION_PREFIX}/{segment}/upload/{safe_name}"


def _session_upload_content_type(file_name: str) -> str:
    """Content-Type for session uploads; never returns ``no info``."""
    content_type = get_contents_type(file_name)
    if content_type == "no info":
        return "application/octet-stream"
    return content_type


def upload_to_session_upload(
    file_bytes: bytes,
    file_name: str,
    user_id: str | None = None,
) -> dict | None:
    """Upload a chat Load-files attachment under agentcore-sessions/{user}/upload/.

    AgentCore Runtime mounts ``agentcore-sessions/`` at ``/mnt/workspace``, so the
    object is visible to the agent as
    ``/mnt/workspace/{user}/upload/{file_name}``.
    """
    if not s3_bucket:
        logger.error("s3_bucket is not configured")
        return None

    safe_name = os.path.basename(file_name or "").strip() or "upload.bin"
    s3_key = session_upload_s3_key(safe_name, user_id=user_id)
    content_type = _session_upload_content_type(safe_name)

    try:
        with _without_env_proxies():
            s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
            put_params: dict = {
                "Bucket": s3_bucket,
                "Key": s3_key,
                "Body": file_bytes,
                "Metadata": {"content_type": content_type},
                "ContentType": content_type,
            }
            if content_type == "application/pdf":
                put_params["ContentDisposition"] = "inline"
            response = s3_client.put_object(**put_params)
            logger.info(
                "session upload response user=%s key=%s: %s",
                _sanitize_s3_user_segment(user_id) or "default",
                s3_key,
                response,
            )

        return {
            "file_name": safe_name,
            "s3_key": s3_key,
            "content_type": content_type,
        }
    except Exception:
        logger.error("Error uploading to session storage: %s", traceback.format_exc())
        return None


def generate_session_upload_presigned_put(
    file_name: str,
    user_id: str | None = None,
    *,
    expires_in: int = 900,
) -> dict | None:
    """Return a browser-usable presigned PUT URL for Load-files uploads.

    The client must PUT the raw body with the returned ``headers`` (especially
    ``Content-Type``) so the signature matches.
    """
    if not s3_bucket:
        logger.error("s3_bucket is not configured")
        return None

    safe_name = os.path.basename(file_name or "").strip() or "upload.bin"
    s3_key = session_upload_s3_key(safe_name, user_id=user_id)
    content_type = _session_upload_content_type(safe_name)
    headers = {"Content-Type": content_type}
    params: dict = {
        "Bucket": s3_bucket,
        "Key": s3_key,
        "ContentType": content_type,
    }
    if content_type == "application/pdf":
        params["ContentDisposition"] = "inline"
        headers["Content-Disposition"] = "inline"

    try:
        with _without_env_proxies():
            s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
            upload_url = s3_client.generate_presigned_url(
                ClientMethod="put_object",
                Params=params,
                ExpiresIn=max(60, int(expires_in)),
                HttpMethod="PUT",
            )
        return {
            "file_name": safe_name,
            "s3_key": s3_key,
            "content_type": content_type,
            "upload_url": upload_url,
            "headers": headers,
            "expires_in": max(60, int(expires_in)),
        }
    except Exception:
        logger.error(
            "Error generating session upload presign: %s", traceback.format_exc()
        )
        return None





def rag_docs_s3_key(file_name: str, user_id: str | None = None) -> str:
    """Build ``docs/{user}/{file}`` key used by Knowledge Base ingest."""
    safe_name = os.path.basename(file_name or "").strip() or "upload.bin"
    user_segment = _sanitize_s3_user_segment(user_id)
    if user_segment:
        return f"{s3_prefix}/{user_segment}/{safe_name}"
    return f"{s3_prefix}/{safe_name}"

def rag_docs_public_url(file_name: str, user_id: str | None = None) -> str | None:
    """CloudFront/sharing URL for a docs/ object, if configured."""
    if not sharing_url:
        return None
    safe_name = os.path.basename(file_name or "").strip() or "upload.bin"
    user_segment = _sanitize_s3_user_segment(user_id)
    if user_segment:
        relative = f"{s3_prefix}/{parse.quote(user_segment)}/{parse.quote(safe_name)}"
    else:
        relative = f"{s3_prefix}/{parse.quote(safe_name)}"
    return f"{sharing_url.rstrip('/')}/{relative}"


def s3_uri_to_sharing_url(uri: str, sharing_base: str | None = None) -> str | None:
    """Map ``s3://bucket/key`` to ``{sharing_base}/key`` using the full object key.

    RAG citations must keep the full key (e.g. ``docs/{user}/file.pdf``) — using only
    ``docs/{filename}`` yields CloudFront AccessDenied (object missing).
    """
    base = (sharing_base if sharing_base is not None else sharing_url) or ""
    base = base.strip().rstrip("/")
    if not uri or not uri.startswith("s3://") or not base:
        return None
    rest = uri[5:]
    parts = rest.split("/", 1)
    if len(parts) < 2 or not parts[1]:
        return None
    encoded = "/".join(parse.quote(seg) for seg in parts[1].split("/"))
    return f"{base}/{encoded}"


def generate_rag_upload_presigned_put(
    file_name: str,
    user_id: str | None = None,
    *,
    expires_in: int = 900,
) -> dict | None:
    """Return a browser-usable presigned PUT URL for RAG docs uploads.

    Only ``Content-Type`` is signed (same as Load-files / Wiki) so browser PUT
    matches CORS/signature.
    """
    if not s3_bucket:
        logger.error("s3_bucket is not configured")
        return None

    safe_name = os.path.basename(file_name or "").strip() or "upload.bin"
    s3_key = rag_docs_s3_key(safe_name, user_id=user_id)
    content_type = _session_upload_content_type(safe_name)
    headers = {"Content-Type": content_type}
    params: dict = {
        "Bucket": s3_bucket,
        "Key": s3_key,
        "ContentType": content_type,
    }

    try:
        with _without_env_proxies():
            s3_client = _s3_client_for_presign()
            upload_url = s3_client.generate_presigned_url(
                ClientMethod="put_object",
                Params=params,
                ExpiresIn=max(60, int(expires_in)),
                HttpMethod="PUT",
            )
        logger.info(
            "rag upload presign key=%s host=%s",
            s3_key,
            parse.urlparse(upload_url).netloc,
        )
        return {
            "file_name": safe_name,
            "s3_key": s3_key,
            "content_type": content_type,
            "upload_url": upload_url,
            "headers": headers,
            "expires_in": max(60, int(expires_in)),
            "url": rag_docs_public_url(safe_name, user_id=user_id),
        }
    except Exception:
        logger.error(
            "Error generating rag upload presign: %s", traceback.format_exc()
        )
        return None

def download_s3_object_to_path(s3_key: str, dest_path: str) -> int:
    """Download an S3 object to ``dest_path`` (streamed to disk). Return size."""
    if not s3_bucket or not s3_key:
        raise ValueError("s3_bucket/s3_key required")
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with _without_env_proxies():
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        s3_client.download_file(s3_bucket, s3_key, dest_path)
    size = os.path.getsize(dest_path) if os.path.isfile(dest_path) else 0
    logger.info("downloaded s3://%s/%s → %s (%s bytes)", s3_bucket, s3_key, dest_path, size)
    return size

def head_session_upload_object(s3_key: str) -> dict | None:
    """HEAD an object; return ``{content_length, content_type}`` or None."""
    if not s3_bucket or not s3_key:
        return None
    try:
        with _without_env_proxies():
            s3_client = _s3_client_for_presign()
            response = s3_client.head_object(Bucket=s3_bucket, Key=s3_key)
        return {
            "content_length": int(response.get("ContentLength") or 0),
            "content_type": response.get("ContentType"),
        }
    except Exception:
        logger.error("Error head_object key=%s: %s", s3_key, traceback.format_exc())
        return None


def wait_for_workspace_file(
    workspace_path: str,
    *,
    expected_size: int | None = None,
    timeout_sec: float = 90.0,
    interval_sec: float = 0.5,
) -> bool:
    """Poll until ``workspace_path`` is visible on the S3 Files mount.

    Returns True when the file exists (and optionally matches ``expected_size``).
    If ``/mnt/workspace`` is not mounted in this process, returns False immediately
    after a debug log — the AgentCore Runtime mount will still catch up later.
    """
    import time

    path = (workspace_path or "").strip()
    if not path:
        return False

    if not os.path.isdir("/mnt/workspace"):
        logger.info(
            "skip workspace wait: /mnt/workspace not mounted here (path=%s)",
            path,
        )
        return False

    deadline = time.monotonic() + max(0.0, timeout_sec)
    last_size: int | None = None
    while True:
        try:
            if os.path.isfile(path):
                size = os.path.getsize(path)
                last_size = size
                if expected_size is None or size == expected_size:
                    logger.info(
                        "workspace file ready: %s (%s bytes)",
                        path,
                        size,
                    )
                    return True
        except OSError:
            pass

        if time.monotonic() >= deadline:
            logger.warning(
                "workspace file not visible after %.1fs: %s (last_size=%s expected=%s)",
                timeout_sec,
                path,
                last_size,
                expected_size,
            )
            return False
        time.sleep(max(0.05, interval_sec))


ACTIVE_INGESTION_STATUSES = ("STARTING", "IN_PROGRESS")


def _bedrock_agent_client():
    return boto3.client(
        service_name="bedrock-agent",
        region_name=bedrock_region,
    )


def get_active_ingestion_job() -> dict | None:
    """Return an in-flight ingestion job if Knowledge Base sync is already running."""
    if not knowledge_base_id or not data_source_id:
        logger.error("knowledge_base_id or data_source_id is not configured")
        return None

    try:
        bedrock_client = _bedrock_agent_client()
        for status in ACTIVE_INGESTION_STATUSES:
            response = bedrock_client.list_ingestion_jobs(
                knowledgeBaseId=knowledge_base_id,
                dataSourceId=data_source_id,
                filters=[
                    {
                        "attribute": "STATUS",
                        "operator": "EQ",
                        "values": [status],
                    }
                ],
                maxResults=1,
                sortBy={
                    "attribute": "STARTED_AT",
                    "order": "DESCENDING",
                },
            )
            summaries = response.get("ingestionJobSummaries") or []
            if not summaries:
                continue
            job = summaries[0]
            logger.info("Active ingestion job found: %s", job)
            return {
                "ingestion_job_id": job.get("ingestionJobId"),
                "status": job.get("status"),
                "started_at": str(job["startedAt"]) if job.get("startedAt") else None,
            }
        return None
    except Exception:
        logger.error("Error listing ingestion jobs: %s", traceback.format_exc())
        raise


def sync_data_source() -> dict | None:
    """Start a Knowledge Base ingestion job for the configured data source."""
    if not knowledge_base_id or not data_source_id:
        logger.error("knowledge_base_id or data_source_id is not configured")
        return None

    try:
        bedrock_client = _bedrock_agent_client()
        response = bedrock_client.start_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
        )
        logger.info("start_ingestion_job response: %s", response)
        job = response.get("ingestionJob", {})
        return {
            "ingestion_job_id": job.get("ingestionJobId"),
            "status": job.get("status"),
        }
    except Exception:
        logger.error("Error syncing data source: %s", traceback.format_exc())
        return None

# ---------------------------------------------------------------------------
# ESS docs uploads (browser → S3 presigned PUT → materialize into ess/regulations/)
# ---------------------------------------------------------------------------

ESS_DOCS_S3_PREFIX = "session-uploads"
MAX_ESS_DOC_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


def ess_docs_s3_key(file_name: str, user_id: str | None = None) -> str:
    """Build ``session-uploads/{user}/ess/{file}`` staging key."""
    segment = sanitize_user_path_segment(user_id) or "default"
    safe_name = os.path.basename(file_name or "").strip() or "upload.bin"
    return f"{ESS_DOCS_S3_PREFIX}/{segment}/ess/{safe_name}"


def generate_ess_docs_presigned_put(
    file_name: str,
    user_id: str | None = None,
    *,
    expires_in: int = 900,
) -> dict | None:
    """Return a browser-usable presigned PUT URL for ESS docs uploads."""
    if not s3_bucket:
        logger.error("s3_bucket is not configured")
        return None

    original = os.path.basename(file_name or "").strip() or "upload.bin"
    try:
        _ensure_ess_on_path()
        from doc_list import sanitize_ess_filename

        safe_name = sanitize_ess_filename(original)
    except Exception:
        safe_name = original.replace(" ", "_")

    s3_key = ess_docs_s3_key(safe_name, user_id=user_id)
    content_type = _session_upload_content_type(safe_name)
    headers = {"Content-Type": content_type}
    params: dict = {
        "Bucket": s3_bucket,
        "Key": s3_key,
        "ContentType": content_type,
    }
    if content_type == "application/pdf":
        params["ContentDisposition"] = "inline"
        headers["Content-Disposition"] = "inline"

    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        upload_url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params=params,
            ExpiresIn=max(60, int(expires_in)),
            HttpMethod="PUT",
        )
        return {
            "file_name": safe_name,
            "original_filename": original,
            "sanitized": original != safe_name,
            "s3_key": s3_key,
            "content_type": content_type,
            "upload_url": upload_url,
            "headers": headers,
            "expires_in": max(60, int(expires_in)),
        }
    except Exception:
        logger.error(
            "Error generating ESS docs presign: %s", traceback.format_exc()
        )
        return None


def materialize_ess_docs_from_s3(
    s3_key: str,
    file_name: str,
    user_id: str | None = None,
    *,
    original_filename: str | None = None,
) -> dict | None:
    """Download a staged ESS object into ``{user}/ess/regulations/`` and update regulations_list."""
    if not s3_bucket or not s3_key:
        return None

    original = (
        os.path.basename(original_filename or file_name or "").strip()
        or "upload.bin"
    )
    try:
        _ensure_ess_on_path()
        from doc_list import sanitize_ess_filename, upsert_document

        safe_name = sanitize_ess_filename(file_name or original)
    except Exception:
        safe_name = os.path.basename(file_name or original) or "upload.bin"
        upsert_document = None  # type: ignore[assignment]

    ess = ensure_user_ess_dir(user_id)
    docs = os.path.join(ess, "regulations")
    os.makedirs(docs, exist_ok=True)
    dest_path = os.path.join(docs, safe_name)
    overwritten = os.path.isfile(dest_path)

    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        s3_client.download_file(s3_bucket, s3_key, dest_path)
        size = os.path.getsize(dest_path) if os.path.isfile(dest_path) else 0
        if size <= 0:
            logger.error("ESS materialize produced empty file: %s", dest_path)
            return None

        segment = sanitize_user_path_segment(user_id) or "default"
        if upsert_document is not None:
            try:
                upsert_document(
                    ess,
                    filename=safe_name,
                    source_path=os.path.abspath(dest_path),
                    bytes_size=size,
                    status="uploaded",
                    user_id=segment,
                    extra={
                        "original_filename": original,
                        "sanitized": original != safe_name,
                        "s3_key": s3_key,
                    },
                )
            except Exception:
                logger.exception("Failed to update ess doc_list after materialize")

        logger.info(
            "ess docs materialized user=%s s3_key=%s path=%s bytes=%s",
            segment,
            s3_key,
            dest_path,
            size,
        )
        return {
            "ess_dir": ess,
            "docs_dir": docs,
            "raw_dir": docs,
            "saved": {
                "name": safe_name,
                "original_filename": original,
                "sanitized": original != safe_name,
                "path": dest_path,
                "bytes": size,
                "overwritten": overwritten,
            },
            "count": 1,
            "s3_key": s3_key,
            "doc_list": ess_doc_list_path(user_id),
            "content_type": _session_upload_content_type(safe_name),
            "content_length": size,
        }
    except Exception:
        logger.error(
            "Error materializing ESS docs key=%s: %s",
            s3_key,
            traceback.format_exc(),
        )
        return None


def ess_projects_s3_key(file_name: str, user_id: str | None = None) -> str:
    """Build ``session-uploads/{user}/ess/projects/{file}`` staging key."""
    segment = sanitize_user_path_segment(user_id) or "default"
    safe_name = os.path.basename(file_name or "").strip() or "upload.bin"
    return f"{ESS_DOCS_S3_PREFIX}/{segment}/ess/projects/{safe_name}"


def generate_ess_projects_presigned_put(
    file_name: str,
    user_id: str | None = None,
    *,
    expires_in: int = 900,
) -> dict | None:
    """Return a browser-usable presigned PUT URL for ESS project docs uploads."""
    if not s3_bucket:
        logger.error("s3_bucket is not configured")
        return None

    original = os.path.basename(file_name or "").strip() or "upload.bin"
    try:
        _ensure_ess_on_path()
        from doc_list import sanitize_ess_filename

        safe_name = sanitize_ess_filename(original)
    except Exception:
        safe_name = original.replace(" ", "_")

    s3_key = ess_projects_s3_key(safe_name, user_id=user_id)
    content_type = _session_upload_content_type(safe_name)
    headers = {"Content-Type": content_type}
    params: dict = {
        "Bucket": s3_bucket,
        "Key": s3_key,
        "ContentType": content_type,
    }
    if content_type == "application/pdf":
        params["ContentDisposition"] = "inline"
        headers["Content-Disposition"] = "inline"

    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        upload_url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params=params,
            ExpiresIn=max(60, int(expires_in)),
            HttpMethod="PUT",
        )
        return {
            "file_name": safe_name,
            "original_filename": original,
            "sanitized": original != safe_name,
            "s3_key": s3_key,
            "content_type": content_type,
            "upload_url": upload_url,
            "headers": headers,
            "expires_in": max(60, int(expires_in)),
        }
    except Exception:
        logger.error(
            "Error generating ESS projects presign: %s", traceback.format_exc()
        )
        return None


def materialize_ess_projects_from_s3(
    s3_key: str,
    file_name: str,
    user_id: str | None = None,
    *,
    original_filename: str | None = None,
) -> dict | None:
    """Download a staged ESS object into ``{user}/ess/projects/`` and update project_list."""
    if not s3_bucket or not s3_key:
        return None

    original = (
        os.path.basename(original_filename or file_name or "").strip()
        or "upload.bin"
    )
    try:
        _ensure_ess_on_path()
        from doc_list import PROJECTS, sanitize_ess_filename, upsert_document

        safe_name = sanitize_ess_filename(file_name or original)
    except Exception:
        safe_name = os.path.basename(file_name or original) or "upload.bin"
        upsert_document = None  # type: ignore[assignment]
        PROJECTS = None  # type: ignore[assignment]

    ess = ensure_user_ess_dir(user_id)
    projects = os.path.join(ess, "projects")
    os.makedirs(projects, exist_ok=True)
    dest_path = os.path.join(projects, safe_name)
    overwritten = os.path.isfile(dest_path)

    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        s3_client.download_file(s3_bucket, s3_key, dest_path)
        size = os.path.getsize(dest_path) if os.path.isfile(dest_path) else 0
        if size <= 0:
            logger.error("ESS project materialize produced empty file: %s", dest_path)
            return None

        segment = sanitize_user_path_segment(user_id) or "default"
        if upsert_document is not None and PROJECTS is not None:
            try:
                upsert_document(
                    ess,
                    filename=safe_name,
                    source_path=os.path.abspath(dest_path),
                    bytes_size=size,
                    status="uploaded",
                    user_id=segment,
                    extra={
                        "original_filename": original,
                        "sanitized": original != safe_name,
                        "s3_key": s3_key,
                    },
                    registry=PROJECTS,
                )
            except Exception:
                logger.exception("Failed to update ess project_list after materialize")

        logger.info(
            "ess projects materialized user=%s s3_key=%s path=%s bytes=%s",
            segment,
            s3_key,
            dest_path,
            size,
        )
        return {
            "ess_dir": ess,
            "projects_dir": projects,
            "docs_dir": projects,
            "raw_dir": projects,
            "saved": {
                "name": safe_name,
                "original_filename": original,
                "sanitized": original != safe_name,
                "path": dest_path,
                "bytes": size,
                "overwritten": overwritten,
            },
            "count": 1,
            "s3_key": s3_key,
            "doc_list": ess_project_list_path(user_id),
            "project_list": ess_project_list_path(user_id),
            "content_type": _session_upload_content_type(safe_name),
            "content_length": size,
        }
    except Exception:
        logger.error(
            "Error materializing ESS projects key=%s: %s",
            s3_key,
            traceback.format_exc(),
        )
        return None


def ess_drawings_s3_key(file_name: str, user_id: str | None = None) -> str:
    """Build ``session-uploads/{user}/ess/drawings/{file}`` staging key."""
    segment = sanitize_user_path_segment(user_id) or "default"
    safe_name = os.path.basename(file_name or "").strip() or "upload.bin"
    return f"{ESS_DOCS_S3_PREFIX}/{segment}/ess/drawings/{safe_name}"


def generate_ess_drawings_presigned_put(
    file_name: str,
    user_id: str | None = None,
    *,
    expires_in: int = 900,
) -> dict | None:
    """Return a browser-usable presigned PUT URL for ESS drawing docs uploads."""
    if not s3_bucket:
        logger.error("s3_bucket is not configured")
        return None

    original = os.path.basename(file_name or "").strip() or "upload.bin"
    try:
        _ensure_ess_on_path()
        from doc_list import sanitize_ess_filename

        safe_name = sanitize_ess_filename(original)
    except Exception:
        safe_name = original.replace(" ", "_")

    s3_key = ess_drawings_s3_key(safe_name, user_id=user_id)
    content_type = _session_upload_content_type(safe_name)
    headers = {"Content-Type": content_type}
    params: dict = {
        "Bucket": s3_bucket,
        "Key": s3_key,
        "ContentType": content_type,
    }
    if content_type == "application/pdf":
        params["ContentDisposition"] = "inline"
        headers["Content-Disposition"] = "inline"

    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        upload_url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params=params,
            ExpiresIn=max(60, int(expires_in)),
            HttpMethod="PUT",
        )
        return {
            "file_name": safe_name,
            "original_filename": original,
            "sanitized": original != safe_name,
            "s3_key": s3_key,
            "content_type": content_type,
            "upload_url": upload_url,
            "headers": headers,
            "expires_in": max(60, int(expires_in)),
        }
    except Exception:
        logger.error(
            "Error generating ESS drawings presign: %s", traceback.format_exc()
        )
        return None


def materialize_ess_drawings_from_s3(
    s3_key: str,
    file_name: str,
    user_id: str | None = None,
    *,
    original_filename: str | None = None,
) -> dict | None:
    """Download a staged ESS object into ``{user}/ess/drawings/`` and update drawings_list."""
    if not s3_bucket or not s3_key:
        return None

    original = (
        os.path.basename(original_filename or file_name or "").strip()
        or "upload.bin"
    )
    try:
        _ensure_ess_on_path()
        from doc_list import DRAWINGS, sanitize_ess_filename, upsert_document

        safe_name = sanitize_ess_filename(file_name or original)
    except Exception:
        safe_name = os.path.basename(file_name or original) or "upload.bin"
        upsert_document = None  # type: ignore[assignment]
        DRAWINGS = None  # type: ignore[assignment]

    ess = ensure_user_ess_dir(user_id)
    drawings = os.path.join(ess, "drawings")
    os.makedirs(drawings, exist_ok=True)
    dest_path = os.path.join(drawings, safe_name)
    overwritten = os.path.isfile(dest_path)

    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        s3_client.download_file(s3_bucket, s3_key, dest_path)
        size = os.path.getsize(dest_path) if os.path.isfile(dest_path) else 0
        if size <= 0:
            logger.error("ESS drawing materialize produced empty file: %s", dest_path)
            return None

        segment = sanitize_user_path_segment(user_id) or "default"
        if upsert_document is not None and DRAWINGS is not None:
            try:
                upsert_document(
                    ess,
                    filename=safe_name,
                    source_path=os.path.abspath(dest_path),
                    bytes_size=size,
                    status="uploaded",
                    user_id=segment,
                    extra={
                        "original_filename": original,
                        "sanitized": original != safe_name,
                        "s3_key": s3_key,
                    },
                    registry=DRAWINGS,
                )
            except Exception:
                logger.exception("Failed to update ess drawings_list after materialize")

        logger.info(
            "ess drawings materialized user=%s s3_key=%s path=%s bytes=%s",
            segment,
            s3_key,
            dest_path,
            size,
        )
        return {
            "ess_dir": ess,
            "drawings_dir": drawings,
            "docs_dir": drawings,
            "raw_dir": drawings,
            "saved": {
                "name": safe_name,
                "original_filename": original,
                "sanitized": original != safe_name,
                "path": dest_path,
                "bytes": size,
                "overwritten": overwritten,
            },
            "count": 1,
            "s3_key": s3_key,
            "doc_list": ess_drawings_list_path(user_id),
            "drawings_list": ess_drawings_list_path(user_id),
            "content_type": _session_upload_content_type(safe_name),
            "content_length": size,
        }
    except Exception:
        logger.error(
            "Error materializing ESS drawings key=%s: %s",
            s3_key,
            traceback.format_exc(),
        )
        return None


# ---------------------------------------------------------------------------
# ESS document list — CloudFront URLs (PDF) + artifacts MD publish
# ---------------------------------------------------------------------------

def ess_pdf_s3_key(file_name: str, user_id: str | None = None) -> str:
    """S3 key for an ESS PDF uploaded via Configure (session-uploads staging)."""
    return ess_docs_s3_key(file_name, user_id=user_id)


def ess_project_pdf_public_url(
    file_name: str, user_id: str | None = None
) -> str | None:
    """CloudFront URL for ``session-uploads/{user}/ess/projects/{pdf}``."""
    if not sharing_url:
        return None
    safe_name = os.path.basename(file_name or "").strip()
    if not safe_name:
        return None
    segment = sanitize_user_path_segment(user_id) or "default"
    relative = (
        f"{ESS_DOCS_S3_PREFIX}/{parse.quote(segment)}/ess/projects/"
        f"{parse.quote(safe_name)}"
    )
    return f"{sharing_url.rstrip('/')}/{relative}"


def ess_drawing_pdf_public_url(
    file_name: str, user_id: str | None = None
) -> str | None:
    """CloudFront URL for ``session-uploads/{user}/ess/drawings/{pdf}``."""
    if not sharing_url:
        return None
    safe_name = os.path.basename(file_name or "").strip()
    if not safe_name:
        return None
    segment = sanitize_user_path_segment(user_id) or "default"
    relative = (
        f"{ESS_DOCS_S3_PREFIX}/{parse.quote(segment)}/ess/drawings/"
        f"{parse.quote(safe_name)}"
    )
    return f"{sharing_url.rstrip('/')}/{relative}"


def ess_pdf_public_url(file_name: str, user_id: str | None = None) -> str | None:
    """CloudFront URL for ``session-uploads/{user}/ess/{pdf}`` when sharing_url is set."""
    if not sharing_url:
        return None
    safe_name = os.path.basename(file_name or "").strip()
    if not safe_name:
        return None
    segment = sanitize_user_path_segment(user_id) or "default"
    relative = (
        f"{ESS_DOCS_S3_PREFIX}/{parse.quote(segment)}/ess/{parse.quote(safe_name)}"
    )
    return f"{sharing_url.rstrip('/')}/{relative}"


def ess_md_artifacts_s3_key(file_name: str, user_id: str | None = None) -> str:
    """``artifacts/{projectName}/{user}/md/{stem}.md`` for CloudFront viewing."""
    segment = sanitize_user_path_segment(user_id) or "default"
    safe_name = os.path.basename(file_name or "").strip() or "document.md"
    if not safe_name.lower().endswith(".md"):
        safe_name = f"{os.path.splitext(safe_name)[0]}.md"
    project = (projectName or "default").strip().strip("/") or "default"
    return f"artifacts/{project}/{segment}/md/{safe_name}"


def ess_md_artifacts_public_url(
    file_name: str, user_id: str | None = None
) -> str | None:
    if not sharing_url:
        return None
    key = ess_md_artifacts_s3_key(file_name, user_id=user_id)
    # Quote each path segment; keep slashes.
    parts = [parse.quote(p) for p in key.split("/")]
    return f"{sharing_url.rstrip('/')}/{'/'.join(parts)}"


def ess_tc_artifacts_s3_key(file_name: str, user_id: str | None = None) -> str:
    """``artifacts/{projectName}/{user}/tc/{name}.xlsx`` for CloudFront download."""
    segment = sanitize_user_path_segment(user_id) or "default"
    safe_name = os.path.basename(file_name or "").strip() or "testcase.xlsx"
    if not safe_name.lower().endswith(".xlsx"):
        safe_name = f"{os.path.splitext(safe_name)[0]}.xlsx"
    project = (projectName or "default").strip().strip("/") or "default"
    return f"artifacts/{project}/{segment}/tc/{safe_name}"


def ess_tc_artifacts_public_url(
    file_name: str, user_id: str | None = None
) -> str | None:
    if not sharing_url:
        return None
    key = ess_tc_artifacts_s3_key(file_name, user_id=user_id)
    parts = [parse.quote(p) for p in key.split("/")]
    return f"{sharing_url.rstrip('/')}/{'/'.join(parts)}"


def ess_tc_local_artifacts_path(
    file_name: str, user_id: str | None = None
) -> str:
    """Local draft: ``{user}/artifacts/tc/{name}.xlsx``."""
    artifacts = ensure_user_artifacts_dir(user_id)
    tc_dir = os.path.join(artifacts, "tc")
    os.makedirs(tc_dir, exist_ok=True)
    safe_name = os.path.basename(file_name or "").strip() or "testcase.xlsx"
    if not safe_name.lower().endswith(".xlsx"):
        safe_name = f"{os.path.splitext(safe_name)[0]}.xlsx"
    return os.path.join(tc_dir, safe_name)


def ess_md_local_artifacts_path(
    file_name: str, user_id: str | None = None
) -> str:
    """Local mirror: ``{user}/artifacts/md/{stem}.md``."""
    artifacts = ensure_user_artifacts_dir(user_id)
    md_dir = os.path.join(artifacts, "md")
    os.makedirs(md_dir, exist_ok=True)
    safe_name = os.path.basename(file_name or "").strip() or "document.md"
    if not safe_name.lower().endswith(".md"):
        safe_name = f"{os.path.splitext(safe_name)[0]}.md"
    return os.path.join(md_dir, safe_name)


def publish_ess_markdown_to_artifacts(
    md_path: str,
    user_id: str | None = None,
    *,
    file_name: str | None = None,
) -> dict | None:
    """Copy markdown next to artifacts and upload to S3 for CloudFront.

    Target key: ``artifacts/{projectName}/{user_id}/md/{name}.md``.
    """
    from pathlib import Path

    src = Path(md_path)
    if not src.is_file():
        logger.warning("ESS md publish skipped; missing file: %s", src)
        return None

    name = os.path.basename(file_name or src.name)
    if not name.lower().endswith(".md"):
        name = f"{os.path.splitext(name)[0]}.md"

    local_dest = ess_md_local_artifacts_path(name, user_id=user_id)
    try:
        src_stat = src.stat()
        if (
            os.path.isfile(local_dest)
            and os.path.getsize(local_dest) == src_stat.st_size
            and os.path.getmtime(local_dest) >= src_stat.st_mtime
            and s3_bucket
        ):
            # Local mirror already fresh — still ensure S3 object exists.
            s3_key = ess_md_artifacts_s3_key(name, user_id=user_id)
            public_url = ess_md_artifacts_public_url(name, user_id=user_id)
            head = _head_s3_object_quiet(s3_key)
            if head and int(head.get("content_length") or 0) == src_stat.st_size:
                return {
                    "file_name": name,
                    "local_path": local_dest,
                    "s3_key": s3_key,
                    "url": public_url,
                    "uploaded": True,
                    "skipped": True,
                    "bytes": src_stat.st_size,
                }
        if os.path.abspath(str(src)) != os.path.abspath(local_dest):
            import shutil

            shutil.copy2(src, local_dest)
    except Exception:
        logger.exception("Failed to copy ESS md to local artifacts: %s", src)
        local_dest = str(src.resolve())

    s3_key = ess_md_artifacts_s3_key(name, user_id=user_id)
    public_url = ess_md_artifacts_public_url(name, user_id=user_id)
    result = {
        "file_name": name,
        "local_path": local_dest,
        "s3_key": s3_key,
        "url": public_url,
        "uploaded": False,
    }

    if not s3_bucket:
        logger.warning("s3_bucket not configured; ESS md kept local only")
        return result

    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        content_type = get_contents_type(name)
        if content_type == "no info":
            content_type = "text/markdown; charset=utf-8"
        with open(local_dest, "rb") as f:
            body = f.read()
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=body,
            ContentType=content_type,
            CacheControl="no-cache, max-age=0, must-revalidate",
        )
        result["uploaded"] = True
        result["bytes"] = len(body)
        logger.info(
            "ESS md published user=%s s3_key=%s bytes=%s url=%s",
            sanitize_user_path_segment(user_id) or "default",
            s3_key,
            len(body),
            public_url,
        )
        return result
    except Exception:
        logger.error(
            "Error publishing ESS md to artifacts: %s", traceback.format_exc()
        )
        return result


def head_ess_pdf_on_s3(
    file_name: str,
    user_id: str | None = None,
    *,
    kind: str = "regulation",
) -> bool:
    """True when the ESS PDF object exists under session-uploads (CloudFront-ready)."""
    if kind == "project":
        key = ess_projects_s3_key(file_name, user_id=user_id)
    elif kind == "drawing":
        key = ess_drawings_s3_key(file_name, user_id=user_id)
    else:
        key = ess_pdf_s3_key(file_name, user_id=user_id)
    if not s3_bucket or not key:
        return False
    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        s3_client.head_object(Bucket=s3_bucket, Key=key)
        return True
    except Exception:
        return False


def ess_pdf_s3_key_for_kind(
    file_name: str,
    user_id: str | None = None,
    *,
    kind: str = "regulation",
) -> str | None:
    """S3 key for an ESS PDF under session-uploads."""
    safe_name = os.path.basename(file_name or "").strip()
    if not safe_name:
        return None
    if kind == "project":
        return ess_projects_s3_key(safe_name, user_id=user_id)
    if kind == "drawing":
        return ess_drawings_s3_key(safe_name, user_id=user_id)
    return ess_pdf_s3_key(safe_name, user_id=user_id)


def stream_ess_pdf_from_s3(
    file_name: str,
    user_id: str | None = None,
    *,
    kind: str = "regulation",
):
    """Stream an ESS PDF from S3 (session-uploads staging key).

    Used by the PDF viewer API when the local copy is missing. Avoids redirecting
    to CloudFront ``/session-uploads/*`` on distributions that still route that
    prefix to the ALB default behavior.
    """
    from fastapi.responses import StreamingResponse

    key = ess_pdf_s3_key_for_kind(file_name, user_id=user_id, kind=kind)
    if not s3_bucket or not key:
        return None
    safe_name = os.path.basename(file_name or "").strip() or "document.pdf"
    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        obj = s3_client.get_object(Bucket=s3_bucket, Key=key)
        body = obj["Body"]
        content_type = obj.get("ContentType") or "application/pdf"
        if content_type in ("binary/octet-stream", "no info", "application/octet-stream"):
            content_type = "application/pdf"
        return StreamingResponse(
            body.iter_chunks(chunk_size=1024 * 256),
            media_type=content_type,
            headers={
                "Content-Disposition": f'inline; filename="{safe_name}"',
                "Cache-Control": "private, max-age=3600",
            },
        )
    except Exception:
        logger.error(
            "Error streaming ESS pdf from S3 key=%s: %s",
            key,
            traceback.format_exc(),
        )
        return None


def _head_s3_object_quiet(s3_key: str) -> dict | None:
    if not s3_bucket or not s3_key:
        return None
    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        response = s3_client.head_object(Bucket=s3_bucket, Key=s3_key)
        return {
            "content_length": int(response.get("ContentLength") or 0),
            "content_type": response.get("ContentType"),
        }
    except Exception:
        return None


def enrich_ess_documents_for_ui(
    documents: list[dict],
    user_id: str | None = None,
    *,
    publish_md: bool = True,
    kind: str = "regulation",
) -> list[dict]:
    """Attach pdf/md view URLs for Regulations / Projects / Drawings UI.

    PDF: prefer CloudFront session-uploads; else API fallback.
    MD: copy+upload to ``artifacts/{project}/{user}/md/`` then expose CloudFront + viewer URL.
    """
    folder_kind = kind in {"project", "drawing"}
    if kind == "project":
        docs_root = ess_projects_dir(user_id)
    elif kind == "drawing":
        docs_root = ess_drawings_dir(user_id)
    else:
        docs_root = ess_docs_dir(user_id)
    kind_qs = f"?kind={kind}" if folder_kind else ""

    enriched: list[dict] = []
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        item = dict(doc)
        filename = str(item.get("filename") or "").strip()
        md_file = str(item.get("md_file") or item.get("md_path") or "").strip()
        md_name = os.path.basename(md_file) if md_file else ""
        if not md_name and filename:
            stem = os.path.splitext(filename)[0]
            md_name = f"{stem}.md"

        pdf_name = filename if filename.lower().endswith(".pdf") else ""
        if not pdf_name and filename:
            # source may be pdf even if filename field uses another form
            src = str(item.get("source_path") or "")
            if src.lower().endswith(".pdf"):
                pdf_name = os.path.basename(src)

        local_md = str(item.get("md_path") or "").strip()
        if local_md and not os.path.isfile(local_md) and md_name:
            candidate = os.path.join(docs_root, md_name)
            if os.path.isfile(candidate):
                local_md = candidate
        elif not local_md and md_name:
            candidate = os.path.join(docs_root, md_name)
            if os.path.isfile(candidate):
                local_md = candidate

        local_pdf = ""
        if pdf_name:
            candidate = os.path.join(docs_root, pdf_name)
            if os.path.isfile(candidate):
                local_pdf = candidate
            else:
                src = str(item.get("source_path") or "")
                if src and os.path.isfile(src) and src.lower().endswith(".pdf"):
                    local_pdf = src

        if kind == "project":
            pdf_cf = (
                ess_project_pdf_public_url(pdf_name, user_id=user_id)
                if pdf_name
                else None
            )
        elif kind == "drawing":
            pdf_cf = (
                ess_drawing_pdf_public_url(pdf_name, user_id=user_id)
                if pdf_name
                else None
            )
        else:
            pdf_cf = (
                ess_pdf_public_url(pdf_name, user_id=user_id) if pdf_name else None
            )
        pdf_on_s3 = bool(
            pdf_name
            and head_ess_pdf_on_s3(pdf_name, user_id=user_id, kind=kind)
        )
        item["pdf_available"] = bool(local_pdf) or pdf_on_s3
        item["pdf_url"] = pdf_cf if pdf_on_s3 else None
        item["pdf_api_url"] = (
            f"/api/ess/documents/{parse.quote(pdf_name)}/pdf{kind_qs}"
            if pdf_name
            else None
        )

        md_url = None
        md_published = False
        if local_md and os.path.isfile(local_md) and publish_md:
            published = publish_ess_markdown_to_artifacts(
                local_md, user_id=user_id, file_name=md_name or None
            )
            if published:
                md_url = published.get("url")
                md_published = bool(published.get("uploaded"))
                item["md_s3_key"] = published.get("s3_key")
                item["md_local_artifacts"] = published.get("local_path")
        elif md_name:
            md_url = ess_md_artifacts_public_url(md_name, user_id=user_id)

        item["md_available"] = bool(local_md and os.path.isfile(local_md))
        item["md_url"] = md_url
        item["md_published"] = md_published
        if local_md and os.path.isfile(local_md):
            try:
                item["md_bytes"] = os.path.getsize(local_md)
            except OSError:
                item["md_bytes"] = None
        else:
            item["md_bytes"] = None
        item["md_viewer_url"] = (
            f"/api/ess/documents/{parse.quote(md_name)}/markdown{kind_qs}"
            if md_name
            else None
        )
        item["display_name"] = (
            str(item.get("original_filename") or "").strip() or filename or md_name
        )
        item["kind"] = kind
        enriched.append(item)
    return enriched


def enrich_ess_test_cases_for_ui(
    documents: list[dict],
    user_id: str | None = None,
) -> list[dict]:
    """Attach xlsx/json view URLs for Test Cases UI."""
    tc_root = ess_test_cases_dir(user_id)
    enriched: list[dict] = []
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        item = dict(doc)
        filename = str(item.get("filename") or "").strip()
        stem = os.path.splitext(filename)[0] if filename else ""

        xlsx_name = filename if filename.lower().endswith(".xlsx") else (
            f"{stem}.xlsx" if stem else ""
        )
        local_xlsx = ""
        if xlsx_name:
            candidate = os.path.join(tc_root, xlsx_name)
            if os.path.isfile(candidate):
                local_xlsx = candidate
            else:
                src = str(item.get("source_path") or "").strip()
                if src and os.path.isfile(src) and src.lower().endswith(".xlsx"):
                    local_xlsx = src
                    xlsx_name = os.path.basename(src)

        json_path = str(item.get("json_path") or "").strip()
        json_name = ""
        local_json = ""
        if json_path and os.path.isfile(json_path):
            local_json = json_path
            json_name = os.path.basename(json_path)
        elif stem:
            candidate = os.path.join(tc_root, f"{stem}.json")
            if os.path.isfile(candidate):
                local_json = candidate
                json_name = f"{stem}.json"

        item["xlsx_available"] = bool(local_xlsx)
        item["xlsx_api_url"] = (
            f"/api/ess/documents/{parse.quote(xlsx_name)}/xlsx"
            if xlsx_name
            else None
        )
        item["json_available"] = bool(local_json)
        item["json_viewer_url"] = (
            f"/api/ess/documents/{parse.quote(json_name or xlsx_name)}/json"
            if (json_name or xlsx_name)
            else None
        )
        if local_xlsx and os.path.isfile(local_xlsx):
            try:
                item["bytes"] = item.get("bytes") or os.path.getsize(local_xlsx)
            except OSError:
                pass
        title = str(item.get("title") or "").strip()
        item["display_name"] = (
            title
            or str(item.get("original_filename") or "").strip()
            or filename
            or json_name
        )
        item["kind"] = "test_case"
        enriched.append(item)
    return enriched


def _unlink_under_roots(path: str, *roots: str) -> bool:
    """Delete a file only if it resolves under one of *roots*. Returns True if removed."""
    from pathlib import Path

    try:
        target = Path(path).expanduser().resolve()
    except OSError:
        return False
    if not target.is_file():
        return False
    for root in roots:
        try:
            base = Path(root).expanduser().resolve()
            target.relative_to(base)
        except (OSError, ValueError):
            continue
        try:
            target.unlink()
            return True
        except OSError:
            logger.warning("Failed to unlink ESS file: %s", target)
            return False
    return False


def _delete_s3_key_quiet(s3_key: str | None) -> bool:
    """Best-effort S3 object delete. Returns True if delete was attempted successfully."""
    if not s3_bucket or not s3_key:
        return False
    try:
        s3_client = boto3.client(service_name="s3", region_name=bedrock_region)
        s3_client.delete_object(Bucket=s3_bucket, Key=s3_key)
        return True
    except Exception:
        logger.debug("ESS S3 delete skipped for %s", s3_key, exc_info=True)
        return False


def delete_ess_document(
    user_id: str | None,
    filename: str,
    *,
    kind: str = "regulation",
) -> dict:
    """Remove one ESS document: local source + sidecars + list entry (+ S3 best-effort).

    Deletes:
    - Regulations/Projects: ``{stem}.pdf`` (or source), ``{stem}.md``, ``{stem}.json``
    - Test Cases: ``{stem}.xlsx``, ``{stem}.json``
    - Optional intermediates under ``out/converted/.pdf_pages/{stem}_*``
    - Local ``artifacts/md/{stem}.md`` and matching S3 objects when configured
    """
    import shutil
    from pathlib import Path as _Path

    name = os.path.basename(filename or "").strip()
    if not name or name in {".", ".."}:
        raise ValueError("Invalid document name")

    kind_norm = (kind or "regulation").strip().lower()
    if kind_norm not in {"regulation", "project", "drawing", "test_case"}:
        raise ValueError(f"Unsupported kind: {kind}")

    _ensure_ess_on_path()
    from doc_list import (
        DRAWINGS,
        PROJECTS,
        REGULATIONS,
        TEST_CASES,
        get_document,
        remove_document,
    )

    registry = {
        "regulation": REGULATIONS,
        "project": PROJECTS,
        "drawing": DRAWINGS,
        "test_case": TEST_CASES,
    }[kind_norm]

    ess = ensure_user_ess_dir(user_id)
    artifacts_root = ensure_user_artifacts_dir(user_id)
    docs_dir = {
        "regulation": ess_docs_dir(user_id),
        "project": ess_projects_dir(user_id),
        "drawing": ess_drawings_dir(user_id),
        "test_case": ess_test_cases_dir(user_id),
    }[kind_norm]

    entry = get_document(ess, filename=name, registry=registry)
    if entry is None:
        # Registry miss: still allow cleanup if source/sidecar files exist on disk.
        stem = os.path.splitext(name)[0]
        entry = {
            "filename": name,
            "source_path": os.path.join(docs_dir, name),
            "md_path": (
                os.path.join(docs_dir, f"{stem}.md")
                if kind_norm != "test_case"
                else None
            ),
            "json_path": os.path.join(docs_dir, f"{stem}.json"),
        }
        exists = any(
            p and os.path.isfile(p)
            for p in (
                entry["source_path"],
                entry.get("md_path"),
                entry.get("json_path"),
            )
        )
        if not exists:
            raise FileNotFoundError(f"Document not found: {name}")

    stem = os.path.splitext(str(entry.get("filename") or name))[0] or os.path.splitext(
        name
    )[0]
    deleted_files: list[str] = []
    allow_roots = (ess, artifacts_root, docs_dir)

    paths_to_delete: list[str] = []
    for key in ("source_path", "md_path", "json_path"):
        raw = str(entry.get(key) or "").strip()
        if raw:
            paths_to_delete.append(raw)

    # Always include stem-based siblings next to the registry folder.
    if kind_norm == "test_case":
        for sibling in (f"{stem}.xlsx", f"{stem}.json", name):
            paths_to_delete.append(os.path.join(docs_dir, sibling))
    else:
        for sibling in (f"{stem}.pdf", f"{stem}.md", f"{stem}.json", name):
            paths_to_delete.append(os.path.join(docs_dir, sibling))
        md_file = str(entry.get("md_file") or "").strip()
        if md_file:
            paths_to_delete.append(os.path.join(docs_dir, os.path.basename(md_file)))
            paths_to_delete.append(ess_md_local_artifacts_path(md_file, user_id=user_id))
        else:
            paths_to_delete.append(
                ess_md_local_artifacts_path(f"{stem}.md", user_id=user_id)
            )

    seen: set[str] = set()
    for path in paths_to_delete:
        try:
            resolved = str(_Path(path).expanduser().resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if _unlink_under_roots(resolved, *allow_roots):
            deleted_files.append(resolved)

    # FMP intermediates: ``out/converted/.pdf_pages/{stem}_*``
    pages_root = _Path(ess_converted_dir(user_id)) / ".pdf_pages"
    deleted_dirs: list[str] = []
    if kind_norm != "test_case" and pages_root.is_dir() and stem:
        for work in pages_root.iterdir():
            if not work.is_dir():
                continue
            if work.name == stem or work.name.startswith(f"{stem}_"):
                try:
                    shutil.rmtree(work)
                    deleted_dirs.append(str(work))
                except OSError:
                    logger.warning("Failed to remove pdf_pages dir: %s", work)

    # Best-effort S3 cleanup (PDF staging + published markdown).
    s3_deleted: list[str] = []
    entry_s3 = str(entry.get("s3_key") or "").strip()
    if entry_s3 and _delete_s3_key_quiet(entry_s3):
        s3_deleted.append(entry_s3)

    if kind_norm == "project":
        pdf_key = ess_projects_s3_key(f"{stem}.pdf", user_id=user_id)
    elif kind_norm == "drawing":
        pdf_key = ess_drawings_s3_key(f"{stem}.pdf", user_id=user_id)
    elif kind_norm == "regulation":
        pdf_key = ess_docs_s3_key(f"{stem}.pdf", user_id=user_id)
    else:
        pdf_key = None
    if pdf_key and pdf_key not in s3_deleted and _delete_s3_key_quiet(pdf_key):
        s3_deleted.append(pdf_key)

    if kind_norm != "test_case":
        md_key = ess_md_artifacts_s3_key(f"{stem}.md", user_id=user_id)
        if md_key not in s3_deleted and _delete_s3_key_quiet(md_key):
            s3_deleted.append(md_key)

    removed = remove_document(ess, filename=name, registry=registry)
    if not removed and entry.get("source_path"):
        removed = remove_document(
            ess, source_path=str(entry.get("source_path")), registry=registry
        )

    if not removed and not deleted_files and not deleted_dirs:
        raise FileNotFoundError(f"Document not found: {name}")

    return {
        "ok": True,
        "filename": name,
        "kind": kind_norm,
        "removed_from_list": bool(removed),
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "s3_deleted": s3_deleted,
    }
