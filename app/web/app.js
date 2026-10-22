'use strict';
/* Подбор квартиры — интерфейс СППР. Весь расчёт на сервере (/api/rank), здесь только ввод и отображение. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nf = (d) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: d, minimumFractionDigits: d });
const fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? '—' : nf(d).format(v));
const money = (v) => (v == null ? '—' : v >= 1e6 ? `${fmt(v / 1e6, v >= 1e8 ? 0 : 1)} млн ₸` : `${fmt(v)} ₸`);
const digits = (s) => { const v = String(s || '').replace(/[^\d.,]/g, '').replace(',', '.'); return v ? Number(v) : null; };
const store = {
  get(k, d) { try { const v = localStorage.getItem('apt:' + k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem('apt:' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const S = {
  cfg: null,
  mode: ['sale', 'rent'].includes(new URLSearchParams(location.search).get('mode'))
    ? new URLSearchParams(location.search).get('mode') : store.get('mode', 'sale'),
  dataset: { sale: null, rent: null },
  weights: store.get('weights', { sale: null, rent: null }),
  point: store.get('point', null),
  constraints: store.get('constraints', { sale: {}, rent: {} }),
  missing: 'worst',
  sort: 'wsm',
  sensKey: null,
  sensMethod: 'wsm',
  result: null,
  shown: 15,
  hl: null,
  job: null,
};
const SEARCH_DEFAULTS = {
  sale: { rooms: [2, 3], price_from: 25000000, price_to: 45000000 },
  rent: { rooms: [1, 2], price_from: 150000, price_to: 350000 },
};

/* ---------------- theme ---------------- */
const LIGHT_TO_DARK = {
  '#2a78d6': '#3987e5', '#eb6834': '#d95926', '#1baf7a': '#199e70', '#eda100': '#c98500', '#e87ba4': '#d55181',
  '#6250d6': '#9085e9', '#e34948': '#e66767', '#0b0b0b': '#f0efec', '#52514e': '#c3c2b7', '#e1e0d9': '#2c2c2a',
  '#c3c2b7': '#383835', '#fcfcfb': '#1a1a19',
  // sequential: in dark mode "near zero" recedes into the dark surface
  '#cde2fb': '#0d366b', '#9ec5f4': '#184f95', '#6da7ec': '#256abf', '#3987e5': '#3987e5', '#256abf': '#6da7ec',
  '#184f95': '#9ec5f4', '#104281': '#cde2fb',
};
function isDark() {
  const t = document.documentElement.dataset.theme;
  return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
}
function themed(fig) {
  if (!isDark()) return fig;
  const walk = (o) => {
    if (typeof o === 'string') return LIGHT_TO_DARK[o.toLowerCase()] || o;
    if (Array.isArray(o)) return o.map(walk);
    if (o && typeof o === 'object') { const r = {}; for (const k in o) r[k] = walk(o[k]); return r; }
    return o;
  };
  return walk(fig);
}
$('#theme').addEventListener('click', () => {
  document.documentElement.dataset.theme = isDark() ? 'light' : 'dark';
  store.set('theme', document.documentElement.dataset.theme);
  if (S.result) renderCharts(S.result);
});
{ const t = store.get('theme', null); if (t) document.documentElement.dataset.theme = t; }

/* ---------------- api ---------------- */
async function api(path, body, method) {
  const r = await fetch(path, {
    method: method || (body ? 'POST' : 'GET'),
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `HTTP ${r.status}`);
  return data;
}

/* ---------------- setup ---------------- */
async function init() {
  S.cfg = await api('/api/config');
  for (const m of ['sale', 'rent']) {
    if (!S.weights[m]) S.weights[m] = Object.fromEntries(S.cfg.criteria[m].map((c) => [c.key, c.importance]));
    const own = S.cfg.datasets.find((d) => d.mode === m && !d.sample);
    const any = own || S.cfg.datasets.find((d) => d.mode === m);
    S.dataset[m] = store.get('dataset:' + m, null) || (any && any.id);
    if (!S.cfg.datasets.some((d) => d.id === S.dataset[m])) S.dataset[m] = any ? any.id : null;
  }
  if (!S.point) S.point = { ...S.cfg.places[0] };
  initAI();
  buildPlaces();
  buildPresets();
  initMap();
  setMode(S.mode, true);
}

function setMode(mode, first) {
  S.mode = mode;
  store.set('mode', mode);
  $$('#mode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
  $$('.sale-only').forEach((el) => el.classList.toggle('hidden', mode !== 'sale'));
  const d = SEARCH_DEFAULTS[mode];
  $$('#s-rooms button').forEach((b) => b.classList.toggle('on', d.rooms.includes(+b.dataset.v)));
  $('#s-price-from').value = d.price_from ? fmt(d.price_from) : '';
  $('#s-price-to').value = d.price_to ? fmt(d.price_to) : '';
  S.sensKey = null;
  renderMap.fitted = false;
  buildDatasets();
  buildCriteria();
  loadConstraints();
  if (!first) S.result = null;
  rankSoon(0);
}
$$('#mode button').forEach((b) => b.addEventListener('click', () => b.dataset.mode !== S.mode && setMode(b.dataset.mode)));

function showTab(tab) {
  $$('.tabs button').forEach((x) => x.classList.toggle('on', x.dataset.tab === tab));
  $$('.view').forEach((v) => v.classList.toggle('hidden', v.id !== 'view-' + tab));
  if (tab === 'pick') { map && map.invalidateSize(); if (S.result) renderCharts(S.result); }
  history.replaceState(null, '', tab === 'pick' ? location.pathname : '#' + tab);
}
$$('.tabs button').forEach((b) => b.addEventListener('click', () => { showTab(b.dataset.tab); window.scrollTo({ top: 0 }); }));
if (['calc', 'formal'].includes(location.hash.slice(1))) showTab(location.hash.slice(1));

function buildDatasets() {
  const sel = $('#dataset');
  const list = S.cfg.datasets.filter((d) => d.mode === S.mode);
  sel.innerHTML = list.length ? list.map((d) => {
    const when = new Date(d.created * 1000).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    return `<option value="${esc(d.id)}">${d.sample ? 'Демо-набор' : 'Поиск ' + when} · ${d.count} объявл.</option>`;
  }).join('') : '<option value="">нет данных — запустите поиск</option>';
  if (S.dataset[S.mode]) sel.value = S.dataset[S.mode];
  dsInfo();
  if (!list.length) $('#search-box').open = true;
}
function dsInfo() {
  const d = S.cfg.datasets.find((x) => x.id === S.dataset[S.mode]);
  $('#ds-info').innerHTML = d ? `Источник: <a href="${esc(d.source_url)}" target="_blank" rel="noopener">поиск на Krisha.kz</a>`
    + (d.total_on_site ? ` · на сайте ${fmt(d.total_on_site)} по фильтру` : '') + geoLine(d)
    + (d.sample ? '. Сохранённый снимок — для показа без сети.' : '') : '';
}
function geoLine(d) {
  const g = d.geo || {};
  const names = { krisha: 'точно', house: 'по дому', street: 'по улице', complex: 'по ЖК', district: 'по району', none: 'нет' };
  const parts = Object.keys(names).filter((k) => g[k]).map((k) => `${names[k]} ${g[k]}`);
  return parts.length ? ` · координаты: ${parts.join(', ')}` : ` · с координатами ${d.with_coords} из ${d.count}`;
}
$('#dataset').addEventListener('change', (e) => {
  S.dataset[S.mode] = e.target.value; store.set('dataset:' + S.mode, e.target.value); dsInfo();
  renderMap.fitted = false; rankSoon(0);
});

/* ---------------- criteria and weights ---------------- */
function buildCriteria() {
  const w = S.weights[S.mode];
  $('#crit').innerHTML = S.cfg.criteria[S.mode].map((c) => `
    <div class="crit${w[c.key] > 0 ? '' : ' off'}" data-k="${c.key}">
      <input type="checkbox" ${w[c.key] > 0 ? 'checked' : ''} aria-label="Учитывать «${esc(c.name)}»">
      <div class="name" title="${esc(c.how)}">${esc(c.name)}${S.cfg.ai ? `<button type="button" class="ask" title="Спросить ИИ" data-ask="${esc(`Зачем нужен критерий «${c.name}», как он измеряется и почему он ${c.kind === 'cost' ? '«меньше — лучше»' : '«больше — лучше»'}? Как на рейтинг повлияет его важность сейчас?`)}">?</button>` : ''}
        <span class="kind">${c.kind === 'cost' ? '↓ меньше — лучше' : '↑ больше — лучше'}</span>
        <small>${esc(c.unit)}</small></div>
      <div class="w" id="w-${c.key}">—</div>
      <input type="range" min="0" max="10" step="0.1" value="${w[c.key] || 0}" aria-label="Важность «${esc(c.name)}»">
      ${c.scale ? `<div class="scale">${esc(c.scale)}</div>` : ''}
    </div>`).join('');
  $$('#crit .crit').forEach((row) => {
    const k = row.dataset.k;
    const box = $('input[type=checkbox]', row);
    const rng = $('input[type=range]', row);
    box.addEventListener('change', () => {
      S.weights[S.mode][k] = box.checked ? (Number(rng.value) || 5) : 0;
      rng.value = S.weights[S.mode][k];
      weightsChanged();
    });
    rng.addEventListener('input', () => {
      S.weights[S.mode][k] = Number(rng.value);
      box.checked = Number(rng.value) > 0;
      weightsChanged(true);
    });
  });
  showWeights();
}
function showWeights() {
  const w = S.weights[S.mode];
  const total = Object.values(w).reduce((a, b) => a + Math.max(0, b || 0), 0);
  for (const c of S.cfg.criteria[S.mode]) {
    const v = total > 0 && w[c.key] > 0 ? w[c.key] / total : 0;
    const el = $('#w-' + c.key);
    if (el) el.textContent = v ? (v * 100).toFixed(1) + '%' : '—';
    const row = $(`.crit[data-k="${c.key}"]`);
    if (row) row.classList.toggle('off', !(w[c.key] > 0));
  }
  $('#w-sum').textContent = total > 0 ? '1.00' : '0';
  $$('#presets button').forEach((b) => b.classList.remove('on'));
}
function weightsChanged(sliding) {
  store.set('weights', S.weights);
  showWeights();
  rankSoon(sliding ? 220 : 0);
}
function buildPresets() {
  $('#presets').innerHTML = Object.entries(S.cfg.presets).map(([k, p]) => `<button data-p="${k}">${esc(p.title)}</button>`).join('');
  $$('#presets button').forEach((b) => b.addEventListener('click', () => {
    const p = S.cfg.presets[b.dataset.p][S.mode];
    S.weights[S.mode] = Object.fromEntries(S.cfg.criteria[S.mode].map((c) => [c.key, p[c.key] || 0]));
    buildCriteria();
    store.set('weights', S.weights);
    $$('#presets button').forEach((x) => x.classList.toggle('on', x === b));
    rankSoon(0);
  }));
}

/* ---------------- constraints ---------------- */
const C_FIELDS = [['price_max', '#c-price'], ['area_min', '#c-area'], ['year_min', '#c-year'], ['distance_max', '#c-dist']];
function loadConstraints() {
  const c = S.constraints[S.mode] || {};
  for (const [k, sel] of C_FIELDS) $(sel).value = c[k] ? fmt(c[k], k === 'distance_max' ? 1 : 0) : '';
  $('#c-cond').value = c.condition_min || '';
  $('#c-not-first').checked = !!c.not_first;
  $('#c-not-last').checked = !!c.not_last;
  $$('#c-rooms button').forEach((b) => b.classList.toggle('on', (c.rooms || []).includes(+b.dataset.v)));
}
function readConstraints() {
  const c = {};
  for (const [k, sel] of C_FIELDS) { const v = digits($(sel).value); if (v) c[k] = v; }
  if ($('#c-cond').value) c.condition_min = Number($('#c-cond').value);
  if ($('#c-not-first').checked) c.not_first = true;
  if ($('#c-not-last').checked) c.not_last = true;
  const rooms = $$('#c-rooms button.on').map((b) => +b.dataset.v);
  if (rooms.length) c.rooms = rooms;
  S.constraints[S.mode] = c;
  store.set('constraints', S.constraints);
  return c;
}
for (const [, sel] of C_FIELDS) $(sel).addEventListener('change', () => rankSoon(0));
['#c-cond', '#c-not-first', '#c-not-last', '#missing'].forEach((s) => $(s).addEventListener('change', () => rankSoon(0)));
function chipToggle(container, after) {
  $$(container + ' button').forEach((b) => b.addEventListener('click', () => { b.classList.toggle('on'); after && after(); }));
}
chipToggle('#c-rooms', () => rankSoon(0));
chipToggle('#s-rooms');

function segment(sel, onPick) {
  $$(sel + ' button').forEach((b) => b.addEventListener('click', () => {
    $$(sel + ' button').forEach((x) => x.classList.toggle('on', x === b));
    onPick(b.dataset.v);
  }));
}
segment('#sort', (v) => { S.sort = v; if (S.result) { renderList(S.result); renderVerdict(S.result); renderMap(S.result); } });
segment('#sens-method', (v) => { S.sensMethod = v; rankSoon(0); });
$('#sens-key').addEventListener('change', (e) => { S.sensKey = e.target.value; rankSoon(0); });

/* ---------------- ranking ---------------- */
let rankTimer = null;
let rankSeq = 0;
function rankSoon(ms) {
  clearTimeout(rankTimer);
  rankTimer = setTimeout(rankNow, ms);
}
async function rankNow() {
  const ds = S.dataset[S.mode];
  if (!ds) { showEmpty(); return; }
  const seq = ++rankSeq;
  $('#main').classList.add('busy');
  try {
    const r = await api('/api/rank', {
      dataset: ds,
      weights: S.weights[S.mode],
      point: { lat: S.point.lat, lon: S.point.lon },
      point_name: S.point.name || '',
      constraints: readConstraints(),
      missing: $('#missing').value,
      sens_key: S.sensKey,
      sens_method: S.sensMethod,
      top: 15,
    });
    if (seq !== rankSeq) return;                // a newer request is on its way
    S.result = r;
    S.shown = 15;
    render(r);
  } catch (e) {
    if (seq === rankSeq) alertBox([e.message], true);
  } finally {
    if (seq === rankSeq) $('#main').classList.remove('busy');
  }
}
function showEmpty() {
  alertBox(['Нет загруженных объявлений для этого режима. Откройте «Новый поиск на Krisha.kz» слева и нажмите «Найти и загрузить».']);
  ['#stats', '#verdict', '#list', '#view-calc', '#view-formal'].forEach((s) => { $(s).innerHTML = ''; });
  ['scores', 'contrib', 'ranks', 'heat', 'sens'].forEach((k) => Plotly.purge('ch-' + k));
  if (markers) markers.clearLayers();
}
function alertBox(lines, err) {
  const el = $('#alert');
  if (!lines || !lines.length) { el.classList.add('hidden'); return; }
  el.className = 'alert' + (err ? ' err' : '');
  el.innerHTML = lines.length === 1 ? esc(lines[0]) : `<ul>${lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>`;
}

function ordered(r) {
  const key = S.sort + '_rank';
  return [...(r.listings || [])].sort((a, b) => a[key] - b[key] || a.wsm_rank - b.wsm_rank);
}

function render(r) {
  const v = r.validation || { errors: [], warnings: [] };
  const notes = [...v.errors];
  if (!v.errors.length) {
    notes.push(...v.warnings);
    const m = r.missing || {};
    if (m.filled && m.filled.length) {
      const by = Object.entries(m.by_criterion || {}).map(([k, n]) => `${k} — ${n}`).join(', ');
      notes.push(`В объявлениях не указаны некоторые параметры (${by}). ${m.strategy === 'median' ? 'Подставлена медиана' : 'Подставлено худшее значение'} — такие клетки отмечены в «Расчёте».`);
    }
    if (m.dropped && m.dropped.length) notes.push(`Исключено из-за пропусков: ${m.dropped.length}.`);
    if (m.dropped_criteria && m.dropped_criteria.length) notes.push(`Критерий без данных отключён: ${m.dropped_criteria.join(', ')}.`);
  }
  alertBox(notes, v.errors.length > 0);
  renderStats(r);
  if (!v.ok) {
    ['#verdict', '#list', '#view-calc', '#view-formal'].forEach((s) => { $(s).innerHTML = ''; });
    ['scores', 'contrib', 'ranks', 'heat', 'sens'].forEach((k) => Plotly.purge('ch-' + k));
    renderCalc(r);
    stepAsks();
    return;
  }
  renderVerdict(r);
  renderList(r);
  renderMap(r);
  renderCharts(r);
  renderCalc(r);
  renderFormal(r);
  stepAsks();
}

// "explain this step" next to every heading of the 4.2 and 4.1 tabs
function stepAsks() {
  if (!S.cfg.ai) return;
  $$('#view-calc .step > h2').forEach((h) => {
    const t = h.textContent.replace(/^\d+/, '').trim();
    h.insertAdjacentHTML('beforeend', `<button type="button" class="ask-link" style="margin-left:auto" data-ask="${esc(`Объясни шаг «${t}» простыми словами на текущих данных: что здесь происходит, зачем это нужно и как читать таблицу.`)}">Объяснить шаг</button>`);
  });
  $$('#view-formal .step > h2').forEach((h) => {
    const t = h.textContent.replace(/·.*$/, '').trim();
    h.insertAdjacentHTML('beforeend', `<button type="button" class="ask-link" style="margin-left:auto" data-ask="${esc(`Объясни раздел формализации 4.1 «${t}»: что это значит в теории (лекция 6) и почему здесь написано именно так.`)}">Объяснить</button>`);
  });
}

function renderStats(r) {
  const n = (r.listings || []).length;
  const ex = (r.excluded || []).length;
  const c = r.comparison;
  $('#stats').innerHTML = `
    <div class="stat"><div class="k">Объявлений в наборе</div><div class="v">${fmt(r.total || 0)}</div>
      <div class="d">${r.dataset ? (r.dataset.sample ? 'демо-набор' : 'последний поиск') : ''}</div></div>
    <div class="stat"><div class="k">Альтернатив после ограничений</div><div class="v">${fmt(n)}</div>
      <div class="d">${ex ? `исключено ${ex}` : 'ограничения никого не исключили'}${(r.missing && r.missing.dropped.length) ? `, из-за пропусков ${r.missing.dropped.length}` : ''}</div></div>
    <div class="stat"><div class="k">Критериев в модели</div><div class="v">${(r.criteria || []).length}</div>
      <div class="d">сумма весов = 1</div></div>
    <div class="stat"><div class="k">Согласие WSM и TOPSIS</div><div class="v">${c ? 'ρ = ' + fmt(c.spearman, 2) : '—'}</div>
      <div class="d">${c ? (c.same_winner ? 'лидер одинаковый' : 'лидеры разные') : ''}</div></div>`;
}

function renderVerdict(r) {
  const best = ordered(r)[0];
  if (!best) { $('#verdict').innerHTML = ''; return; }
  const s = r.sensitivity;
  const th = s && s.thresholds.length ? s.thresholds : null;
  const label = (code) => (r.labels && r.labels[code]) || code;
  $('#verdict').innerHTML = `
    <h3>Лучший вариант по ${S.sort.toUpperCase()}</h3>
    <div class="win"><a href="${esc(best.url)}" target="_blank" rel="noopener">${esc(best.label)}</a></div>
    <div class="muted">${esc(best.address || '')}</div>
    <div class="tags" style="margin-top:8px">${best.strengths.map((x) => `<span class="tag good">${esc(x)}</span>`).join('')}
      ${best.weaknesses.map((x) => `<span class="tag bad">${esc(x)}</span>`).join('')}</div>
    <div class="qa">${r.comparison.answers.map((a) => `<p><b>${esc(a.q)}</b> ${esc(a.a)}</p>`).join('')}</div>
    <div class="qa"><p><b>А WPM?</b> ${(() => {
      const m = r.methods; const lw = m.leaders.wpm[0];
      return (lw === m.leaders.wsm[0] ? `Тоже ставит первой ${esc(label(lw))}` : `Ставит первой ${esc(label(lw))}, а не лидера WSM`)
        + ` — взвешенное произведение не прощает провалов по одному критерию. Согласие с WSM ρ = ${fmt(m.spearman['wsm~wpm'], 2)}, с TOPSIS ρ = ${fmt(m.spearman['topsis~wpm'], 2)}.`
        + (m.same_leader ? ' Все три метода сошлись — выбор надёжный.' : '');
    })()}</p></div>
    <div class="qa"><p><b>Устойчивость.</b> ${th
      ? `Если вес «${esc(s.name)}» изменить с ${fmt(s.current, 2)} до ${fmt(th[0].weight, 2)}, лидер по ${s.method.toUpperCase()} сменится: ${esc(label(th[0].from))} → ${esc(label(th[0].to))}.`
      : `При любом весе «${esc(s.name)}» от 0 до 1 лидер по ${s.method.toUpperCase()} не меняется.`}</p>
      ${r.dominance.length ? `<p><b>Доминирование.</b> ${r.dominance.length} из ${r.listings.length} вариантов хуже другого по всем критериям сразу — при любых весах они не станут первыми.</p>` : ''}
      ${S.cfg.ai ? `<p><button type="button" class="ask-link" data-ask="${esc(`Объясни простыми словами этот результат: почему ${best.code} первая по ${S.sort.toUpperCase()}, что значат ответы на три вопроса, устойчивость и доминирование.`)}">Объяснить результат простыми словами →</button></p>` : ''}</div>`;
}

function facts(x) {
  const f = [];
  if (x.floor) f.push(`этаж ${x.floor}${x.floors ? '/' + x.floors : ''}`);
  if (x.year) f.push(`${x.year} г.`);
  if (x.house_type) f.push(x.house_type);
  if (x.condition) f.push(x.condition);
  if (x.furniture) f.push(x.furniture === 'без мебели' ? 'без мебели' : 'мебель: ' + x.furniture);
  if (x.values.distance != null && !x.imputed.includes('distance')) {
    const approx = { street: ' (по улице)', complex: ' (по ЖК)', district: ' (≈ по району)' }[x.geo] || '';
    f.push(`${fmt(x.values.distance, 1)} км до точки${approx}`);
  }
  return f;
}

function renderList(r) {
  const rows = ordered(r);
  const names = Object.fromEntries((r.criteria || []).map((c) => [c.key, c.name]));
  const key = S.sort;
  const top = rows.length ? rows[0][key] : 1;
  $('#list').innerHTML = rows.slice(0, S.shown).map((x) => {
    const place = x[key + '_rank'];
    const ppm = x.price && x.area ? x.price / x.area : null;
    return `<li class="item${S.hl === x.code ? ' hl' : ''}" id="it-${x.code}" data-code="${x.code}">
      <div class="rank"><b>${place}</b><span>${esc(x.code)}</span></div>
      ${x.photo ? `<img class="photo" loading="lazy" referrerpolicy="no-referrer" src="${esc(x.photo)}" alt="">` : '<div class="photo"></div>'}
      <div>
        <h3><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title || x.label)}</a></h3>
        <div class="addr">${esc(x.address || '')}${x.complex ? ' · ЖК ' + esc(x.complex) : ''}</div>
        <div class="facts">${facts(x).map((f) => `<span>${esc(f)}</span>`).join('')}</div>
        <div class="tags">
          ${x.strengths.map((s) => `<span class="tag good">+ ${esc(s)}</span>`).join('')}
          ${x.weaknesses.map((s) => `<span class="tag bad">− ${esc(s)}</span>`).join('')}
          ${x.imputed.length ? `<span class="tag warn">не указано: ${x.imputed.map((k) => esc((names[k] || k).toLowerCase())).join(', ')}</span>` : ''}
          ${x.dominated_by ? `<span class="tag warn">доминируется ${esc(x.dominated_by)}</span>` : ''}
        </div>
        ${x.consequences.length ? `<ul class="cons">${x.consequences.slice(0, 3).map((c) => `<li>${esc(c)}</li>`).join('')}</ul>` : ''}
      </div>
      <div class="price-col">
        <div class="price">${money(x.price)}</div>
        <div class="ppm">${S.mode === 'rent' ? 'в месяц' : ppm ? money(Math.round(ppm)) + ' за м²' : ''}</div>
        <div class="score">${key.toUpperCase()} <b>${fmt(x[key], 4)}</b>
          <div class="meter"><i style="width:${Math.max(2, (x[key] / (top || 1)) * 100).toFixed(1)}%"></i></div></div>
        <div class="score">WSM #${x.wsm_rank} · TOPSIS #${x.topsis_rank} · WPM #${x.wpm_rank}</div>
        ${S.cfg.ai ? `<div class="score"><button type="button" class="ask-link" data-ask="${esc(`Почему ${x.code} на ${place}-м месте по ${key.toUpperCase()}? Разбери по критериям: что тянет вверх, что вниз.`)}">Почему ${place}-е место?</button></div>` : ''}
      </div>
    </li>`;
  }).join('');
  $('#more').classList.toggle('hidden', rows.length <= S.shown);
  $('#list-note').textContent = `${rows.length} вариантов, сортировка по ${key.toUpperCase()}`;
  $$('#list .item').forEach((li) => li.addEventListener('mouseenter', () => highlight(li.dataset.code, false)));
}
$('#more').addEventListener('click', () => { S.shown += 15; renderList(S.result); });

/* ---------------- map ---------------- */
let map = null;
let markers = null;
let home = null;
const markerByCode = {};
function initMap() {
  map = L.map('map', { zoomControl: true, attributionControl: true }).setView([S.cfg.center.lat, S.cfg.center.lon], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  markers = L.layerGroup().addTo(map);
  home = L.marker([S.point.lat, S.point.lon], {
    draggable: true, zIndexOffset: 1000, title: 'Точка притяжения',
    icon: L.divIcon({ className: '', html: '<div class="home"><svg viewBox="0 0 24 24"><path d="M3 11 12 3l9 8v10h-6v-6H9v6H3z"/></svg></div>', iconSize: [30, 30], iconAnchor: [15, 15] }),
  }).addTo(map);
  home.on('dragend', () => { const p = home.getLatLng(); setPoint({ lat: p.lat, lon: p.lng, name: 'Точка на карте' }); });
  map.on('click', (e) => setPoint({ lat: e.latlng.lat, lon: e.latlng.lng, name: 'Точка на карте' }));
}
function setPoint(p) {
  S.point = p;
  store.set('point', p);
  home.setLatLng([p.lat, p.lon]);
  buildPlaces();
  rankSoon(0);
}
function buildPlaces() {
  const sel = $('#place');
  const known = S.cfg.places.findIndex((x) => Math.abs(x.lat - S.point.lat) < 1e-6 && Math.abs(x.lon - S.point.lon) < 1e-6);
  sel.innerHTML = S.cfg.places.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join('')
    + (known < 0 ? `<option value="custom" selected>Точка на карте (${S.point.lat.toFixed(4)}, ${S.point.lon.toFixed(4)})</option>` : '');
  if (known >= 0) sel.value = String(known);
}
$('#place').addEventListener('change', (e) => {
  if (e.target.value === 'custom') return;
  const p = S.cfg.places[+e.target.value];
  setPoint({ ...p });
  map.panTo([p.lat, p.lon]);
});
function renderMap(r) {
  markers.clearLayers();
  for (const k of Object.keys(markerByCode)) delete markerByCode[k];
  const rows = ordered(r);
  const pts = [];
  rows.forEach((x, i) => {
    if (!x.lat || !x.lon) return;
    const top = i < 10;
    const m = L.marker([x.lat, x.lon], {
      zIndexOffset: top ? 500 - i : 0,
      icon: L.divIcon({ className: '', html: `<div class="pin${top ? '' : ' rest'}${S.hl === x.code ? ' hl' : ''}">${top ? i + 1 : ''}</div>`,
        iconSize: top ? [26, 26] : [12, 12], iconAnchor: top ? [13, 13] : [6, 6] }),
    });
    m.bindTooltip(`${i + 1}. ${esc(x.label)}`, { direction: 'top', offset: [0, -10] });
    m.on('click', () => highlight(x.code, true));
    m.addTo(markers);
    markerByCode[x.code] = { m, top, i };
    pts.push([x.lat, x.lon]);
  });
  if (pts.length && !renderMap.fitted) { map.fitBounds(L.latLngBounds(pts).extend([S.point.lat, S.point.lon]).pad(0.08)); renderMap.fitted = true; }
}
function highlight(code, scroll) {
  const prev = S.hl;
  S.hl = code;
  for (const c of [prev, code]) {
    const mk = c && markerByCode[c];
    if (mk) {
      mk.m.setIcon(L.divIcon({ className: '', html: `<div class="pin${mk.top ? '' : ' rest'}${c === code ? ' hl' : ''}">${mk.top ? mk.i + 1 : ''}</div>`,
        iconSize: mk.top ? [26, 26] : [12, 12], iconAnchor: mk.top ? [13, 13] : [6, 6] }));
    }
  }
  $$('#list .item').forEach((li) => li.classList.toggle('hl', li.dataset.code === code));
  if (scroll) {
    let li = $('#it-' + code);
    if (!li) { S.shown = ordered(S.result).findIndex((x) => x.code === code) + 5; renderList(S.result); li = $('#it-' + code); }
    li && li.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

/* ---------------- charts ---------------- */
const PLOT_CFG = { displayModeBar: false, responsive: true, locale: 'ru' };
function plot(id, fig) {
  if (!fig) { Plotly.purge(id); return; }
  const f = themed(fig);
  Plotly.react(id, f.data, f.layout, PLOT_CFG);
}
function renderCharts(r) {
  if (!r.charts) return;
  if ($('#view-pick').classList.contains('hidden')) return;
  plot('ch-scores', r.charts.scores);
  plot('ch-contrib', r.charts.contrib);
  plot('ch-ranks', r.charts.ranks);
  plot('ch-heat', r.charts.heatmap);
  plot('ch-sens', r.charts.sensitivity);
  const sel = $('#sens-key');
  sel.innerHTML = r.criteria.map((c) => `<option value="${c.key}">${esc(c.name)}</option>`).join('');
  sel.value = r.sensitivity.key;
  const s = r.sensitivity;
  const label = (code) => (r.labels && r.labels[code]) || code;
  $('#sens-note').innerHTML = s.thresholds.length
    ? 'Точки смены лидера: ' + s.thresholds.map((t) => `при весе ${fmt(t.weight, 3)} — ${esc(label(t.from))} → ${esc(label(t.to))}`).join('; ') + '.'
    : `Лидер не меняется при любом весе «${esc(s.name)}».`;
}

/* ---------------- 4.2: the calculation step by step ---------------- */
// decimals per criterion in the raw matrix; -1 = a year, no thousands separator
const DEC = { price: 0, price_m2: 0, year: -1, area: 1, kitchen: 1, distance: 2, ceiling: 2, rooms: 0, facilities: 0, floor: 0, house: 0, furniture: 0, condition: 1 };
const cfmt = (k, v) => (v == null ? '—' : DEC[k] === -1 ? String(Math.round(v)) : fmt(v, DEC[k] ?? 2));
const flat = (code) => ((S.result && S.result.labels && S.result.labels[code]) || code).replace(/^A\d+ · /, '');
function table(cols, rows, opts = {}) {
  const head = cols.map((c) => `<th class="${c.l ? 'l' : c.n ? 'n' : ''}">${esc(c.t)}</th>`).join('');
  const body = rows.map((r) => `<tr class="${opts.lead && opts.lead(r) ? 'lead' : ''}">${cols.map((c) => {
    const v = c.f ? c.f(r) : r[c.k];
    const cls = [c.l ? 'l' : c.n ? 'n' : '', opts.cls ? opts.cls(r, c) : ''].join(' ').trim();
    return `<td class="${cls}">${c.raw ? v : esc(v)}</td>`;
  }).join('')}</tr>`).join('');
  return `<div class="tbl-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function renderCalc(r) {
  const el = $('#view-calc');
  const v = r.validation;
  const crit = r.criteria || [];
  if (!crit.length) { el.innerHTML = ''; return; }
  const imp = new Set(((r.missing && r.missing.filled) || []).map((f) => f.code + '|' + f.key));
  const label = (code) => (r.labels && r.labels[code]) || code;
  const critCols = (d) => crit.map((c) => ({ t: c.name, k: c.key, f: (row) => (d === 'raw' ? cfmt(c.key, row[c.key]) : fmt(row[c.key], d)) }));
  const checks = [
    ['Есть альтернативы и критерии', (r.matrix || []).length >= 2 && crit.length >= 1],
    ['Нет пропусков (после обработки)', !v.errors.some((e) => e.includes('нет значений'))],
    ['Все значения числовые', !v.errors.some((e) => e.includes('Нечисловые'))],
    ['Сумма весов = 1', !v.errors.some((e) => e.includes('Сумма весов'))],
    ['У всех альтернатив есть все критерии', !v.errors.some((e) => e.includes('нет столбцов'))],
  ];
  let html = `
  <div class="step"><h2><i>1</i>Ввод данных</h2>
    <p>Альтернативы — объявления, прошедшие ограничения (${(r.matrix || []).length}); критерии и веса — из панели слева. Веса — важность каждого критерия, делённая на сумму важностей.</p>
    ${table([{ t: 'Критерий', k: 'name', l: 1 }, { t: 'Ключ', k: 'key' }, { t: 'Тип', f: (c) => c.kind }, { t: 'Ед.', k: 'unit' },
      { t: 'Важность', f: (c) => fmt(c.importance, 1) }, { t: 'Вес', f: (c) => fmt(c.weight, 4) }], crit)}
    <p>Сумма весов: <b>${fmt(crit.reduce((a, c) => a + c.weight, 0), 4)}</b></p>
    ${r.matrix ? `<p>Матрица решений (оранжевым — значения, подставленные вместо пропусков):</p>
    ${table([{ t: 'Код', k: 'code' }, { t: 'Квартира', n: 1, f: (x) => flat(x.code) }, ...critCols('raw')], r.matrix,
      { cls: (row, c) => (c.k && imp.has(row.code + '|' + c.k) ? 'imp' : '') })}` : ''}
  </div>
  <div class="step"><h2><i>2</i>Проверка данных</h2>
    <div class="pills">${checks.map(([t, ok]) => `<span class="tag ${ok ? 'good' : 'bad'}">${ok ? '✓' : '✗'} ${esc(t)}</span>`).join('')}</div>
    ${v.errors.length ? `<p class="no">${v.errors.map(esc).join('<br>')}</p>` : ''}
    ${v.warnings.length ? `<p>${v.warnings.map(esc).join('<br>')}</p>` : ''}
    ${r.missing ? `<p>Пропуски: стратегия «${{ worst: 'худшее значение', median: 'медиана', drop: 'исключить' }[r.missing.strategy]}», заполнено клеток — ${r.missing.filled.length}, исключено объявлений — ${r.missing.dropped.length}.</p>` : ''}
    ${(r.constraints || []).length ? table([{ t: 'Ограничение', k: 'label', l: 1 }, { t: 'Условие', k: 'value' }, { t: 'Исключено', k: 'excluded' }, { t: 'Не указано', k: 'unknown' }], r.constraints) : '<p>Ограничения не заданы.</p>'}
  </div>`;
  if (!v.ok) { el.innerHTML = html; return; }
  const lead = (row) => row.code === r.comparison.table[0].code;
  html += `
  <div class="step"><h2><i>3</i>Нормализация</h2>
    <p>Критерии в разных единицах (₸, м², км, баллы) приводятся к шкале 0…1, где 1 — лучшее значение в подборке.</p>
    <div class="formula">benefit (больше — лучше):  r = x / max(x)
cost (меньше — лучше):     r = min(x) / x</div>
    ${table([{ t: 'Код', k: 'code' }, ...critCols(4)], r.normalized)}
  </div>
  <div class="step"><h2><i>4</i>WSM — взвешенная сумма</h2>
    <div class="formula">S(i) = Σ w(j) · r(i, j)</div>
    <ol class="score-list">${r.wsm.map((x) => `<li>${esc(x.code)} → ${fmt(x.score, 4)}</li>`).join('')}</ol>
    ${table([{ t: 'Код', k: 'code' }, ...crit.map((c) => ({ t: 'w·r ' + c.name, f: (row) => fmt(row[c.key], 4) })),
      { t: 'Балл S', f: (x) => fmt(x.score, 4) }, { t: 'Место', k: 'rank' }], r.wsm, { lead: (x) => x.rank === 1 })}
  </div>
  <div class="step"><h2><i>5</i>TOPSIS — близость к идеалу</h2>
    <div class="formula">1) векторная нормализация  n(i,j) = x(i,j) / √Σ x(k,j)²
2) взвешивание               v(i,j) = w(j) · n(i,j)
3) идеал A+ = лучшие v по каждому критерию (max для benefit, min для cost), анти-идеал A− = худшие
4) расстояния               d+(i) = √Σ (v(i,j) − A+(j))²,   d−(i) = √Σ (v(i,j) − A−(j))²
5) коэффициент близости     C(i) = d−(i) / (d+(i) + d−(i))</div>
    <p>Векторная нормализация:</p>${table([{ t: 'Код', k: 'code' }, ...critCols(5)], r.topsis.normalized)}
    <p>Взвешенная матрица:</p>${table([{ t: 'Код', k: 'code' }, ...critCols(5)], r.topsis.weighted)}
    <p>Идеальная (A+) и наихудшая (A−) точки:</p>${table([{ t: '', f: (x) => (x.code === 'ideal_best' ? 'A+ идеал' : 'A− анти-идеал') }, ...critCols(5)], r.topsis.ideal)}
    <p>Расстояния и итог:</p>${table([{ t: 'Код', k: 'code' }, { t: 'Квартира', n: 1, f: (x) => flat(x.code) }, { t: 'd+', f: (x) => fmt(x.d_plus, 5) }, { t: 'd−', f: (x) => fmt(x.d_minus, 5) },
      { t: 'C', f: (x) => fmt(x.score, 4) }, { t: 'Место', k: 'rank' }], r.topsis.result, { lead: (x) => x.rank === 1 })}
  </div>
  <div class="step"><h2><i>6</i>Сравнение WSM и TOPSIS</h2>
    ${table([{ t: 'Код', k: 'code' }, { t: 'Квартира', n: 1, f: (x) => flat(x.code) }, { t: 'WSM балл', f: (x) => fmt(x.wsm_score, 4) }, { t: 'WSM место', k: 'wsm_rank' },
      { t: 'TOPSIS C', f: (x) => fmt(x.topsis_score, 4) }, { t: 'TOPSIS место', k: 'topsis_rank' },
      { t: 'Сдвиг', f: (x) => (x.shift === 0 ? '0' : (x.shift > 0 ? '↓ ' : '↑ ') + Math.abs(x.shift)) }], r.comparison.table, { lead })}
    <div class="answers">${r.comparison.answers.map((a) => `<p><b>${esc(a.q)}</b><br>${esc(a.a)}</p>`).join('')}</div>
  </div>
  <div class="step"><h2><i>7</i>WPM — взвешенное произведение</h2>
    <p>Третий метод из лекции 7.1. Берёт ту же нормализацию, что и WSM (r = x/max или min/x), но баллы не складывает, а <b>перемножает</b>, возводя каждый r в степень веса.</p>
    <div class="formula">P(i) = Π r(i, j) ^ w(j) = r₁^w₁ · r₂^w₂ · … · rₙ^wₙ</div>
    <p>Множитель r^w = 1, если по критерию квартира лучшая в подборке; чем хуже значение, тем меньше множитель — и он уменьшает <b>весь</b> балл в разы. Поэтому WPM почти не прощает провалов: в WSM сильная сторона компенсирует слабую (слагаемые), а в WPM слабое место «тянет вниз» произведение. Например, r = 0,5 при весе 0,25 даёт множитель 0,5^0,25 ≈ 0,84 — минус 16 % ко всему баллу, сколько бы ни было плюсов по другим критериям. Ещё одно свойство: WPM не зависит от единиц измерения, а нулевое значение обнулило бы произведение (поэтому r ≥ 10⁻¹²).</p>
    <ol class="score-list">${r.wpm.map((x) => `<li>${esc(x.code)} → ${fmt(x.score, 4)}</li>`).join('')}</ol>
    <p>Множители r^w по критериям (произведение строки = балл P). Чем меньше множитель, тем сильнее этот критерий «штрафует» квартиру:</p>
    ${table([{ t: 'Код', k: 'code' }, ...crit.map((c) => ({ t: `${c.name} ^${fmt(c.weight, 3)}`, k: c.key, f: (row) => fmt(row[c.key], 4) })),
      { t: 'Балл P', f: (x) => fmt(x.score, 4) }, { t: 'Место', f: (x) => String(Math.round(x.rank)) }], r.wpm,
      { lead: (x) => x.rank === 1, cls: (row, c) => (c.k && c.k !== 'code' && row[c.k] < 0.85 ? 'low' : '') })}
    <p class="muted">Красным — множители меньше 0,85: этот критерий отнимает у квартиры больше 15 % балла.</p>
    ${r.methods.wpm_notes.length ? `<p><b>Где WPM расходится с WSM и почему:</b></p><ul class="cons">${r.methods.wpm_notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>` : '<p>WPM расставил квартиры так же, как WSM.</p>'}
  </div>
  <div class="step"><h2><i>8</i>Сравнение трёх методов</h2>
    <p>Лидеры: WSM — <b>${esc(flat(r.methods.leaders.wsm[0]))}</b> (${esc(r.methods.leaders.wsm[0])}), TOPSIS — <b>${esc(flat(r.methods.leaders.topsis[0]))}</b> (${esc(r.methods.leaders.topsis[0])}), WPM — <b>${esc(flat(r.methods.leaders.wpm[0]))}</b> (${esc(r.methods.leaders.wpm[0])}). ${r.methods.same_leader ? '<span class="ok">Все три метода выбрали одну квартиру — решение надёжное.</span>' : '<span class="no">Методы выбрали разные квартиры — стоит посмотреть на каждую.</span>'}</p>
    <p>Согласие рангов (коэффициент Спирмена ρ, 1 — порядок совпадает): WSM–TOPSIS ${fmt(r.methods.spearman['wsm~topsis'], 2)}, WSM–WPM ${fmt(r.methods.spearman['wsm~wpm'], 2)}, TOPSIS–WPM ${fmt(r.methods.spearman['topsis~wpm'], 2)}. WSM и WPM обычно ближе друг к другу: у них одна нормализация, разница только в сумме и произведении.</p>
    ${table([{ t: 'Код', k: 'code' }, { t: 'Квартира', n: 1, f: (x) => flat(x.code) },
      { t: 'WSM', f: (x) => `${Math.round(x.wsm_rank)} (${fmt(x.wsm_score, 3)})` }, { t: 'TOPSIS', f: (x) => `${Math.round(x.topsis_rank)} (${fmt(x.topsis_score, 3)})` },
      { t: 'WPM', f: (x) => `${Math.round(x.wpm_rank)} (${fmt(x.wpm_score, 3)})` }, { t: 'Разброс мест', f: (x) => String(Math.round(x.spread)) }], r.methods.table,
      { lead: (x) => x.wsm_rank === 1 || x.topsis_rank === 1 || x.wpm_rank === 1 })}
    <p class="muted">Место (балл). «Разброс мест» — насколько по-разному методы оценивают квартиру: 0 — одинаково, большой разброс — у квартиры есть яркий плюс и заметный минус.</p>
  </div>
  <div class="step"><h2><i>9</i>Доминирование и чувствительность</h2>
    <p><b>Доминирование.</b> ${r.dominance.length ? r.dominance.map((d) => `${esc(d.code)} хуже ${esc(d.by)} по всем критериям`).join('; ') + '.' : 'Доминируемых альтернатив нет — каждая чем-то лучше остальных.'}</p>
    <p><b>Чувствительность</b> (${esc(r.sensitivity.method.toUpperCase())}, критерий «${esc(r.sensitivity.name)}», текущий вес ${fmt(r.sensitivity.current, 3)}): ${r.sensitivity.thresholds.length
      ? r.sensitivity.thresholds.map((t) => `при весе ${fmt(t.weight, 4)} лидер ${esc(t.from)} → ${esc(t.to)}`).join('; ')
      : 'лидер не меняется на всём диапазоне весов'}. График — на вкладке «Подбор».</p>
  </div>
  <div class="step"><h2><i>10</i>Визуализация и интерфейс</h2>
    <p>На вкладке «Подбор»: столбцы баллов WSM, TOPSIS и WPM, разложение балла WSM по критериям, график мест по WSM и TOPSIS, тепловая карта нормализованной матрицы и анализ чувствительности. Веса меняются ползунками слева — пересчёт происходит сразу. Сортировку списка можно переключить на любой из трёх методов (раздел 5 слева).</p>
  </div>`;
  el.innerHTML = html;
}

/* ---------------- 4.1: formalization ---------------- */
function renderFormal(r) {
  const f = r.formalization;
  if (!f) { $('#view-formal').innerHTML = ''; return; }
  const crit = f.criteria;
  const label = (code) => (r.labels && r.labels[code]) || code;
  const alts = f.alternatives;
  const ds = r.dataset || {};
  const q = ds.query || {};
  const searchFilter = [
    q.rooms && q.rooms.length ? 'комнат: ' + q.rooms.map((x) => (x >= 5 ? '5+' : x)).join(', ') : '',
    q.price_from ? 'цена от ' + money(q.price_from) : '', q.price_to ? 'цена до ' + money(q.price_to) : '',
    q.area_from ? `площадь от ${q.area_from} м²` : '', q.year_from ? `год от ${q.year_from}` : '',
  ].filter(Boolean).join(', ');
  $('#view-formal').innerHTML = `
  <div class="step"><h2>Постановка задачи</h2>
    <dl class="kv">
      <dt>Название задачи</dt><dd><b>${esc(f.name)}</b></dd>
      <dt>ЛПР</dt><dd>${esc(f.dm)}</dd>
      <dt>Цель</dt><dd>${esc(f.goal)}</dd>
      <dt>Условия</dt><dd>${esc(f.conditions)}</dd>
      <dt>Тип задачи</dt><dd><span class="badge">${esc(f.task_type.value)}</span><br>${esc(f.task_type.why)}</dd>
      <dt>Тип СППР</dt><dd><span class="badge">${esc(f.dss_type.value)}</span><br>${esc(f.dss_type.why)}</dd>
    </dl>
  </div>
  <div class="step"><h2>Альтернативы <span class="muted">· ${alts.length}</span></h2>
    <p>Каждая альтернатива — реальное объявление Krisha.kz${searchFilter ? ` (поиск: ${esc(searchFilter)})` : ''}, прошедшее ограничения. ${alts.length >= 5 ? '<span class="ok">✓ не меньше 5</span>' : '<span class="no">✗ меньше 5 — ослабьте ограничения</span>'}</p>
    ${table([{ t: 'Код', k: 'code' }, { t: 'Объявление', l: 1, raw: 1, f: (a) => `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title || '')}</a>` }, { t: 'Адрес', l: 1, k: 'address' }], alts)}
  </div>
  <div class="step"><h2>Критерии <span class="muted">· ${crit.length}</span></h2>
    ${crit.length >= 5 ? '' : '<p class="no">Для задания нужно не меньше 5 критериев — включите ещё в панели слева.</p>'}
    ${table([{ t: 'Критерий', k: 'name', l: 1 }, { t: 'Тип', n: 1, f: (c) => (c.kind === 'benefit' ? 'benefit (польза)' : 'cost (затраты)') }, { t: 'Ед. измерения', n: 1, k: 'unit' },
      { t: 'Как измеряется', k: 'how', l: 1 }, { t: 'Шкала', k: 'scale', l: 1 }, { t: 'Вес', f: (c) => fmt(c.weight, 4) }], crit)}
    <p>Сумма весов: <b>${fmt(crit.reduce((a, c) => a + c.weight, 0), 4)}</b> = 1</p>
  </div>
  <div class="step"><h2>Ограничения</h2>
    ${f.constraints.length ? table([{ t: 'Ограничение', k: 'label', l: 1 }, { t: 'Условие', k: 'value' }, { t: 'Исключено', k: 'excluded' }], f.constraints) : '<p>Сейчас ограничения не заданы — их можно включить в панели слева (бюджет, площадь, комнаты, этаж, год, состояние, расстояние).</p>'}
    <p>Кроме того, в поиск не попадают предложения застройщиков «от N ₸» без конкретной квартиры и (для аренды) посуточная аренда.</p>
  </div>
  <div class="step"><h2>Последствия выбора</h2>
    <p>Что означает выбор каждой из лучших альтернатив для ЛПР:</p>
    ${f.consequences.map((c) => `<p><b>${esc(c.label)}</b></p><ul class="cons">${c.items.map((i) => `<li>${esc(i)}</li>`).join('') || '<li>Без особых рисков по имеющимся данным.</li>'}</ul>`).join('')}
  </div>
  <div class="step"><h2>Модель Саймона</h2>
    ${table([{ t: 'Фаза', k: 'phase', l: 1 }, { t: 'В этой системе', k: 'here', l: 1 }], f.simon)}
  </div>
  <div class="step"><h2>Матрица решений</h2>
    ${table([{ t: 'Код', k: 'code' }, { t: 'Квартира', n: 1, f: (x) => flat(x.code) }, ...crit.map((c) => ({ t: `${c.name}, ${c.unit}`, f: (row) => cfmt(c.key, row[c.key]) }))], f.matrix)}
  </div>
  <div class="step"><h2>Веса критериев</h2>
    ${table([{ t: 'Критерий', k: 'name', l: 1 }, { t: 'Важность (0–10)', f: (c) => fmt(c.importance, 1) }, { t: 'Вес', f: (c) => fmt(c.weight, 4) }], crit)}
    <p>Σ = ${fmt(crit.reduce((a, c) => a + c.weight, 0), 4)}</p>
  </div>
  <div class="step"><h2>Возможные ошибки СППР и как они учтены</h2>
    <ul class="cons">${f.risks.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
  </div>`;
}

/* ---------------- search on krisha ---------------- */
$('#s-pages').addEventListener('input', (e) => { $('#s-pages-v').textContent = e.target.value; $('#s-pages-n').textContent = e.target.value * 20; });
$('#s-go').addEventListener('click', async () => {
  const body = {
    mode: S.mode,
    rooms: $$('#s-rooms button.on').map((b) => +b.dataset.v),
    price_from: digits($('#s-price-from').value), price_to: digits($('#s-price-to').value),
    area_from: digits($('#s-area').value), year_from: S.mode === 'sale' ? digits($('#s-year').value) : null,
    not_first: $('#s-not-first').checked, not_last: $('#s-not-last').checked,
    pages: +$("#s-pages").value, details: $('#s-details').checked, max_details: 40,
  };
  try {
    const r = await api('/api/search', body);
    S.job = r.job;
    $('#s-go').disabled = true;
    $('#s-progress').classList.remove('hidden');
    poll();
  } catch (e) { alertBox([e.message], true); }
});
$('#s-cancel').addEventListener('click', () => S.job && api('/api/search/' + S.job, null, 'DELETE'));
async function poll() {
  if (!S.job) return;
  let j;
  try { j = await api('/api/search/' + S.job); } catch (e) { j = { status: 'error', error: e.message }; }
  const p = j.progress || {};
  const part = p.done / Math.max(1, p.total);
  const share = { search: 0.15 * part, details: 0.15 + 0.45 * part, geocode: 0.6 + 0.4 * part, done: 1 }[p.stage] ?? 0.02;
  $('#s-bar').style.width = (share * 100).toFixed(1) + '%';
  $('#s-msg').textContent = p.message || '…';
  if (j.status === 'running') { setTimeout(poll, 700); return; }
  S.job = null;
  $('#s-go').disabled = false;
  setTimeout(() => $('#s-progress').classList.add('hidden'), 1500);
  if (j.status === 'error') { alertBox([j.error], true); return; }
  S.cfg = await api('/api/config');
  S.dataset[S.mode] = j.dataset.id;
  store.set('dataset:' + S.mode, j.dataset.id);
  renderMap.fitted = false;
  buildDatasets();
  $('#search-box').open = false;
  rankSoon(0);
}

/* ---------------- AHP ---------------- */
const SAATY = [9, 7, 5, 3, 1, 1 / 3, 1 / 5, 1 / 7, 1 / 9];
const SAATY_TXT = ['абсолютно', 'очень сильно', 'сильно', 'немного'];
let ahpKeys = [];
let ahpWeights = null;
$('#ahp-open').addEventListener('click', () => {
  const w = S.weights[S.mode];
  const crit = S.cfg.criteria[S.mode].filter((c) => w[c.key] > 0);
  if (crit.length < 2) { alertBox(['Для AHP включите хотя бы два критерия.']); return; }
  ahpKeys = crit.map((c) => c.key);
  const names = Object.fromEntries(crit.map((c) => [c.key, c.name]));
  const nearest = (ratio) => SAATY.reduce((best, s) => (Math.abs(Math.log(s / ratio)) < Math.abs(Math.log(best / ratio)) ? s : best), 1);
  let html = '';
  for (let i = 0; i < ahpKeys.length; i++) {
    for (let j = i + 1; j < ahpKeys.length; j++) {
      const a = ahpKeys[i]; const b = ahpKeys[j];
      const pre = nearest(w[a] / w[b]);
      const opts = SAATY.map((s, idx) => {
        const txt = s === 1 ? 'одинаково важны' : s > 1 ? `слева ${SAATY_TXT[idx]} важнее (${s})` : `справа ${SAATY_TXT[8 - idx]} важнее (${Math.round(1 / s)})`;
        return `<option value="${s}" ${Math.abs(s - pre) < 1e-9 ? 'selected' : ''}>${txt}</option>`;
      }).join('');
      html += `<div class="pair"><span class="r">${esc(names[a])}</span><select data-i="${i}" data-j="${j}">${opts}</select><span>${esc(names[b])}</span></div>`;
    }
  }
  $('#ahp-pairs').innerHTML = html;
  $$('#ahp-pairs select').forEach((s) => s.addEventListener('change', ahpCompute));
  ahpCompute();
  $('#ahp').showModal();
});
async function ahpCompute() {
  const n = ahpKeys.length;
  const m = Array.from({ length: n }, () => Array(n).fill(1));
  $$('#ahp-pairs select').forEach((s) => { const i = +s.dataset.i; const j = +s.dataset.j; m[i][j] = Number(s.value); m[j][i] = 1 / Number(s.value); });
  try {
    const r = await api('/api/ahp', { keys: ahpKeys, matrix: m });
    ahpWeights = r.weights;
    const names = Object.fromEntries(S.cfg.criteria[S.mode].map((c) => [c.key, c.name]));
    $('#ahp-out').innerHTML = `<div class="pills">${ahpKeys.map((k) => `<span class="tag">${esc(names[k])}: <b>${fmt(r.weights[k], 3)}</b></span>`).join('')}</div>
      <p>λmax = ${fmt(r.lambda_max, 3)}, CI = ${fmt(r.ci, 3)}, CR = <b class="${r.consistent ? 'ok' : 'no'}">${fmt(r.cr, 3)}</b> —
      ${r.consistent ? 'сравнения согласованы (CR ≤ 0.10).' : 'сравнения противоречивы (CR > 0.10), пересмотрите их.'}</p>`;
    $('#ahp-apply').disabled = !r.consistent;
  } catch (e) { $('#ahp-out').textContent = e.message; $('#ahp-apply').disabled = true; }
}
$('#ahp').addEventListener('close', () => {
  if ($('#ahp').returnValue !== 'apply' || !ahpWeights) return;
  const max = Math.max(...Object.values(ahpWeights));
  const w = S.weights[S.mode];
  for (const k of Object.keys(w)) w[k] = ahpKeys.includes(k) ? Math.round((ahpWeights[k] / max) * 1000) / 100 : 0;
  buildCriteria();
  weightsChanged();
});

/* ---------------- AI assistant ---------------- */
const AI = { chatId: null, busy: false };
const AI_SUGGEST = [
  'Что вообще делает эта система и с чего начать?',
  'Почему первое место у лидера?',
  'Чем WSM отличается от TOPSIS простыми словами?',
  'Зачем нужны ограничения, если есть веса?',
  'Что будет, если цена станет вдвое важнее?',
  'Настрой веса для семьи с ребёнком и пересчитай',
];

function initAI() {
  if (!S.cfg.ai) return;
  $('#ai-fab').classList.remove('hidden');
  $('#ai-chips').innerHTML = AI_SUGGEST.map((q) => `<button type="button" data-ask="${esc(q)}">${esc(q)}</button>`).join('');
}
function openAI(open) {
  $('#ai').classList.toggle('hidden', !open);
  document.body.classList.toggle('ai-open', open);
  $('#ai-fab').classList.toggle('hidden', open || !S.cfg.ai);
  setTimeout(() => { map && map.invalidateSize(); if (S.result && !$('#view-pick').classList.contains('hidden')) renderCharts(S.result); }, 50);
  if (open) $('#ai-input').focus();
}
$('#ai-fab').addEventListener('click', () => openAI(true));
$('#ai-close').addEventListener('click', () => openAI(false));
$('#ai-new').addEventListener('click', () => {
  if (AI.busy) return;
  AI.chatId = null;
  $$('#ai-log > .msg-user, #ai-log > .msg-ai').forEach((m) => m.remove());
  $('#ai-hello').classList.remove('hidden');
});
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-ask]');
  if (!b) return;
  e.preventDefault();
  e.stopPropagation();
  askAI(b.dataset.ask);
});
const aiInput = $('#ai-input');
aiInput.addEventListener('input', () => { aiInput.style.height = 'auto'; aiInput.style.height = Math.min(140, aiInput.scrollHeight) + 'px'; });
aiInput.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#ai-form').requestSubmit(); } });
$('#ai-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const q = aiInput.value.trim();
  if (q) { aiInput.value = ''; aiInput.style.height = 'auto'; askAI(q); }
});

function currentTab() { return ($('.tabs button.on') || {}).textContent || 'Подбор'; }
function screenState() {
  return {
    dataset: S.dataset[S.mode], weights: S.weights[S.mode], point: { lat: S.point.lat, lon: S.point.lon },
    point_name: S.point.name || '', constraints: readConstraints(), missing: $('#missing').value,
    sens_key: S.sensKey, sens_method: S.sensMethod, sort: S.sort,
  };
}

async function askAI(question) {
  if (!S.cfg.ai) return;
  openAI(true);
  if (AI.busy) return;
  if (!S.dataset[S.mode]) { alertBox(['Сначала загрузите набор объявлений — помощнику нужны данные для расчёта.']); return; }
  AI.busy = true;
  $('#ai-send').disabled = true;
  $('#ai-hello').classList.add('hidden');
  const log = $('#ai-log');
  log.insertAdjacentHTML('beforeend', `<div class="msg-user">${esc(question)}</div>`);
  const box = document.createElement('div');
  box.className = 'msg-ai';
  box.innerHTML = '<ul class="steps"></ul><details class="think hidden"><summary>Ход рассуждений</summary><div></div></details><div class="md"></div><div class="wait">Думаю</div>';
  log.appendChild(box);
  const steps = $('.steps', box), think = $('details.think', box), thinkText = $('details.think div', box), out = $('.md', box);
  let text = '', thinking = '';
  const stick = () => { if (log.scrollHeight - log.scrollTop - log.clientHeight < 140) log.scrollTop = log.scrollHeight; };
  log.scrollTop = log.scrollHeight;
  const stepEl = {};
  const handle = (ev) => {
    if (ev.type === 'chat') { AI.chatId = ev.chat_id; return; }
    $('.wait', box) && ev.type !== 'done' && ev.type !== 'thinking' && $('.wait', box).remove();
    if (ev.type === 'thinking') {
      thinking += ev.text;
      think.classList.remove('hidden');
      thinkText.textContent = thinking;
    } else if (ev.type === 'text') {
      text += ev.text;
      out.innerHTML = md(text);
    } else if (ev.type === 'step') {
      let li = stepEl[ev.id];
      if (!li) { li = document.createElement('li'); li.innerHTML = '<i></i><span></span>'; steps.appendChild(li); stepEl[ev.id] = li; }
      li.className = ev.status === 'start' ? '' : ev.status;
      $('span', li).textContent = ev.label;
    } else if (ev.type === 'apply') {
      applyFromAI(ev.settings || {});
      const li = document.createElement('li');
      li.className = 'note';
      li.innerHTML = '<i></i><span></span>';
      $('span', li).textContent = 'На экране изменено: ' + (ev.summary || 'настройки');
      steps.appendChild(li);
    } else if (ev.type === 'error') {
      box.insertAdjacentHTML('beforeend', `<p class="err">${esc(ev.message)}</p>`);
    }
    stick();
  };
  try {
    const r = await fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: AI.chatId, message: question, state: screenState(), tab: currentTab() }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${r.status}`);
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i);
        buf = buf.slice(i + 2);
        for (const line of chunk.split('\n')) {
          if (line.startsWith('data: ')) { try { handle(JSON.parse(line.slice(6))); } catch (err) { console.warn(err); } }
        }
      }
    }
  } catch (e) {
    handle({ type: 'error', message: 'Не удалось получить ответ: ' + e.message });
  } finally {
    $('.wait', box) && $('.wait', box).remove();
    if (thinking) think.querySelector('summary').textContent = 'Ход рассуждений';
    AI.busy = false;
    $('#ai-send').disabled = false;
    stick();
  }
}

// settings the assistant changed on request: same paths as the user's own clicks
function applyFromAI(s) {
  const w = S.weights[S.mode];
  const changed = [];
  if (s.weights) for (const [k, v] of Object.entries(s.weights)) { if (k in w) { w[k] = Math.max(0, Math.min(10, Number(v))); changed.push(k); } }
  if (s.constraints) {
    const c = S.constraints[S.mode] || {};
    for (const [k, v] of Object.entries(s.constraints)) {
      if (v == null || v === false || (Array.isArray(v) && !v.length)) delete c[k]; else c[k] = v;
    }
    S.constraints[S.mode] = c;
    loadConstraints();
  }
  if (s.missing) $('#missing').value = s.missing;
  if (s.sort) { S.sort = s.sort; $$('#sort button').forEach((b) => b.classList.toggle('on', b.dataset.v === s.sort)); }
  if (s.point) {
    S.point = { lat: s.point.lat, lon: s.point.lon, name: s.point.name || 'Точка от ИИ-помощника' };
    store.set('point', S.point);
    home.setLatLng([S.point.lat, S.point.lon]);
    buildPlaces();
  }
  store.set('weights', S.weights);
  buildCriteria();
  changed.forEach((k) => { const row = $(`.crit[data-k="${k}"]`); row && row.classList.add('flash'); });
  rankSoon(0);
}

// a small, safe markdown subset: paragraphs, lists, tables, headings, bold, italics, code
function md(src) {
  const inline = (s) => s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<i>$2</i>');
  const lines = esc(src).split('\n');
  let html = '';
  let list = null;
  let para = [];
  let table = [];
  const flushPara = () => { if (para.length) { html += `<p>${inline(para.join('<br>'))}</p>`; para = []; } };
  const flushList = () => { if (list) { html += `<${list.tag}>${list.items.map((x) => `<li>${inline(x)}</li>`).join('')}</${list.tag}>`; list = null; } };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter((r) => !/^\|?\s*:?-{2,}/.test(r)).map((r) => r.replace(/^\||\|$/g, '').split('|').map((c) => inline(c.trim())));
    if (rows.length) {
      html += `<div class="tbl-wrap"><table><thead><tr>${rows[0].map((c) => `<th class="n">${c}</th>`).join('')}</tr></thead><tbody>`
        + rows.slice(1).map((r) => `<tr>${r.map((c) => `<td class="n">${c}</td>`).join('')}</tr>`).join('') + '</tbody></table></div>';
    }
    table = [];
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\s*\|/.test(line)) { flushPara(); flushList(); table.push(line.trim()); continue; }
    flushTable();
    let m;
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if ((m = line.match(/^#{1,6}\s+(.*)/))) { flushPara(); flushList(); html += `<h4>${inline(m[1])}</h4>`; continue; }
    if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { flushPara(); if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; } list.items.push(m[1]); continue; }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flushPara(); if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [] }; } list.items.push(m[1]); continue; }
    if (list && /^\s{2,}/.test(raw)) { list.items[list.items.length - 1] += ' ' + line.trim(); continue; }
    flushList();
    para.push(line);
  }
  flushPara(); flushList(); flushTable();
  return html;
}

init().catch((e) => alertBox(['Не удалось загрузить настройки: ' + e.message], true));
