#!/usr/bin/env python3
"""H03-01/H03-02/H03-03: audit covers, export device JPEGs, write the manifest.

Reads ``data/seed/cover_mapping.json`` as the single source of truth, verifies
every mapped cover, converts it into the two device roles and emits
``assets-manifest.json``. Source images are opened read-only and never
rewritten, renamed or recompressed in place.

Usage:
    python hardware/tools/prepare_assets.py --audit-only --report hardware/assets/generated/source-audit.json
    python hardware/tools/prepare_assets.py
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import PIL
from PIL import Image

import assets_lib as lib

FILE_TITLE_RE = re.compile(r"PIC-[A-Za-z0-9]+_")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prepare_assets.py",
        description="Audit Zhiya cover sources and export device JPEG covers plus a manifest.",
    )
    parser.add_argument("--repo-root", default=None, help="repository root (default: auto-detect)")
    parser.add_argument("--mapping", default=lib.DEFAULT_MAPPING, help="cover mapping JSON")
    parser.add_argument("--storybooks", default=lib.DEFAULT_STORYBOOKS, help="storybooks seed JSON")
    parser.add_argument("--story-versions", default=lib.DEFAULT_VERSIONS, help="story versions seed JSON")
    parser.add_argument("--source-root", default=lib.DEFAULT_SOURCE_ROOT, help="only covers under this directory are accepted")
    parser.add_argument("--output-dir", default=lib.DEFAULT_OUTPUT_DIR, help="device asset output directory")
    parser.add_argument("--thumb-size", default="240x160", help="THUMBNAIL box, WIDTHxHEIGHT")
    parser.add_argument("--detail-size", default="400x300", help="DETAIL box, WIDTHxHEIGHT")
    parser.add_argument("--quality", type=int, default=85, help="JPEG quality 1-100 (default: 85)")
    parser.add_argument("--background", default="#FFFFFF", help="alpha compositing / padding colour, #RRGGBB")
    parser.add_argument("--pad", action="store_true", help="letterbox onto the exact role box instead of keeping natural size")
    parser.add_argument("--max-thumb-bytes", type=int, default=lib.DEFAULT_ROLE_MAX_BYTES[lib.ROLE_THUMBNAIL])
    parser.add_argument("--max-detail-bytes", type=int, default=lib.DEFAULT_ROLE_MAX_BYTES[lib.ROLE_DETAIL])
    parser.add_argument("--audit-only", action="store_true", help="check sources only; write nothing to the asset directory")
    parser.add_argument("--report", default=None, help="write the audit result JSON to this path")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    return parser


@dataclass
class AuditEntry:
    record: lib.CoverRecord
    probe: lib.SourceProbe | None
    version: lib.VersionInfo | None
    row: dict[str, Any]
    log: lib.IssueLog = field(default_factory=lib.IssueLog)

    @property
    def ok(self) -> bool:
        return not self.log.has_errors


def title_from_file_name(path: Path) -> str | None:
    stem = path.stem
    match = FILE_TITLE_RE.match(stem)
    if not match:
        return None
    return stem[match.end() :]


def audit_sources(
    records: Sequence[lib.CoverRecord],
    repo_root: Path,
    versions_by_book: dict[int, list[lib.VersionInfo]] | None,
    known_storybooks: set[int] | None,
    log: lib.IssueLog,
) -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    for record in records:
        entry_log = lib.IssueLog()
        source_rel = lib.posix_rel(record.source_path, repo_root)

        row: dict[str, Any] = {
            "bookNo": record.book_no,
            "storybookId": record.storybook_id,
            "storyVersionId": None,
            "storyVersionStatus": None,
            "title": None,
            "coverPath": source_rel,
            "exists": False,
            "bytes": 0,
            "sha256": None,
            "format": None,
            "extension": record.source_path.suffix.lower(),
            "formatMatchesExtension": False,
            "width": 0,
            "height": 0,
            "mode": None,
            "hasAlpha": False,
            "frames": 1,
            "exifOrientation": None,
            "verifyOk": False,
            "decodeOk": False,
            "titleMatches": None,
        }

        version, reason = lib.lookup_version(versions_by_book, record.storybook_id)
        if version is not None:
            row["storyVersionId"] = version.version_id
            row["storyVersionStatus"] = version.status
            row["title"] = version.title
        elif reason == "ambiguous":
            entry_log.warning(
                "STORY_VERSION_AMBIGUOUS",
                "more than one seed version exists for this storybook; storyVersionId stays null",
                bookNo=record.book_no,
                storybookId=record.storybook_id,
            )
        elif reason == "not-found":
            entry_log.warning(
                "STORY_VERSION_NOT_FOUND",
                "no seed version matches this storybook_id",
                bookNo=record.book_no,
                storybookId=record.storybook_id,
            )

        if known_storybooks is not None and record.storybook_id not in known_storybooks:
            entry_log.error(
                "STORYBOOK_ID_UNKNOWN",
                "storybook_id is not present in data/seed/storybooks.json",
                bookNo=record.book_no,
                storybookId=record.storybook_id,
            )

        probe: lib.SourceProbe | None = None
        if not record.source_path.is_file():
            entry_log.error(
                "SOURCE_MISSING",
                "cover file does not exist",
                bookNo=record.book_no,
                path=source_rel,
            )
        else:
            row["exists"] = True
            probe = lib.probe_source(record.source_path)
            row.update(
                bytes=probe.bytes,
                sha256=probe.sha256,
                format=probe.format,
                width=probe.width,
                height=probe.height,
                mode=probe.mode,
                hasAlpha=probe.has_alpha,
                frames=probe.frames,
                exifOrientation=probe.exif_orientation,
                verifyOk=probe.verify_ok,
                decodeOk=probe.decode_ok,
            )

            declared = lib.expected_format(record.source_path.suffix)
            row["formatMatchesExtension"] = probe.format is not None and probe.format == declared
            if declared is None:
                entry_log.warning(
                    "SOURCE_EXTENSION_UNKNOWN",
                    "cover file has an extension this tool does not map to a format",
                    bookNo=record.book_no,
                    suffix=record.source_path.suffix,
                )
            elif probe.format != declared:
                entry_log.error(
                    "FORMAT_EXTENSION_MISMATCH",
                    f"extension claims {declared} but the bytes decode as {probe.format}",
                    bookNo=record.book_no,
                    declared=declared,
                    actual=probe.format,
                )
            if probe.bytes == 0:
                entry_log.error("SOURCE_EMPTY", "cover file is empty", bookNo=record.book_no)
            if probe.format is None:
                entry_log.error(
                    "SOURCE_FORMAT_UNKNOWN",
                    "cover bytes are not a decodable image",
                    bookNo=record.book_no,
                    error=probe.error,
                )
            if probe.verify_ok is False:
                entry_log.error(
                    "SOURCE_VERIFY_FAILED",
                    "cover failed Pillow verify(); the file is likely damaged",
                    bookNo=record.book_no,
                    error=probe.error,
                )
            if probe.decode_ok is False:
                entry_log.error(
                    "SOURCE_DECODE_FAILED",
                    "cover could not be fully decoded; the file is truncated or corrupted",
                    bookNo=record.book_no,
                    error=probe.error,
                )
            if probe.decode_ok:
                problems = lib.dimension_problems(probe.width, probe.height)
                if problems:
                    entry_log.error(
                        "SOURCE_DIMENSIONS_ABNORMAL",
                        "cover dimensions are outside the accepted range",
                        bookNo=record.book_no,
                        width=probe.width,
                        height=probe.height,
                        problems=problems,
                    )
            if probe.frames > 1:
                entry_log.warning(
                    "SOURCE_MULTI_FRAME",
                    "cover has several frames; only the first frame is converted",
                    bookNo=record.book_no,
                    frames=probe.frames,
                )

            expected_title = title_from_file_name(record.source_path)
            if expected_title and row["title"]:
                row["titleMatches"] = expected_title.strip() == row["title"].strip()
                if not row["titleMatches"]:
                    entry_log.warning(
                        "TITLE_MISMATCH",
                        "file name title and seed title differ",
                        bookNo=record.book_no,
                        fileNameTitle=expected_title,
                        seedTitle=row["title"],
                    )

        entry = AuditEntry(record=record, probe=probe, version=version, row=row)
        entry.log.extend(entry_log.items)
        row["ok"] = entry.ok
        row["issues"] = entry_log.as_list()
        log.extend(entry_log.items)
        entries.append(entry)
    return entries


def _role_box(role: str, thumb_box: tuple[int, int], detail_box: tuple[int, int]) -> tuple[int, int]:
    return thumb_box if role == lib.ROLE_THUMBNAIL else detail_box


def _role_max_bytes(role: str, thumb_max: int, detail_max: int) -> int:
    return thumb_max if role == lib.ROLE_THUMBNAIL else detail_max


def convert_entries(
    entries: Sequence[AuditEntry],
    repo_root: Path,
    source_root: Path,
    output_dir: Path,
    *,
    thumb_box: tuple[int, int],
    detail_box: tuple[int, int],
    quality: int,
    background: tuple[int, int, int],
    pad: bool,
    thumb_max_bytes: int,
    detail_max_bytes: int,
    log: lib.IssueLog,
    quiet: bool,
) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    source_dir = source_root.resolve()
    output_dir = output_dir.resolve()
    if output_dir == source_dir or source_dir in output_dir.parents:
        raise lib.AssetPipelineError(
            "OUTPUT_OVERLAPS_SOURCE",
            f"output directory {output_dir} must not live under the source directory {source_dir}",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    used_names: dict[str, str] = {}

    for entry in entries:
        record = entry.record
        probe = entry.probe
        if not entry.ok or probe is None or not probe.decode_ok:
            log.error(
                "BOOK_NOT_CONVERTED",
                "cover failed the source audit, so no device asset was produced",
                bookNo=record.book_no,
            )
            continue

        source_rel = lib.posix_rel(record.source_path, repo_root)
        for role in lib.ROLES:
            box = _role_box(role, thumb_box, detail_box)
            name = lib.asset_file_name(record.book_no, role)
            owner = used_names.get(name)
            if owner is not None:
                log.error(
                    "DUPLICATE_ASSET_NAME",
                    "two book numbers collapse to the same device file name; rename one of them",
                    bookNo=record.book_no,
                    role=role,
                    fileName=name,
                    firstBookNo=owner,
                )
                continue
            used_names[name] = record.book_no
            destination = output_dir / name
            try:
                result = lib.convert_cover(
                    record.source_path,
                    destination,
                    box,
                    quality=quality,
                    background=background,
                    pad=pad,
                )
            except Exception as exc:  # noqa: BLE001 - one bad cover must not abort the batch
                log.error(
                    "CONVERSION_FAILED",
                    f"{type(exc).__name__}: {exc}",
                    bookNo=record.book_no,
                    role=role,
                )
                continue

            if result.width > box[0] or result.height > box[1]:
                log.error(
                    "OUTPUT_EXCEEDS_ROLE_BOX",
                    "converted cover is larger than its role box",
                    bookNo=record.book_no,
                    role=role,
                    width=result.width,
                    height=result.height,
                    box=f"{box[0]}x{box[1]}",
                )
            max_bytes = _role_max_bytes(role, thumb_max_bytes, detail_max_bytes)
            if result.file_size > max_bytes:
                log.error(
                    "ASSET_TOO_LARGE",
                    "converted cover exceeds the configured per-role byte budget",
                    bookNo=record.book_no,
                    role=role,
                    fileSize=result.file_size,
                    maxBytes=max_bytes,
                )

            asset = {
                "bookNo": record.book_no,
                "storybookId": record.storybook_id,
                "storyVersionId": entry.version.version_id if entry.version else None,
                "storyVersionStatus": entry.version.status if entry.version else None,
                "title": entry.version.title if entry.version else None,
                "assetRole": role,
                "sourcePath": source_rel,
                "assetPath": lib.posix_rel(destination, repo_root),
                "devicePath": lib.device_path(name),
                "width": result.width,
                "height": result.height,
                "format": "JPEG",
                "fileSize": result.file_size,
                "sha256": result.sha256,
                "sourceFormat": probe.format,
                "sourceWidth": probe.width,
                "sourceHeight": probe.height,
                "sourceFileSize": probe.bytes,
                "sourceSha256": probe.sha256,
                "alphaComposited": result.alpha_composited,
                "exifOrientationApplied": result.exif_orientation_applied,
                "sizeRatio": round(result.file_size / probe.bytes, 6) if probe.bytes else None,
            }
            assets.append(asset)
            if not quiet:
                print(
                    "{book:<7} {role:<10} {sw}x{sh} -> {dw}x{dh}  "
                    "{src:>10} -> {dst:>9}  {ratio:>7.2%}".format(
                        book=record.book_no,
                        role=role,
                        sw=probe.width,
                        sh=probe.height,
                        dw=result.width,
                        dh=result.height,
                        src=lib.human_bytes(probe.bytes),
                        dst=lib.human_bytes(result.file_size),
                        ratio=(result.file_size / probe.bytes) if probe.bytes else 0.0,
                    )
                )
    return assets


def build_manifest(
    *,
    mapping_path: Path,
    repo_root: Path,
    output_dir: Path,
    source_root: Path,
    assets: Sequence[dict[str, Any]],
    thumb_box: tuple[int, int],
    detail_box: tuple[int, int],
    quality: int,
    background: tuple[int, int, int],
    pad: bool,
    thumb_max_bytes: int,
    detail_max_bytes: int,
    entries: Sequence[AuditEntry],
) -> dict[str, Any]:
    counts = {role: sum(1 for asset in assets if asset["assetRole"] == role) for role in lib.ROLES}
    total_asset_bytes = sum(asset["fileSize"] for asset in assets)
    unique_source_bytes = sum(entry.probe.bytes for entry in entries if entry.probe is not None)
    compression_ratio = round(total_asset_bytes / unique_source_bytes, 6) if unique_source_bytes else None

    return {
        "schemaVersion": lib.SCHEMA_VERSION,
        "kind": lib.MANIFEST_KIND,
        "notice": lib.NOTICE,
        "assetSet": {
            "name": "zhiya-cover-demo",
            "sourceMapping": lib.posix_rel(mapping_path, repo_root),
            "sourceMappingSha256": lib.sha256_file(mapping_path),
            "sourceRoot": lib.posix_rel(source_root, repo_root),
            "outputDir": lib.posix_rel(output_dir, repo_root),
            "mappingRecords": len(entries),
            "roles": {
                lib.ROLE_THUMBNAIL: {
                    "maxWidth": thumb_box[0],
                    "maxHeight": thumb_box[1],
                    "maxFileBytes": thumb_max_bytes,
                    "count": counts[lib.ROLE_THUMBNAIL],
                },
                lib.ROLE_DETAIL: {
                    "maxWidth": detail_box[0],
                    "maxHeight": detail_box[1],
                    "maxFileBytes": detail_max_bytes,
                    "count": counts[lib.ROLE_DETAIL],
                },
            },
            "encoder": {
                "tool": "hardware/tools/prepare_assets.py",
                "pillowVersion": PIL.__version__,
                "format": "JPEG",
                "jpegQuality": quality,
                "jpegProgressive": False,
                "jpegOptimize": True,
                "resample": "LANCZOS",
                "background": lib.color_to_hex(background),
                "padding": pad,
                "exif": "applied then stripped",
                "alpha": "composited onto background",
            },
            "targetDisplay": "800x480 RGB565",
            "decoderVerified": False,
            "decoderNote": "JPEG is a proposal for H03; LVGL/ESP-IDF decoder integration and on-device display are unverified and belong to H06.",
        },
        "totals": {
            "assets": len(assets),
            "sourceBytes": unique_source_bytes,
            "assetBytes": total_asset_bytes,
            "compressionRatio": compression_ratio,
        },
        "assets": list(assets),
    }


def build_audit_report(
    *,
    mapping_path: Path,
    repo_root: Path,
    source_root: Path,
    entries: Sequence[AuditEntry],
    log: lib.IssueLog,
    versions_available: bool,
    storybooks_available: bool,
) -> dict[str, Any]:
    passed = sum(1 for entry in entries if entry.ok)
    failed = len(entries) - passed
    return {
        "schemaVersion": lib.SCHEMA_VERSION,
        "kind": "cover-source-audit",
        "notice": lib.NOTICE,
        "mappingPath": lib.posix_rel(mapping_path, repo_root),
        "mappingSha256": lib.sha256_file(mapping_path),
        "sourceRoot": lib.posix_rel(source_root, repo_root),
        "seedChecks": {
            "storyVersionsAvailable": versions_available,
            "storybooksAvailable": storybooks_available,
        },
        "summary": {
            "records": len(entries),
            "passed": passed,
            "failed": failed,
            "warnings": len(log.warnings),
            "errors": len(log.errors),
            "totalBytes": sum(entry.probe.bytes for entry in entries if entry.probe is not None),
            "withAlpha": sum(1 for entry in entries if entry.probe is not None and entry.probe.has_alpha),
            "withExifOrientation": sum(
                1 for entry in entries if entry.probe is not None and entry.probe.exif_orientation is not None
            ),
        },
        "records": [entry.row for entry in entries],
        "issues": log.as_list(),
    }


def _print_issues(log: lib.IssueLog, start: int = 0) -> None:
    for issue in log.items[start:]:
        label = "ERROR" if issue.severity == "error" else "WARN "
        context = " ".join(f"{key}={value}" for key, value in issue.context.items())
        print(f"  {label} {issue.code}: {issue.message}" + (f" [{context}]" if context else ""))


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = lib.IssueLog()

    try:
        repo_root = Path(args.repo_root).resolve() if args.repo_root else lib.repo_root_from(Path(__file__).parent)
        mapping_path = lib.resolve_arg(repo_root, args.mapping)
        source_root = lib.resolve_arg(repo_root, args.source_root)
        output_dir = lib.resolve_arg(repo_root, args.output_dir)
        storybooks_path = lib.resolve_arg(repo_root, args.storybooks)
        versions_path = lib.resolve_arg(repo_root, args.story_versions)
        thumb_box = lib.parse_size(args.thumb_size)
        detail_box = lib.parse_size(args.detail_size)
        background = lib.parse_color(args.background)
        if not 1 <= args.quality <= 100:
            raise lib.AssetPipelineError("QUALITY_INVALID", "--quality must be between 1 and 100")
        if args.max_thumb_bytes <= 0 or args.max_detail_bytes <= 0:
            raise lib.AssetPipelineError("MAX_BYTES_INVALID", "per-role byte budgets must be positive")
    except lib.AssetPipelineError as exc:
        print(f"FATAL [{exc.code}] {exc.message}")
        return 2

    if not mapping_path.is_file():
        print(f"FATAL [FILE_NOT_FOUND] cover mapping not found: {mapping_path}")
        return 2

    try:
        records = lib.load_cover_mapping(mapping_path, repo_root, source_root, log)
    except lib.AssetPipelineError as exc:
        print(f"FATAL [{exc.code}] {exc.message}")
        return 2

    versions_by_book: dict[int, list[lib.VersionInfo]] | None = None
    versions_available = False
    if versions_path.is_file():
        try:
            versions_by_book = lib.load_story_versions(versions_path, log)
            versions_available = True
        except lib.AssetPipelineError as exc:
            log.warning("STORY_VERSIONS_UNUSABLE", exc.message)
    else:
        log.warning("STORY_VERSIONS_MISSING", "story versions seed is absent; storyVersionId stays null")

    known_storybooks: set[int] | None = None
    storybooks_available = False
    if storybooks_path.is_file():
        try:
            known_storybooks = lib.load_book_ids(storybooks_path, log)
            storybooks_available = True
        except lib.AssetPipelineError as exc:
            log.warning("STORYBOOKS_UNUSABLE", exc.message)

    entries = audit_sources(records, repo_root, versions_by_book, known_storybooks, log)

    draft_books = [entry.record.book_no for entry in entries if entry.version is not None and entry.version.status == "DRAFT"]
    if draft_books:
        log.warning(
            "SEED_VERSIONS_DRAFT",
            "all mapped seed versions are DRAFT; these covers are demo-only and must not be presented as published content",
            books=len(draft_books),
        )

    if args.report:
        report_path = lib.resolve_arg(repo_root, args.report)
        lib.write_json(
            report_path,
            build_audit_report(
                mapping_path=mapping_path,
                repo_root=repo_root,
                source_root=source_root,
                entries=entries,
                log=log,
                versions_available=versions_available,
                storybooks_available=storybooks_available,
            ),
        )
        print(f"[audit] report written: {lib.posix_rel(report_path, repo_root)}")

    passed = sum(1 for entry in entries if entry.ok)
    failed = len(entries) - passed
    print(f"[audit] mapping records: {len(records)}  passed: {passed}  failed: {failed}  warnings: {len(log.warnings)}")
    printed_issues = len(log.items)
    _print_issues(log)

    if args.audit_only:
        return 0 if not log.has_errors else 1

    try:
        assets = convert_entries(
            entries,
            repo_root,
            source_root,
            output_dir,
            thumb_box=thumb_box,
            detail_box=detail_box,
            quality=args.quality,
            background=background,
            pad=args.pad,
            thumb_max_bytes=args.max_thumb_bytes,
            detail_max_bytes=args.max_detail_bytes,
            log=log,
            quiet=args.quiet,
        )
    except lib.AssetPipelineError as exc:
        print(f"FATAL [{exc.code}] {exc.message}")
        return 2

    expected_assets = len(records) * len(lib.ROLES)
    total_asset_bytes = sum(asset["fileSize"] for asset in assets)
    unique_source_bytes = sum(entry.probe.bytes for entry in entries if entry.probe is not None)
    ratio = total_asset_bytes / unique_source_bytes if unique_source_bytes else 0.0
    print(
        f"[assets] converted: {len(assets)}/{expected_assets}  "
        f"source: {lib.human_bytes(unique_source_bytes)} -> asset: {lib.human_bytes(total_asset_bytes)}  "
        f"ratio: {ratio:.2%}"
    )
    _print_issues(log, printed_issues)

    if log.has_errors or len(assets) != expected_assets:
        print("[manifest] not written: fix the errors above, then re-run")
        print(f"[result] FAILED errors={len(log.errors)}")
        return 1

    manifest = build_manifest(
        mapping_path=mapping_path,
        repo_root=repo_root,
        output_dir=output_dir,
        source_root=source_root,
        assets=assets,
        thumb_box=thumb_box,
        detail_box=detail_box,
        quality=args.quality,
        background=background,
        pad=args.pad,
        thumb_max_bytes=args.max_thumb_bytes,
        detail_max_bytes=args.max_detail_bytes,
        entries=entries,
    )
    manifest_path = output_dir / lib.MANIFEST_NAME
    lib.write_json(manifest_path, manifest)
    print(f"[manifest] written: {lib.posix_rel(manifest_path, repo_root)}")
    print(f"[result] OK assets={len(assets)} errors=0 warnings={len(log.warnings)}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
