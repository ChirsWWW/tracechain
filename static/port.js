/* port.js —— 各作业端共用交互
   流程：采集追溯码 → ① 核对上游已登记记录（只读） → ② 录入本端上报内容 → 存证 */
(function () {
  const TC = window.TC;
  const $ = id => document.getElementById(id);
  const STAGE_CN = { produce: '生产端', logistics: '流通端', retail: '销售端', consume: '消费端' };

  function currentStage() { return document.body.dataset.stage; }
  function traceBase() { return (document.body.dataset.traceBase || '').replace(/\/$/, ''); }

  /** 顶部步骤向导：1 采集 → 2 核对 → 3 录入 */
  function setStep(n) {
    [1, 2, 3].forEach(i => {
      const el = $('wz-' + i);
      if (!el) return;
      el.className = 'wz' + (i < n ? ' done' : (i === n ? ' on' : ''));
    });
  }

  /** 记忆本机操作主体，避免每次重复填写 */
  function loadActor() {
    const s = currentStage();
    const box = $('actor-box'), form = $('actor-form');
    const saved = localStorage.getItem('tc_actor_' + s) || '';
    const region = localStorage.getItem('tc_region_' + s) || '';
    if (saved && box) {
      box.innerHTML = `<div class="who">
          <span class="who-label">当前作业主体</span>
          <b>${TC.esc(saved)}</b>
          ${region ? `<span class="who-label">${TC.esc(region)}</span>` : ''}
          <span style="flex:1"></span>
          <button class="btn ghost sm" type="button" onclick="PortUI.editActor()">修改</button>
        </div>`;
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

  function saveActor() {
    const v = $('actor').value.trim();
    if (!v) { toast('请填写主体名称'); return; }
    localStorage.setItem('tc_actor_' + currentStage(), v);
    if ($('region')) localStorage.setItem('tc_region_' + currentStage(), $('region').value.trim());
    loadActor();
    toast('已保存作业主体：' + v, 'ok');
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

  function pad4(n) { return String(n == null ? 0 : n).padStart(4, '0'); }

  /** 链路完整度：已完成环节用实心点，未完成用空心点 */
  function progressBar(progress) {
    if (!progress || !progress.length) return '';
    return '<div class="steps">' + progress.map(s =>
      `<span class="step ${s.done ? 'done' : ''}"><b>${TC.esc(s.label)}</b>` +
      (s.count > 1 ? `<em>×${s.count}</em>` : '') + '</span>'
    ).join('<span class="step-sep"></span>') + '</div>';
  }

  /** 一条已登记记录（上游或本端） */
  function upstreamRow(ev, mine) {
    const p = payloadOf(ev);
    const L = (window.TC_LABELS_BY_STAGE || {})[ev.stage] || window.TC_LABELS || {};
    const stars = [];
    ['occur_at', 'goods_state', 'action'].forEach(k => {
      if (p[k]) stars.push(`<span class="kv-star"><i>${TC.esc(L[k] || k)}</i><b>${TC.esc(p[k])}</b></span>`);
    });
    let kv = '';
    for (const k in p) {
      if (p[k] === '' || p[k] == null || k === 'action') continue;
      if (typeof p[k] === 'string' && p[k].indexOf('data:image/') === 0) {
        kv += `<dt>${TC.esc(L[k] || k)}</dt><dd>
          <a href="${p[k]}" target="_blank" rel="noopener">
            <img class="shot" src="${p[k]}" alt="现场照片"></a></dd>`;
        continue;
      }
      kv += `<dt>${TC.esc(L[k] || k)}</dt><dd>${TC.esc(p[k])}</dd>`;
    }
    return `<div class="up-row ${mine ? 'mine' : ''}">
      <div class="up-head">
        <span class="up-stage">${TC.esc(STAGE_CN[ev.stage] || ev.stage)}<em>${TC.esc(ev.event_type || '')}</em></span>
        ${mine ? '<span class="tag ok">本端记录</span>' : '<span class="tag">上游记录</span>'}
      </div>
      ${stars.length ? `<div class="kv-stars">${stars.join('')}</div>` : ''}
      <div class="up-meta">
        <span>上报主体 ${TC.esc(ev.actor)}${ev.region ? ' · ' + TC.esc(ev.region) : ''}</span>
        <span>存证时间 ${TC.esc(ev.timestamp)}</span>
      </div>
      ${kv ? `<dl class="kv">${kv}</dl>` : ''}
      <div class="up-meta" style="margin-top:9px">
        <span class="mono">记录 #${pad4(ev.seq)}</span>
        <span class="mono">数据摘要 ${TC.esc((ev.payload_hash || '').slice(0, 16))}…</span>
      </div>
    </div>`;
  }

  /** 上游已登记记录（只读），置于本端表单之前 */
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
             ${prod.batch ? `<span>批次 <b>${TC.esc(prod.batch)}</b></span>` : ''}
             ${prod.produce_date ? `<span>生产日期 <b>${TC.esc(prod.produce_date)}</b></span>` : ''}
             ${prod.shelf_life ? `<span>保质期 <b>${TC.esc(prod.shelf_life)}</b></span>` : ''}
             ${prod.origin ? `<span>产地 <b>${TC.esc(prod.origin)}</b></span>` : ''}
             ${prod.license ? `<span>生产许可证 <b>${TC.esc(prod.license)}</b></span>` : ''}
           </div>
         </div>`
      : `<div class="alert warn" style="margin:0">
           <b>该码尚未完成生产登记</b><br>商品身份信息为空，须由生产厂家在生产端登记后本端方可作业。
         </div>`;

    const upstreamBody = ups.length
      ? ups.map(e => upstreamRow(e, false)).join('')
      : `<div class="empty">该码在本端之前没有任何登记记录</div>`;
    const mineBody = mine.length
      ? `<h4 class="grp">本端已登记记录 ${mine.length} 条</h4>${mine.map(e => upstreamRow(e, true)).join('')}`
      : '';

    box.innerHTML = `<div class="card">
      <div class="card-hd">
        <div>
          <div class="t">上游环节已登记记录</div>
          <div class="s">其他端口此前写入该码的记录，只读，本端无法修改</div>
        </div>
        <div class="r">READ-ONLY</div>
      </div>
      ${progressBar(r.progress)}
      <div style="margin-top:14px">${arch}</div>
      <h4 class="grp">其他环节记录 ${ups.length} 条</h4>
      ${upstreamBody}
      ${mineBody}
    </div>`;
  }

  /* 核心节点唤醒期：自动重试，不把等待转嫁成一次「失败」。
     免费实例冷启动几十秒是常态，让用户为此手动点「重新校验」毫无意义。 */
  const WAKE_MAX = 14;

  async function waitCore(code, st) {
    for (let n = 1; n <= WAKE_MAX; n++) {
      st.innerHTML = `<div class="alert info"><b>数据核心节点正在唤醒</b><br>
        正在自动重试（第 ${n} / ${WAKE_MAX} 次），请保持页面打开，无需手动操作。</div>`;
      await new Promise(s => setTimeout(s, n === 1 ? 1200 : 2500));
      let r;
      try { r = await TC.api('/api/scan', { code: code, stage: currentStage() }); }
      catch (e) { r = { ok: false, net: true }; }
      if (r && r.ok) { window.__code = code; renderResult(code, r); return; }
      if (r && !r.net) {
        const head = r.unreadable ? '未能识别追溯码'
          : (r.forged ? '追溯码校验未通过' : '无法识别');
        st.innerHTML = `<div class="alert bad"><b>${head}</b><br>${TC.esc(r.reason || '')}</div>`;
        return;
      }
    }
    st.innerHTML = `<div class="alert bad"><b>数据核心节点暂时不可用</b><br>
      已自动重试 ${WAKE_MAX} 次仍未成功，请稍后再试。
      <div class="btn-row" style="margin-top:10px">
        <button class="btn ghost sm" type="button" id="btn-retry">重新校验</button>
      </div></div>`;
    const rt = $('btn-retry');
    if (rt) rt.onclick = () => pick(code);
  }

  /** 选中一个追溯码：校验 → 展示上游记录与本端可作业性 */
  async function pick(raw) {
    const code = TC.extractCode(raw);
    if (!code) return;
    if ($('manual')) $('manual').value = code;
    const st = $('status');
    // 先把界面切到「校验中」，再发请求；任何一步出错都必须给出可见提示，
    // 否则用户看到的就是一个永远停在「正在校验」的死页面。
    st.innerHTML = `<div class="alert info">正在校验 <span class="mono">${TC.esc(code)}</span> …</div>`;
    if ($('form')) $('form').style.display = 'none';
    if ($('result')) $('result').innerHTML = '';
    if ($('upstream')) $('upstream').innerHTML = '';
    setStep(1);

    let r;
    try {
      r = await TC.api('/api/scan', { code: code, stage: currentStage() });
    } catch (e) {
      r = { ok: false, reason: '校验请求异常：' + ((e && e.message) ? e.message : e) };
    }
    if (!r || typeof r !== 'object') r = { ok: false, reason: '校验返回了无法识别的内容' };

    window.__code = code;

    if (!r.ok) {
      // 核心节点尚未醒来：自动继续重试，醒来后直接接着往下走
      if (r.net) { await waitCore(code, st); return; }
      // 伪造码 / 识别失败重试多少次结果都一样，不再给按钮，避免噪音
      const head = r.unreadable ? '未能识别追溯码'
        : (r.forged ? '追溯码校验未通过' : '无法识别');
      st.innerHTML = `<div class="alert bad"><b>${head}</b><br>${TC.esc(r.reason || '请稍后重试')}</div>`;
      return;
    }

    try {
      renderResult(code, r);
    } catch (e) {
      st.innerHTML = `<div class="alert bad"><b>页面渲染失败</b><br>${TC.esc(e && e.message ? e.message : e)}
        <div class="btn-row" style="margin-top:10px">
          <button class="btn ghost sm" type="button" id="btn-retry">重新校验</button>
        </div></div>`;
      const rt2 = $('btn-retry');
      if (rt2) rt2.onclick = () => pick(code);
    }
  }

  /** 校验通过后的界面切换（独立出来，便于整体兜底） */
  function renderResult(code, r) {
    const st = $('status');
    const gate = r.gate || {};
    const limit = gate.limit;
    st.innerHTML = `
      <div class="alert ok"><b>追溯码校验通过</b>　<span class="code-chip">${TC.esc(code)}</span></div>
      <div class="up-meta" style="margin-top:9px">
        <span>本端已登记 ${gate.used || 0} 次${limit ? `（上限 ${limit} 次）` : '（不限次数）'}</span>
        <span>该码累计扫描 ${(r.scan_stats || {}).total || 0} 次</span>
        <span>存证记录 ${(r.upstream || []).length} 条</span>
      </div>`;

    renderUpstream(r);
    setStep(2);

    const up = $('upstream');
    if (r.writable) {
      if ($('form')) $('form').style.display = '';
      if ($('code-show')) $('code-show').textContent = code;
      if (r.server_now) setServerNow(r.server_now);   // 用扫码响应里的系统时间先校准
      fillTimeDefaults();
      setStep(3);
      if (up) up.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
      if (up) {
        up.insertAdjacentHTML('beforeend',
          `<div class="card"><div class="alert warn" style="margin:0"><b>本端暂不可上报</b><br>${TC.esc(gate.reason || '')}</div></div>`);
        up.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
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
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5])).getTime();
  }

  function fmtMinute(ms) {
    const d = new Date(ms), p = n => String(n).padStart(2, '0');
    return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
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

  /** 校准显示用的系统时间。
      不再为了一个时间戳去请求核心节点——页面本身已经注入了服务端时间基准
      （顶栏时钟用的就是它），扫码与上报的响应里也会带 server_now 覆盖校准。 */
  function syncServerTime() {
    if (__srvBase != null) return;
    const el = document.getElementById('sys-clock');
    const ts = el ? (parseInt(el.dataset.ts, 10) || 0) : 0;
    const tz = el ? (parseInt(el.dataset.tz, 10) || 0) : 0;
    if (ts) {
      __srvBase = (ts + tz) * 1000;
      __cliBase = Date.now();
    } else {
      // 极少数情况下拿不到基准：退回本机时间，时间字段不会因此空着
      __srvBase = Date.now();
      __cliBase = Date.now();
    }
    paintTime();
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
    paintTime();
    syncServerTime();
  }

  let __tick = null;
  function startTimeTicker() {
    if (__tick) return;
    __tick = setInterval(paintTime, 15000);
  }

  /* ---------------- 图片凭证（选填） ----------------
     手机直出的照片动辄几 MB，直接塞进 payload 会把库撑爆，也会让存证记录变得极重。
     所以在浏览器里先压到长边 1000px 以内、JPEG 质量递减到约 200KB 以下，
     再以 data URL 形式随 payload 一起进哈希——改图就等于改数据，链会断。 */
  const SHOT_MAX_SIDE = 1000;
  const SHOT_TARGET = 200 * 1024;
  const SHOT_HARD_CAP = 380 * 1024;

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
        if (info) info.textContent = '压缩后仍超出体积上限，请更换图片';
        clearPhoto(k);
        return;
      }
      const hidden = $('f_' + k), prev = $('shot-prev-' + k);
      if (hidden) hidden.value = url;
      if (prev) prev.innerHTML = `<img src="${url}" alt="图片预览">`;
      if (info) info.textContent = `已就绪 · 约 ${Math.round(url.length / 1024)} KB · 将随记录一起存证`;
    } catch (e) {
      if (info) info.textContent = '读取失败，请更换图片';
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
    if (!code) { toast('请先采集追溯码'); return; }
    const who = actor();
    if (!who) { toast('请先填写作业主体名称'); $('actor-form').style.display = ''; return; }

    const payload = {};
    document.querySelectorAll('[data-field]').forEach(n => { payload[n.dataset.field] = n.value.trim(); });

    const btn = $('btn-submit');
    btn.disabled = true;
    const old = btn.innerHTML;
    btn.textContent = '写入存证中…';

    const body = {
      code: code, stage: currentStage(), actor: who,
      region: ($('region') && $('region').value.trim()) || localStorage.getItem('tc_region_' + currentStage()) || '',
      payload: payload
    };
    let r = await TC.api('/api/event', body);
    // 核心节点冷启动时不能把用户填好的内容丢掉：自动等它醒来再提交一次。
    // 重试用的还是同一份 payload，不会产生半条记录。
    for (let n = 1; r && r.ok === false && r.net && n <= WAKE_MAX; n++) {
      btn.textContent = `核心节点正在唤醒，自动重试 ${n} / ${WAKE_MAX}…`;
      await new Promise(s => setTimeout(s, n === 1 ? 1200 : 2500));
      r = await TC.api('/api/event', body);
    }

    btn.disabled = false;
    btn.innerHTML = old;

    if (!r || !r.ok || !r.event) {
      const head = (r && r.net) ? '数据核心节点暂时不可用' : '上报未成功';
      $('result').innerHTML = `<div class="card"><div class="alert bad">
        <b>${head}</b><br>${TC.esc((r && r.reason) || '请稍后重试')}</div></div>`;
      $('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }

    const v = r.verify || {};
    $('result').innerHTML = `
      <div class="card">
        <div class="card-hd">
          <div>
            <div class="t">上报成功</div>
            <div class="s">该码第 ${r.event.seq} 条记录已写入存证链</div>
          </div>
          <div class="r">TX-${pad4(r.event.seq)}</div>
        </div>
        <div class="alert ok" style="margin-top:0"><b>链路校验通过</b>　
          已复核 ${v.checked} 条记录，哈希链连续、签名有效</div>
        ${progressBar(r.progress)}
        <div class="table-wrap" style="margin-top:16px">
          <table>
            <thead><tr><th style="width:110px">项目</th><th>内容</th></tr></thead>
            <tbody>
              <tr><td>环节</td><td>${TC.esc(r.event.event_type)}　·　${TC.esc(r.event.actor)}</td></tr>
              <tr><td>存证时间</td><td class="mono">${TC.esc(r.event.timestamp)}</td></tr>
              <tr><td>数据摘要</td><td class="mono">${r.event.payload_hash}</td></tr>
              <tr><td>前序哈希</td><td class="mono">${r.event.prev_hash || 'GENESIS（该码首条记录）'}</td></tr>
              <tr><td>链上哈希</td><td class="mono">${r.event.event_hash}</td></tr>
              <tr><td>数字签名</td><td class="mono">${r.event.signature.slice(0, 48)}…</td></tr>
            </tbody>
          </table>
        </div>
        <div class="btn-row" style="margin-top:14px">
          <a class="btn ghost sm" href="${traceBase()}/t/${encodeURIComponent(code)}" target="_blank">查看验真页</a>
          <button class="btn ghost sm" type="button" onclick="PortUI.next()">继续下一个</button>
        </div>
      </div>`;
    setStep(4);
    $('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function next() {
    window.__code = null;
    $('form').reset();
    $('form').style.display = 'none';
    $('result').innerHTML = '';
    $('upstream').innerHTML = '';
    clearAllPhotos();
    $('status').innerHTML = '<div class="alert info">请扫描商品标签上的二维码，或手工录入追溯码。</div>';
    if ($('manual')) $('manual').value = '';
    setStep(1);
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
    setStep(1);

    const q = new URLSearchParams(location.search).get('code');
    if (q) { if ($('manual')) $('manual').value = q; pick(q); }
    else if ($('status')) $('status').innerHTML =
      '<div class="alert info">请扫描商品标签上的二维码，或手工录入追溯码。</div>';
  }

  window.PortUI = {
    init, pick, next, clearPhoto,
    editActor: () => { localStorage.removeItem('tc_actor_' + currentStage()); loadActor(); }
  };
  document.addEventListener('DOMContentLoaded', init);
})();
