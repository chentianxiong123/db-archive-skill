#!/usr/bin/env python3
"""Insert picoclaw token usage into cc-switch usage_daily_rollups (aggregation layer).

学 picoclaw/trae 的插入方式: 直接写聚合表, 不走 proxy_request_logs 明细。
幂等: INSERT OR REPLACE, 可重复跑。

用法:
  python3 insert_picoclaw_to_ccswitch.py                 # 读 ~/.cc-switch/cc-switch.db (本机)
  python3 insert_picoclaw_to_ccswitch.py --db /path/cc-switch.db --dry-run

数据源: seahorse.db (messages.token_count/created_at/model_name)
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

DEFAULT_PICOCLAW_DB = os.path.expanduser("~/.picoclaw/workspace/sessions/seahorse.db")
DEFAULT_CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ccswitch_picoclaw_insert.sql")

def sql_str(s):
    return "'" + s.replace("'", "''") + "'"

def build_insert_sql(picoclaw_db, ccswitch_db):
    conn = sqlite3.connect(f"file:{picoclaw_db}?mode=ro", uri=True)
    cur = conn.cursor()

    # 按会话聚合: 每个会话的 model 取其中 assistant 的 model,
    # token 汇总全部消息(含 tool/user, 它们是同一会话的组成部分), request = assistant 消息数
    rows = cur.execute(
        "SELECT c.conversation_id, substr(c.created_at,1,10), "
        "       (SELECT COALESCE(model_name,'') FROM messages m2 "
        "        WHERE m2.conversation_id=c.conversation_id AND m2.role='assistant' "
        "        AND m2.model_name IS NOT NULL AND m2.model_name != '' LIMIT 1), "
        "       SUM(COALESCE(m.token_count,0)), "
        "       SUM(CASE WHEN m.role='assistant' THEN 1 ELSE 0 END) "
        "FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.conversation_id "
        "GROUP BY c.conversation_id"
    ).fetchall()

    agg = {}  # (day, model) -> dict
    for cid, day, model, total_tok, asst_cnt in rows:
        if not day:
            continue
        key = (day, model or "unknown")
        e = agg.setdefault(key, {"req": 0, "succ": 0, "in": 0, "out": 0})
        e["req"] += asst_cnt or 0
        e["succ"] += asst_cnt or 0
        e["in"] += total_tok or 0
    conn.close()

    lines = []
    for (day, model), e in sorted(agg.items()):
        lines.append(
            "INSERT OR REPLACE INTO usage_daily_rollups "
            "(date, app_type, provider_id, model, request_model, pricing_model, "
            "request_count, success_count, input_tokens, output_tokens, "
            "cache_read_tokens, cache_creation_tokens, total_cost_usd, avg_latency_ms, "
            "input_token_semantics) VALUES "
            f"('{day}', 'picoclaw', 'picoclaw', {sql_str(model)}, '', '', "
            f"{e['req']}, {e['succ']}, {e['in']}, {e['out']}, "
            f"0, 0, '0', 0, 0);"
        )
    return lines, agg

def main():
    ap = argparse.ArgumentParser(description="把 picoclaw token 用量插入 cc-switch 聚合表")
    ap.add_argument("--db", default=DEFAULT_PICOCLAW_DB, help="seahorse.db 路径")
    ap.add_argument("--ccswitch", default=DEFAULT_CCSWITCH_DB, help="cc-switch.db 路径")
    ap.add_argument("--out", default=DEFAULT_OUT, help="SQL 输出文件")
    ap.add_argument("--dry-run", action="store_true", help="只生成 SQL 不执行")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[-] seahorse.db 不存在: {args.db}")
        sys.exit(1)
    if not os.path.exists(args.ccswitch) and not args.dry_run:
        print(f"[-] cc-switch.db 不存在: {args.ccswitch}")
        sys.exit(1)

    lines, agg = build_insert_sql(args.db, args.ccswitch)
    print(f"[+] 生成 {len(lines)} 条 INSERT (按 天×模型)")
    for (day, model), e in sorted(agg.items()):
        print(f"    {day} {model}: {e['req']} req, in={e['in']:,}")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("-- picoclaw token usage -> cc-switch usage_daily_rollups\n")
        f.write(f"-- generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n".join(lines) + "\n")
    print(f"[+] SQL 已写: {args.out}")

    if args.dry_run:
        print("[*] dry-run 模式, 未写库")
        return

    conn = sqlite3.connect(args.ccswitch)
    cur = conn.cursor()
    for line in lines:
        cur.execute(line)
    conn.commit()
    conn.close()
    print(f"[+] 已插入 {len(lines)} 行 -> {args.ccswitch}")

if __name__ == "__main__":
    main()