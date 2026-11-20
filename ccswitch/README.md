# CC Switch 用量迁移

> 跨机器迁移 CC Switch (`~/.cc-switch/cc-switch.db`) 的**调用用量记录**，不迁移会话记录。

## 背景

CC Switch 把各 AI 编程助手（Claude / Codex / Gemini / OpenCode / Grok Build / Pi / OMP / MCode / Antigravity / Kimi / Copilot / WorkBuddy / DSH / Hermes / Crush / Picoclaw 等）的 token 用量统一汇总到 `~/.cc-switch/cc-switch.db`。换机、重装或想把旧机的用量历史带到新机器时，需要把调用记录搬过去。

本工具做的是：**只搬"调用记录"（`proxy_request_logs` + 日汇总 `usage_daily_rollups` + 去重账本 `session_usage_dedup`），不搬"会话文件"本身（`session_log_sync` 游标表也不搬，因为它绑定本机绝对路径，搬过去无意义）**。

- 迁移完成后，新机器上 CC Switch 的用量面板会显示旧机器积累的历史用量
- 各 agent 的原始会话日志文件（如 `~/.dsh/sessions/**/session.v4.jsonl.zstd`、`~/.pi/agent/sessions/**/*.jsonl`）不需要搬——CC Switch 的用量面板只读 DB，不读这些文件
- 如果希望新机器后续也继续从这些会话文件里增量同步用量，请在新机器上安装各 agent 工具，CC Switch 会自动检测并同步

## 数据模型（源码知识点）

### 1. 核心 3 张表

`cc-switch.db` 的用量数据分 3 层，全部要迁移：

| 表 | 作用 | 主键 | 迁移时冲突策略 |
|----|------|------|--------------|
| `proxy_request_logs` | 逐次调用记录（含 `input_tokens`/`output_tokens`/成本/延迟） | `request_id` | `INSERT OR IGNORE`（已有则跳过） |
| `usage_daily_rollups` | 按 (日期, app_type, provider_id, model, request_model, pricing_model) 聚合 | 复合主键 | `INSERT OR IGNORE`（保留目标端累加值） |
| `session_usage_dedup` | 去重账本（记录哪些 `request_id`/`semantic_id` 已处理过） | `(data_source, request_id)` | `INSERT OR IGNORE`（已有则跳过） |

**为什么不搬 `session_log_sync`**：该表存的是"每个会话文件读到了第几字节"的增量同步游标，文件路径绑定本机（如 `dsh:C:\Users\...` vs `dsh:/home/...`），搬过去会导致新机器去读旧机器的绝对路径，找不到文件报错。新机器会从头建自己的游标。

### 2. 各 agent 的 `data_source` 常量（写库时用的标识）

源码 `src-tauri/src/services/session_usage_*.rs` 里每个 agent 模块声明一个 `data_source`，对应 `proxy_request_logs.data_source` 和 `session_usage_dedup.data_source` 的取值：

| Agent | 模块 | `data_source` | `app_type` | 占位 provider_id |
|-------|------|---------------|-----------|------------------|
| DSH (DeepSeek Harness) | `session_usage_dsh.rs` | `dsh_session` | `dsh` | `_dsh_session` / `_dsh_account_session` |
| OMP (Oh My Pi) | `session_usage_omp.rs` | `omp_session` | `omp` | `_omp_session` |
| Pi | `session_usage_pi.rs` | `pi_session` | `pi` | `_pi_session` |
| OpenCode | `session_usage_opencode.rs` | `opencode_session` | `opencode` | `_opencode_session` |
| Codex | `session_usage_codex.rs` | `codex_session` | `codex` | `_codex_session` |
| Gemini | `session_usage_gemini.rs` | `gemini_session` | `gemini` | `_gemini_session` |
| Grok Build | `session_usage_grokbuild.rs` | `grokbuild_session` | `grokbuild` | `_grokbuild_session` |
| MCode | `session_usage_mcode.rs` | `mcode_session` | `mcode` | `_mcode_session` |
| Antigravity | `session_usage_agy.rs` | `agy_session` | `agy` | `_agy_session` |
| Kimi Code | `session_usage_kimi.rs` | `kimi_session` | `kimi` | `_kimi_session` |
| GitHub Copilot CLI | `session_usage_copilot.rs` | `copilot_session` | `copilot` | `_copilot_session` |
| WorkBuddy | `session_usage_workbuddy.rs` | `workbuddy_session` | `workbuddy` | `_workbuddy_session` |

**注意**：本机 DB 里实际出现的 `data_source` 可能少于上表（取决于本机装过哪些 agent）。迁移脚本**不要按 data_source 过滤**——把所有 `proxy_request_logs` 全表搬过去最稳，让目标端自己合并去重。

### 3. 去重机制（为什么 `session_usage_dedup` 必须一起搬）

源码 `session_usage_dsh.rs` / `session_usage_pi.rs` 的导入逻辑是：

```text
对每条 usage record：
  1. INSERT OR IGNORE INTO session_usage_dedup (data_source, request_id, semantic_id, has_entry_id=1)
  2. 如果第 1 步返回 0 行（已存在）→ 跳过（去重命中）
  3. 否则 INSERT OR IGNORE INTO proxy_request_logs (request_id, ...)
```

`session_usage_dedup` 是"长期收据"：一旦某 `request_id` 被标记处理过，即使 `proxy_request_logs` 里的明细行被 `rollup_and_prune` 删除（30 天前的记录会汇总进 `usage_daily_rollups` 并删除明细），也不会被重导。

**如果只搬 `proxy_request_logs` 不搬 `session_usage_dedup`**：目标端下次启动 CC Switch 做增量同步时，对源端已经处理过的 `request_id` 没有收据，可能重复导入（取决于目标端是否也有对应的会话文件；如果目标端没有，则无事发生，但语义不一致）。

**正确做法**：3 张表一起搬，保持收据-明细-汇总三者一致。

### 4. Rollup & Prune 行为（为什么 `usage_daily_rollups` 用 REPLACE）

源码 `database/dao/usage_rollup.rs`：CC Switch 会周期性把 30 天前的 `proxy_request_logs` 明细汇总进 `usage_daily_rollups`，然后 `DELETE` 掉那些明细行。汇总公式是 **累加**（`COALESCE(old.request_count,0) + new_req`），不是覆盖。

所以源端的 `usage_daily_rollups` 数值 = 源端历史累加结果；目标端如果已经有同一 key 的 rollup 行（比如两台机器都在跑 CC Switch、都在积累用量），**直接 REPLACE 会丢失目标端的累加**。

**推荐策略**（本 skill 采用）：
- 如果目标端 `usage_daily_rollups` 里已有同 key 的行 → **跳过**（保留目标端数值），只搬新增 key 的行
- 实现：用 `INSERT OR IGNORE`，让已有 key 保持不动；新 key 正常插入

> 这比之前写的 `INSERT OR REPLACE` 更安全——REPLACE 会把目标端同 key 的累加值冲掉。

## 前置条件

| 项 | 要求 |
|----|------|
| 源端（旧机器） | 有 `~/.cc-switch/cc-switch.db`；`sqlite3` 可用（`apt install sqlite3` / `brew install sqlite` / Windows 装 SQLite CLI） |
| 目标端（新机器） | 有 `~/.cc-switch/cc-switch.db`（CC Switch 已安装并跑过一次）；CC Switch 进程**已停止**（有 `.db-wal`/`.db-shm` 时进程还开着，必须先关）；`sqlite3` 可用 |
| 网络 | 源端能 `scp` 到目标端（或能经中转拷贝） |
| 备份 | **两端 DB 都必须先备份**（下面脚本自动做） |

## 命令

### 源端：导出

```bash
cd ~/.cc-switch   # Windows: C:\Users\<user>\.cc-switch

# 1) 备份
cp cc-switch.db "cc-switch.db.bak.$(date +%Y%m%d-%H%M%S)"

# 2) 导出 3 张表（全量 CSV，带表头）
sqlite3 cc-switch.db \
  ".headers on" ".mode csv" \
  "SELECT * FROM proxy_request_logs;" > usage_proxy_request_logs.csv
sqlite3 cc-switch.db \
  ".headers on" ".mode csv" \
  "SELECT * FROM usage_daily_rollups;" > usage_daily_rollups.csv
sqlite3 cc-switch.db \
  ".headers on" ".mode csv" \
  "SELECT * FROM session_usage_dedup;" > usage_session_usage_dedup.csv

# 3) 打包并传送到目标端
tar czf ccswitch_usage_migrate.tgz \
  usage_proxy_request_logs.csv \
  usage_daily_rollups.csv \
  usage_session_usage_dedup.csv
scp ccswitch_usage_migrate.tgz <target-host>:/tmp/

# 4) 对账（确认行数）
wc -l usage_proxy_request_logs.csv usage_daily_rollups.csv usage_session_usage_dedup.csv
```

### 目标端：导入

```bash
cd ~/.cc-switch

# 1) 停 CC Switch（有 -wal/-shm 就说明进程开着）
pkill -f cc-switch 2>/dev/null; sleep 1

# 2) 备份
cp cc-switch.db "cc-switch.db.bak.$(date +%Y%m%d-%H%M%S)"

# 3) 解包
tar xzf /tmp/ccswitch_usage_migrate.tgz -C /tmp

# 4) 导入
sqlite3 cc-switch.db <<'SQL'
.mode csv
.headers on

-- session_usage_dedup（去重收据，先导，主键 (data_source, request_id)）
CREATE TEMP TABLE dedup_in AS SELECT * FROM session_usage_dedup;
.import /tmp/usage_session_usage_dedup.csv dedup_in
INSERT OR IGNORE INTO session_usage_dedup SELECT * FROM dedup_in;
SELECT 'dedup: ' || (SELECT COUNT(*) FROM dedup_in) || ' rows considered, '
     || (SELECT COUNT(*) FROM session_usage_dedup) || ' total now';

-- proxy_request_logs（明细调用记录，主键 request_id，已存在则跳过）
CREATE TEMP TABLE logs_in AS SELECT * FROM proxy_request_logs;
.import /tmp/usage_proxy_request_logs.csv logs_in
INSERT OR IGNORE INTO proxy_request_logs SELECT * FROM logs_in;
SELECT 'logs: ' || (SELECT COUNT(*) FROM logs_in) || ' rows considered, '
     || (SELECT COUNT(*) FROM proxy_request_logs) || ' total now';

-- usage_daily_rollups（日汇总，主键 6 元组，已存在则跳过以保留目标端累加值）
CREATE TEMP TABLE rollups_in AS SELECT * FROM usage_daily_rollups;
.import /tmp/usage_daily_rollups.csv rollups_in
INSERT OR IGNORE INTO usage_daily_rollups SELECT * FROM rollups_in;
SELECT 'rollups: ' || (SELECT COUNT(*) FROM rollups_in) || ' rows considered, '
     || (SELECT COUNT(*) FROM usage_daily_rollups) || ' total now';

DROP TABLE dedup_in;
DROP TABLE logs_in;
DROP TABLE rollups_in;
SQL

# 5) checkpoint WAL（让 -wal 文件归位）
sqlite3 cc-switch.db "PRAGMA wal_checkpoint(TRUNCATE);"

# 6) 校验
sqlite3 cc-switch.db "SELECT data_source, COUNT(*) FROM proxy_request_logs GROUP BY data_source;"
sqlite3 cc-switch.db "SELECT app_type, COUNT(*), MIN(date), MAX(date) FROM usage_daily_rollups GROUP BY app_type;"
```

重启 CC Switch，用量面板应显示源端的历史用量。

## 校验（导入后必跑）

```bash
# 1) 两端 dedup 与 logs 的一致性（dedup 行数应 >= logs 行数，差额 = 已被 rollup 剪掉的明细）
sqlite3 cc-switch.db "
SELECT
  (SELECT COUNT(*) FROM session_usage_dedup) AS dedup,
  (SELECT COUNT(*) FROM proxy_request_logs) AS logs;
"

# 2) 确认没有"有 log 无 dedup 收据"的孤儿行（正常应为 0）
sqlite3 cc-switch.db "
SELECT COUNT(*) FROM proxy_request_logs p
WHERE NOT EXISTS (
  SELECT 1 FROM session_usage_dedup d
  WHERE d.request_id = p.request_id AND d.data_source = p.data_source
);
"
# 期望：0

# 3) 各 data_source 分布（对比源端，确认搬全了）
sqlite3 cc-switch.db "SELECT data_source, COUNT(*) FROM proxy_request_logs GROUP BY data_source ORDER BY 2 DESC;"
```

## 失败模式对照表

| 现象 | 原因 | 处理 |
|------|------|------|
| 导入时报 `database is locked` | CC Switch 进程还开着 | `pkill -f cc-switch` 后重试；确认 `.db-wal` `.db-shm` 消失或变小 |
| `CSV 导入后行数明显偏少` | CSV 里某字段含换行（极少见，`error_message` 可能） | 用 `sqlite3` 的 `.import` 默认 CSV 模式；若异常，改用 Python `csv` 模块按行写 `INSERT` |
| 目标端 `usage_daily_rollups` 数值比源端小 | 用了 `INSERT OR IGNORE`，已有 key 保留目标端旧值 | 这是**设计行为**（保留目标端累加）；若想强制源端覆盖，把 rollups 的 `INSERT OR IGNORE` 改成 `INSERT OR REPLACE` |
| 导入后 CC Switch 用量面板没变化 | WAL 未 checkpoint，前端读旧快照 | 跑 `PRAGMA wal_checkpoint(TRUNCATE);` 并**重启** CC Switch |
| 目标端出现"重复计数" | 目标端有源端同样的会话文件，启动后增量同步把同一 `request_id` 又导入一次 | 不会发生——`session_usage_dedup` 有收据，二次导入被跳过 |
| `scp` 中断 | 网络抖动 | 重传 tgz 即可，导入是幂等的 |

## 回滚

目标端：
```bash
cd ~/.cc-switch
ls -lt cc-switch.db.bak.* | head -1   # 找最新备份
cp <备份文件> cc-switch.db
pkill -f cc-switch 2>/dev/null
sqlite3 cc-switch.db "PRAGMA wal_checkpoint(TRUNCATE);"
```
回滚到备份即恢复到导入前状态。

## 硬约束（给 AI 跑时遵守）

1. **操作前必须备份两端 DB**，备份文件留在 `~/.cc-switch/` 目录（不要放 `/tmp`，重启会丢）。
2. **不搬 `session_log_sync`**。它是本机路径游标，搬过去只会导致新机器去读旧机器绝对路径。
3. **不搬** `providers` / `settings` / `profiles` / `prompts` / `mcp_servers` / `skills` 等配置表——本 skill 只管用量。
4. `usage_daily_rollups` 默认用 `INSERT OR IGNORE`（保留目标端累加值）；用户明确要求"源端覆盖目标端"时才换 `INSERT OR REPLACE`。
5. 目标端导入前必须确认 CC Switch 进程已停止（`lsof cc-switch.db` 无输出 / `.db-wal` 不在增长）。
6. 导入后必须跑"校验"节的 3 条 SQL，确认无孤儿行、数据完整，再重启 CC Switch。
