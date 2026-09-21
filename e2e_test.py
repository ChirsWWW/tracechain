# -*- coding: utf-8 -*-
"""
端到端自测（v3 五站点独立部署版）

覆盖：
  1. 五个站点各自独立（各自域名、各自只呈现本端、互不跳转）
  2. 跨站点数据共享（作业端把数据写到核心节点）
  3. 扫码后先看到上游环节已登记信息
  4. 本端只允许上报本端字段，越权字段被丢弃
  5. 空码 → 生产 → 流通多次 → 销售 → 消费 全流程 + 顺序与次数校验
  6. 篡改检测 / 区块 / 清除 / 备份恢复 / 鉴权

用法（本地）：
    python e2e_test.py
用法（线上）：
    TC_CORE=https://tracechain-core.onrender.com \
    TC_PRODUCE=https://tracechain-produce.onrender.com \
    TC_LOGISTICS=https://tracechain-logistics.onrender.com \
    TC_RETAIL=https://tracechain-retail.onrender.com \
    TC_CONSUME=https://tracechain-consume.onrender.com python e2e_test.py
"""
import base64
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

SITES = {
    "core": os.environ.get("TC_CORE", "http://127.0.0.1:8010").rstrip("/"),
    "produce": os.environ.get("TC_PRODUCE", "http://127.0.0.1:8011").rstrip("/"),
    "logistics": os.environ.get("TC_LOGISTICS", "http://127.0.0.1:8012").rstrip("/"),
    "retail": os.environ.get("TC_RETAIL", "http://127.0.0.1:8013").rstrip("/"),
    "consume": os.environ.get("TC_CONSUME", "http://127.0.0.1:8014").rstrip("/"),
}
CORE = SITES["core"]
TOKEN = os.environ.get("TC_PWD", "admin888")
LOCAL = "127.0.0.1" in CORE or "localhost" in CORE
PASS, FAIL = [], []


def _as_json(blob):
    """尽力把应答字节解析成对象；不是 JSON（多为网关 HTML 错误页）返回 None。

    为什么需要它：Render 免费层冷启动或网关兜底时，回来的是一整页 HTML，
    而不是我们的 JSON。老写法直接把这个 bytes 交回调用方，调用方一句 .get()
    就 AttributeError 崩掉 —— 脚本报的是「测试挂了」，掩盖了「服务暂时不可用」。
    """
    try:
        return json.loads(blob.decode("utf-8"))
    except Exception:
        return None


def req(path, body=None, method=None, raw=False, token=TOKEN, site="core"):
    url = SITES[site] + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    last = None
    # 502/503/504 多半不是真故障，而是作业站在说「核心节点正在唤醒」。
    # 免费实例冷启动是常态（实测 30~60 秒），本地测不出来的假失败全出在这里，必须重试。
    tries = int(os.environ.get("TC_E2E_TRIES", "12"))
    for attempt in range(tries):
        r = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
        r.add_header("Content-Type", "application/json")
        r.add_header("X-Admin-Token", token)
        try:
            with urllib.request.urlopen(r, timeout=75) as resp:
                ctype = resp.headers.get("Content-Type", "")
                payload = resp.read()
                if raw:
                    return resp.status, payload
                obj = _as_json(payload)
                if obj is not None:
                    return resp.status, obj
                # 200 但内容不是 JSON：同样是网关兜底页，按「暂时不可用」重试，
                # 绝不能把 bytes 交回去让调用方崩在半路。
                last = Exception(f"HTTP {resp.status} 非 JSON 应答（{ctype[:40] or '无类型'}）")
                time.sleep(3)
                continue
        except urllib.error.HTTPError as e:
            blob = e.read()
            retryable = e.code in (502, 503, 504)
            # 退避拉长：冷启动要几十秒，3 秒一轮打不够，8 轮也就 24 秒。
            if retryable and attempt < tries - 1:
                last = Exception(f"HTTP {e.code}（核心节点唤醒中）")
                time.sleep(min(3 + attempt * 2, 10))
                continue
            obj = _as_json(blob)
            if obj is not None:
                return e.code, obj
            # 非 JSON 且不再重试：也要给调用方一个形状正确的对象
            return e.code, {"ok": False,
                            "reason": f"HTTP {e.code}：" + blob.decode("utf-8", "ignore")[:200]}
        except Exception as e:          # 超时 / DNS 抖动 / 连接重置，一律重试
            last = e
            time.sleep(2 + attempt * 2)
    # 重试真的用光了：
    #   raw=True 的调用方（要二进制/HTML，如 /qr/*.png、/api/backup）必须拿到异常，
    #   否则它们会对一个 dict 调 .decode()。
    #   JSON 调用方则要给一个形状正确的对象，让用例报「失败」而不是让脚本抛栈崩掉——
    #   测试脚本崩溃会把「服务暂时不可用」伪装成「测试代码有 bug」，最难排查的就是这种。
    if raw:
        raise last
    return 0, {"ok": False, "reason": f"重试 {tries} 次仍失败：{last}"}


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK] " if cond else "  [!!] ") + name + (("  → " + str(extra)) if extra else ""))


def text(path, site="core"):
    st, raw = req(path, raw=True, site=site)
    return st, (raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw))


SITE_TITLES = {
    "produce": "源产通", "logistics": "运链通", "retail": "销证通",
    "consume": "正源查", "core": "溯链中心",
}

print("== 1. 五个站点各自独立，根路径只呈现本端 ==")
for role, name in SITE_TITLES.items():
    st, html = text("/", role)
    check(f"{role:9s} 站首页 200", st == 200, st)
    check(f"{role:9s} 站首页是「{name}」", name in html, name not in html)

print("\n== 2. 站点之间互不提供对方功能 ==")
for role in ["produce", "logistics", "retail", "consume"]:
    others = [s for s in ["produce", "logistics", "retail", "consume"] if s != role]
    codes = []
    for o in others:
        st, _ = req("/" + o, raw=True, site=role)
        codes.append(st)
    check(f"{role:9s} 站访问其他端口路径全部 404", all(c == 404 for c in codes), codes)
    st, _ = req("/admin", raw=True, site=role)
    check(f"{role:9s} 站不提供管理端", st == 404, st)
    st, _ = req("/chain", raw=True, site=role)
    check(f"{role:9s} 站不提供区块浏览器", st == 404, st)
    st, _ = req("/healthz", raw=True, site=role)
    check(f"{role:9s} 站健康检查可用", st == 200, st)

print("\n== 3. 旧地址 301 重定向（已印标签不失效）==")
for old in ["/p/produce", "/p/logistics", "/p/consume"]:
    st, _ = req(old, raw=True, method="GET")
    check(f"核心站 {old} 重定向", st in (200, 301), st)

print("\n== 4. 赋码只产生空码（管理端在核心站）==")
st, j = req("/api/codes/generate", {"count": 2, "prefix": "TC", "note": "自测批次"})
check("生成 2 个空码", st == 200 and len(j.get("codes", [])) == 2, j)
CODE = j["codes"][0]
st, t = req("/api/trace/" + CODE)
check("新码状态 blank", t["product"]["status"] == "blank", t["product"]["status"])
check("新码无商品信息", (t["product"]["product_name"] or "") == "", t["product"]["product_name"])
check("新码无存证记录", len(t["events"]) == 0, len(t["events"]))
st, html = text("/t/" + CODE)
check("验真页提示尚未登记", "尚未登记" in html)

print("\n== 5. 顺序校验：未生产登记时流通/销售被拒 ==")
st, j = req("/api/event", {"code": CODE, "stage": "logistics", "actor": "某物流",
                           "payload": {"action": "出库", "occur_at": "2026-09-17T10:00",
                                       "goods_state": "完好", "carrier": "顺丰"}}, site="logistics")
check("流通站被拒（缺生产登记）", not j.get("ok") and j.get("missing_stage") == "produce", j.get("reason"))
st, j = req("/api/event", {"code": CODE, "stage": "retail", "actor": "某门店",
                           "payload": {"action": "销售核销", "shop": "某店"}}, site="retail")
check("销售站被拒（缺生产登记）", not j.get("ok") and j.get("missing_stage") == "produce", j.get("reason"))

print("\n== 6. 生产站扫码：先看到上游环节已登记信息（此时为空）==")
st, s = req("/api/scan", {"code": CODE, "stage": "produce"}, site="produce")
check("生产站可扫码", s.get("ok"), s.get("reason"))
check("生产站可作业", s.get("writable"), s.get("gate"))
check("生产站返回上游记录字段", "upstream" in s, list(s.keys())[:6])
check("生产站上游记录为 0 条", len(s.get("upstream", [])) == 0, len(s.get("upstream", [])))
check("生产站允许字段列表非空", len(s.get("allowed_fields", [])) > 0, s.get("allowed_fields"))

print("\n== 7. 生产端登记（含越权字段应被丢弃）==")
st, j = req("/api/event", {"code": CODE, "stage": "produce", "actor": "江西绿野山茶油有限公司",
                           "region": "江西 宜春",
                           "payload": {"product_name": "有机山茶油", "spec": "500ml/瓶", "net_weight": "500ml",
                                       "batch": "B20260917", "occur_at": "2026-09-17T08:30",
                                       "goods_state": "合格待售", "produce_date": "2026-09-15",
                                       "shelf_life": "18 个月", "standard": "GB/T 1534",
                                       "license": "SC10236070200001", "origin": "江西 宜春",
                                       "qc_result": "合格", "illegal_field": "越权数据"}}, site="produce")
check("生产站登记成功", j.get("ok"), j.get("reason", ""))
check("越权字段被丢弃", j.get("dropped") == ["illegal_field"], j.get("dropped"))
st, t = req("/api/trace/" + CODE)
check("码上回填商品身份", t["product"]["product_name"] == "有机山茶油" and t["product"]["status"] == "active",
      t["product"]["product_name"])
st, j = req("/api/event", {"code": CODE, "stage": "produce", "actor": "另一家厂",
                           "payload": {"product_name": "X", "batch": "Y", "occur_at": "2026-09-17T09:00",
                                       "goods_state": "待检", "produce_date": "2026-09-02"}}, site="produce")
check("生产站二次登记被拒", not j.get("ok"), j.get("reason"))

print("\n== 8. 流通站扫码：能看到上游（生产端）已登记信息 ==")
st, s = req("/api/scan", {"code": CODE, "stage": "logistics"}, site="logistics")
ups = s.get("upstream", [])
check("流通站上游记录 1 条（生产端）", len(ups) == 1, len(ups))
check("上游记录环节为 produce", ups and ups[0]["stage"] == "produce", ups[0]["stage"] if ups else "-")
check("上游记录带商品名", s["product"].get("product_name") == "有机山茶油", s["product"].get("product_name"))
check("生产站商品名对流通站只读可见", "有机山茶油" in json.dumps(ups, ensure_ascii=False))

print("\n== 9. 必填与越权字段校验（流通站）==")
st, j = req("/api/event", {"code": CODE, "stage": "logistics", "actor": "某物流",
                           "payload": {"action": "出库"}}, site="logistics")
# 作业时间已改为服务端系统时间自动填充，不会再出现在「缺必填」提示里
check("缺必填项被拒并列出字段",
      not j.get("ok") and "承运商" in (j.get("reason") or "") and "货物状态" in (j.get("reason") or "")
      and "作业时间" not in (j.get("reason") or ""),
      j.get("reason"))
st, j = req("/api/event", {"code": CODE, "stage": "logistics", "actor": "顺丰冷运",
                           "payload": {"action": "出库", "occur_at": "2026-09-17T09:00",
                                       "goods_state": "完好", "carrier": "顺丰冷运",
                                       "product_name": "试图篡改商品名", "price": "999"}}, site="logistics")
check("流通站越权字段被丢弃", j.get("ok") and sorted(j.get("dropped", [])) == ["price", "product_name"],
      j.get("dropped"))
st, t = req("/api/trace/" + CODE)
check("生产端字段未被流通端篡改", t["product"]["product_name"] == "有机山茶油", t["product"]["product_name"])

print("\n== 10. 流通站可多次上报（唯一不限次的站点）==")
for i, act in enumerate(["干线运输", "到货入库"]):
    st, j = req("/api/event", {"code": CODE, "stage": "logistics", "actor": f"承运方{i + 1}",
                               "payload": {"action": act, "occur_at": f"2026-09-17T1{i + 1}:00",
                                           "goods_state": "完好", "carrier": f"物流公司{i + 1}",
                                           "waybill": f"SF771200{i}", "from_loc": "南昌",
                                           "to_loc": "重庆"}}, site="logistics")
    check(f"流通站第 {i + 2} 次上报（{act}）", j.get("ok"), j.get("reason", ""))

print("\n== 11. 销售站 / 消费站各限一次 ==")
st, s = req("/api/scan", {"code": CODE, "stage": "retail"}, site="retail")
check("销售站上游记录 4 条（生产1+流通3）", len(s.get("upstream", [])) == 4, len(s.get("upstream", [])))
st, j = req("/api/event", {"code": CODE, "stage": "retail", "actor": "解放碑旗舰店",
                           "payload": {"action": "销售核销", "occur_at": "2026-09-17T15:00",
                                       "goods_state": "已售出", "shop": "解放碑旗舰店",
                                       "shop_code": "CQ-0017", "price": "128"}}, site="retail")
check("销售站核销成功", j.get("ok"), j.get("reason", ""))
st, j = req("/api/event", {"code": CODE, "stage": "retail", "actor": "另店",
                           "payload": {"action": "销售核销", "occur_at": "2026-09-17T16:00",
                                       "goods_state": "已售出", "shop": "Y"}}, site="retail")
check("销售站二次核销被拒", not j.get("ok"), j.get("reason"))
st, s = req("/api/scan", {"code": CODE, "stage": "consume"}, site="consume")
check("消费站上游记录 5 条", len(s.get("upstream", [])) == 5, len(s.get("upstream", [])))
st, j = req("/api/event", {"code": CODE, "stage": "consume", "actor": "消费者张先生",
                           "payload": {"channel": "线下门店", "occur_at": "2026-09-17T17:00",
                                       "goods_state": "完好", "city": "重庆", "rating": "满意"},
                           "region": "重庆 渝中区"}, site="consume")
check("消费站登记成功", j.get("ok"), j.get("reason", ""))
st, j = req("/api/event", {"code": CODE, "stage": "consume", "actor": "另一人",
                           "payload": {"channel": "电商平台"}}, site="consume")
check("消费站二次登记被拒", not j.get("ok"), j.get("reason"))

print("\n== 12. 追溯链与进度（核心站汇总）==")
st, t = req("/api/trace/" + CODE)
check("共 6 条记录（生产1+流通3+销售1+消费1）", len(t["events"]) == 6, len(t["events"]))
check("链路校验通过", t["verify"]["valid"], t["verify"]["issues"])
check("四个环节全部已填充", all(p["done"] for p in t["progress"]),
      [(p["label"], p["done"]) for p in t["progress"]])
check("每条记录都带时间与状态字段",
      all("occur_at" in json.dumps(e["payload"], ensure_ascii=False) for e in t["events"]),
      [e["stage"] for e in t["events"]])

print("\n== 13. 扫码取码：整条链接 / 旧链接 / 裸码都必须识别正确 ==")
st, j = req("/api/codes/generate", {"count": 1, "prefix": "TC", "note": "自测"})
BLANK = j["codes"][0]
st, s = req("/api/scan", {"code": "https://tracechain-core.onrender.com/t/" + CODE,
                          "stage": "logistics"}, site="logistics")
check("扫整条验真链接能取到码（不取成域名 tracechain-core）", s.get("code") == CODE, s.get("code"))
check("扫整条链接不误报伪造码", not s.get("forged"), s.get("reason"))
st, s = req("/api/scan", {"code": "https://tracechain-logistics.onrender.com/?code=" + CODE,
                          "stage": "logistics"}, site="logistics")
check("带 ?code= 的链接能取码", s.get("code") == CODE, s.get("code"))
st, s = req("/api/scan", {"code": "/c/" + CODE, "stage": "logistics"}, site="logistics")
check("旧 /c/ 链接仍兼容", s.get("code") == CODE, s.get("code"))
st, s = req("/api/scan", {"code": CODE, "stage": "logistics"}, site="logistics")
check("裸码正常识别", s.get("code") == CODE and not s.get("forged"), s.get("code"))
for site_name in ("produce", "logistics", "retail", "consume"):
    st, html = req("/", raw=True, site=site_name)
    check(f"{site_name} 站页面已移除待作业清单",
          "待作业清单" not in html.decode("utf-8", "ignore") and "worklist" not in html.decode("utf-8", "ignore"))

print("\n== 13b. 时间锁定为系统时间 / 检验报告编号移除 / 图片凭证 ==")
# 客户端传 1999 年，服务端必须无视，仍然写系统时间
st, j = req("/api/event", {"code": BLANK, "stage": "produce", "actor": "自测工厂",
                           "payload": {"product_name": "时间锁定测试品", "batch": "T1",
                                       "occur_at": "1999-01-01T00:00", "goods_state": "合格待售",
                                       "produce_date": "2026-01-01",
                                       "qc_report": "这个字段应当已被移除"},
                           }, site="produce")
check("生产端登记成功", j.get("ok"), j.get("reason", ""))
check("检验报告编号已移除（作为越权字段被丢弃）",
      "qc_report" in (j.get("dropped") or []), j.get("dropped"))
def _saved_payload(resp):
    """/api/event 返回的行里 payload 是 JSON 字符串"""
    raw = (resp.get("event") or {}).get("payload")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


saved_at = _saved_payload(j).get("occur_at", "")
check("客户端传的时间没被采信", saved_at and saved_at != "1999-01-01T00:00", saved_at)
st, now = req("/api/now")
check("系统时间接口可用", bool(now.get("ok") and now.get("now")), now)


def _mins(s):
    import datetime as _dt
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M")
    except Exception:
        return None


diff = None
if _mins(saved_at) and _mins(now.get("now", "")):
    diff = abs((_mins(saved_at) - _mins(now["now"])).total_seconds())
check("登记时间就是系统时间（误差 ≤ 2 分钟）", diff is not None and diff <= 120,
      (saved_at, now.get("now"), diff))

# 图片凭证：可选的 data URL，随 payload 进哈希
TINY_JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64).decode()
st, j = req("/api/event", {"code": BLANK, "stage": "logistics", "actor": "自测承运",
                           "payload": {"action": "出库", "occur_at": "1999-01-01T00:00",
                                       "goods_state": "完好", "carrier": "自测承运",
                                       "photo": TINY_JPEG}}, site="logistics")
check("流通端带图片上报成功", j.get("ok"), j.get("reason", ""))
check("图片已写入存证 payload", _saved_payload(j).get("photo", "").startswith("data:image/"))
st, j = req("/api/event", {"code": BLANK, "stage": "retail", "actor": "自测门店",
                           "payload": {"action": "销售核销", "occur_at": "1999-01-01T00:00",
                                       "goods_state": "已售出", "shop": "自测门店",
                                       "photo": "这不是图片"}}, site="retail")
check("非图片内容被拒绝", not j.get("ok") and "图片" in (j.get("reason") or ""), j.get("reason"))

print("\n== 14. 篡改检测 ==")
if LOCAL:
    db = sqlite3.connect(os.environ.get("TC_DB", "data/v3.db"))
    row = db.execute("SELECT id, payload_json FROM events WHERE code=? ORDER BY seq LIMIT 1", (CODE,)).fetchone()
    db.execute("UPDATE events SET payload_json=? WHERE id=?",
               (row[1].replace("合格待售", "不合格（被人为篡改）"), row[0]))
    db.commit()
    db.close()
    st, t = req("/api/trace/" + CODE)
    check("篡改后链路校验失败", not t["verify"]["valid"], t["verify"]["issues"])
    check("检出摘要不匹配", any("摘要不匹配" in i for i in t["verify"]["issues"]))
else:
    print("  [--] 线上环境跳过（需直连数据库改记录）")

print("\n== 15. 区块 ==")
st, j = req("/api/chain/seal", {})
check("打包区块", st == 200 and j.get("ok"), j)
st, ch = req("/api/chain/blocks")
check("全链校验通过", ch["verify"]["valid"], ch["verify"]["issues"])

print("\n== 16. 二维码与公众验真页（在核心站，独立于五个作业端）==")
st, raw = req("/qr/" + CODE + ".png", raw=True)
check("二维码 PNG 正常", st == 200 and raw[:4] == b"\x89PNG", st)
st, raw = req("/qrurl?u=" + CORE, raw=True)
check("站点入口二维码接口可用", st == 200 and raw[:4] == b"\x89PNG", st)
st, html = text("/t/" + CODE)
check("验真页含商品名", "有机山茶油" in html)
check("验真页含链校验结论", "存证链路完整" in html or "存证链路存在异常" in html)
check("验真页含时间与状态", "产品状态" in html and "登记时间" in html)
check("验真页已移除「补充你的购买信息」板块", "补充你的购买信息" not in html)
check("验真页已移除「前往消费端登记」按钮", "前往消费端登记" not in html)
check("验真页已移除管理控制台入口", "管理控制台" not in html)
check("验真页已移除区块浏览器入口", "区块浏览器" not in html)
st, admin_html = text("/admin")
check("管理控制台自身仍保留后台导航", "管理控制台" in admin_html and "区块浏览器" in admin_html)

print("\n== 17. 管理员清除 ==")
st, j = req("/api/admin/clear", {"code": CODE, "mode": "soft", "operator": "自测"})
check("软清除成功", j.get("ok") and j.get("removed") == 6, j)
st, t = req("/api/trace/" + CODE)
check("软清除后明细为空且状态为 cleared",
      len(t["events"]) == 0 and t["product"]["status"] == "cleared", t["product"]["status"])
st, j = req("/api/admin/clear", {"code": BLANK, "mode": "hard", "operator": "自测"})
check("硬删除成功", j.get("ok"), j)
st, t = req("/api/trace/" + BLANK)
check("硬删除后码不存在", not t["ok"], t["ok"])
# 硬删除会删掉已封块的明细，若不同步撤销区块，全局账本就永久校验不通过了
st, ch = req("/api/chain/blocks")
check("硬删除已封块的记录后，全局账本仍然校验通过", ch["verify"]["valid"], ch["verify"]["issues"])

print("\n== 18. 备份与恢复 ==")
st, raw = req("/api/backup", raw=True)
backup = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
_, before = req("/api/stats")
# ⚠️ 这是全套用例里唯一会「清空整库」的动作。线上跑时一旦中途异常
# （超时、网关 502、脚本自身报错），演示库就会被留在清空状态。
# 所以必须用 finally 兜底恢复：先置标志，再做删除，哪怕删到一半失败也要恢复。
destructive_started = False
try:
    destructive_started = True
    for c in req("/api/codes?limit=500")[1].get("items", []):
        req("/api/admin/clear", {"code": c["code"], "mode": "hard", "operator": "自测"})
    _, emptied = req("/api/stats")
    check("清空后码数为 0", emptied["codes"] == 0, emptied["codes"])
finally:
    if destructive_started:
        st, j = req("/api/restore", backup)
        check("恢复备份成功（含异常中断的兜底恢复）", j.get("ok"), j.get("reason", ""))
_, after = req("/api/stats")
check("恢复后数据量一致", after["codes"] == before["codes"] and after["events"] == before["events"],
      f"before={before['codes']}/{before['events']} after={after['codes']}/{after['events']}")

print("\n== 19. 鉴权与结构接口 ==")
st, _ = req("/api/codes", token="wrong")
check("错误口令被拒", st == 401, st)
st, s = req("/api/schema")
check("端口字段定义可对外提供", s["ok"] and "produce" in s["fields"], list(s.get("fields", {}).keys()))
check("站点定义可对外提供", "produce" in s.get("sites", {}), list(s.get("sites", {}).keys()))

print("\n" + "=" * 48)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("全部通过")
