# DSH 聊天记录导出 — AI 操作手册（SKILLS）

> 给 AI 编程助手读的操作手册。把本目录丢给任意 AI 助手，它能自己判断前置条件、跑命令、校验结果。

## 能力
- 从本地 `~/.dsh/` 提取 DSH (DeepSeek Harness) 全部会话，解压 zstd JSONL 事件流，过滤噪声，统一导出为 `{role, content}` JSONL
- 附带 token 统计（input/output/cache）
- 输出格式与 hermes/opencode/crush/picoclaw 一致，可跨工具对比

## 前置条件（按顺序自检）
| # | 检查 | 命令 | 不满足时 |
|---|------|------|---------|
| 1 | `~/.dsh/sessions/` 存在 | `ls ~/.dsh/sessions/` | 没装 DSH 或 DSH_HOME 被改到别处 → 问用户真实路径，可设 `DSH_HOME` 环境变量 |
| 2 | `zstd` CLI 可用 | `which zstd` | `apt install zstd` / `brew install zstd` / Windows 装 w64devkit 或 MSYS2 |
| 3 | `python3` 可用 | `which python3` | 安装 Python 3.8+ |

## 输出契约
```
dsh_chat_jsonl/
└── {workspace-slug}/
    └── {title}_{YYYYmmdd_HHMMSS}.jsonl
```
- 每行 `{"role": "user"|"assistant", "content": "..."}`
- `role` 只允许 `user` / `assistant`
- `content` 是清洗后的人机对话文本（不含工具调用、思考过程、系统提示、压缩摘要）

## 命令
```bash
cd dsh && python3 export_dsh.py
```

## 数据模型（必须理解再动手）

### 会话事件流（zstd JSONL）
每个 `session.v4.jsonl.zstd` 是一个事件流，事件类型：

| 事件 | 说明 | 导出？ |
|------|------|--------|
| `session` | 会话元数据（id, createdAt, cwd, agentPreset） | 提取 createdAt + cwd |
| `session/title` | 会话标题（source.kind: provider/fallback） | 提取标题，优先 provider |
| `user/message` | 用户消息（`source.kind` 区分真用户 vs 注入） | 只导 `kind == "user"` |
| `assistant/message` | 助手回复 | ✅ 全导 |
| `system/message` | 系统提示 | ❌ |
| `developer/message` | 开发者注入 | ❌ |
| `compaction/summary` | 上下文压缩摘要 | ❌ |
| `tool/call` + `tool/result` | 工具调用 | ❌ |
| `step/start` + `step/end` | 步骤标记 | ❌ |
| `turn/start` + `turn/end` | 轮次标记 | ❌ |
| `agent/inbox/spliced` | 代理收件箱注入 | ❌ |
| 其他 | permission/sandbox/model-selection/request 等 | ❌ |

### `user/message` 的 `source.kind` 分类
| kind | 说明 | 导出？ |
|------|------|--------|
| `user` | 真用户输入 | ✅ |
| `runtime-context` | 运行时快照（文件策略、沙箱等） | ❌ |
| `skill-catalog` | 技能目录注入 | ❌ |
| `time-context` | 时间快照 | ❌ |
| `compact-checkpoint` | 压缩检查点 | ❌ |
| `tool-jobs` | 工具任务注入 | ❌ |
| `model-selection` | 模型选择注入 | ❌ |
| `user-approval` | 审批注入 | ❌ |

### token 统计（projcache）
`~/.dsh/storages/session_projcache/sessions/{session_id}.json`：
- `rows.tokenUsage.val.totals`: `uncachedInputTokens` + `cacheReadTokens` = 总 input tokens；`outputTokens` = 总 output
- `rows.sessionStats.val`: turns, steps, llmMs, ttftMs, decodeMs
- `rows.title.val`: 会话标题（可能与事件流标题不同）

## 校验（导出后必跑）
```bash
# 1. JSON 合法性
python3 -c "import json,glob
bad=0
for f in glob.glob('dsh_chat_jsonl/**/*.jsonl', recursive=True):
    for i,l in enumerate(open(f,encoding='utf-8'),1):
        if not l.strip(): continue
        try: json.loads(l)
        except: bad+=1; print('坏行',f,i)
print('坏行',bad)"

# 2. 会话数对账
find ~/.dsh/sessions -name 'session.v4.jsonl.zstd' | wc -l   # 应等于导出 JSONL 文件数
find dsh_chat_jsonl -name '*.jsonl' | wc -l

# 3. role 唯一性
grep -oh '"role": "[^"]*"' dsh_chat_jsonl/*/*.jsonl | sort | uniq -c
# 只允许 user / assistant

# 4. 敏感信息提醒
grep -rl "password\|api-key\|BEGIN.*PRIVATE" dsh_chat_jsonl/ | head
```

## 失败模式对照表
| 现象 | 原因 | 处理 |
|------|------|------|
| `WARN: cannot decompress` | `zstd` 和 `zcat` 都不在 PATH | 装 zstd（`apt install zstd` / `choco install zstd`） |
| 会话数对不上 | 部分 zstd 文件损坏或为空 | 检查 `ls -la ~/.dsh/sessions/**/*.zstd`，损坏文件跳过不影响其他 |
| 全部文件名带 `_unknown` | `createdAt` 字段缺失或 `datetime` 导入失败 | 检查 `python3 -c "from datetime import datetime"` 是否报错 |
| token 统计全 0 | projcache 文件不存在或 session_id 不匹配 | 正常——只有跑过 LLM 的会话才有 token 数据 |
| 某会话 0 行（empty） | 只有注入消息没有真用户/助手文本 | 正常——纯工具调用会话 |

## 硬约束
1. **只读**——导出器不修改 `~/.dsh/` 下任何文件
2. `role` 只有 `user` / `assistant`，工具调用、系统提示、压缩摘要全部不进输出
3. 输出目录 `dsh_chat_jsonl/` 在 `.gitignore` 里（不提交）
4. 敏感信息（API key、密码）可能出现在对话内容中——导出后提醒用户不要外传
