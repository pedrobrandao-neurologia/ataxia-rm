"""Converte as marcações de disco feitas no PWA (disc_labels.json) em NIfTI de rótulos
no formato esperado por sct_register_to_template -ldisc (valor 3 = disco C2/C3,
valor 4 = disco C3/C4), substituindo o visualizador sct_label_utils -create-viewer.

Formato do JSON (gerado pelo PWA):
{
  "format": "enigma-disc-labels-v1",
  "subjects": {
    "sub01": {"shape": [256, 256, 176],
              "labels": [{"value": 3, "ijk": [120, 98, 60]}, {"value": 4, "ijk": [121, 92, 48]}]}
  }
}
ijk = índices de voxel na matriz do NIfTI original (0-based), sem reorientação.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import nibabel as nib

VALID = {3: "C2/C3", 4: "C3/C4"}


def load(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != "enigma-disc-labels-v1":
        raise ValueError(f"{path}: formato desconhecido ({data.get('format')})")
    return data


def has_subject(path: Path, sid: str) -> bool:
    return sid in load(path).get("subjects", {})


def write_disc_labels(path: Path, sid: str, t1_file: Path, out_file: Path) -> None:
    entry = load(path)["subjects"][sid]
    img = nib.load(t1_file)
    shape = tuple(int(x) for x in img.shape[:3])
    if tuple(entry.get("shape", shape)) != shape:
        raise ValueError(f"{sid}: dimensões do T1 ({shape}) diferem das usadas na marcação "
                         f"({tuple(entry['shape'])}) — marque de novo no PWA com este arquivo")
    lab = np.zeros(shape, dtype=np.uint8)
    seen = set()
    for item in entry["labels"]:
        v = int(item["value"])
        if v not in VALID:
            raise ValueError(f"{sid}: valor de rótulo inválido {v} (use 3 ou 4)")
        i, j, k = (int(round(c)) for c in item["ijk"])
        if not (0 <= i < shape[0] and 0 <= j < shape[1] and 0 <= k < shape[2]):
            raise ValueError(f"{sid}: rótulo {v} fora da imagem: {(i, j, k)}")
        lab[i, j, k] = v
        seen.add(v)
    missing = set(VALID) - seen
    if missing:
        raise ValueError(f"{sid}: faltam os rótulos {[VALID[m] for m in sorted(missing)]}")
    out = nib.Nifti1Image(lab, img.affine, img.header)
    out.set_data_dtype(np.uint8)
    out.header.set_slope_inter(1, 0)
    nib.save(out, out_file)
