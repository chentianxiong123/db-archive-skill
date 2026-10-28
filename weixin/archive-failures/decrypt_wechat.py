#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信 Android 数据库解锁助手
原理：EnMicroMsg.db 是 SQLCipher 加密，密钥由 IMEI+UIN 派生。
经典算法：key = md5(imei + uin) 的前 7 字节（老版本）
备选：     key = md5(imei + uin) 完整 32 位 hex 直接当口令
用法：
  python3 decrypt_wechat.py <EnMicroMsg.db> <imei1> [imei2] [uin]
  （uin 从备份解包后的 shared_prefs 里拿；也可用 -1 塞占位）
"""
import hashlib, os, subprocess, sys

FOLDER_HASH = "23e6933dcaff5cf6d760b941459bf0a4"  # 已知账户目录哈希，用于校验

def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()

def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    db = sys.argv[1]
    imeis = [x for x in sys.argv[2].split(",") if x]
    if len(sys.argv) > 3 and sys.argv[3].isdigit():
        imeis.append(sys.argv[3])
    uin = sys.argv[4] if len(sys.argv) > 4 else ""

    print(f"[*] DB: {db}  ({os.path.getsize(db)} bytes)")
    print(f"[*] IMEI 候选: {imeis}")
    print(f"[*] UIN: {uin or '(未知, 需从 shared_prefs 获取)'}")

    # 密钥候选
    candidates = []
    for imei in imeis:
        for u in [uin] if uin else []:
            raw = imei + u
            h = md5(raw)
            candidates.append((f"md5({imei}+{uin})[:7]", "x'" + h[:14] + "'"))
            candidates.append((f"md5({imei}+{uin}) full-hex", h))
            # 校验目录哈希
            if h == FOLDER_HASH:
                print(f"[✓] 目录哈希匹配! imei={imei} uin={uin} 确认密钥来源正确")

    if not candidates:
        print("[!] 没有 uin，无法派生密钥。先在备份解包后的 shared_prefs 里找到 uin。")
        sys.exit(1)

    # 逐个尝试
    for desc, key in candidates:
        # 尝试两种 key 写法
        for variant, k in [("sqlcipher key as x'hex'", key), ("sqlcipher key as string", key)]:
            if variant.startswith("string"):
                cmd = f"PRAGMA key='{k}';"
            else:
                cmd = f"PRAGMA key={k};"
            r = subprocess.run(["sqlcipher", db, cmd + " SELECT count(*) FROM sqlite_master;"],
                               capture_output=True, text=True, timeout=60)
            out = (r.stdout + r.stderr).strip()
            if "not a database" in out.lower() or "file is encrypted" in out.lower():
                continue
            try:
                n = int(out.splitlines()[-1].strip())
                print(f"[✓✓] 密钥有效! {desc} / {variant}\n     对象数={n}")
                # 导出明文库
                plain = db + ".plain.db"
                exp = (f"PRAGMA key={k};" if variant.startswith("sqlcipher key as x") else f"PRAGMA key='{k}';")
                exp += f" ATTACH DATABASE '{plain}' AS plaintext KEY ''; SELECT sqlcipher_export('plaintext'); DETACH DATABASE plaintext;"
                subprocess.run(["sqlcipher", db, exp], timeout=300)
                print(f"[✓] 明文库已导出: {plain}")
                sys.exit(0)
            except Exception as e:
                continue
    print("[✗] 所有候选密钥都失败。检查 IMEI（*#06# 读 IMEI1）和 UIN 是否正确。")

if __name__ == "__main__":
    main()