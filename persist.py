# -*- coding: utf-8 -*-
"""免费实例持久化兜底：把账本快照推到 GitHub 备份分支，重启后自动拉回。

Render 免费层没有持久磁盘，实例休眠或重新部署都会清空文件系统，
数据库随之丢失 —— 表现就是「扫自己的码提示：未在本平台赋码」。

本模块用 GitHub Git Data API 做冷备，不依赖任何付费资源：
    push_snapshot(obj)  —— 定时调用；内容无变化或库为空则跳过，不产生多余 commit
    fetch_snapshot()    —— 启动时调用；本地库为空才拉回最近一份快照
    start_worker(store) —— 后台线程定时冷备

启用条件：ROLE=core 且环境变量 TC_GITHUB_TOKEN 存在。
快照写在独立分支（默认 tc-backup），不污染 main 的代码历史。
"""
import base64
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
REPO = os.environ.get("TC_BACKUP_REPO", "ChirsWWW/tracechain")
BRANCH = os.environ.get("TC_BACKUP_BRANCH", "tc-backup")
PATH = os.environ.get("TC_BACKUP_PATH", "backup/tracechain.json")
TIMEOUT = 30

_last = {"digest": None}


def token() -> str:
    return (os.environ.get("TC_GITHUB_TOKEN") or "").strip()


def enabled() -> bool:
    return bool(token())


def _req(path, method="GET", data=None):
    r = urllib.request.Request(API + path, method=method)
    r.add_header("Authorization", "Bearer " + token())
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("User-Agent", "tracechain-backup")
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, body, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode("utf-8", "ignore")[:200]}
    except Exception as e:  # 网络不通时不能让主流程崩掉
        return {"_error": 0, "_body": str(e)[:200]}


def push_snapshot(obj) -> dict:
    """把快照提交到备份分支。内容与上次相同则跳过。"""
    if not enabled():
        return {"ok": False, "skipped": "no_token"}
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if digest == _last.get("digest"):
        return {"ok": True, "skipped": "unchanged"}

    base = _req(f"/repos/{REPO}")
    if base.get("_error"):
        return {"ok": False, "error": f"仓库不可达 {base.get('_body')}"}
    default_branch = base.get("default_branch") or "main"

    # 备份分支不存在则基于默认分支创建
    ref = _req(f"/repos/{REPO}/git/ref/heads/{BRANCH}")
    if ref.get("_error"):
        dref = _req(f"/repos/{REPO}/git/ref/heads/{default_branch}")
        if dref.get("_error"):
            return {"ok": False, "error": f"默认分支不可达 {dref.get('_body')}"}
        _req(f"/repos/{REPO}/git/refs", "POST",
             {"ref": f"refs/heads/{BRANCH}", "sha": dref["object"]["sha"]})
        ref = _req(f"/repos/{REPO}/git/ref/heads/{BRANCH}")
    if ref.get("_error"):
        return {"ok": False, "error": f"备份分支不可达 {ref.get('_body')}"}
    parent = ref["object"]["sha"]

    blob = _req(f"/repos/{REPO}/git/blobs", "POST", {"content": text, "encoding": "utf-8"})
    if blob.get("_error"):
        return {"ok": False, "error": f"blob 失败 {blob.get('_body')}"}

    commit = _req(f"/repos/{REPO}/git/commits/{parent}")
    base_tree = (commit.get("tree") or {}).get("sha")
    if not base_tree:
        return {"ok": False, "error": "取不到 base tree"}

    tree = _req(f"/repos/{REPO}/git/trees", "POST", {
        "base_tree": base_tree,
        "tree": [{"path": PATH, "mode": "100644", "type": "blob", "sha": blob["sha"]}],
    })
    if tree.get("_error"):
        return {"ok": False, "error": f"tree 失败 {tree.get('_body')}"}

    newc = _req(f"/repos/{REPO}/git/commits", "POST", {
        "message": f"chore(backup): 账本快照 {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "tree": tree["sha"], "parents": [parent],
    })
    if newc.get("_error"):
        return {"ok": False, "error": f"commit 失败 {newc.get('_body')}"}

    upd = _req(f"/repos/{REPO}/git/refs/heads/{BRANCH}", "PATCH", {"sha": newc["sha"]})
    if upd.get("_error"):
        return {"ok": False, "error": f"ref 更新失败 {upd.get('_body')}"}

    _last["digest"] = digest
    return {"ok": True, "commit": newc["sha"][:8], "bytes": len(text.encode("utf-8"))}


def fetch_snapshot():
    """从备份分支取最近一份快照；取不到返回 None。"""
    if not enabled():
        return None
    d = _req(f"/repos/{REPO}/contents/{PATH}?ref={BRANCH}")
    if d.get("_error") or "content" not in d:
        return None
    try:
        return json.loads(base64.b64decode(d["content"]).decode("utf-8"))
    except Exception:
        return None


def run_once(store_mod) -> dict:
    """库非空才备份，避免把空库推上去覆盖掉好数据。"""
    if not enabled():
        return {"ok": False, "skipped": "no_token"}
    try:
        if store_mod.is_empty():
            return {"ok": False, "skipped": "empty_db"}
        return push_snapshot(store_mod.export_backup())
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def start_worker(store_mod):
    """后台线程定时冷备。失败只打印，不影响服务。"""
    if not enabled():
        return None
    interval = max(60, int(os.environ.get("TC_BACKUP_INTERVAL", "300")))

    def loop():
        while True:
            time.sleep(interval)
            try:
                r = run_once(store_mod)
                if r.get("ok") and not r.get("skipped"):
                    print(f"[persist] 已备份 {r.get('bytes')} 字节 -> {r.get('commit')}", flush=True)
                elif r.get("error"):
                    print(f"[persist] 备份失败：{r.get('error')}", flush=True)
            except Exception as e:
                print(f"[persist] 备份异常：{e}", flush=True)

    t = threading.Thread(target=loop, daemon=True, name="tc-backup")
    t.start()
    return t


def bootstrap(store_mod) -> dict:
    """启动时调用：本地库为空才从备份分支恢复。"""
    if not enabled():
        return {"ok": False, "skipped": "no_token"}
    try:
        if not store_mod.is_empty():
            return {"ok": True, "skipped": "db_not_empty"}
        snap = fetch_snapshot()
        if not snap:
            return {"ok": False, "skipped": "no_snapshot"}
        n = store_mod.import_backup(snap)
        return {"ok": True, "restored": n}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
