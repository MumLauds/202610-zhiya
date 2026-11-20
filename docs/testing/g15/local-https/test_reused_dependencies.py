"""Exercise the offline build with local packages and an empty npm cache."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prepare_frontend import prepare


@unittest.skipUnless(shutil.which("npm") and shutil.which("node"), "offline build requires Node.js and npm")
class OfflineDependencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        repo = cls.root / "repo"
        cls.frontend = repo / "frontend"
        cls.frontend.mkdir(parents=True)
        cls.cache = cls.root / "cache"
        for name, source in (("renderer", "module.exports = require('palette');"),
                             ("palette", "module.exports = 'locked';")):
            package = cls.frontend / name
            package.mkdir()
            (package / "package.json").write_text(json.dumps({"name": name, "version": "1.0.0"}))
            (package / "index.js").write_text(source)
        manifest = {"name": "fixture", "version": "1.0.0",
                    "devDependencies": {"renderer": "file:renderer", "palette": "file:palette"},
                    "scripts": {"build": "node build.cjs"}}
        (cls.frontend / "package.json").write_text(json.dumps(manifest))
        (cls.frontend / "build.cjs").write_text(
            "const fs=require('fs'), path=require('path'), dest=process.argv[3];"
            "fs.mkdirSync(dest); fs.writeFileSync(path.join(dest,'index.html'),require('renderer'));")
        subprocess.run([shutil.which("npm"), "install", "--package-lock-only", "--offline", "--ignore-scripts",
                        "--no-audit", "--no-fund", "--cache", str(cls.cache)], cwd=cls.frontend, check=True)
        for args in (["init", "-q"], ["add", "frontend"],
                     ["-c", "user.email=test@localhost", "-c", "user.name=fixture", "commit", "-qm", "fixture"]):
            subprocess.run(["git", *args], cwd=repo, check=True)
        cls.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        cls.script = repo / "docs/testing/g15/local-https/prepare_frontend.py"

    def check_build(self, changed_package, version, value):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            test = Path(directory)
            old = test / "previous"
            old.mkdir()
            for name in ("package.json", "package-lock.json"):
                shutil.copyfile(self.frontend / name, old / name)
            for name in ("renderer", "palette"):
                shutil.copytree(self.frontend / name, old / "node_modules" / name)
            package = old / "node_modules" / changed_package
            package.mkdir(parents=True, exist_ok=True)
            (package / "package.json").write_text(json.dumps({"name": package.name, "version": version}))
            source = f"module.exports = '{value}';"
            (package / "index.js").write_text(source)
            observed = subprocess.check_output(["node", "-p", "require('renderer')"], cwd=old, text=True)
            self.assertEqual(observed.strip(), value)
            output = test / "build"
            with patch("prepare_frontend.__file__", str(self.script)), patch.dict(os.environ, NODE_ENV="production"):
                prepare(self.commit, output, old, self.cache)
            self.assertEqual((output / "dist/index.html").read_text(), "locked")
            self.assertEqual((output / "commit").read_text().strip(), self.commit)
            self.assertEqual((package / "index.js").read_text(), source)

    def test_local_edits_with_unchanged_version_do_not_reach_dist(self):
        self.check_build("renderer", "1.0.0", "locally edited")

    def test_unlisted_nested_dependency_cannot_shadow_locked_package(self):
        self.check_build("renderer/node_modules/palette", "0.9.0", "stale nested palette")


if __name__ == "__main__":
    unittest.main()
