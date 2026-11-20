# CC Switch 用量迁移 — AI 操作手册（SKILLS）

> 给 AI 编程助手读的操作手册。把本目录丢给任意 AI 助手，它能自己判断前置条件、跑命令、校验结果。

## 能力
- 跨机器迁移 CC Switch `~/.cc-switch/cc-switch.db` 中的**调用用量记录**（所有 agent）
- 迁移 3 张表：`proxy_request_logs` + `usage_daily_rollups` + `session_usage_dedup`
- **不迁移**：`session_log_sync`（本机路径游标）、会话文件、配置表
- 幂等：重复执行不会产生脏数据
- 自动备份两端 DB，可一键回滚

## 前置条件（按顺序自检）
| # | 检查 | 命令 | 不满足时 |
|---|------|------|---------|
| 1 | 源端有 `cc-switch.db` | `ls ~/.cc-switch/cc-switch.db` | 源端没装 CC Switch 或路径被改到别处 → 问用户真实路径 |
| 2 | 源端有 `sqlite3` | `which sqlite3` | `apt install sqlite3` / `brew install sqlite` / Windows 装 SQLite CLI |
| 3 | 目标端有 `cc-switch.db` | `ssh <target> "ls ~/.cc-switch/cc-switch.db"` | 目标端先装 CC Switch 并跑一次 |
| 4 | 目标端 CC Switch 已停止 | `ssh <target> "ls ~/.cc-switch/cc-switch.db-wal 2>/dev/null && pkill -f cc-switch"` | 有 `-wal`/`-shm` 且进程在跑 → 先 `pkill -f cc-switch` |
| 5 | 网络可达 | `scp` 或 `rsync` 能到目标端 | 不通就走中转（拷 tgz 到本地再传） |

## 输出契约
源端产出 3 个 CSV（全表导出，带表头，CSV 模式）：
```
usage_proxy_request_logs.csv     # proxy_request_logs 全表
usage_daily_rollups.csv          # usage_daily_rollups 全表
usage_session_usage_dedup.csv    # session_usage_dedup 全表
```
打包成 `ccswitch_usage_migrate.tgz`。目标端导入后：
- 目标端 `proxy_request_logs` 行数 >= 源端（旧行保留 + 新行加入，主键冲突跳过）
- 目标端 `usage_daily_rollups` 行数 >= 源端（同上）
- 目标端 `session_usage_dedup` 行数 >= 源端（同上）
- "孤儿行"（有 log 无 dedup 收据）= 0

## 命令

### 源端导出
```bash
cd ~/.cc-switch

# 1) 备份（必须留在 ~/.cc-switch/，不要放 /tmp）
cp cc-switch.db "cc-switch.db.bak.$(date +%Y%m%d-%H%M%S)"

# 2) 全表导出（3 张表）
sqlite3 cc-switch.db ".headers on" ".mode csv" \
  "SELECT * FROM proxy_request_logs;" > usage_proxy_request_logs.csv
sqlite3 cc-switch.db ".headers on" ".mode csv" \
  "SELECT * FROM usage_daily_rollups;" > usage_daily_rollups.csv
sqlite3 cc-switch.db ".headers on" ".mode csv" \
  "SELECT * FROM session_usage_dedup;" > usage_session_usage_dedup.csv

# 3) 对账（源端行数，导入后目标端应 >= 这些数）
wc -l usage_proxy_request_logs.csv usage_daily_rollups.csv usage_session_usage_dedup.csv

# 4) 打包 + 传送
tar czf ccswitch_usage_migrate.tgz \
  usage_proxy_request_logs.csv usage_daily_rollups.csv usage_session_usage_dedup.csv import_usage.sh
scp ccswitch_usage_migrate.tgz <target-host>:/tmp/
```

### 目标端导入
```bash
ssh <target-host> 'bash -s' <<'EOF'
set -euo pipefail
tar xzf /tmp/ccswitch_usage_migrate.tgz -C /tmp
chmod +x /tmp/import_usage.sh
/tmp/import_usage.sh /tmp
EOF
```

或本地有目标端 SSH 时：
```bash
scp import_usage.sh <target-host>:/tmp/
ssh <target-host> 'tar xzf /tmp/ccswitch_usage_migrate.tgz -C /tmp && /tmp/import_usage.sh /tmp'
```

### 校验（目标端，导入后必跑）
```bash
ssh <target-host> 'cd ~/.cc-switch && sqlite3 cc-switch.db "
SELECT
  (SELECT COUNT(*) FROM session_usage_dedup) AS dedup,
  (SELECT COUNT(*) FROM proxy_request_logs) AS logs,
  (SELECT COUNT(*) FROM usage_daily_rollups) AS rollups;
SELECT data_source, COUNT(*) FROM proxy_request_logs GROUP BY data_source ORDER BY 2 DESC;"'
```
期望：
- `dedup >= logs`（差额 = 已被 rollup 剪掉的明细，正常）
- `orphan_logs = 0`（脚本已打印）
- `data_source` 分布与源端一致或更多

## 数据模型（必须理解再动手）

### 为什么是 3 张表
| 表 | 角色 | 主键 | 冲突策略 |
|----|------|------|---------|
| `proxy_request_logs` | 逐次调用明细 | `request_id` | `INSERT OR IGNORE` |
| `usage_daily_rollups` | 日汇总 | (date, app_type, provider_id, model, request_model, pricing_model) | `INSERT OR IGNORE`（保留目标端累加值） |
| `session_usage_dedup` | 去重收据 | (data_source, request_id) | `INSERT OR IGNORE` |

### 为什么 `usage_daily_rollups` 默认 IGNORE 而不是 REPLACE
源码 `database/dao/usage_rollup.rs` 的汇总公式是**累加**（`COALESCE(old.request_count,0) + new_req`），不是覆盖。两台机器都跑 CC Switch、都在积累用量的场景下，源端 rollup 数值 = 源端历史累加；目标端同 key 的 rollup = 目标端历史累加。**REPLACE 会冲掉目标端累加值**。所以默认 IGNORE：已有 key 保留目标端，新 key 才插入。用户明确要求"源端覆盖目标端"时才改 REPLACE。

### 为什么必须搬 `session_usage_dedup`
源码 `session_usage_dsh.rs` / `session_usage_pi.rs` 的导入逻辑：先 `INSERT OR IGNORE INTO session_usage_dedup`，返回 0 行（已存在）就跳过，否则 `INSERT OR IGNORE INTO proxy_request_logs`。`session_usage_dedup` 是"长期收据"——即使 `proxy_request_logs` 明细行被 `rollup_and_prune` 删除（30 天前），收据还在，防止重导。只搬 logs 不搬 dedup 会导致语义不一致。

### 为什么不搬 `session_log_sync`
该表存"每个会话文件读到了第几字节"的增量游标，`file_path` 绑定本机绝对路径（如 `dsh:C:\Users\...` vs `dsh:/home/...`）。搬过去会让目标端去读源端绝对路径，找不到文件报错。目标端会从头建自己的游标。

### 各 agent 的 `data_source` 常量
| Agent | 模块 | `data_source` | `app_type` |
|-------|------|---------------|-----------|
| DSH | `session_usage_dsh.rs` | `dsh_session` | `dsh` |
| OMP | `session_usage_omp.rs` | `omp_session` | `omp` |
| Pi | `session_usage_pi.rs` | `pi_session` | `pi` |
| OpenCode | `session_usage_opencode.rs` | `opencode_session` | `opencode` |
| Codex | `session_usage_codex.rs` | `codex_session` | `codex` |
| Gemini | `session_usage_gemini.rs` | `gemini_session` | `gemini` |
| Grok Build | `session_usage_grokbuild.rs` | `grokbuild_session` | `grokbuild` |
| MCode | `session_usage_mcode.rs` | `mcode_session` | `mcode` |
| Antigravity | `session_usage_agy.rs` | `agy_session` | `agy` |
| Kimi Code | `session_usage_kimi.rs` | `kimi_session` [INFERENCE] | `kimi` |
| Copilot CLI | `session_usage_copilot.rs` | `copilot_session` [INFERENCE] | `copilot` |
| WorkBuddy | `session_usage_workbuddy.rs` | `workbuddy_session` | `workbuddy` |

**迁移脚本不按 data_source 过滤**——全表搬过去最稳，让目标端自己合并去重。

## 失败模式对照表

| 现象 | 原因 | 处理 |
|------|------|------|
| `database is locked` | CC Switch 进程还开着 | `pkill -f cc-switch`；确认 `.db-wal` 停止增长 |
| CSV 导入后行数偏少 | `error_message` 字段含换行/引号 | 用 Python `csv` 模块按行写 `INSERT` 替代 `.import` |
| 目标端 rollups 数值比源端小 | 用了 IGNORE 保留目标端旧值 | 设计行为；要覆盖就改 `INSERT OR REPLACE` |
| 导入后面板没变化 | WAL 未 checkpoint | `PRAGMA wal_checkpoint(TRUNCATE);` + 重启 CC Switch |
| 目标端出现重复计数 | 目标端有源端同样的会话文件 | 不会发生——dedup 有收据 |
| `scp` 中断 | 网络抖动 | 重传 tgz；导入幂等 |

## 硬约束
1. **操作前必须备份两端 DB**，备份留在 `~/.cc-switch/`（不放 `/tmp`）。
2. **不搬 `session_log_sync`**（本机路径游标）。
3. **不搬配置表**（providers/settings/profiles/prompts/mcp/skills）。
4. `usage_daily_rollups` 默认 `INSERT OR IGNORE`；用户明确要求覆盖才改 REPLACE。
5. 目标端导入前确认 CC Switch 已停止（`.db-wal` 不在增长）。
6. 导入后必跑校验 SQL，确认孤儿行 = 0、各 data_source 分布完整，再重启 CC Switch。

## 回滚
```bash
cd ~/.cc-switch
ls -lt cc-switch.db.bak.* | head -1   # 找导入前备份
cp <备份文件> cc-switch.db
pkill -f cc-switch 2>/dev/null
sqlite3 cc-switch.db "PRAGMA wal_checkpoint(TRUNCATE);"
```
