import { Project, canWrite } from './fsx.js';
import { loadNifti, writeMaskLike, robustRange } from './nifti.js';
import { Viewer } from './viewer.js';
import { HELP_HTML } from './help.js';

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const NII = /\.nii(\.gz)?$/;
const stem = n => n.replace(NII, '');

const proj = new Project();
const state = {
  subjects: [],                       // {id, t1, t2, ext, site, group}
  labels: { format: 'enigma-disc-labels-v1', subjects: {} },
  review: {},                         // {id: {kind: {decision, note, edited, time}}}
  results: null,
};

function toast(msg, ms = 2600) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), ms);
}

// ------------------------------------------------------------------ abas
$$('#tabs button').forEach(b => b.addEventListener('click', () => {
  $$('#tabs button').forEach(x => x.classList.toggle('active', x === b));
  $$('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
  if (b.dataset.tab === 'discos') renderDiscList();
  if (b.dataset.tab === 'revisao') renderRevList();
  window.dispatchEvent(new Event('resize'));
}));
$('#helpText').innerHTML = HELP_HTML;

// ------------------------------------------------------------------ projeto
$('#fsNote').textContent = canWrite
  ? 'Este navegador grava direto na pasta (config, marcações, máscaras editadas).'
  : 'Este navegador não permite gravar em pastas: use "Abrir só para leitura" — os arquivos gerados serão baixados e você os coloca na pasta do projeto.';
if (!canWrite) $('#btnPick').classList.add('hidden');

$('#btnPick').addEventListener('click', async () => {
  try { if (await proj.pick()) await openProject(); } catch (e) { if (e.name !== 'AbortError') toast(e.message); }
});
$('#btnPerm').addEventListener('click', async () => {
  if (await proj.requestPermission()) { $('#btnPerm').classList.add('hidden'); await openProject(); }
});
$('#dirInput').addEventListener('change', async e => { proj.useFileList(e.target.files); await openProject(); });

(async () => {
  const r = await proj.restore().catch(() => false);
  if (r === true) await openProject();
  else if (r === 'needs-permission') {
    $('#btnPerm').classList.remove('hidden');
    $('#btnPerm').textContent = `Reabrir "${proj.name}" (autorizar acesso)`;
  }
})();

async function openProject() {
  $('#projInfo').textContent = `Projeto: ${proj.name}${proj.writable ? '' : ' (somente leitura)'}`;
  const inputs = await proj.list('input');
  if (!inputs.length) toast('Não encontrei a subpasta input/ com os exames.', 4000);
  const names = inputs.filter(f => f.kind === 'file' && NII.test(f.name)).map(f => f.name);
  const byStem = Object.fromEntries(names.map(n => [stem(n), n]));
  const subs = names.filter(n => stem(n).endsWith('_t1')).map(n => {
    const id = stem(n).slice(0, -3);
    return { id, t1: n, t2: byStem[`${id}_t2`] || '', ext: byStem[`${id}_ceresmask`] || '', site: '', group: '' };
  });
  // recupera centro/grupo/parâmetros de um config.json existente
  const cfgTxt = await proj.readText('config.json');
  if (cfgTxt) {
    try {
      const cfg = JSON.parse(cfgTxt);
      const prev = Object.fromEntries((cfg.subjects || []).map(s => [s.id, s]));
      subs.forEach(s => { if (prev[s.id]) { s.site = prev[s.id].site || ''; s.group = prev[s.id].group || ''; } });
      applyParams(cfg);
    } catch { toast('config.json existente não pôde ser lido'); }
  }
  state.subjects = subs;
  const lab = await proj.readText('disc_labels.json');
  if (lab) try { state.labels = JSON.parse(lab); } catch { /* ignora */ }
  const rev = await proj.readText('review.json');
  if (rev) try { state.review = JSON.parse(rev); } catch { /* ignora */ }
  renderSubjects();
  toast(`${subs.length} sujeitos encontrados em input/`);
}

function renderSubjects() {
  const tb = $('#subjTable tbody');
  tb.innerHTML = state.subjects.map((s, i) => `<tr>
    <td><input type="checkbox" class="sel" data-i="${i}"></td>
    <td>${esc(s.id)}${isExcluded(s.id) ? ' <span class="pill bad">excluído</span>' : ''}</td>
    <td>${esc(s.t1)}</td><td>${esc(s.t2) || '<span class="note">—</span>'}</td>
    <td>${esc(s.ext) || '<span class="note">—</span>'}</td>
    <td><input data-i="${i}" data-k="site" value="${esc(s.site)}"></td>
    <td><select data-i="${i}" data-k="group">${['', 'FRDA', 'CONT', 'outro'].map(g =>
      `<option ${g === s.group ? 'selected' : ''}>${g}</option>`).join('')}</select></td>
    <td>${discDone(s.id) ? '<span class="pill ok">✓</span>' : '<span class="pill">—</span>'}</td></tr>`).join('');
  tb.querySelectorAll('input[data-k],select[data-k]').forEach(el => el.addEventListener('change', () => {
    state.subjects[el.dataset.i][el.dataset.k] = el.value;
  }));
}
$('#selAll').addEventListener('change', e => $$('#subjTable .sel').forEach(c => { c.checked = e.target.checked; }));
$('#btnBulk').addEventListener('click', () => {
  const g = $('#bulkGroup').value, site = $('#bulkSite').value.trim();
  $$('#subjTable .sel:checked').forEach(c => {
    const s = state.subjects[c.dataset.i];
    if (g) s.group = g;
    if (site) s.site = site;
  });
  renderSubjects();
});

function params() {
  return {
    suit: {
      enabled: $('#suitOn').checked, use_t2: $('#useT2').checked, external_mask: $('#extMask').value,
      tissue_seg: $('#tissueSeg').value, space: $('#space').value, fwhm: +$('#fwhm').value,
    },
    sct: {
      enabled: $('#sctOn').checked, seg_engine: $('#segEngine').value, vert: $('#vert').value,
      labels_file: 'disc_labels.json',
    },
    qc: { sd_threshold: 2, prop_scaling: false },
  };
}
function applyParams(cfg) {
  const su = cfg.suit || {}, sc = cfg.sct || {};
  if ('enabled' in su) $('#suitOn').checked = su.enabled;
  if ('use_t2' in su) $('#useT2').checked = su.use_t2;
  if (su.external_mask) $('#extMask').value = su.external_mask;
  if (su.tissue_seg) $('#tissueSeg').value = su.tissue_seg;
  if (su.space) $('#space').value = su.space;
  if ('fwhm' in su) $('#fwhm').value = su.fwhm;
  if ('enabled' in sc) $('#sctOn').checked = sc.enabled;
  if (sc.seg_engine) $('#segEngine').value = sc.seg_engine;
  if (sc.vert) $('#vert').value = sc.vert;
  if (cfg._venv) $('#venv').value = cfg._venv;
}

const isExcluded = id => Object.values(state.review[id] || {}).some(r => r.decision === 'excluir');

function buildConfig() {
  return {
    project: proj.name || 'enigma', input_dir: 'input', output_dir: 'deriv',
    subjects: state.subjects.map(s => ({
      id: s.id, t1: s.t1, t2: s.t2 || null, ext_mask: s.ext || null,
      site: s.site || 'site1', group: s.group || '',
    })),
    exclude: state.subjects.filter(s => isExcluded(s.id)).map(s => s.id),
    ...params(),
    _venv: $('#venv').value,
    _gerado_por: 'PWA ENIGMA SUIT + SCT', _data: new Date().toISOString(),
  };
}

function buildScript() {
  const venv = $('#venv').value.trim() || '$HOME/enigma-env';
  return `#!/usr/bin/env bash
# Gerado pelo PWA ENIGMA SUIT + SCT em ${new Date().toLocaleString('pt-BR')}
# Uso:  bash run_enigma.sh check | etapa1 | etapa2 | tudo | refazer <comando> [opções]
set -uo pipefail
cd "$(dirname "$0")"
if [ -f "${venv}/bin/activate" ]; then source "${venv}/bin/activate"; fi
CFG=config.json
P="enigma-pipeline"

case "\${1:-ajuda}" in
  check)
    $P check --config "$CFG" ;;
  atlas)
    $P fetch-atlas --config "$CFG" ;;
  etapa1)
    # cerebelo até a máscara final (para revisar) e medula até a segmentação (para revisar)
    $P suit --config "$CFG" --until suit_mask
    $P sct  --config "$CFG" --until sct_segment
    echo; echo ">> Agora: marque os discos e revise as máscaras no PWA; depois rode: bash run_enigma.sh etapa2" ;;
  etapa2)
    $P suit --config "$CFG"
    $P sct  --config "$CFG"
    $P qc --config "$CFG"
    $P collect --config "$CFG"
    echo; echo ">> Resultados em deriv/results/ — abra a aba Resultados no PWA" ;;
  tudo)
    $P all --config "$CFG" ;;
  refazer)
    # ex.: bash run_enigma.sh refazer suit --subjects sub01 --from suit_normalize
    shift; cmd="$1"; shift; $P "$cmd" --config "$CFG" "$@" ;;
  *)
    sed -n '2,4p' "$0" ;;
esac
`;
}

$('#btnSaveCfg').addEventListener('click', async () => {
  if (!state.subjects.length) return toast('Abra a pasta do projeto primeiro');
  const noSite = state.subjects.filter(s => !s.site).length;
  const r1 = await proj.writeFile('config.json', JSON.stringify(buildConfig(), null, 2));
  const r2 = await proj.writeFile('run_enigma.sh', buildScript());
  await saveLabels(true);
  $('#cfgMsg').textContent = (r1 === 'saved' ? 'config.json, run_enigma.sh e disc_labels.json salvos na pasta do projeto.'
    : 'Arquivos baixados — coloque-os na pasta do projeto.') + (noSite ? ` Atenção: ${noSite} sujeito(s) sem centro (vai como "site1").` : '');
});

// ------------------------------------------------------------------ discos
const discViewer = new Viewer($('#discCanvas'));
discViewer.mode = 'pick';
let discSubj = null, discLabel = 3;
const LABEL_COLORS = { 3: '#00e676', 4: '#40c4ff' };

const discDone = id => {
  const e = state.labels.subjects[id];
  return !!e && [3, 4].every(v => e.labels.some(l => l.value === v));
};

function renderDiscList() {
  $('#discList').innerHTML = state.subjects.map(s => `<li data-id="${esc(s.id)}" class="${s.id === discSubj ? 'sel' : ''}">
    <span>${esc(s.id)}</span>${discDone(s.id) ? '<span class="ok">✓</span>' : '<span class="note">—</span>'}</li>`).join('')
    || '<li class="note">abra um projeto</li>';
  $$('#discList li[data-id]').forEach(li => li.addEventListener('click', () => openDisc(li.dataset.id)));
}

async function openDisc(id) {
  const s = state.subjects.find(x => x.id === id);
  const f = await proj.getFile(`input/${s.t1}`);
  if (!f) return toast('T1 não encontrado');
  $('#discInfo').textContent = 'carregando…';
  const nii = await loadNifti(f);
  discSubj = id;
  discViewer.setBase(nii);
  discViewer.markers = markersFor(id);
  const e = state.labels.subjects[id];
  if (e && e.shape.join() !== [nii.header.nx, nii.header.ny, nii.header.nz].join()) {
    toast('As marcações salvas foram feitas numa imagem de outro tamanho — marque de novo.', 5000);
  }
  syncDiscSlider();
  // começa na fatia sagital da marcação existente
  if (discViewer.markers.length && discViewer.view === 'sag') discViewer.goTo(discViewer.ijkToScreen(discViewer.markers[0].ijk)[2]);
  $('#discSlice').value = discViewer.slice;
  setDiscLabel(discDone(id) ? 3 : (e?.labels.some(l => l.value === 3) ? 4 : 3));
  renderDiscList(); updateDiscInfo();
}

function markersFor(id) {
  const e = state.labels.subjects[id];
  return (e?.labels || []).map(l => ({ ijk: l.ijk, value: l.value, color: LABEL_COLORS[l.value],
    text: l.value === 3 ? '3 C2/C3' : '4 C3/C4' }));
}
function syncDiscSlider() { $('#discSlice').max = discViewer.nSlices - 1; $('#discSlice').value = discViewer.slice; }
function setDiscLabel(v) { discLabel = v; $$('#tab-discos .lab').forEach(b => b.classList.toggle('active', +b.dataset.v === v)); }
$$('#tab-discos .lab').forEach(b => b.addEventListener('click', () => setDiscLabel(+b.dataset.v)));
$('#discView').addEventListener('change', e => { discViewer.setView(e.target.value); syncDiscSlider(); });
$('#discSlice').addEventListener('input', e => discViewer.goTo(+e.target.value));
discViewer.onSlice = s => { $('#discSlice').value = s; };
$('#discWin').addEventListener('input', e => {
  if (!discViewer.nii) return;
  const [lo, hi] = robustRange(discViewer.nii.data);
  discViewer.setWindow(lo, lo + (hi - lo) * (+e.target.value / 100));
});
discViewer.onPick = ijk => {
  if (!discSubj) return;
  const h = discViewer.nii.header;
  const e = state.labels.subjects[discSubj] ||= { shape: [h.nx, h.ny, h.nz], labels: [] };
  e.shape = [h.nx, h.ny, h.nz];
  e.labels = e.labels.filter(l => l.value !== discLabel);
  e.labels.push({ value: discLabel, ijk });
  e.labels.sort((a, b) => a.value - b.value);
  e.time = new Date().toISOString();
  discViewer.markers = markersFor(discSubj);
  discViewer.draw();
  if (discLabel === 3) setDiscLabel(4);
  updateDiscInfo(); renderDiscList(); saveLabels();
};
$('#discClear').addEventListener('click', () => {
  if (!discSubj) return;
  delete state.labels.subjects[discSubj];
  discViewer.markers = []; discViewer.draw(); setDiscLabel(3); updateDiscInfo(); renderDiscList(); saveLabels();
});
function updateDiscInfo() {
  const e = state.labels.subjects[discSubj];
  const txt = (e?.labels || []).map(l => `rótulo ${l.value}: voxel [${l.ijk.join(', ')}]`).join(' · ');
  $('#discInfo').textContent = discSubj ? `${discSubj} — ${txt || 'sem marcações'}` : '';
}
let saveTimer;
async function saveLabels(now = false) {
  try { localStorage.setItem('enigma-labels-' + proj.name, JSON.stringify(state.labels)); } catch { /* cota */ }
  if (!proj.writable) return now ? proj.writeFile('disc_labels.json', JSON.stringify(state.labels, null, 1)) : null;
  clearTimeout(saveTimer);
  const go = () => proj.writeFile('disc_labels.json', JSON.stringify(state.labels, null, 1));
  if (now) return go();
  saveTimer = setTimeout(go, 400);
}

// ------------------------------------------------------------------ revisão de máscaras
const revViewer = new Viewer($('#revCanvas'));
let revSubj = null, revMaskNii = null, revDirty = false;

function revPaths(id, kind) {
  if (kind === 'sctseg') return { base: `deriv/sct/${id}/${id}_t1.nii.gz`, mask: `deriv/sct/${id}/${id}_t1_seg.nii.gz` };
  const m = kind === 'dseg' ? `${id}_t1_cerebellum_dseg.nii.gz` : `${id}_t1_pcereb.nii.gz`;
  return { base: `deriv/suit/${id}/${id}_t1.nii.gz`, mask: `deriv/suit/${id}/${m}` };
}

async function renderRevList() {
  const kind = $('#revKind').value;
  const items = await Promise.all(state.subjects.map(async s => {
    const has = await proj.exists(revPaths(s.id, kind).mask);
    const r = state.review[s.id]?.[kind];
    const tag = r ? (r.decision === 'excluir' ? '<span class="bad">✕</span>' : r.decision === 'aprovado' ? '<span class="ok">✓</span>'
      : '<span class="warn">✎</span>') : '';
    return `<li data-id="${esc(s.id)}" class="${s.id === revSubj ? 'sel' : ''}"><span>${esc(s.id)}</span>
      <span>${has ? tag || '' : '<span class="note">sem arquivo</span>'}</span></li>`;
  }));
  $('#revList').innerHTML = items.join('') || '<li class="note">abra um projeto</li>';
  $$('#revList li[data-id]').forEach(li => li.addEventListener('click', () => openRev(li.dataset.id)));
}
$('#revKind').addEventListener('change', () => { revSubj = null; renderRevList(); });

async function openRev(id) {
  if (revDirty && !confirm('Há edições não salvas. Descartar?')) return;
  const kind = $('#revKind').value;
  const p = revPaths(id, kind);
  const [fb, fm] = [await proj.getFile(p.base), await proj.getFile(p.mask)];
  if (!fb || !fm) return toast('Arquivos ainda não gerados — rode a etapa 1 do script.');
  $('#revMsg').textContent = 'carregando…';
  const base = await loadNifti(fb);
  revMaskNii = await loadNifti(fm);
  const hb = base.header, hm = revMaskNii.header;
  if (hb.nx !== hm.nx || hb.ny !== hm.ny || hb.nz !== hm.nz) return toast('Máscara e T1 com dimensões diferentes');
  const u8 = new Uint8Array(revMaskNii.data.length);
  let sx = 0, sy = 0, sz = 0, n = 0;
  for (let i = 0; i < u8.length; i++) if (revMaskNii.data[i] > 0.5) {
    u8[i] = 1; n++;
    sx += i % hm.nx; sy += Math.floor(i / hm.nx) % hm.ny; sz += Math.floor(i / (hm.nx * hm.ny));
  }
  revSubj = id; revDirty = false;
  revViewer.setBase(base);
  revViewer.setView($('#revView').value);
  revViewer.setOverlay(u8, kind === 'sctseg' ? [255, 200, 0] : [255, 60, 60]);
  if (n) {   // vai para a fatia do centro da máscara
    const c = [sx / n, sy / n, sz / n].map(Math.round);
    revViewer.goTo(revViewer.ijkToScreen(c)[2]);
  }
  $('#revSlice').max = revViewer.nSlices - 1; $('#revSlice').value = revViewer.slice;
  const r = state.review[id]?.[kind];
  $('#revNote').value = r?.note || '';
  $('#revMsg').textContent = `${id}: ${n} voxels na máscara${r ? ` · ${r.decision}${r.edited ? ' (editada)' : ''}` : ''}`;
  renderRevList();
}
$$('#tab-revisao .tool').forEach(b => b.addEventListener('click', () => {
  $$('#tab-revisao .tool').forEach(x => x.classList.toggle('active', x === b));
  revViewer.mode = b.dataset.mode;
}));
$('#revRadius').addEventListener('change', e => { revViewer.brushRadius = +e.target.value; });
$('#rev3d').addEventListener('change', e => { revViewer.brush3d = e.target.checked; });
$('#revUndo').addEventListener('click', () => revViewer.undo());
$('#revView').addEventListener('change', e => { if (revViewer.nii) { revViewer.setView(e.target.value); $('#revSlice').max = revViewer.nSlices - 1; $('#revSlice').value = revViewer.slice; } });
$('#revSlice').addEventListener('input', e => revViewer.goTo(+e.target.value));
revViewer.onSlice = s => { $('#revSlice').value = s; };
$('#revAlpha').addEventListener('input', e => { revViewer.overlayAlpha = +e.target.value / 100; revViewer.draw(); });
revViewer.onChange = () => { revDirty = true; $('#revMsg').textContent = `${revSubj}: edições não salvas`; };
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'z' && $('#tab-revisao').classList.contains('active')) { e.preventDefault(); revViewer.undo(); }
});

$('#revSave').addEventListener('click', async () => {
  if (!revSubj) return;
  const kind = $('#revKind').value, p = revPaths(revSubj, kind);
  const orig = p.mask.replace(NII, '_orig.nii.gz');
  if (proj.writable && !(await proj.exists(orig))) await proj.copy(p.mask, orig);
  const out = await writeMaskLike(revMaskNii, revViewer.overlay);
  const r = await proj.writeFile(p.mask, out);
  revDirty = false;
  await setDecision('editado', true);
  $('#revMsg').textContent = r === 'saved' ? `Máscara salva (original em ${orig.split('/').pop()}).`
    : `Arquivo baixado: coloque em ${p.mask}`;
});
$$('#tab-revisao .dec').forEach(b => b.addEventListener('click', () => setDecision(b.dataset.dec)));
async function setDecision(decision, edited = false) {
  if (!revSubj) return;
  const kind = $('#revKind').value;
  const prev = state.review[revSubj]?.[kind];
  (state.review[revSubj] ||= {})[kind] = {
    decision: decision === 'editado' ? (prev?.decision === 'excluir' ? 'excluir' : 'editado') : decision,
    edited: edited || !!prev?.edited, note: $('#revNote').value, time: new Date().toISOString(),
  };
  await proj.writeFile('review.json', JSON.stringify(state.review, null, 1), { download: false });
  try { localStorage.setItem('enigma-review-' + proj.name, JSON.stringify(state.review)); } catch { /* */ }
  if (decision === 'excluir') toast('Marcado para exclusão — salve o config.json de novo para que o pipeline ignore este sujeito.', 4500);
  renderRevList();
}

// ------------------------------------------------------------------ resultados
$('#btnLoadRes').addEventListener('click', async () => {
  const t = await proj.readText('deriv/results/results.json');
  if (!t) return toast('deriv/results/results.json não encontrado — rode a etapa 2.');
  loadResults(JSON.parse(t));
});
$('#resFile').addEventListener('change', async e => loadResults(JSON.parse(await e.target.files[0].text())));
$('#roiTissue').addEventListener('change', () => state.results && renderRoi());
$('#roiMeasure').addEventListener('change', () => state.results && renderRoi());

function loadResults(r) {
  if (r.format !== 'enigma-results-v1') return toast('Arquivo de resultados em formato desconhecido');
  state.results = r;
  $('#resMsg').textContent = `${r.subjects.length} sujeitos · ${r.sct.length} linhas SCT · ${r.suit_rois.length} linhas SUIT`;
  renderStatus(); renderSct(); renderQc(); renderRoi();
}

const STEP_ORDER = ['suit_prepare', 'suit_isolate', 'suit_segment', 'suit_mask', 'suit_normalize', 'suit_reslice', 'suit_smooth',
  'sct_prepare', 'sct_segment', 'sct_labels', 'sct_register', 'sct_warp', 'sct_metrics'];
function renderStatus() {
  const r = state.results;
  const head = STEP_ORDER.map(s => `<th title="${s}">${s.replace(/^(suit|sct)_/, '')}</th>`).join('');
  const rows = r.subjects.map(s => {
    const st = r.status[s.id] || {};
    return `<tr><td>${esc(s.id)}</td>${STEP_ORDER.map(k => {
      const x = st[k];
      if (!x) return '<td class="note">·</td>';
      const cls = x.state === 'ok' ? 'ok' : x.state === 'error' ? 'bad' : 'warn';
      return `<td><span class="pill ${cls}" title="${esc(x.message || x.time || '')}">${x.state === 'ok' ? '✓' : x.state}</span></td>`;
    }).join('')}</tr>`;
  }).join('');
  $('#resStatus').innerHTML = `<table><thead><tr><th>sujeito</th>${head}</tr><tr><th></th>${STEP_ORDER.map(s =>
    `<th class="note">${s.startsWith('suit') ? 'SUIT' : 'SCT'}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>`;
}

const mean = a => a.reduce((x, y) => x + y, 0) / a.length;
const sd = a => a.length > 1 ? Math.sqrt(a.reduce((s, x) => s + (x - mean(a)) ** 2, 0) / (a.length - 1)) : NaN;
const fmt = (x, d = 2) => (x == null || Number.isNaN(x)) ? '—' : x.toFixed(d);
function cohenD(a, b) {     // (média CONT − média FRDA) / DP combinado, como no artigo do SCT
  if (a.length < 2 || b.length < 2) return NaN;
  const sp = Math.sqrt(((a.length - 1) * sd(a) ** 2 + (b.length - 1) * sd(b) ** 2) / (a.length + b.length - 2));
  return (mean(a) - mean(b)) / sp;
}
function groupSummary(rows, keyFn, valKey) {
  const groups = [...new Set(rows.map(r => r.group || '(sem grupo)'))].sort();
  const keys = [...new Set(rows.map(keyFn))];
  const out = keys.map(k => {
    const sub = rows.filter(r => keyFn(r) === k && r[valKey] != null);
    const by = Object.fromEntries(groups.map(g => [g, sub.filter(r => (r.group || '(sem grupo)') === g).map(r => +r[valKey])]));
    return { key: k, by, d: by.CONT && by.FRDA ? cohenD(by.CONT, by.FRDA) : NaN };
  });
  return { groups, out };
}
function summaryTable(title, { groups, out }, dec) {
  return `<table><thead><tr><th>${title}</th>${groups.map(g => `<th>${esc(g)} (n · média ± DP)</th>`).join('')}<th>d (CONT − FRDA)</th></tr></thead>
    <tbody>${out.map(o => `<tr><td>${esc(o.key)}</td>${groups.map(g => {
      const a = o.by[g] || []; return `<td>${a.length} · ${fmt(mean(a), dec)} ± ${fmt(sd(a), dec)}</td>`;
    }).join('')}<td>${fmt(o.d, 2)}</td></tr>`).join('')}</tbody></table>`;
}
function renderSct() {
  const rows = state.results.sct;
  if (!rows.length) { $('#resSct').innerHTML = '<p class="note">Sem dados do SCT.</p>'; return; }
  const lv = r => r.level;
  const levels = [...new Set(rows.map(lv))].sort();
  const subs = state.results.subjects;
  const wide = subs.map(s => {
    const o = { subject: s.id, site: s.site, group: s.group };
    levels.forEach(L => {
      const r = rows.find(x => x.subject === s.id && x.level === L);
      o[`${L}_csa_mm2`] = r?.csa_mm2 ?? ''; o[`${L}_eccentricity`] = r?.eccentricity ?? '';
    });
    return o;
  });
  $('#resSct').innerHTML =
    '<h3>Área de secção (mm²)</h3>' + summaryTable('nível', groupSummary(rows, lv, 'csa_mm2'), 1) +
    '<h3 style="margin-top:12px">Excentricidade</h3>' + summaryTable('nível', groupSummary(rows, lv, 'eccentricity'), 3) +
    `<div class="row"><button id="dlSct">Baixar tabela por sujeito (CSV)</button></div>`;
  $('#dlSct').onclick = () => downloadCsv('sct_por_sujeito.csv', wide);
}
function renderRoi() {
  const tissue = $('#roiTissue').value, m = $('#roiMeasure').value;
  const rows = state.results.suit_rois.filter(r => r.tissue === tissue);
  if (!rows.length) { $('#resRoi').innerHTML = '<p class="note">Sem dados do SUIT para este tecido.</p>'; return; }
  const nat = state.results.suit_native || [];
  const natHtml = nat.length ? '<h3>Volume nativo dentro da máscara (mL)</h3>' +
    summaryTable('medida', groupSummary(nat.flatMap(n => [
      { group: n.group, key: 'GM (seg1)', v: n.seg1_native_ml }, { group: n.group, key: 'WM (seg2)', v: n.seg2_native_ml }]),
    r => r.key, 'v'), 2) : '';
  const regions = [...new Set(rows.map(r => r.region))];
  const wide = state.results.subjects.map(s => {
    const o = { subject: s.id, site: s.site, group: s.group };
    regions.forEach(R => { o[R] = rows.find(x => x.subject === s.id && x.region === R)?.[m] ?? ''; });
    return o;
  });
  $('#resRoi').innerHTML = natHtml + `<h3 style="margin-top:12px">Regiões do atlas SUIT (${tissue}, ${m === 'mean' ? 'média' : 'mL'})</h3>` +
    summaryTable('região', groupSummary(rows, r => r.region, m), m === 'mean' ? 4 : 3) +
    `<div class="row"><button id="dlRoi">Baixar tabela por sujeito (CSV)</button></div>`;
  $('#dlRoi').onclick = () => downloadCsv(`suit_${tissue}_${m}.csv`, wide);
}
function renderQc() {
  const q = state.results.qc || [];
  if (!q.length) { $('#resQc').innerHTML = '<p class="note">Sem resultado de covariância (rode a etapa 2).</p>'; return; }
  const sites = [...new Set(q.map(r => r.site))];
  const W = 640, rowH = 70, H = sites.length * rowH + 30;
  const vals = q.map(r => +r.mean_cov), thr = q.map(r => +r.threshold);
  const lo = Math.min(...vals, ...thr) - 0.02, hi = Math.max(...vals) + 0.01;
  const X = v => 120 + (v - lo) / (hi - lo) * (W - 140);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px">`;
  sites.forEach((site, i) => {
    const y = 20 + i * rowH + rowH / 2;
    const rs = q.filter(r => r.site === site);
    svg += `<text x="8" y="${y + 4}" font-size="12">${esc(site)} (n=${rs.length})</text>`;
    svg += `<line x1="${X(+rs[0].threshold)}" x2="${X(+rs[0].threshold)}" y1="${y - 25}" y2="${y + 25}" stroke="#c62828" stroke-dasharray="4 3"/>`;
    rs.forEach((r, k) => {
      const fl = +r.flag_below_2sd === 1;
      const yy = y + ((k % 5) - 2) * 6;
      svg += `<circle cx="${X(+r.mean_cov)}" cy="${yy}" r="4.5" fill="${fl ? '#c62828' : '#1f6f8b'}" opacity=".85"><title>${esc(r.subject)}: ${r.mean_cov}</title></circle>`;
      if (fl) svg += `<text x="${X(+r.mean_cov) + 6}" y="${yy - 6}" font-size="11" fill="#c62828">${esc(r.subject)}</text>`;
    });
  });
  svg += `<text x="120" y="${H - 6}" font-size="11" fill="#5d6b7a">${lo.toFixed(3)}</text>
    <text x="${W - 60}" y="${H - 6}" font-size="11" fill="#5d6b7a">${hi.toFixed(3)}</text></svg>`;
  const flagged = q.filter(r => +r.flag_below_2sd === 1);
  $('#resQc').innerHTML = svg + `<p class="note">Correlação espacial média de cada imagem SUIT modulada (wd*_seg1) com as demais
    do mesmo centro; linha vermelha = média − 2 DP. Sinalizados: ${flagged.length ? flagged.map(r => esc(r.subject)).join(', ') : 'nenhum'}.
    Pelo protocolo, sinalizados são inspecionados e só excluídos se houver artefato ou falha de processamento.</p>`;
}

function downloadCsv(name, rows) {
  if (!rows.length) return;
  const keys = Object.keys(rows[0]);
  const q = v => { const s = String(v ?? ''); return /[",;\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csv = [keys.join(','), ...rows.map(r => keys.map(k => q(r[k])).join(','))].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = name; a.click();
}

// ------------------------------------------------------------------ PWA
if ('serviceWorker' in navigator && location.protocol !== 'file:') {
  navigator.serviceWorker.register('sw.js').catch(() => { /* sem SW em ambiente de teste */ });
}
window.__enigma = { state, proj, discViewer, revViewer, buildConfig, buildScript, loadResults };
