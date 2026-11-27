"""Test H06 resources, the portable state machine, and the production LVGL UI."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "hardware/tools"))
from pack_reader_demo import checked_jpeg, load, pack, prepare


class MediaPackTest(unittest.TestCase):
    def test_deterministic_and_subset(self):
        one = pack(ROOT, 1)
        three = pack(ROOT, 3)
        self.assertEqual(three, pack(ROOT, 3))
        self.assertTrue(three[1].startswith(one[1]))
        self.assertLess(len(three[1]), 1024 * 1024)
        self.assertIn("小狐狸的雨伞", one[0])

    def test_manifest_offsets_and_real_jpeg(self):
        source, bundle, _, _ = pack(ROOT, 3)
        assets = re.findall(r"\{(\d+), (\d+), (\d+), (\d+)\}", source)
        self.assertEqual(len(assets), 23)
        offset = 0
        for first, size, width, height in sorted(tuple(map(int, asset)) for asset in assets):
            self.assertEqual(first, offset)
            offset += size
            with Image.open(io.BytesIO(bundle[first:offset])) as image:
                image.load()
                self.assertEqual(image.size, (width, height))
                self.assertEqual(image.format, "JPEG")
                self.assertFalse(image.info.get("progressive"))
        self.assertEqual(offset, len(bundle))

    def test_missing_file_bad_path_and_hash(self):
        _, _, _, pages, _ = prepare(ROOT, 3)
        page = pages[(2, 2, 1)]
        directory = "hardware/assets/generated-pages"
        with self.assertRaises(ValueError):
            checked_jpeg(ROOT, {**page, "assetPath": f"{directory}/../page.jpg"}, page, directory)
        with self.assertRaises(ValueError):
            checked_jpeg(ROOT, {**page, "sha256": "0" * 64}, page, directory)
        with tempfile.TemporaryDirectory() as path, self.assertRaises(FileNotFoundError):
            checked_jpeg(Path(path), page, page, directory)

    def test_rejects_non_device_images_with_matching_manifest_hash(self):
        cases = (
            ("PNG", {}, (40, 30), "JPEG decode failed"),
            ("JPEG", {"progressive": True}, (40, 30), "RGB Baseline"),
            ("JPEG", {}, (41, 30), "dimensions differ from manifest"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "assets"
            directory.mkdir()
            file = directory / "sample.jpg"
            for format_name, options, (width, height), error in cases:
                with self.subTest(format=format_name, dimensions=(width, height), **options):
                    Image.new("RGB", (40, 30)).save(file, format=format_name, **options)
                    data = file.read_bytes()
                    ref = {
                        "assetPath": "assets/sample.jpg", "assetRole": "PAGE", "devicePath": "sample.jpg",
                        "format": "JPEG", "width": width, "height": height, "fileSize": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    with self.assertRaisesRegex(ValueError, error):
                        checked_jpeg(root, ref, ref, "assets")

    def test_font_covers_pack_and_labels(self):
        _, _, glyphs, _ = pack(ROOT, 3)
        font = (ROOT / "hardware/firmware/main/h06_reader_font.c").read_text(encoding="utf-8")
        supported = {int(code, 16) for code in re.findall(r"/\* U\+([0-9A-Fa-f]+)", font)}
        self.assertTrue({ord(char) for char in glyphs} <= supported)
        self.assertTrue(set(range(32, 127)) <= supported)

    def test_shelf_excludes_draft_and_rejects_stale_version(self):
        def with_draft(root, path):
            document = load(root, path)
            if path.endswith("/storybooks.json"):
                document["items"][1]["status"] = "DRAFT"
            return document

        with patch("pack_reader_demo.load", side_effect=with_draft):
            source, _, _, _ = pack(ROOT, 3)
            self.assertNotIn("青蛙的音乐会", source)

        def stale_detail(root, path):
            document = load(root, path)
            if path.endswith("/story-details.json"):
                document["items"][0]["versionNumber"] += 1
            return document

        with patch("pack_reader_demo.load", side_effect=stale_detail), self.assertRaises(ValueError):
            pack(ROOT, 1)

    def test_missing_page_asset_keeps_text_and_navigation(self):
        def with_missing_image(root, path):
            document = load(root, path)
            if path.endswith("/story-details.json"):
                document["items"][0]["pages"][0]["localPageAsset"] = None
            if path.endswith("/pages-manifest.json"):
                document["assets"] = [
                    item for item in document["assets"]
                    if (item["storybookId"], item["storyVersionId"], item["pageNumber"]) != (2, 2, 1)
                ]
            return document

        actual, original, _, _ = pack(ROOT, 1)
        with patch("pack_reader_demo.load", side_effect=with_missing_image):
            source, bundle, _, _ = pack(ROOT, 1)
        first_page = next(line for line in actual.splitlines() if line.strip().startswith("{1, 2, "))
        self.assertIn(first_page.rsplit(", {", 1)[0] + ", {0}},", source)
        self.assertLess(len(bundle), len(original))


def run_ui_regression(cc, jobs, work, core, catalog):
    firmware = ROOT / "hardware/firmware"
    lvgl = firmware / "managed_components/lvgl__lvgl"
    version = (lvgl / "lv_version.h").read_text(encoding="utf-8")
    for key, expected in (("MAJOR", 9), ("MINOR", 2), ("PATCH", 0)):
        if not re.search(rf"#define LVGL_VERSION_{key}\s+{expected}\b", version):
            raise ValueError("Expected LVGL 9.2.0")

    (work / "esp_err.h").write_text(
        "#pragma once\ntypedef int esp_err_t;\n#define ESP_OK 0\n#define ESP_ERR_NO_MEM 257\n"
    )
    (work / "board_4_3c.h").write_text(
        '#pragma once\n#include "esp_err.h"\n#include "lvgl.h"\nvoid board_4_3c_log_memory(void);\n'
    )
    (work / "esp_log.h").write_text(
        "#include <stdio.h>\n"
        "#define ESP_LOGI(tag, ...) do { (void)(tag); if (0) fprintf(stderr, __VA_ARGS__); } while (0)\n"
    )
    common = [cc, "-std=c11", "-O1", "-DLV_CONF_SKIP"]
    for directory in (lvgl, work, core / "include", firmware / "main"):
        common.extend(("-I", str(directory)))
    sources = sorted((lvgl / "src").rglob("*.c")) + [
        core / "reader_core.c", catalog, firmware / "main/h06_reader_font.c", HERE / "reader_ui_regression.c",
    ]

    def compile_one(item):
        index, source = item
        obj = work / f"ui-{index}.o"
        subprocess.run(common + ["-c", str(source), "-o", str(obj)], check=True)
        return obj

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        objects = list(pool.map(compile_one, enumerate(sources)))
    response = work / "ui-objects.rsp"
    response.write_text("\n".join(f'"{obj.as_posix()}"' for obj in objects), encoding="utf-8")
    executable = work / "reader_ui_regression.exe"
    subprocess.run([cc, "@" + str(response), "-lm", "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MediaPackTest))
    if not result.wasSuccessful():
        return 1
    core = ROOT / "hardware/firmware/components/reader_core"
    if not (ROOT / "hardware/firmware/managed_components/lvgl__lvgl/lv_version.h").is_file():
        parser.error("Run idf.py build first to obtain LVGL 9.2.0")
    with tempfile.TemporaryDirectory(prefix="h06-reader-") as path:
        path = Path(path)
        for limit in (1, 3):
            source = path / "reader_demo.c"
            source.write_text(pack(ROOT, limit)[0], encoding="utf-8")
            program = path / "reader_core_test.exe"
            subprocess.run([args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(core / "include"), str(core / "reader_core.c"),
                            str(source), str(HERE / "reader_core_test.c"), "-o", str(program)], check=True)
            subprocess.run([str(program)], check=True)
        run_ui_regression(args.cc, args.jobs, path, core, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
