"""Extração por regiões (atlas SUIT de lóbulos e núcleos) e agregação dos resultados
SUIT + SCT em tabelas (CSV) e num results.json que o PWA abre."""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import numpy as np
import nibabel as nib

from . import suit, sct

log = logging.getLogger("enigma_pipeline")

ATLAS = "Diedrichsen_2009"
ATLAS_DSEG = "atl-Anatom_space-SUIT_dseg.nii"
ATLAS_TSV = "atl-Anatom.tsv"


def atlas_paths(cfg) -> tuple[Path, Path]:
    base = Path(cfg.suit.get("atlas_dir") or (cfg.output_dir / "atlases"))
    d = base / ATLAS
    return d / ATLAS_DSEG, d / ATLAS_TSV


def fetch_atlas(cfg) -> None:
    """Baixa o atlas de lóbulos (Diedrichsen 2009) do repositório cerebellar_atlases
    (github.com/DiedrichsenLab/cerebellar_atlases)."""
    import urllib.request
    dseg, tsv = atlas_paths(cfg)
    dseg.parent.mkdir(parents=True, exist_ok=True)
    raw = "https://raw.githubusercontent.com/DiedrichsenLab/cerebellar_atlases/master/" + ATLAS
    try:
        for f in (dseg, tsv):
            log.info("baixando %s", f.name)
            urllib.request.urlretrieve(f"{raw}/{f.name}", f)
    except Exception as e:  # alternativa: função do próprio SUITPy
        log.warning("download direto falhou (%s); tentando SUITPy.fetch_atlas", e)
        import SUITPy
        SUITPy.fetch_atlas(ATLAS, atlas_dir=str(dseg.parent.parent), maps="atl-Anatom", space="SUIT")
    if not dseg.exists():
        raise RuntimeError(f"atlas não encontrado após download: {dseg}")
    if not tsv.exists():
        raise RuntimeError(f"tabela de rótulos não encontrada: {tsv}")


def read_lut(tsv: Path) -> dict[int, str]:
    lut = {}
    with open(tsv, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            lut[int(row["index"])] = row["name"]
    return lut


def roi_table(cfg) -> list[dict]:
    from nibabel.processing import resample_from_to
    dseg_p, tsv_p = atlas_paths(cfg)
    if not dseg_p.exists():
        fetch_atlas(cfg)
    lut = read_lut(tsv_p)
    atlas = nib.load(dseg_p)
    rows = []
    for s in cfg.subjects:
        p = suit.paths(cfg, s)
        for key, tissue in (("wd1", "GM"), ("wd2", "WM")):
            if not p[key].exists():
                continue
            img = nib.load(p[key])
            data = np.nan_to_num(img.get_fdata())
            # reamostra o atlas pelo espaço do mundo (o atlas tem eixo x invertido em
            # relação ao template SUIT do SUITPy)
            lab = resample_from_to(atlas, (img.shape[:3], img.affine), order=0).get_fdata()
            lab = np.rint(lab).astype(int)
            vox = float(np.prod(img.header.get_zooms()[:3]))
            for idx, name in lut.items():
                m = lab == idx
                if not m.any():
                    continue
                rows.append({"subject": s.id, "site": s.site, "group": s.group,
                             "tissue": tissue, "region": name,
                             "volume_ml": round(float(data[m].sum() * vox / 1000), 5),
                             "mean": round(float(data[m].mean()), 6)})
    return rows


def sct_table(cfg) -> list[dict]:
    rows = []
    for s in cfg.subjects:
        f = sct.paths(cfg, s)["csv"]
        if not f.exists():
            continue
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                lvl = r.get("VertLevel", "")
                rows.append({"subject": s.id, "site": s.site, "group": s.group,
                             "level": f"C{lvl}" if lvl and lvl.isdigit() else lvl,
                             "csa_mm2": _num(r.get("MEAN(area)")),
                             "eccentricity": _num(r.get("MEAN(eccentricity)")),
                             "slices": r.get("Slice (I->S)", "")})
    return rows


def collect(cfg) -> Path:
    out = cfg.output_dir / "results"
    out.mkdir(parents=True, exist_ok=True)
    sct_rows = sct_table(cfg)
    try:
        roi_rows = roi_table(cfg)
    except Exception as e:  # atlas indisponível não impede as demais tabelas
        log.warning("ROIs do SUIT não extraídas: %s", e)
        roi_rows = []
    native = []
    for s in cfg.subjects:
        f = suit.paths(cfg, s)["native"]
        if f.exists():
            native.append({"subject": s.id, "site": s.site, "group": s.group,
                           **json.loads(f.read_text())})
    status = {}
    for s in cfg.subjects:
        st = {}
        for mod in (suit, sct):
            sf = mod.subj_dir(cfg, s) / "status.json"
            if sf.exists():
                st.update(json.loads(sf.read_text()))
        status[s.id] = st
    qc_rows = []
    qc_csv = cfg.output_dir / "qc" / "covariance_wd1.csv"
    if qc_csv.exists():
        with open(qc_csv, encoding="utf-8") as fh:
            qc_rows = list(csv.DictReader(fh))

    _write_csv(out / "sct_spinal_cord.csv", sct_rows)
    _write_csv(out / "suit_rois_long.csv", roi_rows)
    _write_csv(out / "suit_native_volumes.csv", native)
    bundle = {"format": "enigma-results-v1", "project": cfg.project,
              "subjects": [{"id": s.id, "site": s.site, "group": s.group} for s in cfg.subjects],
              "sct": sct_rows, "suit_rois": roi_rows, "suit_native": native,
              "qc": qc_rows, "status": status}
    path = out / "results.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False))
    log.info("resultados em %s", out)
    return path


def _num(v):
    try:
        return round(float(v), 5)
    except (TypeError, ValueError):
        return None


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    keys = list(rows[0].keys())
    for r in rows[1:]:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
