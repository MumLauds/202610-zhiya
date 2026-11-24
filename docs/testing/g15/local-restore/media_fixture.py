"""Add a synthetic paper-boat illustration to the unused G15 source fixture."""
import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from PIL import Image, ImageDraw


def seed_image(database):
    root = database.parent / "fixture-source"
    image_path = root / "g15-restore-paper-boat.png"
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True)) as db, db:
        pages = db.execute("SELECT version_id,page_no,body_text,image_ref FROM story_pages").fetchall()
        if (pages != [(11, 1, "小园把一只纸船放进水里。", "none")]
                or db.execute("SELECT 1 FROM reading_sessions LIMIT 1").fetchone()
                or (database.parent / f".{database.name}.media_pins").exists()):
            raise ValueError("Expected the fresh G15 fixture before its first reading or media request")

        root.mkdir()
        image = Image.new("RGB", (128, 80), "#d2eeed")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 50, 127, 79), fill="#60b5bb")
        draw.polygon([(33, 46), (67, 22), (81, 46)], fill="#fff7df")
        draw.polygon([(23, 43), (59, 49), (99, 41), (79, 59), (43, 58)], fill="#efae62")
        draw.line([(23, 43), (59, 49), (99, 41)], fill="#6b564c", width=2)
        draw.line([(17, 65), (42, 65)], fill="#edf7eb", width=2)
        draw.line([(79, 69), (106, 69)], fill="#edf7eb", width=2)
        with image_path.open("xb") as output:
            image.save(output, format="PNG", compress_level=0)
        db.execute("UPDATE story_pages SET image_ref=? WHERE version_id=11 AND page_no=1", (image_path.name,))

    data = image_path.read_bytes()
    print(json.dumps({"asset": "asset-11-page-1", "width": image.width, "height": image.height,
                      "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "rgbaSha256": hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    seed_image(parser.parse_args().database)
