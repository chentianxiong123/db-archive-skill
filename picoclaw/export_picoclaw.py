"""Export picoclaw chat sessions to JSONL — 模仿 agent-db-archive/opencode 契约 v2.
用 meta.summary 作为标题(截断), meta.created_at 作为时间戳.
"""
import argparse, json, os, re, shutil
from datetime import datetime

SESS_DIR = "/mnt/shared/.picoclaw/workspace/sessions"
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "picoclaw_chat_jsonl")

def safe(s, maxlen=60):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', str(s))
    s = re.sub(r'\s+', '_', s).strip('_')[:maxlen].rstrip('. ')
    return s or 'untitled'

def fmt_ts(ts):
    # 兼容时间戳和 ISO 字符串
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
    # 摘要第一行文本作为标题
    for line in s.split("\n"):
        line = line.strip().lstrip("#* -")
        if line and not line.startswith("**"):
            return line[:40]
    return s[:40] or "untitled"

def export(out_dir=None):
    out_dir = out_dir or DEFAULT_OUT
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
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
        ws = "picoclaw"
        wd = os.path.join(out_dir, ws)
        os.makedirs(wd, exist_ok=True)
        fn_out = f"{safe(title)}_{fmt_ts(created)}.jsonl"
        with open(os.path.join(wd, fn_out), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        total_sessions += 1
        total_lines += len(lines)
        print(f"  {ws}/{fn_out}  ({len(lines)} 行)")
    print(f"\n[+] {total_sessions} 个会话, {total_lines} 行对话 -> {out_dir}")
    return out_dir

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="导出 picoclaw 会话为 JSONL")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    export(args.out)
