from __future__ import annotations

from pathlib import Path
import zipfile

from app.analysis import analyze_book, translation_plan
from app.book_io import export_epub, export_txt, inspect_epub, load_epub_book, load_txt_book
from app.config import AppConfig, AutomationConfig, ContextConfig, EpubConfig, LlmConfig, QualityConfig, ReviewConfig, TranslationConfig
from app.context import context_for_batch, context_status, summarize_context
from app.delivery import package_delivery
from app.feedback import verify_feedback_text
from app.manual import export_pending_translations, import_manual_translations, reset_translations
from app.memory import export_memory, import_memory, load_memory, lookup_memory, remember_translation, terminology_hash
from app.models import Paragraph, load_book, save_book
from app.placeholders import extract_placeholders, placeholder_mismatches
from app.review import apply_review_fixes, review_translations
from app.runs import failed_batches, latest_failed_paragraph_ids, record_batch, run_report
from app.snapshots import create_snapshot, list_snapshots, restore_snapshot
from app.terminology import Term, extract_term_candidates
from app.translator import validate_llm_response
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
    assert book.paragraphs[0].metadata["epub"]["chapter_path"] == "OEBPS/chapter1.xhtml"
    assert book.paragraphs[0].metadata["epub"]["node_index"] == 0


def test_inspect_epub_reports_nav_toc_and_risks(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(
        epub,
        chapter_name="chapter1.html",
        chapter_body='<body><nav><ol><li><a href="chapter1.html">第一章</a></li></ol></nav><p><ruby>漢<rt>かん</rt></ruby><a href="#n1">注</a></p></body>',
        extra_manifest='<item id="nav" href="chapter1.html" media-type="application/xhtml+xml" properties="nav"/><item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        spine_attrs=' toc="toc"',
    )

    report = inspect_epub(epub)

    assert report["summary"]["has_nav"] is True
    assert report["summary"]["has_toc"] is True
    assert report["summary"]["html_file_count"] >= 1
    assert report["summary"]["ruby_count"] == 1
    assert report["summary"]["link_count"] >= 1


def test_epub_export_uses_node_locator_for_duplicate_text(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, chapter_body="<body><p>Repeat.</p><p>Repeat.</p></body>")
    book = load_epub_book(epub)
    book.paragraphs[0].translated = "第一个。"
    book.paragraphs[1].translated = "第二个。"
    book.source_file = str(epub)

    output = tmp_path / "translated.epub"
    result = export_epub(book, output)
    with zipfile.ZipFile(output) as archive:
        xhtml = archive.read("OEBPS/chapter1.xhtml").decode("utf-8")

    assert result["warning_count"] == 0
    assert "第一个。" in xhtml
    assert "第二个。" in xhtml
    assert xhtml.index("第一个。") < xhtml.index("第二个。")


def test_epub_export_preserves_outer_attributes(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, chapter_body='<body><p id="p1" class="lead">Hello.</p></body>')
    book = load_epub_book(epub)
    book.paragraphs[0].translated = "你好。"
    book.source_file = str(epub)

    output = tmp_path / "translated.epub"
    export_epub(book, output)
    with zipfile.ZipFile(output) as archive:
        xhtml = archive.read("OEBPS/chapter1.xhtml").decode("utf-8")

    assert 'id="p1"' in xhtml
    assert 'class="lead"' in xhtml
    assert "你好。" in xhtml


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


def test_translation_memory_respects_terminology_hash(tmp_path: Path) -> None:
    books_dir = tmp_path / "books"
    entries = []
    terms = [Term(source="Alice", target="爱丽丝")]
    term_hash = terminology_hash(terms)
    remember_translation(entries, source="Alice opened the door.", translated="爱丽丝推开门。", term_hash=term_hash, model="test-model")

    assert lookup_memory(entries, "Alice opened the door.", term_hash).translated == "爱丽丝推开门。"
    assert lookup_memory(entries, "Alice opened the door.", terminology_hash([Term(source="Alice", target="艾丽丝")])) is None

    from app.memory import save_memory

    save_memory(books_dir, "book", entries)
    exported = tmp_path / "memory.json"
    export_memory(books_dir, "book", exported)
    imported_books = tmp_path / "imported"
    report = import_memory(imported_books, "book", exported)

    assert report["summary"]["imported"] == 1
    assert len(load_memory(imported_books, "book")) == 1


def test_context_summary_and_batch_payload(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.\n\nTwo.\n\nThree.\n\nFour.", encoding="utf-8")
    book = load_txt_book(source, title="Context")
    book.paragraphs[0].translated = "一。"
    config = ContextConfig(previous_paragraphs=1, next_paragraphs=1, chapter_summary_max_chars=8)

    report = summarize_context(tmp_path / "books", book, config)
    status = context_status(tmp_path / "books", book)
    context = context_for_batch(book, [book.paragraphs[1]], report_context(tmp_path / "books", book.id), config)

    assert report["summary"]["chapters"] == 1
    assert status["status"] == "ok"
    assert context["chapter_title"] == "Context"
    assert context["previous"][0]["translated"] == "一。"
    assert context["next"][0]["source"] == "Three."
    assert context["chapter_summary"]


def test_validate_llm_response_detects_batch_problems() -> None:
    paragraphs = [
        Paragraph(id="p1", chapter_id="c1", index=1, source="One."),
        Paragraph(id="p2", chapter_id="c1", index=2, source="Two."),
    ]

    missing = validate_llm_response(paragraphs, {"p1": "一。"})
    warnings = validate_llm_response(paragraphs, {"p1": "", "p2": "二。", "extra": "x"})

    assert missing["errors"]
    assert any("空译文" in item for item in warnings["warnings"])
    assert any("未知段落 ID" in item for item in warnings["warnings"])


def test_run_report_and_failed_ids(tmp_path: Path) -> None:
    books_dir = tmp_path / "books"
    record_batch(
        books_dir,
        "book",
        "run-1",
        {
            "batch_id": "run-1-b0001",
            "status": "failed",
            "paragraph_ids": ["p1", "p2"],
            "model": "test-model",
            "duration_seconds": 0.1,
            "retry_count": 0,
            "error": "missing p2",
            "warnings": [],
        },
    )

    report = run_report(books_dir, "book")
    failed = failed_batches(books_dir, "book")

    assert report["summary"]["failed"] == 1
    assert failed["summary"]["failed"] == 1
    assert latest_failed_paragraph_ids(books_dir, "book") == ["p1", "p2"]


def test_analysis_and_translation_plan_report_risks(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text('"Alice opened the door."\n\n"Alice opened the door."\n\nBob smiled.', encoding="utf-8")
    book = load_txt_book(source, title="Analysis")
    books_dir = tmp_path / "books"
    save_book(books_dir, book, source)

    analysis = analyze_book(books_dir, book, [])
    plan = translation_plan(books_dir, book, [], _quality_config())

    assert analysis["summary"]["duplicate_group_count"] == 1
    assert analysis["details"]["dialogue_ratio"] > 0
    assert plan["summary"]["needs_terminology"] is True


def test_review_translations_and_apply_fixes(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text('Hello {name}.\n\n"Hi," Alice said.', encoding="utf-8")
    book = load_txt_book(source, title="Review")
    books_dir = tmp_path / "books"
    save_book(books_dir, book, source)
    book.paragraphs[0].translated = "你好。"
    book.paragraphs[1].translated = '"你好," Alice said.'
    config = _app_config(tmp_path)

    review = review_translations(config, book, [], "risk")
    review_file = Path(review["summary"]["output"])
    raw = review_file.read_text(encoding="utf-8")
    raw = raw.replace('"approved_translation": ""', '"approved_translation": "你好{name}。"')
    review_file.write_text(raw, encoding="utf-8")
    applied = apply_review_fixes(books_dir, book, review_file)

    assert review["summary"]["items"] >= 1
    assert applied["summary"]["applied"] >= 1
    assert book.paragraphs[0].translated == "你好{name}。"


def test_snapshot_restore_and_delivery_package(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.\n\nTwo.", encoding="utf-8")
    book = load_txt_book(source, title="Delivery")
    books_dir = tmp_path / "books"
    save_book(books_dir, book, source)
    book.paragraphs[0].translated = "一。"
    from app.models import persist_book

    persist_book(books_dir, book)
    snapshot = create_snapshot(books_dir, book, "before")
    book.paragraphs[0].translated = "坏译文"
    persist_book(books_dir, book)
    restore = restore_snapshot(books_dir, book.id, snapshot["summary"]["snapshot_id"])
    packaged_book = load_book(books_dir, book.id)
    package = package_delivery(books_dir, packaged_book, [], _quality_config(), EpubConfig(), tmp_path / "delivery")

    assert list_snapshots(books_dir, book.id)["summary"]["count"] == 1
    assert restore["summary"]["snapshot"] == snapshot["summary"]["snapshot_id"]
    assert (tmp_path / "delivery" / "delivery-manifest.json").exists()
    assert package["summary"]["output_dir"].endswith("delivery")


def report_context(books_dir: Path, book_id: str) -> dict:
    from app.context import load_context

    return load_context(books_dir, book_id)


def _quality_config():
    from app.config import QualityConfig

    return QualityConfig(source_residual_patterns=())


def _app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        root=tmp_path,
        llm=LlmConfig(base_url="", api_key="", model="test-model"),
        translation=TranslationConfig(system_prompt_file="prompt.md"),
        context=ContextConfig(),
        review=ReviewConfig(),
        automation=AutomationConfig(),
        quality=QualityConfig(source_residual_patterns=()),
        epub=EpubConfig(),
    )


def _write_epub(
    path: Path,
    *,
    chapter_name: str = "chapter1.xhtml",
    chapter_body: str,
    extra_manifest: str = "",
    spine_attrs: str = "",
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
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
            f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title></metadata>
  <manifest><item id="c1" href="{chapter_name}" media-type="application/xhtml+xml"/>{extra_manifest}</manifest>
  <spine{spine_attrs}><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            f"OEBPS/{chapter_name}",
            f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml">{chapter_body}</html>""",
        )
        if "toc.ncx" in extra_manifest:
            archive.writestr("OEBPS/toc.ncx", "<ncx><navMap></navMap></ncx>")
