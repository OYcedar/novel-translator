# Novel Translator 快速开始

Novel Translator 是一个参考 [A.T.T MZ](https://github.com/yexi-by/att-mz) 工作流构建的 Agent 友好型小说翻译工具，面向 `.epub` 和 `.txt` 小说文件。它会先把小说注册到本地工作区，拆成稳定段落 ID，再调用 OpenAI 兼容接口批量翻译，最后导出译文。

## 适合什么

- 翻译 EPUB 或 TXT 小说。
- 让 Agent 按命令注册书籍、查看文本范围、分批翻译、检查质量、导出成品。
- 中断后继续翻译，已完成段落会保存在 `data/books/<书籍ID>/manifest.json`。

## 准备配置

复制配置示例：

```bash
cp setting.example.toml setting.toml
```

填写 OpenAI 兼容接口：

```toml
[llm]
base_url = "https://<模型服务地址>/v1"
api_key = "<API Key>"
model = "<模型名>"
timeout = 600
```

## 常用命令

```bash
python3 main.py --agent-mode doctor --json
python3 main.py --agent-mode inspect-epub --path ./novel.epub --json
python3 main.py --agent-mode add-book --path ./novel.epub --json
python3 main.py --agent-mode list --json
python3 main.py --agent-mode text-scope --book <书籍ID> --json
python3 main.py --agent-mode analyze-book --book <书籍ID> --json
python3 main.py --agent-mode export-terminology --book <书籍ID> --output-dir ./workspace --json
python3 main.py --agent-mode import-terminology --book <书籍ID> --input ./workspace/terminology/glossary.json --json
python3 main.py --agent-mode terminology-status --book <书籍ID> --json
python3 main.py --agent-mode prepare-agent-workspace --book <书籍ID> --output-dir ./workspace --json
python3 main.py --agent-mode validate-agent-workspace --book <书籍ID> --workspace ./workspace --json
python3 main.py --agent-mode audit-coverage --book <书籍ID> --json
python3 main.py --agent-mode summarize-context --book <书籍ID> --json
python3 main.py --agent-mode context-status --book <书籍ID> --json
python3 main.py --agent-mode translation-plan --book <书籍ID> --json
python3 main.py --agent-mode translate --book <书籍ID> --max-batches 1 --workers 1 --rpm 30 --json
python3 main.py --agent-mode run-report --book <书籍ID> --json
python3 main.py --agent-mode export-run-report --book <书籍ID> --output ./run-report.md --json
python3 main.py --agent-mode failed-batches --book <书籍ID> --json
python3 main.py --agent-mode retry-failed --book <书籍ID> --json
python3 main.py --agent-mode review-translations --book <书籍ID> --mode risk --json
python3 main.py --agent-mode export-review-report --book <书籍ID> --review-id <审校ID> --output ./review.md --json
python3 main.py --agent-mode snapshot --book <书籍ID> --name before-final --json
python3 main.py --agent-mode translation-memory-status --book <书籍ID> --json
python3 main.py --agent-mode translation-status --book <书籍ID> --json
python3 main.py --agent-mode quality-report --book <书籍ID> --json
python3 main.py --agent-mode export-pending-translations --book <书籍ID> --output ./pending.json --json
python3 main.py --agent-mode export-quality-fix --book <书籍ID> --output ./quality-fix.json --json
python3 main.py --agent-mode import-manual-translations --book <书籍ID> --input ./manual.json --json
python3 main.py --agent-mode reset-translations --book <书籍ID> --input ./reset.json --json
python3 main.py --agent-mode verify-feedback-text --book <书籍ID> --input ./feedback.txt --json
python3 main.py --agent-mode export-epub-risk-report --book <书籍ID> --output ./epub-risk.md --json
python3 main.py --agent-mode run-pipeline --book <书籍ID> --json
python3 main.py --agent-mode package-delivery --book <书籍ID> --output-dir ./delivery --json
python3 main.py --agent-mode validate-export --book <书籍ID> --format epub --json
python3 main.py --agent-mode export --book <书籍ID> --format txt --output ./translated.txt --json
python3 main.py --agent-mode export --book <书籍ID> --format epub --output ./translated.epub --json
```

排查流程可先用 `--dry-run` 不调用模型：

```bash
python3 main.py --agent-mode translate --book <书籍ID> --dry-run --json
```

## Agent 工作流建议

项目内置了 Agent Skill：

```text
skills/novel-translator/SKILL.md
```

如果交给 Codex、Claude Code 或其他 Agent 执行整本翻译，让它读取这个 Skill，并按其中的命令契约完成术语、翻译、质量检查和导出流程。本仓库只提供 Skill 文件，不会自动安装到本机 Codex。

1. `doctor --json` 检查配置。
2. `add-book --path <小说文件> --json` 注册小说，记录返回的书籍 ID。
3. `text-scope --book <书籍ID> --json` 确认章节和段落数量。
4. `analyze-book --book <书籍ID> --json` 生成译前项目画像，检查对话比例、重复文本、术语密度和 EPUB 风险。
5. `export-terminology --book <书籍ID> --output-dir <工作区> --json` 导出术语候选和上下文。
6. 人工或 Agent 填写 `<工作区>/terminology/glossary.json` 里的 `target`，删除不需要的候选，统一人名、地名、组织名、能力名等译名。
7. `import-terminology --book <书籍ID> --input <工作区>/terminology/glossary.json --json` 导入术语表。
8. `terminology-status --book <书籍ID> --json` 确认没有冲突；空译名会作为 warning。
9. `prepare-agent-workspace --book <书籍ID> --output-dir <工作区> --json` 导出完整 Agent 工作区。
10. `validate-agent-workspace --book <书籍ID> --workspace <工作区> --json` 验收工作区。
11. `audit-coverage --book <书籍ID> --json` 查看覆盖范围和可导出格式。
12. 长篇小说先运行 `summarize-context --book <书籍ID> --json`，再用 `context-status` 确认章节上下文齐全。
13. `translation-plan --book <书籍ID> --json` 生成 Agent 执行计划。
14. `translate --book <书籍ID> --max-batches 1 --json` 先小批量试翻，必要时设置 `--workers`、`--rpm` 和 `--stop-on-warning`。
15. `run-report --book <书籍ID> --json` 检查批次运行记录；如有失败，先用 `retry-failed --book <书籍ID> --json` 重试。
16. `review-translations --book <书籍ID> --mode risk --json` 审校风险段落；确认修复后用 `apply-review-fixes` 导入。
17. `quality-report --book <书籍ID> --json` 检查未译、源语言残留、术语、占位符、风格和审校风险。
18. `package-delivery --book <书籍ID> --output-dir <交付目录> --json` 生成译本、报告、术语和元数据交付包。

## 术语表流程

术语表保存在：

```text
data/books/<书籍ID>/terms.json
```

导出的工作区结构：

```text
workspace/
  manifest.json
  terminology/
    glossary.json
    contexts/
      term-contexts.json
```

`glossary.json` 示例：

```json
{
  "terms": [
    {
      "source": "Alice",
      "target": "爱丽丝",
      "category": "name",
      "note": "主角",
      "occurrences": 12,
      "sample_ids": ["c0001-p00003"]
    }
  ]
}
```

翻译时，命中当前批次原文的术语会被注入模型请求的 `glossary` 字段。质量报告会检查：如果原文包含术语 `source`，译文必须包含对应 `target`。

## 长篇稳定翻译

工具会在每本书目录下维护三类长篇状态：

```text
data/books/<书籍ID>/
  memory.json
  context.json
  runs/
```

`memory.json` 是翻译记忆，按源文 hash 和当前术语 hash 复用译文；术语表变化后，旧记忆不会自动命中，避免旧译名污染新译名。可用这些命令查看或迁移：

```bash
python3 main.py --agent-mode translation-memory-status --book <书籍ID> --json
python3 main.py --agent-mode export-translation-memory --book <书籍ID> --output ./memory.json --json
python3 main.py --agent-mode import-translation-memory --book <书籍ID> --input ./memory.json --json
```

`context.json` 保存章节摘要。`summarize-context` 会优先使用当前 OpenAI 兼容配置生成模型摘要；如果缺依赖或调用失败，会 warning 并回落到抽取式摘要。`translate` 会把章节标题、章节摘要、前后段落、前文已译片段、相关术语和占位符一起发给模型。长篇小说建议先运行：

```bash
python3 main.py --agent-mode summarize-context --book <书籍ID> --json
python3 main.py --agent-mode context-status --book <书籍ID> --json
```

`runs/` 保存每次翻译运行的批次记录，包括批次 ID、段落 ID、请求时间、耗时、模型、错误和 warning。失败批次不会写入译文缓存，成功批次会增量保存。常用命令：

```bash
python3 main.py --agent-mode translate --book <书籍ID> --run-id first-pass --json
python3 main.py --agent-mode run-report --book <书籍ID> --json
python3 main.py --agent-mode failed-batches --book <书籍ID> --json
python3 main.py --agent-mode retry-failed --book <书籍ID> --json
python3 main.py --agent-mode work-records --book <书籍ID> --json
python3 main.py --agent-mode work-records --book <书籍ID> --collect-log-dir ../logs --json
```

模型输出会进行批次级校验：缺少段落 ID 会让该批失败；未知 ID、空译文、占位符缺失和术语缺失会进入 warning 或质量报告。需要完全绕过翻译记忆时，可传 `translate --no-memory`。

## 单本小说工作记录

每本小说可以有独立的过程记录目录，默认位于：

```text
../workspace/books/<书籍ID>/
  logs/
  reports/
  workspace/
  imports/
  delivery/
```

注册书籍时会自动初始化该目录。也可以手动执行：

```bash
python3 main.py --agent-mode work-records --book <书籍ID> --json
python3 main.py --agent-mode work-records --book <书籍ID> --collect-log-dir ../logs --json
python3 main.py --agent-mode work-records --book <书籍ID> --collect-file ../import_terms.json --json
```

`data/books/<书籍ID>/` 仍然保存核心翻译状态；`work-records` 目录用于收纳后台脚本、日志、外部术语表、质检报告、审校材料和交付包。

## Agent 工作区与人工修复

完整工作区会导出：

```text
workspace/
  manifest.json
  book-summary.json
  text-scope.json
  terminology/
    glossary.json
    contexts/term-contexts.json
  quality/latest-report.json
```

人工修复文件使用 JSON，不依赖 CSV/XLSX。`export-pending-translations` 导出未译段落，`export-quality-fix` 导出质量报告命中的段落；填写 `translated` 后用 `import-manual-translations` 导入。坏译文可用 `reset-translations --input reset.json` 精确清空，或明确传 `--all` 全量清空。

## 审校、快照和交付

成熟流水线会在翻译前后生成更多可追踪文件：

```text
data/books/<书籍ID>/
  analysis.json
  reviews/<审校ID>.json
  snapshots/<快照ID>/manifest.json
```

`run-pipeline` 会自动创建开始前快照，执行分析、计划、上下文检查、翻译、失败重试、质量检查和风险审校。默认不导出最终文件；需要一并导出时传 `--export txt|epub --output <文件>`。

审校默认只处理风险段落：

```bash
python3 main.py --agent-mode review-translations --book <书籍ID> --mode risk --json
python3 main.py --agent-mode export-review-report --book <书籍ID> --review-id <审校ID> --output ./review.md --json
```

审校不会直接覆盖译文。只有在审校 JSON 中填写 `approved_translation` 后，`apply-review-fixes` 才会写入，并且会拒绝未知段落、空译文和占位符缺失。

交付前硬门槛：

- `validate-export` 不能是 `error`。
- `run-report.summary.failed` 必须为 0。
- `quality-report.summary.placeholder_mismatch` 必须为 0。
- EPUB 有标记风险时，交付包必须包含 `epub-risk-report.md`。

交付包命令：

```bash
python3 main.py --agent-mode package-delivery --book <书籍ID> --output-dir ./delivery --json
```

## 占位符保护

翻译请求会把段落里的 `{name}`、`{{name}}`、`%s`、`%d`、HTML 标签和 `[#note]` 这类脚注锚点作为 `placeholders` 传给模型。译文必须原样保留这些占位符；`quality-report` 会用 `placeholder_mismatch` 报告缺失项。

## 数据位置

- 注册书籍和译文缓存：`data/books`
- 原始文件副本：`data/books/<书籍ID>/source.epub` 或 `source.txt`
- 系统提示词：`prompts/novel_translation_system.md`

## EPUB 说明

EPUB 导入会读取 OPF manifest/spine，支持 EPUB2 `toc.ncx`、EPUB3 `nav.xhtml`、`.xhtml`、`.html` 和 `.htm` 章节。导入时会为每个可翻译节点保存 `chapter_path`、`node_index`、`node_tag`、`node_id`、`node_class` 和 `text_hash`，导出时按节点定位回写，重复原文也能写回正确位置。

可先检查 EPUB 内部结构：

```bash
python3 main.py --agent-mode inspect-epub --path ./novel.epub --json
```

默认使用标准库解析；如果本机安装了可选依赖 `beautifulsoup4` / `lxml`，遇到坏 HTML 或非严格 XHTML 时会自动启用增强解析。可选安装：

```bash
python3 -m pip install ".[epub]"
```

配置项：

```toml
[epub]
parser = "auto"
include_non_linear_spine = false
preserve_outer_markup = true
warn_on_ruby = true
warn_on_duplicate_source = true
```

EPUB 导出会复制原始 EPUB，保留 CSS、图片和元数据，并替换 spine 章节中的译文节点。`ruby`、脚注链接、表格、代码块、图片文字等复杂结构会在 `quality-report` 的 `epub_markup_risk` 中提示；最终发布前建议运行 `validate-export`，并抽查 EPUB 阅读器效果。

## 特别感谢

本项目的流程设计、Agent 工作区思路和质量闭环设计参考了 [A.T.T MZ](https://github.com/yexi-by/att-mz)。特别感谢 A.T.T MZ 原作者 [yexi-by](https://github.com/yexi-by) 的开源项目与工作流启发。
