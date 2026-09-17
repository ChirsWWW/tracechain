# -*- coding: utf-8 -*-
"""
schema.py —— 五个独立站点的定义、字段与业务流程规则

设计原则：
  1. 五个端口是五个彼此独立的站点，各自一个网址，站点之间没有任何导航关联。
  2. 二维码独立于端口。赋码只产生一个空码，不带任何商品信息。
  3. 各站点扫码后，先看到上游环节已经登记的信息，再填自己那一份内容。
  4. 有先后依赖：流通、销售的前置条件是生产端已完成登记。
"""

# ---------------------------------------------------------------------------
# 五个独立站点：站名、图标、主色、独立域名
# accent 系列会覆盖前端 CSS 变量，使各站视觉上完全独立
# ---------------------------------------------------------------------------
SITES = {
    "produce": {
        "role": "produce",
        "name": "源产通",
        "sub": "生产登记系统",
        "icon": "🏭",
        "accent": "#1d4ed8", "accent2": "#3b82f6", "accent_soft": "#e8effe",
        "host": "tracechain-produce.onrender.com",
        "slogan": "商品出厂登记 · 一物一码源头录入",
        "title": "源产通 · 生产登记系统",
    },
    "logistics": {
        "role": "logistics",
        "name": "运链通",
        "sub": "流通上报系统",
        "icon": "🚚",
        "accent": "#0f766e", "accent2": "#14b8a6", "accent_soft": "#e3f6f3",
        "host": "tracechain-logistics.onrender.com",
        "slogan": "出入库 · 运输 · 签收 · 全程留痕",
        "title": "运链通 · 流通上报系统",
    },
    "retail": {
        "role": "retail",
        "name": "销证通",
        "sub": "销售核销系统",
        "icon": "🏪",
        "accent": "#b45309", "accent2": "#f59e0b", "accent_soft": "#fdf1e0",
        "host": "tracechain-retail.onrender.com",
        "slogan": "门店核销 · 窜货稽查 · 交易存证",
        "title": "销证通 · 销售核销系统",
    },
    "consume": {
        "role": "consume",
        "name": "正源查",
        "sub": "消费验真系统",
        "icon": "🛡️",
        "accent": "#6d28d9", "accent2": "#8b5cf6", "accent_soft": "#f1ebfd",
        "host": "tracechain-consume.onrender.com",
        "slogan": "扫码验真 · 反馈直达厂家",
        "title": "正源查 · 消费验真系统",
    },
    "core": {
        "role": "core",
        "name": "溯链中心",
        "sub": "管理控制台",
        "icon": "🔗",
        "accent": "#111827", "accent2": "#374151", "accent_soft": "#eceff3",
        "host": "tracechain-core.onrender.com",
        "slogan": "赋码 · 存证核心 · 全网数据",
        "title": "溯链中心 · 管理控制台",
    },
    # 公众验真页（二维码落地页），运行在核心节点上，独立于五个作业端
    "public": {
        "role": "public",
        "name": "溯链验真",
        "sub": "公众查询",
        "icon": "🔍",
        "accent": "#0f766e", "accent2": "#14b8a6", "accent_soft": "#e3f6f3",
        "host": "tracechain-core.onrender.com",
        "slogan": "一物一码 · 全链路可查",
        "title": "溯链验真 · 公众查询",
    },
}

# ---------------------------------------------------------------------------
# 端口元信息（顺序即业务顺序）
# ---------------------------------------------------------------------------
STAGES = {
    "produce": {
        "key": "produce", "label": "生产端", "icon": "🏭",
        "url": "/",
        "owner": "生产厂家 / 代工厂",
        "brief": "商品出厂前录入身份信息。这是整条追溯链的起点，录完商品才有完整身份。",
        "max": 1,
        "repeat_hint": "每个码只能登记一次，登记后可联系管理员更正",
    },
    "logistics": {
        "key": "logistics", "label": "流通端", "icon": "🚚",
        "url": "/",
        "owner": "物流公司 / 仓储 / 配送站",
        "brief": "每一次出入库、转运、签收都追加一条记录。唯一允许反复扫码上报的站点。",
        "max": 0,
        "repeat_hint": "可反复扫码，每扫一次追加一条记录",
    },
    "retail": {
        "key": "retail", "label": "销售端", "icon": "🏪",
        "url": "/",
        "owner": "门店 / 经销商 / 电商客服",
        "brief": "商品售出时核销一次，核销后该码进入已售状态，可作为退换货与窜货稽查依据。",
        "max": 1,
        "repeat_hint": "每个码只能核销一次",
    },
    "consume": {
        "key": "consume", "label": "消费端", "icon": "📱",
        "url": "/",
        "owner": "终端消费者",
        "brief": "消费者扫码验真，可补充购买渠道与使用反馈。若提示已被扫过，请警惕复制码。",
        "max": 1,
        "repeat_hint": "每个码只能登记一次，重复扫码会触发防伪提醒",
    },
}

ORDER = ["produce", "logistics", "retail", "consume"]

# 环节依赖：某站上报前，必须已经在这些环节留下记录
PREREQ = {
    "produce": [],
    "logistics": ["produce"],
    "retail": ["produce"],
    "consume": [],
}

# ---------------------------------------------------------------------------
# 各站字段：key 用于存证 payload，label 用于界面与验真页展示
# 服务端只接受本端字段，站外字段一律丢弃
# ---------------------------------------------------------------------------
FIELDS = {
    "produce": [
        {"k": "product_name", "label": "产品名称", "type": "text", "required": True,
         "ph": "如：有机山茶油", "group": "商品身份"},
        {"k": "spec", "label": "规格型号", "type": "text", "ph": "如：500ml/瓶", "group": "商品身份"},
        {"k": "net_weight", "label": "净含量 / 重量", "type": "text", "ph": "如：500ml 或 2.5kg", "group": "商品身份"},
        {"k": "batch", "label": "生产批次", "type": "text", "required": True,
         "ph": "如：B20260917", "group": "商品身份"},
        {"k": "occur_at", "label": "登记时间", "type": "datetime-local", "required": True,
         "server_time": True, "group": "时间与状态"},
        {"k": "goods_state", "label": "产品状态", "type": "select", "required": True,
         "options": ["待检", "检验中", "合格待售", "冻结待处理"], "group": "时间与状态"},
        {"k": "produce_date", "label": "生产日期", "type": "date", "required": True, "group": "时间与状态"},
        {"k": "shelf_life", "label": "保质期", "type": "text", "ph": "如：18 个月", "group": "时间与状态"},
        {"k": "standard", "label": "执行标准", "type": "text", "ph": "如：GB/T 1534", "group": "质量与合规"},
        {"k": "license", "label": "生产许可证号", "type": "text", "ph": "如：SC10236070200001", "group": "质量与合规"},
        {"k": "origin", "label": "产地", "type": "text", "ph": "如：江西 宜春", "group": "质量与合规"},
        {"k": "line", "label": "生产线 / 车间", "type": "text", "ph": "如：一号灌装线", "group": "质量与合规"},
        {"k": "qc_result", "label": "出厂检验结论", "type": "select",
         "options": ["合格", "不合格", "待检"], "group": "质量与合规"},
        {"k": "remark", "label": "备注", "type": "textarea", "group": "其他"},
        {"k": "photo", "label": "产品 / 出厂照片", "type": "image", "group": "图片凭证",
         "ph": "可选。拍下产品、标签或出厂检验单，随记录一起上链存证"},
    ],
    "logistics": [
        {"k": "action", "label": "本次操作", "type": "select", "required": True,
         "options": ["出库", "装车", "干线运输", "中转分拣", "到货入库", "签收", "退返"],
         "group": "本次作业"},
        {"k": "occur_at", "label": "作业时间", "type": "datetime-local", "required": True,
         "server_time": True, "group": "本次作业"},
        {"k": "goods_state", "label": "货物状态", "type": "select", "required": True,
         "options": ["完好", "外包装破损", "内包装受损", "温控异常", "受潮", "疑似被拆封"],
         "group": "本次作业"},
        {"k": "carrier", "label": "承运商", "type": "text", "required": True,
         "ph": "如：顺丰冷运", "group": "本次作业"},
        {"k": "waybill", "label": "运单号", "type": "text", "ph": "如：SF7712001234", "group": "本次作业"},
        {"k": "plate", "label": "车牌号", "type": "text", "ph": "如：赣A·88888", "group": "本次作业"},
        {"k": "driver", "label": "司机 / 交接人", "type": "text", "ph": "如：李师傅", "group": "本次作业"},
        {"k": "from_loc", "label": "起运地", "type": "text", "ph": "如：江西宜春工厂仓", "group": "路线与环境"},
        {"k": "to_loc", "label": "目的地", "type": "text", "ph": "如：重庆分拨中心", "group": "路线与环境"},
        {"k": "temp", "label": "运输环境", "type": "text", "ph": "如：2~8℃ / 45%RH", "group": "路线与环境"},
        {"k": "remark", "label": "异常与备注", "type": "textarea",
         "ph": "如：外箱轻微挤压，内包装完好", "group": "其他"},
        {"k": "photo", "label": "货物 / 作业照片", "type": "image", "group": "图片凭证",
         "ph": "可选。拍下外包装、装车或签收现场，破损与温控异常尤其建议拍照留证"},
    ],
    "retail": [
        {"k": "action", "label": "本次操作", "type": "select", "required": True,
         "options": ["上架", "销售核销", "退货入库"], "group": "本次作业"},
        {"k": "occur_at", "label": "销售时间", "type": "datetime-local", "required": True,
         "server_time": True, "group": "本次作业"},
        {"k": "goods_state", "label": "商品状态", "type": "select", "required": True,
         "options": ["正常在售", "临期", "破损折价", "已售出", "退货"], "group": "本次作业"},
        {"k": "shop", "label": "门店 / 渠道名称", "type": "text", "required": True,
         "ph": "如：解放碑旗舰店", "group": "本次作业"},
        {"k": "shop_code", "label": "门店编码", "type": "text", "ph": "如：CQ-0017", "group": "本次作业"},
        {"k": "cashier", "label": "经手人", "type": "text", "ph": "如：王小明", "group": "本次作业"},
        {"k": "order_no", "label": "订单号", "type": "text", "ph": "如：T20260917001", "group": "交易信息"},
        {"k": "price", "label": "成交价（元）", "type": "number", "ph": "如：128", "group": "交易信息"},
        {"k": "sold_at", "label": "销售日期", "type": "date", "group": "交易信息"},
        {"k": "remark", "label": "备注", "type": "textarea", "group": "其他"},
        {"k": "photo", "label": "货架 / 小票照片", "type": "image", "group": "图片凭证",
         "ph": "可选。拍下上架陈列或销售小票，作为核销时的实物佐证"},
    ],
    "consume": [
        {"k": "channel", "label": "购买渠道", "type": "select", "required": True,
         "options": ["线下门店", "电商平台", "直播带货", "社群团购", "其他"], "group": "购买信息"},
        {"k": "occur_at", "label": "验收时间", "type": "datetime-local", "required": True,
         "server_time": True, "group": "购买信息"},
        {"k": "goods_state", "label": "收货状态", "type": "select", "required": True,
         "options": ["完好", "破损", "变质", "与描述不符"], "group": "购买信息"},
        {"k": "city", "label": "购买城市", "type": "text", "ph": "如：重庆", "group": "购买信息"},
        {"k": "bought_at", "label": "购买日期", "type": "date", "group": "购买信息"},
        {"k": "rating", "label": "使用满意度", "type": "select",
         "options": ["很满意", "满意", "一般", "不满意"], "group": "使用反馈"},
        {"k": "feedback", "label": "反馈内容", "type": "textarea",
         "ph": "如：包装完好，油品清亮", "group": "使用反馈"},
        {"k": "photo", "label": "实物 / 包装照片", "type": "image", "group": "图片凭证",
         "ph": "可选。遇到破损、变质或与描述不符时，拍照最有说服力"},
    ],
}

# payload key -> 中文标签，供验真页与时间轴统一展示
# 注意：goods_state / occur_at 这类字段各端叫法不同，必须按环节取标签
LABELS_BY_STAGE = {stage: {f["k"]: f["label"] for f in fields} for stage, fields in FIELDS.items()}
LABELS = {k: v for stage in FIELDS for k, v in LABELS_BY_STAGE[stage].items()}
# 时间与状态类字段：放到上游记录卡片的显著位置
KEY_FIELDS = ["occur_at", "goods_state", "action"]
# 时间字段（展示时按环节排布）
TIME_FIELDS = {"occur_at", "produce_date", "sold_at", "bought_at"}

# 由服务端系统时间生成、前端只显示不可编辑的字段（前端锁不住，服务端必须强制覆盖）
SERVER_TIME_FIELDS = {stage: [f["k"] for f in fields if f.get("server_time")]
                      for stage, fields in FIELDS.items()}
# 图片凭证字段（存压缩后的 data URL，随 payload 一起进哈希，改图即断链）
IMAGE_FIELDS = {stage: [f["k"] for f in fields if f.get("type") == "image"]
                for stage, fields in FIELDS.items()}

# 单张图片上限（data URL 字符数）。前端已压缩，这里是服务端兜底，防止把库撑爆
IMAGE_MAX_CHARS = 400_000


def payload_keys(stage: str) -> set:
    return {f["k"] for f in FIELDS.get(stage, [])}


def image_error(value: str) -> str:
    """校验图片 data URL；合法返回空串，否则返回错误原因"""
    if not value:
        return ""
    if not value.startswith("data:image/"):
        return "图片格式不正确，请重新选择图片文件"
    if ";base64," not in value:
        return "图片编码不正确，请重新选择图片文件"
    if len(value) > IMAGE_MAX_CHARS:
        return f"图片过大（约 {len(value) // 1024} KB），请换一张或缩小后再上传"
    return ""


def apply_server_time(stage: str, payload: dict, now_fn) -> None:
    """把「必须等于系统时间」的字段强行覆盖为服务端当前时间。

    前端把输入框设为只读只是提示，任何人都可能绕过前端直接调接口，
    所以真正的约束必须落在服务端：无论客户端传了什么，一律以服务器时间为准。
    """
    for k in SERVER_TIME_FIELDS.get(stage, []):
        payload[k] = now_fn()


def sanitize_payload(stage: str, payload: dict) -> tuple:
    """只保留本端允许上报的字段，越权字段直接丢弃。

    返回 (清洗后的 payload, 被丢弃的字段名列表)。
    """
    allow = payload_keys(stage)
    out, dropped = {}, []
    for k, v in (payload or {}).items():
        v = v if isinstance(v, str) else str(v)
        if k not in allow:
            dropped.append(k)
            continue
        v = v.strip()
        if not v:
            continue
        out[k] = v
    return out, sorted(set(dropped))


# 事件类型（存证记录里保存的具体动作）
def default_event_type(stage: str, payload: dict) -> str:
    if stage == "produce":
        return "生产登记"
    if stage == "logistics":
        return payload.get("action") or "流通作业"
    if stage == "retail":
        return payload.get("action") or "销售核销"
    return "消费者验真"


def required_missing(stage: str, payload: dict) -> list:
    """返回缺失的必填项标签"""
    miss = []
    for f in FIELDS.get(stage, []):
        if f.get("required") and not str(payload.get(f["k"], "") or "").strip():
            miss.append(f["label"])
    return miss


def groups_of(stage: str) -> list:
    """按 group 分组返回字段，便于表单分区渲染"""
    out, cur = [], None
    for f in FIELDS.get(stage, []):
        g = f.get("group", "其他")
        if not cur or cur["name"] != g:
            cur = {"name": g, "fields": []}
            out.append(cur)
        cur["fields"].append(f)
    return out
