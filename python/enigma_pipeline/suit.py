"""Etapas do SUIT (cerebelo) com SUITPy + ANTsPy.

Correspondência com o código MATLAB do ENIGMA-Ataxia (ENIGMA_suit/*.m):

  CreateFolderSUIT.m      -> prepare()      (pasta por sujeito, T1/T2/máscara externa)
  RunSuitT1andT2.m        -> prepare()      (corregistro T2 -> T1; aqui ANTs Rigid, lá SPM coreg NMI)
  suit_isolate_seg        -> isolate()      (SUITPy: U-Net)  + segment() (ANTs Atropos: GM/WM)
  ImCalc (CERES*GM + WM)  -> make_mask()    (mesma expressão: ((ext>0)*GM + WM) > 0.1)
  suit_normalize_dartel   -> normalize()    (SUITPy: ANTs SyN para o template SUIT)
  suit_reslice_dartel     -> reslice_modulate()  (jactransf=1 -> modulação pelo jacobiano
                                                  da transformação afim + não linear)
  (suavização 3 mm FWHM do artigo Harding 2021) -> smooth()

ATENÇÃO: é outro método (SUITPy/ANTs) — os valores não são numericamente comparáveis aos
do ENIGMA-Ataxia (SUIT 3.2 / SPM12 / DARTEL).
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import numpy as np
import nibabel as nib

from .common import Config, Status, Subject, run_step

log = logging.getLogger("enigma_pipeline")


def _ants():
    import ants  # antspyx
    return ants


def _suitpy():
    import SUITPy
    return SUITPy


def subj_dir(cfg: Config, s: Subject) -> Path:
    d = cfg.output_dir / "suit" / s.id
    d.mkdir(parents=True, exist_ok=True)
    return d


def paths(cfg: Config, s: Subject) -> dict[str, Path]:
    d = subj_dir(cfg, s)
    n = s.id
    sp = cfg.suit.get("space", "SUIT")
    return {
        "dir": d,
        "t1": d / f"{n}_t1.nii.gz",
        "rt2": d / f"r{n}_t2.nii.gz",
        "ext": d / f"{n}_extmask.nii.gz",
        "dseg": d / f"{n}_t1_cerebellum_dseg.nii.gz",
        "n4": d / f"{n}_t1_n4.nii.gz",
        "seg1": d / f"{n}_t1_seg1.nii.gz",      # GM (probabilidade)
        "seg2": d / f"{n}_t1_seg2.nii.gz",      # WM (probabilidade)
        "pcereb": d / f"{n}_t1_pcereb.nii.gz",  # máscara final (editável no PWA)
        "deform": d / f"{n}_t1_to-{sp}_mode-image_xfm.nii.gz",
        "detj": d / f"{n}_t1_to-{sp}_mode-image_detJ.nii.gz",
        "norm": d / f"{n}_t1_space-{sp}.nii.gz",
        "wd1": d / f"wd{n}_t1_seg1.nii.gz",     # GM modulada em espaço SUIT (nome do ENIGMA)
        "wd2": d / f"wd{n}_t1_seg2.nii.gz",
        "native": d / f"{n}_native_volumes.json",
    }


# --------------------------------------------------------------------------- etapas

def prepare(cfg: Config, s: Subject) -> None:
    p = paths(cfg, s)
    _copy_as_gz(s.t1, p["t1"])
    if s.ext_mask and cfg.suit.get("external_mask", "auto") != "off":
        _copy_as_gz(s.ext_mask, p["ext"])
    if s.t2 and cfg.suit.get("use_t2", True):
        ants = _ants()
        t1 = ants.image_read(str(p["t1"]))
        t2 = ants.image_read(str(s.t2))
        # equivalente ao spm.spatial.coreg.estwrite (NMI) do RunSuitT1andT2.m
        reg = ants.registration(fixed=t1, moving=t2, type_of_transform="Rigid",
                                aff_metric="mattes")
        ants.image_write(reg["warpedmovout"], str(p["rt2"]))


def isolate(cfg: Config, s: Subject) -> None:
    p = paths(cfg, s)
    suit = _suitpy()
    t2 = str(p["rt2"]) if p["rt2"].exists() else None
    out = suit.isolate(t1_file=str(p["t1"]), t2_file=t2, result_folder=str(p["dir"]),
                       verbose=True)
    if out is None or not p["dseg"].exists():
        raise RuntimeError("SUITPy.isolate não gerou a máscara do cerebelo "
                           "(falha de registro ou memória insuficiente; precisa de > 8 GB de RAM)")


def segment(cfg: Config, s: Subject) -> None:
    """Probabilidades de GM (seg1) e WM (seg2) no espaço do T1, restritas à região do
    cerebelo dilatada. Substitui a segmentação de tecidos do suit_isolate_seg (SPM).

    suit.tissue_seg:
      "atropos_brain"       (padrão) Atropos k-means 3 classes no encéfalo inteiro — as médias
                            de GM/WM vêm do cérebro todo, como na segmentação unificada do SPM
      "atropos_cerebellum"  Atropos só dentro da região do cerebelo
      "external"            mapas próprios em input/<subj>_gm.nii.gz e <subj>_wm.nii.gz
                            (ex.: CAT12, SPM, FastSurfer), reamostrados para o T1
    """
    ants = _ants()
    p = paths(cfg, s)
    mode = cfg.suit.get("tissue_seg", "atropos_brain")
    t1 = ants.image_read(str(p["t1"]))
    dseg = ants.image_read(str(p["dseg"]))
    dil = int(cfg.suit.get("mask_dilation_vox", 3))
    region = ants.iMath(ants.threshold_image(dseg, 0.5, 1e9), "MD", dil)

    if mode == "external":
        from .common import find_image
        for tissue, key in (("gm", "seg1"), ("wm", "seg2")):
            src = find_image(cfg.input_dir, f"{s.id}_{tissue}")
            if src is None:
                raise RuntimeError(f"tissue_seg=external: falta {s.id}_{tissue}.nii(.gz) na entrada")
            img = ants.resample_image_to_target(ants.image_read(str(src)), t1, interp_type="linear")
            ants.image_write(img * region, str(p[key]))
        return

    if mode == "atropos_brain":
        work = _brain_mask(t1)
    elif mode == "atropos_cerebellum":
        work = region
    else:
        raise ValueError(f"tissue_seg desconhecido: {mode}")
    n4 = ants.n4_bias_field_correction(t1, mask=work)
    ants.image_write(n4, str(p["n4"]))
    mrf = float(cfg.suit.get("atropos_mrf", 0.1))
    seg = ants.atropos(a=n4, x=work, i="kmeans[3]", m=f"[{mrf},1x1x1]", c="[5,0]")
    probs = seg["probabilityimages"]  # k-means ordena por intensidade: LCR < GM < WM no T1
    ants.image_write(probs[1] * region, str(p["seg1"]))
    ants.image_write(probs[2] * region, str(p["seg2"]))


def _brain_mask(t1):
    """Máscara do encéfalo: template MNI (incluído no SUITPy) registrado ao T1 por
    transformação afim, máscara do template aplicada e levemente dilatada."""
    import os
    ants = _ants()
    tdir = os.path.join(os.path.dirname(_suitpy().__file__), "templates")
    tpl = ants.image_read(os.path.join(tdir, "tpl-MNI152NLin6Asym_T1w.nii.gz"))
    tmask = ants.image_read(os.path.join(tdir, "tpl-MNI152NLin6Asym-brain_mask.nii.gz"))
    reg = ants.registration(fixed=t1, moving=tpl, type_of_transform="Affine")
    m = ants.apply_transforms(fixed=t1, moving=tmask, transformlist=reg["fwdtransforms"],
                              interpolator="nearestNeighbor")
    return ants.iMath(ants.threshold_image(m, 0.5, 1e9), "MD", 2)


def make_mask(cfg: Config, s: Subject) -> None:
    """Expressão do ImCalc do ENIGMA: (((ext>0).*GM) + WM) > 0.1.
    Sem máscara externa, usa a máscara do SUITPy no lugar do CERES."""
    p = paths(cfg, s)
    gm_img = nib.load(p["seg1"])
    gm = gm_img.get_fdata()
    wm = nib.load(p["seg2"]).get_fdata()
    if p["ext"].exists():
        base = _resample_like(nib.load(p["ext"]), gm_img, order=0).get_fdata()
        src = "externa"
    else:
        base = nib.load(p["dseg"]).get_fdata()
        src = "SUITPy"
    mask = ((((base > 0) * gm) + wm) > 0.1).astype(np.uint8)
    out = nib.Nifti1Image(mask, gm_img.affine)
    out.header.set_data_dtype(np.uint8)
    nib.save(out, p["pcereb"])
    log.info("  máscara final (%s): %d voxels", src, int(mask.sum()))


def normalize(cfg: Config, s: Subject) -> None:
    p = paths(cfg, s)
    suit = _suitpy()
    suit.normalize(source_file=str(p["t1"]), mask_file=str(p["pcereb"]),
                   space=cfg.suit.get("space", "SUIT"),
                   write_normalized=True, write_deformation=True,
                   write_jacobian_determinant=True, result_folder=str(p["dir"]), verbose=1)
    for k in ("deform", "detj"):
        if not p[k].exists():
            raise RuntimeError(f"SUITPy.normalize não gerou {p[k].name}")


def reslice_modulate(cfg: Config, s: Subject) -> None:
    """suit_reslice_dartel com jactransf=1: GM/WM levadas ao espaço SUIT e multiplicadas
    pelo determinante do jacobiano (afim + não linear), preservando o volume nativo."""
    p = paths(cfg, s)
    suit = _suitpy()
    detj = nib.load(p["detj"])
    vols = {}
    for key_in, key_out in (("seg1", "wd1"), ("seg2", "wd2")):
        warped = suit.reslice_image(source_image=str(p[key_in]), deformation=str(p["deform"]),
                                    mask=str(p["pcereb"]), interp=1, mask_thr=0.5)
        j = detj if detj.shape[:3] == warped.shape[:3] else _resample_like(detj, warped, order=1)
        data = np.nan_to_num(warped.get_fdata()) * np.nan_to_num(j.get_fdata())
        out = nib.Nifti1Image(data.astype(np.float32), warped.affine)
        nib.save(out, p[key_out])
        vox_t = float(np.prod(out.header.get_zooms()[:3]))
        vols[f"{key_in}_template_ml"] = float(data.sum() * vox_t / 1000)
    # volumes no espaço nativo (dentro da máscara final), para conferir a modulação
    mask = nib.load(p["pcereb"]).get_fdata() > 0.5
    for key in ("seg1", "seg2"):
        img = nib.load(p[key])
        vox = float(np.prod(img.header.get_zooms()[:3]))
        vols[f"{key}_native_ml"] = float((img.get_fdata() * mask).sum() * vox / 1000)
    p["native"].write_text(json.dumps(vols, indent=2))
    log.info("  volumes (mL): %s", {k: round(v, 2) for k, v in vols.items()})


def smooth(cfg: Config, s: Subject) -> None:
    p = paths(cfg, s)
    fwhm = float(cfg.suit.get("fwhm", 3.0))
    for key in ("wd1", "wd2"):
        src = p[key]
        dst = src.with_name(f"s{int(fwhm) if fwhm.is_integer() else fwhm}{src.name}")
        nib.save(smooth_img(nib.load(src), fwhm), dst)


STEPS = [("suit_prepare", prepare), ("suit_isolate", isolate), ("suit_segment", segment),
         ("suit_mask", make_mask), ("suit_normalize", normalize),
         ("suit_reslice", reslice_modulate), ("suit_smooth", smooth)]


def run_subject(cfg: Config, s: Subject, force: bool = False, until: str | None = None,
                start: str | None = None) -> None:
    run_steps(STEPS, Status(subj_dir(cfg, s)), cfg, s, force, until, start)


def run_steps(steps, status, cfg, s, force=False, until=None, start=None) -> None:
    """Roda as etapas em ordem. Com `start`, refaz a partir dela (e todas as seguintes),
    útil depois de editar a máscara no PWA (start='suit_normalize')."""
    started = start is None
    for name, func in steps:
        if not started:
            started = name == start
            if not started:
                continue
        run_step(status, name, lambda f=func: f(cfg, s), force=force or start is not None)
        if until and name == until:
            break


# --------------------------------------------------------------------------- utilidades

def _copy_as_gz(src: Path, dst: Path) -> None:
    if str(src).endswith(".gz"):
        shutil.copyfile(src, dst)
    else:
        nib.save(nib.load(src), dst)


def _resample_like(img: nib.Nifti1Image, ref: nib.Nifti1Image, order: int = 1):
    from nibabel.processing import resample_from_to
    return resample_from_to(img, (ref.shape[:3], ref.affine), order=order)


def smooth_img(img: nib.Nifti1Image, fwhm_mm: float) -> nib.Nifti1Image:
    from scipy.ndimage import gaussian_filter
    zooms = np.array(img.header.get_zooms()[:3], dtype=float)
    sigma = fwhm_mm / np.sqrt(8 * np.log(2)) / zooms
    data = gaussian_filter(np.nan_to_num(img.get_fdata()), sigma=sigma, mode="constant")
    return nib.Nifti1Image(data.astype(np.float32), img.affine)
