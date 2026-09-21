/* app.js —— 扫码与上报公共逻辑 */
(function () {
  const TC = {};
  let _scanner = null, _running = false;

  /** 标准追溯码：TC + 6 位日期 + 8 位随机串 + "-" + 4 位校验位 */
  TC.CODE_RE = /TC\d{6}[0-9A-HJKMNP-TV-Z]{8}-[0-9A-F]{4}/i;

  /** 从扫码结果里提取追溯码（兼容完整 URL、带参数链接与裸码）
   *
   *  提取顺序必须是「先定位码的位置，再退而求其次全文找」，否则会误伤：
   *  二维码内容是 https://tracechain-core.onrender.com/t/TC2609178TKCSBR9-4A8C 时，
   *  若直接用「字母数字{8,}-字母数字{4}」全文匹配，最先命中的是域名里的
   *  tracechain-core，取出来的就是 TRACECHAIN-CORE，后端必然判为伪造码。
   */
  TC.extractCode = function (text) {
    const s = String(text == null ? '' : text).trim();
    if (!s) return '';
    let m;
    // 1) 链接路径：/t/<码> 或旧的 /c/<码>
    m = s.match(/\/(?:t|c)\/([^\/?#\s]+)/i);
    if (m) return decodeURIComponent(m[1]).toUpperCase();
    // 2) 查询参数：?code=<码>
    m = s.match(/[?&]code=([^&#\s]+)/i);
    if (m) return decodeURIComponent(m[1]).toUpperCase();
    // 3) 全文里的标准追溯码（形状唯一，不会撞上域名）
    m = s.match(TC.CODE_RE);
    if (m) return m[0].toUpperCase();
    // 4) 兜底：整串当作码，去掉空白
    return s.replace(/\s+/g, '').toUpperCase();
  };

  TC.isScannerReady = function () {
    return typeof Html5Qrcode !== 'undefined';
  };

  /** 启动摄像头扫码，onResult(code) 回调 */
  TC.startScanner = function (onResult, onError) {
    const box = document.getElementById('reader');
    if (!TC.isScannerReady()) {
      alert('扫码组件未加载完成，请刷新页面或改用手动输入追溯码。');
      return;
    }
    if (_running) return;
    try {
      _scanner = new Html5Qrcode('reader');
      _scanner.start(
        { facingMode: 'environment' },
        { fps: 10, qrbox: { width: 240, height: 240 } },
        (decodedText) => {
          const code = TC.extractCode(decodedText);
          TC.stopScanner();
          onResult(code);
        },
        () => { /* 未识别到二维码，忽略 */ }
      );
      _running = true;
      if (box) box.style.display = 'block';
    } catch (e) {
      if (onError) onError(e);
      else alert('无法启动摄像头：' + (e && e.message ? e.message : e) + '\n请检查浏览器摄像头权限，或改用手动输入。');
    }
  };

  TC.stopScanner = function () {
    if (_scanner && _running) {
      try { _scanner.stop().then(() => _scanner.clear()); } catch (e) { }
      _running = false;
    }
    const box = document.getElementById('reader');
    if (box) box.style.display = 'none';
  };

  /** 等待上限要略大于服务端代理的最长耗时（70 秒），
   *  这样正常冷启动由服务端先给出 JSON 提示，而不是前端先超时。 */
  TC.API_TIMEOUT = 95000;

  /** 统一接口调用：**不抛异常，必然返回一个对象**
   *
   *  为什么不能直接 `await r.json()`：Render 免费层实例会休眠，冷启动超过
   *  边缘网关的等待上限时，网关返回的是一张 HTML 502 错误页。对它做
   *  JSON.parse 会抛 SyntaxError，而调用方（pick / submit）没有 catch，
   *  于是界面就永远停在「正在校验」，用户完全不知道发生了什么。
   *  这里把网络错误、超时、非 JSON 响应一律收敛成结构化的失败结果，
   *  每个调用方都能据此给出可见的提示。
   */
  TC.api = async function (url, body) {
    const ctl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), TC.API_TIMEOUT) : null;
    let r;
    try {
      r = await fetch(url, {
        method: body ? 'POST' : 'GET',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
        cache: 'no-store',
        signal: ctl ? ctl.signal : undefined
      });
    } catch (e) {
      if (timer) clearTimeout(timer);
      const timeout = e && e.name === 'AbortError';
      return {
        ok: false, net: true, reason: timeout
          ? '数据核心节点响应超时，请稍后重试。'
          : '无法连接服务器：' + ((e && e.message) ? e.message : e)
      };
    }
    if (timer) clearTimeout(timer);

    let text = '';
    try { text = await r.text(); } catch (e) { text = ''; }
    let j = null;
    try { j = JSON.parse(text); } catch (e) { j = null; }
    if (j && typeof j === 'object') return j;
    // 网关/反代的 HTML 错误页会走到这里
    return {
      ok: false, net: true, http: r.status,
      reason: '服务暂时不可用（HTTP ' + r.status + '）。请稍后重试。'
    };
  };

  /** 渲染一条存证记录为时间轴条目 */
  TC.renderEvent = function (ev) {
    const L = (window.TC_LABELS_BY_STAGE || {})[ev.stage] || window.TC_LABELS || {};
    const label = { produce: '生产端', logistics: '流通端', retail: '销售端', consume: '消费端' }[ev.stage] || ev.stage;
    let p = ev.payload;
    if (typeof p === 'string') { try { p = JSON.parse(p); } catch (e) { p = {}; } }
    p = p || {};
    const stars = [];
    ['occur_at', 'goods_state', 'action'].forEach(k => {
      if (p[k]) stars.push(`<span class="kv-star"><i>${TC.esc(L[k] || k)}</i><b>${TC.esc(p[k])}</b></span>`);
    });
    let kv = '';
    for (const k in p) {
      if (p[k] === '' || p[k] == null || k === 'action') continue;
      if (typeof p[k] === 'string' && p[k].indexOf('data:image/') === 0) {
        kv += `<dt>${TC.esc(L[k] || k)}</dt><dd>
          <a href="${p[k]}" target="_blank" rel="noopener"><img class="shot" src="${p[k]}" alt="现场照片"></a></dd>`;
        continue;
      }
      kv += `<dt>${TC.esc(L[k] || k)}</dt><dd>${TC.esc(p[k])}</dd>`;
    }
    const seq = String(ev.seq == null ? 0 : ev.seq).padStart(4, '0');
    return `<li class="${ev.stage}">
      <span class="dot"></span>
      <div class="tl-head"><span class="tl-title">${label} · ${TC.esc(ev.event_type)}</span>
      <span class="tl-time">${TC.esc(ev.timestamp)}</span></div>
      <div class="up-meta">
        <span>上报主体 ${TC.esc(ev.actor)}${ev.region ? ' · ' + TC.esc(ev.region) : ''}</span>
        <span class="mono">记录 #${seq}</span>
      </div>
      ${stars.length ? `<div class="kv-stars">${stars.join('')}</div>` : ''}
      ${kv ? `<dl class="kv">${kv}</dl>` : ''}
      <div class="up-meta" style="margin-top:9px">
        <span class="mono">数据摘要 ${TC.esc((ev.payload_hash || '').slice(0, 16))}…</span>
        <span class="mono">链哈希 ${TC.esc((ev.event_hash || '').slice(0, 16))}…</span>
      </div>
    </li>`;
  };

  TC.esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  };

  TC.renderTimeline = function (events) {
    if (!events || !events.length) return '<p class="small muted">暂无存证记录。</p>';
    // payload 在 API 返回里是对象，在模板渲染里也是对象
    return '<ul class="timeline">' + events.map(TC.renderEvent).join('') + '</ul>';
  };

  window.TC = TC;
})();
