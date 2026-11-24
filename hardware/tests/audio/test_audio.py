"""Compile and exercise H07 production audio logic with deterministic host fakes."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    components = here.parents[1] / "firmware" / "components"
    audio = components / "board_audio"
    with tempfile.TemporaryDirectory(prefix="zhiya-audio-") as temp:
        build = Path(temp)
        for stub in ("esp_err.h", "board_4_3c.h", "esp_heap_caps.h", "esp_log.h", "esp_timer.h",
                     "freertos/queue.h", "freertos/semphr.h", "freertos/task.h"):
            header = build / stub
            header.parent.mkdir(exist_ok=True)
            header.write_text('#include "host_stubs.h"\n', encoding="utf-8")
        panel = (components / "board_4_3c" / "board_4_3c_panel.c").read_text(encoding="utf-8")
        write_functions(build / "expander_functions.h", panel, ("update_expander", "set_output"))
        driver = (audio / "audio_hw.c").read_text(encoding="utf-8")
        write_functions(build / "hardware_functions.h", driver, (
            "keep_first_error", "start_silent_clock", "audio_hw_start_rx", "audio_hw_start_tx",
            "audio_hw_stop", "audio_hw_release",
        ))
        includes = [str(path) for path in (build, here, audio, audio / "include")]
        compiler = [args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", *(f"-I{path}" for path in includes)]
        for test, sources in (
            ("audio_unit", [here / "audio_unit.c", audio / "audio_pcm.c"]),
            ("audio_hw_unit", [here / "audio_hw_unit.c"]),
        ):
            executable = build / f"{test}.exe"
            subprocess.run([*compiler, *map(str, sources), "-o", str(executable), "-lm"], check=True)
            subprocess.run([str(executable)], check=True)


def write_functions(path, source, names):
    bodies = [re.search(r"(?:static )?(?:esp_err_t|void) " + name + r"\([^{}]+\)\n\{.*?\n\}",
                       source, re.S).group() for name in names]
    path.write_text("\n".join(bodies), encoding="utf-8")


if __name__ == "__main__":
    main()
