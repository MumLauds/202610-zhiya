"""Regression checks for browser evidence without running a deployment."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from verify_restored_browser import TITLE, page_illustration, restored_volume


class BrowserProofTests(unittest.TestCase):
    def test_matching_file_with_wrong_rendered_pixels_is_rejected(self):
        folder = Path(__file__).parent
        proof = json.loads((folder / "browser-result.json").read_bytes())["source"]["pageImage"]
        fixture = json.loads((folder / "restore-result.json").read_bytes())["independentFamily"]["illustration"]
        page = Mock()
        image = page.locator.return_value.first
        image.get_attribute.return_value = f"{TITLE} · 第 1 页"
        with patch("verify_restored_browser.expect"):
            image.evaluate.return_value = proof
            self.assertEqual(page_illustration(page, fixture), proof)
            image.evaluate.return_value = dict(proof, rgbaSha256="0" * 64)
            with self.assertRaisesRegex(AssertionError, "pixels"):
                page_illustration(page, fixture)

    def test_second_login_requires_inspection_of_this_runs_restored_backend(self):
        source = {"volume": "run-b-source-data", "image": "same-image"}
        with TemporaryDirectory() as temp:
            path = Path(temp) / "b-restored-service-backend.json"
            with self.assertRaisesRegex(ValueError, "missing"):
                restored_volume(path, source)
            for wrong in (source, dict(source, volume="another-b-restored-data"),
                          {"volume": "run-b-restored-data", "image": "other-image"}):
                path.write_text(json.dumps(wrong), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "differs"):
                    restored_volume(path, source)
            path.write_text(json.dumps(dict(source, volume="run-b-restored-data")), encoding="utf-8")
            self.assertEqual(restored_volume(path, source), "run-b-restored-data")


if __name__ == "__main__":
    unittest.main()
