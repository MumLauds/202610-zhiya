#!/usr/bin/env python3
"""H03-P4: independently validate the page manifest against seed, sources and files.

The validator does not trust the exporter's aggregate counts. It re-reads the
cover mapping, story versions and story pages, re-binds every asset to its
``(storyVersionId, pageNumber)`` seed record, re-reads the source and output
bytes, and checks Baseline JPEG structure, size, hashes, coverage and unused
files. Any error returns exit code 1; unreadable arguments/manifests return 2.

Usage:
    python hardware/tools/validate_page_assets.py
    python hardware/tools/validate_page_assets.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

import assets_lib as lib
import page_assets_lib as page

REQUIRED_PAGE_ASSET_FIELDS = (
    "bookNo",
    "storybookId",
    "storyVersionId",
    "storyVersionStatus",
    "title",
    "pageNumber",
    "assetRole",
    "sourcePath",
    "assetPath",
    "devicePath",
    "imageLicenseNote",
    "format",
    "width",
    "height",
    "fileSize",
    "sha256",
    "sourceFormat",
    "sourceWidth",
    "sourceHeight",
    "sourceFileSize",
    "sourceSha256",
    "alphaComposited",
    "exifOrientationApplied",
    "sizeRatio",
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_page_assets.py",
        description="Validate page manifest, real seed mapping, source images and device files.",
    )
    parser.add_argument("--repo-root", default=None, help="repository root (default: auto-detect)")
    parser.add_argument("--manifest", default=None, help="default: <output-dir>/pages-manifest.json")
    parser.add_argument("--output-dir", default=None, help="override assetSet.outputDir")
    parser.add_argument("--mapping", default=lib.DEFAULT_MAPPING, help="cover mapping JSON")
    parser.add_argument("--storybooks", default=lib.DEFAULT_STORYBOOKS, help="storybooks seed JSON")
    parser.add_argument("--story-versions", default=lib.DEFAULT_VERSIONS, help="story versions seed JSON")
    parser.add_argument("--story-pages", default=page.DEFAULT_STORY_PAGES, help="story pages seed JSON")
    parser.add_argument("--cover-source-root", default=lib.DEFAULT_SOURCE_ROOT, help="cover source directory")
    parser.add_argument("--source-root", default=page.DEFAULT_PAGE_SOURCE_ROOT, help="page source directory")
    parser.add_argument("--max-page-bytes", type=int, default=None, help="override PAGE maxFileBytes")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _asset_label(asset: Any, index: int) -> str:
    if not isinstance(asset, dict):
        return f"#{index}"
    return f"{asset.get('bookNo', f'#{index}')}/v{asset.get('storyVersionId', '?')}/p{asset.get('pageNumber', '?')}"


def _normalise_repo_path(text: str) -> str:
    return str(PurePosixPath(text.replace("\\", "/").strip()))


def _source_record_key(asset: dict[str, Any]) -> tuple[int, int] | None:
    version = asset.get("storyVersionId")
    number = asset.get("pageNumber")
    if _is_int(version) and _is_int(number):
        return version, number
    return None


def _check_source_files(
    manifest: dict[str, Any],
    plan: page.PagePlan,
    repo_root: Path,
    log: lib.IssueLog,
) -> None:
    asset_set = manifest.get("assetSet")
    source_files = asset_set.get("sourceFiles") if isinstance(asset_set, dict) else None
    if not isinstance(source_files, list):
        log.error("MANIFEST_SOURCE_FILES", "assetSet.sourceFiles must be a JSON array")
        return
    expected = {item["role"]: item for item in plan.source_files}
    seen: dict[str, int] = {}
    for index, item in enumerate(source_files):
        if not isinstance(item, dict):
            log.error("SOURCE_FILE_NOT_OBJECT", "sourceFiles entry is not an object", index=index)
            continue
        role = item.get("role")
        path_value = item.get("path")
        digest = item.get("sha256")
        if not isinstance(role, str) or role not in expected:
            log.error("SOURCE_FILE_ROLE", "sourceFiles role is unknown", index=index, role=repr(role))
            continue
        if role in seen:
            log.error("SOURCE_FILE_DUPLICATE_ROLE", "sourceFiles role appears twice", role=role, firstIndex=seen[role])
            continue
        seen[role] = index
        if not isinstance(path_value, str):
            log.error("SOURCE_FILE_PATH", "sourceFiles path must be a string", role=role)
            continue
        candidate, code, message = lib.repo_relative_candidate(path_value, repo_root)
        if candidate is None:
            log.error(code, message, role=role, value=path_value)
            continue
        actual_digest = lib.sha256_file(candidate) if candidate.is_file() else None
        if actual_digest is None:
            log.error("SOURCE_FILE_MISSING", "source file is missing", role=role, value=path_value)
            continue
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            log.error("SOURCE_FILE_SHA_FORMAT", "source file sha256 is not 64 lower-case hex characters", role=role)
        elif digest != actual_digest:
            log.error(
                "SOURCE_FILE_SHA_MISMATCH",
                "source file hash differs from the manifest",
                role=role,
                manifest=digest,
                actual=actual_digest,
            )
        expected_item = expected[role]
        if _normalise_repo_path(path_value) != _normalise_repo_path(expected_item["path"]):
            log.error(
                "SOURCE_FILE_PATH_MISMATCH",
                "source file path differs from the real seed path",
                role=role,
                manifest=path_value,
                expected=expected_item["path"],
            )
    missing = sorted(set(expected) - set(seen))
    for role in missing:
        log.error("SOURCE_FILE_MISSING_ROLE", "manifest is missing a source fingerprint", role=role)


def _check_totals(
    manifest: dict[str, Any],
    plan: page.PagePlan,
    assets: Sequence[dict[str, Any]],
    output_dir: Path,
    repo_root: Path,
    log: lib.IssueLog,
) -> None:
    totals = manifest.get("totals")
    if not isinstance(totals, dict):
        log.error("MANIFEST_TOTALS", "totals must be a JSON object")
        return
    actual_asset_bytes = 0
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("assetPath"), str):
            continue
        candidate, _, _ = lib.repo_relative_candidate(asset["assetPath"], repo_root)
        if candidate is not None and candidate.is_file():
            actual_asset_bytes += candidate.stat().st_size
    checks = {
        "books": plan.books,
        "versions": plan.versions_count,
        "pages": len(plan.records),
        "assets": len(assets),
        "sourceBytes": plan.unique_source_bytes,
        "assetBytes": actual_asset_bytes,
    }
    for key, expected in checks.items():
        declared = totals.get(key)
        if not _is_int(declared):
            log.error("TOTALS_FIELD_INVALID", f"totals.{key} must be a positive integer", field=key)
        elif declared != expected:
            log.error(
                "TOTALS_MISMATCH",
                f"totals.{key} differs from recomputed value",
                field=key,
                manifest=declared,
                actual=expected,
            )


def _validate_source_binding(
    asset: dict[str, Any],
    expected: page.PageRecord,
    *,
    repo_root: Path,
    plan: page.PagePlan,
    log: lib.IssueLog,
    label: str,
) -> None:
    for field, expected_value in (
        ("bookNo", expected.book_no),
        ("storybookId", expected.storybook_id),
        ("storyVersionId", expected.story_version_id),
        ("storyVersionStatus", expected.status),
        ("title", expected.title),
        ("pageNumber", expected.page_number),
        ("imageLicenseNote", expected.seed.image_license_note),
    ):
        if asset.get(field) != expected_value:
            log.error(
                "PAGE_SEED_BINDING_MISMATCH",
                f"{field} does not match the real seed page record",
                asset=label,
                field=field,
                manifest=repr(asset.get(field)),
                expected=repr(expected_value),
            )

    source_value = asset.get("sourcePath")
    if not isinstance(source_value, str):
        log.error("SOURCE_PATH_INVALID", "sourcePath must be a non-empty string", asset=label)
        return
    source_candidate, code, message = lib.repo_relative_candidate(source_value, repo_root)
    if source_candidate is None:
        log.error(code, message, asset=label, field="sourcePath", value=source_value)
        return
    try:
        source_candidate.relative_to(plan.source_root.resolve())
    except ValueError:
        log.error(
            "SOURCE_PATH_OUTSIDE_INNER_ROOT",
            "sourcePath resolves outside the page source root",
            asset=label,
            value=source_value,
        )
    if expected.source_rel is None or _normalise_repo_path(source_value) != _normalise_repo_path(expected.source_rel):
        log.error(
            "SOURCE_PATH_MISMATCH",
            "sourcePath does not match story_pages.json for this version/page",
            asset=label,
            manifest=source_value,
            expected=expected.source_rel,
        )
    if expected.source_path is None or not expected.source_path.is_file():
        log.error("SOURCE_MISSING", "real source page image is missing", asset=label, expected=expected.source_rel)
        return

    actual_source_sha = lib.sha256_file(expected.source_path)
    actual_source_size = expected.source_path.stat().st_size
    declared_sha = asset.get("sourceSha256")
    if not isinstance(declared_sha, str) or not SHA256_RE.fullmatch(declared_sha):
        log.error("SOURCE_SHA_FORMAT", "sourceSha256 must be 64 lower-case hex characters", asset=label)
    elif declared_sha != actual_source_sha:
        log.error(
            "SOURCE_SHA_MISMATCH",
            "sourceSha256 differs from the real source file",
            asset=label,
            manifest=declared_sha,
            actual=actual_source_sha,
        )
    if asset.get("sourceFileSize") != actual_source_size:
        log.error(
            "SOURCE_SIZE_MISMATCH",
            "sourceFileSize differs from the real source file",
            asset=label,
            manifest=asset.get("sourceFileSize"),
            actual=actual_source_size,
        )
    if expected.probe is None:
        log.error("SOURCE_PROBE_UNAVAILABLE", "source image could not be probed", asset=label)
        return
    for field, expected_value in (
        ("sourceFormat", expected.probe.format),
        ("sourceWidth", expected.probe.width),
        ("sourceHeight", expected.probe.height),
        ("sourceFileSize", expected.probe.bytes),
        ("sourceSha256", expected.probe.sha256),
    ):
        if asset.get(field) != expected_value:
            log.error(
                "SOURCE_PROBE_MISMATCH",
                f"{field} differs from the re-read source image",
                asset=label,
                field=field,
                manifest=repr(asset.get(field)),
                actual=repr(expected_value),
            )


def validate_page_asset(
    asset: Any,
    index: int,
    *,
    repo_root: Path,
    output_dir: Path,
    plan: page.PagePlan,
    expected_by_key: dict[tuple[int, int], page.PageRecord],
    page_box: tuple[int, int],
    max_page_bytes: int,
    log: lib.IssueLog,
) -> None:
    if not isinstance(asset, dict):
        log.error("PAGE_ASSET_NOT_OBJECT", "page asset entry is not a JSON object", index=index, value=repr(asset)[:80])
        return
    label = _asset_label(asset, index)
    missing = [name for name in REQUIRED_PAGE_ASSET_FIELDS if name not in asset]
    if missing:
        log.error(
            "PAGE_ASSET_FIELD_MISSING",
            "page asset is missing required fields: " + ", ".join(missing),
            asset=label,
            missingFields=missing,
        )
        return

    book_no = asset["bookNo"]
    storybook_id = asset["storybookId"]
    version_id = asset["storyVersionId"]
    page_number = asset["pageNumber"]
    role = asset["assetRole"]
    if not isinstance(book_no, str) or not lib.BOOK_NO_RE.fullmatch(book_no):
        log.error("PAGE_ASSET_BOOK_NO", "bookNo is not a short ASCII identifier", asset=label, value=repr(book_no))
    if not _is_int(storybook_id) or storybook_id <= 0:
        log.error("PAGE_ASSET_STORYBOOK_ID", "storybookId must be a positive integer", asset=label)
    if not _is_int(version_id) or version_id <= 0:
        log.error("PAGE_ASSET_VERSION_ID", "storyVersionId must be a positive integer", asset=label)
    if not _is_int(page_number) or page_number <= 0:
        log.error("PAGE_ASSET_PAGE_NUMBER", "pageNumber must be a positive integer", asset=label)
    if role != page.PAGE_ROLE:
        log.error("PAGE_ASSET_ROLE", "assetRole must be PAGE", asset=label, value=repr(role))
        return
    if not isinstance(asset["sha256"], str) or not SHA256_RE.fullmatch(asset["sha256"]):
        log.error("PAGE_ASSET_SHA_FORMAT", "sha256 must be 64 lower-case hex characters", asset=label)
    for name in ("width", "height", "fileSize"):
        if not _is_int(asset[name]) or asset[name] <= 0:
            log.error("PAGE_ASSET_NUMBER", f"{name} must be a positive integer", asset=label, field=name)
    for name in ("alphaComposited", "exifOrientationApplied"):
        if not isinstance(asset[name], bool):
            log.error("PAGE_ASSET_BOOLEAN", f"{name} must be a boolean", asset=label, field=name)

    key = _source_record_key(asset)
    expected = expected_by_key.get(key) if key is not None else None
    if expected is None:
        log.error(
            "PAGE_COVERAGE_UNEXPECTED",
            "asset has no matching (storyVersionId, pageNumber) seed record",
            asset=label,
        )
    else:
        _validate_source_binding(asset, expected, repo_root=repo_root, plan=plan, log=log, label=label)

    asset_path_value = asset["assetPath"]
    if not isinstance(asset_path_value, str) or not asset_path_value.strip():
        log.error("PAGE_ASSET_PATH_INVALID", "assetPath must be a non-empty string", asset=label)
        return
    asset_candidate, asset_code, asset_message = lib.repo_relative_candidate(asset_path_value, repo_root)
    if asset_candidate is None:
        log.error(asset_code, asset_message, asset=label, field="assetPath", value=asset_path_value)
        return
    try:
        asset_candidate.relative_to(output_dir.resolve())
    except ValueError:
        log.error(
            "PAGE_ASSET_OUTSIDE_OUTPUT_DIR",
            "assetPath is outside the declared page output directory",
            asset=label,
            value=asset_path_value,
        )
    if asset_candidate.suffix.lower() not in {".jpg", ".jpeg"}:
        log.error("PAGE_ASSET_NOT_JPEG", "page asset must use a .jpg/.jpeg suffix", asset=label)
    if expected is not None:
        expected_name = page.page_file_name(expected.book_no, expected.story_version_id, expected.page_number)
        expected_path = output_dir / expected_name
        if asset_candidate.resolve() != expected_path.resolve():
            log.error(
                "PAGE_ASSET_PATH_NAME_MISMATCH",
                "assetPath does not use the deterministic page file name",
                asset=label,
                manifest=asset_path_value,
                expected=lib.posix_rel(expected_path, repo_root),
            )
        expected_device = page.device_page_path(expected_name)
        if asset.get("devicePath") != expected_device:
            log.error(
                "PAGE_DEVICE_PATH_MISMATCH",
                "devicePath does not match the deterministic page path",
                asset=label,
                manifest=repr(asset.get("devicePath")),
                expected=expected_device,
            )
    if not asset_candidate.is_file():
        log.error("PAGE_ASSET_FILE_MISSING", "page asset file does not exist", asset=label, value=asset_path_value)
        return

    actual_bytes = asset_candidate.stat().st_size
    actual_sha = lib.sha256_file(asset_candidate)
    if asset.get("fileSize") != actual_bytes:
        log.error(
            "PAGE_ASSET_SIZE_MISMATCH",
            "file size on disk differs from the manifest",
            asset=label,
            manifest=asset.get("fileSize"),
            actual=actual_bytes,
        )
    if isinstance(asset.get("sha256"), str) and asset["sha256"] != actual_sha:
        log.error(
            "PAGE_ASSET_SHA_MISMATCH",
            "SHA-256 on disk differs from the manifest",
            asset=label,
            manifest=asset["sha256"],
            actual=actual_sha,
        )
    if actual_bytes > max_page_bytes:
        log.error(
            "PAGE_ASSET_BYTES_EXCEED_LIMIT",
            "page file exceeds the configured byte limit",
            asset=label,
            actual=actual_bytes,
            maxBytes=max_page_bytes,
        )

    try:
        info = page.jpeg_info(asset_candidate)
    except Exception as exc:  # noqa: BLE001 - report a corrupt/non-JPEG output
        log.error("PAGE_ASSET_UNDECODABLE", f"page asset cannot be decoded: {type(exc).__name__}: {exc}", asset=label)
        return
    if info["format"] != "JPEG" or info["mode"] != "RGB":
        log.error("PAGE_ASSET_FORMAT", "page asset must be a decodable RGB JPEG", asset=label)
    if info["baseline"] is not True or info["progressive"]:
        log.error(
            "PAGE_ASSET_NOT_BASELINE",
            "page asset must be a Baseline, non-progressive JPEG",
            asset=label,
            sofMarker=info["sofMarker"],
            progressive=info["progressive"],
        )
    if info["hasExif"]:
        log.error("PAGE_ASSET_EXIF_PRESENT", "page asset must not carry EXIF metadata", asset=label)
    if _is_int(asset.get("width")) and _is_int(asset.get("height")):
        if (info["width"], info["height"]) != (asset["width"], asset["height"]):
            log.error(
                "PAGE_ASSET_DIMENSION_MISMATCH",
                "decoded dimensions differ from the manifest",
                asset=label,
                manifest=f"{asset['width']}x{asset['height']}",
                actual=f"{info['width']}x{info['height']}",
            )
        if asset["width"] > page_box[0] or asset["height"] > page_box[1]:
            log.error(
                "PAGE_ASSET_DECLARED_SIZE_EXCEEDS_BOX",
                "declared dimensions exceed the PAGE box",
                asset=label,
                box=f"{page_box[0]}x{page_box[1]}",
            )
    if info["width"] > page_box[0] or info["height"] > page_box[1]:
        log.error(
            "PAGE_ASSET_SIZE_EXCEEDS_BOX",
            "decoded dimensions exceed the PAGE box",
            asset=label,
            actual=f"{info['width']}x{info['height']}",
            box=f"{page_box[0]}x{page_box[1]}",
        )
    if expected is not None and expected.probe is not None:
        if asset.get("alphaComposited") is not expected.probe.has_alpha:
            log.error(
                "PAGE_ALPHA_FLAG_MISMATCH",
                "alphaComposited differs from the source alpha state",
                asset=label,
                manifest=repr(asset.get("alphaComposited")),
                expected=expected.probe.has_alpha,
            )
        expected_exif = expected.probe.exif_orientation is not None
        if asset.get("exifOrientationApplied") is not expected_exif:
            log.error(
                "PAGE_EXIF_FLAG_MISMATCH",
                "exifOrientationApplied differs from the source EXIF state",
                asset=label,
                manifest=repr(asset.get("exifOrientationApplied")),
                expected=expected_exif,
            )


def check_duplicates(assets: Sequence[Any], log: lib.IssueLog) -> None:
    keys: dict[tuple[int, int], int] = {}
    paths: dict[str, int] = {}
    devices: dict[str, int] = {}
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            continue
        key = _source_record_key(asset)
        if key is not None:
            if key in keys:
                log.error(
                    "PAGE_DUPLICATE_KEY",
                    "duplicate (storyVersionId, pageNumber) page key",
                    index=index,
                    firstIndex=keys[key],
                    storyVersionId=key[0],
                    pageNumber=key[1],
                )
            else:
                keys[key] = index
        for field, mapping in (("assetPath", paths), ("devicePath", devices)):
            value = asset.get(field)
            if isinstance(value, str):
                if value in mapping:
                    log.error(
                        "PAGE_DUPLICATE_PATH" if field == "assetPath" else "PAGE_DUPLICATE_DEVICE_PATH",
                        f"duplicate {field}",
                        value=value,
                        index=index,
                        firstIndex=mapping[value],
                    )
                else:
                    mapping[value] = index


def check_unreferenced(assets: Sequence[Any], output_dir: Path, repo_root: Path, log: lib.IssueLog) -> None:
    referenced: set[Path] = set()
    for asset in assets:
        if isinstance(asset, dict) and isinstance(asset.get("assetPath"), str):
            candidate, _, _ = lib.repo_relative_candidate(asset["assetPath"], repo_root)
            if candidate is not None:
                referenced.add(candidate.resolve())
    if not output_dir.is_dir():
        return
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if path.resolve() not in referenced:
            log.error(
                "PAGE_UNREFERENCED_ASSET",
                "image in the page output directory is not referenced by the manifest",
                value=lib.posix_rel(path, repo_root),
            )


def check_coverage(
    assets: Sequence[Any],
    plan: page.PagePlan,
    log: lib.IssueLog,
) -> None:
    expected = {(record.story_version_id, record.page_number): record for record in plan.records}
    present = {key for asset in assets if isinstance(asset, dict) for key in [_source_record_key(asset)] if key is not None}
    for key in sorted(set(expected) - present):
        record = expected[key]
        log.error(
            "PAGE_COVERAGE_MISSING",
            "seed page has no matching page asset",
            bookNo=record.book_no,
            storyVersionId=record.story_version_id,
            pageNumber=record.page_number,
        )
    for key in sorted(present - set(expected)):
        log.error(
            "PAGE_COVERAGE_UNEXPECTED",
            "page asset has no matching seed page",
            storyVersionId=key[0],
            pageNumber=key[1],
        )


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = lib.IssueLog()
    try:
        repo_root = Path(args.repo_root).resolve() if args.repo_root else lib.repo_root_from(Path(__file__).parent)
        output_dir = lib.resolve_arg(repo_root, args.output_dir or page.DEFAULT_PAGE_OUTPUT_DIR)
        manifest_path = lib.resolve_arg(repo_root, args.manifest) if args.manifest else output_dir / page.PAGE_MANIFEST_NAME
        mapping_path = lib.resolve_arg(repo_root, args.mapping)
        storybooks_path = lib.resolve_arg(repo_root, args.storybooks)
        versions_path = lib.resolve_arg(repo_root, args.story_versions)
        pages_path = lib.resolve_arg(repo_root, args.story_pages)
        cover_source_root = lib.resolve_arg(repo_root, args.cover_source_root)
        source_root = lib.resolve_arg(repo_root, args.source_root)
    except (lib.AssetPipelineError, OSError) as exc:
        print(f"FATAL [{exc.code}] {exc.message}")
        return 2

    if not manifest_path.is_file():
        print(f"FATAL [MANIFEST_MISSING] manifest not found: {manifest_path}")
        return 2
    try:
        manifest = lib.load_json(manifest_path)
    except (lib.AssetPipelineError, OSError) as exc:
        print(f"FATAL [{exc.code}] {exc.message}")
        return 2
    if not isinstance(manifest, dict):
        print("FATAL [MANIFEST_NOT_OBJECT] page manifest must be a JSON object")
        return 2

    if manifest.get("schemaVersion") != page.PAGE_SCHEMA_VERSION:
        log.error(
            "MANIFEST_SCHEMA_VERSION",
            "unexpected page schemaVersion",
            value=manifest.get("schemaVersion"),
            expected=page.PAGE_SCHEMA_VERSION,
        )
    if manifest.get("kind") != page.PAGE_MANIFEST_KIND:
        log.error(
            "MANIFEST_KIND",
            "unexpected page manifest kind",
            value=manifest.get("kind"),
            expected=page.PAGE_MANIFEST_KIND,
        )

    asset_set = manifest.get("assetSet")
    if not isinstance(asset_set, dict):
        log.error("MANIFEST_ASSET_SET", "assetSet must be a JSON object")
        asset_set = {}
    declared_output = asset_set.get("outputDir")
    if isinstance(declared_output, str):
        candidate, code, message = lib.repo_relative_candidate(declared_output, repo_root)
        if candidate is None:
            log.error(code, message, field="assetSet.outputDir", value=declared_output)
        elif args.output_dir is None:
            output_dir = candidate
        elif candidate.resolve() != output_dir.resolve():
            log.error(
                "MANIFEST_OUTPUT_DIR_MISMATCH",
                "assetSet.outputDir differs from --output-dir",
                manifest=declared_output,
                argument=lib.posix_rel(output_dir, repo_root),
            )
    elif args.output_dir is None:
        log.error("MANIFEST_OUTPUT_DIR", "assetSet.outputDir is missing; using the default output directory")

    roles = asset_set.get("roles")
    page_role = roles.get(page.PAGE_ROLE) if isinstance(roles, dict) else None
    if not isinstance(page_role, dict):
        log.error("MANIFEST_PAGE_ROLE", "assetSet.roles.PAGE must be an object")
        page_box = page.DEFAULT_PAGE_BOX
        max_page_bytes = args.max_page_bytes or page.DEFAULT_PAGE_MAX_BYTES
    else:
        box = (page_role.get("maxWidth"), page_role.get("maxHeight"))
        if not all(_is_int(value) and value > 0 for value in box):
            log.error("MANIFEST_PAGE_BOX", "PAGE maxWidth/maxHeight must be positive integers")
            page_box = page.DEFAULT_PAGE_BOX
        else:
            page_box = (box[0], box[1])
        declared_max = page_role.get("maxFileBytes")
        if args.max_page_bytes is not None:
            max_page_bytes = args.max_page_bytes
        elif _is_int(declared_max) and declared_max > 0:
            max_page_bytes = declared_max
        else:
            log.error("MANIFEST_PAGE_MAX_BYTES", "PAGE maxFileBytes must be a positive integer")
            max_page_bytes = page.DEFAULT_PAGE_MAX_BYTES

    assets = manifest.get("assets")
    if not isinstance(assets, list):
        print("FATAL [MANIFEST_ASSETS_NOT_ARRAY] manifest assets must be an array")
        return 2
    if not assets:
        log.error("MANIFEST_ASSETS_EMPTY", "manifest has no page asset entries")

    try:
        plan = page.load_page_plan(
            repo_root=repo_root,
            mapping_path=mapping_path,
            storybooks_path=storybooks_path,
            versions_path=versions_path,
            pages_path=pages_path,
            page_source_root=source_root,
            cover_source_root=cover_source_root,
        )
    except (page.PagePipelineError, lib.AssetPipelineError) as exc:
        print(f"FATAL [{exc.code}] {exc.message}")
        return 2

    expected_by_key = {(record.story_version_id, record.page_number): record for record in plan.records}
    failed_assets = 0
    for index, asset in enumerate(assets):
        before = len(log.errors)
        validate_page_asset(
            asset,
            index,
            repo_root=repo_root,
            output_dir=output_dir,
            plan=plan,
            expected_by_key=expected_by_key,
            page_box=page_box,
            max_page_bytes=max_page_bytes,
            log=log,
        )
        if len(log.errors) > before:
            failed_assets += 1

    check_duplicates(assets, log)
    check_coverage(assets, plan, log)
    check_unreferenced(assets, output_dir, repo_root, log)
    _check_source_files(manifest, plan, repo_root, log)
    _check_totals(manifest, plan, assets, output_dir, repo_root, log)

    if asset_set.get("pageRecords") != len(plan.records):
        log.error(
            "ASSET_SET_PAGE_RECORDS",
            "assetSet.pageRecords differs from the real seed page count",
            manifest=asset_set.get("pageRecords"),
            actual=len(plan.records),
        )
    if asset_set.get("mappingRecords") != len(plan.mapping_records):
        log.error(
            "ASSET_SET_MAPPING_RECORDS",
            "assetSet.mappingRecords differs from cover_mapping.json",
            manifest=asset_set.get("mappingRecords"),
            actual=len(plan.mapping_records),
        )
    if asset_set.get("books") != plan.books:
        log.error("ASSET_SET_BOOKS", "assetSet.books differs from the real seed", manifest=asset_set.get("books"), actual=plan.books)
    if asset_set.get("versions") != plan.versions_count:
        log.error(
            "ASSET_SET_VERSIONS",
            "assetSet.versions differs from the real seed",
            manifest=asset_set.get("versions"),
            actual=plan.versions_count,
        )

    ok = not log.has_errors
    summary = {
        "ok": ok,
        "manifest": lib.posix_rel(manifest_path, repo_root),
        "assets": len(assets),
        "passed": len(assets) - failed_assets,
        "failed": failed_assets,
        "books": plan.books,
        "versions": plan.versions_count,
        "pages": len(plan.records),
        "errors": [item.as_dict() for item in log.errors],
        "warnings": [item.as_dict() for item in log.warnings],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(f"[check] manifest: {summary['manifest']}")
    print(f"[check] assets: {summary['assets']}  passed: {summary['passed']}  failed: {summary['failed']}")
    print(f"[check] books: {summary['books']}  versions: {summary['versions']}  pages: {summary['pages']}")
    for item in log.errors:
        context = " ".join(f"{key}={value}" for key, value in item.context.items())
        print(f"  ERROR {item.code}: {item.message}" + (f" [{context}]" if context else ""))
    for item in log.warnings:
        context = " ".join(f"{key}={value}" for key, value in item.context.items())
        print(f"  WARN  {item.code}: {item.message}" + (f" [{context}]" if context else ""))
    print(f"[result] {'OK' if ok else 'FAILED'} errors={len(log.errors)} warnings={len(log.warnings)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
