# PicoClaw Chat Export

从 PicoClaw（sipeed 轻量 AI agent）的会话文件导出聊天记录为 JSONL 格式。

> 🤖 AI 助手请看同目录 [SKILLS.md](SKILLS.md)

## 状态

✅ 已实现。实测 3 会话 / 23 行对话 / 7.3KB JSONL。

## 快速开始

```bash
cd picoclaw
python3 export_picoclaw.py                    # 默认输出 ./picoclaw_chat_jsonl/
python3 export_picoclaw.py --out <dir>        # 自定义
```

无第三方依赖，Python 3.8+ 标准库即可。

## 导出什么

- **全会话导出**（`workspace/sessions/*.jsonl`）
- 只取 `user` / `assistant` 角色的对话，`tool` 角色（工具调用）过滤
- 标题取自 `meta.summary`（摘要首行，截断 40 字），时间戳取自 `meta.created_at`

## 输出契约

```
picoclaw_chat_jsonl/
└── picoclaw/
    └── {标题}_{YYYYmmdd_HHMMSS}.jsonl
```

每行：`{"role": "user"|"assistant", "content": "..."}`，与 opencode / trae / hermes 输出格式一致。

## 数据位置

```
~/.picoclaw/workspace/sessions/             # 或 /mnt/shared/.picoclaw/workspace/sessions/
├── sk_v1_{key}.jsonl                       # 对话: 每行 {"role","content","created_at"}
└── sk_v1_{key}.meta.json                   # 元数据: summary / count / created_at / scope
```

- 纯 JSONL 明文，无数据库、无加密
- 每行 `role`：`user` / `assistant` / `tool`（工具调用，导出时跳过）

## 已知问题

- **不记录 token 用量**——无 usage 字段，只能按字符估算（中文约 1 token/1.5 字，英文 1 token/4 字符）
- 精确统计需 tiktoken 等分词器对 content 重新计算
- `--out` 目录会先清空重建
- 别把 `workspace/state/evolution/task-records.jsonl` 当对话——那是任务记录（11 条），对话在 sessions/ 目录

## 参考

- [sipeed/picoclaw](https://github.com/sipeed/picoclaw) — 官方仓库