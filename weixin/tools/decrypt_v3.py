#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decrypt_v3.py — Decrypt Huawei HiSuite 14 PC backups (encMsgV3 / checkMsgV3 format)

Reverse-engineered scheme (verified against a HiSuite 14.0.0.320 backup,
HUAWEI nova youth, EMUI 8, backupapp_version=2):

  Module files (.db, *_appDataTar/*.tar):
    key   = PBKDF2-HMAC-SHA256(password_utf8, encMsgV3[0:32], 5000, 32)
    nonce = encMsgV3[32:48]
    file  = AES-256-GCM, plaintext = ct || tag(last 16 bytes)

  Integrity (checkMsgV3):
    per entry "<hmac64><seed64>_<filename>", entries split by "**"
    k = hex(pbkdf2_hmac('sha256', password, seed, 5000, 32)).encode('ascii')
    hmac_sha256(k, ciphertext_bytes) == hmac64

  Multimedia tars (WechatTar/, audio/, document/, per-file IV in *.xml)
  use a different key (backupinfo.ini derivedSalt/pwdsalt/password) — not
  implemented here.

Usage:
  python decrypt_v3.py <backup_dir> <password> <output_dir> [--verify] [--module NAME]
"""
import argparse
import binascii
import hashlib
import hmac as hmacmod
import os
import sys
import xml.dom.minidom as minidom
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Hash import HMAC, SHA256
from Crypto.Protocol.KDF import PBKDF2

DOCSTRING = __doc__ or ''

# PBKDF2 constants for module-file key derivation (verified: 5000 + GCM).
COUNT = 5000
DKLEN = 32


def prf(p, s):
    return HMAC.new(p, s, SHA256).digest()


def parse_info_xml(xml_path):
    """Return {module_name: {'encMsgV3': hex, 'checkMsgV3': hex}} for every
    BackupFileModuleInfo* row."""
    dom = minidom.parse(str(xml_path))
    modules = {}
    for row in dom.getElementsByTagName('row'):
        table = row.getAttribute('table')
        if not table.startswith('BackupFileModuleInfo'):
            continue
        cols = {}
        for c in row.getElementsByTagName('column'):
            v = c.firstChild
            val = None
            if v is not None and v.tagName == 'value':
                for attr in ('String', 'Integer', 'Long', 'Boolean', 'Null'):
                    if v.hasAttribute(attr):
                        val = v.getAttribute(attr)
                        break
            cols[c.getAttribute('name')] = val
        name = cols.get('name')
        if name and cols.get('encMsgV3'):
            modules[name] = cols
    return modules


def check_entries(check_msg_v3):
    """Parse checkMsgV3 into [(expected_hmac_hex, seed_hex, filename)]. For the
    single-file form (128 hex chars) filename is None."""
    if not check_msg_v3:
        return []
    parts = [p for p in check_msg_v3.split('**') if p]
    out = []
    for part in parts:
        if len(part) >= 128:
            out.append((part[:64], part[64:128], part[128:].lstrip('_')))
        else:
            out.append((None, None, part))
    return out


def derive_key(password: bytes, enc_msg_v3_hex: str):
    em = binascii.unhexlify(enc_msg_v3_hex)
    if len(em) != 48:
        raise ValueError(f'encMsgV3 must be 48 bytes, got {len(em)}')
    key = PBKDF2(password, em[:32], DKLEN, COUNT, prf, hmac_hash_module=None)
    return key, em[32:]


def gcm_decrypt(data: bytes, key: bytes, nonce: bytes) -> bytes:
    if len(data) < 16:
        raise ValueError('file too small to carry a GCM tag')
    return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(data[:-16], data[-16:])


def gcm_decrypt_stream(src: Path, dst: Path, key: bytes, nonce: bytes) -> bytes:
    """Decrypt whole file; returns first 512 bytes of plaintext for magic checks."""
    data = src.read_bytes()
    pt = gcm_decrypt(data, key, nonce)
    dst.write_bytes(pt)
    return pt[:512]


def verify_hmac(file_data: bytes, password: bytes, entry) -> bool:
    """entry = (expected_hmac_hex, seed_hex, filename)"""
    expected, seed_hex, _ = entry
    if not expected:
        return None
    try:
        seed = binascii.unhexlify(seed_hex)
    except (ValueError, binascii.Error):
        return None
    pbkdf2_result = hashlib.pbkdf2_hmac('sha256', password, seed, 5000, dklen=32)
    hmac_key = pbkdf2_result.hex().encode('ascii')
    return hmacmod.new(hmac_key, file_data, hashlib.sha256).hexdigest() == expected


def decrypt_module_file(src: Path, dst: Path, enc_hex: str, password: bytes) -> bytes:
    """Decrypt one module file (GCM). Returns plaintext tail."""
    key, nonce = derive_key(password, enc_hex)
    return gcm_decrypt_stream(src, dst, key, nonce)


def main():
    ap = argparse.ArgumentParser(description='Decrypt Huawei HiSuite v3 (encMsgV3) backups')
    ap.add_argument('backup_dir')
    ap.add_argument('password')
    ap.add_argument('output_dir')
    ap.add_argument('--verify', action='store_true', help='verify checkMsgV3 HMACs')
    ap.add_argument('--module', default=None, help='only decrypt this module')
    args = ap.parse_args()

    backup = Path(args.backup_dir)
    out = Path(args.output_dir)
    if not backup.is_dir():
        sys.exit(f'backup dir not found: {backup}')
    pwd = args.password.encode('utf-8')

    modules = parse_info_xml(backup / 'info.xml')
    if not modules:
        sys.exit('no BackupFileModuleInfo rows found in info.xml')
    print(f'{len(modules)} modules')
    for m, cols in modules.items():
        print(f'  {m:24s} enc={len(cols["encMsgV3"])//2}B chk={"yes" if cols.get("checkMsgV3") else "no"}')

    ok = skipn = fail = 0
    entries_by_file = {}

    def handle_file(src: Path, rel: str, module: str):
        nonlocal ok, skipn, fail
        if args.module and module != args.module:
            return
        cols = modules[module]
        enc_hex = cols['encMsgV3']
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            head = decrypt_module_file(src, dst, enc_hex, pwd)
            status = 'ok'
            if args.verify:
                chkv3 = cols.get('checkMsgV3') or ''
                entries = check_entries(chkv3)
                if entries and not (len(entries) == 1 and entries[0][2] is None or entries[0][2] == src.name):
                    pass  # per-file map; see below
                # HMAC over the ciphertext (the encrypted file bytes)
                ok_hmac = None
                for entry in entries:
                    if entry[2] is None or entry[2] == src.name:
                        with open(src, 'rb') as fh:
                            fdata = fh.read()
                        ok_hmac = verify_hmac(fdata, pwd, entry)
                        if ok_hmac:
                            break
                if ok_hmac is False:
                    status = 'HMACMISMATCH'
            if src.suffix == '.db' and not head.startswith(b'SQLite format 3'):
                status = 'NOTSQLITE'
            # Only the first app-data shard carries the tar header; the rest
            # are stream cuts, so only check shard 0 for ustar.
            import re as _re
            m = _re.search(r'(\d+)\.tar$', src.name)
            first = m and m.group(1) == '0'
            if (src.suffix == '.tar' and first
                    and not (head[257:262] == b'ustar' or head[:512] == b'\0' * 512)):
                status = 'NOTTAR'
            print(f'  [{status:12s}] {rel} ({dst.stat().st_size:,} B)')
            if status == 'ok':
                ok += 1
            else:
                fail += 1
        except Exception as e:
            print(f'  [FAIL] {rel}: {e}')
            fail += 1

    # --- root *.db files ------------------------------------------------
    for db in sorted(backup.glob('*.db')):
        mod = db.stem
        if mod in modules:
            handle_file(db, db.name, mod)
        else:
            print(f'  [SKIP no-module] {db.name}')
            skipn += 1

    # --- *_appDataTar shards ---------------------------------------------
    for tdir in sorted(backup.glob('*_appDataTar')):
        mod = tdir.name[:-len('_appDataTar')]
        if mod not in modules:
            print(f'  [SKIP no-module] {tdir.name}')
            skipn += 1
            continue
        for tar in sorted(tdir.glob('*.tar')):
            handle_file(tar, f'{tdir.name}/{tar.name}', mod)

    print(f'\nDecrypted OK: {ok}, skipped: {skipn}, failed: {fail}')


if __name__ == '__main__':
    main()