#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三步: EnMicroMsg.db (SQLCipher) -> 明文 decrypted.db + 导出聊天

用法:
  python tools/decrypt_db.py <EnMicroMsg.db> <微信库密码> <输出目录>

流程(SKILLS.md 第三步):
  1. PRAGMA key='<密码>' 解锁 SQLCipher
  2. PRAGMA wal_checkpoint(TRUNCATE) 合并 WAL
  3. sqlcipher_export -> 明文 decrypted.db
  4. 从明文导出 messages-text.tsv (msgId/type/talker/createTime/imgPath/content)
  5. 生成 wechat-chat-export.html (按会话分组的可读导出)

依赖: sqlcipher3 或 pysqlcipher3 模块; 缺失时回退 sqlcipher CLI。
"""
import os, sys, sqlite3, html, subprocess

def sqlcipher_open(db, pwd):
    """sqlcipher3 模块打开(已 key + WAL 合并); 模块缺失返回 None(走 CLI)。"""
    try:
        import sqlcipher3 as s3  # 或 pysqlcipher3
    except ImportError:
        try:
            import pysqlcipher3 as s3  # type: ignore
        except ImportError:
            return None
    conn = s3.connect(db)
    conn.execute("PRAGMA key=?", (pwd,))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return conn

def cli_export(src, pwd, plain):
    """sqlcipher CLI 解密导出明文库。"""
    subprocess.run(
        ["sqlcipher", src,
         f"PRAGMA key='{pwd}'; PRAGMA wal_checkpoint(TRUNCATE); "
         f"ATTACH DATABASE '{plain}' AS plaintext KEY ''; "
         f"SELECT sqlcipher_export('plaintext'); DETACH DATABASE plaintext;"],
        check=True, capture_output=True, timeout=1200)

def export_tsv(db, path):
    rows = db.execute(
        "SELECT msgId,type,talker,createTime,imgPath,content FROM message "
        "ORDER BY createTime")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("msgId\ttype\ttalker\tcreateTime\timgPath\tcontent\n")
        for r in rows:
            f.write("\t".join(str(x or "") for x in r) + "\n")
    return db.execute("SELECT COUNT(*) FROM message").fetchone()[0]

def export_html(db, path):
    rooms = {}
    for r in db.execute(
            "SELECT talker,type,createTime,isSend,content FROM message ORDER BY createTime"):
        t, ts, send, c = r["talker"], r["createTime"], r["isSend"], (r["content"] or "")
        if t not in rooms:
            rooms[t] = []
        who = "我" if send else (t.split("@")[0] if "@" in t else t)
        rooms[t].append((ts, who, c))
    parts = ["<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
             "<title>微信聊天导出</title><style>"
             "body{font-family:sans-serif;max-width:800px;margin:20px auto;padding:0 12px}"
             ".s{margin:20px 0;padding:10px;border:1px solid #ddd;border-radius:6px}"
             ".m{margin:6px 0}.w{color:#888;font-size:12px}</style></head><body>"]
    for t, msgs in rooms.items():
        parts.append(f"<div class='s'><h3>{html.escape(t)} ({len(msgs)})</h3>")
        for ts, who, c in msgs:
            parts.append(f"<div class='m'><span class='w'>{ts} {html.escape(who)}</span><br>"
                         f"{html.escape(c[:500])}</div>")
        parts.append("</div>")
    parts.append("</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(parts))

def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    src, pwd, out = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out, exist_ok=True)
    plain = os.path.join(out, "decrypted.db")

    conn = sqlcipher_open(src, pwd)
    if conn is not None:
        conn.execute(f"ATTACH DATABASE '{plain}' AS plaintext KEY ''")
        conn.execute("SELECT sqlcipher_export('plaintext')")
        conn.execute("DETACH DATABASE plaintext")
        conn.close()
    else:
        print("[*] sqlcipher3 模块缺失, 回退 sqlcipher CLI")
        cli_export(src, pwd, plain)

    db = sqlite3.connect(plain)
    db.row_factory = sqlite3.Row
    n = export_tsv(db, os.path.join(out, "messages-text.tsv"))
    export_html(db, os.path.join(out, "wechat-chat-export.html"))
    print(f"[✓] message={n} 明文库={plain} 导出=messages-text.tsv / wechat-chat-export.html")

if __name__ == "__main__":
    main()
