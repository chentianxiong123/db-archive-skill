#!/usr/bin/env python3
"""
Huawei KoBackup v4 decryptor
Handles BOPD-mode backups (backupVersion >= 29, KoBackup >= 12.x)

Algorithm (confirmed via APK reverse engineering of KoBackup 12.0.0.360):
  1. K          = PBKDF2-HMAC-SHA256(password, pwkey_salt[:16], iterations=10000)
  2. bkey_bytes = AES-256-GCM decrypt(K, nonce=pwkey_salt[16:], ct=e_perbackupkey[:32])
                  tag = e_perbackupkey[32:]  (raises on wrong password)
  3. bkey_str   = bkey_bytes.decode('utf-8')  # hex string, e.g. "d0f724d6..."
  4. Per module:
       seed     = encMsgV3_bytes[:32]
       iv       = encMsgV3_bytes[32:]
       file_key = PBKDF2-HMAC-SHA256(bkey_str.encode('utf-8'), seed, iterations=5000)
  5. Each file  = AES-256-CTR(file_key, iv, big-endian counter).decrypt(ciphertext)

NOTE: differs from kobackupdec.py in three ways:
  - SHA-256 not SHA-1 for PBKDF2
  - 10000 iterations (not 5000) for bkey derivation
  - bkey is used as a UTF-8 hex string, not raw bytes
"""

import argparse
import binascii
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from Crypto.Cipher import AES
    from Crypto.Hash import HMAC, SHA256
    from Crypto.Protocol.KDF import PBKDF2
    from Crypto.Util import Counter
except ImportError:
    print("ERROR: pycryptodome is required.  pip install pycryptodome")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Directory names inside the backup's media/ folder that share a module key
# ---------------------------------------------------------------------------
MODULE_MEDIA_DIRS = {
    "photo": ["photo", "pictures"],
    "video": ["video", "movies"],
    "audio": ["audio", "audios"],
}


# ---------------------------------------------------------------------------
# Core crypto helpers
# ---------------------------------------------------------------------------

def _prf_sha256(p, s):
    return HMAC.new(p, s, SHA256).digest()


def derive_bkey(password: str, e_perbackupkey_hex: str, pwkey_salt_hex: str) -> str:
    """Derive the per-backup key string from the user password.

    Returns the bkey as a UTF-8 hex string (e.g. "d0f724d6...").
    Raises ValueError if the GCM authentication tag is invalid (wrong password).
    """
    pwd_bytes = password.encode("utf-8")
    pwkey_salt = binascii.unhexlify(pwkey_salt_hex.strip())
    pw_salt = pwkey_salt[:16]
    nonce   = pwkey_salt[16:]

    epbk    = binascii.unhexlify(e_perbackupkey_hex.strip())
    epbk_ct = epbk[:32]
    epbk_tag = epbk[32:]

    K = PBKDF2(pwd_bytes, pw_salt, 32, 10000, _prf_sha256)
    cipher = AES.new(K, AES.MODE_GCM, nonce=nonce)
    bkey_bytes = cipher.decrypt(epbk_ct)
    cipher.verify(epbk_tag)  # raises ValueError on wrong password
    return bkey_bytes.decode("utf-8")


def derive_file_key(bkey_str: str, encmsgv3_hex: str):
    """Return (file_key_bytes, iv_bytes) for a backup module."""
    encmsgv3 = binascii.unhexlify(encmsgv3_hex.strip())
    seed = encmsgv3[:32]
    iv   = encmsgv3[32:]
    file_key = PBKDF2(bkey_str.encode("utf-8"), seed, 32, 5000, _prf_sha256)
    return file_key, iv


def decrypt_file(file_key: bytes, iv_bytes: bytes, src: Path, dst: Path) -> None:
    """Decrypt one file from src to dst (AES-256-CTR, big-endian counter)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    ctr = Counter.new(128, initial_value=int.from_bytes(iv_bytes, "big"), little_endian=False)
    cipher = AES.new(file_key, AES.MODE_CTR, counter=ctr)
    dst.write_bytes(cipher.decrypt(src.read_bytes()))


# ---------------------------------------------------------------------------
# info.xml parser
# ---------------------------------------------------------------------------

def parse_info_xml(xml_path: Path):
    """Parse info.xml.

    Returns (modules, e_perbackupkey_hex, pwkey_salt_hex).
    modules is a dict  module_name -> encMsgV3_hex.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    modules = {}
    e_pbk   = None
    pwkey   = None

    for row in root.iter("row"):
        table = row.get("table", "")

        # Build column_name -> string_value for this row
        cols = {}
        for col in row.findall("column"):
            col_name = col.get("name", "")
            val_el   = col.find("value")
            if val_el is not None:
                for attr in ("String", "Integer", "Long", "Boolean"):
                    v = val_el.get(attr)
                    if v is not None:
                        cols[col_name] = v
                        break

        if table == "BackupFilesTypeInfo":
            e_pbk = cols.get("e_perbackupkey")
            pwkey = cols.get("pwkey_salt")

        if table in ("BackupFileModuleInfo",
                     "BackupFileModuleInfo_Media",
                     "BackupFileModuleInfo_SystemData"):
            name     = cols.get("name")
            encmsgv3 = cols.get("encMsgV3")
            if name and encmsgv3:
                modules[name] = encmsgv3.strip()

    return modules, e_pbk, pwkey


# ---------------------------------------------------------------------------
# Main decryption logic
# ---------------------------------------------------------------------------

def decrypt_backup(backup_dir: Path, output_dir: Path, password: str,
                   verbose: bool = False) -> None:

    session_dir = _find_session_dir(backup_dir)
    info_xml    = session_dir / "info.xml"

    print(f"Backup  : {backup_dir}")
    print(f"Session : {session_dir.name}")
    print(f"Output  : {output_dir}")
    print()

    modules, e_pbk, pwkey = parse_info_xml(info_xml)
    if not e_pbk or not pwkey:
        raise RuntimeError("Could not find e_perbackupkey / pwkey_salt in info.xml")
    print(f"Modules : {', '.join(modules)}")
    print()

    print("Deriving per-backup key …")
    try:
        bkey_str = derive_bkey(password, e_pbk, pwkey)
    except ValueError:
        raise SystemExit("ERROR: GCM authentication failed — wrong password?")
    print("  GCM tag: VALID")
    print()

    total_ok  = 0
    total_err = 0

    # ---- media files -------------------------------------------------------
    media_root = backup_dir / "media" / session_dir.name
    if media_root.is_dir():
        for mod_name, encmsgv3 in modules.items():
            candidate_dirs = MODULE_MEDIA_DIRS.get(mod_name, [mod_name])
            dirs = [media_root / d for d in candidate_dirs if (media_root / d).is_dir()]
            if not dirs:
                continue

            file_key, iv = derive_file_key(bkey_str, encmsgv3)

            for mdir in dirs:
                files = [f for f in mdir.rglob("*") if f.is_file()]
                print(f"[media/{mdir.name}]  {len(files)} files")
                for i, src in enumerate(files):
                    dst = output_dir / src.relative_to(media_root)
                    try:
                        decrypt_file(file_key, iv, src, dst)
                        total_ok += 1
                        if verbose or i < 2 or (i + 1) % 500 == 0:
                            print(f"  {i+1}/{len(files)}  {src.name}")
                    except Exception as exc:
                        total_err += 1
                        print(f"  ERROR {src.name}: {exc}")

    # ---- database / system files -------------------------------------------
    db_root = session_dir   # same as backupFiles1/<session>/
    for mod_name, encmsgv3 in modules.items():
        db_files = []
        for ext in (".db", ".tar", ".zip", ""):
            f = db_root / (mod_name + ext)
            if f.is_file():
                db_files.append(f)
        subdir = db_root / mod_name
        if subdir.is_dir():
            db_files.extend(f for f in subdir.rglob("*") if f.is_file())
        if not db_files:
            continue

        file_key, iv = derive_file_key(bkey_str, encmsgv3)
        print(f"[db/{mod_name}]  {len(db_files)} file(s)")
        for src in db_files:
            dst = output_dir / "databases" / src.relative_to(db_root)
            try:
                decrypt_file(file_key, iv, src, dst)
                total_ok += 1
                if verbose:
                    print(f"  {src.name}")
            except Exception as exc:
                total_err += 1
                print(f"  ERROR {src.name}: {exc}")

    print()
    print(f"Done: {total_ok} files decrypted, {total_err} errors")
    print(f"Output: {output_dir}")


def _find_session_dir(backup_dir: Path) -> Path:
    """Return the session directory inside backupFiles1/."""
    base = backup_dir / "backupFiles1"
    if not base.is_dir():
        raise FileNotFoundError(f"backupFiles1/ not found in {backup_dir}")
    sessions = [d for d in base.iterdir() if d.is_dir()]
    if not sessions:
        raise FileNotFoundError(f"No session directory found in {base}")
    if len(sessions) > 1:
        # Pick the most recently modified one
        sessions.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        print(f"WARNING: multiple sessions found, using newest: {sessions[0].name}")
    return sessions[0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Decrypt a Huawei KoBackup v4 (BOPD) backup.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("backup_dir",  help="Root backup directory (contains backupFiles1/ and media/)")
    parser.add_argument("output_dir",  help="Directory where decrypted files will be written")
    parser.add_argument("password",    help="Backup password")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print every file name")
    args = parser.parse_args()

    decrypt_backup(
        backup_dir  = Path(args.backup_dir),
        output_dir  = Path(args.output_dir),
        password    = args.password,
        verbose     = args.verbose,
    )


if __name__ == "__main__":
    main()
