# -*- coding: utf-8 -*-
"""按端口结束本地站点进程（Windows）。用法：python kill_port.py 8010"""
import re
import subprocess
import sys

port = sys.argv[1] if len(sys.argv) > 1 else "8010"
out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                     encoding="utf-8", errors="ignore").stdout
pids = set()
for line in out.splitlines():
    if "LISTENING" not in line:
        continue
    if not re.search(r":%s\s" % re.escape(port), line):
        continue
    parts = line.split()
    if parts:
        pids.add(parts[-1])

if not pids:
    print("端口 %s 没有监听进程" % port)
    sys.exit(0)

for pid in pids:
    r = subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True,
                       text=True, encoding="utf-8", errors="ignore")
    print("kill %s -> rc=%s %s%s" % (pid, r.returncode, r.stdout or "", r.stderr or ""))
