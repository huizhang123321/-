# dsh-hub-bridge

KeiHub ↔ DeepSeek Harness 的**极薄连接件**。

它只负责：

1. 连 KeiHub：`ws://127.0.0.1:3020`
2. 收到 `inbound` 后调用 DSH 的 `agent.followup()`
3. 把这一轮 assistant 的最后一句话用 `reply` 回传 Hub

不含 FatFish / OneBot / 路由 / 记忆逻辑，那些都在 Hub。

## 安装

```bash
npx @deepseek-ai/dsh plugin --profile <你的 profile> add "E:\聊天后端\keihub\dsh-plugin\dsh-hub-bridge"
```

或者把插件目录放到 DSH profile 的 `node_modules` 下，并在 DSH 的 `cordis.patch.yml` 里加：

```yaml
- insert:
    - id: dsh-hub-bridge
      name: dsh-hub-bridge
```

装完重启 DSH。

## 配置

首次启动会自动生成：

```
%USERPROFILE%\.dsh\hub-bridge-config.json
```

```json
{
  "hubUrl": "ws://127.0.0.1:3020",
  "target": "",
  "reconnectMs": 3000
}
```

| 字段 | 说明 |
|---|---|
| `hubUrl` | KeiHub 的 DSH 桥地址 |
| `target` | 指定 DSH 会话 id；留空自动挑第一个活着的 agent |
| `reconnectMs` | Hub 断开后的重连间隔 |

## KeiHub 侧

`E:\聊天后端\keihub\config.yaml`：

```yaml
adapters:
  dsh:
    enabled: true
    host: "127.0.0.1"
    port: 3020

routing:
  fatfish_brain: "dsh"   # FatFish 消息交给 DSH；想用 momoi_bot 就改回 bot
  onebot_brain: "bot"    # QQ 仍走 momoi_bot
```

Hub 会把 FatFish 消息包装成（`id` 是本次请求号，插件必须原样带回）：

```json
{"type":"inbound","id":"3f9c1a2b7e40","session":"fatfish:private:1","user":"爱丽丝","text":"在吗"}
```

DSH 回复：

```json
{"type":"reply","id":"3f9c1a2b7e40","session":"fatfish:private:1","text":"..."}
```

出错时回 error 帧，Hub 会把原消息改投 momoi_bot，而不是丢掉：

```json
{"type":"error","id":"3f9c1a2b7e40","session":"fatfish:private:1","message":"没有可用的 DSH 会话"}
```

`id` 缺省也兼容（Hub 退化成按 `session` 配对，只适合单条在途的情况）。

Hub 再根据 session 前缀发回 FatFish 或 SnowLuma。

## 注意

- DSH 是 developer preview，`agent.followup` / `session/event` 的事件名如果变了，插件需要跟着改。
- 插件不生成台词，只把 Hub 的消息送进 DSH，让 DSH 自己开一轮。
- 如果 `target` 指向的会话没打开，会自动退回第一个活着的 agent；没有活 agent 就回传错误，不会静默。
