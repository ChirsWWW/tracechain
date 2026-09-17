/* port.js —— 各作业端共用交互
   流程：扫码选码 → ① 先看上游环节已登记信息（只读） → ② 再填本端允许上报的内容 → 存证 */
(function () {
  const TC = window.TC;
  const $ = id => document.getElementById(id);
  const STAGE_ORDER = ['produce', 'logistics', 'retail', 'consume'];
  const STAGE_CN = { produce: '生产端', logistics: '流通端', retail: '销售端', consume: '消费端' };

  function currentStage() { return document.body.dataset.stage; }
  function traceBase() { return (document.body.dataset.traceBase || '').replace(/\/$/, ''); }

  /** 记忆本机操作主体，避免每次重复填写 */
  function loadActor() {
    const s = currentStage();
    const box = $('actor-box'), form = $('actor-form');
    const saved = localStorage.getItem('tc_actor_' + s) || '';
    if (saved && box) {
      box.innerHTML = `<div class="who"><span class="who-label">本机操作主体</span>
          <b>${TC.esc(saved)}</b>
          <button class="btn ghost sm" type="button" onclick="PortUI.editActor()">修改</button></div>`;
      if (form) form.style.display = 'none';
    } else {
      if (form) form.style.display = '';
      if (box) box.innerHTML = '';
    }
    return saved;
  }

  function actor() {
    return ($('actor') && $('actor').value.trim()) || localStorage.getItem('tc_actor_' + currentStage()) || '';
  }

  async function saveActor() {
    const v = $('actor').value.trim();
    if (!v) { alert('请填写主体名称'); return; }
    localStorage.setItem('tc_actor_' + currentStage(), v);
    if ($('region')) localStorage.setItem('tc_region_' + currentStage(), $('region').value.trim());
    loadActor();
    toast('已记住本机操作主体：' + v, 'ok');
  }

  function toast(msg, kind) {
    const t = $('toast');
    if (!t) { alert(msg); return; }
    t.className = 'toast on ' + (kind || '');
    t.textContent = msg;
    clearTimeout(t._timer);
    t._timer = setTimeout(() => { t.className = 'toast'; }, 3200);
  }

  function payloadOf(ev) {
    let p = ev.payload;
    if (typeof p === 'string') { try { p = JSON.parse(p); } catch (e) { p = {}; } }
    return p || {};
  }

  function progressBar(progress) {
    if (!progress || !progress.length) return '';
    return '<div class="steps">' + progress.map(s =>
      `<span class="step ${s.done ? 'done' : ''}">${s.done ? '✅' : '○'} ${s.label}${s.count > 1 ? '×' + s.count : ''}</span>`
    ).join('<span class="step-sep">→</span>') + '</div>';
  }

  /** 一条上游记录 */
  function upstreamRow(ev, mine) {
    const p = payloadOf(ev);
    const L = (window.TC_LABELS_BY_STAGE || {})[ev.stage] || window.TC_LABELS || {};
    const stars = [];
    ['occur_at', 'goods_state', 'action'].forEach(k => {
      if (p[k]) stars.push(`<span class="kv-star"><i>${TC.esc(L[k] || k)}</i>${TC.esc(p[k])}</span>`);
    });
    let kv = '';
    for (const k in p) {
      if (p[k] === '' || p[k] == null) continue;
      if (k === 'action') continue;
      if (typeof p[k] === 'string' && p[k].indexOf('data:image/') === 0) {
        kv += `<dt>${TC.esc(L[k] || k)}</dt><dd>
          <a href="${p[k]}" target="_blank" rel="noopener"><img class="shot" src="${p[k]}" alt="现场照片"></a></dd>`;
        continue;
      }
      kv += `<dt>${TC.esc(L[k] || k)}</dt><dd>${TC.esc(p[k])}</dd>`;
    }
    return `<div class="up-row ${mine ? 'mine' : ''}">
      <div class="up-head">
        <span class="up-stage">${TC.esc(STAGE_CN[ev.stage] || ev.stage)} · ${TC.esc(ev.event_type || '')}</span>
        ${mine ? '<span class="badge ok">本端记录</span>' : '<span class="badge">上游记录</span>'}
      </div>
      ${stars.length ? `<div class="kv-stars">${stars.join('')}</div>` : ''}
      <div class="small muted">上报主体：${TC.esc(ev.actor)}${ev.region ? ' · ' + TC.esc(ev.region) : ''}
        ｜ 存证时间 ${TC.esc(ev.timestamp)}</div>
      ${kv ? `<dl class="kv">${kv}</dl>` : ''}
    </div>`;
  }

  /** 上游已登记信息（只读），放在本端表单之前 */
  function renderUpstream(r) {
    const box = $('upstream');
    const stage = currentStage();
    const events = r.upstream || [];
    const mine = events.filter(e => e.stage === stage);
    const ups = events.filter(e => e.stage !== stage);

    const prodEv = events.find(e => e.stage === 'produce');
    const prod = prodEv ? payloadOf(prodEv) : {};
    const arch = prod.product_name
      ? `<div class="arch">
           <div class="arch-name">${TC.esc(prod.product_name)}${prod.spec ? ' · ' + TC.esc(prod.spec) : ''}</div>
           <div class="arch-meta">
             ${prod.batch ? `<span>批次 ${TC.esc(prod.batch)}</span>` : ''}
             ${prod.produce_date ? `<span>生产日期 ${TC.esc(prod.produce_date)}</span>` : ''}
             ${prod.shelf_life ? `<span>保质期 ${TC.esc(prod.shelf_life)}</span>` : ''}
             ${prod.origin ? `<span>产地 ${TC.esc(prod.origin)}</span>` : ''}
             ${prod.license ? `<span>许可证 ${TC.esc(prod.license)}</span>` : ''}
           </div>
         </div>`
      : `<div class="alert warn" style="margin:0">
           <b>该码尚未完成生产登记</b><br>商品身份信息为空，需由生产厂家在生产端登记后，本端才能作业。
         </div>`;

    const upstreamBody = ups.length
      ? ups.map(e => upstreamRow(e, false)).join('')
      : `<div class="alert info" style="margin:0">本端之前还没有任何环节登记过信息。</div>`;
    const mineBody = mine.length
      ? `<h4 class="grp">本端已登记记录（${mine.length} 条）</h4>${mine.map(e => upstreamRow(e, true)).join('')}`
      : '';

    box.innerHTML = `<div class="card">
      <h3 style="margin-top:0">第 2 步 · 上游环节已登记信息</h3>
      <p class="small muted" style="margin-top:0">下面是其他端口此前已经写入该码的记录，只读，本端无法修改。</p>
      ${progressBar(r.progress)}
      ${arch}
      <h4 class="grp">上游环节记录（${ups.length} 条）</h4>
      ${upstreamBody}
      ${mineBody}
    </div>`;
  }

  /** 选中一个追溯码：校验 → 先展示上游信息与本端可作业性 */
  async function pick(raw) {
    const code = TC.extractCode(raw);
    if (!code) return;
    if ($('manual')) $('manual').value = code;
    const st = $('status');
    st.innerHTML = `<div class="alert info">正在校验 ${TC.esc(code)} …</div>`;
    $('form').style.display = 'none';
    $('result').innerHTML = '';
    $('upstream').innerHTML = '';

    const r = await TC.api('/api/scan', { code: code, stage: currentStage() });
    window.__code = code;

    if (!r.ok) {
      const head = r.unreadable ? '⚠ 没有识别到追溯码'
        : (r.forged ? '⚠ 追溯码校验未通过' : '无法识别');
      st.innerHTML = `<div class="alert bad"><b>${head}</b><br>${TC.esc(r.reason)}</div>`;
      return;
    }

    st.innerHTML = `
      <div class="alert ok"><b>追溯码有效</b>　<span class="code-chip">${TC.esc(code)}</span></div>
      <p class="small muted" style="margin:0">
        本端已登记 ${r.gate.used || 0} 次${r.gate.limit ? '（上限 ' + r.gate.limit + ' 次）' : '（可反复登记）'}
        ｜ 该码累计被扫 ${r.scan_stats.total} 次</p>`;

    renderUpstream(r);

    if (r.writable) {
      $('form').style.display = '';
      if ($('code-show')) $('code-show').textContent = code;
      if (r.server_now) setServerNow(r.server_now);   // 用扫码响应里的系统时间先校准
      fillTimeDefaults();
      $('upstream').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
      $('upstream').insertAdjacentHTML('beforeend',
        `<div class="card"><div class="alert warn" style="margin:0">${TC.esc(r.gate.reason)}</div></div>`);
      $('upstream').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  /* ---------------- 与服务端系统时间同步 ----------------
     服务端才是权威：提交时无论前端传什么都以服务器时间覆盖。
     这里做的是「显示同步」——用服务器时间校准一次，之后按客户端流逝的毫秒数继续走，
     这样即使本机时钟不准，显示出来的也是系统时间。 */
  let __srvBase = null, __cliBase = null;

  function parseMinute(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(s || '');
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0, 0).getTime();
  }

  function fmtMinute(ms) {
    const d = new Date(ms), p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function serverMinute() {
    if (__srvBase == null) return null;
    return fmtMinute(__srvBase + (Date.now() - __cliBase));
  }

  function setServerNow(s) {
    const ms = parseMinute(s);
    if (ms == null) return;
    __srvBase = ms;
    __cliBase = Date.now();
    paintTime();
  }

  /** 拉取一次服务器时间做校准；拿不到就退回本机时间，页面不至于空白 */
  async function syncServerTime() {
    try {
      const r = await fetch('/api/now', { cache: 'no-store' });
      const j = await r.json();
      if (j && j.ok && j.now) { setServerNow(j.now); return; }
    } catch (e) { /* 网络抖动，退回本机时间 */ }
    if (__srvBase == null) {
      const d = new Date();
      __srvBase = d.getTime();
      __cliBase = d.getTime();
      paintTime();
    }
  }

  /** 把锁定的时间字段刷新到当前系统时间；日期类字段用系统日期兜底 */
  function paintTime() {
    const now = serverMinute();
    if (!now) return;
    document.querySelectorAll('input[data-locked="1"]').forEach(n => { n.value = now; });
    const today = now.slice(0, 10);
    ['f_produce_date', 'f_sold_at', 'f_bought_at'].forEach(id => {
      const n = $(id);
      if (n && !n.value) n.value = today;
    });
  }

  function fillTimeDefaults() {
    // 锁定的时间字段由 paintTime 统一刷新，这里只补日期类默认值
    paintTime();
    syncServerTime();
  }

  let __tick = null;
  function startTimeTicker() {
    if (__tick) return;
    __tick = setInterval(paintTime, 15000);
  }

  /* ---------------- 图片凭证（可选） ----------------
     手机直出的照片动辄几 MB，直接塞进 payload 会把库撑爆，也会让存证记录变得极重。
     所以在浏览器里先压到长边 1000px 以内、JPEG 质量递减到约 200KB 以下，
     再以 data URL 形式随 payload 一起进哈希——改图就等于改数据，链会断。 */
  const SHOT_MAX_SIDE = 1000;
  const SHOT_TARGET = 200 * 1024;    // 目标字符数
  const SHOT_HARD_CAP = 380 * 1024;  // 超过就提示换图

  function readDataURL(file) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.onerror = () => rej(new Error('读取失败'));
      r.readAsDataURL(file);
    });
  }

  function loadImage(src) {
    return new Promise((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = () => rej(new Error('不是可用的图片'));
      i.src = src;
    });
  }

  async function compressShot(file) {
    const raw = await readDataURL(file);
    const img = await loadImage(raw);
    const scale = Math.min(1, SHOT_MAX_SIDE / Math.max(img.width || 1, img.height || 1));
    let w = Math.max(1, Math.round((img.width || 1) * scale));
    let h = Math.max(1, Math.round((img.height || 1) * scale));
    const cv = document.createElement('canvas');
    const ctx = cv.getContext('2d');
    let q = 0.72, out = '';
    for (let round = 0; round < 4; round++) {
      cv.width = w; cv.height = h;
      ctx.fillStyle = '#fff';           // 透明 PNG 转 JPEG 会变黑，先铺白底
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
      out = cv.toDataURL('image/jpeg', q);
      if (out.length <= SHOT_TARGET) break;
      q -= 0.12;
      if (q < 0.4) { w = Math.round(w * 0.75); h = Math.round(h * 0.75); q = 0.62; }
    }
    return out;
  }

  async function pickPhoto(k, file) {
    const info = $('shot-info-' + k);
    if (!file) { clearPhoto(k); return; }
    if (!/^image\//.test(file.type)) { if (info) info.textContent = '请选择图片文件'; return; }
    if (info) info.textContent = '正在压缩图片…';
    try {
      const url = await compressShot(file);
      if (url.length > SHOT_HARD_CAP) {
        if (info) info.textContent = '这张图压缩后仍过大，请换一张或截小一点';
        clearPhoto(k);
        return;
      }
      const hidden = $('f_' + k), prev = $('shot-prev-' + k);
      if (hidden) hidden.value = url;
      if (prev) prev.innerHTML = `<img src="${url}" alt="图片预览">`;
      if (info) info.textContent = `已就绪 · 约 ${Math.round(url.length / 1024)} KB · 将随记录一起上链存证`;
    } catch (e) {
      if (info) info.textContent = '读取这张图片失败，请换一张试试';
      clearPhoto(k);
    }
  }

  function clearPhoto(k) {
    const hidden = $('f_' + k), prev = $('shot-prev-' + k),
          file = $('shot_' + k), info = $('shot-info-' + k);
    if (hidden) hidden.value = '';
    if (file) file.value = '';
    if (prev) prev.innerHTML = '未选择图片';
    if (info) info.textContent = '';
  }

  function clearAllPhotos() {
    document.querySelectorAll('[id^="shot-prev-"]').forEach(n => {
      clearPhoto(n.id.replace('shot-prev-', ''));
    });
  }

  async function submit(e) {
    if (e) e.preventDefault();
    const code = window.__code;
    if (!code) { toast('请先选择追溯码'); return; }
    const who = actor();
    if (!who) { toast('请先填写上报主体名称'); $('actor-form').style.display = ''; return; }

    const payload = {};
    document.querySelectorAll('[data-field]').forEach(n => { payload[n.dataset.field] = n.value.trim(); });

    const btn = $('btn-submit');
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = '写入存证中…';

    const r = await TC.api('/api/event', {
      code: code, stage: currentStage(), actor: who,
      region: ($('region') && $('region').value.trim()) || localStorage.getItem('tc_region_' + currentStage()) || '',
      payload: payload
    });

    btn.disabled = false;
    btn.textContent = old;

    if (!r.ok) {
      $('result').innerHTML = `<div class="alert bad"><b>提交失败</b><br>${TC.esc(r.reason)}</div>`;
      $('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }

    const v = r.verify || {};
    $('result').innerHTML = `
      <div class="card">
        <div class="alert ok"><b>✅ 存证成功</b>　该码第 ${r.event.seq} 条记录已写入存证链</div>
        ${progressBar(r.progress)}
        <table>
          <tr><th style="width:104px">环节</th><td>${TC.esc(r.event.event_type)} ｜ ${TC.esc(r.event.actor)}</td></tr>
          <tr><th>时间戳</th><td>${TC.esc(r.event.timestamp)}</td></tr>
          <tr><th>数据摘要</th><td class="mono">${r.event.payload_hash}</td></tr>
          <tr><th>前序哈希</th><td class="mono">${r.event.prev_hash || 'GENESIS（该码首条记录）'}</td></tr>
          <tr><th>链上哈希</th><td class="mono">${r.event.event_hash}</td></tr>
          <tr><th>数字签名</th><td class="mono">${r.event.signature.slice(0, 48)}…</td></tr>
        </table>
        <p class="small muted">链路校验：${v.valid ? '✅ 完整，未被篡改' : '❌ ' + (v.issues || []).join('；')}
          （已复核 ${v.checked} 条）</p>
        <div class="btn-row" style="margin-top:10px">
          <a class="btn ghost sm" href="${traceBase()}/t/${encodeURIComponent(code)}" target="_blank">查看公众验真页</a>
          <button class="btn ghost sm" type="button" onclick="PortUI.next()">继续扫下一个</button>
        </div>
      </div>`;
    $('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function next() {
    window.__code = null;
    $('form').reset();
    $('form').style.display = 'none';
    $('result').innerHTML = '';
    $('upstream').innerHTML = '';
    clearAllPhotos();
    $('status').innerHTML = '<div class="alert info">请扫描下一个追溯码，或手动输入追溯码。</div>';
    if ($('manual')) $('manual').value = '';
    loadActor();
    window.scrollTo({ top: 0, behavior: 'smooth' });
    if (currentStage() === 'logistics') startScan();   // 流通端连续作业
  }

  function startScan() {
    const b = $('btn-scan'), s = $('btn-stop');
    if (b) b.style.display = 'none';
    if (s) s.style.display = '';
    TC.startScanner(code => { if (b) b.style.display = ''; if (s) s.style.display = 'none'; pick(code); },
      () => { if (b) b.style.display = ''; if (s) s.style.display = 'none'; });
  }

  function stopScan() {
    TC.stopScanner();
    const b = $('btn-scan'), s = $('btn-stop');
    if (b) b.style.display = '';
    if (s) s.style.display = 'none';
  }

  function init() {
    loadActor();
    if ($('btn-scan')) $('btn-scan').onclick = startScan;
    if ($('btn-stop')) $('btn-stop').onclick = stopScan;
    if ($('btn-save-actor')) $('btn-save-actor').onclick = saveActor;
    if ($('btn-manual')) $('btn-manual').onclick = () => { const v = $('manual').value.trim(); if (v) pick(v); };
    if ($('manual')) $('manual').addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); const v = $('manual').value.trim(); if (v) pick(v); }
    });
    if ($('form')) $('form').onsubmit = submit;

    // 图片凭证：选中即压缩预览，真正提交的是隐藏字段里的 data URL
    document.querySelectorAll('input[id^="shot_"]').forEach(n => {
      n.addEventListener('change', () => pickPhoto(n.id.replace('shot_', ''), n.files && n.files[0]));
    });

    syncServerTime();
    startTimeTicker();

    const q = new URLSearchParams(location.search).get('code');
    if (q) { if ($('manual')) $('manual').value = q; pick(q); }
    else if ($('status')) $('status').innerHTML =
      '<div class="alert info">请扫描商品上的追溯码，或手动输入追溯码。</div>';
  }

  window.PortUI = {
    init, pick, next, clearPhoto,
    editActor: () => { localStorage.removeItem('tc_actor_' + currentStage()); loadActor(); }
  };
  document.addEventListener('DOMContentLoaded', init);
})();
