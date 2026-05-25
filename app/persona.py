from __future__ import annotations

import re
from typing import Any


BAD_ADDRESS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[\u4e00-\u9fffA-Za-z·・ー]{1,16}桑", "san_transliteration"),
    (r"[\u4e00-\u9fffA-Za-z·・ー]{1,16}酱", "chan_transliteration"),
    (r"[\u4e00-\u9fffA-Za-z·・ー]{1,16}碳", "tan_transliteration"),
    (r"[\u4e00-\u9fffA-Za-z·・ー]{1,16}君", "kun_transliteration"),
    (r"[\u4e00-\u9fffA-Za-z·・ー]{0,16}(?:さん|くん|ちゃん|さま)", "japanese_honorific_residual"),
)

SOURCE_HONORIFIC_PATTERN = re.compile(r"([\u30A1-\u30FA\u30FC一-龥A-Za-z·・ー]{1,24})(さん|くん|君|ちゃん|さま|様|殿|先生|先輩|後輩|氏)")
TARGET_BAD_ADDRESS_RE = re.compile("|".join(f"(?:{pattern})" for pattern, _ in BAD_ADDRESS_PATTERNS))


def person_address_issues(source: str, translated: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not translated:
        return issues
    for pattern, code in BAD_ADDRESS_PATTERNS:
        match = re.search(pattern, translated)
        if match:
            issues.append({"code": code, "match": match.group(0), "suggestion": suggestion_for_code(code)})
    if SOURCE_HONORIFIC_PATTERN.search(source) and not issues:
        for value in ("桑", "酱", "さん", "くん", "ちゃん", "さま"):
            if value in translated:
                issues.append({"code": "honorific_residual", "match": value, "suggestion": "根据人物关系改为中文称呼或直接省略敬称。"})
                break
    return issues


def terminology_address_warnings(terms: list[Any]) -> list[str]:
    warnings: list[str] = []
    for term in terms:
        if term.target and TARGET_BAD_ADDRESS_RE.search(term.target):
            warnings.append(f"术语 {term.source} 的译名 {term.target} 疑似残留日式敬称音译，请改为中文称呼或去掉敬称")
        if SOURCE_HONORIFIC_PATTERN.search(term.source) and term.target and term.source != term.target:
            if any(value in term.target for value in ("桑", "酱", "さん", "くん", "ちゃん", "さま")):
                warnings.append(f"术语 {term.source} 的译名 {term.target} 保留了日式敬称，请按人物关系处理称呼")
    return warnings


def suggestion_for_code(code: str) -> str:
    if code == "san_transliteration":
        return "不要把 さん 直译为“桑”；按语境改为直呼姓名、先生、小姐、前辈、老师等中文称呼。"
    if code == "chan_transliteration":
        return "不要把 ちゃん 直译为“酱”；按亲密度改为昵称、小名、直呼姓名或省略。"
    if code == "tan_transliteration":
        return "不要保留 たん/碳 这类音译敬称；按中文亲昵称呼重写。"
    if code == "kun_transliteration":
        return "不要把 くん/君 机械保留为“君”；按语境改为直呼姓名、同伴称呼、前辈/后辈等中文表达。"
    if code == "japanese_honorific_residual":
        return "译文仍含日文敬称，按人物关系改为中文称呼或省略。"
    return "检查人物称呼是否符合中文语境和人物关系。"
