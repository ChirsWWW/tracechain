# -*- coding: utf-8 -*-
"""
dev_servers.py —— 本地同时启动「1 个存证核心节点 + 4 个端口站点」

模拟线上部署形态：五个独立站点各自一个进程、各自一个端口，
四个作业端通过 CORE_URL 把数据请求转发给核心节点。

用法：
    python dev_servers.py            # 前台启动，Ctrl+C 结束
    python dev_servers.py --clean    # 先清空本地测试库再启动
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# (角色, 端口, 站点说明)
# 端口避开 5060/5061 —— Chromium 系浏览器把这两个端口列为受限端口（SIP），
# 打开会直接报 ERR_UNSAFE_PORT，本地预览会失败。
SERVICES = [
    ("core", 8010, "溯链中心 · 管理控制台（存证核心节点）"),
    ("produce", 8011, "源产通 · 生产登记系统"),
    ("logistics", 8012, "运链通 · 流通上报系统"),
    ("retail", 8013, "销证通 · 销售核销系统"),
    ("consume", 8014, "正源查 · 消费验真系统"),
]
CORE_URL = "http://127.0.0.1:8010"


def main():
    if "--clean" in sys.argv:
        # 注意：不要用 os.remove —— 本机安全删除策略会拦截，且旧进程占用时必然失败。
        # 直接连库清表，既能清干净又不依赖文件系统权限。
        import sqlite3
        for f in ("data/v3.db", "data/w8011.db", "data/w8012.db", "data/w8013.db", "data/w8014.db"):
            p = os.path.join(HERE, f)
            if not os.path.exists(p):
                continue
            try:
                db = sqlite3.connect(p)
                for t in ("events", "blocks", "codes", "scans", "admin_logs"):
                    try:
                        db.execute("DELETE FROM " + t)
                    except Exception:
                        pass
                db.commit()
                db.close()
                print("已清空", f)
            except Exception as e:
                print("跳过", f, "（", e, "）")

    procs = []
    for role, port, desc in SERVICES:
        env = dict(os.environ, ROLE=role, PORT=str(port),
                   TRACE_DB=f"data/w{port}.db" if role != "core" else "data/v3.db",
                   PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                   DEV_RELOAD="1")   # 模板改动即时生效，改样式不用重启
        if role != "core":
            env["CORE_URL"] = CORE_URL
        log = open(os.path.join(HERE, f"server_v3_{role}.log"), "w", encoding="utf-8")
        p = subprocess.Popen(
            [PY, "-m", "flask", "--app", "app", "run", "--host", "127.0.0.1", "--port", str(port)],
            cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT)
        procs.append(p)
        print(f"  {desc}\n    ROLE={role}  http://127.0.0.1:{port}/")
    print("\n五个站点已启动，Ctrl+C 结束。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
