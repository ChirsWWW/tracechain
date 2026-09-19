# -*- coding: utf-8 -*-
"""检查 / 触发 Render 五站点部署。
用法：
    python render_check.py            # 查看各服务最近一次部署状态
    python render_check.py deploy     # 对五个服务各触发一次部署
    python render_check.py wait       # 轮询直到全部 live / 失败
"""
import json
import sys
import time
import urllib.error
import urllib.request

KEY = "rnd_FbGDOO7CiKwa6IZy1WCst9cPZkZy"
API = "https://api.render.com/v1"
SERVICES = {
    "core": "srv-dam006gae00c73civbm0",
    "produce": "srv-dam00encgkoc73fqim80",
    "logistics": "srv-dam00ge5vjqs73b7r5ag",
    "retail": "srv-dam00hlbedkc73a4uec0",
    "consume": "srv-dam00iu7bikc73fqd170",
}


def rq(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Authorization", "Bearer " + KEY)
    r.add_header("Content-Type", "application/json")
    r.add_header("Accept", "application/json")
    for i in range(3):
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < 2:
                time.sleep(3)
                continue
            return {"_error": e.code, "_body": e.read().decode("utf-8", "ignore")[:200]}
        except Exception as e:
            if i < 2:
                time.sleep(3)
                continue
            return {"_error": "net", "_body": str(e)[:200]}


def latest(sid):
    r = rq("GET", f"/services/{sid}/deploys?limit=1")
    if isinstance(r, list) and r:
        return r[0].get("deploy", r[0])
    return r


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    if mode == "deploy":
        for name, sid in SERVICES.items():
            r = rq("POST", f"/services/{sid}/deploys", {"clearCache": "do_not_clear"})
            print(f"{name:10s} 触发 → {r.get('id', r)}")
        return
    if mode == "wait":
        for _ in range(60):
            rows, done = [], True
            for name, sid in SERVICES.items():
                d = latest(sid)
                st = d.get("status", "?") if isinstance(d, dict) else "?"
                rows.append((name, st))
                if st not in ("live", "build_failed", "update_failed", "canceled", "pre_deploy_failed"):
                    done = False
            print(" | ".join(f"{n}={s}" for n, s in rows), flush=True)
            if done:
                break
            time.sleep(20)
        return
    for name, sid in SERVICES.items():
        d = latest(sid)
        if isinstance(d, dict):
            print(f"{name:10s} status={d.get('status')} commit={str(d.get('commit',{}).get('id',''))[:8]} "
                  f"finished={d.get('finishedAt')}")
        else:
            print(name, d)


if __name__ == "__main__":
    main()
