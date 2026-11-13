# KeiHub

FatFish / SnowLuma / momoi_bot / DSH 的独立中间层。  
不改 SnowLuma、FatFish、momoi_bot 的代码；momoi_bot 只把 `forward_url` 指到 Hub。

## 当前阶段

P1：核心转发

- SnowLuma 正向 WebSocket + HTTP 动作兜底
- 对 momoi_bot 的 OneBot WebSocket 服务端
- FatFish 消息库轮询 + API/直写发送（端口自动探测）
- FatFish → bot 的私聊/群聊映射
- 端口、群号、用户映射全部在 `config.yaml`

P2/P3/P4（Windows 服务、托盘、Web 控制台、DSH 接入）见下方对应章节。

## 谁来回话（可选大脑）

Hub 收到消息后交给哪套「脑子」是可选的。**默认仍然是 momoi_bot，行为和以前完全一样**，
不配 `brains` 段就直接走老链路。

```yaml
brains:
  active: "onebot"            # 便捷写法：routing 没显式指定时用它当全局默认

routing:
  fatfish_brain: "onebot"     # FatFish 消息交给谁
  onebot_brain: "onebot"      # QQ 消息交给谁
  channels:                   # 按 FatFish 频道覆盖，优先级最高
    "dm:huizhang:key": "dsh"
```

| 名字 | 是什么 | 需要什么 |
|---|---|---|
| `onebot`（别名 `bot`） | momoi_bot，OneBot 11 服务端那条老链路 | 无 |
| `dsh` | DeepSeek Harness | 装 `dsh-hub-bridge` 插件（见下） |
| `http` | 任意 HTTP 服务，POST 进去、同步拿回复 | 一个能返回 `{"reply": "..."}` 的地址 |

**回落规则**：目标脑没启用、没连上、或处理时抛异常，都会自动回落到 `onebot`，
消息不会因为换脑而丢。

控制台顶部有下拉框可以**运行期切换**（只影响当前进程，重启后回到配置文件的值）。

`http` 脑的约定：

```jsonc
// 请求 POST <brains.http.url>
{"source": "fatfish", "session": "fatfish:general", "user": "爱丽丝", "text": "在吗"}

// 回复，以下任意一种都行
{"reply": "在呢"}   {"text": "在呢"}   {"message": "在呢"}   纯文本 "在呢"
```

**自己加一套脑子**：在 `brains/` 下新建一个文件，继承 `Brain`、用 `@register` 注册，
实现 `deliver(source, payload)` 即可，不用动 `core/`。

```python
from .base import Brain
from .registry import register

@register
class MyBrain(Brain):
    name = "mine"
    label = "我的服务"

    @property
    def connected(self):
        return True

    async def deliver(self, source, payload) -> bool:
        # 接管返回 True；返回 False 就回落 momoi_bot
        ...
        await self.bus.on_brain_reply(f"fatfish:{payload.channel}", "回复内容")
        return True
```

## DSH 接入（P3）

Hub 侧：

```yaml
adapters:
  dsh:
    enabled: true
    host: "127.0.0.1"
    port: 3020
    reply_timeout_seconds: 180   # 超时改投 momoi_bot
    fallback_to_bot: true

routing:
  fatfish_brain: "dsh"   # FatFish 交给 DSH
  onebot_brain: "bot"    # QQ 仍走 momoi_bot
```

DSH 没回（插件报错、超时、掉线）时，Hub 会把**原消息改投 momoi_bot**，
不会静默丢掉；两边都不可用才会打一条 ERROR 日志。协议里带 `id` 请求号，
插件原样回传即可精确配对。

DSH 侧安装极薄插件：

```bat
npx @deepseek-ai/dsh plugin --profile <profile> add "E:\聊天后端\keihub\dsh-plugin\dsh-hub-bridge"
```

插件只负责连 Hub、调用 `agent.followup()`、把 `assistant/message` 回传，不含路由。
配置在 `%USERPROFILE%\.dsh\hub-bridge-config.json`。


## 服务 + 托盘 + Web 控制台（P4）

安装（管理员 PowerShell）：

```powershell
powershell -ExecutionPolicy Bypass -File "E:\聊天后端\keihub\service\install_service.ps1"
```

它会：

- 安装 `pywin32` / `pystray` 等依赖
- 注册并启动 Windows 服务 `KeiHub`
- 把托盘程序加入开机启动

手动打开托盘：

```bat
E:\聊天后端\keihub\start_tray.bat
```

Web 控制台：

```
http://127.0.0.1:8765/
```

控制台可以看连接状态、日志，手动往 FatFish / SnowLuma 发消息。

**FatFish 接入**：页面顶部有输入框，把 FatFish「设置 → API 接入服务」里那条地址
直接粘进去（`?token=...` 会一并提取），点「自动配置」即可：

- 写回 `config.yaml` 的 `api_base` / `token` / `db_path` / `presence_db_path` / `server_log_path`
- **保留原有注释和排版**（改前自动备份 `config.yaml.bak-autoconfig-*`）
- Hub 在跑就**热更新，不用重启**
- 会顺带探测真实端口，端口漂了也能认出来

卸载：

```powershell
powershell -ExecutionPolicy Bypass -File "E:\聊天后端\keihub\service\uninstall_service.ps1"
```

如果服务读不到 FatFish 数据库，在 `services.msc` 里把 `KeiHub` 的登录账号改成当前用户。

## 打包成单文件 EXE

```powershell
powershell -ExecutionPolicy Bypass -File "E:\聊天后端\keihub\build_onefile.ps1"
```

输出：

```
E:\聊天后端\keihub\dist_onefile\KeiHub.exe
```

这是**一个文件**，约 30 MB，包含 Python 运行库。第一次双击会在 exe 旁边自动生成：

- `config.yaml`
- `service\`
- `dsh-plugin\`
- `README.md`

之后直接双击 `KeiHub.exe` 打开启动器。目标机器不需要安装 Python；FatFish / SnowLuma / momoi_bot / DSH 仍需各自安装。

## 打包成便携文件夹

```powershell
powershell -ExecutionPolicy Bypass -File "E:\聊天后端\keihub\build_portable.ps1"
```

输出：

```
E:\聊天后端\keihub\dist\KeiHub\KeiHub.exe
E:\聊天后端\keihub\dist\KeiHub-portable.zip
```

目标机器不需要安装 Python。双击 `KeiHub.exe` 打开启动器，可以启动/停止 Hub、打开控制台、看日志、启动托盘、创建开机自启。

便携版仍需要外部的 FatFish、SnowLuma/NapCat、momoi_bot、DSH；`config.yaml` 在 exe 旁边，可直接改端口/群号。

## 便携/单文件说明

- `dist\KeiHub\` 是 onedir 便携版，启动快，但需要整个文件夹。
- `dist_onefile\KeiHub.exe` 是单文件版，首次启动会释放配置/脚本，启动慢几秒。
- 两者都不含 FatFish、SnowLuma/NapCat、momoi_bot、DSH 本体。

## 端口

| 端口 | 用途 |
|---|---|
| 3010 | Hub 对 momoi_bot 的 OneBot WS 服务端（bot 的 `forward_url` 指这里） |
| 3001 | SnowLuma / NapCat 的正向 WS，Hub 作为客户端连它（可在 `ws_url` 改） |
| 3012 | SnowLuma HTTP 动作接口，WS 连不上时兜底 |
| 3020 | DSH 连接件（`adapters.dsh.enabled` 打开后才监听） |
| 8765 | Web 控制台 |

端口都可以在 `config.yaml` 改。

**注意**：上表是 Hub 自己的端口。FatFish 后端的端口不在这里——它每次启动
可能换（5000 被占就挑 5100/5199…），Hub 会读 `server.log` 自动探测，
`api_base` 只是首选值。

## 启动

```bat
cd /d E:\聊天后端\keihub
D:\AI\python.exe -u app.py
```

先把 momoi_bot 的 `config.yaml` 改成：

```yaml
onebot:
  mode: "forward"
  forward_url: "ws://127.0.0.1:3010"
```

SnowLuma 的 WebSocket 服务端端口对应 `adapters.snowluma.ws_url`。

## FatFish 映射

```yaml
users:
  huizhang: 3337894736   # 爱丽丝
channels:
  general:
    type: group
    group_id: 900000002
  private:1:
    type: group
    group_id: 900000001
```

- 群消息会补 `at` 段，满足 bot 的群聊 `at_required`
- `huizhang` 注入成 `3337894736`，bot 会按爱丽丝对待（`alice_user_ids` 决定显示名）
- 群号用 `900000001` 起的独立号；不要和真实 QQ 群号重复，否则 bot 对那个 QQ 群的回复会被 Hub 转到 FatFish
- `private_route` 的 45 秒窗口只在频道 `type: private` 时生效；现在 `private:1`
  配的是 `type: group`，走的是群映射，用不到这个窗口

### 自动映射（auto_channels）

FatFish 每新建一个私聊/群就会造一个新频道名（`dm:huizhang:key`、`private:3`），
写死在 `channels` 里必然漏。所以默认开着：

```yaml
auto_channels: true
```

Hub 会自动发现并映射：

| 频道名 | 映射成 |
|---|---|
| `general` / `private:N` / `group:N` | 群聊，虚拟群号从 `900000100` 起自动分配（持久化在 `hub.db`，重启不变） |
| `dm:A:B` | 私聊，user 取 A/B 里不是 bot 的那一方 |
| `dm:*:system` | 跳过（系统通知） |

`skip_senders` 补上「哪些 sender 算自己」，防止把 token 持有者发的回复又当成新消息读回来：

```yaml
skip_senders:
  - "kei_bot"
  - "key"
```

关掉就退回老行为：只轮询 `channels` 里显式列出的频道。
- bot 引用 FatFish 消息（`get_msg`）由 Hub 本地回答，不会转到 QQ 侧

## 注意

- 同一时间只运行一个 Hub，避免重复回复
- FatFish 的 token 优先读 `config.yaml`，为空时从 `E:\聊天后端\config.json` 读取
- P1/P2/P3/P4 已完成；服务/托盘/控制台可按上面的步骤安装
- 便携版已打包为 `dist\KeiHub\KeiHub.exe`，目标机器不需要 Python
