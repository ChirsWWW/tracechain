# -*- coding: utf-8 -*-
"""清空线上账本：把所有码硬删除。
配合 clear_code 的新逻辑（硬删已封块明细时同步撤销受影响区块），
这一步会把残留的失效区块一并清掉，让全局账本重新校验得通。
用法：python reset_remote.py
"""
import json
import os
import urllib.request

CORE = os.environ.get("TC_CORE", "https://tracechain-core.onrender.com").rstrip("/")
TOKEN = os.environ.get("TC_PWD", "admin888")


def req(path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(CORE + path, data=data, method="POST" if data else "GET")
    r.add_header("Content-Type", "application/json")
    r.add_header("X-Admin-Token", TOKEN)
    for _ in range(4):
        try:
            with urllib.request.urlopen(r, timeout=80) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
    return {"ok": False, "reason": "网络不可达"}


items = req("/api/codes?limit=500").get("items", [])
print(f"线上现有 {len(items)} 个码，开始硬删除…")
rolled = 0
for it in items:
    r = req("/api/admin/clear", {"code": it["code"], "mode": "hard", "operator": "重置"})
    rolled += r.get("rolled_back_blocks", 0)
    print(f"  {it['code']}: {r.get('ok')} 撤销区块 {r.get('rolled_back_blocks', 0)}")
print(f"撤销区块合计 {rolled} 个")
s = req("/api/stats")
print("当前：", {k: s[k] for k in ("codes", "events", "blocks") if k in s})
v = req("/api/chain/blocks").get("verify", {})
print("全链校验：", v.get("valid"), v.get("issues"))
