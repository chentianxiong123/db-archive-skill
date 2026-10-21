# Crush (Goose) Chat Export

从 crush（Block's Goose AI agent）的 SQLite 数据库导出聊天记录为 JSONL 格式。

> 🤖 AI 助手请看同目录 [SKILLS.md](SKILLS.md)

## 状态

✅ 已实现。实测 91MB 库 → 21 会话 / 13739 行 / 4.7MB JSONL，token 合计 179,500,173。

## 快速开始

```bash
cd crush
python3 export_crush.py                                  # 默认输入输出
python3 export_crush.py --db ~/.crush/crush.db --out xxx  # 自定义
```

无第三方依赖，Python 3.8+ 标准库即可。

## 特色：token 用量可直接统计

crush 的 SQLite 库 `sessions` 表自带用量统计，导出时脚本会打印：

```
[+] token 合计: prompt=170,085,457 completion=9,414,716 total=179,500,173
```

这是 agent-chat-exporter 里**唯一一个数据库自带 token 统计**的工具，其他工具（opencode/trae/hermes）都要靠估算。

## 导出什么

- **全会话导出**（goose 无子代理概念，sessions 全部是人机会话）
- **只取 `type=text` 的 part**（对话正文），thinking / toolCall / tool_result / reasoning / finish 等内部噪声全部过滤
- 文件按会话创建月份分类，标题 + 时间戳命名

## 输出契约

```
crush_chat_jsonl/
└── crush_{YYYYMM}/                # 按会话创建月份分类
    └── {会话标题}_{YYYYmmdd_HHMMSS}.jsonl
```

每行：`{"role": "user"|"assistant", "content": "..."}`，与 opencode / trae / hermes 输出格式一致。

## 数据库

```
~/.crush/crush.db  或  /mnt/shared/.crush/crush.db
```

明文 SQLite（无加密），核心表：`sessions` / `messages` / `files` / `read_files`。

- `sessions`：id、title、message_count、**prompt_tokens、completion_tokens、cost**、时间戳
- `messages`：id、session_id、role、**parts**（JSON）、model、provider、时间戳
- `parts` JSON 数组，**两种格式都要兼容**：
  - 老格式：`{"type": "text", "data": {"text": "..."}}`
  - 新格式：`{"type": "text", "text": "..."}`（平铺，大会话全都是这种）

## 踩坑记录

1. **part 双格式事故**：第一版只处理 `data.text` 嵌套格式，结果 12M+ tokens 的大会话（ssh pve / ssh fnos / 音乐等）全部静默跳过，只导出了 8 个小会话。修复：兼容平铺 `{"text":...}` 后，21 会话全导出，token 合计与库 SUM 完全一致。
2. **shutil 导入位置**：`import shutil` 误放函数内导致 `UnboundLocalError`。
3. **token 字段位置**：`prompt_tokens` 在 `sessions` 表**不在 `messages` 表**，按模型统计要在 messages 上做、总量统计在 sessions 上做。

## 已知问题

- 数据库可能很大，只读模式打开（`mode=ro`），按 session_id 索引查询
- `--out` 目录会先清空重建
- messages 的 parts 是 JSON 文本，解析失败的行直接跳过（空 parts / 异常格式）