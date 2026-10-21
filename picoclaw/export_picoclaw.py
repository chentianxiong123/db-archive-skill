"""Export picoclaw chat sessions to JSONL — v2: 优先读 seahorse.db (DB 模式, 含 token 统计), JSONL 作 fallback.
契约与 agent-db-archive 其他工具一致: {role, content} 每行一个 JSON. 产物平铺在 {工具}_chat_jsonl/
"""
import argparse, json, os, re, shutil, sqlite3
from datetime import datetime

# picoclaw 数据位置
SEAHORSE_DB = os.path.expanduser("~/.picoclaw/workspace/sessions/seahorse.db")
SESS_DIR = os.path.expanduser("~/.picoclaw/workspace/sessions")
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "picoclaw_chat_jsonl")

def safe(s, maxlen=60):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', str(s))
    s = re.sub(r'\s+', '_', s).strip('_')[:maxlen].rstrip('. ')
    return s or 'untitled'

def fmt_ts(ts):
    if isinstance(ts, (int, float)):
        if ts > 1e12: ts /= 1000
        dt = datetime.fromtimestamp(ts)
    elif isinstance(ts, str):
        try:
            ts2 = ts.replace("+08:00", "").replace("Z", "")
            dt = datetime.fromisoformat(ts2.split(".")[0])
        except Exception:
            dt = None
    else:
        dt = None
    return dt.strftime('%Y%m%d_%H%M%S') if dt else 'unknown'

def summary_title(md):
    s = md.get("summary") or ""
    for line in s.split("\n"):
        line = line.strip().lstrip("#* -")
        if line and not line.startswith("**"):
            return line[:40]
    return s[:40] or "untitled"

def export_db(out_dir):
    """DB 模式: 读 seahorse.db, messages 有 token_count, summaries 有压缩摘要"""
    conn = sqlite3.connect(f"file:{SEAHORSE_DB}?mode=ro", uri=True)
    cur = conn.cursor()
    convs = cur.execute("SELECT conversation_id, session_key, created_at, updated_at FROM conversations ORDER BY created_at").fetchall()
    total_sessions = 0
    total_lines = 0
    total_tokens = 0
    session_stats = []
    for cid, skey, created, updated in convs:
        # 会话标题: 找第一条 user 消息
        title_row = cur.execute(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user' AND content!='' ORDER BY created_at LIMIT 1",
            (cid,)).fetchone()
        title = title_row[0][:60] if title_row else "untitled"
        msgs = cur.execute(
            "SELECT role, content, token_count, created_at FROM messages WHERE conversation_id=? AND role IN ('user','assistant') ORDER BY created_at",
            (cid,)).fetchall()
        lines = []
        tok = 0
        for role, content, tc, ts in msgs:
            if not content or not content.strip():
                continue
            content = re.sub(r'\n*\[Assistant tool call\]: .*$', '', content, flags=re.DOTALL)
            if not content.strip() or content.strip().startswith("diff --git"):
                continue
            lines.append(json.dumps({"role": role, "content": content}, ensure_ascii=False))
            tok += (tc or 0)
        session_stats.append({
            "session_id": cid,
            "title": title,
            "created_at": fmt_ts(created),
            "exported_lines": len(lines),
            "dialogue_tokens": tok,
        })
        if not lines:
            continue
        fn = f"{safe(title)}_{fmt_ts(created)}.jsonl"
        with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        total_sessions += 1
        total_lines += len(lines)
        total_tokens += tok
        print(f"  {fn}  ({len(lines)} 行, token={tok:,})")
    conn.close()

    stats_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_stats.json")
    stats = {
        "tool": "picoclaw",
        "storage_mode": "db",
        "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "source": SEAHORSE_DB,
        "sessions_total": len(convs),
        "sessions_exported": total_sessions,
        "lines_exported": total_lines,
        "dialogue_tokens": total_tokens,
        "sessions_detail": session_stats,
    }
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"\n[+] DB 模式: {total_sessions} 会话, {total_lines} 行对话, token={total_tokens:,}")
    print(f"[+] token 统计: {stats_path}")
    return total_sessions, total_lines, total_tokens

def export_jsonl(out_dir):
    """fallback: 纯 JSONL 模式 (无 token 统计)"""
    total_sessions = 0
    total_lines = 0
    for fn in sorted(os.listdir(SESS_DIR)):
        if not fn.endswith(".jsonl"):
            continue
        p = os.path.join(SESS_DIR, fn)
        meta_p = p.replace(".jsonl", ".meta.json")
        md = {}
        if os.path.exists(meta_p):
            try: md = json.load(open(meta_p))
            except Exception: pass
        title = summary_title(md) if md else "untitled"
        created = md.get("created_at")
        lines = []
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line: continue
            try: d = json.loads(line)
            except Exception: continue
            role = d.get("role")
            content = d.get("content", "")
            if role not in ("user", "assistant") or not content: continue
            content = re.sub(r'\n*\[Assistant tool call\]: .*$', '', content, flags=re.DOTALL)
            if not content.strip() or content.strip().startswith("diff --git"): continue
            lines.append(json.dumps({"role": role, "content": content}, ensure_ascii=False))
        if not lines: continue
        fn_out = f"{safe(title)}_{fmt_ts(created)}.jsonl"
        with open(os.path.join(out_dir, fn_out), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        total_sessions += 1
        total_lines += len(lines)
        print(f"  picoclaw/{fn_out}  ({len(lines)} 行)")
    print(f"\n[+] JSONL 模式: {total_sessions} 会话, {total_lines} 行对话")
    return total_sessions, total_lines, 0

def export(out_dir=None, force_jsonl=False):
    out_dir = out_dir or DEFAULT_OUT
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    # 优先 DB 模式
    if os.path.exists(SEAHORSE_DB) and not force_jsonl:
        return export_db(out_dir)
    return export_jsonl(out_dir)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="导出 picoclaw 会话为 JSONL (v2: DB 优先)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--force-jsonl", action="store_true", help="强制用纯 JSONL 模式")
    args = ap.parse_args()
    export(args.out, args.force_jsonl)
