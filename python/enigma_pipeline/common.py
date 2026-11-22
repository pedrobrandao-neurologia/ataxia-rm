"""Utilidades comuns: config, sujeitos, logs, execução de comandos e retomada."""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("enigma_pipeline")

NII_EXTS = (".nii.gz", ".nii")


def strip_nii(name: str) -> str:
    for ext in NII_EXTS:
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def find_image(folder: Path, stem: str) -> Optional[Path]:
    for ext in NII_EXTS:
        p = folder / f"{stem}{ext}"
        if p.exists():
            return p
    return None


@dataclass
class Subject:
    id: str
    t1: Path
    t2: Optional[Path] = None
    ext_mask: Optional[Path] = None   # máscara externa do cerebelo (CERES ou outra)
    site: str = "site1"
    group: str = ""


@dataclass
class Config:
    input_dir: Path
    output_dir: Path
    subjects: list[Subject] = field(default_factory=list)
    suit: dict = field(default_factory=dict)
    sct: dict = field(default_factory=dict)
    qc: dict = field(default_factory=dict)
    project: str = "enigma"
    base_dir: Path = Path(".")
    excluded: list[str] = field(default_factory=list)

    @staticmethod
    def load(path: str | Path) -> "Config":
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        base = path.parent
        input_dir = (base / raw.get("input_dir", ".")).resolve()
        output_dir = (base / raw.get("output_dir", "derivatives")).resolve()
        subjects: list[Subject] = []
        for s in raw.get("subjects", []):
            def res(key):
                v = s.get(key)
                if not v:
                    return None
                p = Path(v)
                return p if p.is_absolute() else (input_dir / p)
            subjects.append(Subject(id=s["id"], t1=res("t1"), t2=res("t2"),
                                    ext_mask=res("ext_mask"),
                                    site=s.get("site", "site1"), group=s.get("group", "")))
        if not subjects:
            subjects = discover_subjects(input_dir)
        excluded = [str(x) for x in raw.get("exclude", [])]
        if excluded:
            subjects = [s for s in subjects if s.id not in excluded]
        cfg = Config(input_dir=input_dir, output_dir=output_dir, subjects=subjects,
                     suit=raw.get("suit", {}), sct=raw.get("sct", {}), qc=raw.get("qc", {}),
                     project=raw.get("project", "enigma"), base_dir=base.resolve(),
                     excluded=excluded)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        return cfg


def discover_subjects(input_dir: Path) -> list[Subject]:
    """Mesma convenção de nomes do protocolo ENIGMA: <subj>_t1.nii.gz, <subj>_t2.nii.gz,
    e máscara externa <subj>_ceresmask.nii(.gz) (opcional)."""
    subjects = []
    for p in sorted(input_dir.iterdir()):
        name = strip_nii(p.name)
        if name.endswith("_t1") and p.name.endswith(NII_EXTS):
            sid = name[: -len("_t1")]
            subjects.append(Subject(
                id=sid, t1=p,
                t2=find_image(input_dir, f"{sid}_t2"),
                ext_mask=find_image(input_dir, f"{sid}_ceresmask")))
    return subjects


def setup_logging(output_dir: Path, verbose: bool = True) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger("enigma_pipeline")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not root.handlers:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
        fh = logging.FileHandler(output_dir / "pipeline.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


class Status:
    """status.json por sujeito, usado para retomar lotes interrompidos."""

    def __init__(self, folder: Path):
        self.path = folder / "status.json"
        folder.mkdir(parents=True, exist_ok=True)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def done(self, step: str) -> bool:
        return self.data.get(step, {}).get("state") == "ok"

    def set(self, step: str, state: str, **extra) -> None:
        self.data[step] = {"state": state, "time": time.strftime("%Y-%m-%d %H:%M:%S"), **extra}
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))


def run_step(status: Status, step: str, func, force: bool = False):
    if status.done(step) and not force:
        log.info("  [pula] %s já concluída", step)
        return None
    t0 = time.time()
    status.set(step, "running")
    try:
        out = func()
    except Exception as e:  # registra e propaga para o runner decidir
        status.set(step, "error", message=str(e))
        raise
    status.set(step, "ok", seconds=round(time.time() - t0, 1))
    return out


def run_cmd(cmd: list[str], cwd: Path, logfile: Path) -> None:
    """Executa um comando externo (SCT), grava stdout/stderr no log do sujeito."""
    line = " ".join(shlex.quote(c) for c in cmd)
    log.info("  $ %s", line)
    with open(logfile, "a", encoding="utf-8") as fh:
        fh.write(f"\n$ {line}\n")
        fh.flush()
        proc = subprocess.run(cmd, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT,
                              env=os.environ.copy())
    if proc.returncode != 0:
        raise RuntimeError(f"comando falhou (código {proc.returncode}): {line} — ver {logfile}")
