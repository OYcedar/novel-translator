from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ast

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on Python version
    tomllib = None


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = 600


@dataclass(frozen=True)
class TranslationConfig:
    target_language: str = "zh-Hans"
    source_language: str = "auto"
    system_prompt_file: str = "prompts/novel_translation_system.md"
    batch_max_chars: int = 4000
    retry_count: int = 3
    retry_delay: int = 2


@dataclass(frozen=True)
class ContextConfig:
    previous_paragraphs: int = 3
    next_paragraphs: int = 2
    chapter_summary_max_chars: int = 1200


@dataclass(frozen=True)
class QualityConfig:
    source_residual_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpubConfig:
    parser: str = "auto"
    include_non_linear_spine: bool = False
    preserve_outer_markup: bool = True
    warn_on_ruby: bool = True
    warn_on_duplicate_source: bool = True


@dataclass(frozen=True)
class AppConfig:
    root: Path
    llm: LlmConfig
    translation: TranslationConfig
    context: ContextConfig
    quality: QualityConfig
    epub: EpubConfig

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def books_dir(self) -> Path:
        return self.data_dir / "books"

    @property
    def system_prompt_path(self) -> Path:
        configured = Path(self.translation.system_prompt_file)
        if configured.is_absolute():
            return configured
        return self.root / configured


def load_config(root: Path, config_path: Path | None = None) -> AppConfig:
    path = config_path or root / "setting.toml"
    if not path.exists():
        example = root / "setting.example.toml"
        raise FileNotFoundError(f"配置文件不存在：{path}。请复制 {example.name} 为 setting.toml 后填写。")
    raw = load_toml(path)
    llm_raw = raw.get("llm", {})
    translation_raw = raw.get("translation", {})
    context_raw = raw.get("context", {})
    quality_raw = raw.get("quality", {})
    epub_raw = raw.get("epub", {})
    llm = LlmConfig(
        base_url=str(llm_raw.get("base_url", "")).rstrip("/"),
        api_key=str(llm_raw.get("api_key", "")),
        model=str(llm_raw.get("model", "")),
        timeout=int(llm_raw.get("timeout", 600)),
    )
    translation = TranslationConfig(
        target_language=str(translation_raw.get("target_language", "zh-Hans")),
        source_language=str(translation_raw.get("source_language", "auto")),
        system_prompt_file=str(
            translation_raw.get("system_prompt_file", "prompts/novel_translation_system.md")
        ),
        batch_max_chars=int(translation_raw.get("batch_max_chars", 4000)),
        retry_count=int(translation_raw.get("retry_count", 3)),
        retry_delay=int(translation_raw.get("retry_delay", 2)),
    )
    quality = QualityConfig(
        source_residual_patterns=tuple(str(item) for item in quality_raw.get("source_residual_patterns", []))
    )
    context = ContextConfig(
        previous_paragraphs=int(context_raw.get("previous_paragraphs", 3)),
        next_paragraphs=int(context_raw.get("next_paragraphs", 2)),
        chapter_summary_max_chars=int(context_raw.get("chapter_summary_max_chars", 1200)),
    )
    epub = EpubConfig(
        parser=str(epub_raw.get("parser", "auto")),
        include_non_linear_spine=bool(epub_raw.get("include_non_linear_spine", False)),
        preserve_outer_markup=bool(epub_raw.get("preserve_outer_markup", True)),
        warn_on_ruby=bool(epub_raw.get("warn_on_ruby", True)),
        warn_on_duplicate_source=bool(epub_raw.get("warn_on_duplicate_source", True)),
    )
    return AppConfig(root=root, llm=llm, translation=translation, context=context, quality=quality, epub=epub)


def load_toml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if tomllib is not None:
        return tomllib.loads(text)
    try:
        import tomli

        return tomli.loads(text)
    except ModuleNotFoundError:
        return parse_simple_toml(text)


def parse_simple_toml(text: str) -> dict:
    """Parse the small TOML subset used by setting.example.toml."""
    result: dict[str, dict] = {}
    current: dict | None = None
    pending_key = ""
    pending_value_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if pending_key:
            pending_value_lines.append(line)
            if line.endswith("]"):
                current[pending_key] = ast.literal_eval(" ".join(pending_value_lines)) if current is not None else []
                pending_key = ""
                pending_value_lines = []
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            current = result.setdefault(section, {})
            continue
        if current is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value == "[":
            pending_key = key
            pending_value_lines = [value]
            continue
        if value.lower() in {"true", "false"}:
            current[key] = value.lower() == "true"
            continue
        try:
            current[key] = ast.literal_eval(value)
        except Exception:
            current[key] = value.strip('"')
    return result
