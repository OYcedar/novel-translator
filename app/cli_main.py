from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.book_io import export_epub, export_txt, load_source_book
from app.config import AppConfig, load_config
from app.models import load_book, persist_book, save_book, slugify
from app.quality import quality_report
from app.translator import make_batches, pending_paragraphs, translate_batch


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

    export = add_common(subparsers.add_parser("export", help="导出译文"), json_flag=True)
    export.add_argument("--book", required=True)
    export.add_argument("--format", choices=["txt", "epub"], required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--bilingual", action="store_true")
    return parser


def add_common(parser: argparse.ArgumentParser, *, json_flag: bool) -> argparse.ArgumentParser:
    if json_flag:
        parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def dispatch(args: argparse.Namespace) -> dict:
    if args.command == "doctor":
        return doctor(args)
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
        return quality_report(book, config.quality)
    if args.command == "export":
        return export_book(config, args)
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
    book = load_source_book(source_path, title=args.title)
    if args.id:
        book.id = slugify(args.id)
    target_dir = save_book(config.books_dir, book, source_path)
    return {
        "status": "ok",
        "warnings": [],
        "summary": {
            "book": book.id,
            "title": book.title,
            "type": book.source_type,
            "chapters": len(book.chapters),
            "paragraphs": len(book.paragraphs),
            "data_dir": str(target_dir),
        },
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
    pending = pending_paragraphs(book.paragraphs)
    batches = make_batches(pending, config.translation.batch_max_chars)
    if args.max_batches is not None:
        batches = batches[: args.max_batches]
    translated_count = 0
    for batch in batches:
        if args.dry_run:
            result = {paragraph.id: paragraph.source for paragraph in batch}
        else:
            result = translate_batch(config, batch)
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


def export_book(config: AppConfig, args: argparse.Namespace) -> dict:
    book = load_book(config.books_dir, args.book)
    output = args.output.expanduser().resolve()
    if args.format == "txt":
        export_txt(book, output, bilingual=args.bilingual)
    elif args.format == "epub":
        export_epub(book, output)
    else:
        raise ValueError(f"不支持导出格式：{args.format}")
    return {
        "status": "ok",
        "warnings": [],
        "summary": {"book": book.id, "output": str(output), "format": args.format},
        "details": {},
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
