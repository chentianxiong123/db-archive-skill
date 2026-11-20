# omp / pi-coding-agent 聊天记录导出 — AI 操作手册（SKILLS）

> 给 AI 编程助手读的操作手册。把本目录丢给任意 AI 助手，它能自己判断前置条件、跑命令、校验结果。

## 能力
- 从本地 `~/.omp/agent/` 提取 omp (pi-coding-agent / @oh-my-pi) 全部**顶层**会话
- 读 JSONL 事件流 + `history.db` 标题（只读），过滤噪声，统一导出 `{role, content}` JSONL
- 自动跳过嵌套子代理会话、agent-internal user 消息（`attribution=agent`）、thinking / toolCall / toolResult
- 输出格式与 hermes/opencode/crush/dsh 一致，可跨工具对比

## 前置条件（按顺序自检）
| # | 检查 | 命令 | 不满足时 |
|---|------|------|---------|
| 1 | `~/.omp/agent/sessions/` 存在 | `ls ~/.omp/agent/sessions/` | 没装 omp 或 OMP_HOME 被改 → 问用户，可设 `OMP_HOME` |
| 2 | `python3` + 系统 `sqlite3` 模块 | `python3 -c "import sqlite3"` | 装 Python 3.8+ |

## 输出契约
```
omp_chat_jsonl/
└── {workspace-slug}/
    └── {title}_{YYYYmmdd_HHMMSS}.jsonl
```
- 每行 `{"role": "user"|"assistant", "content": "..."}`
- `role` 只允许 `user` / `assistant`
- `content` 是清洗后的人机对话文本

## 命令
```bash
cd omp && python3 export_omp.py
```
只读运行，不改 `~/.omp/` 下任何文件（DB 用 `?mode=ro`）。可设 `OMP_HOME` 指向别处。

## 数据模型（必须理解再动手）

### 会话事件流（JSONL）
每个顶层 `~/.omp/agent/sessions/{workspace}/**/{ts}_{session-id}.jsonl`：

| 事件 type | 说明 | 导出？ |
|------|------|--------|
| `session` | 会话元数据（id, timestamp, cwd） | 提取 |
| `title` / `title_change` | 会话标题 | 提取 |
| `message` | 消息（role + content parts） | 按 role 过滤 |
| `custom` | system-prompt-digest / tool_execution_start / session_exit / todo_hud_state | ❌ |
| `model_change` / `thinking_level_change` | 模型/思考切换 | ❌ |

### `message` 的 role + attribution
| role | attribution | 处理 |
|------|-------------|------|
| `user` | `user` | ✅ 真用户 |
| `user` | `agent` | ❌ agent 内部（IRC 父→子、steering 中断、`[Wait interrupted by message]`） |
| `assistant` | — | ✅ 只取 `text` part；`thinking`/`toolCall` 跳过 |
| `toolResult` | — | ❌ |
| `developer` | — | ❌ 系统 reminder |

content part 可能是 dict（`{type:text/thinking/toolCall}`）或**裸 string**——裸 string 的 user 消息基本是 agent-internal（已被 attribution=agent 过滤）。

### 标题优先级
`history.db session_titles` > 事件流 `title` > `session_recaps`（截断 80 字）> 会话 id。
`history.db` 是 live WAL，用 `?mode=ro`；打不开就静默回退事件流标题，不报错。

## 校验（导出后必跑）
```bash
# 1. JSON 合法性
python3 -c "import json,glob
bad=0
for f in glob.glob('omp_chat_jsonl/**/*.jsonl', recursive=True):
    for i,l in enumerate(open(f,encoding='utf-8'),1):
        if not l.strip(): continue
        try: json.loads(l)
        except: bad+=1; print('坏行',f,i)
print('坏行',bad)"

# 2. 会话数对账（顶层，排除嵌套子代理）
find ~/.omp/agent/sessions -mindepth 2 -maxdepth 2 -name '*.jsonl' | wc -l   # 源端顶层数
find omp_chat_jsonl -name '*.jsonl' | wc -l                                  # 导出数（= 顶层中有正文的）

# 3. role 唯一性
grep -oh '"role": "[^"]*"' omp_chat_jsonl/*/*.jsonl | sort | uniq -c
# 只允许 user / assistant
```

## 失败模式对照表
| 现象 | 原因 | 处理 |
|------|------|------|
| 导出数 < 源端顶层数 | 部分会话无 user/assistant 正文（纯工具 / agent-internal） | 正常，看 `(no user/assistant text)` 行 |
| 标题全 fallback 到 session id | `history.db` 被独占锁 | 正常——`?mode=ro` 打不开就回退事件流 title |
| 出现 `toolResult` / `thinking` 文本 | 过滤规则被改 | 恢复 SKILLS 里的 role/attribution 表 |
| 导出了子代理会话 | 误把嵌套 JSONL 当顶层 | `find_session_files` 只取 `-mindepth 2 -maxdepth 2`（workspace 层） |

## 硬约束
1. **只读**——所有 DB `?mode=ro`，会话 JSONL 直接读；绝不写 `~/.omp/`
2. `role` 只有 `user` / `assistant`；thinking / toolCall / toolResult / developer / agent-internal 全不进输出
3. **跳过嵌套子代理会话**（`workspace/parent-session/task.jsonl`）
4. 输出目录 `omp_chat_jsonl/` 在 `.gitignore`（不提交隐私数据）
