import json
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from deployment_baseline import collect
from prepare_frontend import prepare

HERE = Path(__file__).resolve().parent


class DeploymentTests(unittest.TestCase):
    def test_failed_install_cannot_publish_existing_assets(self):
        revision = json.loads((HERE / "deployment-result.json").read_bytes())["commit"]
        actual_run = subprocess.run

        def fail_install(command, **kwargs):
            if command == ["npm", "ci"]:
                raise subprocess.CalledProcessError(1, command)
            return actual_run(command, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "frontend-build"
            with patch("prepare_frontend.shutil.which", return_value="npm"), patch("subprocess.run", side_effect=fail_install):
                with self.assertRaises(subprocess.CalledProcessError):
                    prepare(revision, output, None)
            self.assertFalse((output / "commit").exists())
            self.assertFalse((output / "dist").exists())
            (output / "dist").mkdir()
            (output / "dist/index.html").write_text("stale")
            with self.assertRaises(FileExistsError):
                prepare(revision, output, None)

    def test_requires_post_login_snapshot_without_extra_reading_writes_or_provider_calls(self):
        archive = json.loads((HERE / "deployment-result.json").read_bytes())
        browser = json.loads((HERE / "browser-result.json").read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            for index, phase in enumerate(("beforeRestart", "afterBrowserReadback")):
                record = archive[phase]
                probe = {"database": record["database"], "counts": record["counts"], "loginSessions": 1 + index}
                (state / f"{phase}-db.json").write_text(json.dumps(probe))
                calls = [dict(call, status=200) for call in record["supplierCalls"]]
                (state / f"{phase}-provider.jsonl").write_text("\n".join(json.dumps(call) for call in calls) + "\n")
                for name, container in archive["containers"].items():
                    value = {"image": container["image"], "reference": "fixture:" + name, "started": str(index),
                             "revision": archive["commit"], "volume": container.get("persistentSqliteVolume", "")}
                    (state / f"{phase}-{name}.json").write_text(json.dumps(value))
            args = (state, browser, archive["project"], archive["commit"])
            self.assertEqual(collect(*args)["afterBrowserReadback"]["counts"]["reports"], 1)
            dbfile = state / "afterBrowserReadback-db.json"
            original = dbfile.read_text()
            for key in ("loginSessions", "counts"):
                with self.subTest(key=key):
                    changed = json.loads(original)
                    if key == "loginSessions":
                        changed[key] -= 1
                    else:
                        changed[key]["executions"] += 1
                    dbfile.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        collect(*args)
            dbfile.write_text(original)
            with (state / "afterBrowserReadback-provider.jsonl").open("a") as log:
                log.write('{"call": "report", "status": 422}\n')
            with self.assertRaises(ValueError):
                collect(*args)


if __name__ == "__main__":
    unittest.main()
