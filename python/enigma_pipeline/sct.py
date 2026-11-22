"""Etapas do Spinal Cord Toolbox — tradução de SpinalCord/ENIGMA_sctGUI.m e
ENIGMA_sctGUIdataextract.m. O SCT já é Python; aqui os comandos são chamados pela CLI
oficial, com os mesmos parâmetros da interface MATLAB do ENIGMA.

  ENIGMA_sctGUI.m (linha 112)  sct_deepseg_sc -i T1 -c t1 -qc         -> segment()
  ENIGMA_sctGUI.m (linha 529)  sct_label_utils -create-viewer 3,4     -> disc_labels()
                               (ou rótulos marcados no PWA: labels.py)
  ENIGMA_sctGUI.m (linha 260)  sct_register_to_template ... -param    -> register()
  ENIGMA_sctGUI.m (linha 285)  sct_warp_template                      -> warp_template()
  ENIGMA_sctGUIdataextract.m   sct_process_segmentation -perlevel 1   -> metrics()
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import nibabel as nib

from .common import Config, Status, Subject, run_cmd
from . import labels as labels_mod
from .suit import run_steps

log = logging.getLogger("enigma_pipeline")

# parâmetros idênticos ao ENIGMA_sctGUI.m
REGISTER_PARAM = ("step=1,type=seg,algo=centermassrot:"
                  "step=2,type=seg,algo=bsplinesyn,slicewise=1")


def subj_dir(cfg: Config, s: Subject) -> Path:
    d = cfg.output_dir / "sct" / s.id
    d.mkdir(parents=True, exist_ok=True)
    return d


def paths(cfg: Config, s: Subject) -> dict[str, Path]:
    d = subj_dir(cfg, s)
    n = s.id
    return {
        "dir": d,
        "log": d / "sct_commands.log",
        "t1": d / f"{n}_t1.nii.gz",
        "seg": d / f"{n}_t1_seg.nii.gz",
        "disc": d / f"{n}_t1_labels_disc.nii.gz",
        "reg": d / "reg",
        "warp_t2a": d / "reg" / "warp_template2anat.nii.gz",
        "label": d / "label",
        "levels": d / "label" / "template" / "PAM50_levels.nii.gz",
        "csv": d / f"{n}_CSA.csv",
        "qc": cfg.output_dir / "sct_qc",
    }


def prepare(cfg: Config, s: Subject) -> None:
    p = paths(cfg, s)
    if str(s.t1).endswith(".gz"):
        shutil.copyfile(s.t1, p["t1"])
    else:
        nib.save(nib.load(s.t1), p["t1"])


def segment(cfg: Config, s: Subject) -> None:
    p = paths(cfg, s)
    engine = cfg.sct.get("seg_engine", "deepseg_sc")
    if engine == "deepseg_sc":   # comando do ENIGMA (obsoleto no SCT 7.x, ainda disponível)
        cmd = ["sct_deepseg_sc", "-i", p["t1"].name, "-c", "t1", "-o", p["seg"].name,
               "-qc", str(p["qc"])]
    elif engine == "deepseg":    # modelo atual do SCT (contrast-agnostic)
        cmd = ["sct_deepseg", "spinalcord", "-i", p["t1"].name, "-o", p["seg"].name,
               "-qc", str(p["qc"])]
    else:
        raise ValueError(f"seg_engine desconhecido: {engine}")
    run_cmd(cmd, p["dir"], p["log"])


def disc_labels(cfg: Config, s: Subject) -> None:
    """Rótulos dos discos (3 = C2/C3, 4 = C3/C4, na ponta posterior do disco).
    Prioridade: arquivo exportado pelo PWA; senão, o visualizador do próprio SCT."""
    p = paths(cfg, s)
    lf = cfg.sct.get("labels_file")
    if lf:
        lf_path = Path(lf) if Path(lf).is_absolute() else cfg.base_dir / lf
        if lf_path.exists() and labels_mod.has_subject(lf_path, s.id):
            labels_mod.write_disc_labels(lf_path, s.id, p["t1"], p["disc"])
            return
    if cfg.sct.get("interactive_labels", False):
        cmd = ["sct_label_utils", "-i", p["t1"].name, "-create-viewer", "3,4",
               "-o", p["disc"].name, "-msg",
               "Place label at the posterior tip of each inter-vertebral disc. E.g. label 3: C2/C3."]
        run_cmd(cmd, p["dir"], p["log"])
        return
    raise RuntimeError(f"sem rótulos de disco para {s.id}: marque C2/C3 e C3/C4 no PWA e "
                       f"exporte o disc_labels.json (ou ative sct.interactive_labels)")


def register(cfg: Config, s: Subject) -> None:
    p = paths(cfg, s)
    cmd = ["sct_register_to_template", "-i", p["t1"].name, "-s", p["seg"].name,
           "-ldisc", p["disc"].name, "-param", REGISTER_PARAM, "-c", "t1",
           "-ofolder", "reg", "-qc", str(p["qc"])]
    run_cmd(cmd, p["dir"], p["log"])


def warp_template(cfg: Config, s: Subject) -> None:
    p = paths(cfg, s)
    cmd = ["sct_warp_template", "-d", p["t1"].name, "-w", str(p["warp_t2a"].relative_to(p["dir"])),
           "-ofolder", "label", "-qc", str(p["qc"])]
    run_cmd(cmd, p["dir"], p["log"])


def metrics(cfg: Config, s: Subject) -> None:
    """Área de secção (MEAN(area)) e excentricidade (MEAN(eccentricity)) por nível.
    vert = '1:4' reproduz o artigo (C1–C4); o script do repositório usava '2:3'."""
    p = paths(cfg, s)
    vert = str(cfg.sct.get("vert", "1:4"))
    cmd = ["sct_process_segmentation", "-i", p["seg"].name, "-vert", vert, "-perlevel", "1",
           "-vertfile", str(p["levels"].relative_to(p["dir"])), "-o", p["csv"].name]
    run_cmd(cmd, p["dir"], p["log"])


STEPS = [("sct_prepare", prepare), ("sct_segment", segment), ("sct_labels", disc_labels),
         ("sct_register", register), ("sct_warp", warp_template), ("sct_metrics", metrics)]


def run_subject(cfg: Config, s: Subject, force: bool = False, until: str | None = None,
                start: str | None = None) -> None:
    run_steps(STEPS, Status(subj_dir(cfg, s)), cfg, s, force, until, start)


def check_installation() -> str:
    """Retorna a versão do SCT instalada (ou lança erro com instrução)."""
    import subprocess
    exe = shutil.which("sct_version") or shutil.which("sct_check_dependencies")
    if not exe:
        raise RuntimeError("Spinal Cord Toolbox não encontrado no PATH. Instale em "
                           "https://spinalcordtoolbox.com e abra um novo terminal.")
    out = subprocess.run([shutil.which("sct_version") or exe], capture_output=True, text=True)
    return (out.stdout or out.stderr).strip().splitlines()[-1] if (out.stdout or out.stderr) else "?"
