# SKILLS.md — PicoClaw Chat Export (v2)

**给 AI 助手读的操作手册。** 这份文件描述如何从 PicoClaw（sipeed 的轻量 AI agent）的会话数据导出聊天记录为统一 JSONL。人类读者请看同目录 [README.md](README.md)。

## 能力

从 PicoClaw 的 workspace 导出**人机对话**为统一 JSONL。PicoClaw 有两种存储模式：

1. **DB 模式（优先）** — `seahorse.db`（SQLite，含完整对话 + `token_count` 统计 + FTS 索引），这是**当前实际运行模式**
2. **纯 JSONL 模式（fallback）** — `sessions/sk_v1_*.jsonl`（明文 JSONL），早期模式遗留

- Python 3.8+，**无第三方依赖**，标准库即可
- **优先读 DB**（`seahorse.db`），DB 不存在/为旧副本时才回退 JSONL

## 前置条件

先确认，不要假设：

```bash
ls -la /root/.picoclaw/workspace/sessions/seahorse.db        # DB 模式（优先）
ls -la /root/.picoclaw/workspace/sessions/*.jsonl           # 纯 JSONL 模式（fallback）
```

- **注意**：picoclaw 数据可能同时存在于 `/root/.picoclaw/workspace/` 和 `/mnt/shared/.picoclaw/workspace/`（两份拷贝，inode 不同）
- **真正运行的是 `/root/.picoclaw`**（进程 workspace 指向它，WAL 持续更新）；`/mnt/shared` 是旧副本，**别把旧副本当唯一数据源**
- 数据库含 WAL/SHM 文件（`seahorse.db-wal` / `seahorse.db-shm`），**先 checkpoint 合并再读**（或直接只读连，SQLite 自动合并）

## 命令

```bash
python3 export_picoclaw.py                    # 默认输出 ./picoclaw_chat_jsonl/（DB 优先）
python3 export_picoclaw.py --force-jsonl      # 强制纯 JSONL 模式
python3 export_picoclaw.py --out <dir>        # 自定义输出
```

- 默认 DB：`/root/.picoclaw/workspace/sessions/seahorse.db`（脚本 SEAHORSE_DB 可改）
- 默认 JSONL：`/root/.picoclaw/workspace/sessions/`（脚本 SESS_DIR 可改）
- 默认输出：`./picoclaw_chat_jsonl/`（**会先清空重建**）

## 输出契约

```
picoclaw_chat_jsonl/
└── picoclaw/
    └── {标题}_{YYYYmmdd_HHMMSS}.jsonl
```

每行结构：`{"role": "user"|"assistant", "content": "..."}`

标题取**首条 user 消息**的前 60 字（比旧版用 meta.summary 更直观）。

## 数据模型（提取依据）

### DB 模式（seahorse.db）

```
conversations (conversation_id, session_key, created_at, updated_at)
  └── messages (message_id, conversation_id, role, content, token_count, created_at)
        └── message_parts (part_id, message_id, type, text, ...)   # tool_use/tool_result
        └── summaries (summary_id, kind, depth, content, token_count, ...)  # 压缩摘要
```

- `messages.content` 是对话正文（user/assistant 直接有）
- `messages.token_count` 记录每条消息 token 数——**这是 picoclaw 唯一的 token 来源**
- `tool` 角色消息（工具调用）跳过；`tool_use`/`tool_result` 在 message_parts 里，跳过
- `summaries` 是上下文压缩摘要（token 另计，默认不导出正文）

### JSONL 模式（fallback）

```
sessions/sk_v1_{key}.jsonl     # 每行 {"role","content","created_at"}
sessions/sk_v1_{key}.meta.json # {"key","summary","count","created_at","scope",...}
```

- role 值：`user` / `assistant` / `tool`
- **无 token / usage 字段**——纯 JSONL 模式不记录 token

## Token 统计说明

DB 模式自动打印（只统计 user+assistant 对话），并**固定写入脚本同目录 `token_stats.json`**（不进入 `picoclaw_chat_jsonl/`，不污染归档）：

```bash
[+] DB 模式: 3 会话, 44 行对话, token=11,888
```

```json
// token_stats.json 固定格式
{
  "tool": "picoclaw",
  "storage_mode": "db",
  "generated_at": "YYYY-MM-DD HH:MM:SS",
  "source": "~/.picoclaw/workspace/sessions/seahorse.db",
  "sessions_total": 3, "sessions_exported": 3, "lines_exported": 44,
  "dialogue_tokens": 11888,
  "sessions_detail": [ { "session_id", "title", "created_at", "exported_lines", "dialogue_tokens" } ]
}
```

| 角色 | 消息数 | tokens |
|------|--------|--------|
| user | 8 | 101 |
| assistant | 36 | 11,787 |
| dialogue 小计 | 44 | **11,888** |
| tool | 71 | 48,644 |
| **messages 总计** | 115 | **60,532** |
| summaries | 3 | +690 |

JSONL 模式无 token 统计，只能字符估算。

## 校验

```bash
python3 -c "import json,glob; bad=0
for f in glob.glob('picoclaw_chat_jsonl/**/*.jsonl', recursive=True):
    for i,l in enumerate(open(f,encoding='utf-8'),1):
        if not l.strip(): continue
        try: json.loads(l)
        except: bad+=1; print('  坏行',f,i)
print('坏行',bad)"
```

- 文件数与 `SELECT COUNT(*) FROM conversations` 一致（当前：3）
- 每行 `role` 只能有 `user` / `assistant`

## 常见失败

| 现象 | 原因 | 处理 |
|------|------|------|
| 导出比预期少 | 只读了 `/mnt/shared` 旧副本 / 只读 JSONL | 确认脚本指向 `/root/.picoclaw` 的 seahorse.db |
| 无 token 行 | JSONL 模式（无记录） | 属正常，或切 DB 模式 |
| 标题全 untitled | meta 无 summary | v2 已改用首条 user 消息为标题 |
| checkpoint 后仍小 | WAL 主要是 FTS 索引 | 数据量以 messages 表为准 |

## 硬约束

- **不要**把导出的 `*.jsonl` 提交进 git（`.gitignore` 已覆盖）
- 会话内容可能含**敏感信息**（密码、IP、密钥），产物只留本地，**不要外传**
- 只读打开数据库，不改动原库
- **归档案的灵魂是对话内容本身**——token 统计是附带信息，不是归档目的
- 别把 `workspace/state/evolution/task-records.jsonl` 当对话（那是任务记录）