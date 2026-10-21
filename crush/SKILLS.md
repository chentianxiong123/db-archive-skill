# SKILLS.md — Crush (Goose) Chat Export

**给 AI 助手读的操作手册。** 这份文件描述如何从 crush（Block's Goose AI agent）的 SQLite 数据库导出聊天记录为统一 JSONL。人类读者请看同目录 [README.md](README.md)。

## 能力

从 crush 的明文 SQLite 数据库（`crush.db`）导出**全部会话**的人机对话为统一 JSONL。goose/crush 是 Block 的开源 AI agent（Go 写，SQLite 存储）。

- Python 3.8+，**无第三方依赖**，标准库即可
- 数据库是明文 SQLite（无加密），无需解密
- **token 用量直接可从库中统计**（sessions 表有 `prompt_tokens` / `completion_tokens` / `cost` 列），这是本库的特色

## 前置条件

先确认，不要假设：

```bash
ls -la /mnt/shared/.crush/crush.db    # 存在才能跑
```

- crush 必须装过并跑过至少一次，否则没有数据库
- 数据库可能很大（实测 91MB → 13739 行 / 4.7MB JSONL）
- 典型路径：`~/.crush/crush.db` 或 `/mnt/shared/.crush/crush.db`

## 命令

```bash
python3 export_crush.py                                # 默认输入输出
python3 export_crush.py --db <path> --out <dir>        # 自定义
python3 export_crush.py --help
```

- 默认输入：`/mnt/shared/.crush/crush.db`（可在脚本 DEFAULT_DB 修改）
- 默认输出：`./crush_chat_jsonl/`（**会先清空重建**，注意别指向重要目录）
- 产物已被 `.gitignore` 的 `*chat_jsonl/` 覆盖，不会误提交

## 输出契约

```
crush_chat_jsonl/
└── crush_{YYYYMM}/                  # 按会话创建月份分类
    └── {会话标题}_{YYYYmmdd_HHMMSS}.jsonl
```

每行结构：`{"role": "user"|"assistant", "content": "..."}`

只取 `type=text` 的 part。thinking 思考、toolCall 工具调用、tool_result 工具结果、reasoning、finish 全部不产出。

## 数据模型（提取依据）

```
sessions (id, title, message_count, prompt_tokens, completion_tokens, cost)
    └── messages (id, session_id, role, parts, model, provider, created_at)
```

- `messages.parts` 是 JSON 数组，每个 part 两种格式：
  - 老格式：`{"type": "text", "data": {"text": "..."}}`
  - 新格式（平铺）：`{"type": "text", "text": "..."}`
- **脚本必须兼容两种格式**——只处理 `data.text` 会漏掉所有新格式会话（实测事故：大会话 12M+ tokens 全部静默跳过）
- `role` 值：`user` / `assistant`（对话）/ `tool` / `toolResult`（工具，跳过）

## Token 统计

库内 `sessions` 表自带用量统计，导出脚本末尾会打印，并**固定写入脚本同目录 `token_stats.json`**（不进入 `crush_chat_jsonl/`，不污染归档）：

```bash
[+] token 合计: prompt=170,085,457 completion=9,414,716 total=179,500,173
```

需要单独统计可用：

```bash
sqlite3 crush.db "SELECT SUM(prompt_tokens), SUM(completion_tokens), SUM(cost) FROM sessions"
```

## 校验

```bash
python3 -c "import json,glob; bad=0
for f in glob.glob('crush_chat_jsonl/**/*.jsonl', recursive=True):
    for i,l in enumerate(open(f,encoding='utf-8'),1):
        if not l.strip(): continue
        try: json.loads(l)
        except: bad+=1; print('  坏行',f,i)
print('坏行',bad)"
```

- 会话数与 `SELECT COUNT(*) FROM sessions` 一致（当前数据：21）
- 每行 `role` 只能有 `user` / `assistant` 两种值
- token 合计必须与 sessions 表 SUM 一致

```json
// token_stats.json 固定格式
{
  "tool": "crush",
  "generated_at": "YYYY-MM-DD HH:MM:SS",
  "source": "/root/.crush/crush.db",
  "sessions_total": 21, "sessions_exported": 21, "lines_exported": 13739,
  "prompt_tokens": 170085457, "completion_tokens": 9414716, "total_tokens": 179500173,
  "daily_tokens": [ { "date": "YYYY-MM-DD", "prompt_tokens": N, "completion_tokens": N,
                      "total_tokens": N } ],
  "sessions_detail": [ { "session_id", "title", "created_at", "exported_lines",
                         "prompt_tokens", "completion_tokens", "total_tokens" } ]
}
```

- `daily_tokens`：按天聚合（token 在 sessions 表按会话记，**按会话 created_at 归天**，非消息级）
- daily 数组按日期升序，便于画折线图 / 趋势分析

## 常见失败

| 现象 | 原因 | 处理 |
|------|------|------|
| 导出 0 行 / 会话缺失 | 只处理了 `data.text` 嵌套格式，新平铺格式被跳过 | 确认脚本兼容 `{"text":...}` 平铺 part |
| token 合计为 0 | 会话全是工具会话无 text part | 属正常（有 21 会话，导出 21 个文件） |
| `UnboundLocalError: shutil` | `import shutil` 误放在函数内 | 移到文件顶部 |
| 查询超时 | 大库全表扫 | 按 session_id 索引查询，勿全表 json_extract |

## 硬约束

- **不要**把导出的 `*.jsonl` 提交进 git（`.gitignore` 已覆盖，别手动 `git add -f`）
- 会话内容可能含**敏感信息**（密码、密钥、IP、路径），产物只留在本地，**不要外传**
- 本工具仅限处理**用户自己设备上、自己账号下**的数据
- 只读模式打开数据库（`mode=ro`），不会改动原库
- crush.db 里每条消息还含 thinking / toolCall / tool_result 等内部数据，**默认不导出**；如需原始全量，另写脚本按 parts 全量 dump