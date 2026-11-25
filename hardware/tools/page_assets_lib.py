"""H03-P1/P2/P3 helpers for the 104 story-page assets.

The cover pipeline remains in ``assets_lib.py``. This module adds the page
specific seed join, source audit, deterministic naming and manifest schema.
It never writes below the source image directory.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import PIL
from PIL import Image

import assets_lib as lib

PAGE_SCHEMA_VERSION = "1.0"
PAGE_MANIFEST_KIND = "device-page-assets"
PAGE_AUDIT_KIND = "page-source-audit"
PAGE_ROLE = "PAGE"
PAGE_ROLES = (PAGE_ROLE,)
PAGE_MANIFEST_NAME = "pages-manifest.json"
PAGE_AUDIT_NAME = "pages-source-audit.json"
DEFAULT_STORY_PAGES = "data/seed/story_pages.json"
DEFAULT_PAGE_SOURCE_ROOT = "knowledge-base_v2.5/图片/内页"
DEFAULT_PAGE_OUTPUT_DIR = "hardware/assets/generated-pages"
DEFAULT_PAGE_BOX = (400, 300)
DEFAULT_PAGE_MAX_BYTES = 256 * 1024
PAGE_NOTICE = (
    "本地课程 DEMO 内页素材：仅供知芽课程学习与演示，不代表正式发布、家长授权或商用许可；"
    "assetPath 不是 API-024 受控媒体 resource key，也不是公网媒体 URL。"
)
PAGE_DECODER_NOTE = (
    "JPEG 是 H03 的设备侧建议格式；ESP-IDF/LVGL 的真实解码、内存与显示效果属于 H06，"
    "尚未在实板验证。"
)
PAGE_SOURCE_ROLES = (
    ("coverMapping", "cover_mapping.json"),
    ("storybooks", "storybooks.json"),
    ("storyVersions", "story_versions.json"),
    ("storyPages", "story_pages.json"),
)


class PagePipelineError(lib.AssetPipelineError):
    """A page pipeline error carrying a stable code for CLI exit handling."""


@dataclass
class PageVersion:
    version_id: int
    storybook_id: int
    version_no: int
    title: str
    status: str
    license_note: str | None
    storybook_source_type: str | None = None


@dataclass
class PageSeed:
    version_id: int
    page_no: int
    image_ref: Any
    image_license_note: str | None
    default_next_page_number: int | None
    body_text: str | None = None


@dataclass
class PageRecord:
    book_no: str
    storybook_id: int
    version: PageVersion
    seed: PageSeed
    source_path: Path | None = None
    source_rel: str | None = None
    probe: lib.SourceProbe | None = None
    issues: list[lib.Issue] = field(default_factory=list)

    @property
    def page_number(self) -> int:
        return self.seed.page_no

    @property
    def story_version_id(self) -> int:
        return self.version.version_id

    @property
    def title(self) -> str:
        return self.version.title

    @property
    def status(self) -> str:
        return self.version.status

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


@dataclass
class PagePlan:
    records: list[PageRecord]
    mapping_records: list[lib.CoverRecord]
    versions: list[PageVersion]
    pages_by_version: dict[int, list[PageSeed]]
    excluded_versions: list[dict[str, Any]]
    source_files: list[dict[str, Any]]
    source_root: Path
    log: lib.IssueLog

    @property
    def books(self) -> int:
        return len({record.book_no for record in self.records})

    @property
    def versions_count(self) -> int:
        return len({record.story_version_id for record in self.records})

    @property
    def unique_source_bytes(self) -> int:
        unique: dict[str, int] = {}
        for record in self.records:
            if record.source_rel is not None and record.probe is not None:
                unique[record.source_rel] = record.probe.bytes
        return sum(unique.values())


def page_file_name(book_no: str, story_version_id: int, page_number: int) -> str:
    """ASCII, deterministic page file name, independent of Chinese paths."""

    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise PagePipelineError("PAGE_NUMBER_INVALID", f"page number must be a positive integer: {page_number!r}")
    if not isinstance(story_version_id, int) or isinstance(story_version_id, bool) or story_version_id < 1:
        raise PagePipelineError(
            "STORY_VERSION_ID_INVALID", f"story version id must be a positive integer: {story_version_id!r}"
        )
    return f"book-{lib.slug_book_no(book_no)}-v{story_version_id}-p{page_number:03d}.jpg"


def device_page_path(file_name: str) -> str:
    return f"/assets/pages/{file_name}"


def source_file_records(repo_root: Path, paths: dict[str, Path]) -> list[dict[str, Any]]:
    """Stable source-file fingerprints used by both audit and manifest."""

    result: list[dict[str, Any]] = []
    for role, default_name in PAGE_SOURCE_ROLES:
        path = paths.get(role)
        if path is None:
            path = repo_root / "data" / "seed" / default_name
        result.append(
            {
                "role": role,
                "path": lib.posix_rel(path, repo_root),
                "sha256": lib.sha256_file(path),
            }
        )
    return result


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _parse_versions(path: Path, log: lib.IssueLog) -> list[PageVersion]:
    payload = lib.load_json(path)
    if not isinstance(payload, list):
        raise PagePipelineError("STORY_VERSIONS_NOT_ARRAY", f"{path} must contain a JSON array")
    versions: list[PageVersion] = []
    seen_ids: dict[int, int] = {}
    seen_storybook_versions: dict[tuple[int, int], int] = {}
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            log.error("VERSION_ENTRY_NOT_OBJECT", "version entry is not a JSON object", index=index)
            continue
        version_id = entry.get("id")
        storybook_id = entry.get("storybook_id")
        version_no = entry.get("version_no", 0)
        if not _positive_int(version_id):
            log.error("VERSION_ID_INVALID", "version id must be a positive integer", index=index, value=repr(version_id))
            continue
        if not _positive_int(storybook_id):
            log.error(
                "VERSION_STORYBOOK_ID_INVALID",
                "storybook_id must be a positive integer",
                index=index,
                value=repr(storybook_id),
            )
            continue
        if not _positive_int(version_no):
            log.error(
                "VERSION_NUMBER_INVALID",
                "version_no must be a positive integer",
                index=index,
                value=repr(version_no),
            )
            continue
        duplicate = seen_ids.get(version_id)
        if duplicate is not None:
            log.error(
                "VERSION_DUPLICATE_ID",
                f"version id {version_id} already appears at index {duplicate}",
                index=index,
                versionId=version_id,
                firstIndex=duplicate,
            )
            continue
        pair = (storybook_id, version_no)
        duplicate_pair = seen_storybook_versions.get(pair)
        if duplicate_pair is not None:
            log.error(
                "VERSION_DUPLICATE_NUMBER",
                "the same storybook and version_no appears more than once",
                index=index,
                storybookId=storybook_id,
                versionNo=version_no,
                firstIndex=duplicate_pair,
            )
            continue
        seen_ids[version_id] = index
        seen_storybook_versions[pair] = index
        title = entry.get("title")
        status = entry.get("status")
        license_note = entry.get("license_note")
        if not isinstance(title, str) or not title.strip():
            log.error("VERSION_TITLE_INVALID", "title must be a non-empty string", versionId=version_id)
            title = ""
        if not isinstance(status, str) or not status.strip():
            log.error("VERSION_STATUS_INVALID", "status must be a non-empty string", versionId=version_id)
            status = ""
        if license_note is not None and not isinstance(license_note, str):
            log.warning("VERSION_LICENSE_INVALID", "license_note should be a string or null", versionId=version_id)
            license_note = None
        versions.append(
            PageVersion(
                version_id=version_id,
                storybook_id=storybook_id,
                version_no=version_no,
                title=title.strip(),
                status=status.strip(),
                license_note=license_note,
            )
        )
    if not versions:
        log.error("VERSIONS_EMPTY", "story_versions.json contains no usable versions")
    return versions


def _parse_pages(path: Path, versions: Sequence[PageVersion], log: lib.IssueLog) -> dict[int, list[PageSeed]]:
    payload = lib.load_json(path)
    if not isinstance(payload, list):
        raise PagePipelineError("STORY_PAGES_NOT_ARRAY", f"{path} must contain a JSON array")
    known_versions = {version.version_id for version in versions}
    pages: dict[int, list[PageSeed]] = {}
    keys: dict[tuple[int, int], int] = {}
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            log.error("PAGE_ENTRY_NOT_OBJECT", "page entry is not a JSON object", index=index)
            continue
        version_id = entry.get("version_id")
        page_no = entry.get("page_no")
        if not _positive_int(version_id):
            log.error("PAGE_VERSION_ID_INVALID", "version_id must be a positive integer", index=index, value=repr(version_id))
            continue
        if version_id not in known_versions:
            log.error(
                "PAGE_VERSION_UNKNOWN",
                "page references a version that is absent from story_versions.json",
                index=index,
                versionId=version_id,
            )
            continue
        if not _positive_int(page_no):
            log.error("PAGE_NUMBER_INVALID", "page_no must be a positive integer", index=index, value=repr(page_no))
            continue
        key = (version_id, page_no)
        duplicate = keys.get(key)
        if duplicate is not None:
            log.error(
                "PAGE_DUPLICATE_NUMBER",
                f"page {page_no} for version {version_id} already appears at index {duplicate}",
                index=index,
                storyVersionId=version_id,
                pageNumber=page_no,
                firstIndex=duplicate,
            )
            continue
        default_next = entry.get("default_next_page_number")
        if default_next is not None and not _positive_int(default_next):
            log.error(
                "PAGE_NEXT_NUMBER_INVALID",
                "default_next_page_number must be null or a positive integer",
                index=index,
                storyVersionId=version_id,
                pageNumber=page_no,
                value=repr(default_next),
            )
            default_next = None
        image_ref = entry.get("image_ref")
        license_note = entry.get("image_license_note")
        if license_note is not None and not isinstance(license_note, str):
            log.warning(
                "PAGE_LICENSE_INVALID",
                "image_license_note should be a string or null",
                versionId=version_id,
                pageNumber=page_no,
            )
            license_note = None
        keys[key] = index
        pages.setdefault(version_id, []).append(
            PageSeed(
                version_id=version_id,
                page_no=page_no,
                image_ref=image_ref,
                image_license_note=license_note,
                default_next_page_number=default_next,
                body_text=entry.get("body_text") if isinstance(entry.get("body_text"), str) else None,
            )
        )
    for version_id, items in pages.items():
        numbers = {item.page_no for item in items}
        if 1 not in numbers:
            log.error(
                "PAGE_FIRST_PAGE_MISSING",
                "the version has no page 1",
                storyVersionId=version_id,
            )
        for item in items:
            next_page = item.default_next_page_number
            if next_page is not None and next_page not in numbers:
                log.error(
                    "PAGE_NEXT_TARGET_MISSING",
                    "default_next_page_number points outside the same version",
                    storyVersionId=version_id,
                    pageNumber=item.page_no,
                    nextPageNumber=next_page,
                )
    return pages


def _validate_page_source(
    record: PageRecord,
    repo_root: Path,
    source_root: Path,
) -> None:
    seed = record.seed
    raw = seed.image_ref
    entry = lib.IssueLog()

    if not isinstance(raw, str) or not raw.strip():
        entry.error(
            "SOURCE_PATH_EMPTY",
            "image_ref must be a non-empty repository-relative path",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            value=repr(raw),
        )
        record.issues.extend(entry.items)
        return

    text = raw.strip()
    if text.lower().startswith(("http://", "https://", "file://")):
        entry.error(
            "SOURCE_PATH_URL",
            "image_ref must be a local file path, not a URL",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            value=text,
        )
        record.issues.extend(entry.items)
        return

    candidate, code, message = lib.repo_relative_candidate(text, repo_root)
    if candidate is None:
        entry.error(
            code,
            message,
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            value=text,
        )
        record.issues.extend(entry.items)
        return

    try:
        candidate.relative_to(source_root.resolve())
    except ValueError:
        entry.error(
            "SOURCE_PATH_OUTSIDE_INNER_ROOT",
            "image_ref resolves outside knowledge-base_v2.5/图片/内页",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            value=text,
            allowed=source_root.as_posix(),
        )
        record.issues.extend(entry.items)
        return

    record.source_path = candidate
    record.source_rel = lib.posix_rel(candidate, repo_root)

    if not candidate.is_file():
        entry.error(
            "SOURCE_MISSING",
            "page image does not exist",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            path=record.source_rel,
        )
        record.issues.extend(entry.items)
        return

    probe = lib.probe_source(candidate)
    record.probe = probe
    declared = lib.expected_format(candidate.suffix)
    if declared is None:
        entry.error(
            "SOURCE_EXTENSION_UNKNOWN",
            "page image extension is not supported",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            suffix=candidate.suffix,
        )
    elif probe.format != declared:
        entry.error(
            "FORMAT_EXTENSION_MISMATCH",
            f"extension claims {declared} but the bytes decode as {probe.format}",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            declared=declared,
            actual=probe.format,
        )
    if probe.bytes == 0:
        entry.error(
            "SOURCE_EMPTY",
            "page image is empty",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
        )
    if probe.format is None:
        entry.error(
            "SOURCE_FORMAT_UNKNOWN",
            "page bytes are not a decodable image",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            error=probe.error,
        )
    if not probe.verify_ok:
        entry.error(
            "SOURCE_VERIFY_FAILED",
            "page image failed Pillow verify(); it is likely damaged",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            error=probe.error,
        )
    if not probe.decode_ok:
        entry.error(
            "SOURCE_DECODE_FAILED",
            "page image could not be fully decoded; it is truncated or corrupted",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            error=probe.error,
        )
    if probe.decode_ok:
        problems = lib.dimension_problems(probe.width, probe.height)
        if problems:
            entry.error(
                "SOURCE_DIMENSIONS_ABNORMAL",
                "page image dimensions are outside the accepted range",
                bookNo=record.book_no,
                storyVersionId=record.story_version_id,
                pageNumber=record.page_number,
                width=probe.width,
                height=probe.height,
                problems=problems,
            )
    if probe.frames > 1:
        entry.error(
            "SOURCE_MULTI_FRAME",
            "animated or multi-frame images are not accepted for page assets",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
            frames=probe.frames,
        )
    record.issues.extend(entry.items)


def load_page_plan(
    *,
    repo_root: Path,
    mapping_path: Path,
    storybooks_path: Path,
    versions_path: Path,
    pages_path: Path,
    page_source_root: Path,
    cover_source_root: Path,
) -> PagePlan:
    """Read the real seeds and bind every original page to its source image."""

    log = lib.IssueLog()
    if not mapping_path.is_file():
        raise PagePipelineError("COVER_MAPPING_MISSING", f"cover mapping not found: {mapping_path}")
    if not versions_path.is_file():
        raise PagePipelineError("STORY_VERSIONS_MISSING", f"story versions seed not found: {versions_path}")
    if not pages_path.is_file():
        raise PagePipelineError("STORY_PAGES_MISSING", f"story pages seed not found: {pages_path}")

    mapping_records = lib.load_cover_mapping(mapping_path, repo_root, cover_source_root, log)
    versions = _parse_versions(versions_path, log)
    pages_by_version = _parse_pages(pages_path, versions, log)
    known_storybooks: set[int] = set()
    if storybooks_path.is_file():
        known_storybooks = lib.load_book_ids(storybooks_path, log)
    else:
        log.error("STORYBOOKS_MISSING", "storybooks seed is absent", path=storybooks_path.as_posix())

    version_by_storybook: dict[int, list[PageVersion]] = {}
    for version in versions:
        version_by_storybook.setdefault(version.storybook_id, []).append(version)

    records: list[PageRecord] = []
    mapped_version_ids: set[int] = set()
    for mapping in mapping_records:
        if mapping.storybook_id not in known_storybooks:
            log.error(
                "STORYBOOK_ID_UNKNOWN",
                "cover mapping storybook_id is absent from storybooks.json",
                bookNo=mapping.book_no,
                storybookId=mapping.storybook_id,
            )
            continue
        matching_versions = version_by_storybook.get(mapping.storybook_id, [])
        if len(matching_versions) != 1:
            log.error(
                "STORY_VERSION_LOOKUP_FAILED",
                "each mapped storybook must have exactly one story version",
                bookNo=mapping.book_no,
                storybookId=mapping.storybook_id,
                matches=len(matching_versions),
            )
            continue
        version = matching_versions[0]
        mapped_version_ids.add(version.version_id)
        page_items = sorted(pages_by_version.get(version.version_id, []), key=lambda item: item.page_no)
        if not page_items:
            log.error(
                "STORY_PAGES_EMPTY",
                "mapped version has no pages",
                bookNo=mapping.book_no,
                storyVersionId=version.version_id,
            )
            continue
        page_numbers = [item.page_no for item in page_items]
        if sorted(page_numbers) != list(range(1, max(page_numbers) + 1)):
            log.warning(
                "PAGE_NUMBER_GAP",
                "page numbers are not contiguous from 1; the manifest keeps the seed values",
                bookNo=mapping.book_no,
                storyVersionId=version.version_id,
                pageNumbers=page_numbers,
            )
        if version.status == "DRAFT":
            log.warning(
                "PAGE_VERSION_DRAFT",
                "mapped version is DRAFT; generated assets remain demo-only",
                bookNo=mapping.book_no,
                storyVersionId=version.version_id,
            )
        for seed in page_items:
            record = PageRecord(
                book_no=mapping.book_no,
                storybook_id=mapping.storybook_id,
                version=version,
                seed=seed,
            )
            _validate_page_source(record, repo_root, page_source_root)
            records.extend([record])
            log.extend(record.issues)

    records.sort(key=lambda item: (item.book_no, item.story_version_id, item.page_number))

    # A source file or byte-identical image used for two different pages is
    # suspicious, but can be legitimate. Report it for human review, never
    # silently swap or deduplicate page records.
    path_owners: dict[str, tuple[str, int, int]] = {}
    hash_owners: dict[str, tuple[str, int, int]] = {}
    for record in records:
        if record.source_rel:
            previous = path_owners.get(record.source_rel)
            if previous is not None:
                log.warning(
                    "PAGE_SOURCE_PATH_REUSED",
                    "the same source path is used by more than one page",
                    bookNo=record.book_no,
                    storyVersionId=record.story_version_id,
                    pageNumber=record.page_number,
                    previousBookNo=previous[0],
                    previousVersionId=previous[1],
                    previousPageNumber=previous[2],
                )
            else:
                path_owners[record.source_rel] = (record.book_no, record.story_version_id, record.page_number)
        if record.probe is not None:
            previous_hash = hash_owners.get(record.probe.sha256)
            if previous_hash is not None:
                log.warning(
                    "PAGE_SOURCE_HASH_REUSED",
                    "byte-identical images are used by more than one page; review before publishing",
                    bookNo=record.book_no,
                    storyVersionId=record.story_version_id,
                    pageNumber=record.page_number,
                    previousBookNo=previous_hash[0],
                    previousVersionId=previous_hash[1],
                    previousPageNumber=previous_hash[2],
                )
            else:
                hash_owners[record.probe.sha256] = (record.book_no, record.story_version_id, record.page_number)

    excluded: list[dict[str, Any]] = []
    for version in versions:
        if version.version_id in mapped_version_ids:
            continue
        excluded.append(
            {
                "storyVersionId": version.version_id,
                "storybookId": version.storybook_id,
                "status": version.status,
                "pageCount": len(pages_by_version.get(version.version_id, [])),
                "reason": "not part of the 20 original cover mapping; kept out of page assets",
            }
        )
    if not records:
        log.error("NO_PAGE_RECORDS", "no page records could be bound to the original storybooks")
    if len(records) < 1:
        log.error("PAGE_RECORD_COUNT_EMPTY", "page manifest would be empty")

    source_paths = {
        "coverMapping": mapping_path,
        "storybooks": storybooks_path,
        "storyVersions": versions_path,
        "storyPages": pages_path,
    }
    source_files = source_file_records(repo_root, source_paths)

    return PagePlan(
        records=records,
        mapping_records=mapping_records,
        versions=versions,
        pages_by_version=pages_by_version,
        excluded_versions=excluded,
        source_files=source_files,
        source_root=page_source_root,
        log=log,
    )


def _dimension_counter(records: Sequence[PageRecord]) -> dict[str, int]:
    values: dict[str, int] = {}
    for record in records:
        if record.probe is not None:
            key = f"{record.probe.width}x{record.probe.height}"
            values[key] = values.get(key, 0) + 1
    return dict(sorted(values.items()))


def build_audit_report(plan: PagePlan, repo_root: Path, page_source_root: Path) -> dict[str, Any]:
    records_payload: list[dict[str, Any]] = []
    for record in plan.records:
        probe = record.probe
        records_payload.append(
            {
                "bookNo": record.book_no,
                "storybookId": record.storybook_id,
                "storyVersionId": record.story_version_id,
                "storyVersionStatus": record.status,
                "title": record.title,
                "pageNumber": record.page_number,
                "sourcePath": record.source_rel,
                "imageLicenseNote": record.seed.image_license_note,
                "defaultNextPageNumber": record.seed.default_next_page_number,
                "exists": record.source_path is not None and record.source_path.is_file(),
                "bytes": probe.bytes if probe else 0,
                "sha256": probe.sha256 if probe else None,
                "format": probe.format if probe else None,
                "extension": record.source_path.suffix.lower() if record.source_path else None,
                "formatMatchesExtension": (
                    probe is not None
                    and record.source_path is not None
                    and probe.format == lib.expected_format(record.source_path.suffix)
                ),
                "width": probe.width if probe else 0,
                "height": probe.height if probe else 0,
                "mode": probe.mode if probe else None,
                "hasAlpha": probe.has_alpha if probe else False,
                "frames": probe.frames if probe else 0,
                "exifOrientation": probe.exif_orientation if probe else None,
                "verifyOk": probe.verify_ok if probe else False,
                "decodeOk": probe.decode_ok if probe else False,
                "ok": record.ok,
                "issues": [issue.as_dict() for issue in record.issues],
            }
        )
    source_bytes = sum(record.probe.bytes for record in plan.records if record.probe is not None)
    unique_source_bytes = plan.unique_source_bytes
    page_counts: dict[str, int] = {}
    for record in plan.records:
        key = str(record.story_version_id)
        page_counts[key] = page_counts.get(key, 0) + 1
    passed = sum(1 for record in plan.records if record.ok)
    failed = len(plan.records) - passed
    formats: dict[str, int] = {}
    for record in plan.records:
        if record.probe and record.probe.format:
            formats[record.probe.format] = formats.get(record.probe.format, 0) + 1
    return {
        "schemaVersion": PAGE_SCHEMA_VERSION,
        "kind": PAGE_AUDIT_KIND,
        "notice": PAGE_NOTICE,
        "coverMapping": plan.source_files[0]["path"],
        "sourceRoot": lib.posix_rel(page_source_root, repo_root),
        "sourceFiles": plan.source_files,
        "excludedVersions": plan.excluded_versions,
        "summary": {
            "mappedCovers": len(plan.mapping_records),
            "books": plan.books,
            "versions": plan.versions_count,
            "pages": len(plan.records),
            "passed": passed,
            "failed": failed,
            "warnings": len(plan.log.warnings),
            "errors": len(plan.log.errors),
            "sourceBytes": source_bytes,
            "uniqueSourceBytes": unique_source_bytes,
            "formats": dict(sorted(formats.items())),
            "dimensions": _dimension_counter(plan.records),
            "withAlpha": sum(1 for record in plan.records if record.probe and record.probe.has_alpha),
            "withExifOrientation": sum(
                1 for record in plan.records if record.probe and record.probe.exif_orientation is not None
            ),
            "pageCounts": dict(sorted(page_counts.items(), key=lambda item: int(item[0]))),
        },
        "records": records_payload,
        "issues": plan.log.as_list(),
    }


def build_page_manifest(
    plan: PagePlan,
    assets: Sequence[dict[str, Any]],
    *,
    repo_root: Path,
    output_dir: Path,
    page_box: tuple[int, int],
    quality: int,
    background: tuple[int, int, int],
    max_page_bytes: int,
) -> dict[str, Any]:
    counts = {PAGE_ROLE: sum(1 for asset in assets if asset.get("assetRole") == PAGE_ROLE)}
    total_asset_bytes = sum(int(asset["fileSize"]) for asset in assets)
    unique_source_bytes = plan.unique_source_bytes
    compression_ratio = round(total_asset_bytes / unique_source_bytes, 6) if unique_source_bytes else None
    assets_sorted = sorted(assets, key=lambda item: (item["bookNo"], item["storyVersionId"], item["pageNumber"]))
    return {
        "schemaVersion": PAGE_SCHEMA_VERSION,
        "kind": PAGE_MANIFEST_KIND,
        "notice": PAGE_NOTICE,
        "assetSet": {
            "name": "zhiya-page-demo",
            "sourceRoot": lib.posix_rel(plan.source_root, repo_root),
            "outputDir": lib.posix_rel(output_dir, repo_root),
            "mappingRecords": len(plan.mapping_records),
            "pageRecords": len(plan.records),
            "books": plan.books,
            "versions": plan.versions_count,
            "excludedVersions": copy.deepcopy(plan.excluded_versions),
            "sourceFiles": copy.deepcopy(plan.source_files),
            "roles": {
                PAGE_ROLE: {
                    "maxWidth": page_box[0],
                    "maxHeight": page_box[1],
                    "maxFileBytes": max_page_bytes,
                    "count": counts[PAGE_ROLE],
                }
            },
            "encoder": {
                "tool": "hardware/tools/prepare_page_assets.py",
                "pillowVersion": PIL.__version__,
                "format": "JPEG",
                "jpegQuality": quality,
                "jpegProgressive": False,
                "jpegOptimize": True,
                "resample": "LANCZOS",
                "background": lib.color_to_hex(background),
                "padding": False,
                "exif": "applied then stripped",
                "alpha": "composited onto background",
            },
            "targetDisplay": "800x480 RGB565",
            "decoderVerified": False,
            "decoderNote": PAGE_DECODER_NOTE,
        },
        "totals": {
            "books": plan.books,
            "versions": plan.versions_count,
            "pages": len(plan.records),
            "assets": len(assets_sorted),
            "sourceBytes": unique_source_bytes,
            "assetBytes": total_asset_bytes,
            "compressionRatio": compression_ratio,
        },
        "assets": assets_sorted,
    }


def jpeg_info(path: Path) -> dict[str, Any]:
    """Read the real JPEG header and identify the SOF marker.

    A generated page must be a baseline SOF0 JPEG, RGB, non-progressive and
    without EXIF. ``Image.open`` is used for decode/metadata and a small marker
    scan is used to distinguish SOF0 from progressive SOF2.
    """

    data = path.read_bytes()
    sof_marker: int | None = None
    width = height = 0
    components = 0
    pos = 2
    if not data.startswith(b"\xff\xd8"):
        raise PagePipelineError("JPEG_SOI_MISSING", f"{path} does not start with a JPEG SOI marker")
    while pos + 1 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if pos + 2 > len(data):
            break
        length = int.from_bytes(data[pos:pos + 2], "big")
        if length < 2 or pos + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            sof_marker = marker
            if pos + 7 <= len(data):
                height = int.from_bytes(data[pos + 3:pos + 5], "big")
                width = int.from_bytes(data[pos + 5:pos + 7], "big")
                components = data[pos + 7]
            break
        pos += length
    with Image.open(path) as image:
        image.load()
        image_format = image.format
        image_mode = image.mode
        actual_width, actual_height = image.size
        has_exif = bool(image.getexif())
        progressive = bool(image.info.get("progressive", False))
    if image_format != "JPEG":
        raise PagePipelineError("JPEG_FORMAT_MISMATCH", f"{path} decodes as {image_format}, not JPEG")
    if image_mode != "RGB":
        raise PagePipelineError("JPEG_MODE_MISMATCH", f"{path} decodes as {image_mode}, not RGB")
    if (actual_width, actual_height) != (width, height):
        raise PagePipelineError(
            "JPEG_HEADER_DIMENSION_MISMATCH",
            f"JPEG header {width}x{height} differs from decoded {actual_width}x{actual_height}",
        )
    return {
        "format": image_format,
        "mode": image_mode,
        "width": actual_width,
        "height": actual_height,
        "components": components,
        "sofMarker": sof_marker,
        "baseline": sof_marker == 0xC0,
        "progressive": progressive,
        "hasExif": has_exif,
        "fileSize": path.stat().st_size,
        "sha256": lib.sha256_file(path),
    }


def retarget_manifest(manifest: dict[str, Any], stage_dir: Path, repo_root: Path) -> dict[str, Any]:
    """Return a validation copy whose asset paths point at the staging directory."""

    copied = copy.deepcopy(manifest)
    copied["assetSet"]["outputDir"] = lib.posix_rel(stage_dir, repo_root)
    for asset in copied.get("assets", []):
        if isinstance(asset, dict) and isinstance(asset.get("assetPath"), str):
            file_name = Path(asset["assetPath"]).name
            asset["assetPath"] = lib.posix_rel(stage_dir / file_name, repo_root)
    return copied
