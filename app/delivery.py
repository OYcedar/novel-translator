from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import shutil

from app.book_io import export_epub, export_txt
from app.memory import memory_status
from app.models import Book
from app.quality import quality_report
from app.runs import export_run_report, run_report
from app.terminology import Term
from app.translator import pending_paragraphs


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


def delivery_check_report(root_books_dir: Path, book: Book, terms: list[Term], quality_config, export_format: str) -> dict:
    quality = quality_report(book, quality_config, terms)
    pending_ids = {paragraph.id for paragraph in pending_paragraphs(book.paragraphs)}
    runs = run_report(root_books_dir, book.id, pending_ids)
    pending = len(pending_ids)
    errors = []
    warnings = []
    if pending:
        errors.append({"code": "pending_translations", "message": f"还有 {pending} 个段落未翻译"})
    if runs["summary"].get("failed", 0):
        errors.append({"code": "failed_batches", "message": f"仍有 {runs['summary']['failed']} 个失败批次未恢复"})
    if quality["summary"].get("placeholder_mismatch", 0):
        errors.append(
            {
                "code": "placeholder_mismatch",
                "message": f"存在 {quality['summary']['placeholder_mismatch']} 个占位符缺失问题",
            }
        )
    if export_format == "epub" and book.source_type != "epub":
        errors.append({"code": "export_format_invalid", "message": "TXT 注册书籍不能导出 EPUB"})
    if quality["status"] != "ok":
        warnings.append("quality-report 仍有 warning，交付前需要修复或在交付说明中解释。")
    if export_format == "epub" and quality["summary"].get("epub_markup_risk", 0):
        warnings.append(f"存在 {quality['summary']['epub_markup_risk']} 个 EPUB 标记风险段落，导出后需要人工复核")
    if runs["status"] == "warning" and not runs["summary"].get("failed", 0):
        warnings.extend(runs.get("warnings", []))
    status = "error" if errors else ("warning" if warnings else "ok")
    steps = [
        {
            "step": "translation-status",
            "status": "ok",
            "summary": {
                "book": book.id,
                "total": len(book.paragraphs),
                "translated": len(book.paragraphs) - pending,
                "pending": pending,
                "progress": round((len(book.paragraphs) - pending) / len(book.paragraphs), 4) if book.paragraphs else 1,
            },
        },
        {"step": "run-report", "status": runs["status"], "summary": runs["summary"]},
        {"step": "quality-report", "status": quality["status"], "summary": quality["summary"]},
        {
            "step": "validate-export",
            "status": "error" if any(item["code"] in {"pending_translations", "export_format_invalid"} for item in errors) else ("warning" if warnings or quality["status"] != "ok" else "ok"),
            "summary": {
                "book": book.id,
                "format": export_format,
                "quality_status": quality["status"],
                "pending": pending,
                "epub_markup_risk": quality["summary"].get("epub_markup_risk", 0),
            },
        },
    ]
    return {
        "status": status,
        "warnings": warnings,
        "errors": errors,
        "summary": {
            "book": book.id,
            "format": export_format,
            "ready": status == "ok",
            "pending": pending,
            "failed_batches": runs["summary"].get("failed", 0),
            "placeholder_mismatch": quality["summary"].get("placeholder_mismatch", 0),
            "quality_status": quality["status"],
            "export_status": steps[-1]["status"],
        },
        "details": {"steps": steps, "blockers": errors, "quality": quality},
    }


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
    delivery_check = delivery_check_report(root_books_dir, book, terms, quality_config, selected_format)
    delivery_check_path = reports_dir / "delivery-check.json"
    delivery_check_path.write_text(json.dumps(delivery_check, ensure_ascii=False, indent=2), encoding="utf-8")
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
    combined_warnings = warnings + delivery_check.get("warnings", [])
    errors = delivery_check.get("errors", [])
    status = "error" if errors else ("warning" if combined_warnings else "ok")
    manifest = {
        "book": book.id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "ready": delivery_check["summary"].get("ready", False),
        "translated": str(translated_path),
        "quality_report": str(quality_path),
        "delivery_check": str(delivery_check_path),
        "run_report": str(run_report_path),
        "epub_risk_report": epub_risk_path,
        "terms": str(terms_path),
        "memory_summary": str(memory_path),
        "bilingual": bilingual,
        "format": selected_format,
        "warnings": combined_warnings,
        "errors": errors,
        "delivery_check_summary": delivery_check.get("summary", {}),
    }
    manifest_path = output_dir / "delivery-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": status,
        "warnings": combined_warnings,
        "errors": errors,
        "summary": {
            "book": book.id,
            "output_dir": str(output_dir),
            "translated": str(translated_path),
            "manifest": str(manifest_path),
            "bilingual": bilingual,
            "format": selected_format,
            "ready": delivery_check["summary"].get("ready", False),
        },
        "details": manifest,
    }
