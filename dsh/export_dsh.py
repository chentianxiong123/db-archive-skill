#!/usr/bin/env python3
"""DSH (DeepSeek Harness) chat export — zstd JSONL sessions -> unified JSONL.

Data source:  ~/.dsh/sessions/**/session.v4.jsonl.zstd
Token stats:  ~/.dsh/storages/session_projcache/sessions/{session_id}.json
Title:        session/title event in event stream, or projcache rows.title

Filter rules (noise out, dialogue in):
  user/message:   only source.kind == "user"  (skip runtime-context, skill-catalog,
                  time-context, compact-checkpoint, tool-jobs, model-selection,
                  user-approval, and any other injected kind)
  assistant/message: always include
  system/message, developer/message, compaction/summary, tool/call, tool/result,
  step/start, step/end, turn/start, turn/end, agent/inbox/spliced, etc.: skip

Output:  {out_dir}/dsh_chat_jsonl/{workspace_slug}/{title}_{YYYYmmdd_HHMMSS}.jsonl
         Each line: {"role": "user"|"assistant", "content": "..."}
"""
import json, os, re, shutil, subprocess, sys
from datetime import datetime

DASH = os.sep
HOME = os.path.expanduser("~")
DSH_HOME = os.environ.get("DSH_HOME", os.path.join(HOME, ".dsh"))
SESSIONS_DIR = os.path.join(DSH_HOME, "sessions")
PROJCACHE_DIR = os.path.join(DSH_HOME, "storages", "session_projcache", "sessions")
OUT_DIR = os.path.join(os.getcwd(), "dsh_chat_jsonl")

# ---- helpers -----------------------------------------------------------

def safe(s, maxlen=80):
    s = re.sub(r'[\\/:*?"<>|]', "_", s)
    s = s.strip().rstrip(".")
    return s[:maxlen] or "untitled"

def fmt_ts(ms):
    """Millisecond epoch -> YYYYmmdd_HHMMSS. Returns 'unknown' on failure."""
    if not ms:
        return "unknown"
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y%m%d_%H%M%S")
    except Exception:
        return "unknown"

def read_zstd_jsonl(path):
    # Prefer zstd CLI (available in MSYS2 / w64devkit) over Python module
    import subprocess
    proc = subprocess.run(
        ["zstd", "-d", "-c", path], capture_output=True, text=False
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            ["zcat", path], capture_output=True, text=False
        )
        if proc.returncode != 0:
            print(f"  WARN: cannot decompress {path}")
            return
    for line in proc.stdout.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue

def extract_text_from_content(content):
    """Extract text from a content array. Returns '' if no text part."""
    if not content or not isinstance(content, list):
        return ""
    parts = []
    for p in content:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            t = p.get("text", "")
            if t:
                parts.append(t)
    return "\n".join(parts).strip()

def load_projcache(session_id):
    """Load token usage + title from projcache JSON (optional)."""
    path = os.path.join(PROJCACHE_DIR, f"{session_id}.json")
    if not os.path.isfile(path):
        return None, None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            o = json.load(f)
        rec = o.get("record", {})
        rows = rec.get("rows", {})
        # tokenUsage
        tu = rows.get("tokenUsage", {})
        tu_val = tu.get("val", {}) if isinstance(tu, dict) else {}
        # title
        title = None
        t = rows.get("title", "")
        if isinstance(t, dict):
            title = t.get("val", None)
        elif isinstance(t, str):
            title = t
        # sessionStats
        stats = rows.get("sessionStats", {})
        stats_val = stats.get("val", {}) if isinstance(stats, dict) else {}
        return tu_val, title, stats_val
    except Exception:
        return None, None, None

# ---- main export -------------------------------------------------------

def find_session_files():
    """Find all session.v4.jsonl.zstd files under SESSIONS_DIR."""
    found = []
    if not os.path.isdir(SESSIONS_DIR):
        return found
    for root, dirs, files in os.walk(SESSIONS_DIR):
        for fn in files:
            if fn == "session.v4.jsonl.zstd":
                found.append(os.path.join(root, fn))
    return found

def export_session(path, out_dir):
    """Export one session file. Returns (session_id, title, line_count, token_info)."""
    # walk the event stream
    session_meta = None
    title = None
    lines_out = []
    token_usage = None
    session_stats = None
    session_id = None

    for o in read_zstd_jsonl(path):
        t = o.get("type", "")

        if t == "session":
            session_meta = o
            session_id = o.get("id", "")

        elif t == "session/title":
            d = o.get("data", {})
            src = d.get("source", {})
            # prefer provider-generated title over fallback
            if src.get("kind") == "provider" or title is None:
                title = d.get("title")

        elif t == "user/message":
            data = o.get("data", {})
            src = data.get("source", {})
            kind = src.get("kind", "")
            if kind != "user":
                continue  # skip injected noise
            text = extract_text_from_content(data.get("content"))
            if not text:
                continue
            lines_out.append(json.dumps(
                {"role": "user", "content": text}, ensure_ascii=False))

        elif t == "assistant/message":
            data = o.get("data", {})
            msg = data.get("message", {})
            text = extract_text_from_content(msg.get("content"))
            if not text:
                continue
            lines_out.append(json.dumps(
                {"role": "assistant", "content": text}, ensure_ascii=False))
            # skip other event types (system, developer, tool, compaction, etc.)

    # load projcache for token stats
    if session_id:
        token_usage, pc_title, session_stats = load_projcache(session_id)
        if pc_title and (not title or len(pc_title) > len(title)):
            # provider title from projcache may be better
            pass  # keep the event-stream title (already set)

    if not lines_out:
        return session_id, title, 0, token_usage

    # write output file
    created_ms = (session_meta or {}).get("createdAt")
    ts_str = fmt_ts(created_ms)
    title_safe = safe(title or "untitled")
    ws_slug = safe(os.path.basename(os.path.dirname(path)))
    out_sub = os.path.join(out_dir, "dsh_chat_jsonl", ws_slug)
    os.makedirs(out_sub, exist_ok=True)
    out_file = os.path.join(out_sub, f"{title_safe}_{ts_str}.jsonl")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out) + "\n")

    return session_id, title, len(lines_out), token_usage

def main():
    import atexit
    shutil.rmtree(OUT_DIR, ignore_errors=True)

    print(f"DSH_HOME = {DSH_HOME}")
    print(f"Output   = {OUT_DIR}")
    print()

    files = find_session_files()
    print(f"Found {len(files)} session files under {SESSIONS_DIR}")
    if not files:
        print("No sessions found. Is DSH_HOME correct?")
        sys.exit(1)

    total_lines = 0
    ok_count = 0
    empty_count = 0
    token_report = []

    for i, path in enumerate(sorted(files), 1):
        sid, title, n, tu = export_session(path, os.getcwd())
        label = safe(title or sid or "unknown", 40)
        if n > 0:
            ok_count += 1
            total_lines += n
            tu_str = ""
            if tu:
                totals = tu.get("totals", {})
                inp = totals.get("uncachedInputTokens", 0) + totals.get("cacheReadTokens", 0)
                out = totals.get("outputTokens", 0)
                tu_str = f"  [tokens: in={inp} out={out}]"
            print(f"  {i:2d}/{len(files)}  {label:40s}  {n:4d} lines{tu_str}")
        else:
            empty_count += 1
            print(f"  {i:2d}/{len(files)}  {label:40s}  (empty — no user/assistant text)")

    print()
    print(f"Done: {ok_count} sessions exported, {empty_count} empty, {total_lines} total dialogue lines")
    print(f"Output: {OUT_DIR}")

if __name__ == "__main__":
    main()
