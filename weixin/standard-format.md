# 聊天记录标准结构 chat-standard v1.0

> 目标：微信/QQ/Telegram/短信等任何渠道、任何版本导出的聊天记录，都转换到**同一套标准 JSON 结构**。
> 浏览器/导出器只认标准结构；换数据源 = 只换转换器（adapter），不换渲染。

## 核心思想

```
[微信 7.0.17] ──adapter──┐
[微信 4.x]   ──adapter──┼──>  chatdata.json (标准结构) ──> HTML 浏览器 / 导出器
[QQ 各版]    ──adapter──┤
[Telegram]   ──adapter──┘
```

- **标准结构**：`chatdata.json`（唯一的中间格式）
- **adapter**：每个渠道/版本一个转换器，输出标准结构
- **消费端**：浏览器、导出、分析——只读标准结构，不认识任何渠道细节

## 顶层结构

```json
{
  "format": "chat-standard",
  "format_version": "1.0.0",
  "source": {
    "app": "wechat",              // wechat | qq | telegram | sms | other
    "app_version": "7.0.17",      // 渠道版本
    "platform": "android",        // android | ios | windows | macos
    "device": "HUAWEI nova 3e",   // 设备名
    "exported_at": 1769436000000  // 导出时间戳(ms)，可选
  },
  "contacts": [
    {"id": "wxid_vxr0pp6tjdwv22", "name": "昵称", "remark": "备注名",
     "avatar": "media/avatar/xx.jpg", "type": "user"}   // user|group|official|system
  ],
  "sessions": [
    {
      "id": "1582366907@chatroom",   // 会话唯一 id（渠道原始 id 或 hash）
      "name": "红灯、刘书成、周舟…",   // 会话显示名（群名/对方名/公众号名）
      "type": "group",               // group | private | official | system
      "avatar": "",
      "member_count": 15,            // 群成员数（群聊）
      "members": ["wxid_a", "wxid_b"],
      "messages": [
        {
          "id": "12345",             // 消息 id（渠道原始 id）
          "direction": "recv",       // send=我发出 | recv=对方/他人
          "sender": "wxid_xxx",      // 发送者 id（群聊区分成员）
          "sender_name": "刘文秀",    // 发送者显示名
          "timestamp": 1595839565000, // 毫秒时间戳
          "type": "text",            // 见下方消息类型枚举
          "text": "消息内容/标题",     // 文本内容（图片/语音等为描述）
          "media": {                  // 媒体消息才有
            "path": "media/00/xx.jpg", // 本地文件路径（相对或绝对）
            "url": "",                 // 远程 url（可选）
            "thumb": "",               // 缩略图路径（可选）
            "duration": 3.5,           // 语音/视频时长秒（可选）
            "size": 1024               // 字节（可选）
          },
          "reply_to": "prev_msg_id",  // 引用消息 id（可选）
          "raw": {}                    // 渠道原始字段（可选，诊断用）
        }
      ]
    }
  ],
  "stats": {"sessions": 20, "messages": 18152}
}
```

## 消息类型枚举（统一）

| 标准 type | 含义 | 微信 7.0.17 原始 type |
|---|---|---|
| `text` | 文本 | 1 |
| `image` | 图片 | 3 |
| `voice` | 语音 | 34 |
| `video` | 视频 | 43 |
| `emoji` | 表情 | 47 |
| `link` | 链接/卡片 | 49 |
| `file` | 文件 | 49(XML 含 appmsg=file) |
| `location` | 位置 | 48 |
| `redpacket` | 红包 | 49(XML 含 appmsg=redpacket) |
| `transfer` | 转账 | 49(XML 含 appmsg=transfer) |
| `quote` | 引用 | 49(XML 含 refermsg) |
| `system` | 系统通知 | 10000 / 285212721 |
| `unknown` | 未知 | 其他 |

## 方向

- `direction: "send"` = 自己发出（isSend=1）
- `direction: "recv"` = 对方/他人（isSend=0）

## 适配器约定（adapter）

每个渠道写一个转换脚本，**输入渠道原始数据，输出标准 chatdata.json**：

- 微信 7.0.17（HiSuite 备份解密库）: `adapters/wechat_717.py`
- 微信 4.x 电脑版: `adapters/wechat_4x.py`（TODO）
- QQ 各版本: `adapters/qq.py`（TODO）

adapter 职责：
1. 会话/联系人 → `sessions[]`/`contacts[]`（统一 id/name/type）
2. 消息 → `messages[]`（方向/类型枚举/文本/媒体/时间统一）
3. 媒体路径 → `media.path`（指向浏览器可访问的文件）

## 验收（adapter 必须满足）

- 输出的 `format == "chat-standard"`
- 每个 session 有唯一 `id`、非空 `name`、合法 `type`
- 每条 message 有 `direction`(send|recv)、合法 `type`、数字 `timestamp`(ms)
- 文本消息有 `text`；媒体消息有 `media.path`
- 浏览器渲染：零渠道特定逻辑（只读标准结构）
