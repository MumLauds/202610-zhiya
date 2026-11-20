"""Compile the production touch/flush functions against locked LVGL and mocked I2C.

Requires a host GCC-compatible compiler. Run after idf.py build has fetched LVGL.
Generated files and objects stay in a temporary directory.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[3]
BOARD = ROOT / "hardware/firmware/components/board_4_3c"


def function(source, name):
    """Copy a complete production definition, without rewriting its implementation."""
    match = re.search(r"(?:static )?(?:esp_err_t|void) " + name + r"\([^;]*?\)\n\{", source)
    if match is None:
        raise ValueError(f"Cannot locate {name}")
    depth = 1
    end = match.end()
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--revision", help="Read board functions from an earlier Git revision")
    args = parser.parse_args()
    lvgl = ROOT / "hardware/firmware/managed_components/lvgl__lvgl"
    if not (lvgl / "src/indev/lv_indev.c").is_file():
        parser.error("Run idf.py build first to obtain locked LVGL")
    versions = (lvgl / "lv_version.h").read_text(encoding="utf-8")
    for key, value in (("MAJOR", 9), ("MINOR", 2), ("PATCH", 0)):
        if not re.search(rf"#define LVGL_VERSION_{key}\s+{value}\b", versions):
            raise ValueError("Expected LVGL 9.2.0")

    def read_source(name):
        path = BOARD / name
        if args.revision:
            return subprocess.check_output(
                ["git", "show", args.revision + ":" + path.relative_to(ROOT).as_posix()],
                cwd=ROOT, encoding="utf-8")
        return path.read_text(encoding="utf-8")
    panel = read_source("board_4_3c_panel.c")
    port = read_source("board_4_3c.c")
    pieces = [function(panel, name) for name in
              ("touch_read", "touch_acknowledge", "board_4_3c_panel_poll_touch")]
    pieces += [function(port, name) for name in ("read_touch", "flush_display")]
    constants = "\n".join(line for line in panel.splitlines()
                          if line.startswith("#define GT911_") or line.startswith("#define I2C_TIMEOUT_MS"))
    harness = Path(__file__).with_name("touch_regression.c").read_text(encoding="utf-8")
    harness = harness.replace("/* PRODUCTION_CONSTANTS */", constants)
    harness = harness.replace("/* PRODUCTION_FUNCTIONS */", "\n\n".join(pieces))

    with tempfile.TemporaryDirectory(prefix="zhiya-h02-") as temporary:
        work = Path(temporary)
        source = work / "harness.c"
        source.write_text(harness, encoding="utf-8")
        sources = sorted((lvgl / "src").rglob("*.c")) + [source]
        common = [args.cc, "-std=c99", "-O1", "-DLV_CONF_SKIP", "-I", str(lvgl)]
        # Keep each compilation bounded and print its error in full.
        def compile_one(item):
            index, path = item
            obj = work / f"{index}.o"
            result = subprocess.run(common + ["-c", str(path), "-o", str(obj)],
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f"{path}\n{result.stdout}\n{result.stderr}")
            return obj
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            objects = list(pool.map(compile_one, enumerate(sources)))
        response = work / "objects.rsp"
        response.write_text("\n".join('"' + obj.as_posix() + '"' for obj in objects), encoding="utf-8")
        executable = work / "touch-regression.exe"
        subprocess.run([args.cc, "@" + str(response), "-lm", "-o", str(executable)], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    run()
