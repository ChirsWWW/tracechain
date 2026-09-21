# -*- coding: utf-8 -*-
"""模拟「用手机扫这张二维码」后各端口站点的取码结果。
覆盖用户实际会遇到的输入：整条验真链接、带参数链接、旧 /c/ 链接、裸码、小写、脏数据。
"""
import json
import os
import time
import urllib.error
import urllib.request

CORE = os.environ.get("TC_CORE", "https://tracechain-core.onrender.com").rstrip("/")
CODE = os.environ.get("TC_CODE", "TC260917BWEQ5VF3-2A27")
SITES = {
    "produce": os.environ.get("TC_PRODUCE", "https://tracechain-produce.onrender.com").rstrip("/"),
    "logistics": os.environ.get("TC_LOGISTICS", "https://tracechain-logistics.onrender.com").rstrip("/"),
    "retail": os.environ.get("TC_RETAIL", "https://tracechain-retail.onrender.com").rstrip("/"),
    "consume": os.environ.get("TC_CONSUME", "https://tracechain-consume.onrender.com").rstrip("/"),
}

CASES = [
    (f"{CORE}/t/{CODE}", "扫整条验真链接（二维码里的真实内容）"),
    (f"{CORE}/t/{CODE}?from=wechat", "带查询参数的链接"),
    (f"{CORE}/c/{CODE}", "旧版 /c/ 链接"),
    (CODE, "手动输入裸码"),
    (CODE.lower(), "小写裸码"),
    (f"  {CORE}/t/{CODE}\n", "首尾带空白（扫码器常见）"),
    ("tracechain-core", "只有域名、没有码（应提示未识别）"),
]


def scan(site, payload, tries=8):
    """带退避地取扫码结果。

    注意：Render 免费层冷启动要几十秒，期间回来的可能是网关的 HTML 兜底页。
    老写法 4 次紧凑重试（中间不等待）会在这时判成「网络异常」，
    而排查扫码问题恰恰最需要这个工具可靠 —— 所以退避要够长，非 JSON 也算重试。
    """
    r = urllib.request.Request(SITES[site] + "/api/scan",
                               data=json.dumps({"code": payload, "stage": site}).encode(),
                               method="POST")
    r.add_header("Content-Type", "application/json")
    last = "net"
    for i in range(tries):
        try:
            with urllib.request.urlopen(r, timeout=75) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if i < tries - 1:
                time.sleep(min(3 + i * 2, 10))
        except Exception as e:
            # 含 JSON 解析失败（= 拿到的不是 JSON，多半是网关 HTML 页）
            last = type(e).__name__
            if i < tries - 1:
                time.sleep(min(3 + i * 2, 10))
    return {"_error": last}


if "TC_CODE" not in os.environ:
    print(f"提示：未设置 TC_CODE，使用内置样例码 {CODE}。\n"
          f"      演示码会随 seed_demo.py 重新生成而失效，若结果全是「未赋码」，\n"
          f"      请用 TC_CODE=<当前演示码> 再跑一次，例如：\n"
          f"      TC_CODE=TC260919V3ZSGM00-7A10 python scan_check.py\n")

ok = bad = 0
for site in ("produce", "logistics", "retail", "consume"):
    print(f"\n===== {site} 站 =====")
    for payload, desc in CASES:
        res = scan(site, payload)
        got = res.get("code")
        reason = res.get("reason") or ""
        # /api/scan 的真实字段是 ok / forged / unreadable。
        # 老写法找的 valid_code、valid 都不存在，于是「合规」这栏永远打印 None，
        # 排查时最该看到的信息反而丢了。
        registered = res.get("ok")
        forged = res.get("forged")
        unreadable = res.get("unreadable")
        flags = f"已赋码={registered}"
        if forged:
            flags += "｜校验位不通过（疑似伪造）"
        if unreadable:
            flags += "｜未能识别完整码"
        if res.get("_error"):
            flags = f"请求失败（{res['_error']}）"
        expect_ok = not desc.startswith("只有域名")
        good = (got == CODE) if expect_ok else (got != CODE)
        ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
        mark = "[OK]" if good else "[!!]"
        print(f"  {mark} {desc}\n       取到={got}  {flags}  {reason}")

print(f"\n合计 {ok} 项符合预期，{bad} 项异常")
