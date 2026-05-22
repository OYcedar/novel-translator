from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.book_io import export_epub, export_txt, inspect_epub, load_source_book
from app.config import AppConfig, EpubConfig, load_config
from app.feedback import verify_feedback_text
from app.manual import (
    audit_coverage_report,
    export_pending_translations,
    export_quality_fix,
    import_manual_translations,
    reset_translations,
)
from app.models import load_book, persist_book, save_book, slugify
from app.quality import quality_report
from app.terminology import (
    export_terminology_workspace,
    load_terms,
    save_terms,
    term_from_dict,
    validate_terms,
)
from app.translator import make_batches, pending_paragraphs, translate_batch
from app.workspace import prepare_agent_workspace, validate_agent_workspace


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = dispatch(args)
        emit(result, json_output=getattr(args, "json_output", False))
        return 0 if result.get("status") != "error" else 1
    except Exception as error:
        payload = {
            "status": "error",
            "errors": [{"code": type(error).__name__, "message": str(error)}],
            "warnings": [],
            "summary": {},
            "details": {},
        }
        emit(payload, json_output=getattr(args, "json_output", False))
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel-translator")
    parser.add_argument("--agent-mode", action="store_true", help="保留给 Agent 工作流使用")
    parser.add_argument("--config", type=Path, help="配置文件路径，默认 setting.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_common(subparsers.add_parser("doctor", help="检查配置和目录"), json_flag=True)

    inspect_epub_parser = add_common(subparsers.add_parser("inspect-epub", help="检查 EPUB 内部结构"), json_flag=True)
    inspect_epub_parser.add_argument("--path", type=Path, required=True)

    add_book = add_common(subparsers.add_parser("add-book", help="注册 EPUB/TXT 小说"), json_flag=True)
    add_book.add_argument("--path", type=Path, required=True)
    add_book.add_argument("--title")
    add_book.add_argument("--id")

    list_cmd = add_common(subparsers.add_parser("list", help="列出已注册小说"), json_flag=True)
    list_cmd.set_defaults(_unused=True)

    scope = add_common(subparsers.add_parser("text-scope", help="查看段落范围"), json_flag=True)
    scope.add_argument("--book", required=True)

    translate = add_common(subparsers.add_parser("translate", help="翻译未完成段落"), json_flag=True)
    translate.add_argument("--book", required=True)
    translate.add_argument("--max-batches", type=int)
    translate.add_argument("--dry-run", action="store_true", help="不调用模型，直接把原文写入译文字段")

    status = add_common(subparsers.add_parser("translation-status", help="查看翻译进度"), json_flag=True)
    status.add_argument("--book", required=True)

    quality = add_common(subparsers.add_parser("quality-report", help="检查未译和源语言残留"), json_flag=True)
    quality.add_argument("--book", required=True)

    export_terms = add_common(
        subparsers.add_parser("export-terminology", help="导出术语候选和上下文，供 Agent 或人工填写"),
        json_flag=True,
    )
    export_terms.add_argument("--book", required=True)
    export_terms.add_argument("--output-dir", type=Path, required=True)

    import_terms = add_common(
        subparsers.add_parser("import-terminology", help="导入审查后的正文术语表"),
        json_flag=True,
    )
    import_terms.add_argument("--book", required=True)
    import_terms.add_argument("--input", type=Path, required=True)

    terminology_status = add_common(
        subparsers.add_parser("terminology-status", help="查看当前书籍术语表状态"),
        json_flag=True,
    )
    terminology_status.add_argument("--book", required=True)

    prepare_workspace = add_common(
        subparsers.add_parser("prepare-agent-workspace", help="导出 Agent 分析工作区"),
        json_flag=True,
    )
    prepare_workspace.add_argument("--book", required=True)
    prepare_workspace.add_argument("--output-dir", type=Path, required=True)

    validate_workspace = add_common(
        subparsers.add_parser("validate-agent-workspace", help="校验 Agent 工作区"),
        json_flag=True,
    )
    validate_workspace.add_argument("--book", required=True)
    validate_workspace.add_argument("--workspace", type=Path, required=True)

    audit = add_common(subparsers.add_parser("audit-coverage", help="审计翻译覆盖范围"), json_flag=True)
    audit.add_argument("--book", required=True)

    pending_export = add_common(
        subparsers.add_parser("export-pending-translations", help="导出未译段落"),
        json_flag=True,
    )
    pending_export.add_argument("--book", required=True)
    pending_export.add_argument("--output", type=Path, required=True)

    quality_fix = add_common(
        subparsers.add_parser("export-quality-fix", help="导出质量修复表"),
        json_flag=True,
    )
    quality_fix.add_argument("--book", required=True)
    quality_fix.add_argument("--output", type=Path, required=True)

    manual_import = add_common(
        subparsers.add_parser("import-manual-translations", help="导入人工填写译文"),
        json_flag=True,
    )
    manual_import.add_argument("--book", required=True)
    manual_import.add_argument("--input", type=Path, required=True)

    reset = add_common(subparsers.add_parser("reset-translations", help="精确重置坏译文"), json_flag=True)
    reset.add_argument("--book", required=True)
    reset.add_argument("--input", type=Path)
    reset.add_argument("--all", action="store_true", dest="reset_all")

    feedback = add_common(subparsers.add_parser("verify-feedback-text", help="按反馈文本反查段落"), json_flag=True)
    feedback.add_argument("--book", required=True)
    feedback.add_argument("--input", type=Path, required=True)

    export = add_common(subparsers.add_parser("export", help="导出译文"), json_flag=True)
    export.add_argument("--book", required=True)
    export.add_argument("--format", choices=["txt", "epub"], required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--bilingual", action="store_true")

    validate_export_parser = add_common(subparsers.add_parser("validate-export", help="导出前检查"), json_flag=True)
    validate_export_parser.add_argument("--book", required=True)
    validate_export_parser.add_argument("--format", choices=["txt", "epub"], required=True)
    return parser


def add_common(parser: argparse.ArgumentParser, *, json_flag: bool) -> argparse.ArgumentParser:
    if json_flag:
        parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def dispatch(args: argparse.Namespace) -> dict:
    if args.command == "doctor":
        return doctor(args)
    if args.command == "inspect-epub":
        try:
            epub_config = load_config(ROOT, args.config).epub
        except FileNotFoundError:
            epub_config = EpubConfig()
        return inspect_epub(args.path.expanduser().resolve(), epub_config)
    config = load_config(ROOT, args.config)
    config.books_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "add-book":
        return add_book(config, args)
    if args.command == "list":
        return list_books(config)
    if args.command == "text-scope":
        return text_scope(config, args.book)
    if args.command == "translation-status":
        return translation_status(config, args.book)
    if args.command == "translate":
        return translate(config, args)
    if args.command == "quality-report":
        book = load_book(config.books_dir, args.book)
        return quality_report(book, config.quality, load_terms(config.books_dir, book.id))
    if args.command == "export-terminology":
        return export_terminology(config, args)
    if args.command == "import-terminology":
        return import_terminology(config, args)
    if args.command == "terminology-status":
        return terminology_status(config, args.book)
    if args.command == "prepare-agent-workspace":
        book = load_book(config.books_dir, args.book)
        return prepare_agent_workspace(
            config.books_dir,
            book,
            load_terms(config.books_dir, book.id),
            config.quality,
            args.output_dir.expanduser().resolve(),
        )
    if args.command == "validate-agent-workspace":
        book = load_book(config.books_dir, args.book)
        return validate_agent_workspace(book, args.workspace.expanduser().resolve())
    if args.command == "audit-coverage":
        book = load_book(config.books_dir, args.book)
        return audit_coverage_report(book, load_terms(config.books_dir, book.id))
    if args.command == "export-pending-translations":
        book = load_book(config.books_dir, args.book)
        return export_pending_translations(book, load_terms(config.books_dir, book.id), args.output.expanduser().resolve())
    if args.command == "export-quality-fix":
        book = load_book(config.books_dir, args.book)
        return export_quality_fix(book, load_terms(config.books_dir, book.id), config.quality, args.output.expanduser().resolve())
    if args.command == "import-manual-translations":
        book = load_book(config.books_dir, args.book)
        return import_manual_translations(config.books_dir, book, args.input.expanduser().resolve())
    if args.command == "reset-translations":
        book = load_book(config.books_dir, args.book)
        return reset_translations(
            config.books_dir,
            book,
            input_path=args.input.expanduser().resolve() if args.input else None,
            reset_all=args.reset_all,
        )
    if args.command == "verify-feedback-text":
        book = load_book(config.books_dir, args.book)
        return verify_feedback_text(book, args.input.expanduser().resolve())
    if args.command == "export":
        return export_book(config, args)
    if args.command == "validate-export":
        return validate_export(config, args)
    raise ValueError(f"未知命令：{args.command}")


def doctor(args: argparse.Namespace) -> dict:
    config_exists = (args.config or ROOT / "setting.toml").exists()
    example_exists = (ROOT / "setting.example.toml").exists()
    return {
        "status": "ok" if config_exists else "warning",
        "warnings": [] if config_exists else ["setting.toml 不存在，复制 setting.example.toml 后填写模型配置。"],
        "summary": {
            "root": str(ROOT),
            "config_exists": config_exists,
            "example_exists": example_exists,
            "python": sys.version.split()[0],
        },
        "details": {},
    }


def add_book(config: AppConfig, args: argparse.Namespace) -> dict:
    source_path = args.path.expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"文件不存在：{source_path}")
    book = load_source_book(source_path, title=args.title, epub_config=config.epub)
    if args.id:
        book.id = slugify(args.id)
    target_dir = save_book(config.books_dir, book, source_path)
    summary = {
        "book": book.id,
        "title": book.title,
        "type": book.source_type,
        "chapters": len(book.chapters),
        "paragraphs": len(book.paragraphs),
        "data_dir": str(target_dir),
    }
    warnings = []
    if book.source_type == "epub":
        epub_meta = book.metadata.get("epub", {})
        summary.update(
            {
                "parser_mode": epub_meta.get("parser_mode", ""),
                "nav_path": epub_meta.get("nav_path", ""),
                "toc_path": epub_meta.get("toc_path", ""),
                "warning_count": epub_meta.get("warning_count", 0),
            }
        )
        warnings = list(epub_meta.get("warnings", []))
    return {
        "status": "warning" if warnings else "ok",
        "warnings": warnings,
        "summary": summary,
        "details": {},
    }


def list_books(config: AppConfig) -> dict:
    books = []
    for manifest in sorted(config.books_dir.glob("*/manifest.json")):
        book = load_book(config.books_dir, manifest.parent.name)
        books.append(
            {
                "id": book.id,
                "title": book.title,
                "type": book.source_type,
                "chapters": len(book.chapters),
                "paragraphs": len(book.paragraphs),
            }
        )
    return {"status": "ok", "warnings": [], "summary": {"count": len(books)}, "details": {"books": books}}


def text_scope(config: AppConfig, book_id: str) -> dict:
    book = load_book(config.books_dir, book_id)
    chapters = [
        {"id": chapter.id, "title": chapter.title, "paragraphs": len(chapter.paragraphs)}
        for chapter in book.chapters
    ]
    return {
        "status": "ok",
        "warnings": [],
        "summary": {"book": book.id, "chapters": len(book.chapters), "paragraphs": len(book.paragraphs)},
        "details": {"chapters": chapters},
    }


def translation_status(config: AppConfig, book_id: str) -> dict:
    book = load_book(config.books_dir, book_id)
    translated = sum(1 for paragraph in book.paragraphs if paragraph.translated.strip())
    total = len(book.paragraphs)
    return {
        "status": "ok",
        "warnings": [],
        "summary": {
            "book": book.id,
            "total": total,
            "translated": translated,
            "pending": total - translated,
            "progress": round(translated / total, 4) if total else 1,
        },
        "details": {},
    }


def translate(config: AppConfig, args: argparse.Namespace) -> dict:
    book = load_book(config.books_dir, args.book)
    terms = load_terms(config.books_dir, book.id)
    pending = pending_paragraphs(book.paragraphs)
    batches = make_batches(pending, config.translation.batch_max_chars)
    if args.max_batches is not None:
        batches = batches[: args.max_batches]
    translated_count = 0
    for batch in batches:
        if args.dry_run:
            result = {paragraph.id: paragraph.source for paragraph in batch}
        else:
            result = translate_batch(config, batch, terms)
        for paragraph in batch:
            translated = result.get(paragraph.id, "").strip()
            if translated:
                paragraph.translated = translated
                translated_count += 1
        persist_book(config.books_dir, book)
    return {
        "status": "ok",
        "warnings": [],
        "summary": {
            "book": book.id,
            "batches": len(batches),
            "translated": translated_count,
            "pending": len(pending_paragraphs(book.paragraphs)),
        },
        "details": {},
    }


def export_terminology(config: AppConfig, args: argparse.Namespace) -> dict:
    book = load_book(config.books_dir, args.book)
    output_dir = args.output_dir.expanduser().resolve()
    details = export_terminology_workspace(book, output_dir, load_terms(config.books_dir, book.id))
    return {
        "status": "ok",
        "warnings": [],
        "summary": {
            "book": book.id,
            "candidate_count": details["candidate_count"],
            "filled_count": details["filled_count"],
            "output_dir": str(output_dir),
        },
        "details": details,
    }


def import_terminology(config: AppConfig, args: argparse.Namespace) -> dict:
    book = load_book(config.books_dir, args.book)
    input_path = args.input.expanduser().resolve()
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    items = raw.get("terms", raw if isinstance(raw, list) else [])
    if not isinstance(items, list):
        raise ValueError("术语表必须是数组，或包含 terms 数组的对象")
    terms = [term_from_dict(item) for item in items if isinstance(item, dict)]
    errors, warnings = validate_terms(terms)
    if errors:
        return {
            "status": "error",
            "errors": [{"code": "terminology_invalid", "message": message} for message in errors],
            "warnings": warnings,
            "summary": {"book": book.id, "input": str(input_path)},
            "details": {},
        }
    saved_path = save_terms(config.books_dir, book.id, terms)
    return {
        "status": "ok",
        "warnings": warnings,
        "summary": {
            "book": book.id,
            "input": str(input_path),
            "saved": str(saved_path),
            "term_count": len(terms),
            "filled_count": sum(1 for term in terms if term.target),
            "empty_count": sum(1 for term in terms if not term.target),
        },
        "details": {},
    }


def terminology_status(config: AppConfig, book_id: str) -> dict:
    book = load_book(config.books_dir, book_id)
    terms = load_terms(config.books_dir, book.id)
    errors, warnings = validate_terms(terms)
    status = "error" if errors else "ok"
    if not errors and warnings:
        status = "warning"
    return {
        "status": status,
        "errors": [{"code": "terminology_invalid", "message": message} for message in errors],
        "warnings": warnings,
        "summary": {
            "book": book.id,
            "term_count": len(terms),
            "filled_count": sum(1 for term in terms if term.target),
            "empty_count": sum(1 for term in terms if not term.target),
        },
        "details": {"terms": [term.__dict__ for term in terms[:100]]},
    }


def export_book(config: AppConfig, args: argparse.Namespace) -> dict:
    book = load_book(config.books_dir, args.book)
    output = args.output.expanduser().resolve()
    if args.format == "txt":
        export_txt(book, output, bilingual=args.bilingual)
        warnings = []
    elif args.format == "epub":
        result = export_epub(book, output, config.epub)
        warnings = result["warnings"]
    else:
        raise ValueError(f"不支持导出格式：{args.format}")
    return {
        "status": "ok" if not warnings else "warning",
        "warnings": warnings,
        "summary": {"book": book.id, "output": str(output), "format": args.format, "warning_count": len(warnings)},
        "details": {},
    }


def validate_export(config: AppConfig, args: argparse.Namespace) -> dict:
    book = load_book(config.books_dir, args.book)
    terms = load_terms(config.books_dir, book.id)
    quality = quality_report(book, config.quality, terms)
    errors = []
    warnings = []
    pending = quality["summary"].get("untranslated", 0)
    if pending:
        errors.append({"code": "export_pending_translations", "message": f"还有 {pending} 个段落未翻译"})
    if args.format == "epub" and book.source_type != "epub":
        errors.append({"code": "export_format_invalid", "message": "TXT 注册书籍不能导出 EPUB"})
    if args.format == "epub":
        risk_count = quality["summary"].get("epub_markup_risk", 0)
        if risk_count:
            warnings.append(f"存在 {risk_count} 个 EPUB 标记风险段落，导出后需要人工复核")
    status = "error" if errors else ("warning" if warnings or quality["status"] != "ok" else "ok")
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "book": book.id,
            "format": args.format,
            "quality_status": quality["status"],
            "pending": pending,
            "epub_markup_risk": quality["summary"].get("epub_markup_risk", 0),
        },
        "details": {"quality": quality},
    }


def emit(payload: dict, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    status = payload.get("status", "ok")
    print(f"status: {status}")
    warnings = payload.get("warnings") or []
    for warning in warnings:
        print(f"warning: {warning}")
    summary = payload.get("summary") or {}
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
