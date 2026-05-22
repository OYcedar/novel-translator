# CLI 命令契约

所有命令在 `<项目目录>` 执行，默认前缀：

```bash
python3 main.py --agent-mode <命令> ...
```

需要机器读取时加 `--json`。命令返回 JSON 的通用外层字段为：

- `status`: `ok`、`warning` 或 `error`
- `warnings`: 可继续但必须解释的风险
- `summary`: 当前阶段摘要
- `details`: 明细，可能较长
- `errors`: 失败时出现

## 环境与注册

| 命令 | 用途 | 成功判断 | 失败处理 |
| --- | --- | --- | --- |
| `doctor --json` | 检查项目根目录、配置文件和 Python 版本 | `status` 不是 `error` | 缺 `setting.toml` 时复制示例并填写；仅 dry-run 可继续 |
| `add-book --path <小说文件> --json` | 注册 EPUB/TXT 小说 | `summary.book` 可用于后续命令 | 修路径或格式后重跑 |
| `add-book --path <小说文件> --title <标题> --id <ID> --json` | 用指定标题和 ID 注册小说 | `summary.book` 等于规范化 ID | ID 冲突时确认是否覆盖当前本地缓存 |
| `list --json` | 列出已注册小说 | `details.books` 可读 | 未注册时先 `add-book` |
| `text-scope --book <书籍ID> --json` | 查看章节和段落范围 | 段落数合理 | EPUB 章节缺失时先检查源文件 |

## 术语

| 命令 | 用途 | 成功判断 | 失败处理 |
| --- | --- | --- | --- |
| `export-terminology --book <书籍ID> --output-dir <工作区> --json` | 导出术语候选和上下文 | `details.glossary` 与 `details.contexts` 文件存在 | 删除不完整工作区后重跑 |
| `import-terminology --book <书籍ID> --input <glossary.json> --json` | 导入审查后的术语表 | `status` 为 `ok`，`summary.term_count` 可解释 | 修空 source、重复冲突或 JSON 结构后重跑 |
| `terminology-status --book <书籍ID> --json` | 查看术语表状态 | 无 `errors` | 冲突必须修；空译名 warning 必须解释 |

术语表输入必须是数组，或包含 `terms` 数组的对象。每项建议包含 `source`、`target`、`category`、`note`、`occurrences`、`sample_ids`。

## 翻译与检查

| 命令 | 用途 | 成功判断 | 失败处理 |
| --- | --- | --- | --- |
| `translate --book <书籍ID> --max-batches 1 --json` | 小批量真实翻译 | 命令正常结束，pending 下降 | 看质量报告，不盲目全量 |
| `translate --book <书籍ID> --json` | 继续翻译所有未译段落 | pending 持续下降 | 停滞时检查模型配置、术语或手动处理 |
| `translate --book <书籍ID> --dry-run --json` | 不调用模型，把原文写入译文字段验证流程 | 只用于测试流程 | 不得当真实译文交付 |
| `translation-status --book <书籍ID> --json` | 查看总数、已译、待译和进度 | 数量可解释 | pending 不下降时排查翻译请求 |
| `quality-report --book <书籍ID> --json` | 检查未译、源文残留和术语不一致 | 最终交付前应为 `ok` | 按 details 修译文或术语 |

## 导出

| 命令 | 用途 | 成功判断 | 失败处理 |
| --- | --- | --- | --- |
| `export --book <书籍ID> --format txt --output <文件> --json` | 导出 TXT 译本 | 输出文件存在 | 路径不可写时换输出路径 |
| `export --book <书籍ID> --format txt --output <文件> --bilingual --json` | 导出双语 TXT | 输出文件包含原文和译文 | 仅用于校对，不一定适合发布 |
| `export --book <书籍ID> --format epub --output <文件> --json` | 导出 EPUB 译本 | 源书为 EPUB 且输出文件存在 | TXT 书不能导出 EPUB；复杂排版需人工复核 |

