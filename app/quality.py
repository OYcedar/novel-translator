from __future__ import annotations

import re

from app.config import QualityConfig
from app.models import Book
from app.terminology import Term, relevant_terms_for_text


def quality_report(book: Book, config: QualityConfig, terms: list[Term] | None = None) -> dict:
    untranslated = []
    residual = []
    terminology_mismatch = []
    patterns = [re.compile(pattern) for pattern in config.source_residual_patterns]
    glossary = terms or []
    for paragraph in book.paragraphs:
        if not paragraph.translated.strip():
            untranslated.append(paragraph.id)
            continue
        for term in relevant_terms_for_text(glossary, paragraph.source):
            if term.target not in paragraph.translated:
                terminology_mismatch.append(
                    {
                        "id": paragraph.id,
                        "source": term.source,
                        "expected": term.target,
                        "text": paragraph.translated,
                    }
                )
        for pattern in patterns:
            if pattern.search(paragraph.translated):
                residual.append({"id": paragraph.id, "pattern": pattern.pattern, "text": paragraph.translated})
                break
    status = "ok" if not untranslated and not residual and not terminology_mismatch else "warning"
    return {
        "status": status,
        "warnings": [],
        "summary": {
            "chapters": len(book.chapters),
            "paragraphs": len(book.paragraphs),
            "translated": sum(1 for item in book.paragraphs if item.translated.strip()),
            "untranslated": len(untranslated),
            "source_residual": len(residual),
            "terminology_mismatch": len(terminology_mismatch),
        },
        "details": {
            "untranslated": untranslated[:100],
            "source_residual": residual[:100],
            "terminology_mismatch": terminology_mismatch[:100],
        },
    }
