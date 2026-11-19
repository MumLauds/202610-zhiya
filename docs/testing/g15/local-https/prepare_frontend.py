"""Build only the tracked frontend at a named commit and publish a fresh, verified dist."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def verify_reused_dependencies(frontend, installed):
    manifest = json.loads((frontend / "package.json").read_bytes())
    lock = json.loads((frontend / "package-lock.json").read_bytes())
    if (manifest != json.loads((installed / "package.json").read_bytes())
            or lock != json.loads((installed / "package-lock.json").read_bytes())):
        raise ValueError("Dependency manifests differ from the requested commit")

    # npm's hidden lock lists the packages installed on this platform.
    installed_packages = json.loads((installed / "node_modules/.package-lock.json").read_bytes())["packages"]
    locked_packages = lock["packages"]
    if installed_packages.keys() - locked_packages.keys():
        raise ValueError("The npm installation contains packages outside the requested lockfile")
    for path, expected in locked_packages.items():
        if not path:  # Project itself.
            continue
        receipt = installed_packages.get(path)
        package = installed / path / "package.json"
        if receipt is None and expected.get("optional") and not package.exists():
            continue
        if receipt is None or any(receipt.get(key) != expected.get(key) for key in ("version", "resolved", "integrity")):
            raise ValueError(f"npm installation and requested lockfile differ for {path}")
        if json.loads(package.read_bytes())["version"] != expected["version"]:
            raise ValueError(f"Installed version differs from the requested lockfile for {path}")


def prepare(commit, output, installed):
    repo = Path(__file__).resolve().parents[4]
    git = ["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo)]
    actual = subprocess.check_output([*git, "rev-parse", "--verify", commit + "^{commit}"], text=True).strip()
    if actual != commit:
        raise ValueError("Specify the full commit ID")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Node resolves imports from ancestor node_modules when reusing a local installation.
    with tempfile.TemporaryDirectory(prefix="g15-frontend-", dir=installed) as workspace:
        stage = Path(workspace)
        archive = stage / "source.zip"
        subprocess.run([*git, "archive", "--format=zip", "-o", archive, commit, "--", "frontend"], check=True)
        with zipfile.ZipFile(archive) as source:
            source.extractall(stage)
        frontend = stage / "frontend"
        if installed:
            verify_reused_dependencies(frontend, installed)
            vite = installed / "node_modules/vite/bin/vite.js"
            build = ["node", str(vite), "build"]
        else:
            npm = shutil.which("npm")
            if not npm:
                raise RuntimeError("npm is required to install the locked dependencies")
            subprocess.run([npm, "ci"], cwd=frontend, check=True)
            build = [npm, "run", "build", "--"]
        subprocess.run([*build, "--outDir", str(output / "dist")], cwd=frontend, check=True)
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
    parser.add_argument("--reuse-deps", type=Path, help="Optional frontend with matching manifests and node_modules")
    args = parser.parse_args()
    prepare(args.commit, args.output, args.reuse_deps.resolve() if args.reuse_deps else None)
