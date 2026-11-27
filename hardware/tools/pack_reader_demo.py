"""Build deterministic firmware DTOs and a JPEG bundle from H03 and H05."""
import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
MAX_ID = 9007199254740991


def load(root, path):
    return json.loads((root / path).read_text(encoding="utf-8"))


def integer(value, maximum=MAX_ID):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"Invalid positive integer: {value!r}")
    return value


def string(value, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()) or "\0" in value:
        raise ValueError("Invalid display text")
    return json.dumps(value, ensure_ascii=False)


def indexed(items, key):
    result = {}
    for item in items:
        identity = key(item)
        if identity in result:
            raise ValueError(f"Duplicate record: {identity}")
        result[identity] = item
    return result


def book_key(item):
    return integer(item["storybookId"]), integer(item["storyVersionId"])


def checked_jpeg(root, ref, manifest, directory):
    path = PurePosixPath(ref["assetPath"])
    if path.parent.as_posix() != directory or path.suffix != ".jpg" or "\\" in str(path):
        raise ValueError(f"Invalid local JPEG path: {path}")
    file = root / path
    if not file.resolve().is_relative_to((root / directory).resolve()):
        raise ValueError(f"JPEG resolves outside asset directory: {path}")
    for field in ("assetPath", "assetRole", "devicePath", "format", "width", "height", "fileSize", "sha256"):
        if ref[field] != manifest[field]:
            raise ValueError(f"Manifest mismatch for {field}: {path}")
    width, height = integer(ref["width"], 400), integer(ref["height"], 300)
    data = file.read_bytes()
    if ref["format"] != "JPEG" or len(data) != ref["fileSize"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise ValueError(f"JPEG integrity failed: {path}")
    return data, width, height


def prepare(root, limit):
    fixtures = "hardware/mock/fixtures"
    catalog = load(root, f"{fixtures}/storybooks.json")
    details = load(root, f"{fixtures}/story-details.json")
    demo = load(root, f"{fixtures}/demo-session.json")
    if not all(item.get("demoOnly") is True for item in (catalog, details, demo)):
        raise ValueError("Offline pack requires DEMO fixtures")
    featured = integer(demo["session"]["storyVersionId"])
    if demo["session"]["currentPage"] != 1:
        raise ValueError("Unexpected DEMO fixture start page")
    books = indexed(details["items"], book_key)
    cover_dir = "hardware/assets/generated"
    page_dir = "hardware/assets/generated-pages"
    covers = indexed(load(root, f"{cover_dir}/assets-manifest.json")["assets"],
                     lambda item: (*book_key(item), item["assetRole"]))
    pages = indexed(load(root, f"{page_dir}/pages-manifest.json")["assets"],
                    lambda item: (*book_key(item), item["pageNumber"]))
    published_demo = (item for item in catalog["items"] if item["status"] == "PUBLISHED" and
                      item.get("demoOnly") is True)
    selected = sorted(indexed(published_demo, book_key).values(),
                      key=lambda item: (item["storyVersionId"] != featured, *book_key(item)))[:limit]
    if not selected or selected[0]["storyVersionId"] != featured:
        raise ValueError("DEMO reference book missing")
    return selected, books, covers, pages, featured


def pack(root, limit):
    selected, books, covers, pages, featured = prepare(root, limit)
    bundle = bytearray()
    dependencies = set()
    c_source = ['#include "reader_core.h"']
    records = []
    ui_source = root / "hardware/firmware/main/reader_ui.c"
    glyph_text = re.findall(r'"([^"\n]*)"', ui_source.read_text(encoding="utf-8"))

    def asset(ref, record, directory):
        if ref is None:
            if record is not None:
                raise ValueError("Fixture omits an existing local asset")
            return "{0}"
        if record is None:
            raise ValueError("Local asset has no manifest entry")
        data, width, height = checked_jpeg(root, ref, record, directory)
        dependencies.add((root / ref["assetPath"]).resolve())
        result = f"{{{len(bundle)}, {len(data)}, {width}, {height}}}"
        bundle.extend(data)
        return result

    for index, entry in enumerate(selected):
        identity = book_key(entry)
        book = books[identity]
        for key in ("versionNumber", "title", "ageMin", "ageMax", "pageCount", "localCover", "status", "demoOnly"):
            if entry[key] != book[key]:
                raise ValueError(f"Shelf/detail mismatch for {key}: {identity}")
        version = integer(book["versionNumber"], 2**32 - 1)
        page_index = indexed(book["pages"], lambda item: integer(item["pageNumber"], 2**32 - 1))
        if 1 not in page_index or len(page_index) != book["pageCount"] or len(page_index) > 128:
            raise ValueError(f"Invalid page index: {identity}")
        visited = set()
        number = 1
        while number is not None:
            if number not in page_index or number in visited:
                raise ValueError(f"Invalid default reading path: {identity}")
            visited.add(number)
            number = page_index[number]["defaultNextPageNumber"]
            if number is not None:
                integer(number, 2**32 - 1)
        if len(visited) != len(page_index):
            raise ValueError(f"DEMO requires a complete default reading path: {identity}")
        summary = book["summary"] or ""
        glyph_text.extend((book["title"], summary))
        c_source.append(f"static const reader_page_t pages_{index}[] = {{")
        for number, page in sorted(page_index.items()):
            image = asset(page["localPageAsset"], pages.get((*identity, number)), "hardware/assets/generated-pages")
            glyph_text.append(page["text"])
            c_source.append(f'    {{{number}, {page["defaultNextPageNumber"] or 0}, {string(page["text"])}, {image}}},')
        c_source.append("};")
        thumb = asset(book["localCover"]["thumbnail"], covers.get((*identity, "THUMBNAIL")), "hardware/assets/generated")
        cover = asset(book["localCover"]["detail"], covers.get((*identity, "DETAIL")), "hardware/assets/generated")
        ages = integer(book["ageMin"], 99), integer(book["ageMax"], 99)
        records.append(f"    {{{identity[0]}ULL, {identity[1]}ULL, {version}, {string(book['title'])}, "
                       f'{string(summary, allow_empty=True)}, {ages[0]}, {ages[1]}, '
                       f'{thumb}, {cover}, pages_{index}, {len(page_index)}}},')
    c_source.extend(["static const reader_book_t books[] = {", *records, "};",
                     f"const reader_catalog_t reader_demo = {{books, {len(records)}, {featured}ULL}};", ""])
    glyphs = "".join(sorted({char for text in glyph_text for char in text if ord(char) >= 128}))
    return "\n".join(c_source), bytes(bundle), glyphs, sorted(dependencies | {ui_source.resolve()})


def write_if_changed(path, data):
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--books", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    source, bundle, glyphs, dependencies = pack(args.root.resolve(), args.books)
    args.out.mkdir(parents=True, exist_ok=True)
    write_if_changed(args.out / "reader_demo.c", source.encode("utf-8"))
    write_if_changed(args.out / "h06_media.bin", bundle)
    write_if_changed(args.out / "glyphs.txt", glyphs.encode("utf-8"))
    write_if_changed(args.out / "inputs.txt", "\n".join(path.as_posix() for path in dependencies).encode())
    jpeg_count = sum(path.suffix == ".jpg" for path in dependencies)
    print(f"H06 pack: {args.books} books, {jpeg_count} JPEGs, {len(bundle)} B, {len(glyphs)} Unicode glyphs")


if __name__ == "__main__":
    main()
