'use strict';
/**
 * Astraea shared frontend utilities.
 * Served by all Astraea apps at /static/astraea/astraea.js
 * Load BEFORE the jurisdiction-specific app.js.
 */
(function (global) {

  // ---- HTML escaping ----
  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ---- Answer rendering ----
  function renderAnswer(text) {
    const idx = text.lastIndexOf('\n\nSources:');
    if (idx !== -1) text = text.substring(0, idx);
    text = escapeHtml(text.trim());

    const html = text.split(/\n{2,}/).map(para => {
      if (/^---+$/.test(para.trim())) return '<hr>';
      const lines = para.split('\n');

      const h = lines[0].match(/^(#{1,4}) (.+)/);
      if (h) {
        const level = Math.min(h[1].length + 2, 6);
        return `<h${level}>${h[2]}</h${level}>`;
      }

      if (lines.some(l => /^[-*] /.test(l.trim()))) {
        const items = []; let cur = null;
        for (const line of lines) {
          if (/^[-*] /.test(line.trim())) {
            if (cur !== null) items.push(cur);
            cur = line.trim().replace(/^[-*] /, '').replace(/  $/, '');
          } else if (cur !== null && line.trim()) {
            cur += ' ' + line.trim();
          }
        }
        if (cur !== null) items.push(cur);
        return `<ul>${items.map(t => `<li>${t}</li>`).join('')}</ul>`;
      }

      if (lines.some(l => /^\d+\. /.test(l.trim()))) {
        const items = []; let cur = null;
        for (const line of lines) {
          const m = line.trim().match(/^(\d+)\. (.*)/);
          if (m) { if (cur) items.push(cur); cur = { num: m[1], text: m[2].replace(/  $/, '') }; }
          else if (cur && line.trim()) cur.text += ' ' + line.trim();
        }
        if (cur) items.push(cur);
        return `<ol>${items.map(it => `<li value="${it.num}">${it.text}</li>`).join('')}</ol>`;
      }

      const tableLines = lines.filter(l => /^\|/.test(l.trim()));
      if (tableLines.length >= 2) {
        const sepIdx = tableLines.findIndex(l => /^\|[\s\-|:]+\|/.test(l.trim()) && !/[a-zA-Z0-9]/.test(l));
        if (sepIdx === 1) {
          const parseRow = row => row.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
          const headers = parseRow(tableLines[0]);
          const dataRows = tableLines.slice(sepIdx + 1);
          const thead = `<thead><tr>${headers.map(hh => `<th>${hh}</th>`).join('')}</tr></thead>`;
          const tbody = `<tbody>${dataRows.map(r => `<tr>${parseRow(r).map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>`;
          return `<table class="answer-table">${thead}${tbody}</table>`;
        }
      }

      return `<p>${lines.map(l => l.replace(/  $/, '')).join('<br>')}</p>`;
    }).join('');

    return html
      .replace(/\[S(\d+)\]/g, '<a href="#ctx-S$1" class="citation-link" data-source="S$1">[S$1]</a>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\b(https?:\/\/[^\s<>"]+)/g, url => `<a href="${url}" target="_blank" rel="noopener">${url}</a>`);
  }

  // ---- Citation link click - delegated, auto-registered ----
  // Scopes to .compare-col if present so compare mode works correctly.
  document.addEventListener('click', e => {
    const link = e.target.closest('.citation-link');
    if (!link) return;
    e.preventDefault();
    const src = link.dataset.source;
    const scope = link.closest('.compare-col') || document;
    const card = scope.querySelector(`#ctx-${CSS.escape(src)}`);
    if (!card) return;
    const det = card.closest('details');
    if (det && !det.open) det.open = true;
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.remove('citation-highlight');
    void card.offsetWidth;
    card.classList.add('citation-highlight');
    setTimeout(() => card.classList.remove('citation-highlight'), 2500);
  });

  // ---- Source cards ----
  // opts: {
  //   legislationGroupLabel: string,  default 'Relevant legislation'
  //   decisionGroupLabel: string,     default 'Decisions'
  //   decisionLabel: string,          default 'Decision'
  //   showLegToggle: bool,            default false - adds leg-sec-toggle + data-section
  // }
  function renderSources(sources, legislation, opts) {
    opts = opts || {};
    const sourcesList = document.getElementById('sources-list');
    const sourcesSection = document.getElementById('sources-section');
    if (!sourcesList || !sourcesSection) return;

    const hasLeg = legislation && legislation.length > 0;
    const hasDec = sources && sources.length > 0;
    if (!hasLeg && !hasDec) { sourcesSection.classList.remove('visible'); return; }

    const legGroupLabel = opts.legislationGroupLabel || 'Relevant legislation';
    const decGroupLabel = opts.decisionGroupLabel || 'Decisions';
    const decLabel = opts.decisionLabel || 'Decision';

    let html = '';
    if (hasLeg) {
      if (hasDec) html += `<div class="sources-group-label">${legGroupLabel}</div>`;
      html += legislation.map(s => {
        const url = (s.url || '').startsWith('https://') ? s.url : '#';
        const secMatch = (s.case_id || '').match(/\/s(\d+[A-Z]?)$/i);
        const secNum = secMatch ? secMatch[1] : '';
        const dataAttr = (secNum && opts.showLegToggle) ? ` data-section="${escapeHtml(secNum)}"` : '';
        const spanClass = opts.showLegToggle
          ? 'source-num source-num--leg leg-sec-toggle'
          : 'source-num source-num--leg';
        return `<div class="source-card source-card--leg"><span class="${spanClass}"${dataAttr} title="Show decisions citing this section">&sect;</span><div class="source-info"><a class="source-title" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.title || s.case_id)}</a></div></div>`;
      }).join('');
    }
    if (hasDec) {
      if (hasLeg) html += `<div class="sources-group-label">${decGroupLabel}</div>`;
      html += sources.map((s, i) => {
        const label = s.date
          ? `${s.court_name || decLabel} - ${s.date}`
          : (s.court_name || decLabel);
        const url = (s.url || '').startsWith('https://') ? s.url : '#';
        return `<div class="source-card"><span class="source-num">S${i + 1}</span><div class="source-info"><a class="source-title" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a></div></div>`;
      }).join('');
    }
    sourcesList.innerHTML = html;
    sourcesSection.classList.add('visible');
  }

  // ---- Confidence badge ----
  function renderConfidence(ev, resultCard) {
    const existing = document.getElementById('confidence-badge');
    if (existing) existing.remove();
    if (!ev || !ev.level) return;
    const badge = document.createElement('div');
    badge.id = 'confidence-badge';
    badge.className = `confidence-badge confidence-${ev.level}`;
    const icons = { high: '●', medium: '◑', low: '○' };
    badge.innerHTML = `<span class="confidence-icon">${icons[ev.level] || '●'}</span> <span class="confidence-msg">${escapeHtml(ev.message)}</span>`;
    const container = resultCard || document.getElementById('result-card');
    if (!container) return;
    const aiWarning = container.querySelector('.ai-warning');
    if (aiWarning) container.insertBefore(badge, aiWarning);
    else container.prepend(badge);
  }

  // ---- SSE stream reader ----
  // Reads an SSE response and calls onEvent(parsedEvent) for each data frame.
  // Throws on connection loss so callers can catch and show an error.
  async function streamEvents(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + 2);
        if (!raw.startsWith('data: ')) continue;
        let ev;
        try { ev = JSON.parse(raw.slice(6)); } catch (_) { continue; }
        onEvent(ev);
      }
    }
  }

  // ---- Token loader ----
  async function loadToken() {
    try {
      const r = await fetch('/token');
      return (await r.json()).token || '';
    } catch (_) { return ''; }
  }

  // ---- Queue status ----
  async function pollQueue(queueNoticeEl) {
    try {
      const r = await fetch('/health');
      if (!r.ok) return;
      const d = await r.json();
      if (!queueNoticeEl) return;
      const waiting = d.waiting || 0;
      if (waiting > 0) {
        queueNoticeEl.textContent = `${waiting} ${waiting === 1 ? 'person' : 'people'} waiting - estimated wait ~${d.estimated_wait_seconds || 0}s`;
        queueNoticeEl.classList.add('visible');
      } else {
        queueNoticeEl.classList.remove('visible');
      }
    } catch (_) {}
  }

  // ---- Full feedback save ----
  async function saveFullFeedback(payload, rating, comment, isDebug, apiToken) {
    try {
      await fetch('/feedback/full', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiToken },
        body: JSON.stringify({ ...payload, rating, comment: comment || '', is_debug: isDebug || false }),
      });
    } catch (_) {}
  }

  // ---- Cheat codes (bottom-left floating button + panel) ----
  const _CHEAT_STYLES = `
.astraea-cheat-btn{position:fixed;bottom:1.25rem;left:1.25rem;width:42px;height:42px;border-radius:50%;border:none;background:#374151;color:#e5e7eb;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 10px rgba(0,0,0,.35);z-index:900;transition:background .15s;}
.astraea-cheat-btn:hover{background:#4b5563;box-shadow:0 2px 14px rgba(250,204,21,.45);}
.astraea-cheat-btn.active{background:#7c3aed;}
.astraea-cheat-panel{position:fixed;bottom:4.8rem;left:1.25rem;width:340px;background:#1f2937;border:1px solid #374151;border-radius:10px;padding:1rem;z-index:901;box-shadow:0 8px 30px rgba(0,0,0,.5);}
.astraea-cheat-panel.hidden{display:none;}
.astraea-cheat-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:.5rem;}
.astraea-cheat-hdr h3{margin:0;font-size:.9rem;color:#f3f4f6;font-weight:600;}
.astraea-cheat-x{background:none;border:none;color:#9ca3af;cursor:pointer;font-size:1.1rem;line-height:1;padding:0;}
.astraea-cheat-x:hover{color:#e5e7eb;}
.astraea-cheat-hint{font-size:.75rem;color:#9ca3af;margin:0 0 .75rem;line-height:1.4;}
.astraea-cheat-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px;}
.astraea-cheat-item{border-radius:6px;cursor:pointer;padding:.45rem .6rem;transition:background .1s;}
.astraea-cheat-item:hover{background:#374151;}
.astraea-cheat-row{display:flex;align-items:baseline;gap:.6rem;}
.astraea-cheat-cmd{font-family:monospace;font-size:.8rem;color:#a78bfa;font-weight:700;flex-shrink:0;min-width:80px;}
.astraea-cheat-desc{font-size:.78rem;color:#d1d5db;line-height:1.3;}
.astraea-cheat-eg{font-size:.72rem;color:#6b7280;font-style:italic;margin-top:.25rem;padding-left:0;display:none;line-height:1.4;}
.astraea-cheat-item:hover .astraea-cheat-eg{display:block;}
`;

  const _CHEAT_CODES = [
    { cmd: '/search',    desc: 'Find cases without generating an answer',          eg: '/search hardship fixed term house purchase foreseeable' },
    { cmd: '/case',      desc: 'Focus on Tribunal decisions and outcomes',         eg: '/case Bond deductions for fair wear and tear' },
    { cmd: '/checklist', desc: 'Step-by-step action list',                         eg: '/checklist My landlord won\'t fix the mould' },
    { cmd: '/landlord',  desc: 'Answer from the landlord\'s perspective',          eg: '/landlord My tenant hasn\'t paid rent in 3 weeks' },
    { cmd: '/pitfalls',  desc: 'Common mistakes and risks to avoid',               eg: '/pitfalls I want to end my fixed-term tenancy early' },
  ];

  function initCheatCodes(inputSelector) {
    if (!document.getElementById('astraea-cheat-styles')) {
      const s = document.createElement('style');
      s.id = 'astraea-cheat-styles';
      s.textContent = _CHEAT_STYLES;
      document.head.appendChild(s);
    }

    const btn = document.createElement('button');
    btn.className = 'astraea-cheat-btn';
    btn.title = 'Cheat codes';
    btn.setAttribute('aria-label', 'Show cheat codes');
    btn.innerHTML = '<span style="font-size:1.2rem;line-height:1">&#9889;</span>';

    const itemsHtml = _CHEAT_CODES.map(c =>
      `<li class="astraea-cheat-item" data-cmd="${escapeHtml(c.cmd)}">
        <div class="astraea-cheat-row">
          <span class="astraea-cheat-cmd">${escapeHtml(c.cmd)}</span>
          <span class="astraea-cheat-desc">${escapeHtml(c.desc)}</span>
        </div>
        <div class="astraea-cheat-eg">e.g. ${escapeHtml(c.eg)}</div>
      </li>`
    ).join('');

    const panel = document.createElement('div');
    panel.className = 'astraea-cheat-panel hidden';
    panel.innerHTML =
      '<div class="astraea-cheat-hdr"><h3>&#9889; Cheat codes</h3><button class="astraea-cheat-x" aria-label="Close">&times;</button></div>'
      + '<p class="astraea-cheat-hint">Click to insert into your question. Hover to see an example.</p>'
      + `<ul class="astraea-cheat-list">${itemsHtml}</ul>`;

    document.body.appendChild(btn);
    document.body.appendChild(panel);

    const input = inputSelector ? document.querySelector(inputSelector) : null;

    btn.addEventListener('click', (e) => { e.stopPropagation(); panel.classList.toggle('hidden'); });
    panel.querySelector('.astraea-cheat-x').addEventListener('click', () => panel.classList.add('hidden'));

    panel.querySelectorAll('.astraea-cheat-item').forEach(item => {
      item.addEventListener('click', () => {
        const cmd = item.dataset.cmd + ' ';
        if (input) {
          // replace existing leading command if present, otherwise prepend
          const current = input.value.replace(/^\/\w+\s*/, '');
          input.value = cmd + current;
          input.focus();
          // move cursor to end
          input.setSelectionRange(input.value.length, input.value.length);
        }
        panel.classList.add('hidden');
      });
    });

    document.addEventListener('click', (e) => {
      if (!panel.contains(e.target) && e.target !== btn) panel.classList.add('hidden');
    });
  }

  // ---- User context (localStorage, injected into every request) ----
  const _CTX_STYLES = `
.astraea-ctx-btn{position:fixed;bottom:1.25rem;right:1.25rem;width:42px;height:42px;border-radius:50%;border:none;background:#374151;color:#e5e7eb;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 10px rgba(0,0,0,.35);z-index:900;transition:background .15s;}
.astraea-ctx-btn:hover{background:#4b5563;}
.astraea-ctx-btn.active{background:#1d4ed8;}
.astraea-ctx-panel{position:fixed;bottom:4.8rem;right:1.25rem;width:300px;background:#1f2937;border:1px solid #374151;border-radius:10px;padding:1rem;z-index:901;box-shadow:0 8px 30px rgba(0,0,0,.5);}
.astraea-ctx-panel.hidden{display:none;}
.astraea-ctx-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:.5rem;}
.astraea-ctx-hdr h3{margin:0;font-size:.9rem;color:#f3f4f6;font-weight:600;}
.astraea-ctx-x{background:none;border:none;color:#9ca3af;cursor:pointer;font-size:1.1rem;line-height:1;padding:0;}
.astraea-ctx-x:hover{color:#e5e7eb;}
.astraea-ctx-hint{font-size:.75rem;color:#9ca3af;margin:0 0 .6rem;line-height:1.4;}
.astraea-ctx-panel textarea{width:100%;box-sizing:border-box;background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:.5rem;font-size:.8rem;resize:vertical;min-height:80px;font-family:inherit;}
.astraea-ctx-panel textarea::placeholder{color:#6b7280;}
.astraea-ctx-char{font-size:.7rem;color:#6b7280;text-align:right;margin:.2rem 0 .6rem;}
.astraea-ctx-actions{display:flex;gap:.5rem;justify-content:flex-end;}
.astraea-ctx-actions button{padding:.35rem .75rem;border-radius:5px;border:none;cursor:pointer;font-size:.8rem;font-weight:500;}
.astraea-ctx-clear{background:#374151;color:#e5e7eb;}
.astraea-ctx-clear:hover{background:#4b5563;}
.astraea-ctx-save{background:#1d4ed8;color:#fff;}
.astraea-ctx-save:hover{background:#2563eb;}
`;

  function initUserContext(storageKey) {
    if (!document.getElementById('astraea-ctx-styles')) {
      const s = document.createElement('style');
      s.id = 'astraea-ctx-styles';
      s.textContent = _CTX_STYLES;
      document.head.appendChild(s);
    }

    const btn = document.createElement('button');
    btn.className = 'astraea-ctx-btn';
    btn.title = 'Your context';
    btn.setAttribute('aria-label', 'Set your personal context');
    btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';

    const panel = document.createElement('div');
    panel.className = 'astraea-ctx-panel hidden';
    panel.innerHTML = '<div class="astraea-ctx-hdr"><h3>Your context</h3><button class="astraea-ctx-x" aria-label="Close">&times;</button></div>'
      + '<p class="astraea-ctx-hint">Tell the AI about your situation once. Stored in your browser only - sent with each question.</p>'
      + '<textarea id="astraea-ctx-ta" rows="4" maxlength="500" placeholder="e.g. I am a tenant in Auckland. Periodic tenancy since March 2023. Landlord is a property management company."></textarea>'
      + '<div class="astraea-ctx-char"><span id="astraea-ctx-n">0</span>/500</div>'
      + '<div class="astraea-ctx-actions"><button class="astraea-ctx-clear">Clear</button><button class="astraea-ctx-save">Save</button></div>';

    document.body.appendChild(btn);
    document.body.appendChild(panel);

    const ta = panel.querySelector('#astraea-ctx-ta');
    const counter = panel.querySelector('#astraea-ctx-n');

    function sync() { counter.textContent = ta.value.length; }
    function loadSaved() {
      ta.value = localStorage.getItem(storageKey) || '';
      sync();
      btn.classList.toggle('active', ta.value.length > 0);
    }

    loadSaved();
    ta.addEventListener('input', sync);

    btn.addEventListener('click', (e) => { e.stopPropagation(); loadSaved(); panel.classList.toggle('hidden'); });
    panel.querySelector('.astraea-ctx-x').addEventListener('click', () => panel.classList.add('hidden'));

    panel.querySelector('.astraea-ctx-save').addEventListener('click', () => {
      const val = ta.value.trim();
      val ? localStorage.setItem(storageKey, val) : localStorage.removeItem(storageKey);
      btn.classList.toggle('active', val.length > 0);
      panel.classList.add('hidden');
    });

    panel.querySelector('.astraea-ctx-clear').addEventListener('click', () => { ta.value = ''; sync(); });

    document.addEventListener('click', (e) => {
      if (!panel.contains(e.target) && e.target !== btn) panel.classList.add('hidden');
    });
  }

  function getUserContext(storageKey) {
    return localStorage.getItem(storageKey) || '';
  }

  // ---- Disclaimer modal ----
  function initDisclaimer(storageKey) {
    if (localStorage.getItem(storageKey)) return;
    const modal = document.getElementById('disclaimer-modal');
    const checkbox = document.getElementById('disclaimer-checkbox');
    const agreeBtn = document.getElementById('disclaimer-agree');
    if (!modal || !checkbox || !agreeBtn) return;
    modal.classList.add('visible');
    document.body.classList.add('modal-open');
    checkbox.addEventListener('change', () => { agreeBtn.disabled = !checkbox.checked; });
    agreeBtn.addEventListener('click', () => {
      localStorage.setItem(storageKey, '1');
      modal.classList.remove('visible');
      document.body.classList.remove('modal-open');
    });
  }

  global.Astraea = {
    escapeHtml,
    renderAnswer,
    renderSources,
    renderConfidence,
    streamEvents,
    loadToken,
    pollQueue,
    saveFullFeedback,
    initDisclaimer,
    initUserContext,
    getUserContext,
    initCheatCodes,
  };

})(window);
