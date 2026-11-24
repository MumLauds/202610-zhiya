"""把 P6 测试文档 Markdown 转成 Word（.docx）。

支持本仓库文档实际使用的语法：标题、段落、表格、代码块、列表、引用、粗体、
行内代码、水平线。不追求通用 Markdown 兼容，只服务于测试文档排版。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

TITLE = "知芽——AI 亲子绘本共读与阅读启蒙系统"
SUBTITLE = "软件测试文档（P6）"
META = [
    ("课程", "2026 软件工程实践"),
    ("团队", "妈妈夸我说的队"),
    ("编写", "刘俊延（102401133，测试验收演示）"),
    ("版本", "v1.0"),
]


def set_cell_shading(cell, color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), color)
    tc_pr.append(shading)


def set_paragraph_shading(paragraph, color: str) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), color)
    p_pr.append(shading)


def add_field(paragraph, instruction: str, placeholder: str = "") -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(separate)
    run._r.append(text)
    run._r.append(end)


def add_page_number(section) -> None:
    footer = section.footer
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_field(paragraph, "PAGE", "1")


def add_header(section, text: str = "知芽 P6 软件测试文档") -> None:
    header = section.header
    paragraph = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run(text)
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)


def style_document(doc: Document, header_text: str = "知芽 P6 软件测试文档") -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    normal.paragraph_format.line_spacing = 1.35
    normal.paragraph_format.space_after = Pt(4)

    for name, size in (("Heading 1", 18), ("Heading 2", 14), ("Heading 3", 12)):
        style = doc.styles[name]
        style.font.name = "黑体"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0x1F, 0x3B, 0x2E)
        style.paragraph_format.space_before = Pt(14)
        style.paragraph_format.space_after = Pt(8)

    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.4)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.3)
    add_header(section, header_text)
    add_page_number(section)


def add_cover(doc: Document, title_text: str = TITLE, subtitle_text: str = SUBTITLE,
              meta: list[tuple[str, str]] | None = None) -> None:
    meta = meta or META
    for _ in range(4):
        doc.add_paragraph()
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(title_text)
    run.font.name = "黑体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
    run.font.size = Pt(24)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0x1F, 0x3B, 0x2E)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run(subtitle_text)
    run.font.name = "黑体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
    run.font.size = Pt(18)
    run.font.bold = True

    doc.add_paragraph()
    table = doc.add_table(rows=0, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    for key, value in meta:
        cells = table.add_row().cells
        cells[0].text = key
        cells[1].text = value
        for cell in cells:
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in paragraph.runs:
                    run.font.size = Pt(10.5)
        set_cell_shading(cells[0], "E8F1EA")

    doc.add_paragraph()
    note = doc.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = note.add_run("各项验收状态及证据来源见正文。")
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_page_break()
    toc_title = doc.add_paragraph()
    toc_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = toc_title.add_run("目  录")
    run.font.name = "黑体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
    run.font.size = Pt(16)
    run.font.bold = True
    toc = doc.add_paragraph()
    add_field(toc, 'TOC \\o "1-3" \\h \\z \\u', "目录将在 Word 中自动更新")
    doc.add_page_break()


INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")
INLINE_CODE = re.compile(r"`([^`]+)`")
LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def add_inline(paragraph, text: str) -> None:
    text = LINK.sub(r"\1", text)
    position = 0
    pattern = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")
    for match in pattern.finditer(text):
        if match.start() > position:
            paragraph.add_run(text[position:match.start()])
        token = match.group(0)
        if token.startswith("**"):
            run = paragraph.add_run(token[2:-2])
            run.bold = True
        else:
            run = paragraph.add_run(token[1:-1])
            run.font.name = "Consolas"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
            run.font.size = Pt(9.5)
        position = match.end()
    if position < len(text):
        paragraph.add_run(text[position:])


def add_table(doc: Document, rows: list[str]) -> None:
    parsed = []
    for row in rows:
        cells = [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", row.strip().strip("|"))]
        parsed.append(cells)
    if len(parsed) < 2:
        return
    header = parsed[0]
    body = [row for row in parsed[2:] if any(cell for cell in row)]
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    widths = {
        2: [3.4, 12.8],
        3: [3.6, 5.8, 6.8],
        4: [2.8, 4.2, 4.2, 5.0],
        5: [2.8, 1.8, 4.3, 1.6, 5.7],
        6: [2.2, 3.0, 3.0, 2.6, 2.8, 2.6],
        7: [2.0, 2.1, 2.1, 2.4, 2.4, 2.6, 2.6],
    }.get(len(header))
    if widths is None:
        widths = [16.2 / len(header)] * len(header)
    for index, width in enumerate(widths):
        table.columns[index].width = Cm(width)
    header_row = table.rows[0]
    tr_pr = header_row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)
    for index, value in enumerate(header):
        cell = table.rows[0].cells[index]
        cell.width = Cm(widths[index])
        cell.text = ""
        paragraph = cell.paragraphs[0]
        add_inline(paragraph, value)
        for run in paragraph.runs:
            run.bold = True
            run.font.size = Pt(9)
        set_cell_shading(cell, "E8F1EA")
    for row in body:
        cells = table.add_row().cells
        row_pr = table.rows[-1]._tr.get_or_add_trPr()
        cant_split = OxmlElement("w:cantSplit")
        row_pr.append(cant_split)
        for index in range(len(header)):
            value = row[index] if index < len(row) else ""
            cells[index].width = Cm(widths[index])
            cells[index].text = ""
            paragraph = cells[index].paragraphs[0]
            add_inline(paragraph, value)
            for run in paragraph.runs:
                run.font.size = Pt(8.5)
    doc.add_paragraph()


def add_code(doc: Document, lines: list[str]) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.left_indent = Cm(0.5)
    paragraph.paragraph_format.space_before = Pt(4)
    paragraph.paragraph_format.space_after = Pt(6)
    set_paragraph_shading(paragraph, "F4F6F4")
    for index, line in enumerate(lines):
        run = paragraph.add_run(line)
        run.font.name = "Consolas"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
        run.font.size = Pt(9)
        if index < len(lines) - 1:
            run.add_break()


def build(markdown: Path, output: Path, *, title_text: str = TITLE, subtitle_text: str = SUBTITLE,
          header_text: str = "知芽 P6 软件测试文档") -> None:
    text = markdown.read_text(encoding="utf-8")
    lines = text.splitlines()
    # 去掉文档开头重复的标题，封面单独排版
    while lines and (not lines[0].strip() or lines[0].startswith("# ")):
        lines.pop(0)
    doc = Document()
    style_document(doc, header_text)
    match = re.search(r"(?m)^- 被测提交：`([^`\n]+)`", text)
    commit = match.group(1) if match else "未记录"
    date = re.search(r"(?m)^\| (?:本轮测试日期|编写日期) \| ([^|\n]+?) \|", text)
    if date is None:
        date = re.search(r"(?m)^- 版本：[^\n]*?（(\d{4}-\d{2}-\d{2})）", text)
    add_cover(doc, title_text, subtitle_text,
              [*META, ("日期", date.group(1) if date else "未记录"), ("被测提交", commit)])

    index = 0
    first_h1 = True
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            index += 1
            continue
        if stripped.startswith("```"):
            code_lines = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            index += 1
            add_code(doc, code_lines)
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and set(lines[index + 1].strip()) <= set("|-: "):
            table_lines = [stripped]
            index += 1
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            add_table(doc, table_lines)
            continue
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            paragraph = doc.add_heading(level=1)
            if not first_h1:
                paragraph.paragraph_format.page_break_before = True
            first_h1 = False
            add_inline(paragraph, title)
            index += 1
            continue
        if stripped.startswith("## "):
            paragraph = doc.add_heading(level=2)
            add_inline(paragraph, stripped[3:].strip())
            index += 1
            continue
        if stripped.startswith("### "):
            paragraph = doc.add_heading(level=3)
            add_inline(paragraph, stripped[4:].strip())
            index += 1
            continue
        if stripped.startswith(">"):
            content = stripped.lstrip(">").strip()
            if content:
                paragraph = doc.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(0.8)
                add_inline(paragraph, content)
                for run in paragraph.runs:
                    run.italic = True
                    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
            index += 1
            continue
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            paragraph = doc.add_paragraph(style="List Bullet")
            add_inline(paragraph, ("☑ " if "[x]" in stripped[:5] else "☐ ") + stripped[5:].strip())
            index += 1
            continue
        if stripped.startswith("- "):
            paragraph = doc.add_paragraph(style="List Bullet")
            add_inline(paragraph, stripped[2:].strip())
            index += 1
            continue
        if re.match(r"^\d+\.\s", stripped):
            paragraph = doc.add_paragraph(style="List Number")
            add_inline(paragraph, re.sub(r"^\d+\.\s", "", stripped))
            index += 1
            continue
        if stripped == "---":
            index += 1
            continue
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.first_line_indent = Cm(0)
        add_inline(paragraph, stripped)
        index += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--title")
    parser.add_argument("--subtitle")
    parser.add_argument("--header")
    args = parser.parse_args()
    build(args.markdown, args.output,
          title_text=args.title or TITLE,
          subtitle_text=args.subtitle or SUBTITLE,
          header_text=args.header or "知芽 P6 软件测试文档")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
