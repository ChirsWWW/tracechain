# 溯链 TraceChain —— 五个独立站点的二维码溯源 + 链式存证系统

同一套代码，部署成**五个彼此独立的站点**，各自一个网址，站点之间没有任何导航关联。
二维码独立于所有站点：赋码只产生一张**空码**，哪个站点上报了，码上才有那一份内容。

## 五个站点（各自独立域名）

| 端口 | 站点 | 独立网址 | 使用方 | 每码上报次数 |
|---|---|---|---|---|
| 生产端 | 源产通 · 生产登记系统 | `https://tracechain-produce.onrender.com` | 生产厂家 / 代工厂 | 1 次 |
| 流通端 | 运链通 · 流通上报系统 | `https://tracechain-logistics.onrender.com` | 物流 / 仓储 / 配送站 | **不限次** |
| 销售端 | 销证通 · 销售核销系统 | `https://tracechain-retail.onrender.com` | 门店 / 经销商 | 1 次 |
| 消费端 | 正源查 · 消费验真系统 | `https://tracechain-consume.onrender.com` | 终端消费者 | 1 次 |
| 管理端 | 溯链中心 · 管理控制台 | `https://tracechain-core.onrender.com` | 平台管理员 | — |

**每个站点只呈现自己那一个端口**：在流通站访问 `/produce` 或 `/admin` 一律 404，
站点之间没有互相跳转的按钮。五站共享同一份数据（都写在核心节点上）。

公众验真页 `https://tracechain-core.onrender.com/t/<追溯码>` 独立于五个作业端，
是二维码唯一指向的地址。

## 每个站点的作业流程（三步）

1. **扫码选码** —— 摄像头扫码 / 手动输入追溯码（整条链接粘贴也可识别）
2. **先看上游环节已登记信息（只读）** —— 其他站点此前上报的记录、时间、状态一览
3. **再填本端允许上报的内容** —— 只出现本端字段，越权字段后端直接丢弃

每个端口都带两类字段：**作业时间** 与 **商品/货物状态**，另加各自的业务字段，
四端都可以**选填一张现场照片**（图片凭证）。

| 端口 | 时间字段（系统自动生成，不可修改） | 专属字段（除时间与状态外） |
|---|---|---|
| 生产端 | 登记时间 | 产品名称、规格、净含量、生产批次、生产日期、保质期、执行标准、生产许可证号、产地、生产线、出厂检验结论 |
| 流通端 | 作业时间 | 本次操作（出库/装车/干线运输/中转分拣/到货入库/签收/退返）、承运商、运单号、车牌号、司机、起运地、目的地、运输环境 |
| 销售端 | 销售时间 | 本次操作（上架/销售核销/退货入库）、门店、门店编码、经手人、订单号、成交价、销售日期 |
| 消费端 | 验收时间 | 购买渠道、购买城市、购买日期、使用满意度、反馈内容 |

**时间不可篡改**：登记 / 作业 / 销售 / 验收时间一律取服务端系统时间（中国标准时间，
`TC_UTC_OFFSET` 可调，默认 +8）。前端输入框只读只是提示，真正的约束在服务端——
`schema.apply_server_time()` 会在写入前强制覆盖，客户端传什么都不作数。

**图片凭证**：选填。浏览器端先压到长边 1000px 以内、约 200KB 以下，再以 data URL
随 payload 一起参与哈希——改图即等于改数据，存证链会立刻断。服务端另有 400KB 兜底校验。

业务规则：`流通、销售` 的前置条件是生产端已完成登记；`生产/销售/消费` 每码各 1 次，
`流通` 可反复上报（每次追加一条记录）。

## 存证内核

- **事件哈希链**：`event_hash = Hash(码|环节|事件类型|主体|时间戳|payload 摘要|prev_hash)`
- **非对称签名**：当前为服务端 HMAC 签名，接入联盟链后应替换为各主体私钥签名
- **Merkle 区块**：每 8 条事件打包一个区块，区块头串联 `prev_block_hash`
- **国密 SM3**：内置纯 Python 实现，`HASH_ALGO=SM3` 切换，已通过国标测试向量
- **追溯码防伪**：末 4 位为密钥派生校验位，不知密钥无法伪造合规码段

实测：直接改数据库里的字段（`合格待售` → `不合格（被人为篡改）`），
验真接口立刻报「业务数据摘要不匹配 + 事件哈希重算不一致」。

## 部署形态

五个站点是**同一份代码的五个部署实例**，靠环境变量区分角色：

```bash
# 核心节点（持有数据库、追溯码、存证链、公众验真页、管理控制台）
ROLE=core
PUBLIC_BASE=https://tracechain-core.onrender.com
ADMIN_PASSWORD=********
TRACE_DB=/tmp/tracechain.db
HASH_ALGO=SHA256

# 四个作业站点（不持有数据，/api/* 全部转发到核心节点）
ROLE=produce            # 或 logistics / retail / consume
CORE_URL=https://tracechain-core.onrender.com
```

作业站点是**无状态**的：它们自己的库里没有业务数据，重启不影响任何记录。

## 本地开发

```bash
# 一条命令起 1 个核心节点 + 4 个站点（端口 8010~8014）
# 注意：不要用 5060/5061 —— Chromium 系浏览器视为受限端口，打不开
python dev_servers.py --clean

# 端到端自测（本地）
python e2e_test.py

# 端到端自测（线上五站）
TC_CORE=https://tracechain-core.onrender.com \
TC_PRODUCE=https://tracechain-produce.onrender.com \
TC_LOGISTICS=https://tracechain-logistics.onrender.com \
TC_RETAIL=https://tracechain-retail.onrender.com \
TC_CONSUME=https://tracechain-consume.onrender.com \
python e2e_test.py
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/scan` | 扫码校验，返回码状态、**上游已登记记录**、本端可否上报、进度、服务端当前时间 |
| POST | `/api/event` | 本端上报，只接受本端字段，其余丢弃；时间字段强制取系统时间 |
| GET | `/api/now` | 服务端当前时间（中国标准时间），端口页据此与系统时间同步 |
| POST | `/api/codes/generate` | 赋码（管理口令） |
| GET | `/api/codes` | 码库（管理口令） |
| POST | `/api/admin/clear` | 清除该码全部记录，soft / hard（管理口令） |
| GET | `/api/chain/blocks` | 区块列表与全链校验 |
| POST | `/api/chain/seal` | 立即打包区块（管理口令） |
| GET | `/api/backup` `POST /api/restore` | 备份 / 恢复（管理口令） |
| POST | `/api/backup/push` | 立即把账本推到 GitHub 备份分支（管理口令） |
| GET | `/api/backup/status` | 冷备开关状态（管理口令） |
| GET | `/api/schema` | 端口、字段、站点定义，供第三方对接 |
| GET | `/healthz` | 健康检查 |

## 目录

```
app.py           多站点路由、站点隔离、到核心节点的 API 代理
schema.py        五个站点定义（站名/主色/独立域名）、各端字段、业务规则
store.py         SQLite 数据层、码状态、进度、区块、备份
persist.py       免费层冷备：账本快照推 GitHub 备份分支，重启自动拉回
templates/icons.html  线性图标集（统一 24×24 / stroke 1.6），界面不使用 emoji 当图标
chain.py         哈希链 / SM3 / HMAC 签名 / Merkle
dev_servers.py   本地一键起五站
e2e_test.py      端到端自测（本地 106 项 / 线上 104 项）
seed_demo.py     演示数据灌入
templates/       各站页面（base / port_base / 四个端 / admin / verify / chain / print）
static/          样式与前端逻辑（各站主色由站点配置注入 CSS 变量）
```

## 已知限制

1. **免费实例磁盘是临时的**，核心节点休眠或重新部署会清空数据库。
   `persist.py` 已做兜底：核心节点每 5 分钟把账本快照推到 GitHub 的 `tc-backup` 分支
   （需环境变量 `TC_GITHUB_TOKEN`），启动时若库为空自动拉回最近一份快照，
   所以普通休眠重启不会丢数据；管理台也保留「导出备份 / 导入备份」。
   正式投产仍建议挂载持久盘或换 PostgreSQL——冷备是兜底，不是数据库。
2. **当前是单服务端签名，不是真联盟链**。要达到联盟链标准需 ≥4 节点、≥3 家独立主体，
   并把签名环节换成各方私钥签名。现在这套的准确定位是「链下数据库 + 链式存证」。
3. **链只保证写入后未被篡改，不保证写入内容为真**。真品的码贴到假货上照样显示正品，
   需要物理防伪（易碎标、NFC 芯片）配合。
4. 免费实例休眠后首次访问约 5~50 秒冷启动。三件事一起兜它：
   - **核心节点自保温**：`ROLE=core` 每 4 分钟自敲一次 `/healthz`（仅在 07:00–22:59，
     避免吃光免费额度），使用时段内基本不会睡着（`TC_KEEPALIVE=0` 可关）。
   - **作业站探活 + 预热**：页面一打开就在后台持续轻敲核心节点（`poke_core`），
     用户填表那几十秒正好用来完成冷启动；转发前先探活，核心节点没醒就直接回
     **503 JSON**（而不是让请求干等 70 秒再被网关掐断）。
   - **前端自动轮询**：收到「正在唤醒」的应答后自动重试（扫码与上报各最多 14 次），
     用户不需要手动点任何按钮，醒来后自动接着走原来的流程。
   **网关的等待上限约 100 秒**：真转发时取「单次 55 秒 socket 超时 + 70 秒线程硬截止」
   （`TC_PROXY_TIMEOUT` / `TC_PROXY_DEADLINE` 可调）。重试反而有害：
   3 次 × 80 秒 ≈ 245 秒必然被网关掐断，用户拿到的是网关的 HTML 502。
   **光有 socket timeout 不够**：urllib 的 timeout 不覆盖 `getaddrinfo`，
   Render 的 DNS 偶发卡住时请求会无限期挂起，所以出网调用跑在线程池里，
   用 `future.result(timeout)` 做硬截止，到点就回 503 JSON。
   前端 `TC.api` 已做容错：非 JSON 响应、超时、断网一律收敛成 `{ok:false, net:true, reason}`，
   不会再无声地停在「正在校验」。
   作业端页面不请求 `/api/now`（系统时间由本地与页面注入的时间基准给出），
   少一次跨站往返，也就少一次「为一个时间戳去等冷启动」。
5. 内部服务调用（作业站 → 核心节点）显式绕过环境里的 `HTTP_PROXY`。
   `urllib` 默认会读取该变量，本机若有代理，内部请求会被送去第三方转发并拿回代理自己的 502。
