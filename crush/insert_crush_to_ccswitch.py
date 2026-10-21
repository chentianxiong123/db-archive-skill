#!/usr/bin/env python3
"""Insert crush/goose token usage into cc-switch usage_daily_rollups (aggregation layer).

学 picoclaw/trae 的插入方式: 直接写聚合表, 不走 proxy_request_logs 明细。
幂等: INSERT OR REPLACE, 可重复跑。

用法:
  python3 insert_crush_to_ccswitch.py                 # 读 ~/.cc-switch/cc-switch.db (本机)
  python3 insert_crush_to_ccswitch.py --db /path/cc-switch.db --dry-run

数据源: crush.db (sessions.prompt_tokens/completion_tokens + messages.model)
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

DEFAULT_CRUSH_DB = os.path.expanduser("~/.crush/crush.db")
DEFAULT_CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ccswitch_crush_insert.sql")

# 按会话取主 model: 该会话 messages 里出现最多的非空 model
def session_main_model(cur, session_id):
    rows = cur.execute(
        "SELECT model, provider, COUNT(*) FROM messages "
        "WHERE session_id=? AND model IS NOT NULL AND model != '' "
        "GROUP BY model, provider ORDER BY 3 DESC LIMIT 1",
        (session_id,),
    ).fetchall()
    if not rows:
        return None, None
    return rows[0][0], rows[0][1]

def build_insert_sql(crush_db, ccswitch_db):
    conn = sqlite3.connect(f"file:{crush_db}?mode=ro", uri=True)
    cur = conn.cursor()
    sessions = cur.execute(
        "SELECT id, title, created_at, prompt_tokens, completion_tokens FROM sessions ORDER BY created_at"
    ).fetchall()

    # 按 (date, model) 聚合
    agg = {}  # (date, model) -> dict
    for sid, title, created, pt, ct in sessions:
        pt = pt or 0
        ct = ct or 0
        if pt == 0 and ct == 0:
            continue
        model, provider = session_main_model(cur, sid)
        model = model or "unknown"
        day = datetime.fromtimestamp(created).strftime("%Y-%m-%d")
        key = (day, model)
        e = agg.setdefault(key, {
            "req": 0, "succ": 0, "in": 0, "out": 0, "lat_sum": 0, "lat_n": 0
        })
        # request_count: 会话数(每会话一行) — 每会话 = 1 次使用
        e["req"] += 1
        e["succ"] += 1
        e["in"] += pt
        e["out"] += ct
    conn.close()

    lines = []
    for (day, model), e in sorted(agg.items()):
        lat = e["lat_sum"] // e["lat_n"] if e["lat_n"] else 0
        lines.append(
            "INSERT OR REPLACE INTO usage_daily_rollups "
            "(date, app_type, provider_id, model, request_model, pricing_model, "
            "request_count, success_count, input_tokens, output_tokens, "
            "cache_read_tokens, cache_creation_tokens, total_cost_usd, avg_latency_ms, "
            "input_token_semantics) VALUES "
            f"('{day}', 'crush', 'crush', {sql_str(model)}, '', '', "
            f"{e['req']}, {e['succ']}, {e['in']}, {e['out']}, "
            f"0, 0, '0', {lat}, 0);"
        )
    return lines, agg

def sql_str(s):
    return "'" + s.replace("'", "''") + "'"

def main():
    ap = argparse.ArgumentParser(description="把 crush token 用量插入 cc-switch 聚合表")
    ap.add_argument("--db", default=DEFAULT_CRUSH_DB, help="crush.db 路径")
    ap.add_argument("--ccswitch", default=DEFAULT_CCSWITCH_DB, help="cc-switch.db 路径")
    ap.add_argument("--out", default=DEFAULT_OUT, help="SQL 输出文件")
    ap.add_argument("--dry-run", action="store_true", help="只生成 SQL 不执行")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[-] crush.db 不存在: {args.db}")
        sys.exit(1)
    if not os.path.exists(args.ccswitch) and not args.dry_run:
        print(f"[-] cc-switch.db 不存在: {args.ccswitch}")
        sys.exit(1)

    lines, agg = build_insert_sql(args.db, args.ccswitch)
    print(f"[+] 生成 {len(lines)} 条 INSERT (按 天×模型)")
    for (day, model), e in sorted(agg.items()):
        print(f"    {day} {model}: {e['req']} req, in={e['in']:,} out={e['out']:,}")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("-- crush token usage -> cc-switch usage_daily_rollups\n")
        f.write(f"-- generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n".join(lines) + "\n")
    print(f"[+] SQL 已写: {args.out}")

    if args.dry_run:
        print("[*] dry-run 模式, 未写库")
        return

    # 执行
    conn = sqlite3.connect(args.ccswitch)
    cur = conn.cursor()
    for line in lines:
        cur.execute(line)
    conn.commit()
    conn.close()
    print(f"[+] 已插入 {len(lines)} 行 -> {args.ccswitch}")

if __name__ == "__main__":
    main()
