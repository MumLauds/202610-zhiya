"""Shared helpers for the H03 cover-asset pipeline.

The knowledge base is treated as read-only: this module only opens source
images for decoding, and every write happens under the device asset directory.
Dependencies are the standard library plus Pillow.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Sequence

from PIL import Image, ImageOps

SCHEMA_VERSION = "1.0"
MANIFEST_KIND = "device-cover-assets"

ROLE_THUMBNAIL = "THUMBNAIL"
ROLE_DETAIL = "DETAIL"
ROLES: tuple[str, str] = (ROLE_THUMBNAIL, ROLE_DETAIL)
ROLE_SHORT = {ROLE_THUMBNAIL: "thumb", ROLE_DETAIL: "detail"}

DEFAULT_MAPPING = "data/seed/cover_mapping.json"
DEFAULT_STORYBOOKS = "data/seed/storybooks.json"
DEFAULT_VERSIONS = "data/seed/story_versions.json"
DEFAULT_SOURCE_ROOT = "knowledge-base_v2.5/图片"
DEFAULT_OUTPUT_DIR = "hardware/assets/generated"
MANIFEST_NAME = "assets-manifest.json"
AUDIT_NAME = "source-audit.json"

# Device shelf tile / detail cover budgets. Output never exceeds these bounds
# and never upscales a smaller source.
DEFAULT_ROLE_LIMITS = {ROLE_THUMBNAIL: (240, 160), ROLE_DETAIL: (400, 300)}
# Per-file limits keep a single corrupted asset from exhausting the partition.
DEFAULT_ROLE_MAX_BYTES = {ROLE_THUMBNAIL: 64 * 1024, ROLE_DETAIL: 256 * 1024}

# Guards against absurd sources (decompression bombs, degenerate strips).
MAX_SIDE = 10_000
MAX_PIXELS = 50_000_000
MAX_ASPECT_RATIO = 8.0
MIN_SIDE = 1

EXIF_ORIENTATION_TAG = 0x0112
SUFFIX_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
    ".bmp": "BMP",
    ".gif": "GIF",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}

BOOK_NO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

NOTICE = (
    "本地 DEMO 素材：仅供知芽课程项目设备端演示，不代表绘本已获得正式发布授权；"
    "这些 assetPath 不是 API-024 受控媒体 resource key，也不是可供公网访问的媒体 URL。"
)


class AssetPipelineError(Exception):
    """Fatal pipeline error that stops the current script with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.context:
            payload["context"] = dict(self.context)
        return payload


class IssueLog:
    """Collects errors and warnings so a run can report every problem at once."""

    def __init__(self) -> None:
        self.items: list[Issue] = []

    def error(self, code: str, message: str, **context: Any) -> None:
        self.items.append(Issue("error", code, message, context))

    def warning(self, code: str, message: str, **context: Any) -> None:
        self.items.append(Issue("warning", code, message, context))

    def extend(self, issues: Iterable[Issue]) -> None:
        self.items.extend(issues)

    @property
    def errors(self) -> list[Issue]:
        return [item for item in self.items if item.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [item for item in self.items if item.severity == "warning"]

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.items)

    def as_list(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.items]


# --------------------------------------------------------------------------
# Paths, hashing, JSON
# --------------------------------------------------------------------------


def repo_root_from(start: str | Path) -> Path:
    """Walk upwards until a directory that looks like the repository root."""

    current = Path(start).resolve()
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / "data" / "seed").is_dir() or (candidate / ".git").exists():
            return candidate
    raise AssetPipelineError(
        "REPO_ROOT_NOT_FOUND",
        f"cannot locate a repository root above {current}",
    )


def resolve_arg(root: Path, value: str | Path) -> Path:
    """Resolve a CLI path argument against the repository root."""

    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate


def posix_rel(path: Path, root: Path) -> str:
    """Repository-relative POSIX path; refuses paths outside the repository."""

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise AssetPipelineError(
            "PATH_OUTSIDE_REPO",
            f"{path} is outside the repository root {root}",
        ) from exc


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AssetPipelineError("FILE_NOT_FOUND", f"file not found: {path}") from exc
    except OSError as exc:
        raise AssetPipelineError("FILE_UNREADABLE", f"cannot read {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssetPipelineError(
            "JSON_INVALID", f"{path} is not valid JSON: {exc}"
        ) from exc


def write_json(path: Path, payload: Any) -> None:
    """Write UTF-8 JSON with LF endings through an atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


def slug_book_no(book_no: str) -> str:
    """ASCII-only slug: ``B-002`` becomes ``b002`` so the device name stays short."""

    slug = re.sub(r"[^a-z0-9]", "", book_no.strip().lower())
    if not slug:
        raise AssetPipelineError("BOOK_NO_UNUSABLE", f"cannot build a file name from {book_no!r}")
    return slug


def asset_file_name(book_no: str, role: str) -> str:
    if role not in ROLE_SHORT:
        raise AssetPipelineError("ROLE_UNKNOWN", f"unknown asset role: {role}")
    return f"book-{slug_book_no(book_no)}-{ROLE_SHORT[role]}.jpg"


def device_path(file_name: str) -> str:
    """Advisory firmware-side logical path; not an API-024 resource key."""

    return f"/assets/covers/{file_name}"


def parse_size(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", value.strip().lower())
    if not match:
        raise AssetPipelineError("SIZE_INVALID", f"expected WIDTHxHEIGHT, got {value!r}")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise AssetPipelineError("SIZE_INVALID", f"size must be positive, got {value!r}")
    return width, height


def parse_color(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        raise AssetPipelineError("COLOR_INVALID", f"expected #RRGGBB, got {value!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def color_to_hex(color: Sequence[int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*color)


def human_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.2f} MiB"


# --------------------------------------------------------------------------
# Cover mapping
# --------------------------------------------------------------------------


@dataclass
class CoverRecord:
    index: int
    book_no: str
    storybook_id: int
    cover_path: str
    source_path: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "bookNo": self.book_no,
            "storybookId": self.storybook_id,
            "coverPath": self.cover_path,
        }


def repo_relative_candidate(text: str, repo_root: Path) -> tuple[Path | None, str, str]:
    """Resolve a declared repository path, rejecting absolute and escaping values.

    Returns ``(path, code, message)`` where ``code`` is empty on success.
    """

    normalized = text.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(text)

    if posix.is_absolute() or windows.drive or normalized.startswith("//"):
        return None, "PATH_ABSOLUTE", "path must be repository-relative, not absolute"
    if ".." in posix.parts:
        return None, "PATH_TRAVERSAL", "path contains a parent-directory segment"

    candidate = repo_root.joinpath(*posix.parts).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError:
        return None, "PATH_OUTSIDE_REPO", "path resolves outside the repository"
    return candidate, "", ""


def _validate_mapping_path(
    raw: str,
    repo_root: Path,
    source_root: Path,
    log: IssueLog,
    index: int,
    book_no: str,
) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        log.error("PATH_EMPTY", "cover_path is empty", index=index, bookNo=book_no)
        return None

    text = raw.strip()
    candidate, code, message = repo_relative_candidate(text, repo_root)
    if candidate is None:
        log.error(code, message, index=index, bookNo=book_no, value=text)
        return None

    try:
        candidate.relative_to(source_root.resolve())
    except ValueError:
        log.error(
            "PATH_OUTSIDE_SOURCE_ROOT",
            "cover_path points outside the expected cover directory",
            index=index,
            bookNo=book_no,
            value=text,
            allowed=source_root.as_posix(),
        )
        return None
    return candidate


def load_cover_mapping(
    mapping_path: Path,
    repo_root: Path,
    source_root: Path,
    log: IssueLog,
) -> list[CoverRecord]:
    """Validate ``data/seed/cover_mapping.json`` without mutating any record."""

    payload = load_json(mapping_path)
    if not isinstance(payload, list):
        raise AssetPipelineError(
            "MAPPING_NOT_ARRAY", f"{mapping_path} must contain a JSON array"
        )
    if not payload:
        log.error("MAPPING_EMPTY", "cover mapping contains no records")

    records: list[CoverRecord] = []
    seen_book_no: dict[str, int] = {}
    seen_storybook: dict[int, int] = {}

    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            log.error(
                "MAPPING_ENTRY_NOT_OBJECT",
                "mapping entry is not a JSON object",
                index=index,
            )
            continue

        missing = [key for key in ("book_no", "storybook_id", "cover_path") if key not in entry]
        if missing:
            log.error(
                "MAPPING_FIELD_MISSING",
                "mapping entry is missing required fields: " + ", ".join(missing),
                index=index,
                missingFields=missing,
            )
            continue

        book_no = entry["book_no"]
        storybook_id = entry["storybook_id"]
        cover_path = entry["cover_path"]

        if not isinstance(book_no, str) or not BOOK_NO_RE.fullmatch(book_no.strip()):
            log.error(
                "MAPPING_BOOK_NO_INVALID",
                "book_no must be a short ASCII identifier such as B-002",
                index=index,
                value=repr(book_no),
            )
            continue
        if isinstance(storybook_id, bool) or not isinstance(storybook_id, int) or storybook_id <= 0:
            log.error(
                "MAPPING_STORYBOOK_ID_INVALID",
                "storybook_id must be a positive integer",
                index=index,
                value=repr(storybook_id),
                bookNo=book_no,
            )
            continue

        book_no = book_no.strip()
        duplicate_no = seen_book_no.get(book_no)
        if duplicate_no is not None:
            log.error(
                "MAPPING_DUPLICATE_BOOK_NO",
                f"book_no {book_no} already appears at index {duplicate_no}",
                index=index,
                bookNo=book_no,
                firstIndex=duplicate_no,
            )
            continue
        duplicate_id = seen_storybook.get(storybook_id)
        if duplicate_id is not None:
            log.error(
                "MAPPING_DUPLICATE_STORYBOOK_ID",
                f"storybook_id {storybook_id} already appears at index {duplicate_id}",
                index=index,
                bookNo=book_no,
                storybookId=storybook_id,
                firstIndex=duplicate_id,
            )
            continue

        source_path = _validate_mapping_path(cover_path, repo_root, source_root, log, index, book_no)
        if source_path is None:
            continue

        seen_book_no[book_no] = index
        seen_storybook[storybook_id] = index
        records.append(
            CoverRecord(
                index=index,
                book_no=book_no,
                storybook_id=storybook_id,
                cover_path=cover_path.strip(),
                source_path=source_path,
            )
        )
    return records


@dataclass
class VersionInfo:
    version_id: int
    storybook_id: int
    title: str
    status: str
    version_no: int


def load_story_versions(path: Path, log: IssueLog) -> dict[int, list[VersionInfo]]:
    """Index seed versions by storybook_id. Storybook id is never used as a version id."""

    payload = load_json(path)
    if not isinstance(payload, list):
        raise AssetPipelineError(
            "VERSIONS_NOT_ARRAY", f"{path} must contain a JSON array"
        )

    by_storybook: dict[int, list[VersionInfo]] = {}
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            log.warning("VERSIONS_ENTRY_NOT_OBJECT", "version entry is not an object", index=index)
            continue
        version_id = entry.get("id")
        storybook_id = entry.get("storybook_id")
        if isinstance(version_id, bool) or not isinstance(version_id, int):
            log.warning("VERSIONS_ID_INVALID", "version id is not an integer", index=index)
            continue
        if isinstance(storybook_id, bool) or not isinstance(storybook_id, int):
            log.warning(
                "VERSIONS_STORYBOOK_ID_INVALID",
                "version storybook_id is not an integer",
                index=index,
            )
            continue
        by_storybook.setdefault(storybook_id, []).append(
            VersionInfo(
                version_id=version_id,
                storybook_id=storybook_id,
                title=str(entry.get("title", "")),
                status=str(entry.get("status", "")),
                version_no=int(entry.get("version_no", 0) or 0),
            )
        )
    return by_storybook


def lookup_version(
    by_storybook: dict[int, list[VersionInfo]] | None,
    storybook_id: int,
) -> tuple[VersionInfo | None, str]:
    """Return the single known version for a storybook, or ``None`` plus a reason.

    The seed currently holds one version per original storybook, so a lookup
    result is reliable. When several versions exist the answer is ambiguous and
    must stay null instead of guessing.
    """

    if by_storybook is None:
        return None, "seed-unavailable"
    matches = by_storybook.get(storybook_id, [])
    if not matches:
        return None, "not-found"
    if len(matches) > 1:
        return None, "ambiguous"
    return matches[0], "ok"


def load_book_ids(path: Path, log: IssueLog) -> set[int]:
    payload = load_json(path)
    if not isinstance(payload, list):
        raise AssetPipelineError("STORYBOOKS_NOT_ARRAY", f"{path} must contain a JSON array")
    ids: set[int] = set()
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            log.warning("STORYBOOKS_ENTRY_NOT_OBJECT", "storybook entry is not an object", index=index)
            continue
        value = entry.get("id")
        if isinstance(value, bool) or not isinstance(value, int):
            log.warning("STORYBOOKS_ID_INVALID", "storybook id is not an integer", index=index)
            continue
        ids.add(value)
    return ids


# --------------------------------------------------------------------------
# Image probing
# --------------------------------------------------------------------------


def image_has_alpha(image: Image.Image) -> bool:
    mode = image.mode
    if mode in {"RGBA", "LA", "PA", "RGBa", "La"}:
        return True
    return "transparency" in image.info


def read_exif_orientation(image: Image.Image) -> int | None:
    try:
        value = image.getexif().get(EXIF_ORIENTATION_TAG)
    except Exception:  # pragma: no cover - malformed EXIF is reported elsewhere
        return None
    if not value:
        return None
    try:
        orientation = int(value)
    except (TypeError, ValueError):
        return None
    return orientation if orientation != 1 else None


@dataclass
class SourceProbe:
    path: Path
    bytes: int
    sha256: str
    format: str | None
    mode: str | None
    width: int
    height: int
    frames: int
    has_alpha: bool
    exif_orientation: int | None
    verify_ok: bool
    decode_ok: bool
    error: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "mode": self.mode,
            "width": self.width,
            "height": self.height,
            "frames": self.frames,
            "hasAlpha": self.has_alpha,
            "exifOrientation": self.exif_orientation,
            "verifyOk": self.verify_ok,
            "decodeOk": self.decode_ok,
            "error": self.error,
        }


def probe_source(path: Path) -> SourceProbe:
    """Decode a source image twice: ``verify()`` for structural damage, ``load()`` for truncation."""

    size = path.stat().st_size
    digest = sha256_file(path)
    fmt: str | None = None
    mode: str | None = None
    width = height = 0
    frames = 1
    alpha = False
    orientation: int | None = None
    verify_ok = False
    decode_ok = False
    error: str | None = None

    try:
        with Image.open(path) as image:
            fmt = image.format
            mode = image.mode
            width, height = image.size
            frames = int(getattr(image, "n_frames", 1) or 1)
            alpha = image_has_alpha(image)
            try:
                image.verify()
                verify_ok = True
            except Exception as exc:  # noqa: BLE001 - reported as data, not raised
                error = f"verify failed: {type(exc).__name__}: {exc}"
        with Image.open(path) as image:
            orientation = read_exif_orientation(image)
            image.load()
            decode_ok = True
    except Exception as exc:  # noqa: BLE001 - reported as data, not raised
        error = error or f"decode failed: {type(exc).__name__}: {exc}"

    return SourceProbe(
        path=path,
        bytes=size,
        sha256=digest,
        format=fmt,
        mode=mode,
        width=width,
        height=height,
        frames=frames,
        has_alpha=alpha,
        exif_orientation=orientation,
        verify_ok=verify_ok,
        decode_ok=decode_ok,
        error=error,
    )


def expected_format(suffix: str) -> str | None:
    return SUFFIX_FORMATS.get(suffix.lower())


def dimension_problems(width: int, height: int) -> list[str]:
    problems: list[str] = []
    if width < MIN_SIDE or height < MIN_SIDE:
        problems.append("zero or negative extent")
    if width > MAX_SIDE or height > MAX_SIDE:
        problems.append(f"side longer than {MAX_SIDE}px")
    if width * height > MAX_PIXELS:
        problems.append(f"more than {MAX_PIXELS} pixels")
    if width > 0 and height > 0:
        ratio = max(width / height, height / width)
        if ratio > MAX_ASPECT_RATIO:
            problems.append(f"aspect ratio beyond {MAX_ASPECT_RATIO:g}:1")
    return problems


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------


def fit_within(width: int, height: int, box: tuple[int, int]) -> tuple[int, int]:
    """Largest size inside ``box`` with the source aspect ratio; never upscales."""

    max_width, max_height = box
    if width <= 0 or height <= 0:
        raise AssetPipelineError("SOURCE_DIMENSIONS_INVALID", "source has no usable extent")
    scale = min(max_width / width, max_height / height, 1.0)
    target_width = max(1, int(round(width * scale)))
    target_height = max(1, int(round(height * scale)))
    return min(target_width, max_width), min(target_height, max_height)


@dataclass
class ConversionResult:
    width: int
    height: int
    file_size: int
    sha256: str
    alpha_composited: bool
    exif_orientation_applied: bool


def convert_cover(
    source: Path,
    destination: Path,
    box: tuple[int, int],
    *,
    quality: int,
    background: tuple[int, int, int],
    pad: bool,
) -> ConversionResult:
    """Fit a cover into ``box`` and write a baseline JPEG next to the manifest.

    Transparency is composited onto ``background`` before JPEG encoding, because
    JPEG has no alpha channel. EXIF orientation is applied and then dropped, so
    the stored pixels are already upright and no metadata travels to the device.
    """

    if destination.resolve() == source.resolve():
        raise AssetPipelineError(
            "OUTPUT_OVERWRITES_SOURCE",
            f"refusing to write over the source image: {source}",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".tmp")

    with Image.open(source) as image:
        orientation = read_exif_orientation(image)
        alpha = image_has_alpha(image)
        upright = ImageOps.exif_transpose(image)
        try:
            if alpha:
                rgba = upright.convert("RGBA")
                canvas = Image.new("RGBA", rgba.size, background + (255,))
                canvas.alpha_composite(rgba)
                working = canvas.convert("RGB")
                rgba.close()
                canvas.close()
            else:
                working = upright.convert("RGB")

            working.thumbnail(box, Image.Resampling.LANCZOS, reducing_gap=3.0)
            if pad:
                padded = Image.new("RGB", box, background)
                offset = ((box[0] - working.width) // 2, (box[1] - working.height) // 2)
                padded.paste(working, offset)
                working.close()
                working = padded

            working.info.pop("icc_profile", None)
            working.info.pop("exif", None)
            working.save(
                temp,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=False,
                exif=b"",
                icc_profile=None,
            )
            result = ConversionResult(
                width=working.width,
                height=working.height,
                file_size=temp.stat().st_size,
                sha256=sha256_file(temp),
                alpha_composited=alpha,
                exif_orientation_applied=orientation is not None,
            )
        finally:
            upright.close()
            working.close()
    os.replace(temp, destination)
    return result
