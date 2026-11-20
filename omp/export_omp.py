#!/usr/bin/env python3
"""omp / pi-coding-agent (@oh-my-pi) chat export — sessions JSONL -> unified JSONL.

Data sources (all read-only, never mutates the live DBs):
  Sessions:  ~/.omp/agent/sessions/**/*.jsonl   (one event-stream file per session)
  Titles:    ~/.omp/agent/history.db session_titles / session_recaps (optional, read-only copy)

Filter rules (noise out, dialogue in):
  message role=user, attribution=user        -> export  (real user input)
  message role=user, attribution=agent       -> skip    (agent-internal: IRC, steering, interrupt)
  message role=assistant, text parts         -> export
  message role=assistant, thinking parts     -> skip    (reasoning, not dialogue)
  message role=assistant, toolCall parts     -> skip    (tool calls, not dialogue)
  message role=toolResult / developer        -> skip    (tool output / system reminder)
  custom events (system-prompt-digest, tool_execution_start, session_exit, todo_hud_state)
                                                -> skip
  model_change / thinking_level_change / title / title_change / session -> skip

Output:  {cwd}/omp_chat_jsonl/{workspace_slug}/{title}_{YYYYmmdd_HHMMSS}.jsonl
         Each line: {"role": "user"|"assistant", "content": "..."}
"""
import json, os, re, shutil, sys
from datetime import datetime

OMP_HOME = os.environ.get("OMP_HOME", os.path.expanduser("~/.omp"))
SESSIONS_DIR = os.path.join(OMP_HOME, "agent", "sessions")
OUT_DIR = os.path.join(os.getcwd(), "omp_chat_jsonl")


def safe(s, maxlen=80):
    s = re.sub(r'[\\/:*?"<>|]', "_", s)
    s = s.strip().rstrip(".")
    return s[:maxlen] or "untitled"


def fmt_iso(iso_str):
    """ISO 8601 (UTC) -> local YYYYmmdd_HHMMSS."""
    if not iso_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y%m%d_%H%M%S")
    except Exception:
        return "unknown"


def extract_text(content):
    """Extract user/assistant dialogue text from a message content array.
    content parts may be dicts ({type: text|thinking|toolCall}) or bare strings."""
    if not content:
        return ""
    if isinstance(content, str):
        return content.strip()
    parts = []
    for p in content:
        if isinstance(p, dict) and p.get("type") == "text":
            t = p.get("text", "")
            if t:
                parts.append(t)
        elif isinstance(p, str) and p.strip():
            parts.append(p.strip())
    return "\n".join(parts).strip()
def find_session_files():
    """Return TOP-LEVEL session files only.
    Nested files (workspace/parent-session/task.jsonl) are subagent sessions — skip per METHODOLOGY."""
    found = []
    if not os.path.isdir(SESSIONS_DIR):
        return found
    base_depth = SESSIONS_DIR.count(os.sep) + 1   # the -tmp workspace level
    for root, _, files in os.walk(SESSIONS_DIR):
        if root.count(os.sep) != base_depth:
            continue
        for fn in files:
            if fn.endswith(".jsonl"):
                found.append(os.path.join(root, fn))
    return found


def load_titles():
    """Read session_titles / session_recaps from history.db (read-only).
    Falls back to event-stream title if DB missing or locked."""
    import sqlite3
    titles = {}
    recaps = {}
    db = os.path.join(OMP_HOME, "agent", "history.db")
    if os.path.isfile(db):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for sid, title in conn.execute("SELECT session_id, title FROM session_titles"):
                titles[sid] = title
            for sid, recap in conn.execute("SELECT session_id, recap FROM session_recaps"):
                recaps[sid] = recap
            conn.close()
        except sqlite3.OperationalError:
            pass  # locked / not openable — use event-stream title only
    return titles, recaps



def export_session(path, out_dir, titles_db):
    session_id = None
    created_iso = None
    cwd = None
    stream_title = None
    lines_out = []

    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = o.get("type", "")
        if t == "session":
            session_id = o.get("id")
            created_iso = o.get("timestamp")
            cwd = o.get("cwd")
        elif t in ("title", "title_change"):
            stream_title = o.get("title") or stream_title
        elif t == "message":
            msg = o.get("message", {})
            role = msg.get("role")
            attribution = msg.get("attribution")
            if role == "user":
                # only real user input; skip agent-internal (IRC/steering/interrupt)
                if attribution != "user":
                    continue
                text = extract_text(msg.get("content"))
                if not text:
                    continue
                lines_out.append(json.dumps({"role": "user", "content": text}, ensure_ascii=False))
            elif role == "assistant":
                text = extract_text(msg.get("content"))
                if not text:
                    continue
                lines_out.append(json.dumps({"role": "assistant", "content": text}, ensure_ascii=False))
            # toolResult / developer / other roles: skip

    if not lines_out:
        return session_id, stream_title, 0

    # title precedence: DB session_titles > event-stream title > recap > session id
    db_titles = titles_db[0]
    title = (db_titles.get(session_id) or stream_title
             or titles_db[1].get(session_id, "")[:80] or "untitled")

    ts_str = fmt_iso(created_iso)
    ws_slug = safe(os.path.basename(os.path.dirname(path))) or "unknown"
    out_sub = os.path.join(out_dir, "omp_chat_jsonl", ws_slug)
    os.makedirs(out_sub, exist_ok=True)
    out_file = os.path.join(out_sub, f"{safe(title)}_{ts_str}.jsonl")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out) + "\n")

    return session_id, title, len(lines_out)


def main():
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    print(f"OMP_HOME = {OMP_HOME}")
    print(f"Output   = {OUT_DIR}")
    print()
    files = find_session_files()
    print(f"Found {len(files)} session files under {SESSIONS_DIR}")
    if not files:
        print("No sessions found. Is OMP_HOME correct?")
        sys.exit(1)

    titles_db = load_titles()
    print(f"Loaded {len(titles_db[0])} titles + {len(titles_db[1])} recaps from history.db")
    print()

    total = 0
    ok = 0
    empty = 0
    for i, path in enumerate(sorted(files), 1):
        sid, title, n = export_session(path, os.getcwd(), titles_db)
        label = safe(title or sid or "unknown", 40)
        if n > 0:
            ok += 1
            total += n
            print(f"  {i:3d}/{len(files)}  {label:42s}  {n:5d} lines")
        else:
            empty += 1
            print(f"  {i:3d}/{len(files)}  {label:42s}  (no user/assistant text)")

    print()
    print(f"Done: {ok} sessions exported, {empty} empty, {total} total dialogue lines")
    print(f"Output: {OUT_DIR}")


if __name__ == "__main__":
    main()
