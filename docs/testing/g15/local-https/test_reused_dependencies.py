import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from prepare_frontend import verify_reused_dependencies


class ReusedDependenciesTests(unittest.TestCase):
    def test_reused_tree_must_match_receipt_and_locked_versions(self):
        key = "node_modules/vite"
        expected = {"version": "7.3.8", "integrity": "test-integrity"}
        with TemporaryDirectory() as directory:
            frontend = Path(directory) / "source"
            installed = Path(directory) / "installed"
            package = installed / key / "package.json"
            receipt = installed / "node_modules/.package-lock.json"
            frontend.mkdir()
            package.parent.mkdir(parents=True)
            packages = {"": {}, key: expected, "node_modules/platform-addon": {"version": "1", "optional": True}}
            for folder in (frontend, installed):
                (folder / "package.json").write_text('{"devDependencies":{"vite":"^7"}}')
                (folder / "package-lock.json").write_text(json.dumps({"packages": packages}))
            package.write_text(json.dumps(expected))
            receipt.write_text(json.dumps({"packages": {key: expected}}))
            verify_reused_dependencies(frontend, installed)

            for outdated in ({}, {key: dict(expected, version="7.2.0")},
                             {key: dict(expected, integrity="other-integrity")}):
                with self.subTest(receipt=outdated), self.assertRaisesRegex(ValueError, key):
                    receipt.write_text(json.dumps({"packages": outdated}))
                    verify_reused_dependencies(frontend, installed)
            receipt.write_text(json.dumps({"packages": {key: expected}}))
            package.write_text('{"version":"7.2.0"}')
            with self.assertRaisesRegex(ValueError, "Installed version"):
                verify_reused_dependencies(frontend, installed)


if __name__ == "__main__":
    unittest.main()
