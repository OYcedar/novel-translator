from __future__ import annotations

from pathlib import Path
import zipfile

from app.book_io import export_txt, load_epub_book, load_txt_book


def test_load_txt_book_splits_blank_line_paragraphs(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("第一段。\n\n第二段。\n第三行。", encoding="utf-8")

    book = load_txt_book(source)

    assert book.source_type == "txt"
    assert book.title == "novel"
    assert len(book.chapters) == 1
    assert [item.source for item in book.paragraphs] == ["第一段。", "第二段。\n第三行。"]


def test_export_txt_uses_translation_when_available(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Hello.\n\nWorld.", encoding="utf-8")
    book = load_txt_book(source)
    book.paragraphs[0].translated = "你好。"

    output = tmp_path / "out.txt"
    export_txt(book, output)

    text = output.read_text(encoding="utf-8")
    assert "你好。" in text
    assert "World." in text


def test_load_epub_book_reads_spine_xhtml(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title></metadata>
  <manifest><item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第一章</title></head>
<body><h1>第一章</h1><p>她推开门。</p><p>风从走廊尽头吹来。</p></body></html>""",
        )

    book = load_epub_book(epub)

    assert book.title == "测试书"
    assert book.source_type == "epub"
    assert len(book.chapters) == 1
    assert [item.source for item in book.paragraphs] == ["第一章", "她推开门。", "风从走廊尽头吹来。"]

