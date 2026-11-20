"""Controlled image media resolution, full decode validation, and per-version pinning (DCL-005 / API-024)."""

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path, PurePosixPath

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[3]
MEDIA_DIR_PREFIX = ("knowledge-base_v2.5", "图片")
DEFAULT_MEDIA_ROOT = (REPO_ROOT.joinpath(*MEDIA_DIR_PREFIX)).resolve()
DEFAULT_COVER_MAPPING = REPO_ROOT / "data" / "seed" / "cover_mapping.json"
DEFAULT_TEMPLATE_VERSIONS = REPO_ROOT / "data" / "seed" / "story_versions.json"
DEFAULT_TEMPLATE_PAGES = REPO_ROOT / "data" / "seed" / "story_pages.json"
SEED_COVER_SOURCE_REF = "妈妈夸我说的队 · 知芽项目自研绘本"
TEMPLATE_VERSION_LICENSE_NOTE = "模型生成原创文字，配合知芽项目自研非商用模板插画（NO-COMMERCIAL），待家长审核"
TEMPLATE_PAGE_LICENSE = "NO-COMMERCIAL"
AGE_BAND_BOUNDS = {"AGE_3_4": (3, 4), "AGE_4_5": (4, 5), "AGE_5_6": (5, 6)}
ALLOWED_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
MEDIA_TYPE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
IMAGE_FORMATS = ("PNG", "JPEG", "WEBP")
ASSET_KEY_RE = re.compile(r"^asset-([1-9][0-9]{0,15})-(cover|page-([1-9][0-9]{0,15}))$")
BLOB_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp)$")
MAX_ID = 9007199254740991
DEFAULT_DB_PATH = REPO_ROOT / "database" / "instance" / "zhiya.sqlite3"
VerifiedSource = tuple[Path, str, str, bytes]

_PIN_LOCK = threading.RLock()
_DIGEST_LOCK = threading.Lock()
_VERIFIED_DIGESTS: dict[str, str] = {}
_VERIFIED_FILES: dict[tuple[str, str], tuple[int, ...]] = {}


class MediaDependencyError(RuntimeError):
    """The media source or the version snapshot store is temporarily unavailable."""


def verify_and_detect_image(data: bytes) -> str | None:
    """Return the actual MIME type only after verifying the container and decoding every frame."""
    try:
        with Image.open(BytesIO(data), formats=IMAGE_FORMATS) as image:
            fmt = image.format
            if fmt == "PNG" and not data.endswith(b"\0\0\0\0IEND\xae\x42\x60\x82"):
                return None
            if fmt == "JPEG" and not data.endswith(b"\xff\xd9"):
                return None
            if fmt == "WEBP" and int.from_bytes(data[4:8], "little") + 8 != len(data):
                return None
            image.verify()

        # Pillow requires reopening the image after verify() to decode its pixels.
        with Image.open(BytesIO(data), formats=(fmt,)) as image:
            for frame in range(getattr(image, "n_frames", 1)):
                image.seek(frame)
                image.load()
        return Image.MIME[fmt]
    except (OSError, SyntaxError, ValueError, EOFError, Image.DecompressionBombError):
        return None


def _media_root() -> Path:
    """Directory that every media reference is resolved against and confined to."""
    configured = os.environ.get("ZHIYA_MEDIA_ROOT", "").strip()
    target = Path(configured) if configured else DEFAULT_MEDIA_ROOT
    try:
        resolved = target.resolve()
        if not resolved.is_dir():
            raise MediaDependencyError("Media root is unavailable")
    except OSError as exc:
        raise MediaDependencyError("Media root is unavailable") from exc
    return resolved


def _cover_mapping_entries() -> list[dict]:
    configured = os.environ.get("ZHIYA_COVER_MAPPING_PATH", "").strip()
    mapping_path = Path(configured) if configured else DEFAULT_COVER_MAPPING
    try:
        entries = json.loads(mapping_path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise MediaDependencyError("Invalid cover mapping format")
        for item in entries:
            if not isinstance(item, dict) or "storybook_id" not in item or "cover_path" not in item:
                raise MediaDependencyError("Invalid cover mapping entry")
            int(item["storybook_id"])
            if "version_no" in item and item["version_no"] is not None:
                int(item["version_no"])
            if "story_version_id" in item and item["story_version_id"] is not None:
                int(item["story_version_id"])
            if "storyVersionId" in item and item["storyVersionId"] is not None:
                int(item["storyVersionId"])
        return entries
    except MediaDependencyError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise MediaDependencyError("Cover mapping is unavailable") from exc


def ensure_media_dependencies() -> tuple[Path, list[dict]]:
    """Fail closed with MediaDependencyError whenever the active media root or cover mapping is missing."""
    return _media_root(), _cover_mapping_entries()


def _select_cover_path(entries: list[dict], storybook_id: int, version_id: int, version_no: int) -> str | None:
    by_version_id: str | None = None
    by_version_no: str | None = None
    fallback_by_book: str | None = None
    for item in entries:
        raw_vid = item.get("story_version_id")
        if raw_vid is None:
            raw_vid = item.get("storyVersionId")
        if raw_vid is not None:
            if int(raw_vid) == version_id:
                by_version_id = str(item["cover_path"])
            continue
        if int(item["storybook_id"]) != storybook_id:
            continue
        if "version_no" in item and item["version_no"] is not None:
            if int(item["version_no"]) == version_no:
                by_version_no = str(item["cover_path"])
        else:
            fallback_by_book = str(item["cover_path"])
    return by_version_id if by_version_id is not None else (
        by_version_no if by_version_no is not None else fallback_by_book
    )


def _resolved_db_path(db_path: Path | None = None) -> Path:
    if db_path is not None:
        return db_path.resolve()
    configured = os.environ.get("ZHIYA_DB_PATH", "").strip()
    return (Path(configured) if configured else DEFAULT_DB_PATH).resolve()


def _pin_store_root(db_path: Path | None = None) -> Path:
    resolved = _resolved_db_path(db_path)
    return resolved.parent / f".{resolved.name}.media_pins"


def _image_path_parts(raw_ref: str | None) -> tuple[str, ...] | None:
    if not raw_ref or not isinstance(raw_ref, str):
        return None
    ref = raw_ref.strip()
    if not ref or ref == "none" or ref.startswith(("/", "\\")) or "\\" in ref or ":" in ref:
        return None
    posix = PurePosixPath(ref)
    if posix.is_absolute() or ".." in posix.parts:
        return None
    parts = posix.parts
    if parts[:len(MEDIA_DIR_PREFIX)] == MEDIA_DIR_PREFIX:
        parts = parts[len(MEDIA_DIR_PREFIX):]
    if not parts or posix.suffix.lower() not in ALLOWED_IMAGE_TYPES:
        return None
    return parts


def _resolve_verified_source(raw_ref: str | None, root: Path) -> VerifiedSource | None:
    parts = _image_path_parts(raw_ref)
    if parts is None:
        return None
    try:
        candidate = root.joinpath(*parts).resolve()
    except OSError as exc:
        raise MediaDependencyError("Media path resolution failed") from exc
    if not candidate.is_relative_to(root):
        return None
    try:
        if not candidate.is_file():
            return None
        data = candidate.read_bytes()
    except OSError as exc:
        raise MediaDependencyError("Media storage check failed") from exc

    digest = hashlib.sha256(data).hexdigest()
    media_type = _verified_media_type(data, digest)
    if media_type is None:
        return None
    return candidate, digest, media_type, data


def _verified_media_type(data: bytes, digest: str) -> str | None:
    with _DIGEST_LOCK:
        media_type = _VERIFIED_DIGESTS.get(digest)
    if media_type is None:
        media_type = verify_and_detect_image(data)
        if media_type is not None:
            with _DIGEST_LOCK:
                if len(_VERIFIED_DIGESTS) > 512:
                    _VERIFIED_DIGESTS.clear()
                _VERIFIED_DIGESTS[digest] = media_type
    return media_type


def _write_snapshot_file(path: Path, data: bytes) -> None:
    """Publish complete immutable files; concurrent first readers keep the same winner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{path.name}-{os.getpid()}-{threading.get_ident()}")
    try:
        tmp.write_bytes(data)
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
    finally:
        tmp.unlink(missing_ok=True)


def _write_blob_once(store_root: Path, digest: str, media_type: str, data: bytes) -> str:
    blob_name = digest + MEDIA_TYPE_EXTENSIONS[media_type]
    target = store_root / "blobs" / blob_name
    if not target.is_file():
        _write_snapshot_file(target, data)
    return blob_name


@contextmanager
def _open_pinned_blob(blob_name: str):
    if not isinstance(blob_name, str) or not BLOB_NAME_RE.fullmatch(blob_name):
        yield None
        return
    try:
        blobs_dir = (_pin_store_root() / "blobs").resolve()
        candidate = (blobs_dir / blob_name).resolve()
        if not candidate.is_relative_to(blobs_dir):
            yield None
            return
        with candidate.open("rb") as stream:
            yield stream
    except OSError as exc:
        raise MediaDependencyError("Pinned media storage is unavailable") from exc


def _read_pinned_blob(stream, blob_name: str) -> tuple[bytes, str] | None:
    data = stream.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != blob_name.partition(".")[0]:
        with _DIGEST_LOCK:
            _VERIFIED_FILES.pop((stream.name, blob_name), None)
        raise MediaDependencyError("Pinned media does not match its digest")
    media_type = _verified_media_type(data, digest)
    if media_type is None:
        return None
    return data, media_type


def _identity_for_row(version_row: sqlite3.Row | dict, source_type: str) -> dict:
    return {
        "storybookId": int(version_row["storybook_id"]),
        "versionNo": int(version_row["version_no"]),
        "sourceType": source_type,
        "sourceRef": version_row["source_ref"],
    }


def _manifest_paths(store: Path, name: str) -> tuple[Path, Path]:
    bindings = store.with_name(store.name + ".bindings")
    return store / "versions" / f"{name}.json", bindings / f"{name}.sha256"


def _load_version_manifest(meta_path: Path, binding_path: Path, expected_identity: dict, *,
                           draft: bool = False) -> dict | None:
    try:
        binding = binding_path.read_bytes()
    except FileNotFoundError:
        binding = None
    try:
        raw = meta_path.read_bytes()
    except FileNotFoundError as exc:
        if binding is not None:
            raise MediaDependencyError("Bound version media snapshot is unavailable") from exc
        return None
    digest = hashlib.sha256(raw).hexdigest().encode("ascii")
    if binding is not None and binding != digest:
        raise MediaDependencyError("Version media snapshot does not match its binding")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise MediaDependencyError("Invalid version media snapshot") from exc
    if not isinstance(data, dict) or "cover" not in data or not isinstance(data.get("pages"), dict):
        raise MediaDependencyError("Invalid version media snapshot")
    recorded = data.get("identity")
    if draft and isinstance(recorded, dict):
        # Keep the original cover provenance when an editor changes the draft's source.
        source_ref = recorded.get("sourceRef")
        if source_ref is None or isinstance(source_ref, str):
            expected_identity = {**expected_identity, "sourceRef": source_ref}
    if recorded != expected_identity:
        raise MediaDependencyError("Invalid version media snapshot")
    if binding is None:
        # Adopt complete manifests from the previous layout without consulting mutable source files.
        _write_snapshot_file(binding_path, digest)
        if binding_path.read_bytes() != digest:
            raise MediaDependencyError("Version media snapshot does not match its binding")
    return data


def _pin_verified_asset(store_root: Path, resolved: VerifiedSource | None, *, raw_ref: str | None = None) -> dict | None:
    if resolved is None:
        return None
    _, digest, media_type, data = resolved
    blob_name = _write_blob_once(store_root, digest, media_type, data)
    asset = {"blob": blob_name, "mediaType": media_type}
    if raw_ref is not None:
        asset["rawRef"] = raw_ref
    return asset


def _pin_page_asset(store_root: Path, root: Path, raw_ref: str) -> dict | None:
    return _pin_verified_asset(store_root, _resolve_verified_source(raw_ref, root), raw_ref=raw_ref)


def _previously_returned_image_pages(db: sqlite3.Connection, version: sqlite3.Row | dict) -> set[int]:
    """Legacy drafts recorded their image keys in the successful API-025 response."""
    receipt = db.execute(
        """SELECT json_extract(response_body, '$.data.pages') FROM request_executions
           WHERE scope_type = 'STORY_GENERATION' AND scope_id = (
               SELECT owner_guardian_id FROM storybooks WHERE id = ?)
             AND status = 'SUCCEEDED' AND json_extract(response_body, '$.data.storyVersionId') = ?""",
        (version["storybook_id"], version["id"]),
    ).fetchone()
    if not receipt or receipt[0] is None:
        return set()
    return {page["pageNumber"] for page in json.loads(receipt[0]) if page["imageRef"] is not None}


def freeze_approval_page_media(db: sqlite3.Connection, version: sqlite3.Row | dict, *,
                              preview_page_no: int | None = None) -> str:
    """Bind just the previewed page, or all remaining pages at approval."""
    root, _ = ensure_media_dependencies()
    previous = version["approved_page_media_json"]
    entries = json.loads(previous or "{}")
    pages = db.execute("""SELECT page_no, image_ref FROM story_pages
                          WHERE version_id = ? AND (? IS NULL OR page_no = ?)""",
                       (version["id"], preview_page_no, preview_page_no))
    pending = [(page, ref) for page, ref in pages if entries.get(str(page)) is None]
    if pending:
        promised_pages = _previously_returned_image_pages(db, version)
        unbound = [(page, ref) for page, ref in pending if str(page) not in entries or page in promised_pages]
        for page, entry in _review_page_entries(db, version, root, unbound).items():
            if entry is None and page in promised_pages:
                raise MediaDependencyError("Promised page image is unavailable")
            entries[str(page)] = entry
    checked = entries.values() if preview_page_no is None else (entries.get(str(preview_page_no)),)
    if any(not _pinned_blob_is_readable(entry) for entry in checked if entry is not None):
        raise MediaDependencyError("Review page image is unavailable")
    frozen = json.dumps(entries, ensure_ascii=False)
    if frozen != previous:
        db.execute("UPDATE story_versions SET approved_page_media_json = ? WHERE id = ?",
                   (frozen, version["id"]))
    return frozen


def _review_page_entries(db: sqlite3.Connection, version: sqlite3.Row | dict,
                         root: Path, pages: list[tuple[int, str]]) -> dict:
    store = _pin_store_root()
    entries = {}
    prefix = json.loads(version["source_visited_pages_json"]) if version["source_session_id"] is not None else []
    inherited = {page: prefix[page - 1] for page, _ in pages if page <= len(prefix)}
    if inherited:
        source = db.execute("SELECT * FROM story_versions WHERE id = ?", (version["source_version_id"],)).fetchone()
        frozen_pages = pin_version_media(db, source)["pages"]
        entries = {page: frozen_pages.get(str(source_page)) for page, source_page in inherited.items()}
    try:
        for page, raw_ref in pages:
            if page not in entries:
                entries[page] = _pin_page_asset(store, root, raw_ref)
        return entries
    except OSError as exc:
        raise MediaDependencyError("Page image storage is unavailable") from exc


def pin_version_media(db: sqlite3.Connection, version_row: sqlite3.Row | dict, *,
                      source_type: str | None = None, pages: list[sqlite3.Row] | None = None,
                      db_path: Path | None = None, backfill: bool = False) -> dict:
    """Reserve imported covers while a version is editable; freeze its pages once released."""
    version_id = int(version_row["id"])
    storybook_id = int(version_row["storybook_id"])
    version_no = int(version_row["version_no"])
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    src_type = source_type
    if src_type is None:
        if hasattr(version_row, "keys") and "storybook_source_type" in version_row.keys():
            src_type = version_row["storybook_source_type"]
        else:
            src_type = cur.execute("SELECT source_type FROM storybooks WHERE id = ?", (storybook_id,)).fetchone()[0]
    identity = _identity_for_row(version_row, src_type)
    if "approved_page_media_json" in version_row.keys() and version_row["approved_page_media_json"] is not None:
        if not backfill:
            ensure_media_dependencies()
        return {"identity": identity, "cover": None, "pages": json.loads(version_row["approved_page_media_json"])}
    draft = version_row["status"] in ("DRAFT", "CHECK_FAILED", "PENDING_REVIEW")
    has_seed_cover = src_type == "LIBRARY" and version_row["source_ref"] == SEED_COVER_SOURCE_REF
    try:
        store_root = _pin_store_root(db_path)
        original_paths = _manifest_paths(store_root, str(version_id))
        draft_paths = _manifest_paths(store_root, f"{version_id}.draft")
        released_paths = _manifest_paths(store_root, f"{version_id}.released")
        dependencies = None if backfill else ensure_media_dependencies()
        with _PIN_LOCK:
            if not draft:
                released = _load_version_manifest(*released_paths, identity)
                if released is not None:
                    return released
            staged = _load_version_manifest(*draft_paths, identity, draft=True)
            if staged is not None and staged["identity"] != identity and has_seed_cover:
                # A previously non-seed draft can reserve a seed cover without overwriting its old receipt.
                draft_paths = _manifest_paths(store_root, f"{version_id}.draft-seed")
                seed_manifest = _load_version_manifest(*draft_paths, identity)
                if seed_manifest is not None:
                    staged = seed_manifest
            if draft and staged is not None:
                if staged["identity"] == identity:
                    return staged
                if not has_seed_cover:
                    return {"identity": identity, "cover": None, "pages": {}}
            original = None
            if staged is None:
                original = _load_version_manifest(*original_paths, identity, draft=draft)
            if not draft and original is not None:
                return original

            # On upgrade, an old manifest seen while still DRAFT can supply its cover, never its pages.
            # An already released legacy version keeps its original manifest byte for byte.
            previous = original if draft else staged
            cover = None
            if has_seed_cover and previous is not None and previous["identity"] == identity:
                cover = previous["cover"]
            if draft:
                meta_path, binding_path = draft_paths
                pages = []
            else:
                meta_path, binding_path = released_paths if staged is not None else original_paths
            if pages is None:
                pages = cur.execute("SELECT page_no, image_ref FROM story_pages WHERE version_id = ? ORDER BY page_no",
                                    (version_id,)).fetchall()
            # Older sessions may predate binding receipts. Text-only versions have no image to rebind.
            if not backfill and (has_seed_cover or any(_image_path_parts(p["image_ref"]) for p in pages)):
                if cur.execute("SELECT 1 FROM reading_sessions WHERE version_id = ? LIMIT 1", (version_id,)).fetchone():
                    raise MediaDependencyError("Reading session requires its original media snapshot")
            root, entries = dependencies or ensure_media_dependencies()
            if cover is None and has_seed_cover:
                cover_path = _select_cover_path(entries, storybook_id, version_id, version_no)
                cover = _pin_verified_asset(store_root, _resolve_verified_source(cover_path, root))
            manifest = {"identity": identity, "cover": cover, "pages": {}}
            for page in pages:
                manifest["pages"][str(page["page_no"])] = _pin_page_asset(store_root, root, page["image_ref"])
            _write_snapshot_file(meta_path, json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
            manifest = _load_version_manifest(meta_path, binding_path, identity)
            if manifest is None:
                raise MediaDependencyError("New version media snapshot is unavailable")
            return manifest
    except OSError as exc:
        raise MediaDependencyError("Version media snapshot storage is unavailable") from exc


def pin_database_versions(db: sqlite3.Connection, *, db_path: Path, require_images_for: set[int] | None = None) -> None:
    """Reserve draft covers and bind released versions to their current page images during import or upgrade."""
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    versions = cur.execute(
        """SELECT v.*, b.source_type AS storybook_source_type FROM story_versions v
           JOIN storybooks b ON b.id = v.storybook_id ORDER BY v.id"""
    ).fetchall()
    for row in versions:
        manifest = pin_version_media(db, row, source_type=row["storybook_source_type"], db_path=db_path, backfill=True)
        if require_images_for and row["id"] in require_images_for:
            if not manifest["cover"] or not manifest["pages"] or not all(manifest["pages"].values()):
                raise MediaDependencyError(f"Missing licensed seed illustration for {row['title']}")


def _file_state(stream) -> tuple[int, ...]:
    stat = os.fstat(stream.fileno())
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _pinned_blob_is_readable(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    blob_name = entry.get("blob")
    with _open_pinned_blob(blob_name) as stream:
        if stream is None:
            return False
        state = _file_state(stream)
        key = (stream.name, blob_name)
        with _DIGEST_LOCK:
            if _VERIFIED_FILES.get(key) == state and blob_name.partition(".")[0] in _VERIFIED_DIGESTS:
                return True
        if _read_pinned_blob(stream, blob_name) is None:
            return False
        if _file_state(stream) != state:
            raise MediaDependencyError("Pinned media changed during validation")
        with _DIGEST_LOCK:
            if len(_VERIFIED_FILES) >= 512:
                _VERIFIED_FILES.clear()
            _VERIFIED_FILES[key] = state
        return True


def ensure_opening_media(db: sqlite3.Connection, version: sqlite3.Row, *, page_no: int = 1) -> None:
    manifest = pin_version_media(db, version)
    entries = [manifest["cover"], manifest["pages"].get("1")]
    if page_no != 1:
        entries.append(manifest["pages"].get(str(page_no)))
    for entry in entries:
        if entry is not None and not _pinned_blob_is_readable(entry):
            raise MediaDependencyError("Pinned opening image is unavailable")


def ensure_version_pages_media(db: sqlite3.Connection, version: sqlite3.Row, page_numbers: list[int]) -> dict:
    """Verify the source version manifest and every referenced page's pinned blob are readable."""
    manifest = pin_version_media(db, version)
    pages = manifest.get("pages", {})
    for page_no in dict.fromkeys(page_numbers):
        entry = pages.get(str(page_no))
        if entry is not None and not _pinned_blob_is_readable(entry):
            raise MediaDependencyError("Pinned page image is unavailable")
    return manifest


def _template_catalog() -> list[dict]:
    try:
        versions = json.loads(DEFAULT_TEMPLATE_VERSIONS.read_text(encoding="utf-8"))
        pages = json.loads(DEFAULT_TEMPLATE_PAGES.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MediaDependencyError("Template illustration catalog is unavailable") from exc
    if not isinstance(versions, list) or not isinstance(pages, list):
        raise MediaDependencyError("Template illustration catalog is invalid")
    pages_by_version: dict[int, list[dict]] = {}
    for page in pages:
        if isinstance(page, dict) and "version_id" in page and "page_no" in page:
            pages_by_version.setdefault(int(page["version_id"]), []).append(page)
    catalog = []
    for version in versions:
        if not isinstance(version, dict) or version.get("source_ref") != SEED_COVER_SOURCE_REF:
            continue
        vid = int(version["id"])
        ordered = sorted(pages_by_version.get(vid, ()), key=lambda item: int(item["page_no"]))
        assets: list[tuple[str, str]] = []
        for item in ordered:
            ref = item.get("image_ref")
            note = item.get("image_license_note")
            if isinstance(note, str) and note.strip() == TEMPLATE_PAGE_LICENSE and _image_path_parts(ref) is not None:
                assets.append((str(ref), TEMPLATE_PAGE_LICENSE))
        if assets:
            catalog.append({
                "version_id": vid,
                "age_min": int(version["age_min"]),
                "age_max": int(version["age_max"]),
                "topics": set(version.get("topics_json") or ()),
                "assets": assets,
            })
    return catalog


def _template_candidates(age_band: str, topic: str, page_count: int,
                         carried_count: int = 0) -> Iterator[list[tuple[str, str]]]:
    low, high = AGE_BAND_BOUNDS.get(age_band, (3, 6))
    try:
        catalog = _template_catalog()
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MediaDependencyError("Template illustration catalog is invalid") from exc
    eligible = (entry for entry in catalog if entry["age_min"] <= low and entry["age_max"] >= high)
    for entry in sorted(eligible, key=lambda entry: (topic not in entry["topics"], entry["version_id"])):
        assets = entry["assets"]
        # Keep page positions for a full-length template. Shorter sets supply just the new pages.
        start = carried_count if len(assets) >= carried_count + page_count else 0
        yield assets[start:start + page_count]


def _resolve_template_illustrations(
    age_band: str, topic: str, page_count: int, *, carried_count: int, allow_partial: bool,
) -> list[tuple[str, VerifiedSource | None]]:
    """Select one template by readable new pages, preserving their positions and verified bytes."""
    root, _ = ensure_media_dependencies()
    best = []
    best_count = 0
    for assets in _template_candidates(age_band, topic, page_count, carried_count):
        if len(assets) <= best_count or (not allow_partial and len(assets) < page_count):
            continue
        images = []
        for ref, _ in assets:
            try:
                source = _resolve_verified_source(ref, root)
            except MediaDependencyError:
                source = None
            if source is None and not allow_partial:
                break
            images.append((ref, source))
        count = sum(source is not None for _, source in images)
        if count == page_count:
            return images
        if allow_partial and count > best_count:
            best, best_count = images, count
    if not allow_partial:
        raise MediaDependencyError("No complete template illustration set is available")
    return best


def select_template_illustrations(age_band: str, topic: str, page_count: int, *,
                                  carried_count: int = 0, allow_partial: bool = False) -> list[tuple[str, str]]:
    """Return licensed references from the template selected for the active media root."""
    selected = _resolve_template_illustrations(age_band, topic, page_count,
                                               carried_count=carried_count, allow_partial=allow_partial)
    return [(ref, TEMPLATE_PAGE_LICENSE) for ref, _ in selected]


def pin_generation_illustrations(age_band: str, topic: str, new_page_count: int,
                                 carried_count: int) -> list[dict | None]:
    """Pin new illustrations before returning their keys. Custom-root continuations allow placeholders."""
    optional = carried_count > 0 and bool(os.environ.get("ZHIYA_MEDIA_ROOT", "").strip())
    selected = _resolve_template_illustrations(age_band, topic, new_page_count,
                                               carried_count=carried_count, allow_partial=optional)
    store = _pin_store_root()
    try:
        images = [_pin_verified_asset(store, source, raw_ref=ref) for ref, source in selected]
    except OSError as exc:
        raise MediaDependencyError("Page image storage is unavailable") from exc
    ensure_generation_illustrations_readable(images)
    return images + [None] * (new_page_count - len(images))


def ensure_generation_illustrations_readable(images: list[dict | None]) -> None:
    if any(not _pinned_blob_is_readable(image) for image in images if image is not None):
        raise MediaDependencyError("Pinned template illustration is unavailable")


def cover_ref_from_manifest(version_id: int, manifest: dict) -> str | None:
    return f"asset-{version_id}-cover" if _pinned_blob_is_readable(manifest.get("cover")) else None


def page_image_ref_from_manifest(version_id: int, page_no: int, manifest: dict) -> str | None:
    entry = manifest.get("pages", {}).get(str(page_no))
    return f"asset-{version_id}-page-{page_no}" if _pinned_blob_is_readable(entry) else None


def page_image_ref_for(db: sqlite3.Connection, version_id: int, page_no: int) -> str | None:
    """Ensure the parent story_version is pinned and return a controlled key if page_no has a valid image."""
    row = db.execute(
        """SELECT v.*, b.source_type AS storybook_source_type FROM story_versions v
           JOIN storybooks b ON b.id = v.storybook_id WHERE v.id = ?""",
        (version_id,),
    ).fetchone()
    if row is None:
        ensure_media_dependencies()
        return None
    manifest = pin_version_media(db, row, source_type=row["storybook_source_type"])
    return page_image_ref_from_manifest(version_id, page_no, manifest)


def read_pinned_asset_bytes(db: sqlite3.Connection, version_row: sqlite3.Row | dict,
                            page_no: int | None) -> tuple[bytes, str] | None:
    """Validate media dependencies and serve verified immutable bytes pinned for this storyVersionId."""
    manifest = pin_version_media(db, version_row)
    if page_no is None:
        entry = manifest.get("cover")
    else:
        entry = manifest.get("pages", {}).get(str(page_no))
    if not isinstance(entry, dict):
        return None
    blob_name = entry.get("blob")
    with _open_pinned_blob(blob_name) as stream:
        return _read_pinned_blob(stream, blob_name) if stream is not None else None


def resolve_cover_asset(storybook_id: int, version_no: int, source_type: str,
                        source_ref: str | None) -> tuple[Path, str] | None:
    root, entries = ensure_media_dependencies()
    if source_type != "LIBRARY" or source_ref != SEED_COVER_SOURCE_REF:
        return None
    cover_path = _select_cover_path(entries, storybook_id, 0, version_no)
    resolved = _resolve_verified_source(cover_path, root)
    if resolved is None:
        return None
    return resolved[0], resolved[2]


def parse_asset_key(resource_key: str) -> tuple[int, int | None] | None:
    """Parse asset-{version_id}-cover or asset-{version_id}-page-{page_no} into (version_id, page_no)."""
    match = ASSET_KEY_RE.fullmatch(resource_key)
    if not match:
        return None
    version_id = int(match.group(1))
    page_no = int(match.group(3)) if match.group(3) is not None else None
    if version_id > MAX_ID or (page_no is not None and page_no > MAX_ID):
        return None
    return version_id, page_no
