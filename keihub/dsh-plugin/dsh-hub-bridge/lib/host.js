/**
 * dsh-hub-bridge —— KeiHub ↔ DSH 的极薄连接件。
 *
 * 只做三件事：
 *   1. 连 KeiHub 的 ws://127.0.0.1:3020
 *   2. 收到 inbound 后调用对应 agent 的 followup()
 *   3. 把这一轮 assistant 的最后一句话回传 Hub
 *
 * 不含 FatFish / OneBot / 路由 / 记忆逻辑；那些都在 Hub。
 *
 * 配置：~/.dsh/hub-bridge-config.json
 *   { "hubUrl": "ws://127.0.0.1:3020", "target": "", "reconnectMs": 3000 }
 * target 为空时自动挑第一个还有 followup 的活 agent。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'

export const name = 'hub-bridge'
export const inject = ['agents', 'sessions']

const HOME = process.env.DSH_HOME || join(homedir(), '.dsh')
const CFG_PATH = join(HOME, 'hub-bridge-config.json')
const DEFAULTS = {
  hubUrl: 'ws://127.0.0.1:3020',
  target: '',
  reconnectMs: 3000,
}

function log(...args) {
  try {
    console.log('[hub-bridge]', ...args)
  } catch {
    /* 日志失败不影响转发 */
  }
}

function readCfg() {
  try {
    if (!existsSync(HOME)) mkdirSync(HOME, { recursive: true })
    if (!existsSync(CFG_PATH)) {
      writeFileSync(CFG_PATH, JSON.stringify(DEFAULTS, null, 2), 'utf8')
    }
    const raw = JSON.parse(readFileSync(CFG_PATH, 'utf8'))
    return { ...DEFAULTS, ...(raw && typeof raw === 'object' ? raw : {}) }
  } catch (error) {
    log('读取配置失败，用默认值:', String(error && error.message || error))
    return { ...DEFAULTS }
  }
}

function sessionKey(id) {
  return String(id == null ? '' : id).replace(/^session-/, '').toLowerCase()
}

function textOfMessage(message) {
  if (!message) return ''
  if (typeof message === 'string') return message
  const content = message.content ?? message.message ?? message
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .filter((part) => part && part.type === 'text')
      .map((part) => String(part.text || ''))
      .join('')
  }
  return ''
}

function makeUserMessage(text) {
  return {
    id: randomUUID(),
    role: 'user',
    content: [{ type: 'text', text: String(text) }],
    source: { kind: 'hub-bridge' },
  }
}

function findAgent(ctx, cfg) {
  const wanted = String(cfg.target || '').trim()
  const list = (() => {
    try {
      return typeof ctx.agents.list === 'function' ? ctx.agents.list() : []
    } catch {
      return []
    }
  })()

  if (wanted) {
    try {
      const direct = ctx.agents.get(wanted)
      if (direct && typeof direct.followup === 'function') return direct
    } catch {
      /* 继续按清单找 */
    }
    const hit = list.find((agent) => {
      const id = agent && (agent.id || (agent.session && agent.session.id))
      return id && sessionKey(id) === sessionKey(wanted)
    })
    if (hit) return hit
  }

  return list.find((agent) => agent && typeof agent.followup === 'function')
}

export function apply(ctx, config) {
  void config
  const cfg = readCfg()
  const pending = new Map() // dsh session key -> { hubSession, texts: [] }
  const queue = []
  let busy = false
  let ws = null
  let stopped = false
  let reconnectTimer = null

  function send(payload) {
    if (!ws || ws.readyState !== 1) return false
    try {
      ws.send(JSON.stringify(payload))
      return true
    } catch (error) {
      log('发送失败:', String(error && error.message || error))
      return false
    }
  }

  async function handleInbound(message) {
    const hubSession = String(message.session || '')
    const requestId = String(message.id || '')
    const text = String(message.text || '').trim()
    if (!hubSession || !text) return
    const agent = findAgent(ctx, cfg)
    if (!agent) {
      send({ type: 'error', id: requestId, session: hubSession, message: '没有可用的 DSH 会话' })
      return
    }
    const key = sessionKey(agent.session && agent.session.id || agent.id)
    if (!key) {
      send({ type: 'error', id: requestId, session: hubSession, message: '无法确定 DSH 会话 id' })
      return
    }
    pending.set(key, { hubSession, requestId, texts: [], started: false })
    try {
      await agent.followup(makeUserMessage(text))
      log('已送入 DSH:', hubSession, text.slice(0, 60))
    } catch (error) {
      pending.delete(key)
      const msg = String(error && error.message || error)
      log('followup 失败:', msg)
      send({ type: 'error', id: requestId, session: hubSession, message: msg })
    }
  }

  async function pump() {
    if (busy) return
    busy = true
    try {
      while (queue.length) {
        await handleInbound(queue.shift())
      }
    } finally {
      busy = false
    }
  }

  function enqueue(message) {
    queue.push(message)
    void pump()
  }

  // 抓这一轮 assistant 的最后一句话，turn/end 时回传 Hub
  try {
    ctx.on('session/event', (session, event) => {
      if (!event || typeof event.type !== 'string') return
      const key = sessionKey(session && (session.id || session.sessionId))
      if (!key) return
      const job = pending.get(key)
      if (!job) return
      if (event.type === 'turn/start') {
        job.started = true
        return
      }
      if (event.type === 'assistant/message') {
        const said = textOfMessage(event.data && event.data.message)
        if (said) job.texts.push(said)
        return
      }
      if (event.type !== 'turn/end') return
      pending.delete(key)
      if (!job.started) {
        log('没抓到这一轮的 turn/start，丢弃回复，避免发错回合')
        return
      }
      const last = job.texts.filter(Boolean).pop()
      if (!last) {
        send({ type: 'error', id: job.requestId, session: job.hubSession, message: '这一轮 DSH 没有文本回复' })
        return
      }
      send({ type: 'reply', id: job.requestId, session: job.hubSession, text: last })
      log('回传 Hub:', job.hubSession, last.slice(0, 60))
    })
  } catch (error) {
    log('挂载 session/event 失败:', String(error && error.message || error))
  }

  function scheduleReconnect() {
    if (stopped || reconnectTimer) return
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, Math.max(500, Number(cfg.reconnectMs) || 3000))
  }

  function connect() {
    if (stopped) return
    try {
      ws = new WebSocket(cfg.hubUrl)
    } catch (error) {
      log('创建 WebSocket 失败:', String(error && error.message || error))
      scheduleReconnect()
      return
    }

    ws.addEventListener('open', () => {
      log('已连接 Hub:', cfg.hubUrl)
    })

    ws.addEventListener('message', (event) => {
      let data
      try {
        data = JSON.parse(String(event.data || ''))
      } catch {
        return
      }
      if (data && data.type === 'inbound') enqueue(data)
    })

    ws.addEventListener('close', () => {
      ws = null
      log('Hub 连接断开，准备重连')
      scheduleReconnect()
    })

    ws.addEventListener('error', () => {
      // close 会跟着来，这里不重复安排
    })
  }

  connect()

  try {
    ctx.effect(() => () => {
      stopped = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      try {
        if (ws) ws.close()
      } catch {
        /* ignore */
      }
    })
  } catch {
    /* 老版本 cordis 没有 effect 就算了 */
  }
}
