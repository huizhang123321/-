# FatFish 本地聊天 bot

[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-3776AB.svg)](requirements.txt)

适配 `FatFish.exe`（后端 `resources\server\minimost-server.exe`，默认
`http://127.0.0.1:5000`）的独立 Python bot。

FatFish 官方 API 只有推送方向、没有读取接口，所以这里靠**只读轮询它的 SQLite**
来收消息，再通过 API 或直写数据库回消息。在此之上有一个可选的中间层
[`keihub/`](keihub/README.md)，能把 FatFish、QQ、momoi_bot、DeepSeek Harness
串成一条链路，并允许按频道选择由哪个「大脑」来回复。

## 仓库里有两个东西

| 路径 | 是什么 |
|---|---|
| [`keihub/`](keihub/README.md) | **主项目**：FatFish / QQ(SnowLuma) / momoi_bot / DSH 的中间层，带 Web 控制台、托盘、Windows 服务和打包脚本 |
| [`fatfish_bot.py`](fatfish_bot.py) | 极简单文件版：只接 FatFish，直连 DeepSeek，没有 QQ，也不需要 KeiHub |

下面先讲单文件版，中间的 KeiHub 见 [`keihub/README.md`](keihub/README.md)。

## 接 DSH（DeepSeek Harness）

KeiHub 默认用 momoi_bot 回话。想让 DSH 当大脑，把它挂成一条**可选路径**——
不启用时行为和以前完全一样，不会强制使用 DSH。

### 链路

```
FatFish(messages.db) ──读──→ KeiHub ──WS 3020──→ dsh-hub-bridge ──followup()──→ DSH
                                │                                                  │
                                │        reply{id,session,text} ←── turn/end 取最后一句
                                ↓
                          写回 FatFish（API 或直写 DB）
```

Hub 是 **WebSocket 服务端**（默认 `127.0.0.1:3020`），DSH 侧插件是**客户端**。
两边只传三种帧：

| 方向 | 帧 |
|---|---|
| Hub → 插件 | `{"type":"inbound","id":"…","session":"fatfish:private:1","user":"爱丽丝","text":"在吗"}` |
| 插件 → Hub | `{"type":"reply","id":"…","session":"…","text":"…"}` |
| 插件 → Hub | `{"type":"error","id":"…","session":"…","message":"…"}` |

`id` 是请求号，插件原样带回用于精确配对；旧版插件不带 `id` 也能按 `session` 兜底配对。

### 1. Hub 侧

```yaml
brains:
  dsh:
    enabled: true
    host: "127.0.0.1"
    port: 3020
    reply_timeout_seconds: 180   # 这么久没回就放弃
    fallback_to_bot: true        # 放弃时改投 momoi_bot

routing:
  fatfish_brain: "dsh"    # FatFish 交给 DSH
  onebot_brain: "onebot"  # QQ 仍走 momoi_bot
```

只想让某个 FatFish 频道走 DSH：

```yaml
routing:
  channels:
    "dm:huizhang:key": "dsh"
```

改完重启 Hub。**DSH 没接上时消息会自动回落 momoi_bot，不会静默丢。**

### 2. DSH 侧装插件

用 DSH 自带的 CLI（不用 npx，本机就有）：

```bat
"C:\Users\<你>\AppData\Local\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd" ^
  plugin --profile desktop add "<仓库路径>\keihub\dsh-plugin\dsh-hub-bridge"
```

`plugin add` 只是透传 `pnpm add`，**不会自动挂载**，还得二选一：

- 在 `~/.dsh/profiles/<profile>/package.json` 的 `dsh.profile.bundles` 里加
  `"dsh-hub-bridge"`
- 或在同目录 `cordis.patch.yml` 里加：

  ```yaml
  - insert:
      - id: dsh-hub-bridge
        name: dsh-hub-bridge
  ```

然后重启 DSH。首次启动会自动生成
`%USERPROFILE%\.dsh\hub-bridge-config.json`：

```json
{ "hubUrl": "ws://127.0.0.1:3020", "target": "", "reconnectMs": 3000 }
```

`target` 留空 = 自动挑第一个有 `followup` 的 agent——很可能就是你正在用的会话，
建议填一个专用会话 id 隔离起来。

### 3. 确认连上了

```powershell
(Invoke-RestMethod http://127.0.0.1:8765/api/status).dsh_connected   # True
```

Hub 日志里出现 `keihub.dsh: DSH bridge WS 服务已启动` 和 `DSH 极薄插件已连接`
即表示链路通了；控制台卡片「DSH 插件」也会变绿。

### 不想要 DSH 怎么办

`brains.dsh.enabled: false`（默认），一切照旧走 momoi_bot。
另外 `brains.http` 可以指到任意自建服务，POST 进去同步拿回复，
适合接别的模型或别人的 DSH——详见
[`keihub/README.md`](keihub/README.md) 的「谁来回话」一节。

## 它怎么工作

FatFish 官方 API 只有“推送”方向，没有订阅/读取接口，所以：

- **收**：只读轮询 `%APPDATA%\FatFish\data\users\messages.db`；
- **发**：两种模式
  - `api`：`POST /api/v1/messages`，需要令牌；
  - `db`：直接写 `messages.db`，不需要账号、不需要令牌、不需要登录；
- **想**：DeepSeek（默认复用 `D:\bot配置文件\config.yaml` 的 key）；
- **人设**：默认 `D:\bot配置文件\personas\kei.yaml`。

不会修改 FatFish 安装目录。

## 不登录模式（默认，开箱即用）

`config.json` 默认：

```json
"send_mode": "auto",
"token": "",
"dry_run": false
```

`auto` 在没有 token 时会自动走**直写数据库**：

- 不需要注册 FatFish 账号；
- 不需要生成 API 令牌；
- 不需要登录网页；
- `bot_username`（默认 `kei_bot`）只是消息里显示的发送者名字，不需要真实存在。

bot 写入消息后会顺带把 `presence.db` 的 `event_signal.gen` 加 1，尝试让
已经打开的 FatFish 界面实时收到；如果界面没刷新，切换一下频道或刷新页面即可。

**要求**：FatFish 后端要在运行（桌面快捷方式打开即可），bot 和它在同一台机器。

## 端口自动探测

FatFish 后端**每次启动可能换端口**：`5000` 被占（上一次没退干净、TIME_WAIT 等）
就会挑 `5100`、`5199` 之类。所以 `api_base` 只是首选地址，不是硬性要求。

bot 启动时会：

1. 先试 `api_base`；
2. 不通就从 FatFish 的 `server.log` 尾部倒着读最近用过的端口；
3. 再兜底扫 `5000-5020` / `5100-5110`。

结果缓存 30 秒，发送失败会自动重新探测一次。实际端口变了会打一条日志：

```
INFO FatFish 后端端口已漂移：配置=http://127.0.0.1:5000，实际=http://127.0.0.1:5100
```

## Token 模式（可选，长期更稳）

如果想要官方推送的实时性、@提及、已读状态，建议长期用 token：

1. 在 FatFish 注册一个独立账号，例如 `kei_bot`（不要用本人账号）；
2. 用该账号登录 → 设置 → **API 接入服务** → 生成令牌；
3. 把令牌填进 `config.json` 的 `fatfish.token`，`bot_username` 改成该账号名；
4. `send_mode` 保持 `auto` 或改成 `api`。

令牌只需要生成一次。之后 bot **不需要保持登录**：只要 FatFish 后端在跑，
它就能用令牌发消息。

## 启动

先确认 FatFish 后端正在运行，然后双击：

```
start_bot.bat
```

首次启动会从当前最新消息之后开始，不回复历史。`start_from` 改成
`"beginning"` 才会从最早的消息开始。

停止：窗口里按 `Ctrl+C`。

## 配置说明

| 字段 | 说明 |
|---|---|
| `fatfish.api_base` | FatFish 后端地址，默认 `http://127.0.0.1:5000` |
| `fatfish.server_log_path` | FatFish 的 `server.log`；留空则按 `db_path` 推出来 |
| `fatfish.send_mode` | `auto`：有 token 走 API，没有就直写；`api`：只走 API；`db`：只直写 |
| `fatfish.token` | 可选；设置页「API 接入服务」生成的令牌 |
| `fatfish.db_path` | FatFish 消息库，默认 `%APPDATA%\FatFish\data\users\messages.db` |
| `fatfish.presence_db_path` | 事件信号库，默认 `%APPDATA%\FatFish\data\presence.db` |
| `fatfish.bot_username` | 消息里显示的 bot 名；不登录模式下不需要真实账号 |
| `fatfish.channels` | 要回复的频道；`["*"]` 为全部 |
| `fatfish.start_from` | `latest` 从最新开始；`beginning` 从历史开始 |
| `fatfish.dry_run` | `true` 只打印不发送；默认已关 |
| `fatfish.auto_launch` | 后端没起来时是否自动打开快捷方式 |
| `llm.api_key` | 留空则读取 `llm.fallback_config` 里的 `llm.api_key` |
| `persona.alice_usernames` | FatFish 里算作爱丽丝的用户名，默认 `huizhang` |
| `behaviour.context_messages` | 每次带多少条历史消息给模型 |
| `behaviour.max_backlog_seconds` | 超过这个秒数的积压消息不回复 |

## 调试

只处理一轮，不发送：

```bat
"D:\AI\python.exe" -u fatfish_bot.py --once --dry-run --skip-health
```

发一条新消息到 `general`，观察 `bot.log` 里的 `[dry-run]` 输出。

## 限制

1. **不登录模式是直接写 SQLite**，不是官方接口。FatFish 升级如果改了
   `messages` 表结构，需要同步改 `fetch_new()` / `fetch_context()` /
   `send_fatfish_db()`。
2. 直写模式依赖 FatFish 前端重新拉取消息；`event_signal` 只是尝试触发，
   不保证每条都实时出现在已打开的界面。
3. `bot_username` 不是真实账号时，头像会显示首字母，@提及不会真的提醒到人。
4. FatFish 后端只监听 `127.0.0.1`，bot 必须和它跑在同一台机器上。
5. 不要同时开多个 bot 实例，否则同一条消息可能被回复多次。**尤其不要和
   `keihub\` 同时跑**——两边轮询同一个 `messages.db`，各有各的游标。
   bot 启动时会探测 KeiHub 的 `http://127.0.0.1:8765/api/status`，
   发现它在跑就直接退出（退出码 2）；确实要同时跑就加 `--force`。
6. 令牌等于 bot 账号的发言权，不要提交到仓库或截图外传。

## 许可

[MIT](LICENSE) © 2026 huizhang123321

随便用、改、商用、再发布，保留版权声明即可。仓库里不含任何 FatFish / NapCat /
DeepSeek Harness 的代码，本项目只通过它们的公开接口或本地数据库与之交互；
这些软件各自的许可请见其官方发布页。
