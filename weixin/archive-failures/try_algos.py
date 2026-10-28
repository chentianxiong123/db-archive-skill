#!/usr/bin/env python3
"""Systematically try all encryption parameter combinations on clock.db."""
import binascii, hashlib, hmac as hmacmod, sys, itertools
from Crypto.Cipher import AES
from Crypto.Util import Counter
from Crypto.Hash import HMAC, SHA1, SHA256, SHA512
from Crypto.Protocol.KDF import PBKDF2

BACKUP = r'C:\Users\a1\Documents\HiSuite\backup\HUAWEI nova youth_2026-10-26 21.47.14'
pwd_str = 'aA123456'
pwd_bytes = pwd_str.encode('utf-8')

CHK_HEX = 'bbf6818905acf43de64353ca37fa80145666ab22ab7fb795afe7db16ccbff2ffcc839147ac0a49fe5d0ea4cc4dfbc193cc39be2d0399d1b03ea312c09d1fc892'
ENC_HEX = '4e7388260e74f025b437d5a32bcd98b5bb8e005d186cc3071dc659372a3495bc3c41be9eab97ea7fc97c2f2616b7a375'

data = open(BACKUP + r'\clock.db', 'rb').read()
print(f'clock.db: {len(data)} bytes')
print(f'password: {pwd_str!r}')

# Sanity: HMAC check (uncognic algorithm)
seed = binascii.unhexlify(CHK_HEX[64:128])
hmac_key = hashlib.pbkdf2_hmac('sha256', pwd_bytes, seed, 5000, dklen=32).hex().encode('ascii')
calc = hmacmod.new(hmac_key, data, hashlib.sha256).hexdigest()
print(f'\n[Sanity] uncognic HMAC check: {"PASS ✓" if calc == CHK_HEX[:64] else "FAIL"}')

# Try many combinations
print('\n=== Trying decryption combinations ===')

enc = binascii.unhexlify(ENC_HEX)
salt_enc = enc[:32]
iv_enc = enc[32:]  # 16 bytes
seed_chk = binascii.unhexlify(CHK_HEX[64:128])  # 32 bytes from checkMsgV3
first32_chk = CHK_HEX[:64]
last32_chk_hex = CHK_HEX[64:128]

hash_algos = [('SHA256', SHA256), ('SHA1', SHA1), ('SHA512', SHA512)]
iterations_list = [1, 1000, 4000, 5000, 10000, 100000, 400000]

def prf_factory(hash_cls):
    def prf(p, s):
        return HMAC.new(p, s, hash_cls).digest()
    return prf

results = []
for hash_name, hash_cls in hash_algos:
    prf = prf_factory(hash_cls)
    for count in iterations_list:
        for dklen in [16, 32]:
            for pwd_variant, pwd_val in [('str', pwd_str), ('bytes', pwd_bytes)]:
                # Try with salt from encMsgV3
                try:
                    key = PBKDF2(pwd_val, salt_enc, dklen, count, prf, hmac_hash_module=None)
                except Exception as e:
                    continue
                for mode_name, mode, mode_nonce in [
                    ('CTR', AES.MODE_CTR, iv_enc),
                    ('CBC', AES.MODE_CBC, iv_enc),
                ]:
                    try:
                        if mode == AES.MODE_CTR:
                            iv_int = int.from_bytes(iv_enc, 'big')
                            ctr = Counter.new(128, initial_value=iv_int, little_endian=False)
                            cipher = AES.new(key, mode, counter=ctr)
                            pt = cipher.decrypt(data)
                        else:
                            cipher = AES.new(key, mode, iv=iv_enc)
                            pt = cipher.decrypt(data)
                    except Exception as e:
                        continue
                    # Check if looks like SQLite
                    if pt.startswith(b'SQLite format 3'):
                        results.append(('SQLITE', f'encSalt+{hash_name}+{count}+dk{dklen}+{mode_name}'))
                        print(f'  ✓ SQLITE! {mode_name}+{hash_name}+{count}+dk{dklen}')
                        break

                # Try with salt from checkMsgV3 seed
                try:
                    key = PBKDF2(pwd_val, seed_chk, dklen, count, prf, hmac_hash_module=None)
                except Exception as e:
                    continue
                for mode_name, mode, mode_nonce in [
                    ('CTR', AES.MODE_CTR, iv_enc),
                ]:
                    try:
                        iv_int = int.from_bytes(iv_enc, 'big')
                        ctr = Counter.new(128, initial_value=iv_int, little_endian=False)
                        cipher = AES.new(key, mode, counter=ctr)
                        pt = cipher.decrypt(data)
                    except Exception as e:
                        continue
                    if pt.startswith(b'SQLite format 3'):
                        results.append(('SQLITE', f'chkSeed+{hash_name}+{count}+dk{dklen}+{mode_name}'))
                        print(f'  ✓ SQLITE! {mode_name}+{hash_name}+{count}+dk{dklen} (from chk seed)')

# Also try direct SHA256(password) as key
for pwd_variant, pwd_val in [('str', pwd_str.encode('utf-8')), ('bytes', pwd_bytes)]:
    for hlen in [16, 32]:
        for hname, hfn in [('SHA256', lambda x: hashlib.sha256(x).digest()),
                          ('SHA512', lambda x: hashlib.sha512(x).digest()),
                          ('SHA1', lambda x: hashlib.sha1(x).digest())]:
            try:
                key = hfn(pwd_val)[:hlen]
                iv_int = int.from_bytes(iv_enc, 'big')
                ctr = Counter.new(128, initial_value=iv_int, little_endian=False)
                cipher = AES.new(key, AES.MODE_CTR, counter=ctr)
                pt = cipher.decrypt(data)
            except Exception as e:
                continue
            if pt.startswith(b'SQLite format 3'):
                print(f'  ✓ SQLITE! {hname}({pwd_variant})[:{hlen}]')
                results.append(('SQLITE', f'{hname}(pwd)[:{hlen}]'))

print(f'\n=== Results: {len(results)} matches ===')
for r in results:
    print(f'  {r}')

if not results:
    print('\nNo combination worked. Trying CBC with PKCS7 unpad check:')
    # Try CBC more carefully - unpad the result
    for hash_name, hash_cls in hash_algos:
        prf = prf_factory(hash_cls)
        for count in iterations_list:
            key = PBKDF2(pwd_bytes, salt_enc, 32, count, prf, hmac_hash_module=None)
            cipher = AES.new(key, AES.MODE_CBC, iv=iv_enc)
            pt = cipher.decrypt(data)
            if pt.startswith(b'SQLite format 3'):
                print(f'  ✓ SQLITE! CBC+{hash_name}+{count}')
                results.append(('SQLITE', f'CBC+{hash_name}+{count}'))

if not results:
    print('\nNone of the combinations produced a SQLite header.')
    print('Trying to check for tar ustar header at offset 257:')
    for hash_name, hash_cls in hash_algos:
        prf = prf_factory(hash_cls)
        for count in [5000, 10000]:
            key = PBKDF2(pwd_bytes, salt_enc, 32, count, prf, hmac_hash_module=None)
            iv_int = int.from_bytes(iv_enc, 'big')
            ctr = Counter.new(128, initial_value=iv_int, little_endian=False)
            cipher = AES.new(key, AES.MODE_CTR, counter=ctr)
            pt = cipher.decrypt(data)
            if pt[257:262] == b'ustar':
                print(f'  ✓ USTAR! CTR+{hash_name}+{count}')
