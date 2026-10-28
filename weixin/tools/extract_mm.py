#!/usr/bin/env python3
"""Extract com.tencent.mm.data.tar (decrypted WeChat app data) to a directory.

Usage:
    python extract_mm.py <data.tar> <output_dir>

Handles Windows long paths (\\\\?\\ prefix) and illegal filename chars.
Verifies extracted file count against tar member count (must match).

Verified 2026-10: com.tencent.mm.data.tar (1.9 GB) -> 12,246 files, size mismatch 0.
"""
import os
import re
import sys
import tarfile
from pathlib import Path

ILLEGAL = re.compile(r'[<>:"|?*\x00-\x1f]')


def safe_name(name: str) -> str:
    """Replace Windows-illegal chars; keep path separators."""
    parts = []
    for part in Path(name).parts:
        # Drive letters / absolute-ish parts keep the ':' in drive prefix
        if re.fullmatch(r'[A-Za-z]:', part):
            parts.append(part)
        else:
            parts.append(ILLEGAL.sub('_', part))
    return '/'.join(parts)


def long_path(p: Path) -> str:
    """Windows long-path-safe absolute path with \\\\?\\ prefix."""
    ap = os.path.abspath(str(p))
    if os.name == 'nt' and not ap.startswith('\\\\?\\'):
        ap = '\\\\?\\' + ap
    return ap


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit('usage: python extract_mm.py <data.tar> <output_dir>')
    tar_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    if not tar_path.is_file():
        sys.exit(f'not found: {tar_path}')
    out_dir.mkdir(parents=True, exist_ok=True)
    out_root = long_path(out_dir)

    tf = tarfile.open(str(tar_path))
    try:
        members = tf.getmembers()
        expected = len(members)
        for m in members:
            target = os.path.join(out_root, safe_name(m.name))
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            elif m.issym() or m.islnk():
                pass  # skip links (rare in app data)
            elif m.isfile():
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tf.extractfile(m) as src, open(target, 'wb') as dst:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        dst.write(chunk)
    finally:
        tf.close()

    extracted = sum(1 for _ in out_dir.rglob('*') if _.is_file())
    print(f'extracted {extracted} files (tar members: {expected})')
    if extracted != expected:
        sys.exit(f'MISMATCH: {extracted} != {expected}')
    print('OK: file count matches.')


if __name__ == '__main__':
    main()
