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
SERVICES = [
    ("core", 5059, "溯链中心 · 管理控制台（存证核心节点）"),
    ("produce", 5060, "源产通 · 生产登记系统"),
    ("logistics", 5061, "运链通 · 流通上报系统"),
    ("retail", 5062, "销证通 · 销售核销系统"),
    ("consume", 5063, "正源查 · 消费验真系统"),
]
CORE_URL = "http://127.0.0.1:5059"


def main():
    if "--clean" in sys.argv:
        for f in ("data/v3.db", "data/w1.db", "data/w2.db", "data/w3.db", "data/w4.db"):
            p = os.path.join(HERE, f)
            if os.path.exists(p):
                try:
                    os.remove(p)
                    print("清空", f)
                except OSError as e:      # 文件被占用等情况直接跳过
                    print("跳过", f, "（", e, "）")

    procs = []
    for role, port, desc in SERVICES:
        env = dict(os.environ, ROLE=role, PORT=str(port),
                   TRACE_DB=f"data/w{port}.db" if role != "core" else "data/v3.db",
                   PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
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
