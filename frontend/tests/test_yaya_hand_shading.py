"""Check the palm-to-wrist contrast in Yaya's embedded textures (requires Pillow)."""

import json
import struct
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageStat


MODEL = Path(__file__).resolve().parents[1] / "public/assets/mascot/yaya-reading.glb"
# Matching palm and wrist patches on each hand, in the rig's 1024px UV layout.
HAND_PATCHES = (((314, 845), (315, 839)), ((933, 589), (808, 795)))


def green_median(image, point):
    x, y = point
    box = tuple(round(value * scale / 1024) for value, scale in (
        (x - 3, image.width), (y - 3, image.height),
        (x + 4, image.width), (y + 4, image.height),
    ))
    return ImageStat.Stat(image.crop(box)).median[1]


def test_palm_shading():
    data = MODEL.read_bytes()
    json_end = 20 + struct.unpack_from("<I", data, 12)[0]
    model = json.loads(data[20:json_end])
    binary_start = json_end + 8
    for material in model["materials"]:
        texture = material["pbrMetallicRoughness"]["baseColorTexture"]["index"]
        image = model["images"][model["textures"][texture]["source"]]
        view = model["bufferViews"][image["bufferView"]]
        start = binary_start + view.get("byteOffset", 0)
        with Image.open(BytesIO(data[start:start + view["byteLength"]])) as atlas:
            for palm, wrist in HAND_PATCHES:
                # A broad baked dark patch used to reduce this ratio to about 0.47.
                ratio = green_median(atlas, palm) / green_median(atlas, wrist)
                assert ratio > 0.72, (material["name"], palm, ratio)


if __name__ == "__main__":
    test_palm_shading()
