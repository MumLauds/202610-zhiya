"""H03 page-asset pipeline tests.

Fixtures are generated inside a temporary repository root. The real knowledge
base, seed data, cover assets and device are not modified.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import assets_lib as lib  # noqa: E402
import page_assets_lib as page  # noqa: E402
import prepare_page_assets  # noqa: E402
import validate_page_assets  # noqa: E402


class PageAssetRepo:
    """A throw-away repository containing only the page pipeline inputs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.seed = root / "data" / "seed"
        self.cover_root = root / lib.DEFAULT_SOURCE_ROOT
        self.page_root = root / page.DEFAULT_PAGE_SOURCE_ROOT
        self.output = root / page.DEFAULT_PAGE_OUTPUT_DIR
        self.manifest_path = self.output / page.PAGE_MANIFEST_NAME
        self.seed.mkdir(parents=True, exist_ok=True)
        self.cover_root.mkdir(parents=True, exist_ok=True)
        self.page_root.mkdir(parents=True, exist_ok=True)
        self._cover = self.cover("cover-b901.png")
        self.story_pages_path = self.seed / "story_pages.json"

    def write_json(self, name: str, payload: object) -> Path:
        path = self.seed / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def cover(self, name: str, size: tuple[int, int] = (800, 600)) -> Path:
        path = self.cover_root / name
        image = Image.new("RGB", size, (220, 225, 230))
        image.save(path, "PNG")
        image.close()
        return path

    def page(
        self,
        name: str,
        size: tuple[int, int] = (1200, 900),
        *,
        mode: str = "RGB",
        color: tuple[int, ...] = (210, 120, 90),
        fmt: str = "PNG",
        exif_orientation: int | None = None,
        transparent_half: bool = False,
    ) -> Path:
        path = self.page_root / name
        if transparent_half:
            image = Image.new("RGBA", size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rectangle([0, 0, size[0] // 2, size[1] - 1], fill=(205, 80, 90, 255))
        elif mode == "RGBA":
            image = Image.new(mode, size, tuple(color) + (255,))
        else:
            image = Image.new(mode, size, color)
        if exif_orientation is None:
            image.save(path, fmt)
        else:
            exif = Image.Exif()
            exif[274] = exif_orientation
            image.save(path, fmt, exif=exif.tobytes())
        image.close()
        return path

    def book(
        self,
        pages: list[dict],
        *,
        book_no: str = "B-901",
        storybook_id: int = 901,
        version_id: int = 1001,
        status: str = "PUBLISHED",
    ) -> None:
        self.write_json(
            "cover_mapping.json",
            [{
                "book_no": book_no,
                "storybook_id": storybook_id,
                "cover_path": f"{lib.DEFAULT_SOURCE_ROOT}/{self._cover.name}",
            }],
        )
        self.write_json(
            "storybooks.json",
            [{"id": storybook_id, "title": "测试绘本"}],
        )
        self.write_json(
            "story_versions.json",
            [{
                "id": version_id,
                "storybook_id": storybook_id,
                "version_no": 1,
                "title": "测试绘本",
                "status": status,
                "license_note": "NO-COMMERCIAL",
            }],
        )
        payload = []
        for index, spec in enumerate(pages, start=1):
            file_name = spec["file"]
            self.page(file_name, spec.get("size", (1200, 900)), **spec.get("image", {}))
            payload.append(
                {
                    "version_id": version_id,
                    "page_no": index,
                    "image_ref": f"{page.DEFAULT_PAGE_SOURCE_ROOT}/{file_name}",
                    "image_license_note": "NO-COMMERCIAL",
                    "default_next_page_number": index + 1 if index < len(pages) else None,
                    "body_text": f"第 {index} 页",
                }
            )
        self.story_pages_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def prepare(self, *extra: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = prepare_page_assets.run(["--repo-root", str(self.root), *extra])
        return code, buffer.getvalue()

    def validate(self, *extra: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = validate_page_assets.run(["--repo-root", str(self.root), *extra])
        return code, buffer.getvalue()

    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def write_manifest(self, payload: dict) -> None:
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class PagePipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = PageAssetRepo(Path(self._tmp.name))

    def _prepare_two_pages(self) -> None:
        self.repo.book([
            {"file": "page-1.png", "image": {"color": (210, 120, 90)}},
            {"file": "page-2.png", "image": {"color": (90, 120, 210)}},
        ])
        code, output = self.repo.prepare("--quiet")
        self.assertEqual(0, code, output)

    # ---- H03-P2/P3 ------------------------------------------------------
    def test_full_pipeline_and_manifest_contract(self) -> None:
        self.repo.book([
            {"file": "page-1.png", "size": (1200, 900)},
            {"file": "page-2.png", "size": (1600, 1200)},
            {"file": "page-3.png", "size": (800, 600)},
        ])
        code, output = self.repo.prepare("--quiet")
        self.assertEqual(0, code, output)
        manifest = self.repo.manifest()
        self.assertEqual("1.0", manifest["schemaVersion"])
        self.assertEqual("device-page-assets", manifest["kind"])
        self.assertFalse(manifest["assetSet"]["decoderVerified"])
        self.assertEqual(3, manifest["totals"]["assets"])
        self.assertEqual(3, manifest["totals"]["pages"])
        self.assertEqual(1, manifest["totals"]["books"])
        self.assertEqual(1, manifest["totals"]["versions"])
        self.assertEqual(4, len(manifest["assetSet"]["sourceFiles"]))
        self.assertEqual(3, len(manifest["assets"]))
        self.assertEqual(
            manifest["totals"]["assetBytes"],
            sum(asset["fileSize"] for asset in manifest["assets"]),
        )
        for asset in manifest["assets"]:
            self.assertEqual("PAGE", asset["assetRole"])
            self.assertEqual("NO-COMMERCIAL", asset["imageLicenseNote"])
            self.assertLessEqual(asset["width"], 400)
            self.assertLessEqual(asset["height"], 300)
            output_path = self.repo.root / asset["assetPath"]
            self.assertTrue(output_path.is_file())
            with Image.open(output_path) as image:
                self.assertEqual("JPEG", image.format)
                self.assertEqual("RGB", image.mode)
                self.assertEqual((asset["width"], asset["height"]), image.size)
            self.assertEqual(output_path.stat().st_size, asset["fileSize"])
        self.assertEqual(
            "book-b901-v1001-p001.jpg",
            Path(manifest["assets"][0]["assetPath"]).name,
        )
        self.assertEqual(
            "/assets/pages/book-b901-v1001-p001.jpg",
            manifest["assets"][0]["devicePath"],
        )
        code, output = self.repo.validate()
        self.assertEqual(0, code, output)

    def test_aspect_ratio_and_no_upscale(self) -> None:
        self.repo.book([
            {"file": "wide.png", "size": (1600, 400)},
            {"file": "tall.png", "size": (400, 1600)},
            {"file": "small.png", "size": (200, 100)},
        ])
        code, output = self.repo.prepare("--quiet")
        self.assertEqual(0, code, output)
        by_name = {Path(asset["assetPath"]).name: asset for asset in self.repo.manifest()["assets"]}
        self.assertEqual((400, 100), (by_name["book-b901-v1001-p001.jpg"]["width"], by_name["book-b901-v1001-p001.jpg"]["height"]))
        self.assertEqual((75, 300), (by_name["book-b901-v1001-p002.jpg"]["width"], by_name["book-b901-v1001-p002.jpg"]["height"]))
        self.assertEqual((200, 100), (by_name["book-b901-v1001-p003.jpg"]["width"], by_name["book-b901-v1001-p003.jpg"]["height"]))
        self.assertEqual(0, self.repo.validate()[0])

    def test_alpha_and_exif_are_normalised(self) -> None:
        self.repo.book([
            {
                "file": "transparent.png",
                "size": (800, 600),
                "image": {"transparent_half": True},
            },
            {
                "file": "rotated.png",
                "size": (800, 600),
                "image": {"exif_orientation": 6},
            },
        ])
        code, output = self.repo.prepare("--quiet")
        self.assertEqual(0, code, output)
        assets = self.repo.manifest()["assets"]
        self.assertTrue(assets[0]["alphaComposited"])
        self.assertTrue(assets[1]["exifOrientationApplied"])
        self.assertEqual((225, 300), (assets[1]["width"], assets[1]["height"]))
        for asset in assets:
            path = self.repo.root / asset["assetPath"]
            with Image.open(path) as image:
                self.assertEqual("RGB", image.mode)
                self.assertFalse(image.getexif())
        self.assertEqual(0, self.repo.validate()[0])

    # ---- H03-P1 and H03-P4 failure modes --------------------------------
    def test_duplicate_page_number_is_rejected_without_publishing(self) -> None:
        self.repo.book([{"file": "page-1.png"}])
        payload = json.loads(self.repo.story_pages_path.read_text(encoding="utf-8"))
        payload.append(dict(payload[0]))
        self.repo.story_pages_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, output = self.repo.prepare("--quiet")
        self.assertEqual(1, code, output)
        self.assertIn("PAGE_DUPLICATE_NUMBER", output)
        self.assertFalse(self.repo.manifest_path.exists())

    def test_path_traversal_and_url_are_rejected(self) -> None:
        self.repo.book([{"file": "page-1.png"}])
        payload = json.loads(self.repo.story_pages_path.read_text(encoding="utf-8"))
        payload[0]["image_ref"] = "../outside.png"
        self.repo.story_pages_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, output = self.repo.prepare("--quiet")
        self.assertEqual(1, code, output)
        self.assertIn("PATH_TRAVERSAL", output)
        payload[0]["image_ref"] = "https://example.test/page.png"
        self.repo.story_pages_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        code, output = self.repo.prepare("--quiet")
        self.assertEqual(1, code, output)
        self.assertIn("SOURCE_PATH_URL", output)

    def test_corrupt_and_fake_extension_are_rejected(self) -> None:
        self.repo.book([{"file": "page-1.png"}])
        (self.repo.page_root / "page-1.png").write_bytes(b"not an image")
        code, output = self.repo.prepare("--quiet")
        self.assertEqual(1, code, output)
        self.assertIn("SOURCE_FORMAT_UNKNOWN", output)
        self.assertFalse(self.repo.manifest_path.exists())

    def test_validator_detects_tampered_output(self) -> None:
        self._prepare_two_pages()
        asset = self.repo.manifest()["assets"][0]
        path = self.repo.root / asset["assetPath"]
        path.write_bytes(path.read_bytes() + b"tamper")
        code, output = self.repo.validate()
        self.assertEqual(1, code, output)
        self.assertIn("PAGE_ASSET_SIZE_MISMATCH", output)
        self.assertIn("PAGE_ASSET_SHA_MISMATCH", output)

    def test_validator_detects_swapped_page_identity(self) -> None:
        self._prepare_two_pages()
        manifest = self.repo.manifest()
        first, second = manifest["assets"][0], manifest["assets"][1]
        first["sourceSha256"], second["sourceSha256"] = (
            second["sourceSha256"],
            first["sourceSha256"],
        )
        self.repo.write_manifest(manifest)
        code, output = self.repo.validate()
        self.assertEqual(1, code, output)
        self.assertIn("SOURCE_SHA_MISMATCH", output)
        self.assertNotIn("SOURCE_PATH_MISMATCH", output)

    def test_missing_and_extra_outputs_are_rejected(self) -> None:
        self._prepare_two_pages()
        extra = self.repo.output / "unexpected.jpg"
        extra.write_bytes((self.repo.output / "book-b901-v1001-p001.jpg").read_bytes())
        code, output = self.repo.validate()
        self.assertEqual(1, code, output)
        self.assertIn("PAGE_UNREFERENCED_ASSET", output)
        extra.unlink()
        (self.repo.output / "book-b901-v1001-p001.jpg").unlink()
        code, output = self.repo.validate()
        self.assertEqual(1, code, output)
        self.assertIn("PAGE_ASSET_FILE_MISSING", output)

    def test_failed_regeneration_keeps_existing_bundle(self) -> None:
        self._prepare_two_pages()
        manifest_before = self.repo.manifest_path.read_bytes()
        hashes_before = {
            asset["assetPath"]: asset["sha256"]
            for asset in self.repo.manifest()["assets"]
        }
        (self.repo.page_root / "page-1.png").write_bytes(b"broken")
        code, output = self.repo.prepare("--quiet")
        self.assertEqual(1, code, output)
        self.assertEqual(manifest_before, self.repo.manifest_path.read_bytes())
        hashes_after = {
            asset["assetPath"]: asset["sha256"]
            for asset in self.repo.manifest()["assets"]
        }
        self.assertEqual(hashes_before, hashes_after)
        for asset_path in hashes_after:
            self.assertTrue((self.repo.root / asset_path).is_file())

    def test_generation_is_byte_identical_on_second_run(self) -> None:
        self.repo.book([{"file": "page-1.png"}, {"file": "page-2.png"}])
        self.assertEqual(0, self.repo.prepare("--quiet")[0])
        first_manifest = self.repo.manifest_path.read_bytes()
        first_hashes = {
            asset["assetPath"]: asset["sha256"]
            for asset in self.repo.manifest()["assets"]
        }
        self.assertEqual(0, self.repo.prepare("--quiet")[0])
        self.assertEqual(first_manifest, self.repo.manifest_path.read_bytes())
        second_hashes = {
            asset["assetPath"]: asset["sha256"]
            for asset in self.repo.manifest()["assets"]
        }
        self.assertEqual(first_hashes, second_hashes)


class RealPageBundleTest(unittest.TestCase):
    """Integration checks against the committed real page bundle."""

    def test_real_bundle_is_complete_and_cover_manifest_is_unchanged(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        hardware = repo_root / "hardware"
        page_manifest = hardware / "assets" / "generated-pages" / page.PAGE_MANIFEST_NAME
        cover_manifest = hardware / "assets" / "generated" / lib.MANIFEST_NAME
        self.assertTrue(page_manifest.is_file(), "real page manifest is missing")
        self.assertTrue(cover_manifest.is_file(), "cover manifest is missing")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = validate_page_assets.run(["--json"])
        self.assertEqual(0, code, buffer.getvalue())
        report = json.loads(buffer.getvalue())
        self.assertTrue(report["ok"])
        self.assertEqual(104, report["assets"])
        self.assertEqual(20, report["books"])
        self.assertEqual(20, report["versions"])
        self.assertEqual(104, report["pages"])
        cover = json.loads(cover_manifest.read_text(encoding="utf-8"))
        self.assertEqual(40, len(cover["assets"]))
        self.assertEqual("device-cover-assets", cover["kind"])
        self.assertEqual(0, sum(1 for asset in cover["assets"] if asset.get("assetRole") == "PAGE"))


if __name__ == "__main__":
    unittest.main()
