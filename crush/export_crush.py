"""Export crush (goose) chat sessions to JSONL — 模仿 agent-db-archive/opencode 契约.
仅取 type=text 的 part, 过滤内部噪声. 输出: crush_chat_jsonl/{分类}/{标题}_{时间戳}.jsonl
"""
import argparse, json, os, re, shutil, sqlite3
from datetime import datetime

DEFAULT_DB = "/mnt/shared/.crush/crush.db"
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crush_chat_jsonl")

def safe(s, maxlen=80):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', str(s))
    s = re.sub(r'\s+', '_', s).strip('_')[:maxlen].rstrip('. ')
    return s or 'untitled'

def fmt_ts(ts):
    if isinstance(ts, (int, float)):
        if ts > 1e12:
            ts /= 1000
        try:
            return datetime.fromtimestamp(ts).strftime('%Y%m%d_%H%M%S')
        except Exception:
            pass
    return 'unknown'

def export(db_path=None, out_dir=None):
    db_path = db_path or DEFAULT_DB
    out_dir = out_dir or DEFAULT_OUT
    if not os.path.exists(db_path):
        print(f"[-] 数据库不存在: {db_path}")
        return
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    sessions = conn.execute(
        "SELECT id, title, created_at, parent_session_id, prompt_tokens, completion_tokens "
        "FROM sessions ORDER BY created_at"
    ).fetchall()
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    total_sessions = 0
    total_lines = 0
    total_prompt = 0
    total_comp = 0
    for sid, title, created, parent, pt, ct in sessions:
        msgs = conn.execute(
            "SELECT role, parts, created_at FROM messages WHERE session_id = ? ORDER BY created_at",
            (sid,),
        ).fetchall()
        lines = []
        for role, parts, ts in msgs:
            if role not in ('user', 'assistant'):
                continue
            try:
                plist = json.loads(parts) if parts else []
            except Exception:
                continue
            for p in plist:
                if not isinstance(p, dict):
                    continue
                ptype = p.get('type')
                pdata = p.get('data') or {}
                if ptype == 'text':
                    # 兼容两种格式: {'data':{'text':...}} 与平铺 {'text':...}
                    if isinstance(pdata, dict) and pdata.get('text') is not None:
                        text = pdata.get('text', '')
                    elif isinstance(p, dict) and p.get('text') is not None:
                        text = p.get('text', '')
                    else:
                        text = ''
                else:
                    # thinking / toolCall / tool_result / reasoning / finish 全部跳过
                    continue
                text = text.strip()
                if not text or text.startswith('diff --git'):
                    continue
                lines.append(json.dumps({"role": role, "content": text}, ensure_ascii=False))
        if not lines:
            continue
        ws = 'crush_' + safe(fmt_ts(created)[:8])
        wd = os.path.join(out_dir, ws)
        os.makedirs(wd, exist_ok=True)
        fn = f"{safe(title or 'untitled')}_{fmt_ts(created)}.jsonl"
        fp = os.path.join(wd, fn)
        with open(fp, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        total_sessions += 1
        total_lines += len(lines)
        total_prompt += (pt or 0)
        total_comp += (ct or 0)
        print(f"  {ws}/{fn}  ({len(lines)} 行, prompt={pt or 0:,} comp={ct or 0:,})")
    conn.close()
    print(f"\n[+] {total_sessions} 个会话, {total_lines} 行对话 -> {out_dir}")
    print(f"[+] token 合计: prompt={total_prompt:,} completion={total_comp:,} total={total_prompt+total_comp:,}")
    return out_dir

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="导出 crush/goose 会话为 JSONL")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    export(args.db, args.out)
