// Leitura e escrita de NIfTI-1/NIfTI-2 (.nii e .nii.gz) no navegador, sem dependências.
// gzip via DecompressionStream/CompressionStream (Chrome, Edge, Safari 16.4+, Firefox 113+).

export async function gunzipIfNeeded(buf) {
  const u8 = new Uint8Array(buf);
  if (u8[0] === 0x1f && u8[1] === 0x8b) {
    const ds = new DecompressionStream('gzip');
    const out = await new Response(new Blob([buf]).stream().pipeThrough(ds)).arrayBuffer();
    return out;
  }
  return buf;
}

export async function gzip(buf) {
  const cs = new CompressionStream('gzip');
  return await new Response(new Blob([buf]).stream().pipeThrough(cs)).arrayBuffer();
}

const DT = {
  2: [Uint8Array, 1], 4: [Int16Array, 2], 8: [Int32Array, 4], 16: [Float32Array, 4],
  64: [Float64Array, 8], 256: [Int8Array, 1], 512: [Uint16Array, 2], 768: [Uint32Array, 4],
};

export function parseNifti(buffer) {
  const dv = new DataView(buffer);
  let le = true;
  let size = dv.getInt32(0, true);
  if (size !== 348 && size !== 540) { le = false; size = dv.getInt32(0, false); }
  if (size !== 348 && size !== 540) throw new Error('arquivo não é NIfTI (sizeof_hdr inválido)');
  const v2 = size === 540;
  const h = { version: v2 ? 2 : 1, littleEndian: le, headerBytes: size };
  const dim = [];
  let datatype, bitpix, voxOffset, slope, inter, pixdim = [], qform, sform, quat, srow;
  if (!v2) {
    for (let i = 0; i < 8; i++) dim.push(dv.getInt16(40 + 2 * i, le));
    datatype = dv.getInt16(70, le); bitpix = dv.getInt16(72, le);
    for (let i = 0; i < 8; i++) pixdim.push(dv.getFloat32(76 + 4 * i, le));
    voxOffset = dv.getFloat32(108, le);
    slope = dv.getFloat32(112, le); inter = dv.getFloat32(116, le);
    qform = dv.getInt16(252, le); sform = dv.getInt16(254, le);
    quat = [256, 260, 264, 268, 272, 276].map(o => dv.getFloat32(o, le));
    srow = [280, 296, 312].map(o => [0, 1, 2, 3].map(k => dv.getFloat32(o + 4 * k, le)));
  } else {
    datatype = dv.getInt16(12, le); bitpix = dv.getInt16(14, le);
    for (let i = 0; i < 8; i++) dim.push(Number(dv.getBigInt64(16 + 8 * i, le)));
    for (let i = 0; i < 8; i++) pixdim.push(dv.getFloat64(104 + 8 * i, le));
    voxOffset = Number(dv.getBigInt64(168, le));
    slope = dv.getFloat64(176, le); inter = dv.getFloat64(184, le);
    qform = dv.getInt32(344, le); sform = dv.getInt32(348, le);
    quat = [352, 360, 368, 376, 384, 392].map(o => dv.getFloat64(o, le));
    srow = [400, 432, 464].map(o => [0, 1, 2, 3].map(k => dv.getFloat64(o + 8 * k, le)));
  }
  const nx = dim[1], ny = dim[2], nz = Math.max(1, dim[3]);
  if (!DT[datatype]) throw new Error(`tipo de dado NIfTI não suportado: ${datatype}`);
  const [Arr, bytes] = DT[datatype];
  const n = nx * ny * nz;   // só o 1º volume, se 4D
  const off = Math.round(voxOffset) || (v2 ? 544 : 352);
  let raw;
  const nativeLE = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;
  if (le === nativeLE && off % bytes === 0) {
    raw = new Arr(buffer, off, n);
  } else {
    raw = new Arr(n);
    const get = { 1: 'getUint8', 2: datatype === 4 ? 'getInt16' : 'getUint16',
      4: datatype === 16 ? 'getFloat32' : (datatype === 8 ? 'getInt32' : 'getUint32'), 8: 'getFloat64' }[bytes];
    for (let i = 0; i < n; i++) raw[i] = bytes === 1 ? dv.getUint8(off + i) : dv[get](off + i * bytes, le);
    if (datatype === 256) raw = new Int8Array(raw.buffer);
  }
  const useSlope = slope && isFinite(slope) && !(slope === 1 && !inter);
  let data = raw;
  if (useSlope) {
    data = new Float32Array(n);
    for (let i = 0; i < n; i++) data[i] = raw[i] * slope + (inter || 0);
  }
  const affine = computeAffine({ qform, sform, quat, srow, pixdim });
  Object.assign(h, { dim, nx, ny, nz, datatype, bitpix, pixdim, voxOffset: off, slope, inter, affine,
    qformCode: qform, sformCode: sform });
  return { header: h, data, headerBuffer: buffer.slice(0, off) };
}

function computeAffine({ qform, sform, quat, srow, pixdim }) {
  if (sform > 0) return [...srow, [0, 0, 0, 1]];
  if (qform > 0) {
    let [b, c, d, qx, qy, qz] = quat;
    let a = 1 - (b * b + c * c + d * d);
    if (a < 1e-7) { a = 1 / Math.hypot(b, c, d); b *= a; c *= a; d *= a; a = 0; } else a = Math.sqrt(a);
    const qfac = pixdim[0] < 0 ? -1 : 1;
    const [dx, dy, dz] = [pixdim[1], pixdim[2], pixdim[3] * qfac];
    const R = [
      [a * a + b * b - c * c - d * d, 2 * (b * c - a * d), 2 * (b * d + a * c)],
      [2 * (b * c + a * d), a * a + c * c - b * b - d * d, 2 * (c * d - a * b)],
      [2 * (b * d - a * c), 2 * (c * d + a * b), a * a + d * d - c * c - b * b],
    ];
    return [
      [R[0][0] * dx, R[0][1] * dy, R[0][2] * dz, qx],
      [R[1][0] * dx, R[1][1] * dy, R[1][2] * dz, qy],
      [R[2][0] * dx, R[2][1] * dy, R[2][2] * dz, qz],
      [0, 0, 0, 1]];
  }
  return [[pixdim[1], 0, 0, 0], [0, pixdim[2], 0, 0], [0, 0, pixdim[3], 0], [0, 0, 0, 1]];
}

export async function loadNifti(fileOrBuffer) {
  const buf = fileOrBuffer instanceof ArrayBuffer ? fileOrBuffer : await fileOrBuffer.arrayBuffer();
  return parseNifti(await gunzipIfNeeded(buf));
}

// Escreve uma máscara uint8 com a MESMA geometria (cabeçalho copiado) da imagem de referência.
// Funciona para NIfTI-1 e NIfTI-2; preserva sform/qform.
export async function writeMaskLike(ref, maskU8, { compress = true } = {}) {
  const h = ref.header;
  const hdr = new Uint8Array(ref.headerBuffer.slice(0, h.headerBytes));
  const off = h.version === 2 ? 544 : 352;
  const out = new ArrayBuffer(off + maskU8.length);
  const u8 = new Uint8Array(out);
  u8.set(hdr, 0);
  const dv = new DataView(out);
  const le = h.littleEndian;
  if (h.version === 1) {
    dv.setInt16(40, 3, le);                         // dim[0] = 3
    dv.setInt16(48, 1, le);                         // dim[4] = 1
    dv.setInt16(70, 2, le); dv.setInt16(72, 8, le); // uint8
    dv.setFloat32(108, off, le);
    dv.setFloat32(112, 1, le); dv.setFloat32(116, 0, le);
    dv.setFloat32(124, 1, le); dv.setFloat32(128, 0, le); // cal_max/min
  } else {
    dv.setBigInt64(16, 3n, le); dv.setBigInt64(40, 1n, le);
    dv.setInt16(12, 2, le); dv.setInt16(14, 8, le);
    dv.setBigInt64(168, BigInt(off), le);
    dv.setFloat64(176, 1, le); dv.setFloat64(184, 0, le);
  }
  u8.set(maskU8, off);
  return compress ? await gzip(out) : out;
}

// Índice linear (ordem do NIfTI: x mais rápido)
export const idx = (h, i, j, k) => i + j * h.nx + k * h.nx * h.ny;

export function robustRange(data, step = 7) {
  const vals = [];
  for (let i = 0; i < data.length; i += step) { const v = data[i]; if (v !== 0 && isFinite(v)) vals.push(v); }
  if (!vals.length) return [0, 1];
  vals.sort((a, b) => a - b);
  return [vals[Math.floor(vals.length * 0.01)], vals[Math.floor(vals.length * 0.995)]];
}
