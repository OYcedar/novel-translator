---
name: novel-translator
description: 执行 Novel Translator 的 EPUB/TXT 小说翻译流程：注册小说、准备 Agent 工作区、导出/导入术语表、生成章节上下文、使用翻译记忆和批次恢复调用 OpenAI 兼容模型翻译、检查质量、人工修复、反馈反查，并导出 TXT 或 EPUB 译本。
---

# Novel Translator Skill

本 Skill 是给 Agent 使用 Novel Translator 的执行协议，不是用户说明书。所有业务数据都通过 CLI、工作区 JSON、小说源文件和用户明确提供的信息流转；不要直接改 `data/books/*/manifest.json` 或 `terms.json`，除非用户明确要求开发/排障工具本身。

## 运行边界

- `<项目目录>` 是 Novel Translator 仓库根目录。
- 默认命令前缀：`python3 main.py --agent-mode <命令> ...`
- `<小说文件>` 只支持 `.epub` 和 `.txt`。
- `<书籍ID>` 来自 `add-book --json` 的 `summary.book`。
- `<工作区>` 是临时目录，用于放术语候选、上下文和人工修复文件；不要把临时文件散落到项目根目录。
- 模型地址、API Key 和模型名只从 `setting.toml`、环境或用户本地配置读取；不要写进任务文件、报告、提交或聊天总结。
- 所有 JSON、TXT、EPUB 相关临时文件按 UTF-8 处理。
- `--json` 命令的 stdout 才是机器结果；不要把日志或进度文本当 JSON 解析。

## 按需参考资料

| 工作 | 必读参考 | 读取时机 |
| --- | --- | --- |
| 命令调用与成功判断 | `references/cli-command-contract.md` | 运行或排查任一 CLI 阶段前 |
| 术语工程 | `references/terminology-workflow.md` | 导出、填写、审查或导入术语表前 |
| 质量检查与恢复 | `references/quality-and-recovery.md` | `quality-report` 有 warning/error、需要人工修复或反馈定位时 |

只读取当前阶段需要的参考文件，不要把参考资料全文复制进模型 prompt 或交付报告。

## 主流程

1. 在 `<项目目录>` 运行 `doctor --json`，确认 CLI 和配置状态。缺 `setting.toml` 时先让用户复制 `setting.example.toml` 并填写模型配置；只做 `--dry-run` 验证时可继续。
2. 如果源文件是 EPUB，先运行 `inspect-epub --path <小说文件> --json`，查看 spine、nav/toc、重复文本和标记风险。
3. 运行 `add-book --path <小说文件> --json` 注册小说，记录 `<书籍ID>`。
4. 运行 `text-scope --book <书籍ID> --json`，确认章节数和段落数合理。
5. 运行 `export-terminology --book <书籍ID> --output-dir <工作区> --json` 导出术语候选和上下文。
6. 阅读 `terminology-workflow.md`，填写或审查 `<工作区>/terminology/glossary.json`。删除误判候选，补全人名、地名、组织名、能力名等稳定译名。
7. 运行 `import-terminology --book <书籍ID> --input <工作区>/terminology/glossary.json --json` 导入术语表。
8. 运行 `terminology-status --book <书籍ID> --json`。有冲突先修术语表；空译名 warning 必须解释，不能假装已完成。
9. 运行 `prepare-agent-workspace --book <书籍ID> --output-dir <工作区> --json` 和 `validate-agent-workspace --book <书籍ID> --workspace <工作区> --json`，确认工作区完整。
10. 运行 `audit-coverage --book <书籍ID> --json`，确认段落覆盖和可导出格式。
11. 长篇小说先运行 `summarize-context --book <书籍ID> --json`，再运行 `context-status --book <书籍ID> --json`；缺章节摘要时不要直接全量翻译。
12. 先小批量执行 `translate --book <书籍ID> --max-batches 1 --json`。如果只是验证流程，用 `--dry-run`，但不要把 dry-run 当真实译文。
13. 查看 `run-report --book <书籍ID> --json`、`failed-batches --book <书籍ID> --json`、`translation-status --book <书籍ID> --json` 和 `quality-report --book <书籍ID> --json`。有失败批次时先 `retry-failed --book <书籍ID> --json` 或导出人工修复表。
14. 小批量没有规则性事故后，再继续执行 `translate --book <书籍ID> --json`。术语表变更后可继续使用默认翻译记忆；记忆命中会检查当前术语 hash。需要强制重译时传 `--no-memory`。
15. 直到 `pending` 为 0 后，再跑 `run-report` 和 `quality-report`。未译、失败批次、源文残留、术语不一致、占位符缺失或 EPUB 标记风险必须处理或向用户说明。
16. 质量问题较少时，使用 `export-quality-fix` 导出修复表，人工填写 `translated` 后用 `import-manual-translations` 导入；坏译文用 `reset-translations` 精确清空。
17. 用户反馈漏翻/错翻时，用 `verify-feedback-text --book <书籍ID> --input <反馈文件> --json` 反查段落。
18. 导出前运行 `validate-export --book <书籍ID> --format txt|epub --json`。TXT 用 `export --book <书籍ID> --format txt --output <输出.txt> --json`；EPUB 源书才可用 `--format epub`。

## 硬门槛

- 未完成术语导入前，不启动真实模型翻译，除非用户明确要求跳过术语流程。
- `quality-report` 仍有 `terminology_mismatch` 时，不把译文称为最终版。
- `quality-report` 仍有 `placeholder_mismatch` 时，不把译文称为最终版；占位符必须原样保留。
- 长篇小说建议先生成章节上下文；`context-status` 缺摘要时，只能小批量试翻或向用户说明风险。
- 有失败批次时，先 `retry-failed` 或导出人工修复表，不要直接进入最终导出。
- 翻译记忆命中必须匹配当前术语 hash；如果用户刚改过术语，先看 `translation-memory-status`，必要时用 `--no-memory` 强制重译。
- `--dry-run` 只能用于流程验证，不代表翻译完成。
- EPUB 导出是复制原 EPUB 并替换 spine XHTML 段落文本；复杂脚注、内联富文本、图片文字和特殊排版必须提醒用户复核。
- EPUB 回写依赖导入时保存的节点定位；`validate-export` 或 `export` 出现节点定位/hash warning 时，必须说明相关段落已保留原文或需要人工复核。
- 不要直接编辑私钥、API Key、`setting.toml` 或用户小说源文件。

## 禁止做法

- 绕过 CLI 手改 `data/books/<书籍ID>/manifest.json` 来伪造翻译进度。
- 用空译文、原文复制或 dry-run 结果冒充真实译文。
- 删除术语候选只为让状态变绿；必须保留有业务意义的稳定名词。
- 质量报告有 warning 仍直接交付最终结果而不说明风险。
- 把模型密钥写进工作区、报告、提交或 README。
