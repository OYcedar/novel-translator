from __future__ import annotations

import re

from app.config import QualityConfig
from app.models import Book
from app.placeholders import placeholder_mismatches
from app.persona import person_address_issues
from app.terminology import Term, relevant_terms_for_text


def quality_report(book: Book, config: QualityConfig, terms: list[Term] | None = None) -> dict:
    untranslated = []
    residual = []
    terminology_mismatch = []
    placeholder_mismatch = []
    epub_markup_risk = []
    style_inconsistency = []
    dialogue_punctuation = []
    over_literal_translation = []
    person_address_issue = []
    review_required = []
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
        placeholder_mismatch.extend(
            {**item, "text": paragraph.translated}
            for item in placeholder_mismatches(paragraph)
        )
        epub_meta = paragraph.metadata.get("epub", {})
        risks = epub_meta.get("risks", [])
        if risks:
            epub_markup_risk.append(
                {
                    "id": paragraph.id,
                    "chapter_path": epub_meta.get("chapter_path", ""),
                    "risks": list(risks),
                    "text": paragraph.translated,
                }
            )
        for pattern in patterns:
            if pattern.search(paragraph.translated):
                residual.append({"id": paragraph.id, "pattern": pattern.pattern, "text": paragraph.translated})
                break
        if _dialogue_punctuation_issue(paragraph.source, paragraph.translated):
            dialogue_punctuation.append({"id": paragraph.id, "text": paragraph.translated})
        if _over_literal_issue(paragraph.source, paragraph.translated):
            over_literal_translation.append({"id": paragraph.id, "text": paragraph.translated})
        address_issues = person_address_issues(paragraph.source, paragraph.translated)
        if address_issues:
            person_address_issue.append({"id": paragraph.id, "issues": address_issues, "text": paragraph.translated})
        if _style_issue(paragraph.translated):
            style_inconsistency.append({"id": paragraph.id, "text": paragraph.translated})
    review_ids = set()
    for collection in (residual, terminology_mismatch, placeholder_mismatch, epub_markup_risk, style_inconsistency, dialogue_punctuation, over_literal_translation, person_address_issue):
        for item in collection:
            review_ids.add(item["id"])
    review_required = sorted(review_ids)
    status = "ok" if not untranslated and not residual and not terminology_mismatch and not placeholder_mismatch and not epub_markup_risk and not style_inconsistency and not dialogue_punctuation and not over_literal_translation and not person_address_issue else "warning"
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
            "placeholder_mismatch": len(placeholder_mismatch),
            "epub_markup_risk": len(epub_markup_risk),
            "style_inconsistency": len(style_inconsistency),
            "dialogue_punctuation": len(dialogue_punctuation),
            "over_literal_translation": len(over_literal_translation),
            "person_address_issue": len(person_address_issue),
            "review_required": len(review_required),
        },
        "details": {
            "untranslated": untranslated[:100],
            "source_residual": residual[:100],
            "terminology_mismatch": terminology_mismatch[:100],
            "placeholder_mismatch": placeholder_mismatch[:100],
            "epub_markup_risk": epub_markup_risk[:100],
            "style_inconsistency": style_inconsistency[:100],
            "dialogue_punctuation": dialogue_punctuation[:100],
            "over_literal_translation": over_literal_translation[:100],
            "person_address_issue": person_address_issue[:100],
            "review_required": review_required[:100],
        },
    }


def _dialogue_punctuation_issue(source: str, translated: str) -> bool:
    has_dialogue = any(mark in source for mark in ('"', "“", "「", "『"))
    if not has_dialogue:
        return False
    return '"' in translated or translated.count("“") != translated.count("”")


def _over_literal_issue(source: str, translated: str) -> bool:
    if not source or not translated:
        return False
    if source.strip() == translated.strip():
        return True
    ascii_letters = sum(1 for char in translated if ("A" <= char <= "Z") or ("a" <= char <= "z"))
    return len(translated) > 10 and ascii_letters / max(len(translated), 1) > 0.35


def _style_issue(translated: str) -> bool:
    return "  " in translated or "。。" in translated or "，，" in translated
