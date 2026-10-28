#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verify backup password against a single small module (clock) without
decrypting the full 8.7G. Uses both checkMsgV3 (password proof) and
encMsgV3 (per-file key derivation).
"""
import argparse
import binascii
import sys
import xml.dom.minidom as minidom
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Hash import HMAC, SHA256
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Util import Counter

BACKUP = Path(r'C:\Users\a1\Documents\HiSuite\backup\HUAWEI nova youth_2026-10-26 21.47.14')

# Match Decryptor class constants
DKLEN = 32
COUNT = 5000


def prf(p, s):
    return HMAC.new(p, s, SHA256).digest()


def parse_modules(xml_path):
    """Return dict name -> {col: value} for all BackupFileModuleInfo* rows."""
    d = minidom.parse(str(xml_path))
    out = {}
    for row in d.getElementsByTagName('row'):
        tbl = row.getAttribute('table')
        if not tbl.startswith('BackupFileModuleInfo'):
            continue
        cols = {}
        for c in row.getElementsByTagName('column'):
            v = c.firstChild
            val = None
            if v is not None and v.tagName == 'value':
                if v.hasAttribute('String'):
                    val = v.getAttribute('String')
                elif v.hasAttribute('Integer'):
                    val = int(v.getAttribute('Integer'))
                elif v.hasAttribute('Long'):
                    val = int(v.getAttribute('Long'))
                elif v.hasAttribute('Boolean'):
                    val = v.getAttribute('Boolean')
                elif v.hasAttribute('Null'):
                    val = None
            cols[c.getAttribute('name')] = val
        out[cols.get('name')] = (tbl, cols)
    return out


def check_password(password: bytes, chk_hex: str) -> bool:
    chk = binascii.unhexlify(chk_hex)
    if len(chk) < 64:
        return False
    # Take first 64 bytes; some modules (com.tencent.mm) pad with extras.
    chk = chk[:64]
    salt = chk[32:]
    expected = chk[:32]
    res = PBKDF2(password, salt, DKLEN, COUNT, prf, hmac_hash_module=None)
    return res == expected


def decrypt_package(password: bytes, enc_msg_v3_hex: str, ciphertext: bytes) -> bytes:
    em = binascii.unhexlify(enc_msg_v3_hex)
    salt = em[:32]
    iv = em[32:]
    key = PBKDF2(password, salt, DKLEN, COUNT, prf, hmac_hash_module=None)
    cobj = Counter.new(128, initial_value=int.from_bytes(iv, 'big'), little_endian=False)
    return AES.new(key, mode=AES.MODE_CTR, counter=cobj).decrypt(ciphertext)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('password', nargs='+', help='one or more password candidates')
    ap.add_argument('--module', default='clock', help='module to test (default: clock)')
    ap.add_argument('--file', default=None, help='plaintext filename to write out')
    args = ap.parse_args()

    mods = parse_modules(BACKUP / 'info.xml')
    tbl, cols = mods.get(args.module, (None, None))
    if not cols:
        sys.exit(f'Module {args.module!r} not found. Available: {sorted(mods.keys())}')

    chk_hex = cols['checkMsgV3']
    enc_hex = cols['encMsgV3']
    print(f'Module: {args.module}  ({tbl})')
    print(f'  checkMsgV3: {len(chk_hex)} hex chars = {len(chk_hex)//2} bytes')
    print(f'  encMsgV3:   {len(enc_hex)} hex chars = {len(enc_hex)//2} bytes')

    for pwd_str in args.password:
        pwd = pwd_str.encode('utf-8')
        ok = check_password(pwd, chk_hex)
        print(f'\n  password = {pwd_str!r}')
        print(f'  checkMsgV3 verify: {"PASS" if ok else "FAIL"}')
        if ok:
            # Also decrypt the actual .db and check it's a valid SQLite file.
            db = BACKUP / f'{args.module}.db'
            if db.exists():
                ct = db.read_bytes()
                pt = decrypt_package(pwd, enc_hex, ct)
                print(f'  decrypted {args.module}.db ({len(ct)} -> {len(pt)} bytes)')
                print(f'  first 32 bytes: {pt[:32]!r}')
                if pt.startswith(b'SQLite format 3'):
                    print(f'  SQLCipher header: OK (valid SQLite)')
                else:
                    print(f'  SQLCipher header: UNEXPECTED')
                out = BACKUP.parent / f'{args.module}.db.plain'
                out.write_bytes(pt)
                print(f'  wrote: {out}')
            else:
                print(f'  (no {args.module}.db file to decrypt)')


if __name__ == '__main__':
    main()
