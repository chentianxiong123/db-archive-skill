#!/usr/bin/env python3
"""
kobackup-decrypt: Decrypt Huawei/Honor HiSuite backups (encrypt_openssl=true)

Decrypts KoBackup encrypted backup files created by HiSuite PC backup
on Huawei/Honor phones running EMUI/Magic UI with KoBackup v14+.

Handles the `encrypt_openssl=true` encryption variant that no other
open-source tool supports.

Encryption scheme (reverse-engineered from LocalBackup.apk):
  - Algorithm: AES-256-GCM
  - Key: PBKDF2-HMAC-SHA256(password, salt, 10000, 32)
  - Salt: encMsgV3[0:64] hex-decoded (32 bytes)
  - Nonce: encMsgV3[64:96] hex-decoded (16 bytes)
  - Auth tag: last 16 bytes of each encrypted file
  - Ciphertext: file contents minus the last 16 bytes

Usage:
  python kobackup_decrypt.py <backup_dir> <password> [--output <dir>]
  python kobackup_decrypt.py <backup_dir> <password> --list
  python kobackup_decrypt.py <backup_dir> <password> --module <name>

Examples:
  python kobackup_decrypt.py "HONOR 20 PRO_2026-07-13 12.35.31" "MyPassword123"
  python kobackup_decrypt.py backup_dir "pass" --module com.whatsapp --output ./decrypted
  python kobackup_decrypt.py backup_dir "pass" --list
  python kobackup_decrypt.py backup_dir "pass" --verify
"""

import argparse
import binascii
import hashlib
import hmac as hmacmod
import io
import os
import re
import sys
import tarfile
from pathlib import Path

try:
    from Crypto.Cipher import AES
except ImportError:
    print("Error: pycryptodome is required. Install with: pip install pycryptodome")
    sys.exit(1)


def parse_info_xml(backup_dir):
    """Parse info.xml to extract encryption metadata per module."""
    info_path = backup_dir / "info.xml"
    if not info_path.exists():
        raise FileNotFoundError(f"info.xml not found in {backup_dir}")

    content = info_path.read_text(encoding="utf-8")

    # Extract global encryption info
    encrypt_openssl = 'encrypt_openssl' in content and 'Boolean="true"' in content.split('encrypt_openssl')[1][:50]

    # Extract per-module info
    modules = {}
    module_pattern = re.compile(
        r'<row table="BackupFileModuleInfo">(.*?)</row>',
        re.DOTALL
    )

    for match in module_pattern.finditer(content):
        block = match.group(1)

        name_m = re.search(r'name="name".*?String="([^"]*)"', block)
        if not name_m:
            continue
        module_name = name_m.group(1)

        enc_msg_m = re.search(r'name="encMsgV3".*?String="([^"]*)"', block)
        check_msg_m = re.search(r'name="checkMsgV3".*?String="([^"]*)"', block)

        modules[module_name] = {
            "name": module_name,
            "encMsgV3": enc_msg_m.group(1) if enc_msg_m else None,
            "checkMsgV3": check_msg_m.group(1) if check_msg_m else None,
        }

    return {
        "encrypt_openssl": encrypt_openssl,
        "modules": modules,
    }


def derive_key(password, salt, iterations=10000):
    """Derive AES-256 key using PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=32,
    )


def decrypt_file(data, key, nonce):
    """Decrypt a single file using AES-256-GCM. Returns plaintext bytes."""
    if len(data) < 16:
        raise ValueError("File too small to contain GCM auth tag")

    ciphertext = data[:-16]
    tag = data[-16:]

    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    return plaintext


def verify_checksum(file_data, password, check_entry):
    """Verify a file's checkMsgV3 HMAC. Returns True if valid."""
    if len(check_entry) < 128:
        return None

    expected_hmac = check_entry[:64]
    seed_hex = check_entry[64:128]

    try:
        seed_bytes = binascii.unhexlify(seed_hex)
    except (ValueError, binascii.Error):
        return None

    pbkdf2_result = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), seed_bytes, 5000, dklen=32
    )
    hmac_key = pbkdf2_result.hex().encode("ascii")
    calculated = hmacmod.new(hmac_key, file_data, hashlib.sha256).hexdigest()

    return calculated == expected_hmac


def parse_check_msg(check_msg_str):
    """Parse checkMsgV3 string into per-file entries."""
    if not check_msg_str:
        return {}

    entries = {}
    for part in check_msg_str.split("**"):
        # Format: hmac(64) + seed(64) + _filename
        # Find the last underscore that separates hex data from filename
        if len(part) < 129:
            continue
        hex_data = part[:128]
        filename_part = part[128:]
        if filename_part.startswith("_"):
            filename_part = filename_part[1:]
        entries[filename_part] = hex_data

    return entries


def find_module_files(backup_dir, module_name):
    """Find all encrypted tar files for a module."""
    # App data tars: <module>_appDataTar/<module>N.tar
    tar_dir = backup_dir / f"{module_name}_appDataTar"
    files = []

    if tar_dir.exists():
        for f in sorted(tar_dir.iterdir()):
            if f.is_file() and f.suffix == ".tar":
                files.append(f)

    # Also check for standalone files like hwKeyChain.db
    standalone = backup_dir / f"{module_name}.db"
    if standalone.exists():
        files.append(standalone)

    # TwinApp tar
    twin = backup_dir / f"{module_name}#TwinApp.tar"
    if twin.exists():
        files.append(twin)

    return files


def decrypt_module(backup_dir, module_info, password, output_dir, verify=False):
    """Decrypt all files for a module."""
    module_name = module_info["name"]
    enc_msg_hex = module_info["encMsgV3"]

    if not enc_msg_hex:
        print(f"  Skipping {module_name}: no encMsgV3 (not encrypted)")
        return False

    enc_msg_bytes = binascii.unhexlify(enc_msg_hex)
    salt = enc_msg_bytes[:32]
    nonce = enc_msg_bytes[32:]

    key = derive_key(password, salt, iterations=10000)

    # Parse checksums
    check_entries = parse_check_msg(module_info.get("checkMsgV3", ""))

    files = find_module_files(backup_dir, module_name)
    if not files:
        print(f"  No encrypted files found for {module_name}")
        return False

    module_out = output_dir / module_name
    module_out.mkdir(parents=True, exist_ok=True)

    decrypted_parts = []

    for filepath in files:
        raw = filepath.read_bytes()
        filename = filepath.name

        # Verify checksum if available
        if verify and filename in check_entries:
            valid = verify_checksum(raw, password, check_entries[filename])
            if valid:
                print(f"    {filename}: checksum OK")
            elif valid is False:
                print(f"    {filename}: CHECKSUM MISMATCH")
            else:
                print(f"    {filename}: checksum could not be verified")

        try:
            plaintext = decrypt_file(raw, key, nonce)
            print(f"    {filename}: {len(raw):,} -> {len(plaintext):,} bytes (GCM verified)")
            decrypted_parts.append((filename, plaintext))
        except ValueError as e:
            print(f"    {filename}: GCM verification failed: {e}")
            # Decrypt without tag verification as fallback
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            plaintext = cipher.decrypt(raw[:-16])
            print(f"    {filename}: {len(plaintext):,} bytes (tag verification failed, use with caution)")
            decrypted_parts.append((filename, plaintext))
        except Exception as e:
            print(f"    {filename}: decrypt error: {e}")
            continue

    if not decrypted_parts:
        return False

    # Check if parts are tar files that should be concatenated
    is_tar = False
    if decrypted_parts:
        first_data = decrypted_parts[0][1]
        if len(first_data) > 262 and first_data[257:262] == b"ustar":
            is_tar = True

    if is_tar:
        # Concatenate all tar parts and extract
        combined = bytearray()
        for _, data in decrypted_parts:
            combined.extend(data)

        combined_path = module_out / f"{module_name}_appdata.tar"
        combined_path.write_bytes(combined)
        print(f"    Combined tar: {combined_path.name} ({len(combined):,} bytes)")

        # Extract tar
        extracted_dir = module_out / "extracted"
        extracted_dir.mkdir(exist_ok=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(bytes(combined))) as tar:
                members = tar.getmembers()
                tar.extractall(extracted_dir, filter="data")
                print(f"    Extracted {len(members)} entries to {extracted_dir}")
        except Exception as e:
            print(f"    Tar extraction error: {e}")
            print(f"    Raw tar saved at: {combined_path}")
    else:
        # Save individual decrypted files
        for filename, data in decrypted_parts:
            out_path = module_out / f"decrypted_{filename}"
            out_path.write_bytes(data)
            print(f"    Saved: {out_path.name}")

    return True


def try_legacy_decrypt(data, password, enc_msg_hex):
    """Try the legacy (non-OpenSSL) decryption: AES-CTR with 5000 iterations."""
    enc_msg_bytes = binascii.unhexlify(enc_msg_hex)
    salt = enc_msg_bytes[:32]
    iv = enc_msg_bytes[32:]

    key = derive_key(password, salt, iterations=5000)
    from Crypto.Util import Counter

    iv_int = int.from_bytes(iv, "big")
    ctr = Counter.new(128, initial_value=iv_int)
    cipher = AES.new(key, AES.MODE_CTR, counter=ctr)
    pt = cipher.decrypt(data[:512])

    if len(pt) > 262 and pt[257:262] == b"ustar":
        return True, key, iv
    return False, None, None


def main():
    parser = argparse.ArgumentParser(
        description="Decrypt Huawei/Honor HiSuite KoBackup encrypted backups",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "path/to/HONOR 20 PRO_2026-07-13" "MyPassword"
  %(prog)s backup_dir "pass" --module com.whatsapp
  %(prog)s backup_dir "pass" --list
  %(prog)s backup_dir "pass" --verify
        """,
    )
    parser.add_argument("backup_dir", help="Path to the HiSuite backup directory")
    parser.add_argument("password", help="Backup encryption password")
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output directory (default: <backup_dir>_decrypted)",
    )
    parser.add_argument(
        "--module", "-m",
        default=None,
        help="Decrypt only this module (e.g., com.whatsapp)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List modules in the backup without decrypting",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify file checksums before decrypting",
    )

    args = parser.parse_args()
    backup_dir = Path(args.backup_dir)

    if not backup_dir.exists():
        print(f"Error: Backup directory not found: {backup_dir}")
        sys.exit(1)

    # Parse backup metadata
    try:
        info = parse_info_xml(backup_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    modules = info["modules"]
    encrypt_openssl = info["encrypt_openssl"]

    print(f"Backup: {backup_dir.name}")
    print(f"Modules: {len(modules)}")
    print(f"encrypt_openssl: {encrypt_openssl}")

    if not encrypt_openssl:
        print("\nWarning: encrypt_openssl is not set. This tool is designed for")
        print("encrypt_openssl=true backups. The standard format may use different")
        print("parameters (AES-CTR, 5000 iterations). Attempting anyway...")

    if args.list:
        print(f"\nModules in backup:")
        for name, mod in modules.items():
            files = find_module_files(backup_dir, name)
            total_size = sum(f.stat().st_size for f in files)
            enc = "encrypted" if mod["encMsgV3"] else "not encrypted"
            print(f"  {name}: {len(files)} files, {total_size:,} bytes ({enc})")
        return

    # Set up output directory
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = backup_dir.parent / f"{backup_dir.name}_decrypted"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {output_dir}")

    # Decrypt modules
    if args.module:
        if args.module not in modules:
            print(f"\nError: Module '{args.module}' not found in backup.")
            print(f"Available: {', '.join(modules.keys())}")
            sys.exit(1)
        target_modules = {args.module: modules[args.module]}
    else:
        target_modules = modules

    success_count = 0
    for name, mod in target_modules.items():
        print(f"\n[{name}]")
        if decrypt_module(backup_dir, mod, args.password, output_dir, args.verify):
            success_count += 1

    print(f"\nDone. Decrypted {success_count}/{len(target_modules)} modules to {output_dir}")


if __name__ == "__main__":
    main()
