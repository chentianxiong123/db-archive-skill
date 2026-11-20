# omp / pi-coding-agent 聊天记录导出

> 从本地 `~/.omp/agent/` 提取 omp (pi-coding-agent / @oh-my-pi) 会话，读取 JSONL 事件流，过滤噪声，统一导出为 `{role, content}` 格式。只读，不碰 live DB。

## 数据位置

| 数据 | 路径 | 说明 |
|------|------|------|
| 会话事件流 | `~/.omp/agent/sessions/{workspace}/**/{ts}_{session-id}.jsonl` | 每个顶层会话一个 JSONL 事件流 |
| 子代理会话 | `~/.omp/agent/sessions/{workspace}/{parent-session-id}/{task}.jsonl` | 嵌套在父会话目录，**自动跳过** |
| 会话标题 | `~/.omp/agent/history.db` → `session_titles` / `session_recaps` | 只读打开（WAL 锁定时回退到事件流 title） |
| token 用量 | `~/.omp/agent/agent.db` → `model_usage`（按模型，非按会话） | 本次导出未用 |

`OMP_HOME` 可通过环境变量 `OMP_HOME` 覆盖（默认 `~/.omp`）。

## 快速开始

```bash
cd omp && python3 export_omp.py
```

无需第三方依赖（只用标准库 + 系统 `sqlite3`）。**只读**：所有 DB 用 `?mode=ro` 打开，会话 JSONL 直接读，绝不写。

## 输出格式

```
omp_chat_jsonl/
└── {workspace-slug}/
    └── {title}_{YYYYmmdd_HHMMSS}.jsonl
```

每行：`{"role": "user"|"assistant", "content": "..."}`

## 过滤规则

| 事件 / 字段 | 处理 |
|------|------|
| `message` role=`user` + `attribution=user` | ✅ 导出（真用户输入） |
| `message` role=`user` + `attribution=agent` | ❌ 跳过（agent 内部 IRC / steering / 中断，非人机对话） |
| `message` role=`assistant` 的 `text` part | ✅ 导出 |
| `message` role=`assistant` 的 `thinking` part | ❌ 跳过（思考过程） |
| `message` role=`assistant` 的 `toolCall` part | ❌ 跳过（工具调用） |
| `message` role=`toolResult` / `developer` | ❌ 跳过 |
| `custom`（system-prompt-digest / tool_execution_start / session_exit / todo_hud_state） | ❌ 跳过 |
| `model_change` / `thinking_level_change` / `title` / `title_change` / `session` | ❌ 跳过 |
| 嵌套子代理会话（`parent-session-id/task.jsonl`） | ❌ 跳过（无人机交互，按 METHODOLOGY） |

## 已知限制

- 会话数对账：源端**顶层** JSONL 105 个，其中 6 个无 user/assistant 正文（纯工具调用 / agent-internal），实际导出 99
- token 用量在 `agent.db` 里是**按模型**聚合（`model_usage`），非按会话——本次不导出 token（跟 crush 的会话级 token 不同）
- `history.db` 是 live WAL 数据库（OMP 正跑着），用 `?mode=ro` 只读打开；若被独占锁定期会静默回退到事件流 title，不影响导出

## License

[MIT](../LICENSE) © 2026 chentianxiong123
