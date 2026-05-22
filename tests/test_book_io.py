from __future__ import annotations

from pathlib import Path
import zipfile

from app.book_io import export_txt, load_epub_book, load_txt_book
from app.feedback import verify_feedback_text
from app.manual import export_pending_translations, import_manual_translations, reset_translations
from app.models import Paragraph, save_book
from app.placeholders import extract_placeholders, placeholder_mismatches
from app.terminology import extract_term_candidates
from app.workspace import prepare_agent_workspace, validate_agent_workspace


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


def test_extract_term_candidates_counts_repeated_names(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Alice opened the door.\n\nAlice saw Bob.\n\nBob smiled at Alice.", encoding="utf-8")
    book = load_txt_book(source)

    terms = extract_term_candidates(book)

    by_source = {term.source: term for term in terms}
    assert by_source["Alice"].occurrences == 3
    assert by_source["Bob"].occurrences == 2


def test_workspace_export_and_validate(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Alice opened the door.\n\nBob smiled.", encoding="utf-8")
    book = load_txt_book(source, title="Workspace")
    books_dir = tmp_path / "books"
    save_book(books_dir, book, source)

    workspace = tmp_path / "workspace"
    report = prepare_agent_workspace(books_dir, book, [], _quality_config(), workspace)
    validation = validate_agent_workspace(book, workspace)

    assert report["status"] == "ok"
    assert (workspace / "manifest.json").exists()
    assert (workspace / "book-summary.json").exists()
    assert (workspace / "text-scope.json").exists()
    assert validation["status"] == "ok"


def test_workspace_validate_reports_missing_file(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Alice opened the door.", encoding="utf-8")
    book = load_txt_book(source, title="Workspace")
    workspace = tmp_path / "workspace"
    prepare_agent_workspace(tmp_path / "books", book, [], _quality_config(), workspace)
    (workspace / "book-summary.json").unlink()

    validation = validate_agent_workspace(book, workspace)

    assert validation["status"] == "error"
    assert validation["errors"][0]["code"] == "workspace_file_missing"


def test_pending_manual_import_and_reset(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.\n\nTwo.", encoding="utf-8")
    book = load_txt_book(source, title="Manual")
    books_dir = tmp_path / "books"
    save_book(books_dir, book, source)
    book.paragraphs[0].translated = "一。"

    pending_path = tmp_path / "pending.json"
    pending = export_pending_translations(book, [], pending_path)
    manual_path = tmp_path / "manual.json"
    manual_path.write_text(
        '{"items":[{"id":"c0001-p00002","translated":"二。"}]}',
        encoding="utf-8",
    )
    imported = import_manual_translations(books_dir, book, manual_path)
    reset_path = tmp_path / "reset.json"
    reset_path.write_text('["c0001-p00001"]', encoding="utf-8")
    reset = reset_translations(books_dir, book, input_path=reset_path)

    assert pending["summary"]["count"] == 1
    assert imported["summary"]["imported"] == 1
    assert book.paragraphs[1].translated == "二。"
    assert reset["summary"]["reset"] == 1
    assert book.paragraphs[0].translated == ""


def test_manual_import_rejects_unknown_id(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.", encoding="utf-8")
    book = load_txt_book(source, title="Manual")
    path = tmp_path / "manual.json"
    path.write_text('{"items":[{"id":"missing","translated":"x"}]}', encoding="utf-8")

    imported = import_manual_translations(tmp_path / "books", book, path)

    assert imported["status"] == "error"
    assert imported["errors"][0]["code"] == "manual_translation_invalid"


def test_feedback_matches_source_translation_and_not_found(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Alice opened the door.\n\nBob smiled.", encoding="utf-8")
    book = load_txt_book(source, title="Feedback")
    book.paragraphs[0].translated = "爱丽丝推开门。"
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("Alice opened\n爱丽丝推开\nmissing text\n", encoding="utf-8")

    report = verify_feedback_text(book, feedback)

    assert report["summary"]["matched_source"] == 1
    assert report["summary"]["matched_translation"] == 1
    assert report["summary"]["not_found"] == 1


def test_placeholder_detection_and_mismatch() -> None:
    source = "Hello {name}, score=%d <ruby>word</ruby> [#note-1]"
    placeholders = extract_placeholders(source)
    paragraph = Paragraph(
        id="p1",
        chapter_id="c1",
        index=1,
        source=source,
        translated="你好{name}，分数是%d word",
    )

    missing = placeholder_mismatches(paragraph)

    assert "{name}" in [item.value for item in placeholders]
    assert "%d" in [item.value for item in placeholders]
    assert "<ruby>" in [item.value for item in placeholders]
    assert {"</ruby>", "[#note-1]"}.issubset({item["placeholder"] for item in missing})


def _quality_config():
    from app.config import QualityConfig

    return QualityConfig(source_residual_patterns=())
