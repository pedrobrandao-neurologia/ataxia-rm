"""Controle de qualidade por covariância espacial — tradução de shared/cg_check_cov.m
(VBM toolbox, usado pelo ENIGMA). Para cada imagem normalizada, calcula a correlação média
com todas as outras do mesmo centro; imagens abaixo de média − 2 DP são sinalizadas
para inspeção (o artigo Harding 2021 só exclui se houver artefato ou movimento grave).
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import numpy as np
import nibabel as nib

log = logging.getLogger("enigma_pipeline")


def spm_global(data: np.ndarray) -> float:
    """spm_global: média dos voxels acima de (média global)/8."""
    d = data[np.isfinite(data)]
    m = d.mean()
    return float(d[d > m / 8].mean())


def check_covariance(files: list[Path], prop_scaling: bool = False,
                     nuisance: np.ndarray | None = None, sd: float = 2.0) -> dict:
    imgs = [nib.load(f) for f in files]
    n = len(imgs)
    if n < 3:
        raise ValueError("são necessárias pelo menos 3 imagens para a covariância")
    shape = imgs[0].shape[:3]
    for f, im in zip(files, imgs):
        if im.shape[:3] != shape:
            raise ValueError(f"dimensões diferentes: {f}")
        if np.max(np.abs(im.affine - imgs[0].affine)) > 1e-4:
            raise ValueError(f"orientação/voxel diferente: {f}")
    data = np.stack([np.asarray(im.dataobj, dtype=np.float64) for im in imgs], axis=-1)
    if prop_scaling:
        g = np.array([spm_global(data[..., i]) for i in range(n)])
        data *= (g.mean() / g)[None, None, None, :]
    if nuisance is not None:
        nuisance = np.atleast_2d(np.asarray(nuisance, dtype=float))
        if nuisance.shape[1] != n:
            nuisance = nuisance.T
        proj = np.linalg.pinv(nuisance) @ nuisance  # n x n

    ypy = np.zeros((n, n))
    for z in range(shape[2]):                       # fatia a fatia, como o original
        vol = data[:, :, z, :].reshape(-1, n)
        mean_slice = vol.mean(axis=1)
        mask = (mean_slice != 0) & np.isfinite(mean_slice)
        if not mask.any():
            continue
        v = vol[mask]
        if nuisance is not None:
            v = v - v @ proj
        v = v - v.mean(axis=0, keepdims=True)
        ypy += (v.T @ v) / n

    d = np.sqrt(np.diag(ypy))
    corr = ypy / (np.outer(d, d) + np.finfo(float).eps)
    big = np.abs(corr) > 1
    corr[big] = corr[big] / np.abs(corr[big])
    np.fill_diagonal(corr, np.sign(np.diag(corr)))

    mean_cov = np.array([np.delete(corr[i], i).mean() for i in range(n)])
    thr = mean_cov.mean() - sd * mean_cov.std(ddof=1)   # std do MATLAB = n−1
    upper = np.triu(corr, 1)
    identical = [(str(files[i]), str(files[j]), float(upper[i, j]))
                 for i, j in zip(*np.where(upper > 0.9))]
    return {"files": [str(f) for f in files], "mean_cov": mean_cov.tolist(),
            "threshold": float(thr), "flagged": (mean_cov < thr).tolist(),
            "corr": corr.round(4).tolist(), "high_pairs": identical}


def run_qc(cfg, kind: str = "wd1") -> Path:
    """Roda a covariância por centro para as imagens SUIT moduladas (wd*_seg1)."""
    from . import suit
    out_dir = cfg.output_dir / "qc"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_site: dict[str, list] = {}
    for s in cfg.subjects:
        f = suit.paths(cfg, s)[kind]
        if f.exists():
            by_site.setdefault(s.site, []).append((s, f))
        else:
            log.warning("  QC: %s sem %s (etapas do SUIT incompletas?)", s.id, f.name)
    rows, report = [], {}
    sd = float(cfg.qc.get("sd_threshold", 2.0))
    for site, items in by_site.items():
        if len(items) < 3:
            log.warning("  QC: centro %s com %d imagens — covariância não calculada", site, len(items))
            continue
        res = check_covariance([f for _, f in items],
                               prop_scaling=bool(cfg.qc.get("prop_scaling", False)), sd=sd)
        report[site] = {**res, "subjects": [s.id for s, _ in items]}
        for (s, _), mc, fl in zip(items, res["mean_cov"], res["flagged"]):
            rows.append({"subject": s.id, "site": site, "group": s.group,
                         "mean_cov": round(mc, 5), "threshold": round(res["threshold"], 5),
                         "flag_below_2sd": int(fl)})
    csv_path = out_dir / f"covariance_{kind}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["subject", "site", "group", "mean_cov", "threshold",
                                           "flag_below_2sd"])
        w.writeheader()
        w.writerows(rows)
    (out_dir / f"covariance_{kind}.json").write_text(json.dumps(report))
    log.info("QC salvo em %s", csv_path)
    return csv_path
