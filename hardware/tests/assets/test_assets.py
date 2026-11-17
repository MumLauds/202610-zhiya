"""H03-05: offline tests for the cover export and manifest validation tools.

Every fixture is generated inside a temporary directory; the real knowledge
base, the real seed data and the device are never touched. Run with:

    python -m unittest discover -s hardware/tests/assets -v
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import assets_lib as lib  # noqa: E402  (path bootstrap must run first)
import prepare_assets  # noqa: E402
import validate_assets  # noqa: E402


class AssetRepo:
    """A throw-away repository root with only the files the tools need."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.seed = root / "data" / "seed"
        self.source_dir = root / lib.DEFAULT_SOURCE_ROOT
        self.output = root / lib.DEFAULT_OUTPUT_DIR
        self.manifest_path = self.output / lib.MANIFEST_NAME
        self.seed.mkdir(parents=True, exist_ok=True)
        self.source_dir.mkdir(parents=True, exist_ok=True)

    # ---- fixtures -------------------------------------------------------
    def write_json(self, name: str, payload: object) -> Path:
        path = self.seed / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def mapping(self, entries: list[dict]) -> Path:
        return self.write_json("cover_mapping.json", entries)

    def versions(self, storybook_ids: list[int], status: str = "DRAFT") -> Path:
        payload = [
            {
                "id": 1000 + storybook_id,
                "storybook_id": storybook_id,
                "version_no": 1,
                "title": f"story {storybook_id}",
                "status": status,
            }
            for storybook_id in storybook_ids
        ]
        return self.write_json("story_versions.json", payload)

    def storybooks(self, storybook_ids: list[int]) -> Path:
        return self.write_json("storybooks.json", [{"id": storybook_id} for storybook_id in storybook_ids])

    def cover(
        self,
        name: str,
        size: tuple[int, int] = (800, 600),
        mode: str = "RGB",
        color: tuple[int, ...] = (235, 235, 235),
        transparent_left: bool = False,
        fmt: str = "PNG",
        exif_orientation: int | None = None,
    ) -> Path:
        path = self.source_dir / name
        if transparent_left:
            image = Image.new("RGBA", size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rectangle([size[0] // 2, 0, size[0] - 1, size[1] - 1], fill=(200, 40, 40, 255))
        elif mode == "RGBA":
            image = Image.new(mode, size, tuple(color) + (255,))
        else:
            image = Image.new(mode, size, color)
        if exif_orientation is not None:
            exif = Image.Exif()
            exif[274] = exif_orientation
            image.save(path, fmt, exif=exif.tobytes())
        else:
            image.save(path, fmt)
        image.close()
        return path

    def single_book(
        self,
        book_no: str = "B-901",
        storybook_id: int = 901,
        file_name: str = "PIC-B01_cover.png",
        **cover_kwargs,
    ) -> Path:
        cover = self.cover(file_name, **cover_kwargs)
        self.mapping(
            [
                {
                    "book_no": book_no,
                    "storybook_id": storybook_id,
                    "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/{file_name}",
                }
            ]
        )
        self.versions([storybook_id])
        self.storybooks([storybook_id])
        return cover

    # ---- runners --------------------------------------------------------
    def prepare(self, *extra: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = prepare_assets.run(["--repo-root", str(self.root), *extra])
        return code, buffer.getvalue()

    def validate(self, *extra: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = validate_assets.run(["--repo-root", str(self.root), *extra])
        return code, buffer.getvalue()

    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def assets_by_role(self, role: str) -> list[dict]:
        return [asset for asset in self.manifest()["assets"] if asset["assetRole"] == role]


def asset_path_of(repo: AssetRepo, asset: dict) -> Path:
    return repo.root / asset["assetPath"]


class CoverPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = AssetRepo(Path(self._tmp.name))

    # ---- H03-02 conversion ----------------------------------------------
    def test_normal_png_conversion(self) -> None:
        self.repo.single_book(size=(800, 600))
        code, output = self.repo.prepare()
        self.assertEqual(code, 0, output)

        manifest = self.repo.manifest()
        self.assertEqual(len(manifest["assets"]), 2)
        thumb = self.repo.assets_by_role(lib.ROLE_THUMBNAIL)[0]
        detail = self.repo.assets_by_role(lib.ROLE_DETAIL)[0]
        self.assertEqual((thumb["width"], thumb["height"]), (213, 160))
        self.assertEqual((detail["width"], detail["height"]), (400, 300))

        for asset, box in ((thumb, (240, 160)), (detail, (400, 300))):
            path = asset_path_of(self.repo, asset)
            self.assertTrue(path.is_file())
            with Image.open(path) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (asset["width"], asset["height"]))
            self.assertEqual(asset["format"], "JPEG")
            self.assertEqual(path.stat().st_size, asset["fileSize"])
            self.assertLessEqual(asset["width"], box[0])
            self.assertLessEqual(asset["height"], box[1])

    def test_aspect_ratio_kept_for_wide_and_tall_sources(self) -> None:
        self.repo.cover("wide.png", size=(1200, 300))
        self.repo.cover("tall.png", size=(300, 1200))
        self.repo.mapping(
            [
                {"book_no": "B-901", "storybook_id": 901, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/wide.png"},
                {"book_no": "B-902", "storybook_id": 902, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/tall.png"},
            ]
        )
        self.repo.versions([901, 902])
        self.repo.storybooks([901, 902])
        code, output = self.repo.prepare()
        self.assertEqual(code, 0, output)

        by_book = {(asset["bookNo"], asset["assetRole"]): asset for asset in self.repo.manifest()["assets"]}
        self.assertEqual((by_book[("B-901", "THUMBNAIL")]["width"], by_book[("B-901", "THUMBNAIL")]["height"]), (240, 60))
        self.assertEqual((by_book[("B-901", "DETAIL")]["width"], by_book[("B-901", "DETAIL")]["height"]), (400, 100))
        self.assertEqual((by_book[("B-902", "THUMBNAIL")]["width"], by_book[("B-902", "THUMBNAIL")]["height"]), (40, 160))
        self.assertEqual((by_book[("B-902", "DETAIL")]["width"], by_book[("B-902", "DETAIL")]["height"]), (75, 300))

        for asset in by_book.values():
            source_ratio = asset["sourceWidth"] / asset["sourceHeight"]
            output_ratio = asset["width"] / asset["height"]
            self.assertLess(abs(source_ratio - output_ratio), 0.02, asset)

    def test_transparent_png_is_composited_and_declared(self) -> None:
        self.repo.single_book(size=(800, 600), transparent_left=True)
        code, output = self.repo.prepare()
        self.assertEqual(code, 0, output)

        thumb = self.repo.assets_by_role(lib.ROLE_THUMBNAIL)[0]
        self.assertTrue(thumb["alphaComposited"])
        path = asset_path_of(self.repo, thumb)
        with Image.open(path) as image:
            self.assertEqual(image.mode, "RGB")
            converted = image.convert("RGB")
            left = converted.getpixel((5, image.height // 2))
            right = converted.getpixel((image.width - 10, image.height // 2))
        for channel in left:
            self.assertGreaterEqual(channel, 245, f"transparent area is not white: {left}")
        self.assertGreater(right[0], 150)
        self.assertLess(right[1], 90)

    def test_background_colour_is_configurable(self) -> None:
        self.repo.single_book(size=(800, 600), transparent_left=True)
        code, output = self.repo.prepare("--background", "#000000")
        self.assertEqual(code, 0, output)
        thumb = self.repo.assets_by_role(lib.ROLE_THUMBNAIL)[0]
        with Image.open(asset_path_of(self.repo, thumb)) as image:
            corner = image.convert("RGB").getpixel((3, 3))
        for channel in corner:
            self.assertLessEqual(channel, 12, f"transparent area is not black: {corner}")

    def test_exif_orientation_is_applied_and_stripped(self) -> None:
        self.repo.cover("rotated.jpg", size=(600, 300), fmt="JPEG", exif_orientation=6)
        self.repo.mapping(
            [{"book_no": "B-901", "storybook_id": 901, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/rotated.jpg"}]
        )
        self.repo.versions([901])
        self.repo.storybooks([901])
        code, output = self.repo.prepare()
        self.assertEqual(code, 0, output)

        thumb = self.repo.assets_by_role(lib.ROLE_THUMBNAIL)[0]
        self.assertTrue(thumb["exifOrientationApplied"])
        self.assertLess(thumb["width"], thumb["height"])
        self.assertEqual((thumb["width"], thumb["height"]), (80, 160))
        with Image.open(asset_path_of(self.repo, thumb)) as image:
            self.assertIsNone(image.getexif().get(274), "EXIF orientation survived the export")

    def test_pad_option_produces_exact_role_box(self) -> None:
        self.repo.single_book(size=(800, 600))
        code, output = self.repo.prepare("--pad")
        self.assertEqual(code, 0, output)
        thumb = self.repo.assets_by_role(lib.ROLE_THUMBNAIL)[0]
        self.assertEqual((thumb["width"], thumb["height"]), (240, 160))

    def test_device_file_names_are_ascii_and_stable(self) -> None:
        self.repo.single_book()
        code, output = self.repo.prepare()
        self.assertEqual(code, 0, output)
        for asset in self.repo.manifest()["assets"]:
            name = Path(asset["assetPath"]).name
            self.assertRegex(name, r"^book-b\d+-(thumb|detail)\.jpg$")
            self.assertTrue(name.isascii(), name)
            self.assertEqual(asset["devicePath"], f"/assets/covers/{name}")

    # ---- H03-01 audit failures ------------------------------------------
    def test_corrupted_image_is_reported(self) -> None:
        cover = self.repo.single_book()
        payload = cover.read_bytes()
        cover.write_bytes(payload[: len(payload) // 2])

        probe = lib.probe_source(cover)
        self.assertFalse(probe.decode_ok)
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("BOOK_NOT_CONVERTED", output)
        self.assertFalse(self.repo.manifest_path.exists(), "a partial manifest must not be written")

    def test_missing_image_is_reported(self) -> None:
        self.repo.cover("present.png")
        self.repo.mapping(
            [{"book_no": "B-901", "storybook_id": 901, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/absent.png"}]
        )
        self.repo.versions([901])
        self.repo.storybooks([901])
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("SOURCE_MISSING", output)

    def test_mapping_field_missing_is_reported(self) -> None:
        self.repo.cover("present.png")
        self.repo.mapping([{"book_no": "B-901", "storybook_id": 901}])
        self.repo.versions([901])
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("MAPPING_FIELD_MISSING", output)
        self.assertIn("cover_path", output)

    def test_duplicate_book_no_is_reported(self) -> None:
        self.repo.cover("a.png")
        self.repo.cover("b.png")
        self.repo.mapping(
            [
                {"book_no": "B-901", "storybook_id": 901, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/a.png"},
                {"book_no": "B-901", "storybook_id": 902, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/b.png"},
            ]
        )
        self.repo.storybooks([901, 902])
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("MAPPING_DUPLICATE_BOOK_NO", output)

    def test_duplicate_storybook_id_is_reported(self) -> None:
        self.repo.cover("a.png")
        self.repo.cover("b.png")
        self.repo.mapping(
            [
                {"book_no": "B-901", "storybook_id": 901, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/a.png"},
                {"book_no": "B-902", "storybook_id": 901, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/b.png"},
            ]
        )
        self.repo.storybooks([901])
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("MAPPING_DUPLICATE_STORYBOOK_ID", output)

    def test_duplicate_device_file_name_is_reported(self) -> None:
        self.repo.cover("a.png")
        self.repo.cover("b.png")
        self.repo.mapping(
            [
                {"book_no": "B-901", "storybook_id": 901, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/a.png"},
                {"book_no": "B901", "storybook_id": 902, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/b.png"},
            ]
        )
        self.repo.versions([901, 902])
        self.repo.storybooks([901, 902])
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("DUPLICATE_ASSET_NAME", output)

    def test_path_traversal_is_rejected(self) -> None:
        self.repo.mapping([{"book_no": "B-901", "storybook_id": 901, "cover_path": "../../outside.png"}])
        self.repo.versions([901])
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("PATH_TRAVERSAL", output)

    def test_absolute_path_is_rejected(self) -> None:
        self.repo.mapping([{"book_no": "B-901", "storybook_id": 901, "cover_path": "C:/Windows/win.ini"}])
        self.repo.versions([901])
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("PATH_ABSOLUTE", output)

    def test_path_outside_the_cover_directory_is_rejected(self) -> None:
        self.repo.mapping(
            [{"book_no": "B-901", "storybook_id": 901, "cover_path": "data/seed/cover_mapping.json"}]
        )
        self.repo.versions([901])
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("PATH_OUTSIDE_SOURCE_ROOT", output)

    def test_extension_does_not_define_the_format(self) -> None:
        self.repo.cover("fake.png", size=(400, 300), fmt="JPEG")
        self.repo.mapping(
            [{"book_no": "B-901", "storybook_id": 901, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/fake.png"}]
        )
        self.repo.versions([901])
        self.repo.storybooks([901])
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("FORMAT_EXTENSION_MISMATCH", output)
        self.assertFalse(self.repo.manifest_path.exists())

    def test_output_directory_inside_sources_is_refused(self) -> None:
        self.repo.single_book()
        code, output = self.repo.prepare("--output-dir", lib.DEFAULT_SOURCE_ROOT)
        self.assertEqual(code, 2, output)
        self.assertIn("OUTPUT_OVERLAPS_SOURCE", output)

    def test_malformed_mapping_json_is_fatal(self) -> None:
        (self.repo.seed / "cover_mapping.json").write_text("[{", encoding="utf-8")
        code, output = self.repo.prepare()
        self.assertEqual(code, 2, output)
        self.assertIn("JSON_INVALID", output)

    def test_mapping_must_be_an_array(self) -> None:
        (self.repo.seed / "cover_mapping.json").write_text("{}", encoding="utf-8")
        code, output = self.repo.prepare()
        self.assertEqual(code, 2, output)
        self.assertIn("MAPPING_NOT_ARRAY", output)

    def test_partial_batch_failure_is_reported(self) -> None:
        self.repo.cover("a.png")
        broken = self.repo.cover("b.png")
        self.repo.cover("c.png")
        payload = broken.read_bytes()
        broken.write_bytes(payload[: len(payload) // 2])
        self.repo.mapping(
            [
                {"book_no": "B-901", "storybook_id": 901, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/a.png"},
                {"book_no": "B-902", "storybook_id": 902, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/b.png"},
                {"book_no": "B-903", "storybook_id": 903, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/c.png"},
            ]
        )
        self.repo.versions([901, 902, 903])
        self.repo.storybooks([901, 902, 903])
        code, output = self.repo.prepare()
        self.assertEqual(code, 1, output)
        self.assertIn("B-902", output)
        self.assertIn("BOOK_NOT_CONVERTED", output)
        self.assertFalse(self.repo.manifest_path.exists())
        self.assertTrue((self.repo.output / "book-b901-thumb.jpg").is_file())
        self.assertTrue((self.repo.output / "book-b903-detail.jpg").is_file())

    def test_run_is_idempotent_and_leaves_sources_untouched(self) -> None:
        cover = self.repo.single_book()
        before_bytes = cover.read_bytes()
        before_sha = lib.sha256_file(cover)
        before_mtime = cover.stat().st_mtime_ns

        code, output = self.repo.prepare()
        self.assertEqual(code, 0, output)
        first_manifest = self.repo.manifest_path.read_bytes()

        code, output = self.repo.prepare()
        self.assertEqual(code, 0, output)
        second_manifest = self.repo.manifest_path.read_bytes()

        self.assertEqual(first_manifest, second_manifest, "manifest is not reproducible")
        self.assertEqual(cover.read_bytes(), before_bytes)
        self.assertEqual(lib.sha256_file(cover), before_sha)
        self.assertEqual(cover.stat().st_mtime_ns, before_mtime)

    # ---- H03-03 manifest content ----------------------------------------
    def test_story_version_id_comes_from_the_seed_lookup(self) -> None:
        self.repo.single_book(storybook_id=901)
        code, output = self.repo.prepare()
        self.assertEqual(code, 0, output)
        for asset in self.repo.manifest()["assets"]:
            self.assertEqual(asset["storyVersionId"], 1901)
            self.assertNotEqual(asset["storyVersionId"], asset["storybookId"])
            self.assertEqual(asset["storyVersionStatus"], "DRAFT")

    def test_manifest_paths_are_repository_relative(self) -> None:
        self.repo.single_book()
        code, output = self.repo.prepare()
        self.assertEqual(code, 0, output)
        text = self.repo.manifest_path.read_text(encoding="utf-8")
        self.assertNotIn(str(self.repo.root), text)
        self.assertNotIn(str(self.repo.root).replace("\\", "/"), text)
        self.assertNotIn("C:/", text)
        for asset in self.repo.manifest()["assets"]:
            self.assertFalse(Path(asset["assetPath"]).is_absolute())
            self.assertTrue(asset["assetPath"].startswith("hardware/assets/generated/"))

    # ---- H03-04 validation ----------------------------------------------
    def test_valid_manifest_passes(self) -> None:
        self.repo.single_book()
        code, output = self.repo.prepare()
        self.assertEqual(code, 0, output)
        code, output = self.repo.validate()
        self.assertEqual(code, 0, output)
        self.assertIn("passed: 2", output)

    def test_validator_reports_tampered_file(self) -> None:
        self.repo.single_book()
        self.assertEqual(self.repo.prepare()[0], 0)
        target = asset_path_of(self.repo, self.repo.assets_by_role(lib.ROLE_DETAIL)[0])
        target.write_bytes(target.read_bytes() + b"tampered")
        code, output = self.repo.validate()
        self.assertEqual(code, 1, output)
        self.assertIn("ASSET_SHA256_MISMATCH", output)
        self.assertIn("ASSET_SIZE_MISMATCH", output)

    def test_validator_detects_broken_asset(self) -> None:
        self.repo.single_book()
        self.assertEqual(self.repo.prepare()[0], 0)
        target = asset_path_of(self.repo, self.repo.assets_by_role(lib.ROLE_THUMBNAIL)[0])
        target.write_bytes(target.read_bytes()[: 200])
        code, output = self.repo.validate()
        self.assertEqual(code, 1, output)
        self.assertTrue("ASSET_UNDECODABLE" in output or "ASSET_SHA256_MISMATCH" in output)

    def test_validator_detects_unreferenced_output(self) -> None:
        self.repo.single_book()
        self.assertEqual(self.repo.prepare()[0], 0)
        stray = self.repo.output / "book-b999-thumb.jpg"
        with Image.new("RGB", (240, 160), (10, 10, 10)) as image:
            image.save(stray, "JPEG")
        code, output = self.repo.validate()
        self.assertEqual(code, 1, output)
        self.assertIn("UNREFERENCED_ASSET", output)

    def test_validator_detects_coverage_gap(self) -> None:
        self.repo.single_book()
        self.assertEqual(self.repo.prepare()[0], 0)
        manifest = self.repo.manifest()
        manifest["assets"] = [asset for asset in manifest["assets"] if asset["assetRole"] != lib.ROLE_DETAIL]
        self.repo.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        code, output = self.repo.validate()
        self.assertEqual(code, 1, output)
        self.assertIn("COVERAGE_MISSING", output)

    def test_validator_detects_wrong_story_version_id(self) -> None:
        self.repo.single_book(storybook_id=901)
        self.assertEqual(self.repo.prepare()[0], 0)
        manifest = self.repo.manifest()
        for asset in manifest["assets"]:
            asset["storyVersionId"] = asset["storybookId"]  # the classic mistake
        self.repo.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        code, output = self.repo.validate()
        self.assertEqual(code, 1, output)
        self.assertIn("ASSET_VERSION_ID_MISMATCH", output)

    def test_validator_detects_declared_size_over_budget(self) -> None:
        self.repo.single_book()
        self.assertEqual(self.repo.prepare()[0], 0)
        code, output = self.repo.validate("--max-thumb-bytes", "200")
        self.assertEqual(code, 1, output)
        self.assertIn("ASSET_DECLARED_BYTES_EXCEED_LIMIT", output)
        self.assertIn("ASSET_BYTES_EXCEED_LIMIT", output)

    def test_generation_enforces_byte_budget(self) -> None:
        self.repo.single_book()
        code, output = self.repo.prepare("--max-thumb-bytes", "200")
        self.assertEqual(code, 1, output)
        self.assertIn("ASSET_TOO_LARGE", output)
        self.assertFalse(self.repo.manifest_path.exists())

    def test_validator_allows_missing_files_only_with_the_flag(self) -> None:
        self.repo.single_book()
        self.assertEqual(self.repo.prepare()[0], 0)
        for path in self.repo.output.glob("*.jpg"):
            path.unlink()
        code, output = self.repo.validate()
        self.assertEqual(code, 1, output)
        self.assertIn("ASSET_FILE_MISSING", output)
        code, output = self.repo.validate("--allow-missing")
        self.assertEqual(code, 0, output)
        self.assertIn("ASSET_FILE_MISSING", output)

    def test_validator_detects_duplicate_ids(self) -> None:
        self.repo.cover("a.png")
        self.repo.mapping([{"book_no": "B-901", "storybook_id": 901, "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/a.png"}])
        self.repo.versions([901])
        self.repo.storybooks([901])
        self.assertEqual(self.repo.prepare()[0], 0)
        manifest = self.repo.manifest()
        manifest["assets"].append(dict(manifest["assets"][0]))
        self.repo.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        code, output = self.repo.validate()
        self.assertEqual(code, 1, output)
        self.assertIn("ASSET_DUPLICATE_ID", output)
        self.assertIn("ASSET_DUPLICATE_PATH", output)

    # ---- CLI exit codes -------------------------------------------------
    def test_cli_exit_codes(self) -> None:
        self.repo.single_book()
        script = TOOLS_DIR / "prepare_assets.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--repo-root", str(self.repo.root), "--quiet"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

        (self.repo.seed / "cover_mapping.json").write_text("[{", encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(script), "--repo-root", str(self.repo.root), "--quiet"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)

        validator = TOOLS_DIR / "validate_assets.py"
        completed = subprocess.run(
            [sys.executable, str(validator), "--repo-root", str(self.repo.root), "--manifest", "missing.json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
