#!/usr/bin/env python3
"""Fast password brute-force against clock module checkMsgV3 + encMsgV3."""
import binascii
import itertools
import sys
import xml.dom.minidom as minidom
from concurrent.futures import ThreadPoolExecutor, as_completed

from Crypto.Hash import HMAC, SHA256
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Util.strxor import strxor

BACKUP = r'C:\Users\a1\Documents\HiSuite\backup\HUAWEI nova youth_2026-10-26 21.47.14\info.xml'

# Clock module values
CHK_HEX = 'bbf6818905acf43de64353ca37fa80145666ab22ab7fb795afe7db16ccbff2ffcc839147ac0a49fe5d0ea4cc4dfbc193cc39be2d0399d1b03ea312c09d1fc892'
ENC_HEX = '4e7388260e74f025b437d5a32bcd98b5bb8e005d186cc3071dc659372a3495bc3c41be9eab97ea7fc97c2f2616b7a375'

chk = binascii.unhexlify(CHK_HEX)
target = chk[:32]
salt = chk[32:]

# Also grab clock.db first 64 bytes so we can try decryption and check for 'SQLite format 3'
DBPATH = r'C:\Users\a1\Documents\HiSuite\backup\HUAWEI nova youth_2026-10-26 21.47.14\clock.db'
DB_HEAD = open(DBPATH, 'rb').read(16)

DKLEN = 32
COUNT = 5000
PRF = lambda p, s: HMAC.new(p, s, SHA256).digest()


def try_pwd(pwd: bytes):
    res = PBKDF2(pwd, salt, DKLEN, COUNT, PRF, hmac_hash_module=None)
    return res == target


# Candidate list — user said aA123456; test many reasonable variants.
BASES = ['aA123456', 'Aa123456', 'aaaAAA123456', 'aaa123456', '123456', '12345678',
         '123456789', '1234567890', 'password', 'Password', 'qwerty', 'QWERTY',
         'huawei', 'Huawei', 'huawei123', 'wechat', 'WeChat', '111111', '000000',
         '666666', '888888', '999999', 'aA123', 'AA123456', 'a123456', 'a1A123456',
         'aA1234', 'aA12345', 'aA1234567', 'aA12345678', 'aA123456789',
         'aA12345!', 'aA123456!', 'aA1234567!', 'aA12345678!', 'aA123456789!',
         'aA1234567890', 'Aaa123456', 'aaAAA123456', 'aaaAAA123', 'aaaAAA1234',
         'aaaAAA12345', 'aAAA123456', 'AAaaa123456', 'aaa123', 'aa1234',
         'a123', 'aA12', 'aA1', '1', '1234', '12345', '0000', '1111', '1234567',
         'aaa', 'aaaA', 'aaaAAA', 'aA', 'Aa', 'AA', 'aa', '12345678910',
         'aaa111', 'AAA123456', '11111111', 'aaaa1234', 'aA123456789',
         'Huawei123', 'Huawei@123', 'hisuite', '1111111111', '123456789a',
         'a123456789', 'a123456789!', 'a1234567', 'a12345678']

suffixes = ['', '!', '?', '.', '@', '#', '_', '1', '123']

CANDIDATES = []
for b in BASES:
    CANDIDATES.append(b)
    for s in suffixes:
        CANDIDATES.append(b + s)
        CANDIDATES.append(s + b)

# Also try common Chinese pinyin + name patterns
CANDIDATES += [
    'zhongguo', 'CHINA', 'chinese', 'CHINA2026', 'zhongguo123',
    'dad', 'father', 'baba', 'baba123',
    '13579', '135792468', '13572468', '24681357', '121212',
    '2026', '20261026', '2025', '2024', '20261026aA',
    '955243875', '865121031509928', '955243875955243875',
    'aA123456955243875', 'aA123456865121031509928',
    'uemef693x0dy22',
]

# Strip NUL for utf-16 variants
def variants(pwd: str):
    yield ('utf8', pwd.encode('utf-8'))
    yield ('utf16le', pwd.encode('utf-16le'))
    yield ('utf16be', pwd.encode('utf-16be'))
    yield ('gbk', pwd.encode('gbk', 'ignore'))


tested = 0
found = False
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {}
    for pwd in CANDIDATES:
        for label, b in variants(pwd):
            futs[ex.submit(try_pwd, b)] = (pwd, label)
            tested += 1
    for fut in as_completed(futs):
        pwd, label = futs[fut]
        try:
            if fut.result():
                print(f'!!! MATCH !!! pwd={pwd!r} enc={label}')
                found = True
        except Exception as e:
            print(f'error: {pwd} {e}')

print(f'\nTested {tested} (pwd x encoding) combinations. Match: {found}')
