from __future__ import annotations

from pathlib import Path
import json
import shutil

from app.book_io import export_epub, export_txt
from app.memory import memory_status
from app.models import Book
from app.quality import quality_report
from app.runs import export_run_report, run_report
from app.terminology import Term


def export_epub_risk_report(book: Book, output: Path) -> dict:
    items = []
    for paragraph in book.paragraphs:
        epub = paragraph.metadata.get("epub", {})
        risks = epub.get("risks", [])
        if risks:
            items.append(
                {
                    "id": paragraph.id,
                    "chapter_path": epub.get("chapter_path", ""),
                    "risks": risks,
                    "source": paragraph.source,
                    "translated": paragraph.translated,
                }
            )
    lines = [f"# EPUB Risk Report: {book.id}", "", f"- Risk paragraphs: {len(items)}", ""]
    for item in items:
        lines.extend(
            [
                f"## {item['id']}",
                "",
                f"- Chapter path: `{item['chapter_path']}`",
                f"- Risks: {', '.join(item['risks'])}",
                "",
                item["source"],
                "",
            ]
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "warnings": [], "summary": {"book": book.id, "output": str(output), "risks": len(items)}, "details": {"items": items}}


def package_delivery(root_books_dir: Path, book: Book, terms: list[Term], quality_config, epub_config, output_dir: Path, *, bilingual: bool = False, export_format: str | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    translated_dir = output_dir / "translated"
    reports_dir = output_dir / "reports"
    terminology_dir = output_dir / "terminology"
    metadata_dir = output_dir / "metadata"
    for directory in (translated_dir, reports_dir, terminology_dir, metadata_dir):
        directory.mkdir(parents=True, exist_ok=True)

    selected_format = export_format or book.source_type
    if selected_format == "epub":
        if book.source_type != "epub":
            raise ValueError("TXT 书籍不能导出 EPUB")
        translated_path = translated_dir / f"{book.id}.epub"
        export_result = export_epub(book, translated_path, epub_config, bilingual=bilingual)
        warnings = list(export_result.get("warnings", []))
    elif selected_format == "txt":
        translated_path = translated_dir / f"{book.id}.txt"
        export_txt(book, translated_path, bilingual=bilingual)
        warnings = []
    else:
        raise ValueError(f"不支持导出格式：{selected_format}")

    quality = quality_report(book, quality_config, terms)
    quality_path = reports_dir / "quality-report.json"
    quality_path.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    run_report_path = reports_dir / "run-report.md"
    export_run_report(root_books_dir, book.id, run_report_path)
    epub_risk_path = ""
    if book.source_type == "epub":
        epub_risk = export_epub_risk_report(book, reports_dir / "epub-risk-report.md")
        epub_risk_path = epub_risk["summary"]["output"]

    terms_path = terminology_dir / "terms.json"
    terms_path.write_text(json.dumps({"terms": [term.__dict__ for term in terms]}, ensure_ascii=False, indent=2), encoding="utf-8")
    memory = memory_status(root_books_dir, book.id, terms)
    memory_path = metadata_dir / "memory-summary.json"
    memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "book": book.id,
        "translated": str(translated_path),
        "quality_report": str(quality_path),
        "run_report": str(run_report_path),
        "epub_risk_report": epub_risk_path,
        "terms": str(terms_path),
        "memory_summary": str(memory_path),
        "bilingual": bilingual,
        "format": selected_format,
        "warnings": warnings,
    }
    manifest_path = output_dir / "delivery-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "warning" if warnings or quality["status"] != "ok" else "ok",
        "warnings": warnings,
        "summary": {"book": book.id, "output_dir": str(output_dir), "translated": str(translated_path), "manifest": str(manifest_path), "bilingual": bilingual},
        "details": manifest,
    }
