import logging
import sys
import json
import traceback
import boto3
import os
from langchain_community.utilities.tavily_search import TavilySearchAPIWrapper

logging.basicConfig(
    level=logging.INFO,  # Default to INFO level
    format='%(filename)s:%(lineno)d | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("utils")

workingDir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(workingDir, "config.json")
# Prefer AgentCore /mnt/workspace or ECS /mnt/app-data; local fallback is .session_storage.
def _default_session_storage_dir() -> str:
    for candidate in ("/mnt/workspace", "/mnt/app-data"):
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(workingDir, ".session_storage")


SESSION_STORAGE_DIR = os.environ.get("SESSION_STORAGE_DIR") or _default_session_storage_dir()
SKILLS_DIR = os.path.join(workingDir, "skills")

S3_FILES_SESSION_PREFIX = "agentcore-sessions"
S3_FILES_APP_DATA_PREFIX = "app-data/"


def sanitize_user_path_segment(user_id: str | None) -> str | None:
    """Return a safe single path segment for per-user workspace folders, or None."""
    if not user_id:
        return None
    segment = (
        str(user_id)
        .strip()
        .replace("/", "_")
        .replace("\\", "_")
        .replace("..", "_")
    )
    return segment or None


def get_user_artifacts_dir(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/artifacts (does not create)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "artifacts")


def ensure_user_artifacts_dir(user_id: str | None) -> str:
    """Create {SESSION_STORAGE_DIR}/{user_id}/artifacts if needed and return it."""
    artifacts_dir = get_user_artifacts_dir(user_id)
    os.makedirs(artifacts_dir, exist_ok=True)
    logger.info("user artifacts dir ready: %s", artifacts_dir)
    return artifacts_dir


def get_user_skills_dir(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/skills (does not create)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "skills")


def ensure_user_skills_dir(user_id: str | None) -> str:
    """Create {SESSION_STORAGE_DIR}/{user_id}/skills if needed and return it."""
    skills_dir = get_user_skills_dir(user_id)
    os.makedirs(skills_dir, exist_ok=True)
    logger.info("user skills dir ready: %s", skills_dir)
    return skills_dir


def get_user_skills_list_path(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/skills.list (does not create)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "skills.list")


def get_user_graph_dir(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/graph (does not create)."""
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        segment = "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "graph")


_DEFAULT_USER_SETTINGS: dict[str, object] = {
    "knowledge_graph_enabled": True,
}


def get_user_settings_path(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/settings.json (does not create)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "settings.json")


def load_user_settings(user_id: str | None) -> dict[str, object]:
    """Load per-user UI/feature settings. Missing file → defaults (KG on)."""
    settings = dict(_DEFAULT_USER_SETTINGS)
    path = get_user_settings_path(user_id)
    if not os.path.isfile(path):
        return settings
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and "knowledge_graph_enabled" in raw:
            settings["knowledge_graph_enabled"] = bool(raw["knowledge_graph_enabled"])
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load user settings %s: %s", path, e)
    return settings


def is_knowledge_graph_enabled(user_id: str | None) -> bool:
    """True when Knowledge Graph feature is on (default)."""
    return bool(load_user_settings(user_id).get("knowledge_graph_enabled", True))


def is_hybrid_graph_search_enabled() -> bool:
    """True when config.json hybrid_graph_search is enable (embedding vector search)."""
    cfg = load_config() or {}
    raw = str(cfg.get("hybrid_graph_search") or "").strip().lower()
    return raw in {"enable", "enabled", "on", "true", "1", "yes"}


def list_skill_dir_names(skills_dir: str) -> list[str]:
    """Return subdirectory names that contain SKILL.md under skills_dir."""
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


def load_skills_list_file(path: str) -> list[str]:
    """Load skill names from a skills.list file (ignore blanks/comments)."""
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


def builtin_skill_names() -> list[str]:
    """Builtin skill names discovered by scanning SKILLS_DIR for SKILL.md."""
    return list_skill_dir_names(SKILLS_DIR)


def _merged_skill_names(user_id: str | None) -> list[str]:
    """Builtin skills/ dirs + per-user skill-creator skills (deduped, stable order)."""
    merged: list[str] = []
    seen: set[str] = set()
    for name in builtin_skill_names() + list_skill_dir_names(get_user_skills_dir(user_id)):
        if name not in seen:
            merged.append(name)
            seen.add(name)
    return merged


def write_user_skills_list(user_id: str | None, names: list[str] | None = None) -> str:
    """Write {SESSION_STORAGE_DIR}/{user_id}/skills.list and return its path."""
    ensure_user_skills_dir(user_id)
    path = get_user_skills_list_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    merged = names if names is not None else _merged_skill_names(user_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(merged) + ("\n" if merged else ""))
    logger.info("wrote user skills.list (%d skills) -> %s", len(merged), path)
    return path


def ensure_user_skills_list(user_id: str | None) -> str:
    """Sync {SESSION_STORAGE_DIR}/{user_id}/skills.list to builtin + user skills.

    Builtin names come from scanning ``skills/`` (SKILL.md dirs). User-created
    skills come from ``{user_id}/skills/``. Rewrite when the list drifts.
    """
    ensure_user_skills_dir(user_id)
    path = get_user_skills_list_path(user_id)
    desired = _merged_skill_names(user_id)
    existing = load_skills_list_file(path) if os.path.isfile(path) else []
    if existing == desired:
        logger.info(
            "user skills.list up to date (%d skills) -> %s",
            len(existing),
            path,
        )
        return path
    return write_user_skills_list(user_id, desired)


def update_user_skills_list(user_id: str | None) -> str:
    """Refresh {SESSION_STORAGE_DIR}/{user_id}/skills.list from skills dirs."""
    return write_user_skills_list(user_id)


def get_user_ess_dir(user_id: str | None) -> str:
    """Per-user ESS root: ``{SESSION_STORAGE_DIR}/{user_id}/ess``."""
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        segment = "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "ess")


def _ensure_ess_on_path() -> str:
    """Put bundled ``ess/`` on ``sys.path`` so ``doc_list`` is importable."""
    ess_pkg = os.path.join(workingDir, "ess")
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


def ess_test_cases_list_path(user_id: str | None = None) -> str:
    return os.path.join(get_user_ess_dir(user_id), "test_cases_list.json")


def _ess_docs_dest_path(docs_dir: str, filename: str) -> tuple[str, str, str]:
    """Return ``(dest_path, sanitized_name, original_basename)``."""
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


def _mirror_testcases_to_app_data(user_id: str | None) -> dict[str, int]:
    """Publish saved test cases via S3 so the ECS Web UI can mirror them.

    Runtime IAM Denys ``PutObject`` on ``app-data/`` and ``agentcore-sessions/``.
    Uploads go to ``session-uploads/{user}/ess/test_cases/`` (allowed prefix);
    ECS ``sync_user_ess_testcases_from_runtime_storage`` copies from there into
    ``app-data/`` without waiting for S3 Files NFS lag on ``agentcore-sessions/``.
    """
    segment = sanitize_user_path_segment(user_id)
    if not segment or not os.path.isdir("/mnt/workspace"):
        return {"copied": 0}

    cfg = load_config() or {}
    bucket = (cfg.get("s3_bucket") or "").strip()
    region = (cfg.get("region") or "us-west-2").strip()
    if not bucket:
        logger.warning("skip ess test_cases mirror: s3_bucket not configured")
        return {"copied": 0}

    tc_dir = os.path.join(get_user_ess_dir(user_id), "test_cases")
    list_path = ess_test_cases_list_path(user_id)
    # Must stay under Runtime S3 Allow prefixes (session-uploads/), not app-data/.
    dst_cases_prefix = f"session-uploads/{segment}/ess/test_cases/"
    list_dst_key = f"session-uploads/{segment}/ess/test_cases_list.json"

    copied = 0
    try:
        s3 = boto3.client("s3", region_name=region)
        if os.path.isdir(tc_dir):
            for name in sorted(os.listdir(tc_dir)):
                path = os.path.join(tc_dir, name)
                if not os.path.isfile(path):
                    continue
                s3.upload_file(path, bucket, f"{dst_cases_prefix}{name}")
                copied += 1
        if os.path.isfile(list_path):
            s3.upload_file(
                list_path,
                bucket,
                list_dst_key,
                ExtraArgs={
                    "ContentType": "application/json; charset=utf-8",
                    "CacheControl": "no-cache, max-age=0, must-revalidate",
                },
            )
            copied += 1
    except Exception:
        logger.exception("ess test_cases mirror failed user=%s", segment)
        return {"copied": copied}

    if copied:
        logger.info(
            "Published ess test_cases workspace→session-uploads user=%s copied=%s",
            segment,
            copied,
        )
    return {"copied": copied}


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
    """Copy a generated test-case xlsx into ``{user}/ess/test_cases`` and update list."""
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
            with open(cases_src, encoding="utf-8") as cf:
                payload = json.load(cf)
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
    mirror = {"copied": 0}
    try:
        mirror = _mirror_testcases_to_app_data(user_id)
    except Exception:
        logger.exception("ess test_cases post-save mirror failed user=%s", segment)
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
        "mirror": mirror,
    }


def load_config():
    config = None

    try: 
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        config = {}

        session = boto3.Session()
        region = session.region_name
        config['region'] = region
        config['projectName'] = "power-trade"
        
        sts = boto3.client("sts")
        response = sts.get_caller_identity()
        accountId = response["Account"]
        config['accountId'] = accountId
        
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)    
    return config

config = load_config()

accountId = config.get('accountId')
if not accountId:
    sts = boto3.client("sts")
    response = sts.get_caller_identity()
    accountId = response["Account"]
    config['accountId'] = accountId
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

bedrock_region = config.get('region', 'us-west-2')
logger.info(f"bedrock_region: {bedrock_region}")
projectName = config.get('projectName', 'power-trade')
logger.info(f"projectName: {projectName}")

def get_contents_type(file_name):
    if file_name.lower().endswith((".jpg", ".jpeg")):
        content_type = "image/jpeg"
    elif file_name.lower().endswith((".pdf")):
        content_type = "application/pdf"
    elif file_name.lower().endswith((".txt")):
        content_type = "text/plain"
    elif file_name.lower().endswith((".csv")):
        content_type = "text/csv"
    elif file_name.lower().endswith((".ppt", ".pptx")):
        content_type = "application/vnd.ms-powerpoint"
    elif file_name.lower().endswith((".doc", ".docx")):
        content_type = "application/msword"
    elif file_name.lower().endswith((".xls")):
        content_type = "application/vnd.ms-excel"
    elif file_name.lower().endswith((".py")):
        content_type = "text/x-python"
    elif file_name.lower().endswith((".js")):
        content_type = "application/javascript"
    elif file_name.lower().endswith((".md")):
        content_type = "text/markdown"
    elif file_name.lower().endswith((".png")):
        content_type = "image/png"
    elif file_name.lower().endswith((".html", ".htm")):
        content_type = "text/html; charset=utf-8"
    else:
        content_type = "no info"    
    return content_type

# api key to use Tavily Search
def _load_tavily_api_key(app_config: dict) -> str:
    """Load Tavily API key from config.json or Secrets Manager."""
    key = app_config.get("tavily_api_key", "")
    if key:
        return key

    region = app_config.get("region", "us-west-2")
    secret_names = []
    if app_config.get("knowledge_base_name"):
        secret_names.append(f"tavilyapikey-{app_config['knowledge_base_name']}")
    if app_config.get("projectName"):
        secret_names.append(f"tavilyapikey-{app_config['projectName']}")

    secrets_client = boto3.client("secretsmanager", region_name=region)
    for secret_name in dict.fromkeys(secret_names):
        try:
            response = secrets_client.get_secret_value(SecretId=secret_name)
            secret_data = json.loads(response["SecretString"])
            key = secret_data.get("tavily_api_key", "")
            if key:
                logger.info(f"tavily_key loaded from Secrets Manager: {secret_name}")
                return key
        except Exception as e:
            logger.debug(f"Could not load Tavily secret {secret_name}: {e}")
    return ""


tavily_key = _load_tavily_api_key(config)
if tavily_key:
    os.environ["TAVILY_API_KEY"] = tavily_key
    tavily_api_wrapper = TavilySearchAPIWrapper(tavily_api_key=tavily_key)
    logger.info("tavily_key is configured")
else:
    logger.info("tavily_key is not set.")
