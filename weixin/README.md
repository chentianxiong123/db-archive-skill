# weixin — 华为微信老版本备份解密

> 从华为 HiSuite 电脑备份解密微信聊天库/app 数据（`encMsgV3` + PBKDF2 + GCM），**媒体原件直接用 `adb` 从手机提取**，统一导出为可交付物。
> **本工具只对微信 7.0.17（Android）+ HiSuite v3 备份格式有效**，详见 [SKILLS.md](SKILLS.md) 的版本适用范围。

## 适用范围（严格）

| 维度 | 适用值 |
|------|--------|
| 微信版本 | **7.0.17**（Android，数据年份 2017–2021） |
| HiSuite 备份格式 | **v3 外层**（`type_attch=3`，`e_perbackupkey`=Null） |
| 多媒体材料 | **Multimedia v2**（`wechat.xml` 每分片独立 `<Iv>`） |
| 实测机型 | 华为 nova 3e (WAS-AL00)，Android 8.0.0 / EMUI 8.0 |

> ⚠️ 微信 8.x / HiSuite v4 备份**不适用**，先跑 `SKILLS.md` 第零步版本判定。

## 两条主路径（核心）

1. **聊天库 + app 数据**（HiSuite 备份）：`decrypt_v3.py` 用**每模块 `encMsgV3`** 派生 `PBKDF2-HMAC-SHA256(pwd, encMsgV3[:32], 5000, 32)` + **AES-256-GCM**（`nonce=encMsgV3[32:48]`，密文||tag 后 16B）→ 明文 `.db` / `com.tencent.mm.data.tar`
2. **媒体原件**（5.1 GB / 77,447 文件）：**`adb pull /sdcard/tencent/MicroMsg/ ./wechat_media`** —— 不需要解密备份里的 `WechatTar/`（那是另一套密钥，昨天 CTR 失败，见附录 A）

## 交付物

```
wechat-backup-decrypt/
├── decrypted/          # 系统 .db (17) + com.tencent.mm.data.tar
├── mmdata/             # 微信 app 数据解包 (12,246 文件)
├── decrypted.db        # EnMicroMsg.db 明文 SQLite (18,152 条消息)
├── messages-text.tsv   # 全部消息制表符导出
├── wechat-chat-export.html
└── wechat_media/       # sdcard 媒体原件树 — adb 直取 (77,447 文件 / 5.1 GB)
```

## 媒体对齐结果（微信 7.0.17 实测）

| type | 媒体 | 路径规则 | 命中 |
|------|------|----------|------|
| 3 | 图片 | `image2/{md5[:2]}/{md5[2:4]}/{md5}.jpg` | 64% |
| 34 | 语音 | `voice2/{xx}/{yy}/msg_{imgPath}.amr` | 100% |
| 43 | 视频 | `video/{imgPath}.mp4` | 100%（403/414 有 .mp4） |
| 47 | 表情 | `emoji/{md5}` | 99.6% |

**关键**：语音/视频 `imgPath` 是 24–26 位 hex（**不是** 32 位 md5），对齐时用 `[0-9a-f]{20,}` 全串匹配。图片 64% 缺失是微信缓存清理的客观缺失，非 bug。

## 使用

1. 跑 [SKILLS.md](SKILLS.md) **第零步**确认版本（v3 + Multimedia v2）
2. **`decrypt_v3.py`** 解模块文件（encMsgV3 + PBKDF2 + GCM，`--verify` 校验 checkMsgV3）
3. **`adb pull /sdcard/tencent/MicroMsg/`** 直取媒体原件（无需解 `WechatTar/`）
4. EnMicroMsg.db 用 SQLCipher 解密（微信库密码）
5. 按上表对齐 message 表 → `media_map.csv`

## 依赖

- Python 3.8+、`pycryptodome`（AES-GCM / PBKDF2）
- `sqlcipher3` / `pysqlcipher3`（EnMicroMsg.db）
- `adb`（Android platform-tools，媒体直取）
- `decrypt_v3.py`（encMsgV3 模块文件解密器）

## 已知坑（详见 SKILLS.md 常见失败 + 附录 A）

- **别用 kobackupdec 解媒体**：`SHA256(密码)[:16]`+CTR 对 `WechatTar/` 失败（媒体是另一套 key）；`decrypt_file` 还有 `dm.iv` hex 字符串 bug
- **媒体走 adb**，别碰 `WechatTar/`（附录 A2/A3 是失败探索存档）
- `checkMsg` 是 null → 用 `decrypt_v3.py` 直接读 `encMsgV3`/`checkMsgV3`
- 语音/视频对齐必须 `[0-9a-f]{20,}`，`{32}` 会漏光
- 长路径 `\\?\` 前缀 + 非法字符替换（解包）

## License

MIT（同项目根）。仅限处理**用户自己设备、自己账号**的数据。
