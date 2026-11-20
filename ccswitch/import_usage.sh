#!/usr/bin/env bash
# CC Switch 用量迁移 — 目标端导入脚本（全部累加语义）
# 用法: 在目标机器上先 scp 3 个 CSV + 本脚本过去，然后 bash import_usage.sh [CSV_DIR]
# 前置: ~/.cc-switch/cc-switch.db 存在；CC Switch 进程已停止
#
# 语义（严格"只增加不覆盖"）：
#   1) session_usage_dedup   : INSERT OR IGNORE（同一笔去重收据不重复）
#   2) proxy_request_logs   : INSERT OR IGNORE（同一笔调用不重复计数）
#   3) usage_daily_rollups  : 新 key 从源端插入；同 key 把源端数值累加到目标端（旧值+新值）
#      —— 同 key 累加后，laptop 的 CC Switch 自动聚合 30 天前的明细时不会再碰这段
#         （源端 rollup 的日期范围与源端明细的日期范围不重叠），零双重计数
set -euo pipefail

DB="${CC_SWITCH_DB:-$HOME/.cc-switch/cc-switch.db}"
IN_DIR="${1:-/tmp}"
[ -f "$DB" ] || { echo "ERROR: $DB not found"; exit 1; }
for f in usage_proxy_request_logs.csv usage_daily_rollups.csv usage_session_usage_dedup.csv; do
  [ -f "$IN_DIR/$f" ] || { echo "ERROR: $IN_DIR/$f missing"; exit 1; }
done

# 检查 CC Switch 是否在跑（有 -wal/-shm 说明进程可能开着）
if [ -f "$DB-wal" ] && [ "$(stat -c%s "$DB-wal" 2>/dev/null || echo 0)" -gt 0 ] && [ -f "$DB-shm" ]; then
  echo "WARNING: $DB-wal / $DB-shm exist — CC Switch 可能还在运行"
  echo "请确认已 pkill -f cc-switch 再运行本脚本（3 秒后继续，Ctrl-C 取消）"
  sleep 3
fi

# 备份
BAK="$DB.bak.$(date +%Y%m%d-%H%M%S)"
cp "$DB" "$BAK"
echo "备份: $BAK"

# 记录"导入前目标端已有的 rollup key"，用于 3b 只累加这些旧 key（不碰 3a 刚插入的新 key）
PRE_ROLLUP_KEYS="$(sqlite3 -separator ' ' "$DB" "SELECT date, app_type, provider_id, model, request_model, pricing_model FROM usage_daily_rollups;" 2>/dev/null || true)"

# sqlite3 .import 只认"相对 CWD 的文件名"（绝对路径在本机 SQLite 上打不开）。
# 把 CSV 拷到 $DB 同目录、去掉表头行，cd 过去再用裸文件名导入，完成后清理。
DB_DIR="$(cd "$(dirname "$DB")" && pwd)"
DBDIR_STAGE="$DB_DIR/.ccswitch_import.$$"
mkdir -p "$DBDIR_STAGE"
for f in usage_proxy_request_logs.csv usage_daily_rollups.csv usage_session_usage_dedup.csv; do
  tail -n +2 "$IN_DIR/$f" > "$DBDIR_STAGE/$f"   # 去掉第一行表头
done
SQL_FILE="$(mktemp)"
cat > "$SQL_FILE" <<EOF
.mode csv

-- 1) session_usage_dedup（去重收据，先导；主键 (data_source, request_id)，冲突跳过）
CREATE TEMP TABLE dedup_in (
  data_source TEXT, request_id TEXT, semantic_id TEXT, has_entry_id INTEGER
);
.import 'usage_session_usage_dedup.csv' dedup_in
INSERT OR IGNORE INTO session_usage_dedup SELECT * FROM dedup_in;
SELECT 'dedup: ' || (SELECT COUNT(*) FROM dedup_in) || ' rows considered, '
     || (SELECT COUNT(*) FROM session_usage_dedup) || ' total now';

-- 2) proxy_request_logs（明细调用记录；主键 request_id，冲突跳过=不重复计数）
CREATE TEMP TABLE logs_in (
  request_id TEXT, provider_id TEXT, app_type TEXT, model TEXT,
  request_model TEXT, pricing_model TEXT,
  input_tokens INTEGER, output_tokens INTEGER,
  cache_read_tokens INTEGER, cache_creation_tokens INTEGER,
  input_token_semantics INTEGER,
  input_cost_usd TEXT, output_cost_usd TEXT,
  cache_read_cost_usd TEXT, cache_creation_cost_usd TEXT,
  total_cost_usd TEXT, latency_ms INTEGER, first_token_ms INTEGER,
  duration_ms INTEGER, status_code INTEGER, error_message TEXT,
  session_id TEXT, provider_type TEXT, is_streaming INTEGER,
  cost_multiplier TEXT, created_at INTEGER, data_source TEXT
);
.import 'usage_proxy_request_logs.csv' logs_in
INSERT OR IGNORE INTO proxy_request_logs SELECT * FROM logs_in;
SELECT 'logs: ' || (SELECT COUNT(*) FROM logs_in) || ' rows considered, '
     || (SELECT COUNT(*) FROM proxy_request_logs) || ' total now';

-- 3) usage_daily_rollups（日汇总；全部累加语义）
CREATE TEMP TABLE rollups_in (
  date TEXT, app_type TEXT, provider_id TEXT, model TEXT,
  request_model TEXT, pricing_model TEXT,
  request_count INTEGER, success_count INTEGER,
  input_tokens INTEGER, output_tokens INTEGER,
  cache_read_tokens INTEGER, cache_creation_tokens INTEGER,
  total_cost_usd TEXT, avg_latency_ms INTEGER,
  input_token_semantics INTEGER
);
.import 'usage_daily_rollups.csv' rollups_in

-- 3a) 新 key（目标端没有的）从源端原样插入
INSERT INTO usage_daily_rollups
  (date, app_type, provider_id, model, request_model, pricing_model,
   request_count, success_count, input_tokens, output_tokens,
   cache_read_tokens, cache_creation_tokens, total_cost_usd, avg_latency_ms,
   input_token_semantics)
SELECT * FROM rollups_in WHERE NOT EXISTS (
  SELECT 1 FROM usage_daily_rollups u WHERE u.date=rollups_in.date AND u.app_type=rollups_in.app_type AND u.provider_id=rollups_in.provider_id AND u.model=rollups_in.model AND u.request_model=rollups_in.request_model AND u.pricing_model=rollups_in.pricing_model
);

-- 3b) 同 key（导入前目标端已有的，PRE 快照）把源端数值累加上去；
--     新 key 已由 3a 插入，3b 只碰旧 key，避免"新 key 被 3a 插一次 + 3b 再加一次"
UPDATE usage_daily_rollups SET
  request_count = request_count + (SELECT COALESCE(SUM(r.request_count),0) FROM rollups_in r WHERE r.date=usage_daily_rollups.date AND r.app_type=usage_daily_rollups.app_type AND r.provider_id=usage_daily_rollups.provider_id AND r.model=usage_daily_rollups.model AND r.request_model=usage_daily_rollups.request_model AND r.pricing_model=usage_daily_rollups.pricing_model),
  success_count = success_count + (SELECT COALESCE(SUM(r.success_count),0) FROM rollups_in r WHERE r.date=usage_daily_rollups.date AND r.app_type=usage_daily_rollups.app_type AND r.provider_id=usage_daily_rollups.provider_id AND r.model=usage_daily_rollups.model AND r.request_model=usage_daily_rollups.request_model AND r.pricing_model=usage_daily_rollups.pricing_model),
  input_tokens = input_tokens + (SELECT COALESCE(SUM(r.input_tokens),0) FROM rollups_in r WHERE r.date=usage_daily_rollups.date AND r.app_type=usage_daily_rollups.app_type AND r.provider_id=usage_daily_rollups.provider_id AND r.model=usage_daily_rollups.model AND r.request_model=usage_daily_rollups.request_model AND r.pricing_model=usage_daily_rollups.pricing_model),
  output_tokens = output_tokens + (SELECT COALESCE(SUM(r.output_tokens),0) FROM rollups_in r WHERE r.date=usage_daily_rollups.date AND r.app_type=usage_daily_rollups.app_type AND r.provider_id=usage_daily_rollups.provider_id AND r.model=usage_daily_rollups.model AND r.request_model=usage_daily_rollups.request_model AND r.pricing_model=usage_daily_rollups.pricing_model),
  cache_read_tokens = cache_read_tokens + (SELECT COALESCE(SUM(r.cache_read_tokens),0) FROM rollups_in r WHERE r.date=usage_daily_rollups.date AND r.app_type=usage_daily_rollups.app_type AND r.provider_id=usage_daily_rollups.provider_id AND r.model=usage_daily_rollups.model AND r.request_model=usage_daily_rollups.request_model AND r.pricing_model=usage_daily_rollups.pricing_model),
  cache_creation_tokens = cache_creation_tokens + (SELECT COALESCE(SUM(r.cache_creation_tokens),0) FROM rollups_in r WHERE r.date=usage_daily_rollups.date AND r.app_type=usage_daily_rollups.app_type AND r.provider_id=usage_daily_rollups.provider_id AND r.model=usage_daily_rollups.model AND r.request_model=usage_daily_rollups.request_model AND r.pricing_model=usage_daily_rollups.pricing_model),
  total_cost_usd = CAST(CAST(total_cost_usd AS REAL) + (SELECT COALESCE(SUM(CAST(r.total_cost_usd AS REAL)),0) FROM rollups_in r WHERE r.date=usage_daily_rollups.date AND r.app_type=usage_daily_rollups.app_type AND r.provider_id=usage_daily_rollups.provider_id AND r.model=usage_daily_rollups.model AND r.request_model=usage_daily_rollups.request_model AND r.pricing_model=usage_daily_rollups.pricing_model) AS TEXT),
  avg_latency_ms = (avg_latency_ms * request_count + (SELECT COALESCE(SUM(r.avg_latency_ms * r.request_count),0) FROM rollups_in r WHERE r.date=usage_daily_rollups.date AND r.app_type=usage_daily_rollups.app_type AND r.provider_id=usage_daily_rollups.provider_id AND r.model=usage_daily_rollups.model AND r.request_model=usage_daily_rollups.request_model AND r.pricing_model=usage_daily_rollups.pricing_model))
WHERE (
$(if [ -n "$PRE_ROLLUP_KEYS" ]; then echo "$PRE_ROLLUP_KEYS" | awk 'NR>0 {if (NR>1) printf " OR"; printf "(date=\x27%s\x27 AND app_type=\x27%s\x27 AND provider_id=\x27%s\x27 AND model=\x27%s\x27 AND request_model=\x27%s\x27 AND pricing_model=\x27%s\x27)", $1,$2,$3,$4,$5,$6}'; else echo "1=0"; fi)
);

SELECT 'rollups: ' || (SELECT COUNT(*) FROM rollups_in) || ' rows considered, '
     || (SELECT COUNT(*) FROM usage_daily_rollups) || ' total now';

DROP TABLE dedup_in;
DROP TABLE logs_in;
DROP TABLE rollups_in;

-- 校验：孤儿行（有 log 无 dedup 收据）应为 0
SELECT 'orphan_logs (expect 0): ' || (
  SELECT COUNT(*) FROM proxy_request_logs p
  WHERE NOT EXISTS (
    SELECT 1 FROM session_usage_dedup d
    WHERE d.request_id = p.request_id AND d.data_source = p.data_source
  )
);
EOF

echo "=== 开始导入到 $DB ==="
(cd "$DB_DIR" && cp "$DBDIR_STAGE"/*.csv . && sqlite3 "$DB" < "$SQL_FILE")
rm -f "$SQL_FILE"
rm -f "$DB_DIR/usage_proxy_request_logs.csv" "$DB_DIR/usage_daily_rollups.csv" "$DB_DIR/usage_session_usage_dedup.csv"
rm -rf "$DBDIR_STAGE"

# checkpoint WAL（若存在）
sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true

echo "导入完成。校验各 data_source 分布："
sqlite3 "$DB" "SELECT data_source, COUNT(*) AS n FROM proxy_request_logs GROUP BY data_source ORDER BY 2 DESC;"
echo "重启 CC Switch 查看用量面板。回滚: cp $BAK $DB && pkill -f cc-switch"
