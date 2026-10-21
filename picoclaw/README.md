# PicoClaw Chat Export (v2)

从 PicoClaw（sipeed 轻量 AI agent）的会话数据导出聊天记录为 JSONL 格式。

> 🤖 AI 助手请看同目录 [SKILLS.md](SKILLS.md)

## 状态

✅ 已实现（v2）。实测 DB 模式 → 3 会话 / 44 行对话 / token 11,888。

## 两种存储模式

PicoClaw 先后用过两种存储，**v2 导出器同时支持**（DB 优先，JSONL fallback）：

| 模式 | 位置 | 特点 |
|------|------|------|
| **DB（优先）** | `~/.picoclaw/workspace/sessions/seahorse.db` | SQLite，含 `token_count` 统计，完整对话 |
| **纯 JSONL（fallback）** | `~/.picoclaw/workspace/sessions/*.jsonl` | 明文 JSONL，早期模式，无 token |

⚠️ **坑**：picoclaw 数据可能同时存在 `/root/.picoclaw/` 和 `/mnt/shared/.picoclaw/` 两份（inode 不同）。**真正运行的是 `/root/.picoclaw`**（进程 workspace 指向它，WAL 持续更新）；`/mnt/shared` 是旧副本。**别把旧副本当唯一数据源**。

## 快速开始

```bash
cd picoclaw
python3 export_picoclaw.py                    # DB 优先，默认输出 ./picoclaw_chat_jsonl/
python3 export_picoclaw.py --force-jsonl      # 强制纯 JSONL 模式
python3 export_picoclaw.py --out <dir>        # 自定义输出
```

无第三方依赖，Python 3.8+ 标准库即可。

## 导出什么

- **全会话导出**（`conversations` 表 / `sessions/*.jsonl`）
- 只取 `user` / `assistant` 角色的对话，`tool` 角色（工具调用）过滤
- 标题取**首条 user 消息**前 60 字（v2 改进，比旧版 meta.summary 更直观）
- DB 模式自动统计对话 token（`messages.token_count` 字段）

## 输出契约

```
picoclaw_chat_jsonl/
└── picoclaw/
    └── {标题}_{YYYYmmdd_HHMMSS}.jsonl
```

每行：`{"role": "user"|"assistant", "content": "..."}`，与 opencode / trae / hermes / crush 输出格式一致。

## 数据位置

### DB 模式（seahorse.db）

```
~/.picoclaw/workspace/sessions/seahorse.db
```

明文 SQLite，核心表：

- `conversations` — id / session_key / created_at / updated_at
- `messages` — role / content / **token_count** / created_at
- `message_parts` — tool_use / tool_result（工具细节，跳过）
- `summaries` — 上下文压缩摘要（含 token_count）

### JSONL 模式

```
~/.picoclaw/workspace/sessions/sk_v1_{key}.jsonl
~/.picoclaw/workspace/sessions/sk_v1_{key}.meta.json
```

每行 `{"role","content","created_at"}`，role 有 user / assistant / tool，**无 token 记录**。

## Token 统计

DB 模式有 `messages.token_count`，是 picoclaw 唯一精确 token 来源：

```
user:  8 条,    101 tokens
assistant: 36 条, 11,787 tokens
对话小计: 44 行, 11,888 tokens     ← 导出器默认统计这个
tool: 71 条, 48,644 tokens
总计: 115 条, 60,532 tokens
```

JSONL 模式无 token，需字符估算或 tiktoken 重算。

## 已知问题

- 两份数据拷贝（/root 运行 + /mnt/shared 旧副本），旧副本内容可能滞后
- `--out` 目录会先清空重建
- 数据库带 WAL/SHM，只读连接 SQLite 自动合并；非要旧数据快照可先 `PRAGMA wal_checkpoint(TRUNCATE)`
- 别把 `workspace/state/evolution/task-records.jsonl` 当对话——那是任务记录（11 条），对话在 sessions 目录 / seahorse.db

## 参考

- [sipeed/picoclaw](https://github.com/sipeed/picoclaw) — 官方仓库