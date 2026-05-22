from __future__ import annotations

from collections.abc import Iterable
import json
import time

from app.config import AppConfig
from app.models import Paragraph


def pending_paragraphs(paragraphs: Iterable[Paragraph]) -> list[Paragraph]:
    return [paragraph for paragraph in paragraphs if not paragraph.translated.strip()]


def make_batches(paragraphs: list[Paragraph], max_chars: int) -> list[list[Paragraph]]:
    batches: list[list[Paragraph]] = []
    current: list[Paragraph] = []
    current_chars = 0
    for paragraph in paragraphs:
        size = len(paragraph.source)
        if current and current_chars + size > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(paragraph)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def translate_batch(config: AppConfig, paragraphs: list[Paragraph]) -> dict[str, str]:
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("缺少 openai 依赖，请先安装项目依赖。") from error

    system_prompt = config.system_prompt_path.read_text(encoding="utf-8")
    client = OpenAI(
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
        timeout=config.llm.timeout,
    )
    payload = {
        "source_language": config.translation.source_language,
        "target_language": config.translation.target_language,
        "items": [{"id": item.id, "text": item.source} for item in paragraphs],
    }
    last_error: Exception | None = None
    for attempt in range(config.translation.retry_count + 1):
        try:
            response = client.chat.completions.create(
                model=config.llm.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            return parse_translation_response(content)
        except Exception as error:
            last_error = error
            if attempt >= config.translation.retry_count:
                break
            time.sleep(config.translation.retry_delay)
    raise RuntimeError(f"翻译请求失败：{last_error}") from last_error


def parse_translation_response(content: str) -> dict[str, str]:
    raw = json.loads(content)
    items = raw.get("items", raw if isinstance(raw, list) else [])
    if not isinstance(items, list):
        raise ValueError("模型输出 JSON 缺少 items 数组")
    result: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        if item_id and text:
            result[item_id] = text
    return result

