# SKILLS.md — 华为微信老版本备份解密

**给 AI 助手读的操作手册。** 这份文件描述如何从华为 HiSuite 电脑备份解密微信聊天数据（聊天库 + app 数据），以及**用 adb 直接从手机提取 sdcard 媒体原件**，并导出。人类读者请看同目录 [README.md](README.md)。

---

## ⚠️ 版本适用范围（先读这个，不满足就停）

> **本流程只对"微信老版本 + 华为 HiSuite 老备份格式"有效。**
> **对微信 8.x 或 HiSuite 新备份格式（v4+）不适用，不要套用。**

| 维度 | 适用值 | 判定依据 |
|------|--------|----------|
| **微信版本** | **7.0.17**（Android） | 数据年份 2017–2021；`message.imgPath` 为 **25–26 位 hex**（非 32 位 md5） |
| **HiSuite 备份格式** | **v3 外层**（`type_attch=3`） | `info.xml` 的 `BackupFilesTypeInfo`：`e_perbackupkey`=**Null**、`pwkey_salt`=**Null**、`type_attch`=**3** |
| **多媒体材料格式** | **Multimedia v2** | `wechat.xml` 里 `<File>` 每分片独立 `<Iv>`，`version="2"` |
| **机型/系统** | 华为 nova 3e (WAS-AL00)，Android 8.0.0 / EMUI 8.0，hi6250 | `info.xml` 的 `BackupFilePhoneInfo` |

**判定脚本**（备份根目录下跑）——`type_attch==3` 且 `e_perbackupkey` 为 Null 才继续：

```python
import re
info = open('info.xml', encoding='utf-8').read()
m = re.search(r'<row table="BackupFilesTypeInfo">(.*?)</row>', info, re.S)
print(m.group(1) if m else 'no BackupFilesTypeInfo')
# 期望: e_perbackupkey=Null  pwkey_salt=Null  type_attch=Integer 3
```

> 若 `e_perbackupkey` 有值 → 是 v4 备份，密钥材料在 `backupinfo.ini` 的 `pwdsalt`/`pwdsalt_iv`/`password` 字段，**本 skill 不覆盖**（见附录 A3）。

---

## 能力

1. **聊天库**：`EnMicroMsg.db`（SQLCipher）→ 明文 SQLite → 导出 `messages-text.tsv` / `wechat-chat-export.html`
2. **微信 app 数据**：`com.tencent.mm_appDataTar`（39 分卷）→ `com.tencent.mm.data.tar` → 解包到 `mmdata/`（12,246 文件，含 image2 缓存）
3. **sdcard 媒体原件**：**`adb pull` 直接从手机拉取**（原图/视频/语音/表情等，5.1 GB / 77,447 文件）——**不需要解密备份里的 `WechatTar/`**
4. **媒体 ↔ 消息对齐**：把媒体文件按 `message` 表的 `imgPath`/`content` 字段精确映射到聊天记录

依赖：Python 3.8+、`pycryptodome`（AES/PBKDF2）、`sqlcipher3` 或 `pysqlcipher3`（EnMicroMsg.db）、**`adb`（Android platform-tools）**。脚本：`tools/decrypt_v3.py`、`tools/extract_mm.py`、`tools/decrypt_db.py`、`tools/align_media.py`。

## 前置条件

```bash
# 备份根目录（HiSuite 导出的原始备份）
# 形如: .../HiSuite/backup/HUAWEI nova youth_2026-10-26 21.47.14/
# 里面必须有: info.xml  backupinfo.ini  check/  WechatTar/  audio/  document/
ls "备份根目录/"

# 手机（媒体原件来源）—— 见第四步 adb
adb devices        # 手机已连接并授权（USB 调试开）
```

**两个密码**（用户提供，缺一不可）：

| 密码 | 用途 | 说明 |
|------|------|------|
| 外层备份密码 | 解 HiSuite 备份（.db/.tar 模块文件） | 示例 `aA123456` |
| 微信库密码 | 解 EnMicroMsg.db（SQLCipher） | 示例 `555be53`，需用户从微信设置确认 |

> 外层密码用于 `decrypt_v3.py`（encMsgV3 + PBKDF2 + GCM）；微信库密码直接给 SQLCipher。

## 第零步：确认版本（必做，不做会白跑）

跑上面的**判定脚本**。三点全中才继续：

- [ ] `BackupFilesTypeInfo`：`e_perbackupkey`=Null、`pwkey_salt`=Null、`type_attch`=3
- [ ] `wechat.xml` 存在且 `<File>` 每片带独立 `<Iv>`（Multimedia v2）
- [ ] 消息数据年份 ≤ 2021（微信 7.0.17 时代）

## 第一步：外层备份解密（encMsgV3 模块文件）

**实测正确的方案**（`tools/decrypt_v3.py`，已验证 HiSuite 14.0.0.320 备份）。**模块文件**（根下 `*.db`、`*_appDataTar/*.tar`）用 `info.xml` 里**每个模块自己的 `encMsgV3`** 派生密钥：

```
key   = PBKDF2-HMAC-SHA256(password_utf8, encMsgV3[0:32], 5000, 32)
nonce = encMsgV3[32:48]
file  = AES-256-GCM: 明文 = 密文 || tag(最后 16 字节)
```

```python
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Hash import HMAC, SHA256
import binascii

def derive_key(password: bytes, enc_msg_v3_hex: str):
    em = binascii.unhexlify(enc_msg_v3_hex)
    key = PBKDF2(password, em[:32], 32, 5000, lambda p, s: HMAC.new(p, s, SHA256).digest())
    return key, em[32:]          # key, nonce

def gcm_decrypt(data: bytes, key: bytes, nonce: bytes) -> bytes:
    return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(data[:-16], data[-16:])
```

- **每个模块独立 `encMsgV3`**（`parse_info_xml` 返回 `{module: {encMsgV3, checkMsgV3}}`），不是全局一个
- **校验**（`checkMsgV3`）：条目格式 `<hmac64><seed64>_<filename>`，`**` 分隔；`k = hex(pbkdf2_hmac('sha256', password, seed, 5000, 32))`；`hmac_sha256(k, 密文字节) == hmac64`
- **appDataTar 分卷**：只有第 0 分卷带 tar 头（`ustar` 或 512 字节零），其余是流切分——校验时**只查第 0 卷**
- 用 `python tools/decrypt_v3.py <备份根> <密码> <输出> --verify` 一条命令跑完根 `*.db` + `*_appDataTar`（脚本在本仓库 `weixin/tools/`）

输出：`decrypted/` 根下的系统 `.db`（17 个明文）+ `decrypted/com.tencent.mm.data.tar`（1.9 GB 明文 tar）。

## 第二步：微信 app 数据解包

```bash
# tools/extract_mm.py —— 长路径坑: \\?\ 前缀 + os.path.abspath; ':' 等非法字符替换
python tools/extract_mm.py "decrypted/com.tencent.mm.data.tar" "mmdata/"
# 输出: "extracted 12246 files (tar members: 12246)" + OK 即全量
```

## 第三步：EnMicroMsg.db 解密 + 导出聊天

```bash
# tools/decrypt_db.py —— SQLCipher 解密 + WAL 合并 + 导出明文 + tsv/html
python tools/decrypt_db.py <EnMicroMsg.db> <微信库密码> <输出目录>
```

```python
# SQLCipher 解密 (密码 = 微信库密码, 例 555be53)
# 用 sqlcipher3 / pysqlcipher3, 打开后合并 WAL:
#   PRAGMA key='555be53'; PRAGMA wal_checkpoint(TRUNCATE);
# 明文库含 549 对象; message 表 18,152 条 (WAL 已合并)
```

导出为 `messages-text.tsv`（`msgId/type/talker/createTime/imgPath/content`）与 `wechat-chat-export.html`。

## 第四步：媒体原件 — **直接 adb 从手机提取**（主路径）

**sdcard 媒体原件不需要解密备份里的 `WechatTar/`。** 手机还在、能 adb 的话，直接拉，几分钟搞定 5.1 GB：

```bash
# 微信 sdcard 媒体根: /sdcard/tencent/MicroMsg/<hash>/  (每个账号一个 hash 目录)
# 内含 image2/ video/ voice2/ emoji/ image/ bizimg/ record/ music/ favorite/ 共 9 个子目录

# ① 推荐：只拉目标账号（hash 目录名从 mmdata 的 MicroMsg/<hash>/ 或消息 talker 推断）
adb shell ls /sdcard/tencent/MicroMsg/            # 先看有哪些账号目录
adb pull /sdcard/tencent/MicroMsg/<hash>/ ./wechat_media

# ② 备选：整树拉（注意：结果会多一层 <hash>/ 子目录，wechat_media/<hash>/image2/...）
adb pull /sdcard/tencent/MicroMsg/ ./wechat_media
```

**要点**：
- `/sdcard`（`/storage/emulated/0`）普通 adb 即可读，**不需要 root**（adb 属 shell 组，media_rw 可读）
- 拉出的 `wechat_media/` 顶层直接是 9 个媒体子目录（`image2/ video/ voice2/ emoji/ ...`）——即某个账号 `MicroMsg/<hash>/` 的内容
- 实测：77,447 文件 / 5.1 GB（2026-10-28 拉取，微信 7.0.17 数据）
- `audio/`（录音）、`document/`（文档）同理在 sdcard 对应目录，一并 `adb pull`

> **为什么不用备份解密**：`WechatTar/`（6.1 GB，93 片 `.tar.enc`）用的是 Multimedia v2 材料 + **另一套密钥**（`backupinfo.ini` 的 `derivedSalt`/`pwdsalt`/`password`，非外层密码）——昨天的 v3 CTR 尝试失败，**没解通**（见附录 A）。adb 直取绕开全部加密。

## 第五步：媒体 ↔ 消息对齐（核心产出）

```bash
# tools/align_media.py —— message 表 → media_map.csv (逐类型映射)
python tools/align_media.py decrypted.db wechat_media media_map.csv
```

`message` 表的媒体消息（`type`）映射到媒体文件。**微信 7.0.17 老版存储规则**（已逐条实测验证）：

| type | 媒体 | 本地路径规则 | 命名字段 | 实测命中 |
|------|------|-------------|----------|----------|
| **3** | 图片 | `image2/{md5[:2]}/{md5[2:4]}/{md5}.jpg`（`th_` 前缀=缩略图） | `imgPath` 里 `THUMBNAIL_DIRPATH://th_XXXX` 的 XXXX = **md5** | 4,422/6,913 = **64%** |
| **34** | 语音 | `voice2/{xx}/{yy}/msg_{imgPath}.amr` | **`imgPath` 全串 = 文件名**（26 hex，**不是 md5**） | 867/867 = **100%** |
| **43** | 视频 | `video/{imgPath}.mp4`（扁平目录，同名 `.jpg` 缩略图） | **`imgPath` 全串 = 文件名**（24–25 hex） | 414/414 = **100%**（403/414 有 .mp4） |
| **47** | 表情 | `emoji/{md5}`（扁平，`_cover`=封面） | `content` 里 `md5="XXXX"` 或 `imgPath` 32hex | 505/507 = **99.6%** |

### ⚠️ 最大的坑：imgPath 不是标准 32 位 md5！

语音/视频的 `imgPath` 是 **24–26 位 hex**（不是 32 位）。用 `re.findall(r'[0-9a-f]{32}', imgPath)` 提取候选会**全部漏掉**（正则要求恰好 32 位）→ 对齐 0%。**必须用 `[0-9a-f]{20,}` 全串匹配**：

```python
import re
if re.fullmatch(r'[0-9a-fA-F]{20,}', imgPath.strip()):
    key = imgPath.strip().lower()      # 直接就是文件名（去 msg_/扩展名）
```

图片缺失（64%）**不是映射失败**——是微信已自动清理的缓存或 CDN 未下载图，手机里确实没有。语音/视频缺失同理（视频 11 条只剩 `.jpg` 缩略图，原 `.mp4` 已被 sdcard 清理）。

## 输出契约

交付物（用户指定输出目录，例 `C:\Users\a1\Desktop\wechat-backup-decrypt\`）：

```
wechat-backup-decrypt/
├── decrypted/                      # 系统 .db (17) + com.tencent.mm.data.tar
├── mmdata/                         # 微信 app 数据解包 (12,246 文件)
├── decrypted.db                    # EnMicroMsg.db 明文 SQLite
├── messages-text.tsv               # 全部消息 (18,152 条) 制表符分隔
├── wechat-chat-export.html         # 人类可读聊天导出
└── wechat_media/                   # sdcard 媒体原件树 — adb 直取 (77,447 文件 / 5.1 GB)
    ├── image2/  video/  voice2/  emoji/  image/  bizimg/  record/  music/  favorite/
```

**媒体对齐产出**（可选，推荐生成）：`media_map.csv`，每行 `msgId,type,talker,createTime,media_path`——把每条媒体消息钉到具体文件，缺失的行留空。

## 校验

```bash
# 1. app 数据全量性: 解包文件数 vs tar 成员数
tar -tf com.tencent.mm.data.tar | wc -l        # 应 = 解包出的文件数 (12,246)
# 2. 聊天库完整性: message 行数 + WAL 已合并
sqlite3 decrypted.db "SELECT COUNT(*) FROM message"    # 18,152
# 3. 媒体完整性: adb 拉取后文件数/大小对账
find wechat_media -type f | wc -l                     # 77,447 (去 .nomedia)
du -sh wechat_media                                   # 5.1 GB
# 4. 媒体对齐: 逐类型命中率 (见第五步表), 语音/视频应 100%
```

## 常见失败

| 现象 | 原因 | 处理 |
|------|------|------|
| `crypto_init` 的 `good` 永远 False | 用了 kobackupdec 的 checkMsg 校验，但 HiSuite 14 字段是 `checkMsgV3` | 用 `decrypt_v3.py`（encMsgV3 路径），不要走 kobackupdec 的 checkMsg 分支 |
| `decrypt_file` 抛 `int.from_bytes` 错 | `dm.iv` 是 hex **字符串**（kobackupdec 历史 bug） | 别用 kobackupdec 的 `decrypt_file` 解媒体；媒体走 adb（第四步） |
| 解 `.enc` 得随机字节（非 tar 头） | 用 SHA256(密码)[:16] CTR 解媒体——**密钥不对**（媒体是另一套 key） | **直接 adb 拉**，别解 `WechatTar/`（附录 A） |
| `decrypted/` 没有 `MicroMsg*.tar` | `WechatTar/` 不在模块文件范围，本就不需要 | 媒体走 adb；`decrypt_v3.py` 只产出 `*_appDataTar` |
| 语音/视频对齐 0% | 用 `[0-9a-f]{32}` 正则提取 imgPath（要求恰好 32 位） | 改用 `[0-9a-f]{20,}` 全串匹配（第五步坑） |
| 图片对齐 ~64% | 微信已清理缓存 / CDN 图未下载 | **不是 bug**，手机里确实没有，如实报告 |
| 解包报文件名非法 | `:` 等 Windows 非法字符 / 长路径 | `\\?\` 前缀 + `os.path.abspath` + 非法字符替换 |
| `adb pull` 拒绝访问 sdcard | 手机 USB 调试未授权 / 存储权限 | 手机开 USB 调试并授权；`adb devices` 先确认 |

## 硬约束

- **不要**把原始备份、`decrypted.db`、`mmdata/`、`wechat_media/` 提交进 git（项目 `.gitignore` 已排除 `*.db`/`*chat_jsonl/`，仍需注意）
- **不要**把备份密码、微信库密码写进提交的脚本或文档（用占位符 `aA123456`/`555be53` 示例，真实密码仅存本地）
- **只处理用户自己设备、自己账号**的数据；不用于绕过他人保护
- **微信 8.x / HiSuite v4 备份不要硬套**——先跑第零步版本判定，不满足就停
- 图片 64% / 视频缺 11 条 **不是 bug**，是微信缓存清理的客观缺失，如实报告不粉饰

---

## 附录 A：失败探索记录（保留备查，勿再走弯路）

> 昨天的弯路。**主路径已明确（encMsgV3+GCM 解模块文件 / adb 直取媒体），以下仅存档，别再尝试。** 失败脚本在 `archive-failures/`。

### A1. kobackupdec 的 `SHA256(密码)[:16]` + CTR 解 `WechatTar` —— **失败**

`bkey = 密码` → `bkey_sha256 = SHA256(password)[:16]`（例 `1967bfa68e40013348762184193c2a45`），`Counter.new(128, int(iv,16))` 解 `MicroMsg0.tar.enc` → **随机字节，非 tar 头**。原因：Multimedia 用**另一套密钥**，不是外层密码的 SHA256。

### A2. Multimedia tars 密钥在 `backupinfo.ini` —— **未解通**

`decrypt_v3.py` docstring 原文：*"Multimedia tars (WechatTar/, audio/, document/, per-file IV in *.xml) use a different key (backupinfo.ini derivedSalt/pwdsalt/password) — not implemented here."*
`backupinfo.ini`（`check/` 与根下两份相同）：
```
derivedSalt = 53369352C7B0851818A7B36D4644BA2A    # 16B
pwdsalt     = 7CBA...3158                          # 32B
pwdsalt_iv  = C8F3A6CC1C1977303DF687C6A5F394EE    # 16B (GCM nonce)
password    = rnV5b6...                            # base64, ~88B (GCM 加密的 bkey 材料)
```
方向对，但没实现/验证成功。**不需要了**——adb 直取。

### A3. v4 `derive_bkey`（`e_perbackupkey`/`pwkey_salt`）—— **主 info.xml 为 Null**

`kobackup_v4.py` 的 `derive_bkey`（PBKDF2 10000 + GCM，`pwkey_salt[:16]`=salt、`[16:]`=nonce、`e_perbackupkey[:32]`=ct、`[32:]`=tag）**需要** `e_perbackupkey`/`pwkey_salt` 有值。本备份 `BackupFilesTypeInfo` 里这俩是 **Null**（`type_attch=3` 走 v3 路径），故 v4 分支不适用。

### A4. `checkMsg`（老字段）vs `checkMsgV3` —— **字段更名**

HiSuite 14 的 `info.xml` 老字段 `checkMsg` 是 **null**，换成 `checkMsgV3`（64B hex）。kobackupdec 补丁 `checkMsgV3`→`checkMsg` 后密码校验能过，但那只影响 kobackupdec 内部；**正确路径是 `decrypt_v3.py` 直接读 `encMsgV3`/`checkMsgV3`**（第一步）。

### A5. `wechat.xml`/`audio.xml`/`document.xml`（Multimedia v2 材料）

`WechatTar/wechat.xml`（10,298 B）：93 个 `<File><Path>\MicroMsgN.tar</Path><Iv>HEX16B</Iv>`（每分片独立 IV）。`audio.xml`（`audio0-6.tar.enc`）、`document.xml`（`file0.tar.enc`）同格式。**这些是解密 `WechatTar/` 才需要的材料——adb 直取后不需要**。
