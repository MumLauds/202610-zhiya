#!/usr/bin/env python3
"""H03-P2/P3: audit and export 104 story-page JPEGs + pages-manifest.json.

This is a thin page-specific entry point. It reuses ``assets_lib`` for path
safety, hashing, probing and JPEG conversion, while the old cover CLI remains
unchanged.

Usage:
    python hardware/tools/prepare_page_assets.py --audit-only \
        --report hardware/assets/generated-pages/pages-source-audit.json
    python hardware/tools/prepare_page_assets.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence

import page_assets_lib as page
import validate_page_assets
import assets_lib as lib


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prepare_page_assets.py",
        description="Audit 104 story-page sources and export device JPEG page assets.",
    )
    parser.add_argument("--repo-root", default=None, help="repository root (default: auto-detect)")
    parser.add_argument("--mapping", default=lib.DEFAULT_MAPPING, help="cover mapping JSON")
    parser.add_argument("--storybooks", default=lib.DEFAULT_STORYBOOKS, help="storybooks seed JSON")
    parser.add_argument("--story-versions", default=lib.DEFAULT_VERSIONS, help="story versions seed JSON")
    parser.add_argument("--story-pages", default=page.DEFAULT_STORY_PAGES, help="story pages seed JSON")
    parser.add_argument(
        "--cover-source-root",
        default=lib.DEFAULT_SOURCE_ROOT,
        help="expected cover source directory used to validate cover_mapping.json",
    )
    parser.add_argument("--source-root", default=page.DEFAULT_PAGE_SOURCE_ROOT, help="only page images under this directory are accepted")
    parser.add_argument("--output-dir", default=page.DEFAULT_PAGE_OUTPUT_DIR, help="page asset output directory")
    parser.add_argument("--page-size", default="400x300", help="PAGE box, WIDTHxHEIGHT")
    parser.add_argument("--quality", type=int, default=85, help="JPEG quality 1-100 (default: 85)")
    parser.add_argument("--max-page-bytes", type=int, default=page.DEFAULT_PAGE_MAX_BYTES, help="per-page byte safety limit")
    parser.add_argument("--background", default="#FFFFFF", help="alpha compositing colour, #RRGGBB")
    parser.add_argument("--audit-only", action="store_true", help="check sources only; write no image assets")
    parser.add_argument("--report", default=None, help="write the audit JSON to this path")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    return parser


def _print_issues(log: lib.IssueLog) -> None:
    for issue in log.items:
        label = "ERROR" if issue.severity == "error" else "WARN "
        context = " ".join(f"{key}={value}" for key, value in issue.context.items())
        print(f"  {label} {issue.code}: {issue.message}" + (f" [{context}]" if context else ""))


def _print_summary(plan: page.PagePlan) -> None:
    passed = sum(1 for record in plan.records if record.ok)
    print(
        f"[audit] covers: {len(plan.mapping_records)}  books: {plan.books}  "
        f"versions: {plan.versions_count}  pages: {len(plan.records)}  "
        f"passed: {passed}  failed: {len(plan.records) - passed}  "
        f"warnings: {len(plan.log.warnings)}  errors: {len(plan.log.errors)}"
    )
    if plan.excluded_versions:
        excluded = ", ".join(
            f"v{item['storyVersionId']}({item['status']}/{item['pageCount']}p)" for item in plan.excluded_versions
        )
        print(f"[audit] excluded versions: {excluded}")


def _write_report(report_path: Path, payload: dict[str, Any]) -> None:
    lib.write_json(report_path, payload)
    print(f"[audit] report written: {report_path}")


def _convert_pages(
    plan: page.PagePlan,
    *,
    repo_root: Path,
    stage_dir: Path,
    final_output_dir: Path,
    page_box: tuple[int, int],
    quality: int,
    background: tuple[int, int, int],
    max_page_bytes: int,
    log: lib.IssueLog,
    quiet: bool,
) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    used_names: dict[str, str] = {}
    for record in plan.records:
        if not record.ok or record.probe is None or not record.probe.decode_ok or record.source_path is None:
            log.error(
                "PAGE_NOT_CONVERTED",
                "page failed source audit, so no device asset was produced",
                bookNo=record.book_no,
                storyVersionId=record.story_version_id,
                pageNumber=record.page_number,
            )
            continue
        name = page.page_file_name(record.book_no, record.story_version_id, record.page_number)
        owner = used_names.get(name)
        if owner is not None:
            log.error(
                "PAGE_DUPLICATE_FILE_NAME",
                "two page records collapse to the same device file name",
                bookNo=record.book_no,
                storyVersionId=record.story_version_id,
                pageNumber=record.page_number,
                fileName=name,
                firstKey=owner,
            )
            continue
        used_names[name] = f"{record.book_no}/v{record.story_version_id}/p{record.page_number}"
        destination = stage_dir / name
        try:
            result = lib.convert_cover(
                record.source_path,
                destination,
                page_box,
                quality=quality,
                background=background,
                pad=False,
            )
        except Exception as exc:  # noqa: BLE001 - one bad page must not abort all conversion
            log.error(
                "PAGE_CONVERSION_FAILED",
                f"{type(exc).__name__}: {exc}",
                bookNo=record.book_no,
                storyVersionId=record.story_version_id,
                pageNumber=record.page_number,
            )
            continue

        try:
            info = page.jpeg_info(destination)
        except Exception as exc:  # noqa: BLE001 - validate the actual output bytes
            log.error(
                "PAGE_JPEG_INVALID",
                f"generated file is not a valid JPEG: {type(exc).__name__}: {exc}",
                bookNo=record.book_no,
                storyVersionId=record.story_version_id,
                pageNumber=record.page_number,
            )
            continue

        if result.width > page_box[0] or result.height > page_box[1]:
            log.error(
                "PAGE_OUTPUT_EXCEEDS_BOX",
                "converted page is larger than its configured box",
                bookNo=record.book_no,
                storyVersionId=record.story_version_id,
                pageNumber=record.page_number,
                width=result.width,
                height=result.height,
                box=f"{page_box[0]}x{page_box[1]}",
            )
        if result.file_size > max_page_bytes:
            log.error(
                "PAGE_ASSET_TOO_LARGE",
                "converted page exceeds the configured byte limit",
                bookNo=record.book_no,
                storyVersionId=record.story_version_id,
                pageNumber=record.page_number,
                fileSize=result.file_size,
                maxBytes=max_page_bytes,
            )
        if info["baseline"] is not True or info["progressive"]:
            log.error(
                "PAGE_JPEG_NOT_BASELINE",
                "generated page is not a non-progressive baseline JPEG",
                bookNo=record.book_no,
                storyVersionId=record.story_version_id,
                pageNumber=record.page_number,
                sofMarker=info["sofMarker"],
                progressive=info["progressive"],
            )
        if info["mode"] != "RGB" or info["hasExif"]:
            log.error(
                "PAGE_JPEG_METADATA_INVALID",
                "generated page must be RGB and carry no EXIF metadata",
                bookNo=record.book_no,
                storyVersionId=record.story_version_id,
                pageNumber=record.page_number,
                mode=info["mode"],
                hasExif=info["hasExif"],
            )

        asset = {
            "bookNo": record.book_no,
            "storybookId": record.storybook_id,
            "storyVersionId": record.story_version_id,
            "storyVersionStatus": record.status,
            "title": record.title,
            "pageNumber": record.page_number,
            "assetRole": page.PAGE_ROLE,
            "sourcePath": record.source_rel,
            "assetPath": lib.posix_rel(final_output_dir / name, repo_root),
            "devicePath": page.device_page_path(name),
            "imageLicenseNote": record.seed.image_license_note,
            "format": "JPEG",
            "width": result.width,
            "height": result.height,
            "fileSize": result.file_size,
            "sha256": result.sha256,
            "sourceFormat": record.probe.format,
            "sourceWidth": record.probe.width,
            "sourceHeight": record.probe.height,
            "sourceFileSize": record.probe.bytes,
            "sourceSha256": record.probe.sha256,
            "alphaComposited": result.alpha_composited,
            "exifOrientationApplied": result.exif_orientation_applied,
            "sizeRatio": round(result.file_size / record.probe.bytes, 6) if record.probe.bytes else None,
        }
        assets.append(asset)
        if not quiet:
            print(
                "{book:<7} v{version:<3} p{page:<3} {sw}x{sh} -> {dw}x{dh}  "
                "{src:>10} -> {dst:>9}  {ratio:>7.2%}".format(
                    book=record.book_no,
                    version=record.story_version_id,
                    page=record.page_number,
                    sw=record.probe.width,
                    sh=record.probe.height,
                    dw=result.width,
                    dh=result.height,
                    src=lib.human_bytes(record.probe.bytes),
                    dst=lib.human_bytes(result.file_size),
                    ratio=(result.file_size / record.probe.bytes) if record.probe.bytes else 0.0,
                )
            )
    return assets


def _published_backup(output_dir: Path) -> Path | None:
    if not output_dir.exists():
        return None
    if not output_dir.is_dir():
        raise page.PagePipelineError("OUTPUT_NOT_DIRECTORY", f"output path is not a directory: {output_dir}")
    backup = output_dir.with_name(f"{output_dir.name}.bak-{uuid.uuid4().hex[:8]}")
    os.replace(output_dir, backup)
    return backup


def _restore_backup(output_dir: Path, backup: Path | None) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    if backup is not None:
        os.replace(backup, output_dir)


def _publish(
    stage_dir: Path,
    output_dir: Path,
    *,
    repo_root: Path,
    report_path: Path | None,
    audit_payload: dict[str, Any],
    log: lib.IssueLog,
) -> int:
    backup = _published_backup(output_dir)
    try:
        os.replace(stage_dir, output_dir)
    except Exception:
        _restore_backup(output_dir, backup)
        raise
    try:
        # Verify the published bundle independently after the swap, including
        # manifest paths, file hashes, source mappings and unreferenced files.
        code = validate_page_assets.run(
            [
                "--repo-root",
                str(repo_root),
                "--manifest",
                str(output_dir / page.PAGE_MANIFEST_NAME),
                "--output-dir",
                str(output_dir),
            ]
        )
        if code != 0:
            _restore_backup(output_dir, backup)
            print("[publish] final validation failed; previous output restored")
            return code
    except Exception:
        _restore_backup(output_dir, backup)
        raise
    if report_path is not None:
        # Keep the audit report in the published output as the default artifact,
        # and also honor an explicitly requested safe report path.
        if report_path.resolve() != (output_dir / page.PAGE_AUDIT_NAME).resolve():
            lib.write_json(report_path, audit_payload)
            print(f"[audit] report written: {report_path}")
    if backup is not None:
        try:
            shutil.rmtree(backup)
        except OSError as exc:
            log.warning("BACKUP_CLEANUP_FAILED", f"previous output backup kept: {backup}: {exc}")
    return 0


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repo_root = (
            Path(args.repo_root).resolve()
            if args.repo_root
            else lib.repo_root_from(Path(__file__).parent)
        )
        mapping_path = lib.resolve_arg(repo_root, args.mapping)
        storybooks_path = lib.resolve_arg(repo_root, args.storybooks)
        versions_path = lib.resolve_arg(repo_root, args.story_versions)
        pages_path = lib.resolve_arg(repo_root, args.story_pages)
        cover_source_root = lib.resolve_arg(repo_root, args.cover_source_root)
        source_root = lib.resolve_arg(repo_root, args.source_root)
        output_dir = lib.resolve_arg(repo_root, args.output_dir)
        page_box = lib.parse_size(args.page_size)
        background = lib.parse_color(args.background)
        if not 1 <= args.quality <= 100:
            raise page.PagePipelineError("QUALITY_INVALID", "--quality must be between 1 and 100")
        if args.max_page_bytes <= 0:
            raise page.PagePipelineError("MAX_BYTES_INVALID", "--max-page-bytes must be positive")
        if not source_root.is_dir():
            raise page.PagePipelineError("PAGE_SOURCE_ROOT_MISSING", f"page source root not found: {source_root}")
        if output_dir.resolve() == source_root.resolve() or source_root.resolve() in output_dir.resolve().parents:
            raise page.PagePipelineError(
                "OUTPUT_OVERLAPS_SOURCE", "output directory must not live under the page source directory"
            )
        lib.posix_rel(output_dir, repo_root)  # reject output outside the repository
    except (page.PagePipelineError, lib.AssetPipelineError, OSError) as exc:
        print(f"FATAL [{exc.code}] {exc.message}")
        return 2

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

    _print_summary(plan)
    _print_issues(plan.log)
    audit_payload = page.build_audit_report(plan, repo_root, source_root)

    report_path: Path | None = None
    if args.report:
        report_path = lib.resolve_arg(repo_root, args.report)

    if args.audit_only:
        if report_path is None:
            report_path = output_dir / page.PAGE_AUDIT_NAME
        _write_report(report_path, audit_payload)
        print(f"[result] {'OK' if not plan.log.has_errors else 'FAILED'} errors={len(plan.log.errors)} warnings={len(plan.log.warnings)}")
        return 0 if not plan.log.has_errors else 1

    if plan.log.has_errors:
        print("[assets] not generated: fix the audit errors, then re-run")
        print(f"[result] FAILED errors={len(plan.log.errors)}")
        return 1

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".pages-stage-", dir=str(output_dir.parent)))
    log = lib.IssueLog()
    try:
        assets = _convert_pages(
            plan,
            repo_root=repo_root,
            stage_dir=stage_dir,
            final_output_dir=output_dir,
            page_box=page_box,
            quality=args.quality,
            background=background,
            max_page_bytes=args.max_page_bytes,
            log=log,
            quiet=args.quiet,
        )
        expected_assets = len(plan.records)
        if log.has_errors or len(assets) != expected_assets:
            print(
                f"[assets] converted: {len(assets)}/{expected_assets}; "
                f"not publishing because errors={len(log.errors)}"
            )
            _print_issues(log)
            print("[result] FAILED")
            return 1

        manifest = page.build_page_manifest(
            plan,
            assets,
            repo_root=repo_root,
            output_dir=output_dir,
            page_box=page_box,
            quality=args.quality,
            background=background,
            max_page_bytes=args.max_page_bytes,
        )
        lib.write_json(stage_dir / page.PAGE_AUDIT_NAME, audit_payload)

        # Validate the staged bundle before exposing it at the final path.
        validation_manifest = page.retarget_manifest(manifest, stage_dir, repo_root)
        validation_manifest_path = stage_dir / "pages-manifest.validation.json"
        lib.write_json(validation_manifest_path, validation_manifest)
        validate_code = validate_page_assets.run(
            [
                "--repo-root",
                str(repo_root),
                "--manifest",
                str(validation_manifest_path),
                "--output-dir",
                str(stage_dir),
            ]
        )
        if validate_code != 0:
            print("[publish] staged validation failed; existing output was not replaced")
            print("[result] FAILED")
            return 1
        validation_manifest_path.unlink(missing_ok=True)

        # The published manifest uses final repository-relative paths. Its
        # files are identical to the staged files just validated.
        lib.write_json(stage_dir / page.PAGE_MANIFEST_NAME, manifest)

        if report_path is not None:
            lib.write_json(report_path, audit_payload)
            print(f"[audit] report written: {report_path}")

        code = _publish(
            stage_dir,
            output_dir,
            repo_root=repo_root,
            report_path=None,
            audit_payload=audit_payload,
            log=log,
        )
        if code != 0:
            print("[result] FAILED")
            return code
        print(
            f"[assets] converted: {len(assets)}/{expected_assets}  "
            f"source: {lib.human_bytes(plan.unique_source_bytes)}  "
            f"asset: {lib.human_bytes(sum(asset['fileSize'] for asset in assets))}"
        )
        print(f"[manifest] written: {lib.posix_rel(output_dir / page.PAGE_MANIFEST_NAME, repo_root)}")
        print(f"[result] OK assets={len(assets)} errors={len(log.errors)} warnings={len(plan.log.warnings)}")
        return 0
    except (page.PagePipelineError, lib.AssetPipelineError) as exc:
        print(f"FATAL [{exc.code}] {exc.message}")
        print("[result] FAILED")
        return 2
    except Exception as exc:  # noqa: BLE001 - preserve output on unexpected failure
        print(f"FATAL [UNEXPECTED] {type(exc).__name__}: {exc}")
        print("[result] FAILED")
        return 2
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(run())
