import json
import logging
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request

from core.config import (
    auto_configure_fatfish,
    discover_fatfish_base,
    http_healthy,
    load_config,
)

log = logging.getLogger("keihub.console")

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KeiHub 控制台</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;
         background:#11151c; color:#d9e2ef; }
  header { padding:18px 24px; border-bottom:1px solid #263041;
           display:flex; align-items:center; gap:12px; }
  header h1 { font-size:18px; margin:0; font-weight:600; }
  .dot { width:9px; height:9px; border-radius:50%; background:#f59e0b; }
  .dot.on { background:#22c55e; }
  main { padding:20px 24px; max-width:1100px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
           gap:12px; margin-bottom:18px; }
  .card { background:#171d27; border:1px solid #263041; border-radius:10px;
          padding:14px 16px; }
  .card .k { color:#8fa3bd; font-size:12px; }
  .card .v { font-size:20px; margin-top:4px; }
  .ok { color:#22c55e; } .bad { color:#f87171; }
  section { background:#171d27; border:1px solid #263041; border-radius:10px;
            padding:14px 16px; margin-bottom:16px; }
  h2 { font-size:14px; margin:0 0 10px; color:#a9bad2; font-weight:600; }
  input, select, textarea { background:#0e131a; color:#d9e2ef; border:1px solid #2d3a4d;
            border-radius:6px; padding:7px 9px; font:inherit; }
  textarea { width:100%; box-sizing:border-box; min-height:64px; }
  button { background:#2563eb; color:white; border:0; border-radius:6px;
           padding:7px 14px; font:inherit; cursor:pointer; }
  button.ghost { background:#243044; }
  pre { background:#0b0f14; border:1px solid #222d3d; border-radius:8px;
        padding:12px; overflow:auto; max-height:320px; white-space:pre-wrap;
        font:12px/1.6 Consolas,monospace; color:#b9c8dc; }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .muted { color:#7f93ad; font-size:12px; }
</style>
</head>
<body>
<header>
  <span id="dot" class="dot"></span>
  <h1>KeiHub 控制台</h1>
  <span id="uptime" class="muted"></span>
</header>
<main>
  <div class="cards">
    <div class="card"><div class="k">momoi_bot</div><div id="bot" class="v bad">未连接</div></div>
    <div class="card"><div class="k">SnowLuma</div><div id="snow" class="v bad">未连接</div></div>
    <div class="card"><div class="k">DSH 插件</div><div id="dsh" class="v bad">未连接</div></div>
    <div class="card"><div class="k">FatFish 频道</div><div id="channels" class="v">-</div></div>
    <div class="card"><div class="k">主脑</div><div id="brains" class="v">-</div></div>
  </div>

  <section>
    <h2>大脑（谁来回话）</h2>
    <div class="row">
      <span class="muted">FatFish</span>
      <select id="brainFat"></select>
      <span class="muted">QQ</span>
      <select id="brainQq"></select>
      <button onclick="applyBrains()">切换</button>
      <span id="brainResult" class="muted"></span>
    </div>
    <div id="brainList" class="muted" style="margin-top:8px">-</div>
  </section>

  <section>
    <h2>FatFish 接入</h2>
    <div class="row">
      <input id="ffUrl" placeholder="粘贴 FatFish「设置 → API 接入服务」里的地址" style="flex:1;min-width:330px">
      <input id="ffToken" type="password" placeholder="令牌（可留空，地址带 ?token= 也行）" style="width:210px">
      <button onclick="ffAuto()">自动配置</button>
    </div>
    <div id="ffResult" class="muted" style="margin-top:8px">粘贴地址后点「自动配置」，会写回 config.yaml 并立即生效</div>
  </section>

  <section>
    <h2>手动发送</h2>
    <div class="row">
      <select id="platform">
        <option value="fatfish">FatFish</option>
        <option value="onebot">SnowLuma / QQ</option>
      </select>
      <input id="target" placeholder="FatFish 频道 或 QQ 私聊号" value="general" style="width:220px">
      <select id="kind">
        <option value="private">私聊</option>
        <option value="group">群聊</option>
      </select>
      <button onclick="sendMsg()">发送</button>
    </div>
    <div style="margin-top:8px"><textarea id="text" placeholder="要发送的内容"></textarea></div>
    <div id="sendResult" class="muted" style="margin-top:6px"></div>
  </section>

  <section>
    <h2>日志 <button class="ghost" onclick="loadLogs()">刷新</button></h2>
    <pre id="logs">加载中…</pre>
  </section>
</main>
<script>
async function refresh() {
  try {
    const r = await fetch('/api/status');
    const s = await r.json();
    document.getElementById('dot').className = 'dot ' + (s.bot_connected || s.snow_connected || s.dsh_connected ? 'on' : '');
    document.getElementById('bot').textContent = s.bot_connected ? '已连接' : '未连接';
    document.getElementById('bot').className = 'v ' + (s.bot_connected ? 'ok' : 'bad');
    document.getElementById('snow').textContent = s.snow_connected ? '已连接' : '未连接';
    document.getElementById('snow').className = 'v ' + (s.snow_connected ? 'ok' : 'bad');
    document.getElementById('dsh').textContent = s.dsh_connected ? '已连接' : '未连接';
    document.getElementById('dsh').className = 'v ' + (s.dsh_connected ? 'ok' : 'bad');
    document.getElementById('channels').textContent = (s.fatfish_channels || []).join(', ') || '-';
    document.getElementById('brains').textContent = JSON.stringify(s.brains || {});
    document.getElementById('uptime').textContent = '运行 ' + Math.floor((s.uptime_seconds||0)/60) + ' 分钟';
    const list = s.brain_list || [];
    fillBrainSelect('brainFat', (s.brains || {}).fatfish, list);
    fillBrainSelect('brainQq', (s.brains || {}).onebot, list);
    document.getElementById('brainList').textContent = list.length
      ? list.map(b => b.name + (b.enabled ? '' : '(未启用)') + (b.connected ? '●' : '○')).join('  ') +
        ((s.channel_brains && Object.keys(s.channel_brains).length)
          ? '   按频道：' + JSON.stringify(s.channel_brains) : '')
      : '-';
    const ff = document.getElementById('ffUrl');
    if (ff && !ff.dataset.touched && s.fatfish_api_base) {
      ff.value = s.fatfish_api_base + '/api/v1/messages';
    }
  } catch (e) { console.error(e); }
}
function fillBrainSelect(id, current, list) {
  const el = document.getElementById(id);
  if (!el || !list || !list.length) return;
  const want = String(current || 'onebot');
  const html = list.map(b => {
    const mark = b.name === want ? ' selected' : '';
    const off = b.enabled ? '' : '（未启用）';
    const live = b.connected ? '' : '（离线）';
    return '<option value="' + b.name + '"' + mark + '>' + b.label + off + live + '</option>';
  }).join('');
  if (el.dataset.sig !== html) { el.innerHTML = html; el.dataset.sig = html; }
}
async function applyBrains() {
  const out = document.getElementById('brainResult');
  out.textContent = '切换中…';
  const pairs = [['fatfish', 'brainFat'], ['onebot', 'brainQq']];
  for (const [source, id] of pairs) {
    const brain = document.getElementById(id).value;
    try {
      const r = await fetch('/api/brains/select', {
        method: 'POST', headers: {'content-type': 'application/json'},
        body: JSON.stringify({source: source, brain: brain})
      });
      const d = await r.json();
      if (!d.ok) { out.textContent = '失败：' + (d.error || ''); return; }
    } catch (e) { out.textContent = '失败：' + e; return; }
  }
  out.textContent = '已切换（重启后回到配置文件的值）';
  refresh();
}
async function loadLogs() {
  try {
    const r = await fetch('/api/logs?lines=120');
    const d = await r.json();
    document.getElementById('logs').textContent = (d.lines || []).join('\n') || '(空)';
  } catch (e) { document.getElementById('logs').textContent = String(e); }
}
async function sendMsg() {
  const platform = document.getElementById('platform').value;
  const target = document.getElementById('target').value.trim();
  const kind = document.getElementById('kind').value;
  const text = document.getElementById('text').value;
  const out = document.getElementById('sendResult');
  out.textContent = '发送中…';
  try {
    const payload = { platform, text };
    if (platform === 'fatfish') payload.channel = target;
    else { payload.kind = kind; if (kind === 'group') payload.group_id = Number(target); else payload.user_id = Number(target); }
    const r = await fetch('/api/send', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(payload)});
    const d = await r.json();
    out.textContent = d.ok ? ('已发送: ' + JSON.stringify(d.result)) : ('失败: ' + (d.error || ''));
  } catch (e) { out.textContent = '失败: ' + e; }
}
async function ffAuto() {
  const url = document.getElementById('ffUrl').value.trim();
  const token = document.getElementById('ffToken').value.trim();
  const out = document.getElementById('ffResult');
  if (!url) { out.textContent = '请先填写地址'; return; }
  out.textContent = '配置中…';
  try {
    const r = await fetch('/api/fatfish/config', {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({url, token})
    });
    const d = await r.json();
    if (!d.ok) { out.textContent = '失败：' + (d.error || ''); return; }
    const bits = [
      'API：' + d.api_base,
      (d.effective_api_base && d.effective_api_base !== d.api_base
        ? '端口已漂移，实际：' + d.effective_api_base : ''),
      '令牌：' + (d.token_set ? '已写入' : '未改动'),
      '数据库：' + (d.db_found ? '已自动找到' : '未找到'),
      '后端：' + (d.health ? '在线' : '未响应'),
      d.applied ? '已热更新，立即生效' : (d.note || '已写入，重启后生效'),
      d.comments_preserved ? '注释已保留' : '本次整份重写，注释已丢（备份：' + d.backup + '）'
    ].filter(Boolean);
    out.textContent = bits.join(' · ');
    document.getElementById('ffToken').value = '';
    refresh();
  } catch (e) { out.textContent = '失败：' + e; }
}
document.getElementById('ffUrl').addEventListener('input', (e) => { e.target.dataset.touched = '1'; });
refresh(); loadLogs();
setInterval(refresh, 3000);
setInterval(loadLogs, 8000);
</script>
</body>
</html>
"""


def _tail(path: Path, lines: int):
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - 256 * 1024))
            data = f.read().decode("utf-8", "replace")
        return data.splitlines()[-lines:]
    except Exception:
        return []


def create_app(runtime):
    app = Flask("keihub_console")
    app.config["JSON_AS_ASCII"] = False

    async def _apply_fatfish(cfg: dict):
        fat = (cfg.get("adapters") or {}).get("fatfish") or {}
        if runtime.fatfish is not None:
            runtime.fatfish.apply_config(fat)
        if runtime.bus is not None:
            runtime.bus.reload_fatfish(fat)

    @app.get("/")
    def index():
        return Response(PAGE, mimetype="text/html")

    @app.post("/api/brains/select")
    def brains_select():
        """运行期切换某个来源用哪个大脑（只影响当前进程，不写配置）。"""
        data = request.get_json(force=True, silent=True) or {}
        source = str(data.get("source") or "fatfish").strip().lower()
        name = str(data.get("brain") or "").strip()
        if runtime.bus is None:
            return jsonify({"ok": False, "error": "Hub 未就绪"}), 503
        try:
            applied = runtime.bus.set_brain(source, name)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        log.info("控制台切换大脑：%s -> %s", source, applied)
        return jsonify({"ok": True, "source": source, "brain": applied})

    @app.post("/api/fatfish/config")
    def fatfish_config():
        """粘贴 FatFish API 接入地址 → 写回 config.yaml → 热更新适配器。"""
        data = request.get_json(force=True, silent=True) or {}
        url = str(data.get("url") or "").strip()
        token = str(data.get("token") or "").strip()
        if not url:
            return jsonify({"ok": False, "error": "请填写 FatFish API 接入地址"}), 400
        try:
            info = auto_configure_fatfish(url, token)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

        applied = False
        note = ""
        cfg = load_config()
        fat = (cfg.get("adapters") or {}).get("fatfish") or {}
        # 端口可能早就漂了：按 server.log 探一次真实地址再判断健康
        effective = discover_fatfish_base(
            info["api_base"], fat.get("server_log_path") or "", force=True
        )
        if runtime.fatfish is None:
            note = "FatFish 适配器未运行，配置已写入，重启 Hub 后生效"
        else:
            try:
                runtime.run_coro(_apply_fatfish(cfg))
                applied = True
            except Exception as e:
                log.exception("FatFish 配置热更新失败")
                note = f"配置已写入，但热更新失败：{e}"

        result = dict(info)
        result.update({
            "ok": True,
            "applied": applied,
            "note": note,
            "effective_api_base": effective,
            "health": http_healthy(effective),
        })
        log.info("控制台更新 FatFish 接入：%s（实际 %s，热更新=%s）",
                 info["api_base"], effective, applied)
        return jsonify(result)

    @app.get("/api/status")
    def status():
        return jsonify(runtime.status())

    @app.get("/api/logs")
    def logs():
        try:
            n = max(10, min(500, int(request.args.get("lines") or 120)))
        except Exception:
            n = 120
        return jsonify({"lines": _tail(Path(runtime.log_path), n)})

    @app.post("/api/send")
    def send():
        data = request.get_json(force=True, silent=True) or {}
        platform = str(data.get("platform") or "").lower()
        text = str(data.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "text 为空"}), 400
        try:
            if platform == "fatfish":
                channel = str(data.get("channel") or "").strip()
                if not channel:
                    return jsonify({"ok": False, "error": "channel 为空"}), 400
                if runtime.fatfish is None:
                    return jsonify({"ok": False, "error": "FatFish 适配器未启用"}), 503
                result = runtime.run_coro(runtime.fatfish.send_to_channel(channel, text))
                return jsonify({"ok": True, "result": result})
            if platform == "onebot":
                if runtime.snow is None:
                    return jsonify({"ok": False, "error": "SnowLuma 未连接"}), 503
                kind = str(data.get("kind") or "private").lower()
                if kind == "group":
                    action = "send_group_msg"
                    params = {"group_id": int(data.get("group_id") or 0), "message": text}
                else:
                    action = "send_private_msg"
                    params = {"user_id": int(data.get("user_id") or 0), "message": text}
                result = runtime.run_coro(runtime.snow.send_action(action, params))
                return jsonify({"ok": True, "result": result})
            return jsonify({"ok": False, "error": "platform 只支持 fatfish/onebot"}), 400
        except Exception as e:
            log.exception("Web 控制台发送失败")
            return jsonify({"ok": False, "error": str(e)}), 500

    return app


def start_console(runtime, host: str = "127.0.0.1", port: int = 8765):
    app = create_app(runtime)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=int(port), threaded=True,
                               debug=False, use_reloader=False),
        name="keihub-console",
        daemon=True,
    )
    thread.start()
    log.info("Web 控制台已启动: http://%s:%s/", host, port)
    return thread
