# -*- coding: utf-8 -*-
"""adapter: 微信 7.0.17 (HiSuite 解密库) -> 标准 chatdata.json
用法: python adapters/wechat_717.py <decrypted.db> <media_dir> <out.json>
"""
import sqlite3, json, re, os, sys, glob

def type_map(t):
    return {
        1: "text", 3: "image", 34: "voice", 43: "video", 47: "emoji",
        49: "link", 48: "location", 10000: "system", 285212721: "system",
    }.get(t, "unknown")

def build_media_index(media_dir):
    """basename -> full path (O(1) 查询)"""
    idx = {}
    for p in glob.glob(os.path.join(media_dir, "*", "*", "*")):
        idx[os.path.basename(p)] = p
    return idx

def find_media(idx, md5, want_thumb=True):
    """按 md5 找 media 文件: {md5}.thumb.jpg / {md5}.jpg"""
    if not md5:
        return ""
    if want_thumb:
        for suf in (".thumb.jpg", ".jpg"):
            f = md5 + suf
            if f in idx:
                return idx[f]
    for suf in (".jpg", ".png", ".gif"):
        f = md5 + suf
        if f in idx:
            return idx[f]
    return ""

def parse_xml_text(content):
    """从消息 XML 提取可读文本（链接 title/url、图片描述等）"""
    if not content or not content.startswith("<"):
        return ""
    title = re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>', content, re.S)
    if title:
        return title.group(1)
    desc = re.search(r'<des>(.*?)</des>', content, re.S)
    if desc:
        return desc.group(1).strip()
    url = re.search(r'<url>(.*?)</url>', content, re.S)
    if url:
        return url.group(1)
    return ""

def main(db_path, media_dir, out_path):
    db = sqlite3.connect(db_path)
    db.text_factory = lambda b: b.decode("utf-8", "replace")
    cur = db.cursor()

    # 会话名
    room = {}
    try:
        for r in cur.execute("SELECT chatroomname, displayname, memberlist FROM chatroom"):
            nm = (r[1] or "").strip()
            if not nm and r[2]:
                mem = r[2].split(";")
                nm = "、".join(mem[:6]) + ("…" if len(mem) > 6 else "")
            room[r[0]] = nm
    except Exception:
        pass
    contact = {}
    try:
        for r in cur.execute("SELECT username, conRemark, nickname FROM rcontact"):
            contact[r[0]] = ((r[1] or "").strip(), (r[2] or "").strip())
    except Exception:
        pass

    def tname(t):
        if t.endswith("@chatroom"):
            return room.get(t) or t.split("@")[0]
        if t in contact:
            cr, nk = contact[t]
            return cr or nk or t
        return t

    media_idx = build_media_index(media_dir)

    # 消息 -> 标准结构
    sessions = {}
    for r in cur.execute(
        "SELECT msgId, talker, isSend, createTime, type, content, imgPath "
        "FROM message ORDER BY createTime ASC"):
        mid, talker, isSend, ts, typ, content, imgPath = r
        content = content or ""
        imgPath = imgPath or ""
        ty = type_map(typ)
        text = ""
        media = None

        if ty == "text":
            text = content
        elif ty == "image":
            m = re.search(r'th_([0-9a-f]{32})', imgPath)
            if m:
                p = find_media(media_idx, m.group(1), True)
                if p:
                    media = {"path": p, "thumb": p, "url": "", "duration": 0, "size": 0}
            text = "[图片]"
        elif ty == "voice":
            text = "[语音]"
            # 语音时长在 content
            m = re.search(r'voicelength="?(\d+)', content)
            dur = int(m.group(1)) / 1000.0 if m else 0
            media = {"path": "", "thumb": "", "url": "", "duration": dur, "size": 0}
        elif ty == "video":
            text = "[视频]"
            media = {"path": "", "thumb": "", "url": "", "duration": 0, "size": 0}
        elif ty == "emoji":
            m = re.search(r'([0-9a-f]{32})', imgPath)
            if m:
                p = find_media(media_idx, m.group(1), False)
                if p:
                    media = {"path": p, "thumb": p, "url": "", "duration": 0, "size": 0}
            text = "[表情]"
        elif ty == "link":
            text = parse_xml_text(content) or "[链接]"
            media = {"path": "", "thumb": "", "url": "", "duration": 0, "size": 0}
        elif ty == "location":
            text = parse_xml_text(content) or "[位置]"
        elif ty == "system":
            text = content.strip()
        else:
            text = content.strip()[:100] or f"[未知类型 {typ}]"

        sess = sessions.get(talker)
        if sess is None:
            sess = {
                "id": talker, "name": tname(talker),
                "type": "group" if talker.endswith("@chatroom") else
                        ("official" if talker.startswith("gh_") else "private"),
                "avatar": "", "member_count": 0, "members": [], "messages": [],
            }
            sessions[talker] = sess
        msg = {
            "id": str(mid), "direction": "send" if isSend else "recv",
            "sender": talker if isSend else talker,  # 手机版无独立发送者，私聊=对方
            "sender_name": "我" if isSend else tname(talker),
            "timestamp": int(ts), "type": ty, "text": text,
            "media": media or {}, "reply_to": "", "raw": {},
        }
        sess["messages"].append(msg)

    sess_list = sorted(sessions.values(), key=lambda s: -s["messages"][-1]["timestamp"])
    total = sum(len(s["messages"]) for s in sess_list)
    data = {
        "format": "chat-standard", "format_version": "1.0.0",
        "source": {"app": "wechat", "app_version": "7.0.17",
                   "platform": "android", "device": "HUAWEI nova 3e",
                   "exported_at": int(__import__("time").time() * 1000)},
        "contacts": [{"id": k, "name": v[1] or v[0], "remark": v[0],
                      "avatar": "", "type": "user"}
                     for k, v in contact.items()],
        "sessions": sess_list,
        "stats": {"sessions": len(sess_list), "messages": total},
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"OK sessions={len(sess_list)} messages={total} size={os.path.getsize(out_path)/1e6:.1f}MB")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
