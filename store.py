# -*- coding: utf-8 -*-
"""
store.py —— 数据层与业务流程规则

核心约束：
  1. 码与端口解耦。赋码只产生「空码」，不带任何商品信息；
     只有某个端口扫码并提交后，这个码上才会留下那一份记录。
  2. 次数规则：生产 / 销售 / 消费各 1 次，流通不限次。
  3. 顺序规则：流通、销售的前置条件是生产端已完成登记。
"""
import json
import os
import sqlite3
import threading
from typing import Optional

import chain
import schema

DB_PATH = os.environ.get("TRACE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tracechain.db"))
BLOCK_SIZE = int(os.environ.get("BLOCK_SIZE", "8"))

_lock = threading.RLock()

STAGE_RULES = schema.STAGES          # 兼容旧引用


def _conn() -> sqlite3.Connection:
    """开一个短连接。

    这里刻意不再执行 `PRAGMA journal_mode=WAL`：WAL 是库文件自身的持久属性，
    在 init_db() 里设置一次即可长期生效。之前每次连接都重设一遍，等于给每次
    写操作额外加了一次库调用，写存证时的卡顿有一部分来自这里。
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _lock, _conn() as c:
        c.execute("PRAGMA journal_mode=WAL")        # 持久属性，设置一次即可
        c.execute("PRAGMA synchronous=NORMAL")      # WAL 下兼顾安全与写入速度
        c.executescript("""
        CREATE TABLE IF NOT EXISTS codes (
            code          TEXT PRIMARY KEY,
            sig           TEXT NOT NULL,
            product_name  TEXT DEFAULT '',
            sku           TEXT DEFAULT '',
            batch         TEXT DEFAULT '',
            spec          TEXT DEFAULT '',
            producer      TEXT DEFAULT '',
            note          TEXT DEFAULT '',
            created_at    TEXT NOT NULL,
            status        TEXT DEFAULT 'blank'
        );
        CREATE TABLE IF NOT EXISTS events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            code          TEXT NOT NULL,
            seq           INTEGER NOT NULL,
            stage         TEXT NOT NULL,
            event_type    TEXT NOT NULL,
            actor         TEXT NOT NULL,
            org_role      TEXT DEFAULT '',
            region        TEXT DEFAULT '',
            payload_json  TEXT NOT NULL,
            payload_hash  TEXT NOT NULL,
            prev_hash     TEXT,
            event_hash    TEXT NOT NULL,
            signature     TEXT NOT NULL,
            timestamp     TEXT NOT NULL,
            block_id      INTEGER,
            voided        INTEGER DEFAULT 0,
            created_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS blocks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            height        INTEGER NOT NULL,
            prev_hash     TEXT,
            merkle_root   TEXT NOT NULL,
            event_count   INTEGER NOT NULL,
            timestamp     TEXT NOT NULL,
            block_hash    TEXT NOT NULL,
            nonce         INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS scans (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            code          TEXT NOT NULL,
            stage         TEXT NOT NULL,
            ip            TEXT DEFAULT '',
            ua            TEXT DEFAULT '',
            created_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            code          TEXT,
            action        TEXT NOT NULL,
            detail        TEXT DEFAULT '',
            operator      TEXT DEFAULT '',
            timestamp     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_code ON events(code);
        CREATE INDEX IF NOT EXISTS idx_scans_code  ON scans(code);
        """)
        # 老库补列
        cols = {r["name"] for r in c.execute("PRAGMA table_info(codes)")}
        for col, ddl in (("producer", "TEXT DEFAULT ''"), ("status", "TEXT DEFAULT 'blank'")):
            if col not in cols:
                c.execute(f"ALTER TABLE codes ADD COLUMN {col} {ddl}")


# ------------------------------------------------------------------ 追溯码
def create_codes(count: int, prefix: str = "TC", note: str = "") -> list:
    """赋码：只产生空码，不写任何商品信息。商品信息由各端口上报后填入。"""
    out = []
    with _lock, _conn() as c:
        for _ in range(count):
            code = chain.make_code(prefix)
            while c.execute("SELECT 1 FROM codes WHERE code=?", (code,)).fetchone():
                code = chain.make_code(prefix)
            c.execute("INSERT INTO codes(code, sig, note, created_at, status, product_name)"
                      " VALUES(?,?,?,?,'blank','')", (code, chain.sign(code), note, chain.now_iso()))
            out.append(code)
    return out


def get_code(code: str) -> Optional[dict]:
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM codes WHERE code=?", (str(code).strip().upper(),)).fetchone()
        return dict(r) if r else None


def list_codes(q: str = "", limit: int = 100, offset: int = 0) -> list:
    with _lock, _conn() as c:
        if q:
            like = f"%{q}%"
            rows = c.execute(
                "SELECT * FROM codes WHERE code LIKE ? OR product_name LIKE ? OR batch LIKE ?"
                " ORDER BY created_at DESC, code DESC LIMIT ? OFFSET ?",
                (like, like, like, limit, offset)).fetchall()
        else:
            rows = c.execute("SELECT * FROM codes ORDER BY created_at DESC, code DESC LIMIT ? OFFSET ?",
                             (limit, offset)).fetchall()
        return [dict(r) for r in rows]


def count_codes(q: str = "") -> int:
    with _lock, _conn() as c:
        if q:
            like = f"%{q}%"
            return c.execute("SELECT COUNT(*) n FROM codes WHERE code LIKE ? OR product_name LIKE ? OR batch LIKE ?",
                             (like, like, like)).fetchone()["n"]
        return c.execute("SELECT COUNT(*) n FROM codes").fetchone()["n"]


def update_code_note(code: str, note: str):
    with _lock, _conn() as c:
        c.execute("UPDATE codes SET note=? WHERE code=?", (note, code.upper()))


# ------------------------------------------------------------------ 扫码留痕
def log_scan(code: str, stage: str, ip: str = "", ua: str = ""):
    with _lock, _conn() as c:
        c.execute("INSERT INTO scans(code, stage, ip, ua, created_at) VALUES(?,?,?,?,?)",
                  (code.upper(), stage, ip, ua, chain.now_iso()))


def scan_stats(code: str) -> dict:
    with _lock, _conn() as c:
        rows = c.execute("SELECT stage, COUNT(*) n FROM scans WHERE code=? GROUP BY stage",
                         (code.upper(),)).fetchall()
        total = c.execute("SELECT COUNT(*) n FROM scans WHERE code=?", (code.upper(),)).fetchone()["n"]
    return {"total": total, "by_stage": {r["stage"]: r["n"] for r in rows}}


# ------------------------------------------------------------------ 事件写入
def stage_count(code: str, stage: str) -> int:
    with _lock, _conn() as c:
        return c.execute("SELECT COUNT(*) n FROM events WHERE code=? AND stage=? AND voided=0",
                         (code.upper(), stage)).fetchone()["n"]


def check_reportable(code: str, stage: str) -> dict:
    """判断某个码在本端能不能上报，返回 {ok, reason, warn}"""
    code = str(code).strip().upper()
    if stage not in schema.STAGES:
        return {"ok": False, "reason": "未知端口"}
    if not chain.is_valid_code(code):
        if chain.code_shape_ok(code):
            return {"ok": False, "forged": True,
                    "reason": "追溯码校验位不通过：该码不是本平台签发的码段，可能为伪造标签"}
        return {"ok": False, "forged": True, "unreadable": True,
                "reason": "没有识别到完整的追溯码，请重新对准二维码扫描，或核对手动输入的内容"}
    rec = get_code(code)
    if not rec:
        return {"ok": False, "reason": "该追溯码未在本平台赋码，无法写入记录", "unregistered": True}
    if rec["status"] == "cleared":
        return {"ok": False, "reason": "该码的记录已被管理员清除，当前不可上报"}

    used = stage_count(code, stage)
    limit = schema.STAGES[stage]["max"]
    if limit and used >= limit:
        return {"ok": False, "used": used, "limit": limit,
                "reason": f"该商品在本端已登记过（{used} 次），本端每个码只能登记 {limit} 次"}

    for pre in schema.PREREQ.get(stage, []):
        if stage_count(code, pre) == 0:
            return {"ok": False, "used": used, "limit": limit, "missing_stage": pre,
                    "reason": f"该商品尚无{schema.STAGES[pre]['label']}登记记录，"
                              f"请先由{schema.STAGES[pre]['owner']}在{schema.STAGES[pre]['label']}完成登记"}
    return {"ok": True, "used": used, "limit": limit}


def add_event(code: str, stage: str, actor: str, payload: dict,
              org_role: str = "", region: str = "") -> dict:
    """写入一条存证事件。返回 {ok, reason, event}"""
    code = str(code).strip().upper()
    gate = check_reportable(code, stage)
    if not gate["ok"]:
        return gate
    miss = schema.required_missing(stage, payload)
    if miss:
        return {"ok": False, "reason": "必填项缺失：" + "、".join(miss)}

    event_type = schema.default_event_type(stage, payload)

    with _lock:
        with _conn() as c:
            last = c.execute("SELECT event_hash, seq FROM events WHERE code=? ORDER BY seq DESC LIMIT 1",
                             (code,)).fetchone()
            prev_hash = last["event_hash"] if last else None
            seq = (last["seq"] + 1) if last else 1
            ts = chain.now_iso()
            ph = chain.payload_hash(payload)
            eh = chain.event_hash(code, stage, event_type, actor or "匿名主体", ts, ph, prev_hash)
            sig = chain.sign(eh)
            cur = c.execute(
                "INSERT INTO events(code, seq, stage, event_type, actor, org_role, region,"
                " payload_json, payload_hash, prev_hash, event_hash, signature, timestamp,"
                " block_id, voided, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,0,?)",
                (code, seq, stage, event_type, actor or "匿名主体", org_role, region,
                 json.dumps(payload, ensure_ascii=False), ph, prev_hash, eh, sig, ts, ts))
            eid = cur.lastrowid
            # 生产端登记后，把商品身份回填到码上（码此前是空的）
            if stage == "produce":
                c.execute("UPDATE codes SET product_name=?, batch=?, spec=?, producer=?, status='active'"
                          " WHERE code=?",
                          (payload.get("product_name", ""), payload.get("batch", ""),
                           payload.get("spec", ""), actor, code))
            # 封块检查与读回放在同一个连接里：写入路径上少开两次连接，
            # 每次操作都要走一遍 SQLite 的打开/提交，省下来就是实打实的响应时间。
            pending = c.execute("SELECT id, event_hash FROM events WHERE block_id IS NULL ORDER BY id").fetchall()
            if len(pending) >= BLOCK_SIZE:
                _seal(c, [dict(p) for p in pending])
            row = c.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        return {"ok": True, "event": dict(row)}


# ------------------------------------------------------------------ 区块打包
def seal_if_ready():
    with _lock, _conn() as c:
        pending = c.execute("SELECT id, event_hash FROM events WHERE block_id IS NULL ORDER BY id").fetchall()
        if len(pending) >= BLOCK_SIZE:
            _seal(c, [dict(p) for p in pending])


def _seal(c: sqlite3.Connection, pendings: list):
    last = c.execute("SELECT block_hash, height FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
    prev_hash = last["block_hash"] if last else None
    height = (last["height"] + 1) if last else 1
    root = chain.merkle_root([p["event_hash"] for p in pendings])
    ts = chain.now_iso()
    bh = chain.block_hash(height, prev_hash, root, ts, 0)
    cur = c.execute("INSERT INTO blocks(height, prev_hash, merkle_root, event_count, timestamp, block_hash, nonce)"
                    " VALUES(?,?,?,?,?,?,0)", (height, prev_hash, root, len(pendings), ts, bh))
    c.executemany("UPDATE events SET block_id=? WHERE id=?", [(cur.lastrowid, p["id"]) for p in pendings])
    return cur.lastrowid


def seal_now() -> dict:
    with _lock, _conn() as c:
        pending = [dict(p) for p in c.execute("SELECT id, event_hash FROM events WHERE block_id IS NULL ORDER BY id")]
        if not pending:
            return {"sealed": 0}
        bid = _seal(c, pending)
        return {"sealed": len(pending), "block_id": bid}


def list_blocks(limit: int = 50) -> list:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM blocks ORDER BY height DESC LIMIT ?", (limit,))]


def get_block(height: int) -> Optional[dict]:
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM blocks WHERE height=?", (height,)).fetchone()
        if not r:
            return None
        b = dict(r)
        b["events"] = [dict(x) for x in c.execute(
            "SELECT id, code, seq, stage, event_type, event_hash FROM events WHERE block_id=? ORDER BY id",
            (b["id"],))]
        return b


# ------------------------------------------------------------------ 查询与校验
def get_events(code: str, include_voided: bool = False) -> list:
    code = code.upper()
    with _lock, _conn() as c:
        sql = "SELECT * FROM events WHERE code=?" + ("" if include_voided else " AND voided=0") + " ORDER BY seq"
        rows = [dict(r) for r in c.execute(sql, (code,))]
    for r in rows:
        try:
            r["payload"] = json.loads(r["payload_json"])
        except Exception:
            r["payload"] = {}
    return rows


def code_progress(code: str) -> list:
    """该码四个环节的填充进度：哪些已登记、哪些还是空白"""
    code = code.upper()
    out = []
    for k in schema.ORDER:
        n = stage_count(code, k)
        out.append({"stage": k, "label": schema.STAGES[k]["label"], "icon": schema.STAGES[k]["icon"],
                    "done": n > 0, "count": n, "url": schema.STAGES[k]["url"]})
    return out


def verify_code_chain(code: str) -> dict:
    """重算该码事件链：哈希链连续性 + 签名有效性 + 数据摘要是否被动过。"""
    rows = get_events(code, include_voided=True)
    if not rows:
        return {"valid": True, "checked": 0, "issues": [], "message": "暂无存证记录"}
    issues = []
    prev = None
    for r in rows:
        expect_ph = chain.payload_hash(json.loads(r["payload_json"]))
        if expect_ph != r["payload_hash"]:
            issues.append(f"#{r['seq']} 业务数据摘要不匹配，明细内容疑似被篡改")
        eh = chain.event_hash(r["code"], r["stage"], r["event_type"], r["actor"],
                              r["timestamp"], expect_ph, prev)
        if r["voided"]:
            prev = r["event_hash"]
            continue
        if r["prev_hash"] != prev:
            issues.append(f"#{r['seq']} 前序哈希断裂，链路被插入或删除过记录")
        if eh != r["event_hash"]:
            issues.append(f"#{r['seq']} 事件哈希重算不一致，记录已被改动")
        if not chain.verify_signature(r["event_hash"], r["signature"]):
            issues.append(f"#{r['seq']} 签名校验失败，来源不可信")
        prev = r["event_hash"]
    return {"valid": not issues, "checked": len(rows), "issues": issues,
            "message": "链路完整，未被篡改" if not issues else "检测到异常"}


def verify_blocks() -> dict:
    issues = []
    with _lock, _conn() as c:
        blocks = [dict(r) for r in c.execute("SELECT * FROM blocks ORDER BY height")]
    prev = None
    for b in blocks:
        if b["prev_hash"] != prev:
            issues.append(f"区块 #{b['height']} 前序区块哈希不匹配")
        with _lock, _conn() as c:
            evs = [dict(r) for r in c.execute("SELECT event_hash FROM events WHERE block_id=? ORDER BY id", (b["id"],))]
        root = chain.merkle_root([e["event_hash"] for e in evs])
        if evs and root != b["merkle_root"]:
            issues.append(f"区块 #{b['height']} Merkle 根不一致")
        if evs and chain.block_hash(b["height"], prev, root, b["timestamp"], b["nonce"]) != b["block_hash"]:
            issues.append(f"区块 #{b['height']} 区块哈希不一致")
        prev = b["block_hash"]
    return {"valid": not issues, "blocks": len(blocks), "issues": issues}


# ------------------------------------------------------------------ 管理操作
def clear_code(code: str, mode: str = "soft", operator: str = "") -> dict:
    """管理员清除某追溯码的全部信息记录。
      soft：标记作废（保留存证痕迹，可审计）
      hard：物理删除明细。若这些明细已经封进区块，连同其后所有区块一并撤销，
            把区块里的其余事件退回「待封块」状态，保证全局账本始终校验得通。
    """
    code = str(code).strip().upper()
    if not get_code(code):
        return {"ok": False, "reason": "追溯码不存在"}
    with _lock, _conn() as c:
        n = c.execute("SELECT COUNT(*) n FROM events WHERE code=?", (code,)).fetchone()["n"]
        rolled_back = 0
        if mode == "hard":
            # 先记下这批事件落在哪些区块：删掉明细后这些区块的 Merkle 根会重算不一致，
            # 只删明细不动区块会让全局账本永久校验失败，所以必须连同其后区块一起撤销。
            hit = c.execute("SELECT DISTINCT b.height FROM events e JOIN blocks b ON b.id=e.block_id"
                            " WHERE e.code=? AND e.block_id IS NOT NULL", (code,)).fetchall()
            c.execute("DELETE FROM events WHERE code=?", (code,))
            c.execute("DELETE FROM scans WHERE code=?", (code,))
            c.execute("DELETE FROM codes WHERE code=?", (code,))
            if hit:
                h0 = min(r["height"] for r in hit)
                ids = [r["id"] for r in c.execute("SELECT id FROM blocks WHERE height>=?", (h0,))]
                if ids:
                    q = ",".join("?" * len(ids))
                    c.execute(f"UPDATE events SET block_id=NULL WHERE block_id IN ({q})", ids)
                    rolled_back = c.execute(f"DELETE FROM blocks WHERE id IN ({q})", ids).rowcount
        else:
            c.execute("UPDATE events SET voided=1 WHERE code=?", (code,))
            c.execute("UPDATE codes SET status='cleared', product_name='', batch='', spec='', producer=''"
                      " WHERE code=?", (code,))
        detail = f"清除 {n} 条记录"
        if rolled_back:
            detail += f"；撤销 {rolled_back} 个受影响区块，其中其余事件已退回待封块"
        c.execute("INSERT INTO admin_logs(code, action, detail, operator, timestamp) VALUES(?,?,?,?,?)",
                  (code, f"clear:{mode}", detail, operator, chain.now_iso()))
    return {"ok": True, "removed": n, "mode": mode, "rolled_back_blocks": rolled_back}


def list_admin_logs(limit: int = 50) -> list:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT ?", (limit,))]


def stats() -> dict:
    with _lock, _conn() as c:
        codes = c.execute("SELECT COUNT(*) n FROM codes").fetchone()["n"]
        blanks = c.execute("SELECT COUNT(*) n FROM codes WHERE status='blank'").fetchone()["n"]
        events = c.execute("SELECT COUNT(*) n FROM events WHERE voided=0").fetchone()["n"]
        blocks = c.execute("SELECT COUNT(*) n FROM blocks").fetchone()["n"]
        scans = c.execute("SELECT COUNT(*) n FROM scans").fetchone()["n"]
        pending = c.execute("SELECT COUNT(*) n FROM events WHERE block_id IS NULL").fetchone()["n"]
        by_stage = {r["stage"]: r["n"] for r in
                    c.execute("SELECT stage, COUNT(*) n FROM events WHERE voided=0 GROUP BY stage")}
    return {"codes": codes, "blanks": blanks, "filled": codes - blanks, "events": events,
            "blocks": blocks, "scans": scans, "pending": pending, "by_stage": by_stage}


def import_backup(data: dict) -> dict:
    """从备份恢复全部数据。用于免费实例重启导致本地库丢失后的恢复。"""
    n = {}
    with _lock, _conn() as c:
        for t in ("events", "blocks", "scans", "codes", "admin_logs"):
            c.execute(f"DELETE FROM {t}")
        for r in data.get("codes", []):
            c.execute("INSERT OR REPLACE INTO codes(code, sig, product_name, sku, batch, spec, producer,"
                      " note, created_at, status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (r["code"], r["sig"], r.get("product_name", ""), r.get("sku", ""), r.get("batch", ""),
                       r.get("spec", ""), r.get("producer", ""), r.get("note", ""),
                       r.get("created_at", chain.now_iso()), r.get("status", "blank")))
        n["codes"] = len(data.get("codes", []))
        for r in data.get("blocks", []):
            c.execute("INSERT OR REPLACE INTO blocks(id, height, prev_hash, merkle_root, event_count, timestamp,"
                      " block_hash, nonce) VALUES(?,?,?,?,?,?,?,?)",
                      (r["id"], r["height"], r.get("prev_hash"), r["merkle_root"], r["event_count"],
                       r["timestamp"], r["block_hash"], r.get("nonce", 0)))
        n["blocks"] = len(data.get("blocks", []))
        for r in data.get("events", []):
            c.execute("INSERT OR REPLACE INTO events(id, code, seq, stage, event_type, actor, org_role, region,"
                      " payload_json, payload_hash, prev_hash, event_hash, signature, timestamp, block_id, voided,"
                      " created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (r["id"], r["code"], r["seq"], r["stage"], r["event_type"], r["actor"], r.get("org_role", ""),
                       r.get("region", ""), r["payload_json"], r["payload_hash"], r.get("prev_hash"), r["event_hash"],
                       r["signature"], r["timestamp"], r.get("block_id"), r.get("voided", 0), r.get("created_at", "")))
        n["events"] = len(data.get("events", []))
        for r in data.get("scans", []):
            c.execute("INSERT OR REPLACE INTO scans(id, code, stage, ip, ua, created_at) VALUES(?,?,?,?,?,?)",
                      (r["id"], r["code"], r["stage"], r.get("ip", ""), r.get("ua", ""), r["created_at"]))
        n["scans"] = len(data.get("scans", []))
        for r in data.get("admin_logs", []):
            c.execute("INSERT OR REPLACE INTO admin_logs(id, code, action, detail, operator, timestamp)"
                      " VALUES(?,?,?,?,?,?)",
                      (r["id"], r.get("code"), r["action"], r.get("detail", ""), r.get("operator", ""), r["timestamp"]))
        n["admin_logs"] = len(data.get("admin_logs", []))
    return n


def is_empty() -> bool:
    """库里是否一条数据都没有。用于判断是否需要从备份恢复（不要把空库推上去覆盖好数据）。"""
    with _lock, _conn() as c:
        n = c.execute("SELECT COUNT(*) n FROM codes").fetchone()["n"]
        if n:
            return False
        return c.execute("SELECT COUNT(*) n FROM events").fetchone()["n"] == 0


def export_backup() -> dict:
    with _lock, _conn() as c:
        return {
            "codes": [dict(r) for r in c.execute("SELECT * FROM codes")],
            "events": [dict(r) for r in c.execute("SELECT * FROM events")],
            "blocks": [dict(r) for r in c.execute("SELECT * FROM blocks")],
            "scans": [dict(r) for r in c.execute("SELECT * FROM scans")],
            "admin_logs": [dict(r) for r in c.execute("SELECT * FROM admin_logs")],
        }
