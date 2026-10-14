/* ============================================================
   秋白超分 - 前端逻辑
   复用去水印项目的设计：队列 / 历史 / 模态预览 / 原生保存
   ============================================================ */

const $ = (id) => document.getElementById(id);

const els = {
  dropzone: $('dropzone'),
  fileInput: $('file-input'),
  uploadStatus: $('upload-status'),
  stepUpload: $('step-upload'),
  stepHistory: $('step-history'),
  appNav: $('app-nav'),
  // 队列
  queueWrap: $('queue-wrap'),
  queueList: $('queue-list'),
  queueTitle: $('queue-title'),
  queueProgress: $('queue-progress'),
  btnBatchProcess: $('btn-batch-process'),
  btnSaveAll: $('btn-save-all'),
  btnQueueClear: $('btn-queue-clear'),
  methodSel: $('method'),
  scaleSel: $('scale'),
  // 历史
  historyList: $('history-list'),
  historyEmpty: $('history-empty'),
  historyCount: $('history-count'),
  btnHistorySaveAll: $('btn-history-save-all'),
  // toast
  toastHost: $('toast-host'),
  // 预览
  previewModal: $('preview-modal'),
  previewCaption: $('preview-caption'),
  compareImages: $('compare-images'),
  compareVideos: $('compare-videos'),
  compareControls: $('compare-controls'),
  cmpImgOrig: $('cmp-img-orig'),
  cmpImgOut: $('cmp-img-out'),
  cmpVideoOrig: $('cmp-video-orig'),
  cmpVideoOut: $('cmp-video-out'),
  cmpPlay: $('cmp-play'),
  cmpPlayIcon: $('cmp-play-icon'),
  cmpSeek: $('cmp-seek'),
  cmpTime: $('cmp-time'),
  // 后端
  backendPill: $('backend-pill'),
};

let state = {
  view: 'home',
  queue: [],
  batchRunning: false,
  historyList: [],
};

const STATUS_LABEL = {
  pending: '待处理',
  processing: '处理中',
  done: '已完成',
  error: '失败',
};

// =========================================================
// Toast
// =========================================================
function toast(msg, type = '') {
  const el = document.createElement('div');
  el.className = 'toast' + (type ? ' ' + type : '');
  el.textContent = msg;
  els.toastHost.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .3s, transform .3s';
    el.style.opacity = '0';
    el.style.transform = 'translateY(8px)';
    setTimeout(() => el.remove(), 300);
  }, 2400);
}
function dismissAllToasts() {
  els.toastHost.querySelectorAll('.toast').forEach(el => {
    el.style.transition = 'opacity .15s, transform .15s';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 160);
  });
}

// =========================================================
// 队列
// =========================================================
function addToQueue(item) {
  state.queue.push({
    id: item.id,
    filename: item.filename,
    src: item.src,
    thumbUrl: item.thumbUrl,
    info: item.info,
    kind: item.kind || 'video',
    status: 'pending',
    progress: 0,
    resultUrl: null,
    resultId: null,
    error: null,
  });
}
function getQueueItem(id) { return state.queue.find(q => q.id === id); }
function updateQueueItem(id, patch) {
  const q = getQueueItem(id);
  if (!q) return;
  Object.assign(q, patch);
  renderQueue();
}
function clearQueue() {
  if (state.batchRunning) { toast('生成中，无法清空', 'warn'); return; }
  const n = state.queue.length;
  state.queue = [];
  els.uploadStatus.classList.add('hidden');
  els.uploadStatus.textContent = '';
  renderQueue();
  dismissAllToasts();
  if (n > 0) toast(`已清空 ${n} 个文件`, 'ok');
}
function renderQueue() {
  if (state.queue.length === 0) {
    els.queueWrap.classList.add('hidden');
    return;
  }
  els.queueWrap.classList.remove('hidden');
  const total = state.queue.length;
  const done = state.queue.filter(q => q.status === 'done').length;
  const err = state.queue.filter(q => q.status === 'error').length;
  els.queueTitle.textContent = `队列 · ${total} 个文件`;
  els.queueProgress.textContent = done > 0 || err > 0 ? `${done} 完成${err > 0 ? ` · ${err} 失败` : ''}` : '';

  const pending = state.queue.filter(q => q.status === 'pending').length;
  els.btnBatchProcess.disabled = pending === 0 || state.batchRunning;
  els.btnBatchProcess.innerHTML = state.batchRunning
    ? '<span class="ico">⏳</span> 生成中…'
    : `<span class="ico">▶</span> 开始生成${pending > 0 ? ` (${pending})` : ''}`;

  const doneItems = state.queue.filter(q => q.status === 'done').length;
  els.btnSaveAll.disabled = doneItems === 0;
  els.btnSaveAll.innerHTML = `<span class="ico">⤓</span> 全部下载 (${doneItems})`;

  els.queueList.innerHTML = '';
  state.queue.forEach(q => {
    const li = document.createElement('li');
    li.className = 'queue-item ' + q.status;
    const isImg = q.kind === 'image';
    const sizeMB = q.info?.size ? (q.info.size / 1024 / 1024).toFixed(1) : '';
    const dim = q.info ? `${q.info.width}×${q.info.height}` : '';
    const dur = isImg ? '图片' : (q.info?.duration ? `${q.info.duration.toFixed(1)}s` : '?');
    li.innerHTML = `
      <div class="queue-thumb">${q.thumbUrl ? `<img src="${q.thumbUrl}" alt="" loading="lazy">` : ''}</div>
      <div class="queue-meta">
        <div class="name">${escapeHtml(q.filename)}</div>
        <div class="info">${dim} · ${dur} · ${sizeMB} MB${q.error ? ' · ' + escapeHtml(q.error) : ''}</div>
        ${q.status === 'processing' ? `<div class="queue-progress-bar"><div style="width:${q.progress}%"></div></div>` : ''}
      </div>
      <span class="queue-status ${q.status}">${STATUS_LABEL[q.status] || q.status}</span>
      <div class="queue-item-actions">
        ${q.status === 'done' ? `<button data-act="view" data-id="${q.id}" title="预览"><span class="ico">👁</span></button>` : ''}
        ${q.status === 'done' ? `<button data-act="save" data-id="${q.id}" title="保存到…"><span class="ico">⤓</span></button>` : ''}
        <button data-act="del" data-id="${q.id}" title="移除" class="danger"><span class="ico">✕</span></button>
      </div>
    `;
    li.querySelector('[data-act=view]')?.addEventListener('click', e => { e.stopPropagation(); viewItem(q); });
    li.querySelector('[data-act=save]')?.addEventListener('click', e => { e.stopPropagation(); saveItem(q); });
    li.querySelector('[data-act=del]')?.addEventListener('click', e => { e.stopPropagation(); removeItem(q.id); });
    els.queueList.appendChild(li);
  });
}

function viewItem(q) {
  if (q.kind === 'image') openImagePreview(q);
  else openVideoPreview(q);
}
function saveItem(q) {
  doSaveAs(q);
}
function removeItem(id) {
  state.queue = state.queue.filter(q => q.id !== id);
  renderQueue();
}
els.btnQueueClear.addEventListener('click', clearQueue);

// =========================================================
// 视图路由
// =========================================================
function showView(view) {
  state.view = view;
  for (const k of ['stepUpload', 'stepHistory']) {
    els[k].classList.add('hidden');
  }
  if (view === 'home') els.stepUpload.classList.remove('hidden');
  else if (view === 'history') {
    els.stepHistory.classList.remove('hidden');
    loadHistory();
  }
  els.appNav.querySelectorAll('.nav-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.view === view);
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
els.appNav.addEventListener('click', e => {
  const btn = e.target.closest('.nav-btn');
  if (btn) showView(btn.dataset.view);
});

// =========================================================
// 1. 上传
// =========================================================
els.dropzone.addEventListener('click', () => els.fileInput.click());
els.fileInput.addEventListener('change', () => {
  if (els.fileInput.files.length) handleFiles(els.fileInput.files);
});
['dragover', 'dragenter'].forEach(ev => els.dropzone.addEventListener(ev, e => {
  e.preventDefault(); els.dropzone.classList.add('drag');
}));
['dragleave', 'drop'].forEach(ev => els.dropzone.addEventListener(ev, e => {
  e.preventDefault(); els.dropzone.classList.remove('drag');
}));
els.dropzone.addEventListener('drop', e => {
  if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
});

async function handleFiles(fileList) {
  const files = Array.from(fileList);
  if (files.length === 0) { toast('请选择文件', 'err'); return; }
  els.uploadStatus.className = 'status';
  els.uploadStatus.classList.remove('hidden');
  els.uploadStatus.textContent = `正在上传 ${files.length} 个文件…`;
  const fd = new FormData();
  files.forEach(f => fd.append('video', f));
  try {
    const res = await fetch('/api/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '上传失败');
    const items = data.items || [{ id: data.id, filename: data.filename || files[0].name, src: data.src, info: data.info, kind: data.kind }];
    items.forEach(it => {
      if (it.id) addToQueue(it);
    });
    const okCount = items.filter(it => it.id).length;
    const errCount = items.length - okCount;
    els.uploadStatus.textContent = errCount > 0
      ? `✓ ${okCount} 个成功 · ✕ ${errCount} 个失败`
      : `✓ 成功添加 ${okCount} 个文件到队列`;
    els.fileInput.value = '';
    renderQueue();
    if (errCount > 0) toast(`${errCount} 个文件失败`, 'err');
    else toast(`已添加 ${okCount} 个文件`, 'ok');
  } catch (e) {
    els.uploadStatus.classList.add('err');
    els.uploadStatus.textContent = '✕ ' + e.message;
    toast(e.message, 'err');
  }
}

// =========================================================
// 2. 处理
// =========================================================
els.btnBatchProcess.addEventListener('click', async () => {
  if (state.batchRunning) return;
  const pending = state.queue.filter(q => q.status === 'pending');
  if (pending.length === 0) { toast('没有待处理文件', 'warn'); return; }
  const method = els.methodSel.value;
  const scale = parseInt(els.scaleSel.value, 10);
  state.batchRunning = true;
  renderQueue();
  let ok = 0, err = 0;
  for (const q of pending) {
    const r = await processOne(q.id, method, scale);
    if (r) ok++; else err++;
  }
  state.batchRunning = false;
  renderQueue();
  toast(`生成完成 · ${ok} 成功${err > 0 ? ` · ${err} 失败` : ''}`, err > 0 ? 'warn' : 'ok');
  loadHistory();
});

async function processOne(id, method, scale) {
  updateQueueItem(id, { status: 'processing', progress: 0 });
  let pollTimer = null;
  let fakeTimer = null;
  try {
    // 1) 提交任务，立即拿到 task_id
    const res = await fetch('/api/process', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, method, scale }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '提交失败');

    // 真实进度还没到来前，给一个 1% → 4% 的微动兜底
    fakeTimer = setInterval(() => {
      const q = getQueueItem(id);
      if (!q || q.status !== 'processing') return clearInterval(fakeTimer);
      if ((q.progress || 0) < 4) {
        q.progress = Math.min(4, (q.progress || 0) + 0.5);
        renderQueue();
      }
    }, 300);

    // 2) 轮询真实进度
    const taskId = data.task_id;
    let lastP = 0;
    const done = await new Promise((resolve, reject) => {
      pollTimer = setInterval(async () => {
        try {
          const tr = await fetch(`/api/task/${taskId}`);
          const td = await tr.json();
          if (!tr.ok) {
            clearInterval(pollTimer);
            return reject(new Error(td.error || '查询失败'));
          }
          if (td.status === 'done' || td.status === 'error') {
            clearInterval(pollTimer);
            return td.status === 'done' ? resolve(td) : reject(new Error(td.error || '处理失败'));
          }
          const p = Math.max(lastP, Math.min(0.99, td.progress || 0));
          if (p !== lastP) {
            lastP = p;
            updateQueueItem(id, { progress: p * 100 });
          }
        } catch (e) {
          clearInterval(pollTimer);
          reject(e);
        }
      }, 400);
    });

    if (fakeTimer) clearInterval(fakeTimer);
    const outUrl = `/api/history/${id}/view`;
    updateQueueItem(id, { status: 'done', progress: 100, resultUrl: outUrl, resultId: id });
    return true;
  } catch (e) {
    if (pollTimer) clearInterval(pollTimer);
    if (fakeTimer) clearInterval(fakeTimer);
    updateQueueItem(id, { status: 'error', error: e.message });
    toast(e.message, 'err');
    return false;
  }
}

// =========================================================
// 3. 保存 + 预览
// =========================================================
async function doSaveAs(q) {
  if (!q || !q.id) return;
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.save_as) {
      const target = await window.pywebview.api.save_as(q.id);
      if (target && target.path) toast('已保存到 ' + target.path, 'ok');
      else if (target && target.error) toast('保存失败：' + target.error, 'err');
      else toast('已取消保存', '');
    } else {
      const a = document.createElement('a');
      a.href = `/api/history/${q.id}/download`;
      a.download = '超分_' + q.filename;
      document.body.appendChild(a); a.click(); a.remove();
      toast('已触发下载', 'ok');
    }
  } catch (e) { toast('保存失败：' + e.message, 'err'); }
}

async function doSaveAll(ids, label = '') {
  if (!ids || ids.length === 0) { toast('没有可下载的文件', 'warn'); return; }
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.save_all) {
      const r = await window.pywebview.api.save_all(ids);
      if (!r) { toast('已取消', ''); return; }
      if (r.count > 0) toast(`已保存 ${r.count} 个文件到 ${r.dir}`, 'ok');
      else toast('保存失败：无有效文件', 'err');
    } else {
      for (const id of ids) {
        const a = document.createElement('a');
        a.href = `/api/history/${id}/download`;
        document.body.appendChild(a); a.click(); a.remove();
        await new Promise(r => setTimeout(r, 200));
      }
      toast(`已触发 ${ids.length} 个下载`, 'ok');
    }
  } catch (e) { toast('全部下载失败：' + e.message, 'err'); }
}
els.btnSaveAll.addEventListener('click', () => {
  const ids = state.queue.filter(q => q.status === 'done').map(q => q.id);
  doSaveAll(ids, '队列');
});
els.btnHistorySaveAll.addEventListener('click', () => {
  const ids = Array.from(els.historyList.querySelectorAll('[data-act=save]'))
    .map(b => b.dataset.id);
  doSaveAll(ids, '历史');
});

function openPreview(id, source, itemKind) {
  let originalUrl = null, outputUrl = null, caption = '', kind = itemKind || 'video';
  if (source === 'queue') {
    const q = getQueueItem(id);
    if (!q) return;
    outputUrl = q.resultUrl;
    originalUrl = q.src || `/uploads/${id}${guessExtFromOutputName(q.resultUrl)}`;
    kind = q.kind || kind;
    const qs = (q.info?.width && q.info?.height) ? ` · ${q.info.width}×${q.info.height}` : '';
    caption = `${q.filename}${qs}`;
  } else if (source === 'history') {
    originalUrl = `/api/history/${id}/original`;
    outputUrl = `/api/history/${id}/view`;
    caption = '对比预览';
    // 尝试从已加载的 history list 里拿 kind；找不到就默认 video，由调用者也可显式传
    if (!itemKind && state.historyList) {
      const hit = state.historyList.find(h => h.id === id);
      if (hit) kind = hit.kind || 'video';
    }
  }
  if (!outputUrl) return;
  els.previewCaption.textContent = caption;

  // 重置左右元素
  els.compareImages.classList.add('hidden');
  els.compareVideos.classList.add('hidden');
  els.compareControls.classList.add('hidden');
  cmpReset();

  if (kind === 'image') {
    els.cmpImgOrig.src = originalUrl;
    els.cmpImgOut.src = outputUrl;
    els.compareImages.classList.remove('hidden');
  } else {
    els.cmpVideoOrig.src = originalUrl;
    els.cmpVideoOut.src = outputUrl;
    els.compareVideos.classList.remove('hidden');
    els.compareControls.classList.remove('hidden');
    // 元数据载入后初始化同步控件
    els.cmpVideoOut.onloadedmetadata = () => cmpInitControls();
    els.cmpVideoOrig.onloadedmetadata = () => cmpInitControls();
  }
  els.previewModal.classList.remove('hidden');
}

function guessExtFromOutputName(url) {
  // 输出 URL 通常形如 /output/<sid>__name_超分.<ext>
  const m = (url || '').match(/\.([a-zA-Z0-9]+)(?:\?|$)/);
  return m ? '.' + m[1].toLowerCase() : '.mp4';
}

function openImagePreview(q) { q.kind = 'image'; openPreview(q.id, 'queue'); }
function openVideoPreview(q) { q.kind = 'video'; openPreview(q.id, 'queue'); }

// ----- A/B 视频对比：共享播放控件 -----
let cmpSyncing = false;

function cmpReset() {
  cmpSyncing = true;
  [els.cmpVideoOrig, els.cmpVideoOut].forEach(v => {
    if (!v) return;
    try { v.pause(); } catch (e) {}
    v.removeAttribute('src');
    v.load && v.load();
  });
  els.cmpSeek.value = 0;
  els.cmpTime.textContent = '00:00 / 00:00';
  els.cmpPlayIcon.textContent = '▶';
  cmpSyncing = false;
}

function cmpInitControls() {
  // 用「输出」视频当主控，原始视频同步跟随
  const v = els.cmpVideoOut;
  if (!v || !v.duration || !isFinite(v.duration)) return;
  els.cmpSeek.value = 0;
  updateCmpTime();
  [els.cmpVideoOrig, els.cmpVideoOut].forEach(x => {
    x.addEventListener('play',  onCmpPlay);
    x.addEventListener('pause', onCmpPause);
    x.addEventListener('ended', onCmpEnded);
    x.addEventListener('timeupdate', updateCmpTime);
  });
  // 默认静音 + 自动播放
  els.cmpVideoOrig.muted = true;
  els.cmpVideoOut.muted = true;
  Promise.allSettled([els.cmpVideoOrig.play(), els.cmpVideoOut.play()])
    .then(() => { els.cmpPlayIcon.textContent = '❚❚'; })
    .catch(() => { /* autoplay blocked, leave paused */ });
}

function onCmpPlay() {
  cmpSyncing = true;
  const master = els.cmpVideoOut;
  const slave = els.cmpVideoOrig;
  if (master.paused && !slave.paused) slave.pause();
  if (!master.paused && slave.paused && Math.abs(slave.currentTime - master.currentTime) > 0.15) {
    slave.currentTime = master.currentTime;
  }
  if (!master.paused && slave.paused) slave.play().catch(() => {});
  els.cmpPlayIcon.textContent = master.paused ? '▶' : '❚❚';
  setTimeout(() => { cmpSyncing = false; }, 80);
}

function onCmpPause() {
  els.cmpPlayIcon.textContent = '▶';
  // 让另一侧也暂停（除非 slider 拖拽中）
  if (cmpSyncing) return;
  cmpSyncing = true;
  els.cmpVideoOrig.pause();
  els.cmpVideoOut.pause();
  setTimeout(() => { cmpSyncing = false; }, 80);
}

function onCmpEnded() {
  els.cmpPlayIcon.textContent = '▶';
}

function fmtSec(s) {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return String(m).padStart(2, '0') + ':' + String(r).padStart(2, '0');
}

function updateCmpTime() {
  const v = els.cmpVideoOut;
  if (!v || !v.duration || !isFinite(v.duration)) {
    els.cmpTime.textContent = '00:00 / 00:00';
    return;
  }
  els.cmpTime.textContent = `${fmtSec(v.currentTime)} / ${fmtSec(v.duration)}`;
  if (!cmpSyncing && document.activeElement !== els.cmpSeek) {
    els.cmpSeek.value = String(Math.floor((v.currentTime / v.duration) * 1000));
  }
}

els.cmpPlay.addEventListener('click', () => {
  const v = els.cmpVideoOut;
  if (v.paused) {
    els.cmpVideoOut.play().catch(() => {});
    els.cmpVideoOrig.play().catch(() => {});
  } else {
    els.cmpVideoOut.pause();
    els.cmpVideoOrig.pause();
  }
  els.cmpPlayIcon.textContent = v.paused ? '▶' : '❚❚';
});

els.cmpSeek.addEventListener('input', () => {
  cmpSyncing = true;
  const v = els.cmpVideoOut;
  if (!v.duration || !isFinite(v.duration)) {
    cmpSyncing = false;
    return;
  }
  const t = (Number(els.cmpSeek.value) / 1000) * v.duration;
  els.cmpVideoOut.currentTime = t;
  els.cmpVideoOrig.currentTime = t;
  setTimeout(() => { cmpSyncing = false; }, 50);
});

function closePreview() {
  cmpReset();
  els.cmpImgOrig.removeAttribute('src');
  els.cmpImgOut.removeAttribute('src');
  els.previewModal.classList.add('hidden');
}
els.previewModal.addEventListener('click', e => {
  if (e.target.dataset.close !== undefined) closePreview();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !els.previewModal.classList.contains('hidden')) closePreview();
});

// =========================================================
// 历史
// =========================================================
async function loadHistory() {
  try {
    const res = await fetch('/api/history');
    if (!res.ok) return;
    const list = await res.json();
    state.historyList = list || [];
    renderHistory(list);
    if (els.historyCount) {
      const n = (list || []).length;
      els.historyCount.textContent = n;
      els.historyCount.setAttribute('data-zero', n === 0 ? '1' : '0');
    }
    if (els.btnHistorySaveAll) {
      els.btnHistorySaveAll.disabled = !(list && list.length > 0);
      els.btnHistorySaveAll.innerHTML = `<span class="ico">⤓</span> 全部下载 (${(list||[]).length})`;
    }
  } catch (e) { console.error(e); }
}

function fmtSize(bytes) {
  if (!bytes || bytes <= 0) return '—';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}
function fmtDur(sec) {
  if (!sec || sec <= 0) return '—';
  const s = Math.round(sec), m = Math.floor(s / 60), r = s % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
}
function fmtRes(w, h) {
  if (!w || !h) return '—';
  return `${w}×${h}`;
}
const METHOD_LABEL = { ffmpeg: 'ffmpeg 高清（lanczos）', realesrgan: 'Real-ESRGAN（AI）' };

function renderHistory(list) {
  els.historyList.innerHTML = '';
  if (!list || list.length === 0) {
    els.historyEmpty.classList.remove('hidden');
    return;
  }
  els.historyEmpty.classList.add('hidden');
  list.forEach(item => {
    const li = document.createElement('li');
    li.className = 'history-item history-item-rich';
    const time = new Date(item.processedAt * 1000).toLocaleString('zh-CN', { hour12: false });
    const kindLabel = item.kind === 'image' ? '图片' : '视频';
    const scale = item.scale || 2;
    const methodLabel = METHOD_LABEL[item.method] || item.method || 'ffmpeg';
    const origW = item.originalWidth || 0, origH = item.originalHeight || 0;
    const outW = item.width || 0, outH = item.height || 0;
    const origSize = item.originalSize || 0, outSize = item.size || 0;
    const origDur = item.originalDuration || 0, outDur = item.duration || 0;
    // 倍率箭头：720x1280 → 1440x2560（2x）
    const resChanged = origW && outW && (origW !== outW || origH !== outH);
    const sizeChanged = origSize && outSize && Math.abs(origSize - outSize) > 1024;
    const durChanged = origDur && outDur && Math.abs(origDur - outDur) > 0.1;

    li.innerHTML = `
      <div class="history-thumb">
        <img src="${item.thumbUrl}" alt="" loading="lazy">
      </div>
      <div class="history-meta">
        <div class="name">${escapeHtml(item.originalName)}</div>
        <div class="info">${kindLabel} · ${scale}x · ${methodLabel} · ${time}</div>
        <div class="cmp-grid">
          <div class="cmp-row">
            <span class="cmp-key">原始</span>
            <span class="cmp-val">${escapeHtml(item.originalName)}</span>
          </div>
          <div class="cmp-row cmp-row-after">
            <span class="cmp-key">输出</span>
            <span class="cmp-val">${escapeHtml(item.outputName || (item.originalName.replace(/(\.\w+)?$/, '_超分$1') || '未命名'))}</span>
          </div>
          <div class="cmp-row">
            <span class="cmp-key">分辨率</span>
            <span class="cmp-val">
              ${resChanged
                ? `<span class="strikethrough">${fmtRes(origW, origH)}</span> <span class="cmp-arrow">→</span> <span class="badge-ok">${fmtRes(outW, outH)}</span>`
                : `<span class="badge-ok">${fmtRes(outW, outH)}</span>`}
            </span>
          </div>
          ${item.kind === 'video' ? `
          <div class="cmp-row">
            <span class="cmp-key">时长</span>
            <span class="cmp-val">
              ${durChanged
                ? `<span class="strikethrough">${fmtDur(origDur)}</span> → ${fmtDur(outDur)}`
                : `${fmtDur(outDur)}`}
            </span>
          </div>` : ''}
          <div class="cmp-row">
            <span class="cmp-key">大小</span>
            <span class="cmp-val">
              ${sizeChanged
                ? `<span class="strikethrough">${fmtSize(origSize)}</span> <span class="cmp-arrow">→</span> <span class="badge-ok">${fmtSize(outSize)}</span>`
                : `<span class="badge-ok">${fmtSize(outSize)}</span>`}
            </span>
          </div>
          <div class="cmp-row">
            <span class="cmp-key">算法</span>
            <span class="cmp-val">${escapeHtml(methodLabel)}</span>
          </div>
          <div class="cmp-row">
            <span class="cmp-key">倍率</span>
            <span class="cmp-val"><span class="badge-ok">${scale}x</span></span>
          </div>
        </div>
      </div>
      <div class="history-actions">
        <button data-act="preview" data-id="${item.id}" title="对比预览"><span class="ico">👁</span></button>
        <button data-act="save" data-id="${item.id}" title="保存到…"><span class="ico">⤓</span></button>
        <button data-act="del" data-id="${item.id}" title="删除" class="danger"><span class="ico">🗑</span></button>
      </div>
    `;
    li.querySelector('[data-act=preview]').addEventListener('click', () => openPreview(item.id, 'history', item.kind));
    li.querySelector('[data-act=save]').addEventListener('click', () => doSaveAs({ id: item.id, filename: item.originalName }));
    li.querySelector('[data-act=del]').addEventListener('click', () => deleteHistoryItem(item));
    els.historyList.appendChild(li);
  });
}
async function deleteHistoryItem(item) {
  if (!confirm(`删除 "${item.originalName}" 的处理记录？源文件也会被删除。`)) return;
  try {
    const res = await fetch(`/api/history/${item.id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('删除失败');
    toast('已删除', 'ok');
    loadHistory();
  } catch (e) { toast(e.message, 'err'); }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// =========================================================
// 后端状态
// =========================================================
fetch('/api/backend').then(r => r.json()).then(d => {
  if (!d.realesrgan) {
    els.backendPill.textContent = 'ffmpeg 模式 · Real-ESRGAN 未安装';
  } else {
    els.backendPill.textContent = 'Real-ESRGAN AI 已就绪';
  }
  // 若选了 realesrgan 但不可用，回退 ffmpeg
  if (els.methodSel.value === 'realesrgan' && !d.realesrgan) {
    els.methodSel.value = 'ffmpeg';
  }
  // 灰显 realesrgan 选项
  for (const opt of els.methodSel.options) {
    if (opt.value === 'realesrgan') {
      opt.disabled = !d.realesrgan;
      opt.textContent = d.realesrgan ? 'Real-ESRGAN（AI）' : 'Real-ESRGAN（需下载）';
    }
  }
}).catch(() => {});

// =========================================================
// Init
// =========================================================
loadHistory();
showView('home');
renderQueue();
