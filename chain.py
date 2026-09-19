# -*- coding: utf-8 -*-
"""
chain.py —— 链式存证内核

职责：为每一次扫码上报生成不可篡改的存证记录。
三条防线：
  1. 事件哈希链：每个事件的哈希由「前一个事件哈希 + 本次数据」共同决定，
     改动任意一条历史记录，其后所有哈希全部断裂。
  2. 数字签名：服务端密钥对事件哈希做 HMAC，防止凭空构造事件。
  3. 区块锚定：若干事件打包成区块，计算 Merkle 根并串联区块哈希，
     形成全局账本，可对外提供整体性证明。

算法默认 SHA-256；设置环境变量 HASH_ALGO=SM3 可切换为国密 SM3（GM/T 0004）。
"""
import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import List, Optional

HASH_ALGO = os.environ.get("HASH_ALGO", "SHA256").upper()
SIGN_KEY = os.environ.get("TRACE_SIGN_KEY", "tracechain-default-sign-key-please-change")

GENESIS = "GENESIS"


# ---------------------------------------------------------------- 国密 SM3
_SM3_IV = [
    0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
    0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E,
]


def _rotl(x: int, n: int) -> int:
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _sm3_ff(x: int, y: int, z: int, j: int) -> int:
    if j < 16:
        return x ^ y ^ z
    return (x & y) | (x & z) | (y & z)


def _sm3_gg(x: int, y: int, z: int, j: int) -> int:
    if j < 16:
        return x ^ y ^ z
    return (x & y) | ((~x & 0xFFFFFFFF) & z)


def _sm3_p0(x: int) -> int:
    return x ^ _rotl(x, 9) ^ _rotl(x, 17)


def _sm3_p1(x: int) -> int:
    return x ^ _rotl(x, 15) ^ _rotl(x, 23)


def _sm3_cf(vi: List[int], block: bytes) -> List[int]:
    w = [int.from_bytes(block[i * 4:i * 4 + 4], "big") for i in range(16)]
    for j in range(16, 68):
        w.append(_sm3_p1(w[j - 16] ^ w[j - 9] ^ _rotl(w[j - 3], 15))
                 ^ _rotl(w[j - 13], 7) ^ w[j - 6])
    w1 = [w[j] ^ w[j + 4] for j in range(64)]

    a, b, c, d, e, f, g, h = vi
    for j in range(64):
        t = 0x79CC4519 if j < 16 else 0x7A879D8A
        ss1 = _rotl((_rotl(a, 12) + e + _rotl(t, j % 32)) & 0xFFFFFFFF, 7)
        ss2 = ss1 ^ _rotl(a, 12)
        tt1 = (_sm3_ff(a, b, c, j) + d + ss2 + w1[j]) & 0xFFFFFFFF
        tt2 = (_sm3_gg(e, f, g, j) + h + ss1 + w[j]) & 0xFFFFFFFF
        d = c
        c = _rotl(b, 9)
        b = a
        a = tt1
        h = g
        g = _rotl(f, 19)
        f = e
        e = _sm3_p0(tt2)
    return [a ^ vi[0], b ^ vi[1], c ^ vi[2], d ^ vi[3],
            e ^ vi[4], f ^ vi[5], g ^ vi[6], h ^ vi[7]]


def sm3_hex(data: bytes) -> str:
    """国密 SM3 摘要（GM/T 0004-2012），纯 Python 实现。"""
    msg = bytearray(data)
    bit_len = len(msg) * 8
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0x00)
    msg += bit_len.to_bytes(8, "big")

    v = list(_SM3_IV)
    for i in range(0, len(msg), 64):
        v = _sm3_cf(v, bytes(msg[i:i + 64]))
    return "".join("%08x" % x for x in v)


# ---------------------------------------------------------------- 摘要与签名
def digest(text: str) -> str:
    """统一摘要入口，按 HASH_ALGO 切换 SHA-256 / SM3。"""
    raw = text.encode("utf-8")
    if HASH_ALGO == "SM3":
        return sm3_hex(raw)
    return hashlib.sha256(raw).hexdigest()


def canon(obj) -> str:
    """规范化序列化：键排序、无多余空格，保证同一数据在任何机器得到同一哈希。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: dict) -> str:
    return digest(canon(payload))


def event_hash(code: str, stage: str, event_type: str, actor: str,
               timestamp: str, payload_hash_hex: str, prev_hash: Optional[str]) -> str:
    """事件哈希 = H(追溯码 | 环节 | 事件类型 | 责任主体 | 时间戳 | 数据摘要 | 前序哈希)"""
    parts = [code, stage, event_type, actor, timestamp, payload_hash_hex, prev_hash or GENESIS]
    return digest("|".join(parts))


def sign(data_hex: str) -> str:
    """对哈希值做密钥签名（HMAC）。生产环境应替换为 SM2 私钥签名或联盟链 SDK。"""
    return hmac.new(SIGN_KEY.encode("utf-8"), data_hex.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_signature(data_hex: str, signature: str) -> bool:
    return hmac.compare_digest(sign(data_hex), signature or "")


# ---------------------------------------------------------------- Merkle 树
def merkle_root(hashes: List[str]) -> str:
    if not hashes:
        return digest("EMPTY")
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])  # 奇数节点复制最后一个补齐
        level = [digest(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(hashes: List[str], index: int) -> List[dict]:
    """生成 Merkle 证明路径，便于对外提供「某条记录确实在某个区块内」的证据。"""
    if index < 0 or index >= len(hashes):
        return []
    proof = []
    level = list(hashes)
    idx = index
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        if idx % 2 == 0:
            sibling = level[idx + 1]
        else:
            sibling = level[idx - 1]
        proof.append({"position": "right" if idx % 2 == 0 else "left", "hash": sibling})
        level = [digest(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
        idx //= 2
    return proof


def block_hash(height: int, prev_block_hash: Optional[str], root: str,
               timestamp: str, nonce: int) -> str:
    parts = [str(height), prev_block_hash or GENESIS, root, str(timestamp), str(nonce)]
    return digest("|".join(parts))


# ---------------------------------------------------------------- 追溯码
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford Base32，剔除易混字符


def _random_str(n: int) -> str:
    return "".join(_ALPHABET[b % 32] for b in os.urandom(n))


def make_code(prefix: str = "TC") -> str:
    """生成追溯码：前缀 + 日期 + 随机串 + 校验位。校验位由密钥派生，无法凭空伪造。"""
    import datetime
    day = datetime.datetime.now().strftime("%y%m%d")
    core = f"{prefix}{day}{_random_str(8)}"
    return f"{core}-{code_checksum(core)}"


def code_checksum(core: str) -> str:
    return hmac.new(SIGN_KEY.encode("utf-8"), core.encode("utf-8"),
                    hashlib.sha256).hexdigest()[:4].upper()


def is_valid_code(code: str) -> bool:
    """校验追溯码格式与校验位。通过不代表数据库里存在，仅代表码本身未被伪造。"""
    code = (code or "").strip().upper()
    if "-" not in code:
        return False
    core, _, checksum = code.rpartition("-")
    if len(core) < 8 or len(checksum) != 4:
        return False
    return hmac.compare_digest(code_checksum(core), checksum.upper())


# 标准追溯码形状：TC + 6 位日期 + 8 位随机串 + "-" + 4 位十六进制校验位
_CODE_RE = re.compile(r"TC\d{6}[0-9A-HJKMNP-TV-Z]{8}-[0-9A-F]{4}", re.I)


def code_shape_ok(code: str) -> bool:
    """这串内容看起来是不是「一个完整的追溯码」。
    用来区分两种失败：根本没扫到码（识别问题） / 码段不合规（疑似伪造）。"""
    return bool(_CODE_RE.fullmatch((code or "").strip()))
_PATH_RE = re.compile(r"/(?:t|c)/([^/?#\s]+)", re.I)
_QUERY_RE = re.compile(r"[?&]code=([^&#\s]+)", re.I)


def extract_code(text: str) -> str:
    """从扫码结果 / 粘贴的链接 / 裸码中提取追溯码。

    必须按「先定位码的位置，再全文搜索」的顺序，否则会误伤：
    内容是 https://tracechain-core.onrender.com/t/TC2609178TKCSBR9-4A8C 时，
    若直接全文匹配「字母数字{8,}-字母数字{4}」，最先命中的是域名里的
    tracechain-core，提取出来的是 TRACECHAIN-CORE，校验位必然不通过，
    会被误判成伪造码。
    """
    s = (text or "").strip()
    if not s:
        return ""
    m = _PATH_RE.search(s)
    if m:
        return urllib.parse.unquote(m.group(1)).strip().upper()
    m = _QUERY_RE.search(s)
    if m:
        return urllib.parse.unquote(m.group(1)).strip().upper()
    m = _CODE_RE.search(s)
    if m:
        return m.group(0).upper()
    return re.sub(r"\s+", "", s).upper()


# ---------------------------------------------------------------- 时间基准
# 服务器通常跑在 UTC，而业务时间必须是中国标准时间，否则「登记时间」会整整差 8 小时。
# 用固定偏移而不是时区名：中国不实行夏令时，固定 +8 即中国标准时间，且不依赖 tzdata
# （Windows 与精简容器里往往没有 tzdata，zoneinfo 会抛 ZoneInfoNotFoundError）。
UTC_OFFSET = int(os.environ.get("TC_UTC_OFFSET", "8"))
TZ = timezone(timedelta(hours=UTC_OFFSET))


def now_dt() -> datetime:
    """当前的中国标准时间（带时区）"""
    return datetime.now(TZ)


def now_iso() -> str:
    return now_dt().strftime("%Y-%m-%dT%H:%M:%S")


def now_minute() -> str:
    """各端「登记 / 作业 / 销售 / 验收时间」用的精度：到分钟，与 datetime-local 一致"""
    return now_dt().strftime("%Y-%m-%dT%H:%M")


if __name__ == "__main__":
    # 自检：SM3 标准测试向量
    assert sm3_hex(b"abc") == "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0", \
        "SM3 实现有误"
    assert len(digest("hello")) in (64,)
    c = make_code()
    assert is_valid_code(c), c
    assert not is_valid_code(c[:-1] + "X")

    # 自检：从各种扫码结果里取码，都不能取成域名
    for raw in (c,
                f"https://tracechain-core.onrender.com/t/{c}",
                f"https://tracechain-core.onrender.com/c/{c}",
                f"https://tracechain-retail.onrender.com/?code={c}",
                f"  {c.lower()}  "):
        got = extract_code(raw)
        assert got == c, f"取码错误：{raw!r} -> {got!r}（应为 {c!r}）"
        assert is_valid_code(got), got
    assert extract_code("") == ""
    print("chain.py 自检通过：SM3 向量正确，追溯码校验位有效，URL 取码不会误取域名")
