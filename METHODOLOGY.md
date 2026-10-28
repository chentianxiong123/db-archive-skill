# 方法论：AI 工具本地聊天数据 → 统一 JSONL

> 本文是 db-archive-skill 的**扩展手册**——如何给新工具写导出器。已有的 6 个工具（trae/hermes/opencode/qq-phone/crush/picoclaw）都是按这套方法论做的。目标是"看到新工具的数据库，10 分钟写一个导出器"。

## 一、核心契约

所有工具最终输出**同一个格式**，这是整个项目能跨工具统一的基础：

```
{工具}_chat_jsonl/
└── {分类目录}/
    └── {会话标题}_{YYYYmmdd_HHMMSS}.jsonl
```

每行：`{"role": "user"|"assistant", "content": "..."}`

- `role` **只能**有 `user` / `assistant` 两种值（工具调用、thinking、系统消息全部不进对话）
- `content` 是**清洗后的人机对话文本**，不含工具输出、思考过程、内部标记
- 文件名规则：`safe()` 函数把非法字符替换为 `_`，时间戳统一 `YYYYmmdd_HHMMSS`

## 二、五步流程（对新工具）

### 1. 定位数据 + 识别格式

先回答三个问题：

```bash
# 数据在哪？多大？
find ~ -maxdepth 3 -name "*.db" -o -name "*session*" 2>/dev/null | head
# 是 SQLite 还是文件？加密没有？
file ~/.xxx/xxx.db
# 有哪几张表/哪些文件？
sqlite3 ~/.xxx/xxx.db ".tables"
ls ~/.xxx/workspace/sessions/
```

**分支判断**：
- 明文 SQLite → 直接读
- SQLCipher 加密 → 需要找密钥（见 trae 的 1_find_key）
- 纯 JSONL 文件 → 直接解析（如 picoclaw）
- XOR 混淆 → 需要找密钥（见 qq-phone）

### 2. 摸清表结构（会话 / 消息 / 正文）

SQLite 工具的标准三件套：

```
sessions (id, title, created_at, ...)         ← 会话级元数据
    └── messages (id, session_id, role, ...)   ← 消息级，含 role
        └── parts / content (type, data)        ← 正文，往往嵌在 JSON 里
```

写个小探针脚本打印表结构和样本：

```python
import sqlite3
conn = sqlite3.connect("file:xxx.db?mode=ro", uri=True)
for t in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
    cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})")]
    n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(t, n, cols)
```

**关键坑**：正文 part 的 JSON 可能有**多种格式**（`data.text` 嵌套 vs 平铺 `text`）——**必须都兼容**。crush 事故：只处理嵌套格式，12M+ tokens 的大会话全被静默跳过。

### 3. 过滤规则（哪些是"对话"，哪些是"噪声"）

| 内容 | 处理 |
|------|------|
| `user` / `assistant` 的 text 正文 | ✅ 导出 |
| `tool` / `toolCall` / `tool_result` 角色 | ❌ 跳过（工具调用不是对话） |
| `thinking` / `reasoning` | ❌ 跳过（思考过程） |
| `finish` / `step-start` / `step-finish` | ❌ 跳过（流程标记） |
| 上下文压缩检查点（无正文的 summary 消息） | ❌ 天然跳过（没有 text） |
| 折叠残留 `[Assistant tool call]: ...` 内嵌文本 | ⚠️ 剥离尾部（保留对话部分） |
| `diff --git` 开头的 patch 文本 | ❌ 跳过 |
| 子代理会话（parent_id 非空） | ❌ 跳过（无人机交互） |

### 4. 写导出器（模板抄 opencode/export_opencode.py）

标准骨架：

```python
import sqlite3, json, os, re, shutil
from datetime import datetime

def safe(s, maxlen=80): ...          # 文件名清洗
def fmt_ts(ts): ...                   # 时间戳格式化（兼容秒/毫秒/ISO）

def export(db_path, out_dir):
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    sessions = conn.execute("SELECT ... FROM sessions ORDER BY created_at")
    for sid, title, created, *rest in sessions:
        lines = []
        for role, parts, ts in conn.execute(
            "SELECT role, parts, created_at FROM messages WHERE session_id=? ORDER BY created_at", (sid,)):
            if role not in ('user', 'assistant'): continue
            for p in json.loads(parts):
                if p.get('type') != 'text': continue
                text = extract_text(p)   # 兼容两种格式
                if not text or text.startswith('diff --git'): continue
                lines.append(json.dumps({"role": role, "content": text}, ensure_ascii=False))
        if not lines: continue
        write_file(out_dir, safe(title), fmt_ts(created), lines)
```

要点：
- **只读模式** `mode=ro`，绝不改原库
- 每会话一个文件，按会话维度遍历
- `ensure_ascii=False` 保留中文

### 5. 写文档（README + SKILLS）

每个子目录三件套，缺一不可：

| 文件 | 读者 | 内容 |
|------|------|------|
| `export_xxx.py` | 机器 | 导出脚本（自包含默认路径） |
| `README.md` | 人类 | 状态、快速开始、数据位置、已知问题 |
| `SKILLS.md` | AI | 能力、前置条件、命令、**输出契约**、**数据模型**、校验、**失败模式对照表**、硬约束 |

SKILLS.md 是给 AI 跑的操作手册——**把子目录丢给任意 AI 编程助手，它能自己判断前置条件、跑命令、校验结果**。这是项目"可持续扩展"的关键。

## 三、验证清单（交付前必跑）

```bash
# 1. JSON 合法性
python3 -c "import json,glob; bad=0
for f in glob.glob('*_chat_jsonl/**/*.jsonl', recursive=True):
    for i,l in enumerate(open(f,encoding='utf-8'),1):
        if not l.strip(): continue
        try: json.loads(l)
        except: bad+=1; print('坏行',f,i)
print('坏行',bad)"

# 2. 会话数对账
sqlite3 xxx.db "SELECT COUNT(*) FROM sessions"    # 应与导出文件数一致

# 3. role 唯一性
grep -o '"role": "[^"]*"' *_chat_jsonl/*/*.jsonl | sort | uniq -c
# 只允许 user / assistant

# 4. 敏感信息提醒
grep -rn "password\|api-key\|BEGIN.*PRIVATE" *_chat_jsonl/ | head   # 提醒不外传
```

## 四、已支持工具的差异速查

| 工具 | 存储 | 特殊处理 | token 统计 |
|------|------|----------|-----------|
| trae | SQLCipher 4 | 三步：内存取密钥 → 解密 → 导出 | 无 |
| hermes | 明文 SQLite | 简单 | 无 |
| opencode | 明文 SQLite | 只导顶层会话 | 无 |
| qq-phone | SQLite+XOR | 两套密钥推导 | 无 |
| **crush** | 明文 SQLite | part 双格式兼容 | ✅ **库自带**（sessions.prompt_tokens/completion_tokens） |
| **picoclaw** | 纯 JSONL 文件 | 无数据库 | ❌ 无记录，需估算 |

## 五、扩展新工具的 checklist

- [ ] 找到数据库/数据文件，确认格式（SQLite? JSONL? 加密?）
- [ ] 探针脚本打印表结构和样本
- [ ] 识别会话表、消息表、正文位置
- [ ] 写导出器（模板 + 双格式兼容）
- [ ] 跑导出，对账会话数 / token 数
- [ ] 写 README.md + SKILLS.md
- [ ] 更新根 README 的工具表
- [ ] `.gitignore` 加 `*chat_jsonl/`
