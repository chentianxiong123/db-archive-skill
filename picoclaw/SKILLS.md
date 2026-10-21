# SKILLS.md — PicoClaw Chat Export

**给 AI 助手读的操作手册。** 这份文件描述如何从 PicoClaw（sipeed 的轻量 AI agent）的会话文件导出聊天记录为统一 JSONL。人类读者请看同目录 [README.md](README.md)。

## 能力

从 PicoClaw 的 workspace 会话目录导出**人机对话**为统一 JSONL。PicoClaw 的对话就是 JSONL 明文文件，无数据库、无加密。

- Python 3.8+，**无第三方依赖**，标准库即可
- 会话文件本身就是 `{role, content, created_at}` JSONL，直接清洗转换
- 会话元数据在同名 `.meta.json`（含 summary 摘要、created_at 时间戳）

## 前置条件

先确认，不要假设：

```bash
ls -la ~/.picoclaw/workspace/sessions/    # 必须存在 sessions 目录
```

- 会话目录默认：`~/.picoclaw/workspace/sessions/`，也可能在 `/mnt/shared/.picoclaw/workspace/sessions/`
- 每个会话两文件：`sk_v1_xxx.jsonl`（对话）+ `sk_v1_xxx.meta.json`（元数据）
- 未出现 `seahorse.db`（SQLite 索引，非必要）

## 命令

```bash
python3 export_picoclaw.py                    # 默认输出 ./picoclaw_chat_jsonl/
python3 export_picoclaw.py --out <dir>        # 自定义
```

- 默认输入：`/mnt/shared/.picoclaw/workspace/sessions`（可在脚本 SESS_DIR 修改）
- 默认输出：`./picoclaw_chat_jsonl/`（**会先清空重建**）

## 输出契约

```
picoclaw_chat_jsonl/
└── picoclaw/                    # 单一分类
    └── {标题}_{YYYYmmdd_HHMMSS}.jsonl
```

每行结构：`{"role": "user"|"assistant", "content": "..."}`

标题来自 `meta.summary`（摘要首行，截断 40 字），无摘要则 `untitled`。

## 数据模型（提取依据）

```
workspace/sessions/
├── sk_v1_{key}.jsonl        # 对话: 每行 {"role","content","created_at"}
└── sk_v1_{key}.meta.json    # 元数据: {"key","summary","count","created_at","scope",...}
```

- `role` 值：`user` / `assistant`（对话）/ `tool`（工具调用，**跳过**）
- `content` 是纯字符串
- 无 token / usage 字段——**PicoClaw 不记录 token 用量**，只能按字符估算（中文约 1 token/1.5 字，英文约 1 token/4 字符）

## Token 统计说明

```bash
# picoclaw 无 token 记录, 只能用字符数估算
[+] 总字符数: 7,326 → 估算 ~2,930 tokens（中英混合按 2.5 字/token）
```

如需精确统计必须用 tiktoken 等分词器对 content 重新计算。

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

- 文件数与 sessions 目录 JSONL 数一致（当前：3 会话）
- 每行 `role` 只能有 `user` / `assistant` 两种值

## 常见失败

| 现象 | 原因 | 处理 |
|------|------|------|
| 文件名全 `untitled` | meta 无 summary 字段 | 正常兜底，检查 SKILLS 标题提取逻辑 |
| 消息数比原始少 | tool 角色消息被过滤 | 属预期（工具调用是噪声） |
| 标题带 `对话摘要` | summary 首行是 markdown 标题 | 已剥掉 `#`/`*`/`-` 前缀 |

## 硬约束

- **不要**把导出的 `*.jsonl` 提交进 git（`.gitignore` 已覆盖）
- 会话内容可能含**敏感信息**，产物只留在本地，**不要外传**
- 本工具仅限处理**用户自己设备上、自己账号下**的数据
- 只读读取原始会话文件，不会改动源数据
- PicoClaw 的 `task-records.jsonl`（`workspace/state/evolution/`）是任务记录不是对话，别混淆