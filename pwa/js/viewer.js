// Visualizador ortogonal simples (sagital/coronal/axial) com sobreposição de máscara,
// marcadores (rótulos de disco) e pincel/borracha para editar máscaras.
// Orientação: eixo de voxel dominante de cada eixo do mundo (RAS) pela matriz afim.
import { idx, robustRange } from './nifti.js';

const VIEWS = {
  // h = eixo horizontal da tela, v = vertical (de cima para baixo), f = eixo fixo (fatia)
  // dir: +1 => valores do mundo crescem para a direita/para cima
  sag: { h: 1, hDir: -1, v: 2, f: 0, lab: ['A', 'P', 'S', 'I'] },  // anterior à esquerda
  cor: { h: 0, hDir: -1, v: 2, f: 1, lab: ['D', 'E', 'S', 'I'] },  // convenção radiológica
  ax:  { h: 0, hDir: -1, v: 1, f: 2, lab: ['D', 'E', 'A', 'P'] },
};

export class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.view = 'sag';
    this.slice = 0;
    this.zoom = 1;
    this.markers = [];       // {ijk:[i,j,k], value, color, text}
    this.overlay = null;     // Uint8Array
    this.overlayColor = [255, 60, 60];
    this.overlayAlpha = 0.45;
    this.mode = 'none';      // 'none' | 'brush' | 'erase' | 'pick'
    this.brushRadius = 2;
    this.brush3d = false;
    this.undoStack = [];
    this.onPick = null;
    this.onChange = null;
    this._bindEvents();
  }

  setBase(nii) {
    this.nii = nii;
    const h = nii.header;
    this.dims = [h.nx, h.ny, h.nz];
    this.vox = [Math.abs(h.pixdim[1]) || 1, Math.abs(h.pixdim[2]) || 1, Math.abs(h.pixdim[3]) || 1];
    // mapa mundo->voxel: para cada eixo do mundo, eixo de voxel dominante e sinal
    const A = h.affine;
    this.w2v = [0, 1, 2].map(w => {
      let best = 0, bv = 0;
      for (let c = 0; c < 3; c++) { const v = Math.abs(A[w][c]); if (v > bv) { bv = v; best = c; } }
      return { axis: best, sign: Math.sign(A[w][best]) || 1 };
    });
    [this.lo, this.hi] = robustRange(nii.data);
    this.setView(this.view);
  }

  setOverlay(u8, color) { this.overlay = u8; if (color) this.overlayColor = color; this.undoStack = []; this.draw(); }

  setView(v) {
    this.view = v;
    const g = this._geom();
    this.slice = Math.floor(g.nf / 2);
    this.draw();
    return g.nf;
  }

  _geom() {
    const V = VIEWS[this.view];
    const H = this.w2v[V.h], Vv = this.w2v[V.v], F = this.w2v[V.f];
    return {
      V, H, Vv, F,
      nh: this.dims[H.axis], nv: this.dims[Vv.axis], nf: this.dims[F.axis],
      sh: this.vox[H.axis], sv: this.vox[Vv.axis],
    };
  }

  // coordenada de tela (x,y em pixels da fatia) -> ijk
  screenToIJK(x, y, g = this._geom()) {
    const ijk = [0, 0, 0];
    // horizontal: mundo cresce para a direita se hDir=+1
    const hi = (g.V.hDir * g.H.sign > 0) ? x : g.nh - 1 - x;
    // vertical: tela cresce para baixo; mundo (S/A) cresce para cima
    const vi = (g.Vv.sign > 0) ? g.nv - 1 - y : y;
    ijk[g.H.axis] = hi; ijk[g.Vv.axis] = vi; ijk[g.F.axis] = this.slice;
    return ijk;
  }

  ijkToScreen(ijk, g = this._geom()) {
    const hi = ijk[g.H.axis], vi = ijk[g.Vv.axis];
    const x = (g.V.hDir * g.H.sign > 0) ? hi : g.nh - 1 - hi;
    const y = (g.Vv.sign > 0) ? g.nv - 1 - vi : vi;
    return [x, y, ijk[g.F.axis]];
  }

  setWindow(lo, hi) { this.lo = lo; this.hi = hi; this.draw(); }

  draw() {
    if (!this.nii) return;
    const g = this._geom();
    const h = this.nii.header, d = this.nii.data;
    const img = new ImageData(g.nh, g.nv);
    const px = img.data;
    const range = (this.hi - this.lo) || 1;
    const [or, og, ob] = this.overlayColor, a = this.overlayAlpha;
    for (let y = 0; y < g.nv; y++) {
      for (let x = 0; x < g.nh; x++) {
        const [i, j, k] = this.screenToIJK(x, y, g);
        const n = idx(h, i, j, k);
        let v = (d[n] - this.lo) / range * 255;
        v = v < 0 ? 0 : v > 255 ? 255 : v;
        const p = (y * g.nh + x) * 4;
        if (this.overlay && this.overlay[n]) {
          px[p] = v * (1 - a) + or * a; px[p + 1] = v * (1 - a) + og * a; px[p + 2] = v * (1 - a) + ob * a;
        } else { px[p] = px[p + 1] = px[p + 2] = v; }
        px[p + 3] = 255;
      }
    }
    const off = document.createElement('canvas');
    off.width = g.nh; off.height = g.nv;
    off.getContext('2d').putImageData(img, 0, 0);
    // escala física (mm) com zoom
    const wmm = g.nh * g.sh, hmm = g.nv * g.sv;
    const W = this.canvas.clientWidth || 600, Hc = this.canvas.clientHeight || 600;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = W * dpr; this.canvas.height = Hc * dpr;
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, Hc);
    const scale = Math.min(W / wmm, Hc / hmm) * this.zoom;
    const dw = wmm * scale, dh = hmm * scale;
    this.draw_ = { x0: (W - dw) / 2, y0: (Hc - dh) / 2, sx: dw / g.nh, sy: dh / g.nv, g };
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(off, this.draw_.x0, this.draw_.y0, dw, dh);
    // marcadores
    for (const m of this.markers) {
      const [x, y, f] = this.ijkToScreen(m.ijk, g);
      const cx = this.draw_.x0 + (x + 0.5) * this.draw_.sx, cy = this.draw_.y0 + (y + 0.5) * this.draw_.sy;
      const onSlice = f === this.slice;
      ctx.strokeStyle = m.color || '#0f0'; ctx.fillStyle = m.color || '#0f0';
      ctx.globalAlpha = onSlice ? 1 : 0.35;
      ctx.beginPath(); ctx.arc(cx, cy, 5, 0, Math.PI * 2); ctx.lineWidth = 2; ctx.stroke();
      ctx.beginPath(); ctx.arc(cx, cy, 1.5, 0, Math.PI * 2); ctx.fill();
      ctx.font = 'bold 13px system-ui'; ctx.fillText(m.text || String(m.value), cx + 8, cy - 6);
      ctx.globalAlpha = 1;
    }
    // letras de orientação
    ctx.fillStyle = '#ffd400'; ctx.font = 'bold 14px system-ui';
    const L = g.V.lab;
    ctx.fillText(L[0], 8, Hc / 2); ctx.fillText(L[1], W - 18, Hc / 2);
    ctx.fillText(L[2], W / 2, 18); ctx.fillText(L[3], W / 2, Hc - 8);
    ctx.fillStyle = '#ccc'; ctx.font = '12px system-ui';
    ctx.fillText(`fatia ${this.slice + 1}/${g.nf}`, 8, 16);
  }

  _eventToXY(ev) {
    const r = this.canvas.getBoundingClientRect();
    const d = this.draw_;
    if (!d) return null;
    const x = Math.floor((ev.clientX - r.left - d.x0) / d.sx);
    const y = Math.floor((ev.clientY - r.top - d.y0) / d.sy);
    if (x < 0 || y < 0 || x >= d.g.nh || y >= d.g.nv) return null;
    return [x, y];
  }

  _paint(x, y, stroke) {
    const g = this._geom(), h = this.nii.header, r = this.brushRadius;
    const val = this.mode === 'brush' ? 1 : 0;
    const slices = this.brush3d ? [-r, r] : [0, 0];
    for (let dz = slices[0]; dz <= slices[1]; dz++) {
      for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
        if (dx * dx + dy * dy + dz * dz > r * r + 0.5) continue;
        const xx = x + dx, yy = y + dy;
        if (xx < 0 || yy < 0 || xx >= g.nh || yy >= g.nv) continue;
        const ijk = this.screenToIJK(xx, yy, g);
        ijk[g.F.axis] = this.slice + dz;
        if (ijk[g.F.axis] < 0 || ijk[g.F.axis] >= g.nf) continue;
        const n = idx(h, ...ijk);
        if (this.overlay[n] !== val) { stroke.push([n, this.overlay[n]]); this.overlay[n] = val; }
      }
    }
  }

  undo() {
    const s = this.undoStack.pop();
    if (!s) return;
    for (let t = s.length - 1; t >= 0; t--) this.overlay[s[t][0]] = s[t][1];
    this.draw(); this.onChange && this.onChange();
  }

  _bindEvents() {
    let painting = null;
    this.canvas.addEventListener('pointerdown', ev => {
      if (!this.nii) return;
      const xy = this._eventToXY(ev);
      if (!xy) return;
      if (this.mode === 'pick') {
        const ijk = this.screenToIJK(xy[0], xy[1]);
        this.onPick && this.onPick(ijk);
      } else if ((this.mode === 'brush' || this.mode === 'erase') && this.overlay) {
        painting = [];
        this.canvas.setPointerCapture(ev.pointerId);
        this._paint(xy[0], xy[1], painting); this.draw();
      }
    });
    this.canvas.addEventListener('pointermove', ev => {
      if (!painting) return;
      const xy = this._eventToXY(ev);
      if (xy) { this._paint(xy[0], xy[1], painting); this.draw(); }
    });
    const end = () => {
      if (painting) {
        if (painting.length) { this.undoStack.push(painting); this.onChange && this.onChange(); }
        painting = null;
      }
    };
    this.canvas.addEventListener('pointerup', end);
    this.canvas.addEventListener('pointercancel', end);
    this.canvas.addEventListener('wheel', ev => {
      if (!this.nii) return;
      ev.preventDefault();
      if (ev.ctrlKey || ev.metaKey) {
        this.zoom = Math.min(8, Math.max(0.5, this.zoom * (ev.deltaY < 0 ? 1.15 : 1 / 1.15)));
      } else {
        const g = this._geom();
        this.slice = Math.min(g.nf - 1, Math.max(0, this.slice + (ev.deltaY > 0 ? 1 : -1)));
        this.onSlice && this.onSlice(this.slice);
      }
      this.draw();
    }, { passive: false });
    new ResizeObserver(() => this.draw()).observe(this.canvas);
  }

  get nSlices() { return this.nii ? this._geom().nf : 0; }
  goTo(s) { this.slice = Math.min(this.nSlices - 1, Math.max(0, s)); this.draw(); }
}
