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
import time
import urllib.error
import urllib.request
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

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin888")
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "").rstrip("/")
ROLE = (os.environ.get("ROLE") or "core").strip().lower()
CORE_URL = os.environ.get("CORE_URL", "").rstrip("/")
IS_CORE = ROLE == "core" or not CORE_URL
WORKER_ROLES = ("produce", "logistics", "retail", "consume")

SITE = dict(schema.SITES.get(ROLE) or schema.SITES["core"])

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
def _proxy_to_core():
    """作业端不持有数据，所有 /api/* 请求转发到存证核心节点。"""
    url = CORE_URL + request.full_path
    if url.endswith("?"):
        url = url[:-1]
    data = request.get_data() or None
    last = None
    for attempt in range(3):
        r = urllib.request.Request(url, data=data, method=request.method)
        ct = request.headers.get("Content-Type")
        if ct:
            r.add_header("Content-Type", ct)
        tok = request.headers.get("X-Admin-Token")
        if tok:
            r.add_header("X-Admin-Token", tok)
        r.add_header("X-Forwarded-For", client_ip())
        try:
            with urllib.request.urlopen(r, timeout=80) as resp:
                ctype = resp.headers.get("Content-Type", "application/json")
                extra = {}
                cd = resp.headers.get("Content-Disposition")
                if cd:
                    extra["Content-Disposition"] = cd
                return Response(resp.read(), resp.status, content_type=ctype, headers=extra)
        except urllib.error.HTTPError as e:
            ctype = e.headers.get("Content-Type", "application/json") if e.headers else "application/json"
            return Response(e.read(), e.code, content_type=ctype)
        except Exception as e:                       # 网络抖动 / 冷启动
            last = e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    app.logger.warning("proxy to core failed: %s", last)
    return jsonify({"ok": False, "reason": "数据核心节点暂时不可达，请稍后重试"}), 503


@app.before_request
def _site_isolation():
    """每个站点只对外呈现自己那一个端口，其余路径一律 404。"""
    path = request.path
    if path.startswith("/static/") or path in ("/favicon.ico", "/robots.txt", "/healthz"):
        return None
    if IS_CORE:
        return None
    if path.startswith("/api/"):
        return _proxy_to_core()
    if path in ("/", "/" + ROLE):
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
        "upstream": store.get_events(code) if rec else [],
        "events": store.get_events(code) if rec else [],
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


@app.errorhandler(404)
def not_found(e):
    return render_template("notfound.html", **ctx()), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG") == "1")
