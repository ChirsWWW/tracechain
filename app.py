# -*- coding: utf-8 -*-
"""
app.py —— 溯链 TraceChain 主服务（多站点版）

五个端口 = 五个彼此独立的站点，各自一个网址，站点之间没有任何导航关联：

    生产端   https://tracechain-produce.onrender.com     （本机 ROLE=produce）
    流通端   https://tracechain-logistics.onrender.com   （本机 ROLE=logistics）
    销售端   https://tracechain-retail.onrender.com      （本机 ROLE=retail）
    消费端   https://tracechain-consume.onrender.com     （本机 ROLE=consume）
    管理端   https://tracechain-core.onrender.com        （本机 ROLE=core，兼存证核心节点）

部署时用环境变量区分角色：
    ROLE=produce   —— 本站只对外呈现生产端，其他端口路径一律 404
    CORE_URL=https://tracechain-core.onrender.com —— 数据与存证都落到核心节点
ROLE=core 的实例持有数据库、追溯码、存证链、公众验真页（/t/<码>）与区块浏览器。

二维码独立于端口：赋码只产生一个空码，哪个站点上报了，码上才有那一份内容。
"""
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent import futures
from functools import lru_cache

import qrcode
from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, send_file)
from qrcode.constants import ERROR_CORRECT_H

import chain
import schema
import store

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.json.ensure_ascii = False
# 本地开发时模板改动即时生效；线上不开，避免每次请求都读盘
if os.environ.get("DEV_RELOAD") == "1":
    app.config["TEMPLATES_AUTO_RELOAD"] = True

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin888")
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "").rstrip("/")
ROLE = (os.environ.get("ROLE") or "core").strip().lower()
CORE_URL = os.environ.get("CORE_URL", "").rstrip("/")
IS_CORE = ROLE == "core" or not CORE_URL
WORKER_ROLES = ("produce", "logistics", "retail", "consume")

SITE = dict(schema.SITES.get(ROLE) or schema.SITES["core"])

# 顶栏与页脚展示的运行信息（真实系统都会标明节点与版本，便于对账）
APP_VERSION = "2.4.0"
NODE_IDS = {"core": "CORE-01", "produce": "PRD-01", "logistics": "LGT-01",
            "retail": "RTL-01", "consume": "CSM-01", "public": "CORE-01"}

# 各站点呈现的页面模板
ROLE_TPL = {
    "produce": "produce.html",
    "logistics": "logistics.html",
    "retail": "retail.html",
    "consume": "consume.html",
}

store.init_db()


# ------------------------------------------------------------------ 基础工具
def public_base() -> str:
    """核心节点的对外地址（二维码里写的就是它）"""
    return PUBLIC_BASE or request.host_url.rstrip("/")


def trace_base() -> str:
    """公众验真页所在地址：作业端指向核心节点，核心节点指自己"""
    return CORE_URL or public_base()


def client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or ""


def code_url(code: str) -> str:
    return f"{public_base()}/t/{code}"


def admin_guard(req) -> bool:
    token = req.headers.get("X-Admin-Token") or (req.get_json(silent=True) or {}).get("admin_token", "")
    return token == ADMIN_PASSWORD


def ctx(**kw):
    base = {
        "site": SITE, "role": ROLE, "is_core": IS_CORE,
        "stages": schema.STAGES, "order": schema.ORDER, "labels": schema.LABELS,
        "labels_by_stage": schema.LABELS_BY_STAGE,
        "algo": chain.HASH_ALGO,
        "base": public_base(), "trace_base": trace_base(),
        "admin_url": trace_base() + "/admin",
        "sites": schema.SITES,
        "stage_urls": {k: (f"https://{schema.SITES[k]['host']}/" if PUBLIC_BASE else f"/{k}")
                       for k in schema.ORDER},
        "stats": store.stats() if IS_CORE else None,
        "block_size": store.BLOCK_SIZE,
        # 运行信息：顶栏时钟按服务端时间走，与时区无关
        "srv": {
            "node": NODE_IDS.get(SITE.get("role", ""), "CORE-01"),
            "ts": int(time.time()),
            "tz": int(getattr(chain, "UTC_OFFSET", 8)) * 3600,
            "version": APP_VERSION,
        },
    }
    base.update(kw)
    return base


def _port(stage: str):
    return render_template(ROLE_TPL[stage], **ctx(
        stage=stage, meta=schema.STAGES[stage],
        field_groups=schema.groups_of(stage),
        server_now=chain.now_minute(),
        prereq=[schema.STAGES[p] for p in schema.PREREQ.get(stage, [])],
        upstream=[schema.STAGES[p] for p in schema.ORDER[:schema.ORDER.index(stage)]],
    ))


# ------------------------------------------------------------------ 站点隔离
# 作业站代理到核心节点的耗时必须被「钉死」在 Render 网关的等待窗口内
# （网关约 100 秒就回一张 HTML 502 错误页），同时又要足够长以覆盖核心节点
# 20~50 秒的冷启动：
#   × 旧参数 3 次 × 80 秒 + 退避 ≈ 245 秒 —— 必然被网关掐断；
#   × 2 次 × 30 秒 ≈ 62 秒 —— 单次太短，冷启动时两次都在「刚接通前」超时；
#   ✓ 单次 55 秒 socket 超时 + 70 秒线程硬截止 —— 留足网关余量。
# 注意**光靠 socket timeout 不够**：urllib 的 timeout 不覆盖 getaddrinfo，
# Render 的 DNS 偶发卡住时请求会无限期挂起（实测 >100 秒无响应）。
# 所以把出网调用丢进线程池，用 join(result(timeout)) 做硬截止，到点就回 503 JSON。
PROXY_TIMEOUT = int(os.environ.get("TC_PROXY_TIMEOUT", "55"))      # 单次 socket 等待（秒）
PROXY_DEADLINE = int(os.environ.get("TC_PROXY_DEADLINE", "70"))    # 整体硬截止（秒，含 DNS）

# 内部服务调用要绕过环境里的 HTTP_PROXY：
# urllib 默认会读 HTTP_PROXY/HTTPS_PROXY，本机若有代理，请求核心节点会被送去
# 代理转发（拿回一张代理自己的 502）。线上没有该变量，行为不变。
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_POOL = futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="proxy")


def _fetch_core(url, method, data, ctype, token, ip):
    """真正出网的一段，放在线程里跑，便于外部加硬截止。

    注意：这里必须用传进来的 method，不能读 Flask 的 request——
    本函数运行在工作线程里，Flask 的请求上下文是线程局部的，取不到。
    """
    r = urllib.request.Request(url, data=data, method=method)
    if ctype:
        r.add_header("Content-Type", ctype)
    if token:
        r.add_header("X-Admin-Token", token)
    if ip:
        r.add_header("X-Forwarded-For", ip)
    try:
        with _NO_PROXY.open(r, timeout=PROXY_TIMEOUT) as resp:
            return (resp.status,
                    resp.headers.get("Content-Type", "application/json"),
                    resp.headers.get("Content-Disposition"),
                    resp.read())
    except urllib.error.HTTPError as e:
        # 核心节点已给出明确应答（4xx/5xx），原样透传
        hdrs = e.headers
        return (e.code,
                (hdrs.get("Content-Type", "application/json") if hdrs else "application/json"),
                (hdrs.get("Content-Disposition") if hdrs else None),
                e.read())


def _proxy_to_core():
    """作业端不持有数据，所有 /api/* 请求转发到存证核心节点。"""
    url = CORE_URL + request.full_path
    if url.endswith("?"):
        url = url[:-1]
    data = request.get_data() or None
    method = request.method

    # 核心节点还没醒：立刻回一句明确的话，同时后台继续敲它。
    # 前端收到这个应答会自动轮询重试，用户看到的是「正在唤醒」而不是长时间转圈。
    if not _core_alive():
        poke_core()
        return jsonify({"ok": False, "net": True, "waking": True, "upstream": CORE_URL,
                        "reason": "数据核心节点正在唤醒，请稍候重试。"}), 503

    fut = _POOL.submit(_fetch_core, url, method, data,
                       request.headers.get("Content-Type"),
                       request.headers.get("X-Admin-Token"),
                       client_ip())
    try:
        status, ctype, cd, body = fut.result(timeout=PROXY_DEADLINE)
    except futures.TimeoutError:
        # 线程还在跑（Python 杀不掉线程），但请求必须按时结束，否则网关回 HTML 502
        app.logger.warning("proxy to core timed out after %ss: %s", PROXY_DEADLINE, url)
        _probe["alive"] = False
        poke_core()
        return jsonify({"ok": False, "net": True, "waking": True, "upstream": CORE_URL,
                        "reason": "数据核心节点正在唤醒，请稍候重试。"}), 503
    except Exception as e:
        app.logger.warning("proxy to core failed: %s", e)
        _probe["alive"] = False
        poke_core()
        return jsonify({"ok": False, "net": True, "upstream": CORE_URL,
                        "reason": "数据核心节点暂时不可达，请稍后重试。"}), 503

    _probe.update(at=time.time(), alive=True)     # 这一趟通了，短时间内不必再探
    extra = {"Content-Disposition": cd} if cd else {}
    return Response(body, status, content_type=ctype, headers=extra)


PROBE_TIMEOUT = int(os.environ.get("TC_PROBE_TIMEOUT", "6"))       # 探活单次等待（秒）
_probe = {"at": 0.0, "alive": False, "ttl": float(os.environ.get("TC_PROBE_TTL", "20"))}


def _core_alive() -> bool:
    """核心节点是不是已经醒着。

    免费实例休眠后要几十秒才能起来。与其让用户的请求在 70 秒的代理预算里
    干等到超时，不如先用几分钟一次的探活快速判断——醒着才真正转发。
    探测结果缓存若干秒，热态下这里几乎没有额外开销。
    """
    now = time.time()
    if now - _probe["at"] < _probe["ttl"]:
        return _probe["alive"]
    alive = False
    try:
        with _NO_PROXY.open(CORE_URL + "/healthz", timeout=PROBE_TIMEOUT):
            alive = True
    except Exception:
        alive = False
    _probe["at"], _probe["alive"] = now, alive
    return alive


_poke = {"at": 0.0}


def _poke_loop(seconds: float):
    """持续轻敲核心节点，直到它醒来或超时。

    单纯的「发起一次请求」不足以把实例拉起来——请求在网关排队、连接被拒都是
    常态。持续敲既能推动实例启动，也能在它启动完成的瞬间被发现。
    """
    end = time.time() + seconds
    while time.time() < end:
        try:
            with _NO_PROXY.open(CORE_URL + "/healthz", timeout=8):
                _probe.update(at=time.time(), alive=True)
                return
        except Exception:
            time.sleep(2.5)


def poke_core(seconds: float = 90.0):
    """带节流地唤醒核心节点：20 秒内只触发一次，不阻塞调用方。

    默认关闭（TC_POKE=1 可开启）：后台持续敲核心会占用本就只有 0.1 核的
    CPU，是 Render 健康检查 5 秒超时的诱因之一。
    """
    if IS_CORE or not CORE_URL:
        return
    if os.environ.get("TC_POKE", "0") == "0":
        return
    now = time.time()
    if now - _poke["at"] < 20:
        return
    _poke["at"] = now
    threading.Thread(target=_poke_loop, args=(seconds,), daemon=True, name="core-poke").start()


# 五个端口的网址：任一端口被访问时，把其余端口也顺手叫醒，
# 避免在一个会话里逐个切换页面时反复撞上冷启动（免费实例 15 分钟无流量即休眠）。
PORT_URLS = {
    "produce":   "https://tracechain-produce.onrender.com",
    "logistics": "https://tracechain-logistics.onrender.com",
    "retail":    "https://tracechain-retail.onrender.com",
    "consume":   "https://tracechain-consume.onrender.com",
    "core":      "https://tracechain-core.onrender.com",
}
_WARM_AT = {"t": 0.0}


def _warm_peers():
    """轻敲其余端口的 /healthz，让它们从休眠中醒来（每次只敲一下，约 15 分钟内保持温热）。"""
    for role, url in PORT_URLS.items():
        if role == ROLE:
            continue
        try:
            with _NO_PROXY.open(url + "/healthz", timeout=12):
                pass
        except Exception:
            pass


def warm_peers():
    """带节流地唤醒同级端口：120 秒内只触发一次，不阻塞请求。"""
    now = time.time()
    if now - _WARM_AT["t"] < 120:
        return
    _WARM_AT["t"] = now
    threading.Thread(target=_warm_peers, daemon=True, name="warm-peers").start()


@app.before_request
def _site_isolation():
    """每个站点只对外呈现自己那一个端口，其余路径一律 404。

    作业站不持有数据，除本机系统时间外的 /api/* 一律转发到存证核心节点。
    """
    path = request.path
    # PWA 端点对所有角色开放（Android 打包与离线缓存需要）
    if path.startswith("/static/") or path in (
            "/favicon.ico", "/robots.txt", "/healthz",
            "/manifest.json", "/sw.js", "/.well-known/assetlinks.json"):
        return None
    if IS_CORE:
        if path == "/" or path.startswith("/t/"):
            warm_peers()                 # 核心被访问时把四个作业站也叫醒，切换不卡
        return None
    if path == "/api/now":
        # 系统时间作业站自己就能算（同一套 +8 偏移），没必要为此叫醒核心节点
        return jsonify({"ok": True, "now": chain.now_minute(),
                        "iso": chain.now_iso(), "utc_offset": chain.UTC_OFFSET})
    if path.startswith("/api/"):
        return _proxy_to_core()
    if path in ("/", "/" + ROLE):
        warm_peers()                    # 用户填表这几十秒正好把同级端口都叫醒，避免逐个切换卡顿
        return None
    return render_template("notfound.html", **ctx()), 404


# ------------------------------------------------------------------ 入口
@app.get("/")
def root():
    if IS_CORE:
        return render_template("admin.html", **ctx())
    return _port(ROLE)


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "role": ROLE, "core": CORE_URL or "self",
                    "site": SITE["name"]})


# ------------------------------------------------------------------ PWA（供 Android / TWA 打包）
# 各端图标文件名与 ROLE 的对应关系（core 端用 admin 图标）
PWA_ICON = {"produce": "produce", "logistics": "logistics", "retail": "retail",
            "consume": "consume", "core": "admin", "public": "verify"}
# 各站点在 Google Play 的包名
PWA_PKG = {"produce": "com.tracechain.produce",
           "logistics": "com.tracechain.logistics",
           "retail": "com.tracechain.retail",
           "consume": "com.tracechain.consume",
           "core": "com.tracechain.core"}
# 由 PWABuilder CloudAPK 生成签名密钥后回填（用于 TWA 校验，否则应用内会显示地址栏）
ASSETLINKS_SHA256 = {
    "produce":   ["1C:C0:F6:9C:28:BB:05:8B:F4:88:8B:E4:6A:3C:98:9E:C7:6A:86:D1:"
                  "F7:36:67:00:2D:97:2A:4A:0C:98:99:AF"],
    "logistics": ["5C:26:39:96:9C:64:8F:FD:21:8B:3C:4F:75:5F:2F:1D:48:42:A8:63:"
                  "87:43:C8:58:34:91:0A:BD:31:1C:8C:86"],
    "retail":    ["84:67:36:68:18:30:26:48:96:60:36:73:6A:4B:50:D8:11:24:3C:C9:"
                  "10:35:CA:27:DC:6A:A7:3B:8A:65:00:6E"],
    "consume":   ["99:03:C8:2B:59:75:54:A8:0C:A1:D4:14:2F:96:CC:F7:F5:BE:DD:AB:"
                  "55:95:86:02:55:F5:2C:13:4A:7C:03:21"],
    "core":      ["60:86:74:EA:19:B7:28:34:2C:F9:EA:18:BF:67:C0:C4:F5:42:D3:14:"
                  "EF:45:BD:BF:74:2F:D4:92:78:92:05:AC"],
}


@app.get("/manifest.json")
def pwa_manifest():
    slug = PWA_ICON.get(ROLE, "admin")
    return jsonify({
        "name": SITE["title"],
        "short_name": SITE["name"],
        "description": SITE.get("slogan", ""),
        "id": "/",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#ffffff",
        "theme_color": SITE["accent"],
        "lang": "zh-CN",
        "dir": "ltr",
        "categories": ["business", "productivity"],
        "icons": [
            {"src": "/static/icons/icon-%s@192.png" % slug, "sizes": "192x192",
             "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-%s@512.png" % slug, "sizes": "512x512",
             "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-%s@512.png" % slug, "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
    })


@app.get("/sw.js")
def pwa_sw():
    # 网络优先 + 缓存兜底；/api/ 为动态数据，一律不走缓存
    body = """self.addEventListener('install', function (e) { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.indexOf('/api/') === 0) return;
  e.respondWith(
    fetch(req).then(function (res) {
      var copy = res.clone();
      caches.open('tc-v1').then(function (c) { c.put(req, copy); });
      return res;
    }).catch(function () {
      return caches.match(req).then(function (r) { return r || Response.error(); });
    })
  );
});
"""
    return Response(body, mimetype="application/javascript")


@app.get("/.well-known/assetlinks.json")
def pwa_assetlinks():
    pkg = PWA_PKG.get(ROLE, PWA_PKG["core"])
    return jsonify([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {"namespace": "android_app", "package_name": pkg,
                   "sha256_cert_fingerprints": ASSETLINKS_SHA256.get(ROLE) or []},
    }])


# ------------------------------------------------------------------ 各端页面（核心节点上保留，便于运维排查；作业端由 / 或 /<role> 进入）
@app.get("/produce")
def page_produce():
    return _port("produce")


@app.get("/logistics")
def page_logistics():
    return _port("logistics")


@app.get("/retail")
def page_retail():
    return _port("retail")


@app.get("/consume")
def page_consume():
    return _port("consume")


# 兼容旧地址：旧标签上的 /p/xxx 与新链接都不能失效
@app.get("/p/<stage>")
def legacy_port(stage):
    if stage not in schema.STAGES:
        abort(404)
    if PUBLIC_BASE:
        return redirect(f"https://{schema.SITES[stage]['host']}/", code=301)
    return redirect(f"/{stage}", code=301)


@app.get("/c/<code>")
def legacy_consumer(code):
    return redirect(f"/t/{code}", code=301)


# ------------------------------------------------------------------ 公众验真页（独立于五个端口）
@app.get("/t/<code>")
def verify_page(code):
    code = chain.extract_code(code)
    rec = store.get_code(code)
    valid = chain.is_valid_code(code)
    trace, progress, verify, stats = None, None, None, None
    if rec:
        store.log_scan(code, "verify", client_ip(), request.headers.get("User-Agent", "")[:200])
        trace = store.get_events(code)
        progress = store.code_progress(code)
        verify = store.verify_code_chain(code)
        stats = store.scan_stats(code)
    return render_template("verify.html", code=code, rec=rec, valid=valid, trace=trace or [],
                           progress=progress or [], verify=verify, scan=stats,
                           hide_nav=True, **ctx(site=schema.SITES["public"]))


# ------------------------------------------------------------------ 管理端
@app.get("/admin")
def page_admin():
    return render_template("admin.html", **ctx())


@app.get("/chain")
def page_chain():
    return render_template("chain.html", blocks=store.list_blocks(100),
                           verify=store.verify_blocks(), **ctx())


@app.get("/print")
def page_print():
    codes = [chain.extract_code(c) for c in request.args.get("codes", "").split(",") if c.strip()]
    return render_template("print.html", codes=codes, **ctx())


@lru_cache(maxsize=2048)
def _qr_bytes(url: str) -> bytes:
    img = qrcode.make(url, error_correction=ERROR_CORRECT_H, box_size=10, border=3)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@app.get("/qr/<path:code>.png")
def qr_image(code):
    code = chain.extract_code(code)
    return send_file(io.BytesIO(_qr_bytes(code_url(code))), mimetype="image/png",
                     download_name=f"{code}.png", max_age=3600)


@app.get("/qrurl")
def qr_url():
    """给任意网址生成二维码（管理台用于展示五个站点的入口）"""
    u = request.args.get("u", "")
    if not u.startswith("http"):
        abort(400)
    return send_file(io.BytesIO(_qr_bytes(u)), mimetype="image/png", max_age=3600)


# ------------------------------------------------------------------ API
@app.post("/api/scan")
def api_scan():
    """扫码：留痕 + 返回该码状态、上游已登记记录、本端可否上报与填充进度"""
    body = request.get_json(silent=True) or {}
    code = chain.extract_code(body.get("code", ""))
    stage = body.get("stage", "")
    valid_code = chain.is_valid_code(code)
    rec = store.get_code(code) if valid_code else None
    if rec:
        store.log_scan(code, stage or "verify", client_ip(),
                       request.headers.get("User-Agent", "")[:200])
    gate = store.check_reportable(code, stage) if stage in schema.STAGES else {"ok": False, "reason": "未知端口"}
    # 事件只查一次：upstream 与 events 是同一份内容，之前查了两遍
    evs = store.get_events(code) if rec else []
    # 失败原因要分开说：码段不合规（疑似伪造）与根本没识别到码（识别问题）不是一回事
    if valid_code:
        fail_reason = "该追溯码未在本平台赋码"
    elif chain.code_shape_ok(code):
        fail_reason = "追溯码校验位不通过：该码不是本平台签发的码段，可能为伪造标签"
    else:
        fail_reason = "没有识别到完整的追溯码，请重新对准二维码扫描，或核对手动输入的内容"
    return jsonify({
        "ok": bool(rec),
        "reason": None if rec else fail_reason,
        "forged": (not valid_code) and chain.code_shape_ok(code),
        "unreadable": (not valid_code) and not chain.code_shape_ok(code),
        "code": code,
        "product": rec,
        "stage": stage,
        "gate": gate,
        "writable": bool(gate.get("ok")),
        "allowed_fields": [f["k"] for f in schema.FIELDS.get(stage, [])],
        "upstream": evs,
        "events": evs,
        "progress": store.code_progress(code) if rec else [],
        "scan_stats": store.scan_stats(code) if rec else None,
        "trace_url": f"{trace_base()}/t/{code}",
        # 各端「登记 / 作业 / 销售 / 验收时间」以它为准，前端只做展示
        "server_now": chain.now_minute(),
        "server_time_fields": schema.SERVER_TIME_FIELDS.get(stage, []),
    })


@app.post("/api/event")
def api_event():
    body = request.get_json(silent=True) or {}
    code = chain.extract_code(body.get("code", ""))
    stage = body.get("stage", "")
    if stage not in schema.STAGES:
        return jsonify({"ok": False, "reason": "未知端口"})
    actor = (body.get("actor") or "").strip()
    if not actor:
        return jsonify({"ok": False, "reason": "请先填写上报主体名称（企业 / 门店 / 个人）"})
    # 先判业务规则（是否赋码、本端次数上限、前置环节是否完成），再校验必填字段
    gate = store.check_reportable(code, stage)
    if not gate.get("ok"):
        return jsonify(gate)
    # 只接受本端允许上报的字段，越权字段一律丢弃
    sent = body.get("payload")
    raw = dict(sent) if isinstance(sent, dict) else {}
    # 「登记 / 作业 / 销售 / 验收时间」一律以服务端系统时间为准，客户端传什么都不作数
    schema.apply_server_time(stage, raw, chain.now_minute)
    payload, dropped = schema.sanitize_payload(stage, raw)
    for k in schema.IMAGE_FIELDS.get(stage, []):
        err = schema.image_error(payload.get(k, ""))
        if err:
            return jsonify({"ok": False, "reason": err})
    miss = schema.required_missing(stage, payload)
    if miss:
        return jsonify({"ok": False, "reason": "请补全必填项：" + "、".join(miss)})
    res = store.add_event(code, stage, actor, payload,
                          body.get("org_role", schema.STAGES[stage]["owner"]),
                          body.get("region", ""))
    if not res["ok"]:
        return jsonify(res)
    ev = res["event"]
    try:
        saved_payload = json.loads(ev.get("payload_json") or "{}")
    except (TypeError, ValueError):
        saved_payload = {}
    return jsonify({"ok": True, "event": {
        "seq": ev["seq"], "stage": ev["stage"], "event_type": ev["event_type"],
        "actor": ev["actor"], "timestamp": ev["timestamp"],
        # 返回服务端最终落库的内容：时间是强制覆盖后的系统时间，图片是校验过的 data URL
        "payload": saved_payload, "payload_hash": ev["payload_hash"],
        "event_hash": ev["event_hash"], "prev_hash": ev["prev_hash"],
        "signature": ev["signature"],
    }, "dropped": dropped,
        "verify": store.verify_code_chain(code), "progress": store.code_progress(code),
        "trace_url": f"{trace_base()}/t/{code}"})


@app.get("/api/now")
def api_now():
    """服务端当前时间（中国标准时间）。端口页用它与系统时间保持同步。"""
    return jsonify({"ok": True, "now": chain.now_minute(),
                    "iso": chain.now_iso(), "utc_offset": chain.UTC_OFFSET})


@app.get("/api/trace/<code>")
def api_trace(code):
    code = chain.extract_code(code)
    rec = store.get_code(code)
    return jsonify({"ok": bool(rec), "code": code, "valid": chain.is_valid_code(code), "product": rec,
                    "events": store.get_events(code) if rec else [],
                    "progress": store.code_progress(code) if rec else [],
                    "verify": store.verify_code_chain(code) if rec else None,
                    "scan_stats": store.scan_stats(code) if rec else None})


@app.post("/api/codes/generate")
def api_generate():
    if not admin_guard(request):
        return jsonify({"ok": False, "reason": "管理员口令错误"}), 401
    body = request.get_json(silent=True) or {}
    count = max(1, min(int(body.get("count", 1)), 500))
    codes = store.create_codes(count, (body.get("prefix") or "TC").upper()[:4], body.get("note", ""))
    return jsonify({"ok": True, "codes": codes, "count": len(codes), "base": public_base()})


@app.get("/api/codes")
def api_codes():
    if not admin_guard(request):
        return jsonify({"ok": False, "reason": "管理员口令错误"}), 401
    q = request.args.get("q", "")
    rows = store.list_codes(q, min(int(request.args.get("limit", 100)), 500))
    for r in rows:
        r["progress"] = store.code_progress(r["code"])
    return jsonify({"ok": True, "items": rows, "total": store.count_codes(q)})


@app.post("/api/admin/clear")
def api_clear():
    body = request.get_json(silent=True) or {}
    if not admin_guard(request):
        return jsonify({"ok": False, "reason": "管理员口令错误"}), 401
    mode = body.get("mode", "soft")
    if mode not in ("soft", "hard"):
        return jsonify({"ok": False, "reason": "未知的清除模式"})
    return jsonify(store.clear_code(chain.extract_code(body.get("code", "")), mode, body.get("operator", "管理员")))


@app.get("/api/admin/logs")
def api_logs():
    if not admin_guard(request):
        return jsonify({"ok": False, "reason": "管理员口令错误"}), 401
    return jsonify({"ok": True, "items": store.list_admin_logs(100)})


@app.get("/api/chain/blocks")
def api_blocks():
    return jsonify({"ok": True, "items": store.list_blocks(100), "verify": store.verify_blocks()})


@app.get("/api/chain/block/<int:height>")
def api_block(height):
    b = store.get_block(height)
    return jsonify({"ok": bool(b), "block": b})


@app.post("/api/chain/seal")
def api_seal():
    if not admin_guard(request):
        return jsonify({"ok": False, "reason": "管理员口令错误"}), 401
    return jsonify({"ok": True, **store.seal_now()})


@app.get("/api/chain/verify")
def api_verify():
    return jsonify({"ok": True, "blocks": store.verify_blocks(), "stats": store.stats()})


@app.get("/api/stats")
def api_stats():
    return jsonify({"ok": True, **store.stats(), "algo": chain.HASH_ALGO, "role": ROLE})


@app.get("/api/schema")
def api_schema():
    """端口与字段定义，便于第三方系统对接"""
    return jsonify({"ok": True, "stages": schema.STAGES, "order": schema.ORDER,
                    "prereq": schema.PREREQ, "fields": schema.FIELDS, "sites": schema.SITES})


@app.get("/api/backup")
def api_backup():
    if not admin_guard(request):
        return jsonify({"ok": False, "reason": "管理员口令错误"}), 401
    data = json.dumps(store.export_backup(), ensure_ascii=False, indent=2)
    return Response(data, mimetype="application/json; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=tracechain-backup.json"})


@app.post("/api/restore")
def api_restore():
    if not admin_guard(request):
        return jsonify({"ok": False, "reason": "管理员口令错误"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "codes" not in data:
        return jsonify({"ok": False, "reason": "备份文件格式不正确"})
    try:
        n = store.import_backup(data)
    except Exception as e:
        return jsonify({"ok": False, "reason": f"恢复失败：{e}"})
    _qr_bytes.cache_clear()
    return jsonify({"ok": True, "restored": n})


# ---------------------------------------------------------------- 免费层持久化
# Render 免费层没有持久磁盘，实例休眠/重新部署会清空数据库（表现为「未在本平台赋码」）。
# 这里用 GitHub 备份分支做冷备：启动时库为空则自动拉回，之后后台定时推快照。
try:
    import persist
except Exception:  # 备份模块出问题绝不能拖垮主服务
    persist = None

PERSIST_ON = bool(IS_CORE and persist and persist.enabled())
if PERSIST_ON:
    # bootstrap 会访问 GitHub 拉快照，绝不能在 gunicorn worker 启动路径上同步执行——
    # 否则冷备网络抖动会把 worker 卡住几十秒，Render 健康检查 5 秒超时 → 反复重启实例。
    def _persist_boot():
        try:
            _boot = persist.bootstrap(store)
            print(f"[persist] 启动自检：{_boot}", flush=True)
        except Exception as e:
            print(f"[persist] 启动自检失败（不影响服务）：{e}", flush=True)

    # 唯一保留的后台动作：实例重启后库为空时拉回冷备快照（一次性，几秒结束）。
    # 它不是常驻循环；TC_BOOTSTRAP=0 可彻底关闭，代价是重启即丢全部数据。
    if os.environ.get("TC_BOOTSTRAP", "1") != "0":
        threading.Thread(target=_persist_boot, daemon=True, name="persist-boot").start()
    persist.start_worker(store)


@app.post("/api/backup/push")
def api_backup_push():
    """立即把当前账本推到备份分支（管理员）。"""
    if not admin_guard(request):
        return jsonify({"ok": False, "reason": "管理员口令错误"}), 401
    if not PERSIST_ON:
        return jsonify({"ok": False, "reason": "未启用远程备份（缺少 TC_GITHUB_TOKEN）"})
    return jsonify(persist.run_once(store))


@app.get("/api/backup/status")
def api_backup_status():
    if not admin_guard(request):
        return jsonify({"ok": False, "reason": "管理员口令错误"}), 401
    return jsonify({"ok": True, "enabled": PERSIST_ON, "role": ROLE,
                    "repo": getattr(persist, "REPO", None) if persist else None,
                    "branch": getattr(persist, "BRANCH", None) if persist else None})


@app.errorhandler(404)
def not_found(e):
    return render_template("notfound.html", **ctx()), 404


# 作业站被唤醒时，顺带把休眠中的核心节点也叫起来（后台线程，不阻塞启动）
# 默认关闭：见 poke_core 说明，TC_WARMUP=1 可开启
if not IS_CORE and os.environ.get("TC_WARMUP", "0") != "0":
    poke_core(seconds=75)


# 核心节点自保温。
# 免费实例「15 分钟无入站流量」即休眠，而用户往往是隔一阵子演示一次，
# 于是每次都撞上冷启动。这里定时轻敲自己，让它在使用时段内保持清醒。
# 为了不把免费额度（750 实例小时/月）吃光，只在 07:00–22:59 之间保温：
# 每天约 16 小时 ≈ 480 小时，连同其余四站的零星唤醒仍留有充足余量。
# 默认关闭（TC_KEEPALIVE=1 可开启）：定时自敲会让实例永不休眠，
# 既吃免费额度，也让 Render 的健康检查在整个白天都盯着这个实例。
if IS_CORE and os.environ.get("TC_KEEPALIVE", "0") != "0":
    _SELF_URL = (os.environ.get("TC_SELF_URL")
                 or ("https://" + SITE["host"] if SITE.get("host") else "")
                 or PUBLIC_BASE)
    if _SELF_URL:
        def _keepalive_loop(_url=_SELF_URL.rstrip("/") + "/healthz",
                            _gap=max(60, int(os.environ.get("TC_KEEPALIVE_INTERVAL", "240")))):
            while True:
                time.sleep(_gap)
                try:
                    if 7 <= int(chain.now_minute()[11:13]) < 23:
                        with _NO_PROXY.open(_url, timeout=20):
                            pass
                except Exception:
                    pass

        threading.Thread(target=_keepalive_loop, daemon=True, name="keepalive").start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG") == "1")
