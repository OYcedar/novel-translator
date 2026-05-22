from __future__ import annotations

import re

from app.config import QualityConfig
from app.models import Book


def quality_report(book: Book, config: QualityConfig) -> dict:
    untranslated = []
    residual = []
    patterns = [re.compile(pattern) for pattern in config.source_residual_patterns]
    for paragraph in book.paragraphs:
        if not paragraph.translated.strip():
            untranslated.append(paragraph.id)
            continue
        for pattern in patterns:
            if pattern.search(paragraph.translated):
                residual.append({"id": paragraph.id, "pattern": pattern.pattern, "text": paragraph.translated})
                break
    status = "ok" if not untranslated and not residual else "warning"
    return {
        "status": status,
        "warnings": [],
        "summary": {
            "chapters": len(book.chapters),
            "paragraphs": len(book.paragraphs),
            "translated": sum(1 for item in book.paragraphs if item.translated.strip()),
            "untranslated": len(untranslated),
            "source_residual": len(residual),
        },
        "details": {
            "untranslated": untranslated[:100],
            "source_residual": residual[:100],
        },
    }
