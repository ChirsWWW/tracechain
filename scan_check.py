# -*- coding: utf-8 -*-
"""模拟「用手机扫这张二维码」后各端口站点的取码结果。
覆盖用户实际会遇到的输入：整条验真链接、带参数链接、旧 /c/ 链接、裸码、小写、脏数据。
"""
import json
import os
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


def scan(site, payload):
    r = urllib.request.Request(SITES[site] + "/api/scan",
                               data=json.dumps({"code": payload, "stage": site}).encode(),
                               method="POST")
    r.add_header("Content-Type", "application/json")
    for _ in range(4):
        try:
            with urllib.request.urlopen(r, timeout=75) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            continue
    return {"_error": "net"}


ok = bad = 0
for site in ("produce", "logistics", "retail", "consume"):
    print(f"\n===== {site} 站 =====")
    for payload, desc in CASES:
        res = scan(site, payload)
        got = res.get("code")
        valid = res.get("valid_code", res.get("valid"))
        forged = res.get("reason", "")
        expect_ok = not desc.startswith("只有域名")
        good = (got == CODE) if expect_ok else (got != CODE)
        ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
        mark = "[OK]" if good else "[!!]"
        print(f"  {mark} {desc}\n       取到={got}  合规={valid}  {forged if not valid else ''}")

print(f"\n合计 {ok} 项符合预期，{bad} 项异常")
