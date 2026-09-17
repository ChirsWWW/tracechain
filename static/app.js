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

  TC.api = async function (url, body) {
    const r = await fetch(url, {
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined
    });
    return await r.json();
  };

  /** 渲染一条存证记录为时间轴条目 */
  TC.renderEvent = function (ev) {
    const L = (window.TC_LABELS_BY_STAGE || {})[ev.stage] || window.TC_LABELS || {};
    const label = { produce: '生产', logistics: '流通', retail: '销售', consume: '消费' }[ev.stage] || ev.stage;
    let p = ev.payload;
    if (typeof p === 'string') { try { p = JSON.parse(p); } catch (e) { p = {}; } }
    p = p || {};
    const stars = [];
    ['occur_at', 'goods_state', 'action'].forEach(k => {
      if (p[k]) stars.push(`<span class="kv-star"><i>${TC.esc(L[k] || k)}</i>${TC.esc(p[k])}</span>`);
    });
    let kv = '';
    for (const k in p) {
      if (p[k] === '' || p[k] == null || k === 'action') continue;
      kv += `<dt>${TC.esc(L[k] || k)}</dt><dd>${TC.esc(p[k])}</dd>`;
    }
    return `<li class="${ev.stage}">
      <span class="dot"></span>
      <div class="tl-head"><span class="tl-title">${label} · ${TC.esc(ev.event_type)}</span>
      <span class="tl-time">${TC.esc(ev.timestamp)}</span></div>
      <div class="small muted">上报主体：${TC.esc(ev.actor)}${ev.region ? ' · ' + TC.esc(ev.region) : ''}</div>
      ${stars.length ? `<div class="kv-stars">${stars.join('')}</div>` : ''}
      ${kv ? `<dl class="kv">${kv}</dl>` : ''}
      <div class="small mono muted" style="margin-top:6px">指纹 ${(ev.payload_hash || '').slice(0, 16)}… ｜ 链哈希 ${(ev.event_hash || '').slice(0, 16)}…</div>
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
