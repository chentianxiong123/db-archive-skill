# DSH (DeepSeek Harness) 聊天记录导出

> 从本地 `~/.dsh/` 提取 DSH 会话，解压 zstd JSONL，过滤噪声，统一导出为 `{role, content}` 格式。

## 数据位置

| 数据 | 路径 | 说明 |
|------|------|------|
| 会话事件流 | `~/.dsh/sessions/{workspace-slug}/**/session.v4.jsonl.zstd` | 每个会话一个 zstd 压缩 JSONL |
| token 统计 | `~/.dsh/storages/session_projcache/sessions/{session_id}.json` | `rows.tokenUsage` 含 input/output/cache tokens |
| 会话标题 | 事件流 `session/title` + projcache `rows.title` | 优先 provider 生成的标题 |
| workspace 注册 | `~/.dsh/storages/workspace.json` | workspace ID + 关联 session ID |

`DSH_HOME` 可通过环境变量 `DSH_HOME` 覆盖（默认 `~/.dsh`）。

## 快速开始

```bash
cd dsh && python3 export_dsh.py
```

无需第三方依赖。需要 `zstd` CLI（`zstd -d`）在 PATH 中（WSL/MSYS2/w64devkit 自带）。

## 输出格式

```
dsh_chat_jsonl/
└── {workspace-slug}/
    └── {title}_{YYYYmmdd_HHMMSS}.jsonl
```

每行：`{"role": "user"|"assistant", "content": "..."}`

## 过滤规则

| 事件 | 处理 |
|------|------|
| `user/message` + `source.kind == "user"` | ✅ 导出 |
| `user/message` + `source.kind != "user"`（runtime-context / skill-catalog / time-context / compact-checkpoint / tool-jobs / model-selection / user-approval） | ❌ 跳过 |
| `assistant/message` | ✅ 导出 |
| `system/message` / `developer/message` / `compaction/summary` / `tool/call` / `tool/result` / `step/*` / `turn/*` / `agent/inbox/spliced` 等 | ❌ 跳过 |

## 已知限制

- 无数据库，纯文件系统（zstd JSONL 事件流 + projcache JSON）
- token 统计从 projcache 读取，非会话内嵌（导出时会打印 `[tokens: in=X out=Y]`）
- 若 `zstd` CLI 不在 PATH，脚本会回退到 `zcat`；两者都没有则报 WARN 并跳过该文件

## License

[MIT](../LICENSE) © 2026 chentianxiong123
