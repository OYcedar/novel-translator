# Novel Translator 快速开始

Novel Translator 是一个模仿 A.T.T MZ 工作流的 Agent 友好型小说翻译工具，面向 `.epub` 和 `.txt` 小说文件。它会先把小说注册到本地工作区，拆成稳定段落 ID，再调用 OpenAI 兼容接口批量翻译，最后导出译文。

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
python3 main.py --agent-mode add-book --path ./novel.epub --json
python3 main.py --agent-mode list --json
python3 main.py --agent-mode text-scope --book <书籍ID> --json
python3 main.py --agent-mode translate --book <书籍ID> --max-batches 1 --json
python3 main.py --agent-mode translation-status --book <书籍ID> --json
python3 main.py --agent-mode quality-report --book <书籍ID> --json
python3 main.py --agent-mode export --book <书籍ID> --format txt --output ./translated.txt --json
python3 main.py --agent-mode export --book <书籍ID> --format epub --output ./translated.epub --json
```

排查流程可先用 `--dry-run` 不调用模型：

```bash
python3 main.py --agent-mode translate --book <书籍ID> --dry-run --json
```

## Agent 工作流建议

1. `doctor --json` 检查配置。
2. `add-book --path <小说文件> --json` 注册小说，记录返回的书籍 ID。
3. `text-scope --book <书籍ID> --json` 确认章节和段落数量。
4. `translate --book <书籍ID> --max-batches 1 --json` 先小批量试翻。
5. `translation-status --book <书籍ID> --json` 查看进度，继续执行 `translate` 直到 pending 为 0。
6. `quality-report --book <书籍ID> --json` 检查未译和源语言残留。
7. `export` 导出 TXT 或 EPUB。

## 数据位置

- 注册书籍和译文缓存：`data/books`
- 原始文件副本：`data/books/<书籍ID>/source.epub` 或 `source.txt`
- 系统提示词：`prompts/novel_translation_system.md`

## EPUB 说明

EPUB 导出会复制原始 EPUB，并替换 spine 中 XHTML 章节里的段落文本。复杂排版、脚注、图片文字和被拆成多个内联节点的段落可能需要人工复核；稳妥交付时建议同时导出 TXT 做阅读校验。
