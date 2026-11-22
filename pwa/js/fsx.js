// Acesso à pasta do projeto.
// Modo 1 (Chrome/Edge): File System Access API — lê e grava direto na pasta.
// Modo 2 (outros navegadores): seleção de pasta só leitura; gravações viram downloads.

const DB = 'enigma-suit-sct', STORE = 'handles';

function idb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE);
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}
async function idbSet(k, v) {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction(STORE, 'readwrite'); tx.objectStore(STORE).put(v, k);
    tx.oncomplete = res; tx.onerror = () => rej(tx.error);
  });
}
async function idbGet(k) {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction(STORE, 'readonly'); const r = tx.objectStore(STORE).get(k);
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
}

export const canWrite = 'showDirectoryPicker' in window;

export class Project {
  constructor() { this.root = null; this.files = null; this.name = ''; }

  get writable() { return !!this.root; }

  async pick() {
    if (canWrite) {
      this.root = await window.showDirectoryPicker({ id: 'enigma-projeto', mode: 'readwrite' });
      this.name = this.root.name;
      await idbSet('root', this.root);
      return true;
    }
    return false;
  }

  async restore() {
    if (!canWrite) return false;
    const h = await idbGet('root');
    if (!h) return false;
    const perm = await h.queryPermission({ mode: 'readwrite' });
    this.root = h; this.name = h.name;
    return perm === 'granted' ? true : 'needs-permission';
  }

  async requestPermission() {
    return (await this.root.requestPermission({ mode: 'readwrite' })) === 'granted';
  }

  // modo leitura (input webkitdirectory): FileList -> mapa caminho relativo -> File
  useFileList(fileList) {
    this.files = new Map();
    for (const f of fileList) {
      const parts = f.webkitRelativePath.split('/');
      this.name = parts[0];
      this.files.set(parts.slice(1).join('/'), f);
    }
  }

  async _dir(path, create = false) {
    let d = this.root;
    for (const p of path.split('/').filter(Boolean)) d = await d.getDirectoryHandle(p, { create });
    return d;
  }

  async list(path = '') {
    if (this.root) {
      try {
        const d = await this._dir(path);
        const out = [];
        for await (const [name, h] of d.entries()) out.push({ name, kind: h.kind });
        return out.sort((a, b) => a.name.localeCompare(b.name));
      } catch { return []; }
    }
    if (this.files) {
      const pre = path ? path.replace(/\/$/, '') + '/' : '';
      const seen = new Map();
      for (const k of this.files.keys()) {
        if (!k.startsWith(pre)) continue;
        const rest = k.slice(pre.length).split('/');
        seen.set(rest[0], rest.length > 1 ? 'directory' : 'file');
      }
      return [...seen].map(([name, kind]) => ({ name, kind })).sort((a, b) => a.name.localeCompare(b.name));
    }
    return [];
  }

  async getFile(path) {
    if (this.root) {
      const parts = path.split('/');
      const name = parts.pop();
      try { return await (await (await this._dir(parts.join('/'))).getFileHandle(name)).getFile(); }
      catch { return null; }
    }
    return this.files?.get(path) || null;
  }

  async exists(path) { return !!(await this.getFile(path)); }

  async readText(path) { const f = await this.getFile(path); return f ? await f.text() : null; }

  async writeFile(path, data, { download = true } = {}) {
    const parts = path.split('/');
    const name = parts.pop();
    if (this.root) {
      const d = await this._dir(parts.join('/'), true);
      const fh = await d.getFileHandle(name, { create: true });
      const w = await fh.createWritable();
      await w.write(data); await w.close();
      return 'saved';
    }
    if (download) {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([data]));
      a.download = name; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      return 'downloaded';
    }
    return 'skipped';
  }

  async copy(src, dst) {
    const f = await this.getFile(src);
    if (!f) return false;
    await this.writeFile(dst, await f.arrayBuffer(), { download: false });
    return true;
  }
}
