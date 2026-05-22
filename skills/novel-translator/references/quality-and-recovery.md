# 质量检查与恢复

`quality-report` 当前检查三类问题：

- `untranslated`: 段落没有译文。
- `source_residual`: 译文中仍有配置规则识别的源语言残留。
- `terminology_mismatch`: 原文命中术语表，但译文没有使用指定译名。
- `placeholder_mismatch`: 原文中的占位符没有在译文中原样保留。

## 处理顺序

1. 先处理术语表错误。术语错误会放大后续翻译问题。
2. 再处理未译段落。pending 较多时继续 `translate`；少量时可人工修复。
3. 再处理占位符缺失。占位符通常是 `{name}`、`%s`、HTML 标签或脚注锚点，必须原样保留。
4. 最后处理源文残留。确认是人名、拟声词、品牌名等应保留片段时，在术语表或配置中说明；不要全局关闭检查来掩盖漏翻。

## 常见情况

### 未译

运行：

```bash
python3 main.py --agent-mode translation-status --book <书籍ID> --json
python3 main.py --agent-mode translate --book <书籍ID> --json
```

如果 pending 不下降，检查模型配置、网络、API 返回格式和提示词。

### 术语不一致

查看 `details.terminology_mismatch`。如果是译文没有按术语表写，重译或人工修该段；如果术语表本身错误，重新导入术语表后再处理受影响译文。

### 源文残留

查看 `details.source_residual`。不要只因为某个残留看起来“像外文”就删除；先判断它是否是角色名、地名、代码、脚注锚点或 EPUB 协议片段。

## 人工修复命令

导出质量修复表：

```bash
python3 main.py --agent-mode export-quality-fix --book <书籍ID> --output <修复表.json> --json
```

填写每项 `translated` 后导入：

```bash
python3 main.py --agent-mode import-manual-translations --book <书籍ID> --input <修复表.json> --json
```

坏译文重置：

```bash
python3 main.py --agent-mode reset-translations --book <书籍ID> --input <段落ID清单.json> --json
```

用户反馈反查：

```bash
python3 main.py --agent-mode verify-feedback-text --book <书籍ID> --input <反馈文本.txt> --json
```

不要直接编辑缓存伪造状态，除非用户明确要求进行工具开发或数据抢救。

## 交付前检查

最终导出前至少运行：

```bash
python3 main.py --agent-mode translation-status --book <书籍ID> --json
python3 main.py --agent-mode terminology-status --book <书籍ID> --json
python3 main.py --agent-mode quality-report --book <书籍ID> --json
```

`quality-report` 不是 `ok` 时，可以交付校对版，但必须明确说明剩余风险。
