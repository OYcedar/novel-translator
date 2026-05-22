from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from app.analysis import analyze_book as analyze_book_report, translation_plan as translation_plan_report
from app.book_io import export_epub, export_txt, inspect_epub, load_source_book
from app.config import AppConfig, EpubConfig, load_config
from app.context import context_status, load_context, summarize_context
from app.delivery import export_epub_risk_report, package_delivery
from app.feedback import verify_feedback_text
from app.manual import (
    audit_coverage_report,
    export_pending_translations,
    export_quality_fix,
    import_manual_translations,
    reset_translations,
)
from app.memory import (
    export_memory,
    import_memory,
    load_memory,
    lookup_memory,
    memory_status,
    remember_translation,
    save_memory,
    terminology_hash,
)
from app.models import load_book, persist_book, save_book, slugify
from app.placeholders import extract_placeholders
from app.quality import quality_report
from app.review import apply_review_fixes, export_review_report, review_translations
from app.runs import export_run_report, failed_batches, latest_failed_paragraph_ids, new_run_id, record_batch, run_report
from app.snapshots import create_snapshot, list_snapshots, restore_snapshot
from app.terminology import (
    export_terminology_workspace,
    load_terms,
    relevant_terms_for_text,
    save_terms,
    term_from_dict,
    validate_terms,
)
from app.task_control import clear_stop, request_stop, stop_requested, task_status
from app.translator import make_batches, pending_paragraphs, translate_batch, validate_llm_response
from app.work_records import collect_external_logs, collect_files, init_work_records
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
    translate.add_argument("--no-memory", action="store_true", help="不使用翻译记忆")
    translate.add_argument("--run-id", help="指定本轮运行 ID")
    translate.add_argument("--workers", type=int)
    translate.add_argument("--rpm", type=int)
    translate.add_argument("--tpm", type=int)
    translate.add_argument("--stop-on-warning", action="store_true")

    run_folder = add_common(subparsers.add_parser("run-folder", help="批量处理原文文件夹中的 EPUB/TXT 小说"), json_flag=True)
    run_folder.add_argument("--input-dir", type=Path)
    run_folder.add_argument("--output-dir", type=Path)
    run_folder.add_argument("--format", choices=["txt", "epub"])
    run_folder.add_argument("--dry-run", action="store_true")
    run_folder.add_argument("--max-batches", type=int)
    add_export_mode_flags(run_folder)

    repair = add_common(subparsers.add_parser("repair-translations", help="重译质量报告标记的风险段落"), json_flag=True)
    repair.add_argument("--book", required=True)
    repair.add_argument("--dry-run", action="store_true")
    repair.add_argument("--run-id")
    repair.add_argument("--max-batches", type=int)

    refresh = add_common(subparsers.add_parser("context-refresh", help="重建章节上下文摘要"), json_flag=True)
    refresh.add_argument("--book", required=True)

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

    memory_status_parser = add_common(subparsers.add_parser("translation-memory-status", help="查看翻译记忆状态"), json_flag=True)
    memory_status_parser.add_argument("--book", required=True)

    memory_export = add_common(subparsers.add_parser("export-translation-memory", help="导出翻译记忆"), json_flag=True)
    memory_export.add_argument("--book", required=True)
    memory_export.add_argument("--output", type=Path, required=True)

    memory_import = add_common(subparsers.add_parser("import-translation-memory", help="导入翻译记忆"), json_flag=True)
    memory_import.add_argument("--book", required=True)
    memory_import.add_argument("--input", type=Path, required=True)

    summarize = add_common(subparsers.add_parser("summarize-context", help="生成章节上下文摘要"), json_flag=True)
    summarize.add_argument("--book", required=True)

    context_status_parser = add_common(subparsers.add_parser("context-status", help="查看章节上下文状态"), json_flag=True)
    context_status_parser.add_argument("--book", required=True)

    run_report_parser = add_common(subparsers.add_parser("run-report", help="查看翻译运行报告"), json_flag=True)
    run_report_parser.add_argument("--book", required=True)

    failed_parser = add_common(subparsers.add_parser("failed-batches", help="查看失败批次"), json_flag=True)
    failed_parser.add_argument("--book", required=True)

    retry_parser = add_common(subparsers.add_parser("retry-failed", help="重试失败批次"), json_flag=True)
    retry_parser.add_argument("--book", required=True)
    retry_parser.add_argument("--run-id")
    retry_parser.add_argument("--dry-run", action="store_true")

    request_stop_parser = add_common(subparsers.add_parser("request-stop", help="请求运行中的翻译任务优雅停止"), json_flag=True)
    request_stop_parser.add_argument("--book", required=True)
    request_stop_parser.add_argument("--reason", default="")

    clear_stop_parser = add_common(subparsers.add_parser("clear-stop", help="清除翻译停止请求"), json_flag=True)
    clear_stop_parser.add_argument("--book", required=True)

    task_status_parser = add_common(subparsers.add_parser("task-status", help="查看翻译任务控制状态"), json_flag=True)
    task_status_parser.add_argument("--book", required=True)

    records_parser = add_common(subparsers.add_parser("work-records", help="初始化或收纳单本小说的工作记录"), json_flag=True)
    records_parser.add_argument("--book", required=True)
    records_parser.add_argument("--collect-log-dir", type=Path, help="复制外部日志目录中的翻译脚本和日志")
    records_parser.add_argument("--collect-file", type=Path, action="append", default=[], help="复制外部文件到本书 imports 目录")
    records_parser.add_argument("--log-pattern", default="translate*", help="收纳日志时使用的文件匹配模式")

    analyze = add_common(subparsers.add_parser("analyze-book", help="生成译前项目画像"), json_flag=True)
    analyze.add_argument("--book", required=True)

    plan = add_common(subparsers.add_parser("translation-plan", help="生成 Agent 翻译执行计划"), json_flag=True)
    plan.add_argument("--book", required=True)

    review = add_common(subparsers.add_parser("review-translations", help="审校译文并生成建议"), json_flag=True)
    review.add_argument("--book", required=True)
    review.add_argument("--mode", choices=["risk", "sample", "all"])

    apply_review = add_common(subparsers.add_parser("apply-review-fixes", help="应用已批准的审校修复"), json_flag=True)
    apply_review.add_argument("--book", required=True)
    apply_review.add_argument("--input", type=Path, required=True)

    review_report = add_common(subparsers.add_parser("export-review-report", help="导出 Markdown 审校报告"), json_flag=True)
    review_report.add_argument("--book", required=True)
    review_report.add_argument("--review-id", required=True)
    review_report.add_argument("--output", type=Path, required=True)

    pipeline = add_common(subparsers.add_parser("run-pipeline", help="运行成熟翻译流水线"), json_flag=True)
    pipeline.add_argument("--book", required=True)
    pipeline.add_argument("--export", choices=["txt", "epub"])
    pipeline.add_argument("--output", type=Path)
    pipeline.add_argument("--dry-run", action="store_true")
    add_export_mode_flags(pipeline)

    run_report_export = add_common(subparsers.add_parser("export-run-report", help="导出 Markdown 运行报告"), json_flag=True)
    run_report_export.add_argument("--book", required=True)
    run_report_export.add_argument("--output", type=Path, required=True)

    epub_risk = add_common(subparsers.add_parser("export-epub-risk-report", help="导出 EPUB 风险报告"), json_flag=True)
    epub_risk.add_argument("--book", required=True)
    epub_risk.add_argument("--output", type=Path, required=True)

    snapshot_cmd = add_common(subparsers.add_parser("snapshot", help="创建译文快照"), json_flag=True)
    snapshot_cmd.add_argument("--book", required=True)
    snapshot_cmd.add_argument("--name", required=True)

    list_snapshot_cmd = add_common(subparsers.add_parser("list-snapshots", help="列出译文快照"), json_flag=True)
    list_snapshot_cmd.add_argument("--book", required=True)

    restore_snapshot_cmd = add_common(subparsers.add_parser("restore-snapshot", help="恢复译文快照"), json_flag=True)
    restore_snapshot_cmd.add_argument("--book", required=True)
    restore_snapshot_cmd.add_argument("--snapshot", required=True)

    delivery = add_common(subparsers.add_parser("package-delivery", help="生成交付包"), json_flag=True)
    delivery.add_argument("--book", required=True)
    delivery.add_argument("--output-dir", type=Path, required=True)
    add_export_mode_flags(delivery)

    export = add_common(subparsers.add_parser("export", help="导出译文"), json_flag=True)
    export.add_argument("--book", required=True)
    export.add_argument("--format", choices=["txt", "epub"], required=True)
    export.add_argument("--output", type=Path, required=True)
    add_export_mode_flags(export)

    validate_export_parser = add_common(subparsers.add_parser("validate-export", help="导出前检查"), json_flag=True)
    validate_export_parser.add_argument("--book", required=True)
    validate_export_parser.add_argument("--format", choices=["txt", "epub"], required=True)
    return parser


def add_common(parser: argparse.ArgumentParser, *, json_flag: bool) -> argparse.ArgumentParser:
    if json_flag:
        parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def add_export_mode_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--bilingual", action="store_true", help="导出原文和译文对照")
    group.add_argument("--monolingual", action="store_true", help="只导出译文")


def resolve_bilingual(config: AppConfig, args: argparse.Namespace) -> bool:
    if getattr(args, "bilingual", False):
        return True
    if getattr(args, "monolingual", False):
        return False
    return config.export.bilingual


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
    if args.command == "run-folder":
        return run_folder(config, args)
    if args.command == "repair-translations":
        return repair_translations(config, args)
    if args.command == "context-refresh":
        book = load_book(config.books_dir, args.book)
        return summarize_context(config.books_dir, book, config.context, config.llm)
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
    if args.command == "translation-memory-status":
        return memory_status(config.books_dir, args.book, load_terms(config.books_dir, args.book))
    if args.command == "export-translation-memory":
        return export_memory(config.books_dir, args.book, args.output.expanduser().resolve())
    if args.command == "import-translation-memory":
        return import_memory(config.books_dir, args.book, args.input.expanduser().resolve())
    if args.command == "summarize-context":
        book = load_book(config.books_dir, args.book)
        return summarize_context(config.books_dir, book, config.context, config.llm)
    if args.command == "context-status":
        book = load_book(config.books_dir, args.book)
        return context_status(config.books_dir, book)
    if args.command == "run-report":
        book = load_book(config.books_dir, args.book)
        return run_report(config.books_dir, args.book, _pending_id_set(book))
    if args.command == "failed-batches":
        book = load_book(config.books_dir, args.book)
        return failed_batches(config.books_dir, args.book, _pending_id_set(book))
    if args.command == "retry-failed":
        return retry_failed(config, args)
    if args.command == "request-stop":
        return request_stop(config.books_dir, args.book, args.reason)
    if args.command == "clear-stop":
        return clear_stop(config.books_dir, args.book)
    if args.command == "task-status":
        return task_status(config.books_dir, args.book)
    if args.command == "work-records":
        return work_records(config, args)
    if args.command == "analyze-book":
        book = load_book(config.books_dir, args.book)
        return analyze_book_report(config.books_dir, book, load_terms(config.books_dir, book.id))
    if args.command == "translation-plan":
        book = load_book(config.books_dir, args.book)
        return translation_plan_report(config.books_dir, book, load_terms(config.books_dir, book.id), config.quality)
    if args.command == "review-translations":
        book = load_book(config.books_dir, args.book)
        return review_translations(config, book, load_terms(config.books_dir, book.id), args.mode)
    if args.command == "apply-review-fixes":
        book = load_book(config.books_dir, args.book)
        return apply_review_fixes(config.books_dir, book, args.input.expanduser().resolve())
    if args.command == "export-review-report":
        return export_review_report(config.books_dir, args.book, args.review_id, args.output.expanduser().resolve())
    if args.command == "run-pipeline":
        return run_pipeline(config, args)
    if args.command == "export-run-report":
        return export_run_report(config.books_dir, args.book, args.output.expanduser().resolve())
    if args.command == "export-epub-risk-report":
        book = load_book(config.books_dir, args.book)
        return export_epub_risk_report(book, args.output.expanduser().resolve())
    if args.command == "snapshot":
        book = load_book(config.books_dir, args.book)
        return create_snapshot(config.books_dir, book, args.name)
    if args.command == "list-snapshots":
        return list_snapshots(config.books_dir, args.book)
    if args.command == "restore-snapshot":
        return restore_snapshot(config.books_dir, args.book, args.snapshot)
    if args.command == "package-delivery":
        book = load_book(config.books_dir, args.book)
        return package_delivery(config.books_dir, book, load_terms(config.books_dir, book.id), config.quality, config.epub, args.output_dir.expanduser().resolve(), bilingual=resolve_bilingual(config, args))
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
    records = init_work_records(book, _work_records_root(config))
    summary = {
        "book": book.id,
        "title": book.title,
        "type": book.source_type,
        "chapters": len(book.chapters),
        "paragraphs": len(book.paragraphs),
        "data_dir": str(target_dir),
        "work_records_dir": records["summary"]["record_dir"],
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
    term_hash = terminology_hash(terms)
    context = load_context(config.books_dir, book.id)
    memory_entries = load_memory(config.books_dir, book.id)
    run_id = args.run_id or new_run_id()
    target_ids = set(getattr(args, "target_ids", []) or [])
    rpm = int(getattr(args, "rpm", None) or config.automation.rpm)
    workers = int(getattr(args, "workers", None) or config.automation.workers)
    tpm = int(getattr(args, "tpm", None) or config.automation.tpm)
    stop_on_warning = bool(getattr(args, "stop_on_warning", False) or config.automation.stop_on_warning)
    pending = pending_paragraphs(book.paragraphs)
    if target_ids:
        pending = [paragraph for paragraph in pending if paragraph.id in target_ids]
    reused_memory = 0
    if not getattr(args, "no_memory", False) and not getattr(args, "dry_run", False):
        for paragraph in list(pending):
            entry = lookup_memory(memory_entries, paragraph.source, term_hash)
            if entry is not None:
                paragraph.translated = entry.translated
                reused_memory += 1
        if reused_memory:
            persist_book(config.books_dir, book)
            pending = pending_paragraphs(book.paragraphs)
            if target_ids:
                pending = [paragraph for paragraph in pending if paragraph.id in target_ids]
    batches = make_batches(pending, config.translation.batch_max_chars)
    if args.max_batches is not None:
        batches = batches[: args.max_batches]
    saved_translations = 0
    batch_succeeded = 0
    batch_failed = 0
    batch_warnings = []
    stopped = False
    if stop_requested(config.books_dir, book.id):
        return _translate_result(
            book,
            run_id,
            batches,
            batch_succeeded,
            batch_failed,
            batch_warnings,
            reused_memory,
            saved_translations,
            target_ids,
            workers,
            rpm,
            tpm,
            stopped=True,
        )
    if workers > 1 and len(batches) > 1 and not getattr(args, "dry_run", False) and not stop_on_warning:
        completed = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for index, batch in enumerate(batches, start=1):
                if stop_requested(config.books_dir, book.id):
                    stopped = True
                    break
                if index > 1:
                    if _sleep_with_stop(config.books_dir, book.id, _rate_limit_delay(batch, rpm, tpm)):
                        stopped = True
                        break
                batch_id = f"{run_id}-b{index:04d}"
                started = time.time()
                future = executor.submit(_translate_one_batch, config, batch, terms, book, context, False)
                futures[future] = (index, batch_id, batch, started)
            for future in as_completed(futures):
                index, batch_id, batch, started = futures[future]
                try:
                    result, warnings = future.result()
                    completed.append((index, batch_id, batch, started, result, warnings, ""))
                except Exception as exc:
                    completed.append((index, batch_id, batch, started, {}, [], str(exc)))
        for _index, batch_id, batch, started, result, warnings, error in sorted(completed, key=lambda item: item[0]):
            if error:
                batch_failed += 1
                record_batch(
                    config.books_dir,
                    book.id,
                    run_id,
                    _batch_record(
                        batch_id,
                        batch,
                        status="failed",
                        started=started,
                        error=error,
                        warnings=warnings,
                        model=config.llm.model,
                        retry_count=0,
                        result=result,
                        memory_hits=0,
                        rate_limited=False,
                    ),
                )
                continue
            for paragraph in batch:
                translated = result.get(paragraph.id, "").strip()
                if translated:
                    paragraph.translated = translated
                    saved_translations += 1
                    remember_translation(memory_entries, source=paragraph.source, translated=translated, term_hash=term_hash, model=config.llm.model)
            batch_succeeded += 1
            batch_warnings.extend(warnings)
            record_batch(
                config.books_dir,
                book.id,
                run_id,
                _batch_record(
                    batch_id,
                    batch,
                    status="succeeded",
                    started=started,
                    error="",
                    warnings=warnings,
                    model=config.llm.model,
                    retry_count=0,
                    result=result,
                    memory_hits=0,
                    rate_limited=False,
                ),
            )
        persist_book(config.books_dir, book)
        save_memory(config.books_dir, book.id, memory_entries)
        return _translate_result(
            book,
            run_id,
            batches,
            batch_succeeded,
            batch_failed,
            batch_warnings,
            reused_memory,
            saved_translations,
            target_ids,
            workers,
            rpm,
            tpm,
            stopped=stopped,
        )
    for index, batch in enumerate(batches, start=1):
        if stop_requested(config.books_dir, book.id):
            stopped = True
            break
        if index > 1 and not getattr(args, "dry_run", False):
            if _sleep_with_stop(config.books_dir, book.id, _rate_limit_delay(batch, rpm, tpm)):
                stopped = True
                break
        batch_id = f"{run_id}-b{index:04d}"
        started = time.time()
        result: dict[str, str] = {}
        warnings: list[str] = []
        error = ""
        try:
            result, warnings = _translate_one_batch(config, batch, terms, book, context, getattr(args, "dry_run", False))
        except Exception as exc:
            error = str(exc)
            batch_failed += 1
            record_batch(
                config.books_dir,
                book.id,
                run_id,
                _batch_record(
                    batch_id,
                    batch,
                    status="failed",
                    started=started,
                    error=error,
                    warnings=warnings,
                    model=config.llm.model,
                    retry_count=0,
                    result=result,
                    memory_hits=0,
                    rate_limited=False,
                ),
            )
            continue
        for paragraph in batch:
            translated = result.get(paragraph.id, "").strip()
            if translated:
                paragraph.translated = translated
                saved_translations += 1
                if not getattr(args, "dry_run", False):
                    remember_translation(memory_entries, source=paragraph.source, translated=translated, term_hash=term_hash, model=config.llm.model)
        batch_succeeded += 1
        batch_warnings.extend(warnings)
        record_batch(
            config.books_dir,
            book.id,
            run_id,
            _batch_record(
                batch_id,
                batch,
                status="succeeded",
                started=started,
                error="",
                warnings=warnings,
                model=config.llm.model,
                retry_count=0,
                result=result,
                memory_hits=0,
                rate_limited=False,
            ),
        )
        persist_book(config.books_dir, book)
        if not getattr(args, "dry_run", False):
            save_memory(config.books_dir, book.id, memory_entries)
        if warnings and stop_on_warning:
            break
    return _translate_result(
        book,
        run_id,
        batches,
        batch_succeeded,
        batch_failed,
        batch_warnings,
        reused_memory,
        saved_translations,
        target_ids,
        workers,
        rpm,
        tpm,
        stopped=stopped,
    )


def run_folder(config: AppConfig, args: argparse.Namespace) -> dict:
    input_dir = _configured_dir(config.root, args.input_dir, config.automation.folder_input_dir)
    output_dir = _configured_dir(config.root, args.output_dir, config.automation.folder_output_dir)
    files = sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".epub", ".txt"}
    )
    items = []
    warnings = []
    errors = []
    bilingual = resolve_bilingual(config, args)
    for source_path in files:
        item = {
            "source": str(source_path),
            "format": args.format or source_path.suffix.lower().lstrip("."),
            "dry_run": bool(args.dry_run),
            "steps": [],
        }
        try:
            if source_path.suffix.lower() == ".epub":
                inspection = inspect_epub(source_path, config.epub)
                item["steps"].append({"step": "inspect-epub", "status": inspection["status"], "summary": inspection["summary"]})
                if inspection.get("warnings"):
                    warnings.extend(f"{source_path.name}: {warning}" for warning in inspection["warnings"])
            if args.dry_run:
                book = load_source_book(source_path, epub_config=config.epub)
                item["summary"] = {
                    "title": book.title,
                    "type": book.source_type,
                    "chapters": len(book.chapters),
                    "paragraphs": len(book.paragraphs),
                    "would_register": True,
                    "would_translate": True,
                    "would_export": True,
                    "bilingual": bilingual,
                }
                items.append(item)
                continue
            book = load_source_book(source_path, epub_config=config.epub)
            save_book(config.books_dir, book, source_path)
            terms = load_terms(config.books_dir, book.id)
            item["book"] = book.id
            item["steps"].append({"step": "add-book", "status": "ok", "summary": {"book": book.id, "chapters": len(book.chapters), "paragraphs": len(book.paragraphs)}})
            analysis = analyze_book_report(config.books_dir, book, terms)
            item["steps"].append({"step": "analyze-book", "status": analysis["status"], "summary": analysis["summary"]})
            plan = translation_plan_report(config.books_dir, book, terms, config.quality)
            item["steps"].append({"step": "translation-plan", "status": plan["status"], "summary": plan["summary"]})
            context = context_status(config.books_dir, book)
            if context["status"] != "ok":
                context = summarize_context(config.books_dir, book, config.context, config.llm)
            item["steps"].append({"step": "context", "status": context["status"], "summary": context["summary"]})
            translate_args = argparse.Namespace(
                book=book.id,
                max_batches=args.max_batches,
                dry_run=False,
                no_memory=False,
                run_id=new_run_id(),
                workers=config.automation.workers,
                rpm=config.automation.rpm,
                tpm=config.automation.tpm,
                stop_on_warning=config.automation.stop_on_warning,
            )
            translated = translate(config, translate_args)
            item["steps"].append({"step": "translate", "status": translated["status"], "summary": translated["summary"]})
            if config.automation.auto_retry_failed:
                retry = retry_failed(config, argparse.Namespace(book=book.id, run_id=new_run_id(), dry_run=False))
                item["steps"].append({"step": "retry-failed", "status": retry["status"], "summary": retry["summary"]})
            book = load_book(config.books_dir, book.id)
            quality = quality_report(book, config.quality, terms)
            item["steps"].append({"step": "quality-report", "status": quality["status"], "summary": quality["summary"]})
            export_format = args.format or book.source_type
            validation = validate_export(config, argparse.Namespace(book=book.id, format=export_format))
            item["steps"].append({"step": "validate-export", "status": validation["status"], "summary": validation["summary"]})
            if validation["status"] != "error":
                delivery_dir = output_dir / book.id
                delivery = package_delivery(config.books_dir, book, terms, config.quality, config.epub, delivery_dir, bilingual=bilingual, export_format=export_format)
                item["steps"].append({"step": "package-delivery", "status": delivery["status"], "summary": delivery["summary"]})
            items.append(item)
        except Exception as error:
            errors.append({"source": str(source_path), "code": type(error).__name__, "message": str(error)})
            item["error"] = str(error)
            items.append(item)
    status = "error" if errors else ("warning" if warnings or any(any(step.get("status") == "warning" for step in item.get("steps", [])) for item in items) else "ok")
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "files": len(files),
            "dry_run": bool(args.dry_run),
            "bilingual": bilingual,
        },
        "details": {"items": items},
    }


def _translate_result(
    book,
    run_id: str,
    batches: list[list],
    batch_succeeded: int,
    batch_failed: int,
    batch_warnings: list[str],
    reused_memory: int,
    saved_translations: int,
    target_ids: set[str],
    workers: int,
    rpm: int,
    tpm: int,
    *,
    stopped: bool,
) -> dict:
    warnings = list(batch_warnings)
    if stopped:
        warnings.append("收到停止请求，已在安全边界保存进度并退出。")
    status = "warning" if stopped or batch_failed else "ok"
    return {
        "status": status,
        "warnings": warnings,
        "summary": {
            "book": book.id,
            "run_id": run_id,
            "batches": len(batches),
            "batch_succeeded": batch_succeeded,
            "batch_failed": batch_failed,
            "reused_memory": reused_memory,
            "saved_translations": saved_translations,
            "translated": saved_translations,
            "pending": len(pending_paragraphs(book.paragraphs)),
            "targeted": len(target_ids),
            "workers": workers,
            "rpm": rpm,
            "tpm": tpm,
            "stopped": stopped,
        },
        "details": {},
    }


def _sleep_with_stop(root_books_dir: Path, book_id: str, seconds: float) -> bool:
    deadline = time.time() + max(0, seconds)
    while time.time() < deadline:
        if stop_requested(root_books_dir, book_id):
            return True
        time.sleep(min(1, deadline - time.time()))
    return stop_requested(root_books_dir, book_id)


def repair_translations(config: AppConfig, args: argparse.Namespace) -> dict:
    book = load_book(config.books_dir, args.book)
    terms = load_terms(config.books_dir, book.id)
    quality = quality_report(book, config.quality, terms)
    target_ids = _quality_target_ids(quality)
    if args.max_batches is not None:
        batches = make_batches([paragraph for paragraph in book.paragraphs if paragraph.id in target_ids], config.translation.batch_max_chars)
        target_ids = {paragraph.id for batch in batches[: args.max_batches] for paragraph in batch}
    if args.dry_run:
        return {
            "status": "ok",
            "warnings": [],
            "summary": {"book": book.id, "targets": len(target_ids), "dry_run": True},
            "details": {"target_ids": sorted(target_ids), "quality": quality["summary"]},
        }
    for paragraph in book.paragraphs:
        if paragraph.id in target_ids:
            paragraph.translated = ""
    persist_book(config.books_dir, book)
    translate_args = argparse.Namespace(
        book=book.id,
        max_batches=None,
        dry_run=False,
        no_memory=True,
        run_id=args.run_id or new_run_id(),
        target_ids=sorted(target_ids),
        workers=config.automation.workers,
        rpm=config.automation.rpm,
        tpm=config.automation.tpm,
        stop_on_warning=config.automation.stop_on_warning,
    )
    report = translate(config, translate_args)
    report["summary"]["repair_targets"] = len(target_ids)
    report["details"] = {"target_ids": sorted(target_ids), "quality_before": quality["summary"]}
    return report


def _configured_dir(root: Path, explicit: Path | None, configured: str) -> Path:
    path = explicit or Path(configured)
    path = path.expanduser()
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _quality_target_ids(quality: dict) -> set[str]:
    ids: set[str] = set()
    for key in ("source_residual", "terminology_mismatch", "placeholder_mismatch", "style_inconsistency", "dialogue_punctuation", "over_literal_translation", "review_required"):
        for item in quality.get("details", {}).get(key, []):
            if isinstance(item, str):
                ids.add(item)
            elif isinstance(item, dict) and item.get("id"):
                ids.add(str(item["id"]))
    return ids


def retry_failed(config: AppConfig, args: argparse.Namespace) -> dict:
    book = load_book(config.books_dir, args.book)
    ids = latest_failed_paragraph_ids(config.books_dir, book.id, _pending_id_set(book))
    if not ids:
        return {"status": "ok", "warnings": [], "summary": {"book": book.id, "retried": 0, "pending": len(pending_paragraphs(book.paragraphs))}, "details": {}}
    if args.dry_run:
        return {
            "status": "ok",
            "warnings": [],
            "summary": {
                "book": book.id,
                "retried": len(ids),
                "pending": len(pending_paragraphs(book.paragraphs)),
                "dry_run": True,
            },
            "details": {"target_ids": ids},
        }
    targets = set(ids)
    for paragraph in book.paragraphs:
        if paragraph.id in targets:
            paragraph.translated = ""
    persist_book(config.books_dir, book)
    retry_args = argparse.Namespace(
        book=book.id,
        max_batches=None,
        dry_run=args.dry_run,
        no_memory=True,
        run_id=args.run_id or new_run_id(),
        target_ids=ids,
        workers=1,
        rpm=config.automation.rpm,
        tpm=config.automation.tpm,
        stop_on_warning=False,
    )
    report = translate(config, retry_args)
    report["summary"]["retried_ids"] = ids
    return report


def _pending_id_set(book) -> set[str]:
    return {paragraph.id for paragraph in pending_paragraphs(book.paragraphs)}


def work_records(config: AppConfig, args: argparse.Namespace) -> dict:
    book = load_book(config.books_dir, args.book)
    records_root = _work_records_root(config)
    if args.collect_file:
        return collect_files(book, records_root, [path.expanduser().resolve() for path in args.collect_file])
    if args.collect_log_dir:
        return collect_external_logs(
            book,
            records_root,
            args.collect_log_dir.expanduser().resolve(),
            pattern=args.log_pattern,
        )
    return init_work_records(book, records_root)


def _work_records_root(config: AppConfig) -> Path:
    return _configured_dir(config.root, None, config.automation.work_records_dir)


def run_pipeline(config: AppConfig, args: argparse.Namespace) -> dict:
    book = load_book(config.books_dir, args.book)
    terms = load_terms(config.books_dir, book.id)
    steps = []
    warnings = []
    snapshot_report = create_snapshot(config.books_dir, book, "pipeline-start")
    steps.append({"step": "snapshot", "status": snapshot_report["status"], "summary": snapshot_report["summary"]})
    analysis = analyze_book_report(config.books_dir, book, terms)
    steps.append({"step": "analyze-book", "status": analysis["status"], "summary": analysis["summary"]})
    plan = translation_plan_report(config.books_dir, book, terms, config.quality)
    steps.append({"step": "translation-plan", "status": plan["status"], "summary": plan["summary"]})
    if plan.get("warnings"):
        warnings.extend(plan["warnings"])
    context = context_status(config.books_dir, book)
    if context["status"] != "ok":
        context = summarize_context(config.books_dir, book, config.context, config.llm)
    steps.append({"step": "context", "status": context["status"], "summary": context["summary"]})
    translate_args = argparse.Namespace(
        book=book.id,
        max_batches=None,
        dry_run=args.dry_run,
        no_memory=False,
        run_id=new_run_id(),
        workers=config.automation.workers,
        rpm=config.automation.rpm,
        tpm=config.automation.tpm,
        stop_on_warning=config.automation.stop_on_warning,
    )
    translated = translate(config, translate_args)
    steps.append({"step": "translate", "status": translated["status"], "summary": translated["summary"]})
    if config.automation.auto_retry_failed:
        retry_args = argparse.Namespace(book=book.id, run_id=new_run_id(), dry_run=args.dry_run)
        retry = retry_failed(config, retry_args)
        steps.append({"step": "retry-failed", "status": retry["status"], "summary": retry["summary"]})
    book = load_book(config.books_dir, book.id)
    quality = quality_report(book, config.quality, terms)
    steps.append({"step": "quality-report", "status": quality["status"], "summary": quality["summary"]})
    review = review_translations(config, book, terms, "risk")
    steps.append({"step": "review-translations", "status": review["status"], "summary": review["summary"]})
    export_validation = None
    if args.export:
        validation_args = argparse.Namespace(book=book.id, format=args.export)
        export_validation = validate_export(config, validation_args)
        steps.append({"step": "validate-export", "status": export_validation["status"], "summary": export_validation["summary"]})
        if export_validation["status"] != "error":
            output = args.output.expanduser().resolve() if args.output else config.root / f"{book.id}.{args.export}"
            export_args = argparse.Namespace(book=book.id, format=args.export, output=output, bilingual=resolve_bilingual(config, args), monolingual=False)
            exported = export_book(config, export_args)
            steps.append({"step": "export", "status": exported["status"], "summary": exported["summary"]})
    status = "error" if any(step["status"] == "error" for step in steps) else ("warning" if warnings or any(step["status"] == "warning" for step in steps) else "ok")
    return {
        "status": status,
        "warnings": warnings,
        "summary": {"book": book.id, "steps": len(steps), "snapshot": snapshot_report["summary"].get("snapshot_id", "")},
        "details": {"steps": steps},
    }


def _batch_record(batch_id, batch, *, status: str, started: float, error: str, warnings: list[str], model: str, retry_count: int, result: dict[str, str] | None = None, memory_hits: int = 0, rate_limited: bool = False) -> dict:
    result = result or {}
    input_chars = sum(len(paragraph.source) for paragraph in batch)
    output_chars = sum(len(result.get(paragraph.id, "")) for paragraph in batch)
    return {
        "batch_id": batch_id,
        "status": status,
        "paragraph_ids": [paragraph.id for paragraph in batch],
        "requested_at": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(),
        "model": model,
        "duration_seconds": round(time.time() - started, 3),
        "retry_count": retry_count,
        "input_chars": input_chars,
        "output_chars": output_chars,
        "estimated_tokens": max(1, round((input_chars + output_chars) / 2)),
        "memory_hits": memory_hits,
        "rate_limited": rate_limited,
        "cost_estimate": 0,
        "error": error,
        "warnings": warnings,
    }


def _rate_limit_delay(batch, rpm: int, tpm: int) -> float:
    rpm_delay = 60 / rpm if rpm > 0 else 0
    if tpm <= 0:
        return max(0, rpm_delay)
    estimated_tokens = max(1, round(sum(len(paragraph.source) for paragraph in batch) / 2))
    tpm_delay = estimated_tokens * 60 / tpm
    return max(0, rpm_delay, tpm_delay)


def _translate_one_batch(config: AppConfig, batch, terms, book: Book, context: dict, dry_run: bool) -> tuple[dict[str, str], list[str]]:
    if dry_run:
        result = {paragraph.id: paragraph.source for paragraph in batch}
    else:
        result = translate_batch(config, batch, terms, book=book, context=context)
    validation = validate_llm_response(batch, result)
    warnings = validation["warnings"]
    warnings.extend(_translation_quality_warnings(batch, result, terms))
    errors = list(validation["errors"])
    errors.extend(_translation_quality_errors(config, batch, result))
    if errors:
        raise ValueError("; ".join(errors))
    return result, warnings


def _translation_quality_warnings(batch, result: dict[str, str], terms) -> list[str]:
    warnings = []
    for paragraph in batch:
        translated = result.get(paragraph.id, "")
        if not translated.strip():
            continue
        missing_placeholders = [
            placeholder.value
            for placeholder in extract_placeholders(paragraph.source)
            if placeholder.value not in translated
        ]
        if missing_placeholders:
            warnings.append(f"段落 {paragraph.id} 缺少占位符：{', '.join(missing_placeholders)}")
        missing_terms = [
            term.target
            for term in relevant_terms_for_text(terms, paragraph.source)
            if term.target and term.target not in translated
        ]
        if missing_terms:
            warnings.append(f"段落 {paragraph.id} 缺少术语译名：{', '.join(missing_terms)}")
    return warnings


def _translation_quality_errors(config: AppConfig, batch, result: dict[str, str]) -> list[str]:
    errors = []
    if config.translation.fail_on_empty_translation:
        empty_ids = [
            paragraph.id
            for paragraph in batch
            if paragraph.id in result and not result.get(paragraph.id, "").strip()
        ]
        if empty_ids:
            errors.append(f"模型响应包含空译文：{', '.join(empty_ids)}")
    if config.translation.fail_on_placeholder_mismatch:
        for paragraph in batch:
            translated = result.get(paragraph.id, "")
            if not translated.strip():
                continue
            missing = [
                placeholder.value
                for placeholder in extract_placeholders(paragraph.source)
                if placeholder.value not in translated
            ]
            if missing:
                errors.append(f"段落 {paragraph.id} 缺少占位符：{', '.join(missing)}")
    return errors


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
    bilingual = resolve_bilingual(config, args)
    if args.format == "txt":
        export_txt(book, output, bilingual=bilingual)
        warnings = []
    elif args.format == "epub":
        result = export_epub(book, output, config.epub, bilingual=bilingual)
        warnings = result["warnings"]
    else:
        raise ValueError(f"不支持导出格式：{args.format}")
    return {
        "status": "ok" if not warnings else "warning",
        "warnings": warnings,
        "summary": {"book": book.id, "output": str(output), "format": args.format, "bilingual": bilingual, "warning_count": len(warnings)},
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
