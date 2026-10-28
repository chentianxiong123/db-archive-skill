#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第五步: message 表 -> media_map.csv (媒体 ↔ 消息对齐)

用法:
  python tools/align_media.py <decrypted.db> <wechat_media_dir> [media_map.csv]

规则(SKILLS.md 第五步, 微信 7.0.17 实测):
  type 3  图片  image2/{md5[:2]}/{md5[2:4]}/{md5}.jpg  (th_=缩略图)  imgPath 内 THUMBNAIL_DIRPATH://th_<md5>
  type 34 语音  voice2/{xx}/{yy}/msg_{imgPath}.amr       imgPath 全串 = 文件名 (24-26 hex)
  type 43 视频  video/{imgPath}.mp4 (扁平, .jpg 缩略图)  imgPath 全串 = 文件名
  type 47 表情  emoji/{md5} (扁平, _cover=封面)          content 内 md5="..." 或 imgPath 32hex

⚠️ 最大坑: 语音/视频 imgPath 是 24-26 位 hex, 必须用 [0-9a-f]{20,} 全串匹配, {32} 会漏光。
"""
import os, re, sqlite3, csv, sys

THUMB = re.compile(r'th_([0-9a-f]{32})')
HEX20 = re.compile(r'^[0-9a-fA-F]{20,}$')
MD5IN = re.compile(r'md5="([0-9a-f]{32})"')
MD5ANY = re.compile(r'([0-9a-f]{32})')

def build_index(media_dir):
    """全量 basename -> 相对 media_dir 的 posix 路径 (O(1) 查询)。"""
    idx = {}
    for root, _, files in os.walk(media_dir):
        for f in files:
            idx[f] = os.path.relpath(os.path.join(root, f), media_dir).replace("\\", "/")
    return idx

def find_media(idx, typ, img_path, content):
    typ = typ or 0
    img_path = img_path or ""
    content = content or ""
    if typ == 3:  # 图片
        m = THUMB.search(img_path)
        if m:
            md5 = m.group(1)
            for k in (f"{md5}.jpg", f"th_{md5}", f"{md5}"):
                if k in idx:
                    return idx[k]
    elif typ == 34:  # 语音
        ip = img_path.strip()
        if HEX20.match(ip):
            for k in (f"msg_{ip}.amr", f"{ip}.amr"):
                if k in idx:
                    return idx[k]
    elif typ == 43:  # 视频
        ip = img_path.strip()
        if HEX20.match(ip):
            for k in (f"{ip}.mp4", f"{ip}.jpg"):
                if k in idx:
                    return idx[k]
    elif typ == 47:  # 表情
        m = MD5IN.search(content) or MD5ANY.search(img_path)
        if m:
            md5 = m.group(1)
            for k in (md5, f"{md5}.gif", f"{md5}.png"):
                if k in idx:
                    return idx[k]
    return ""

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    db_path, media_dir = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else "media_map.csv"
    idx = build_index(media_dir)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    n = hit = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["msgId", "type", "talker", "createTime", "media_path"])
        for r in conn.execute(
                "SELECT msgId,type,talker,createTime,imgPath,content FROM message"):
            n += 1
            p = find_media(idx, r["type"], r["imgPath"], r["content"])
            if p:
                hit += 1
            w.writerow([r["msgId"], r["type"], r["talker"], r["createTime"], p])
    print(f"[✓] media_map.csv={out} 消息={n} 命中={hit} ({hit * 100.0 / max(n, 1):.1f}%)")

if __name__ == "__main__":
    main()
