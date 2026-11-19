"""Build only the tracked frontend at a named commit and publish a fresh, verified dist."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def prepare(commit, output, installed=None, offline_cache=None):
    repo = Path(__file__).resolve().parents[4]
    git = ["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo)]
    actual = subprocess.check_output([*git, "rev-parse", "--verify", commit + "^{commit}"], text=True).strip()
    if actual != commit:
        raise ValueError("Specify the full commit ID")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="g15-frontend-") as workspace:
        stage = Path(workspace)
        archive = stage / "source.zip"
        subprocess.run([*git, "archive", "--format=zip", "-o", archive, commit, "--", "frontend"], check=True)
        with zipfile.ZipFile(archive) as source:
            source.extractall(stage)
        frontend = stage / "frontend"
        if installed:
            for name in ("package.json", "package-lock.json"):
                if json.loads((frontend / name).read_bytes()) != json.loads((installed / name).read_bytes()):
                    raise ValueError(f"{name} differs from the requested commit")
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("npm is required to install the locked dependencies")
        install = [npm, "ci"]
        if offline_cache is not None or installed is not None:
            # esbuild's optional-binary postinstall has its own network fallback.
            install.extend(["--offline", "--ignore-scripts", "--no-audit", "--no-fund"])
        if offline_cache is not None:
            install.extend(["--cache", str(offline_cache.resolve())])
        subprocess.run(install, cwd=frontend, check=True)
        subprocess.run([npm, "run", "build", "--", "--outDir", str(output / "dist")], cwd=frontend, check=True)
    if not (output / "dist/index.html").is_file():
        raise ValueError("The current build did not produce index.html")
    checksums = []
    for path in (output / "dist").rglob("*"):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            checksums.append(f"{digest}  {path.relative_to(output).as_posix()}\n")
    (output / "sha256sums").write_bytes("".join(sorted(checksums)).encode())
    # The Docker step accepts the bundle only after the entire build and manifest succeeded.
    (output / "commit").write_bytes((commit + "\n").encode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit")
    parser.add_argument("output", type=Path, help="New directory for this run")
    parser.add_argument("--offline-cache", type=Path, help="Install the locked packages offline from this npm cache")
    parser.add_argument("--reuse-deps", type=Path, help="Legacy offline option: check this frontend's manifests and use npm's cache")
    args = parser.parse_args()
    prepare(args.commit, args.output, args.reuse_deps, args.offline_cache)
