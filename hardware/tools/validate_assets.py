#!/usr/bin/env python3
"""H03-04: validate the generated asset manifest against the files on disk.

The validator is deliberately independent from the exporter: it re-opens every
declared asset, recomputes SHA-256, re-reads the image header, re-binds each
asset to `cover_mapping.json` (bookNo, storybookId and sourcePath together),
verifies the mapping fingerprint and the storybook/version relationship. Any
failure exits non-zero so it can be wired into CI.

Usage:
    python hardware/tools/validate_assets.py
    python hardware/tools/validate_assets.py --json
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from PIL import Image

import assets_lib as lib

REQUIRED_ASSET_FIELDS = (
    "bookNo",
    "storybookId",
    "storyVersionId",
    "assetRole",
    "sourcePath",
    "assetPath",
    "width",
    "height",
    "format",
    "fileSize",
    "sha256",
)
ASSET_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_assets.py",
        description="Check the H03 manifest, asset files, coverage and seed relationships.",
    )
    parser.add_argument("--repo-root", default=None, help="repository root (default: auto-detect)")
    parser.add_argument("--manifest", default=None, help="manifest path (default: <output-dir>/assets-manifest.json)")
    parser.add_argument("--output-dir", default=None, help="override the manifest assetSet.outputDir")
    parser.add_argument("--mapping", default=lib.DEFAULT_MAPPING, help="cover mapping JSON used for coverage checks")
    parser.add_argument("--source-root", default=lib.DEFAULT_SOURCE_ROOT, help="cover source directory")
    parser.add_argument("--story-versions", default=lib.DEFAULT_VERSIONS, help="story versions seed JSON")
    parser.add_argument("--max-thumb-bytes", type=int, default=None, help="override THUMBNAIL maxFileBytes")
    parser.add_argument("--max-detail-bytes", type=int, default=None, help="override DETAIL maxFileBytes")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="downgrade missing asset files to warnings (fresh clone without generated binaries)",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary instead of text")
    return parser


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _asset_label(asset: Any, index: int) -> str:
    # Callers may receive anything from a hand-edited manifest, so never assume
    # a mapping-like object here.
    if not isinstance(asset, dict):
        return f"#{index}"
    book = asset.get("bookNo", f"#{index}")
    role = asset.get("assetRole", "?")
    return f"{book}/{role}"


def _normalise_repo_path(text: str) -> str:
    """Compare repository paths without depending on slash style."""

    return str(PurePosixPath(text.replace("\\", "/").strip()))


def validate_asset(
    asset: dict[str, Any],
    index: int,
    *,
    repo_root: Path,
    output_dir: Path,
    role_limits: dict[str, tuple[int, int]],
    role_max_bytes: dict[str, int],
    allow_missing: bool,
    log: lib.IssueLog,
) -> None:
    if not isinstance(asset, dict):
        log.error(
            "ASSET_NOT_OBJECT",
            "asset entry is not a JSON object",
            index=index,
            value=repr(asset)[:80],
        )
        return
    label = _asset_label(asset, index)

    missing = [name for name in REQUIRED_ASSET_FIELDS if name not in asset]
    if missing:
        log.error(
            "ASSET_FIELD_MISSING",
            "asset entry is missing required fields: " + ", ".join(missing),
            asset=label,
            missingFields=missing,
        )
        return

    book_no = asset["bookNo"]
    storybook_id = asset["storybookId"]
    version_id = asset["storyVersionId"]
    role = asset["assetRole"]

    if not isinstance(book_no, str) or not lib.BOOK_NO_RE.fullmatch(book_no):
        log.error("ASSET_BOOK_NO_INVALID", "bookNo is not a short ASCII identifier", asset=label, value=repr(book_no))
    if not _is_int(storybook_id) or storybook_id <= 0:
        log.error("ASSET_STORYBOOK_ID_INVALID", "storybookId must be a positive integer", asset=label, value=repr(storybook_id))
    if version_id is not None and (not _is_int(version_id) or version_id <= 0):
        log.error("ASSET_STORY_VERSION_ID_INVALID", "storyVersionId must be null or a positive integer", asset=label, value=repr(version_id))
    if role not in lib.ROLES:
        log.error("ASSET_ROLE_INVALID", "assetRole must be THUMBNAIL or DETAIL", asset=label, value=repr(role))
        return

    if not isinstance(asset["sha256"], str) or not SHA256_RE.fullmatch(asset["sha256"]):
        log.error("ASSET_SHA256_FORMAT", "sha256 must be 64 lower-case hex characters", asset=label)
    for name in ("width", "height", "fileSize"):
        if not _is_int(asset[name]) or asset[name] <= 0:
            log.error("ASSET_NUMBER_INVALID", f"{name} must be a positive integer", asset=label, field=name, value=repr(asset[name]))

    source_path_value = asset["sourcePath"]
    asset_path_value = asset["assetPath"]
    if not isinstance(source_path_value, str) or not source_path_value.strip():
        log.error("ASSET_SOURCE_PATH_INVALID", "sourcePath must be a non-empty string", asset=label)
        source_candidate = None
    else:
        source_candidate, source_code, source_message = lib.repo_relative_candidate(source_path_value, repo_root)
        if source_candidate is None:
            log.error(source_code, source_message, asset=label, field="sourcePath", value=source_path_value)

    if not isinstance(asset_path_value, str) or not asset_path_value.strip():
        log.error("ASSET_PATH_INVALID", "assetPath must be a non-empty string", asset=label)
        return
    asset_candidate, asset_code, asset_message = lib.repo_relative_candidate(asset_path_value, repo_root)
    if asset_candidate is None:
        log.error(asset_code, asset_message, asset=label, field="assetPath", value=asset_path_value)
        return
    try:
        asset_candidate.relative_to(output_dir.resolve())
    except ValueError:
        log.error(
            "ASSET_OUTSIDE_OUTPUT_DIR",
            "assetPath is not inside the declared output directory",
            asset=label,
            value=asset["assetPath"],
            outputDir=lib.posix_rel(output_dir, repo_root),
        )
    if asset_candidate.suffix.lower() not in {".jpg", ".jpeg"}:
        log.error("ASSET_NOT_JPEG", "device assets are expected to be .jpg", asset=label, value=asset["assetPath"])
    if source_candidate is not None and asset_candidate.resolve() == source_candidate.resolve():
        log.error("ASSET_OVERWRITES_SOURCE", "assetPath points at the source image", asset=label, value=asset["assetPath"])

    box = role_limits[role]
    max_bytes = role_max_bytes[role]
    if _is_int(asset["width"]) and _is_int(asset["height"]):
        if asset["width"] > box[0] or asset["height"] > box[1]:
            log.error(
                "ASSET_DECLARED_SIZE_EXCEEDS_ROLE_BOX",
                "declared width/height exceed the role box",
                asset=label,
                width=asset["width"],
                height=asset["height"],
                box=f"{box[0]}x{box[1]}",
            )
    if _is_int(asset["fileSize"]) and asset["fileSize"] > max_bytes:
        log.error(
            "ASSET_DECLARED_BYTES_EXCEED_LIMIT",
            "declared fileSize exceeds the role byte budget",
            asset=label,
            fileSize=asset["fileSize"],
            maxBytes=max_bytes,
        )

    if not asset_candidate.is_file():
        if allow_missing:
            log.warning(
                "ASSET_FILE_MISSING",
                "asset file is absent (allowed by --allow-missing)",
                asset=label,
                value=asset["assetPath"],
            )
            return
        log.error("ASSET_FILE_MISSING", "asset file does not exist", asset=label, value=asset["assetPath"])
        return

    actual_bytes = asset_candidate.stat().st_size
    actual_sha = lib.sha256_file(asset_candidate)
    if _is_int(asset["fileSize"]) and actual_bytes != asset["fileSize"]:
        log.error(
            "ASSET_SIZE_MISMATCH",
            "file size on disk differs from the manifest",
            asset=label,
            manifest=asset["fileSize"],
            actual=actual_bytes,
        )
    if isinstance(asset["sha256"], str) and actual_sha != asset["sha256"]:
        log.error(
            "ASSET_SHA256_MISMATCH",
            "SHA-256 on disk differs from the manifest",
            asset=label,
            manifest=asset["sha256"],
            actual=actual_sha,
        )
    if actual_bytes > max_bytes:
        log.error(
            "ASSET_BYTES_EXCEED_LIMIT",
            "asset file exceeds the role byte budget",
            asset=label,
            fileSize=actual_bytes,
            maxBytes=max_bytes,
        )

    try:
        with Image.open(asset_candidate) as image:
            actual_format = image.format
            actual_size = image.size
            image.load()
    except Exception as exc:  # noqa: BLE001 - reported as a validation failure
        log.error(
            "ASSET_UNDECODABLE",
            f"asset cannot be decoded: {type(exc).__name__}: {exc}",
            asset=label,
            value=asset["assetPath"],
        )
        return

    if isinstance(asset["format"], str) and actual_format != asset["format"]:
        log.error(
            "ASSET_FORMAT_MISMATCH",
            "decoded format differs from the manifest",
            asset=label,
            manifest=asset["format"],
            actual=actual_format,
        )
    if _is_int(asset["width"]) and _is_int(asset["height"]):
        if actual_size != (asset["width"], asset["height"]):
            log.error(
                "ASSET_DIMENSION_MISMATCH",
                "decoded dimensions differ from the manifest",
                asset=label,
                manifest=f"{asset['width']}x{asset['height']}",
                actual=f"{actual_size[0]}x{actual_size[1]}",
            )
    if actual_size[0] > box[0] or actual_size[1] > box[1]:
        log.error(
            "ASSET_SIZE_EXCEEDS_ROLE_BOX",
            "decoded dimensions exceed the role box",
            asset=label,
            actual=f"{actual_size[0]}x{actual_size[1]}",
            box=f"{box[0]}x{box[1]}",
        )


def role_configuration(manifest: dict[str, Any], args: argparse.Namespace, log: lib.IssueLog) -> tuple[dict[str, tuple[int, int]], dict[str, int]]:
    limits = dict(lib.DEFAULT_ROLE_LIMITS)
    max_bytes = dict(lib.DEFAULT_ROLE_MAX_BYTES)
    asset_set = manifest.get("assetSet")
    roles = asset_set.get("roles", {}) if isinstance(asset_set, dict) else {}
    for role in lib.ROLES:
        payload = roles.get(role) if isinstance(roles, dict) else None
        if not isinstance(payload, dict):
            log.warning("ROLE_CONFIG_MISSING", "manifest does not describe this role", role=role)
            continue
        width, height = payload.get("maxWidth"), payload.get("maxHeight")
        if _is_int(width) and _is_int(height) and width > 0 and height > 0:
            limits[role] = (width, height)
        else:
            log.error("ROLE_CONFIG_INVALID", "role maxWidth/maxHeight must be positive integers", role=role)
        value = payload.get("maxFileBytes")
        if _is_int(value) and value > 0:
            max_bytes[role] = value
        else:
            log.error("ROLE_BYTES_INVALID", "role maxFileBytes must be a positive integer", role=role)

    if args.max_thumb_bytes is not None:
        max_bytes[lib.ROLE_THUMBNAIL] = args.max_thumb_bytes
    if args.max_detail_bytes is not None:
        max_bytes[lib.ROLE_DETAIL] = args.max_detail_bytes
    return limits, max_bytes


def check_mapping_sha(manifest: dict[str, Any], mapping_path: Path, log: lib.IssueLog) -> None:
    """Compare the manifest fingerprint with the mapping file that is on disk."""

    asset_set = manifest.get("assetSet")
    declared = asset_set.get("sourceMappingSha256") if isinstance(asset_set, dict) else None
    actual = lib.sha256_file(mapping_path)
    if not isinstance(declared, str) or not SHA256_RE.fullmatch(declared):
        log.error(
            "MANIFEST_SOURCE_MAPPING_SHA_INVALID",
            "assetSet.sourceMappingSha256 must be 64 lower-case hex characters",
            value=repr(declared),
        )
        return
    if declared != actual:
        log.error(
            "SOURCE_MAPPING_SHA_MISMATCH",
            "cover_mapping.json differs from the version recorded in the manifest",
            manifest=declared,
            actual=actual,
        )


def check_mapping_identity(
    assets: Sequence[dict[str, Any]],
    records: Sequence[lib.CoverRecord],
    log: lib.IssueLog,
) -> None:
    """Re-bind every asset to its mapping record so swapped identities fail.

    A manifest can keep a valid book/role coverage and a valid story/version pair
    while pointing book A at book B's storybook id or at book B's source image.
    Comparing the three mapping fields together is the only way to catch that.
    """

    by_book = {record.book_no: record for record in records}
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            continue
        book_no = asset.get("bookNo")
        if not isinstance(book_no, str):
            continue
        record = by_book.get(book_no)
        if record is None:
            # Unknown book numbers are reported by the coverage check as
            # COVERAGE_UNEXPECTED, so there is nothing to compare here.
            continue
        label = _asset_label(asset, index)
        if asset.get("storybookId") != record.storybook_id:
            log.error(
                "ASSET_STORYBOOK_ID_MISMATCH",
                "storybookId does not match cover_mapping.json for this bookNo",
                asset=label,
                manifest=repr(asset.get("storybookId")),
                mapping=record.storybook_id,
            )
        declared_source = asset.get("sourcePath")
        if isinstance(declared_source, str):
            if _normalise_repo_path(declared_source) != _normalise_repo_path(record.cover_path):
                log.error(
                    "ASSET_SOURCE_PATH_MISMATCH",
                    "sourcePath does not match cover_mapping.json for this bookNo",
                    asset=label,
                    manifest=declared_source,
                    mapping=record.cover_path,
                )


def check_coverage(
    manifest: dict[str, Any],
    assets: Sequence[dict[str, Any]],
    *,
    mapping_path: Path,
    repo_root: Path,
    source_root: Path,
    log: lib.IssueLog,
) -> int:
    if not mapping_path.is_file():
        log.error(
            "MAPPING_MISSING",
            "cover mapping is absent; the manifest cannot be re-bound to its source records",
            value=str(mapping_path),
        )
        return 0
    try:
        records = lib.load_cover_mapping(mapping_path, repo_root, source_root, log)
    except lib.AssetPipelineError as exc:
        log.error(exc.code, f"coverage check could not read the mapping: {exc.message}")
        return 0

    check_mapping_sha(manifest, mapping_path, log)

    asset_set = manifest.get("assetSet")
    if isinstance(asset_set, dict) and _is_int(asset_set.get("mappingRecords")):
        if asset_set["mappingRecords"] != len(records):
            log.error(
                "COVERAGE_RECORD_COUNT",
                "manifest mappingRecords differs from the mapping file",
                manifest=asset_set["mappingRecords"],
                mapping=len(records),
            )

    present = {
        (asset.get("bookNo"), asset.get("assetRole"))
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("bookNo"), str) and isinstance(asset.get("assetRole"), str)
    }
    expected = {(record.book_no, role) for record in records for role in lib.ROLES}
    for key in sorted(expected - present):
        log.error("COVERAGE_MISSING", "mapping record has no matching device asset", bookNo=key[0], assetRole=key[1])
    for key in sorted(present - expected):
        log.error("COVERAGE_UNEXPECTED", "manifest asset has no mapping record", bookNo=key[0], assetRole=key[1])
    check_mapping_identity(assets, records, log)
    return len(records)


def check_story_versions(
    assets: Sequence[dict[str, Any]],
    *,
    versions_path: Path,
    log: lib.IssueLog,
) -> None:
    if not versions_path.is_file():
        log.warning("STORY_VERSIONS_MISSING", "story versions seed is absent; version cross-check skipped")
        return
    try:
        by_storybook = lib.load_story_versions(versions_path, log)
    except lib.AssetPipelineError as exc:
        log.error(exc.code, f"version cross-check could not read the seed: {exc.message}")
        return

    for index, asset in enumerate(assets):
        if not isinstance(asset, dict) or not _is_int(asset.get("storybookId")):
            continue
        label = _asset_label(asset, index)
        version, reason = lib.lookup_version(by_storybook, asset["storybookId"])
        if version is None:
            if reason == "ambiguous" and asset.get("storyVersionId") is not None:
                log.error(
                    "ASSET_VERSION_AMBIGUOUS",
                    "several seed versions exist, yet the manifest claims one id",
                    asset=label,
                    storyVersionId=asset.get("storyVersionId"),
                )
            continue
        if asset.get("storyVersionId") != version.version_id:
            log.error(
                "ASSET_VERSION_ID_MISMATCH",
                "storyVersionId does not match data/seed/story_versions.json",
                asset=label,
                manifest=asset.get("storyVersionId"),
                seed=version.version_id,
                storybookId=asset["storybookId"],
            )
        declared_status = asset.get("storyVersionStatus")
        if isinstance(declared_status, str) and declared_status != version.status:
            log.error(
                "ASSET_VERSION_STATUS_MISMATCH",
                "storyVersionStatus differs from the seed",
                asset=label,
                manifest=declared_status,
                seed=version.status,
            )


def check_duplicates(assets: Sequence[dict[str, Any]], log: lib.IssueLog) -> None:
    keys: dict[tuple[str, Any], int] = {}
    paths: dict[str, int] = {}
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            continue
        # A device asset is identified by book + role; the storybook id and the
        # asset path must identify the same single resource.
        for key in (("book", asset.get("bookNo")), ("storybook", asset.get("storybookId"))):
            composite = (key[0], key[1], asset.get("assetRole"))
            if key[1] is None:
                continue
            if composite in keys:
                log.error(
                    "ASSET_DUPLICATE_ID",
                    "duplicate asset identifier " + "/".join(str(part) for part in composite),
                    index=index,
                    firstIndex=keys[composite],
                )
            else:
                keys[composite] = index
        path_value = asset.get("assetPath")
        if isinstance(path_value, str):
            if path_value in paths:
                log.error(
                    "ASSET_DUPLICATE_PATH",
                    "duplicate assetPath",
                    value=path_value,
                    index=index,
                    firstIndex=paths[path_value],
                )
            else:
                paths[path_value] = index


def check_unreferenced(manifest_assets: Sequence[dict[str, Any]], output_dir: Path, repo_root: Path, log: lib.IssueLog) -> None:
    referenced = set()
    for asset in manifest_assets:
        if isinstance(asset, dict) and isinstance(asset.get("assetPath"), str):
            candidate, code, _ = lib.repo_relative_candidate(asset["assetPath"], repo_root)
            if candidate is not None:
                referenced.add(candidate.resolve())
    if not output_dir.is_dir():
        return
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ASSET_SUFFIXES:
            continue
        if path.resolve() not in referenced:
            log.error(
                "UNREFERENCED_ASSET",
                "file in the output directory is not referenced by the manifest",
                value=lib.posix_rel(path, repo_root),
            )


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = lib.IssueLog()

    try:
        repo_root = Path(args.repo_root).resolve() if args.repo_root else lib.repo_root_from(Path(__file__).parent)
        output_dir = lib.resolve_arg(repo_root, args.output_dir or lib.DEFAULT_OUTPUT_DIR)
        manifest_path = lib.resolve_arg(repo_root, args.manifest) if args.manifest else output_dir / lib.MANIFEST_NAME
        mapping_path = lib.resolve_arg(repo_root, args.mapping)
        source_root = lib.resolve_arg(repo_root, args.source_root)
        versions_path = lib.resolve_arg(repo_root, args.story_versions)
    except lib.AssetPipelineError as exc:
        print(f"FATAL [{exc.code}] {exc.message}")
        return 2

    if not manifest_path.is_file():
        print(f"FATAL [MANIFEST_MISSING] manifest not found: {manifest_path}")
        return 2
    try:
        manifest = lib.load_json(manifest_path)
    except lib.AssetPipelineError as exc:
        print(f"FATAL [{exc.code}] {exc.message}")
        return 2
    if not isinstance(manifest, dict):
        print("FATAL [MANIFEST_NOT_OBJECT] manifest must be a JSON object")
        return 2

    if manifest.get("schemaVersion") != lib.SCHEMA_VERSION:
        log.error(
            "MANIFEST_SCHEMA_VERSION",
            "unexpected schemaVersion",
            value=manifest.get("schemaVersion"),
            expected=lib.SCHEMA_VERSION,
        )
    if manifest.get("kind") != lib.MANIFEST_KIND:
        log.error("MANIFEST_KIND", "unexpected manifest kind", value=manifest.get("kind"), expected=lib.MANIFEST_KIND)

    asset_set = manifest.get("assetSet")
    if not isinstance(asset_set, dict):
        log.error("MANIFEST_ASSET_SET", "assetSet must be a JSON object")
        declared_output = None
    else:
        declared_output = asset_set.get("outputDir")
        if not isinstance(declared_output, str):
            log.error("MANIFEST_OUTPUT_DIR", "assetSet.outputDir must be a string")
        elif args.output_dir is None:
            candidate, code, message = lib.repo_relative_candidate(declared_output, repo_root)
            if candidate is None:
                log.error(code, message, field="assetSet.outputDir", value=declared_output)
            else:
                output_dir = candidate
        else:
            candidate, code, message = lib.repo_relative_candidate(declared_output, repo_root)
            if candidate is not None and candidate.resolve() != output_dir.resolve():
                log.error(
                    "MANIFEST_OUTPUT_DIR_MISMATCH",
                    "assetSet.outputDir differs from --output-dir",
                    manifest=declared_output,
                    argument=lib.posix_rel(output_dir, repo_root),
                )

    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        print("FATAL [MANIFEST_ASSETS_EMPTY] manifest has no asset entries")
        return 2

    limits, max_bytes = role_configuration(manifest, args, log)

    failed_assets = 0
    for index, asset in enumerate(assets):
        before = len(log.errors)
        validate_asset(
            asset,
            index,
            repo_root=repo_root,
            output_dir=output_dir,
            role_limits=limits,
            role_max_bytes=max_bytes,
            allow_missing=args.allow_missing,
            log=log,
        )
        if len(log.errors) > before:
            failed_assets += 1

    check_duplicates(assets, log)
    mapping_records = check_coverage(
        manifest,
        assets,
        mapping_path=mapping_path,
        repo_root=repo_root,
        source_root=source_root,
        log=log,
    )
    check_story_versions(assets, versions_path=versions_path, log=log)
    check_unreferenced(assets, output_dir, repo_root, log)

    ok = not log.has_errors
    summary = {
        "ok": ok,
        "manifest": lib.posix_rel(manifest_path, repo_root),
        "assets": len(assets),
        "passed": len(assets) - failed_assets,
        "failed": failed_assets,
        "mappingRecords": mapping_records,
        "errors": [item.as_dict() for item in log.errors],
        "warnings": [item.as_dict() for item in log.warnings],
    }

    if args.json:
        import json

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(f"[check] manifest: {summary['manifest']}")
    print(f"[check] assets: {summary['assets']}  passed: {summary['passed']}  failed: {summary['failed']}")
    print(f"[check] mapping records: {mapping_records}")
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
