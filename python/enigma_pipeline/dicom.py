"""Conversão de pastas DICOM (uma por paciente) para o padrão de entrada do ENIGMA:

    <projeto>/input/<sujeito>_t1.nii.gz
    <projeto>/input/<sujeito>_t2.nii.gz      (opcional)
    <projeto>/deriv/                         (vazia; o pipeline grava aqui)

A máscara <sujeito>_ceresmask.nii.gz NÃO sai do DICOM: vem do volBrain/CERES (ou outra
ferramenta) e é colocada em input/ à parte.

Fluxo em duas etapas, para que nada seja escolhido sem conferência:

  1) enigma-dicom scan    --dicom PASTA_DICOM --out PASTA_PROJETO
       converte todas as séries de cada paciente com dcm2niix (em PASTA_PROJETO/work/),
       e grava PASTA_PROJETO/series_inventory.csv com uma linha por série, os
       metadados do cabeçalho e uma coluna "role" sugerida (t1 / t2 / vazio) e o motivo.
  2) conferir e, se preciso, editar a coluna "role" (e "subject_id") no CSV
  3) enigma-dicom convert --out PASTA_PROJETO
       copia para input/ as séries marcadas como t1/t2 e grava conversion_report.csv.

  enigma-dicom auto ... faz 1 e 3 de uma vez (sem a conferência manual).

A sugestão de "role" é uma regra fixa e explícita (ver suggest_roles); o motivo de cada
escolha fica registrado no CSV. Séries sem correspondência ficam sem papel.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import nibabel as nib
import numpy as np

INVENTORY = "series_inventory.csv"
REPORT = "conversion_report.csv"

FIELDS = ["subject_folder", "subject_id", "role", "reason", "nifti", "SeriesNumber",
          "SeriesDescription", "ProtocolName", "SequenceName", "ScanningSequence",
          "MRAcquisitionType", "ImageType", "dim", "voxel_mm", "RepetitionTime", "EchoTime",
          "InversionTime", "FlipAngle", "Manufacturer", "ManufacturersModelName",
          "MagneticFieldStrength", "contrast_agent_hint"]

RE_T1 = re.compile(r"t1|mprage|mp[-_ ]?rage|bravo|spgr|fspgr|tfe|mp2rage|3d[-_ ]?t1|ir[-_ ]?fspgr", re.I)
RE_T2 = re.compile(r"t2|space|cube|vista|drive", re.I)
RE_EXCL = re.compile(r"flair|swi|star|\*|dwi|diff|dti|adc|perf|\basl\b|bold|fmri|\bloc|scout|survey|"
                     r"calib|\bmip\b|\bmpr\b|reform|map|tof|angio|\bpd\b|dixon", re.I)
RE_CONTRAST = re.compile(r"\bpost\b|gad|\bgd\b|\+c\b|contrast|contraste|\bc\+|pos[-_ ]?gd", re.I)


def find_dcm2niix() -> str:
    exe = shutil.which("dcm2niix")
    if not exe:
        raise SystemExit("dcm2niix não encontrado. Instale com: pip install dcm2niix "
                         "(ou brew install dcm2niix)")
    return exe


def safe_id(name: str) -> str:
    """Identificador do sujeito: só letras e números (o '_' separa o sufixo _t1/_t2)."""
    s = re.sub(r"[^A-Za-z0-9]+", "", name)
    if not s:
        raise ValueError(f"nome de pasta sem caracteres válidos: {name!r}")
    return s


def load_id_map(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    m = {}
    with open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            m[r["folder"].strip()] = safe_id(r["subject_id"].strip())
    return m


# ------------------------------------------------------------------ etapa 1: scan

def scan(dicom_root: Path, out: Path, id_map: dict[str, str] | None = None,
         only: list[str] | None = None) -> Path:
    exe = find_dcm2niix()
    id_map = id_map or {}
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    (out / "input").mkdir(exist_ok=True)
    (out / "deriv").mkdir(exist_ok=True)
    folders = sorted(p for p in dicom_root.iterdir() if p.is_dir() and not p.name.startswith("."))
    if only:
        folders = [f for f in folders if f.name in only]
    if not folders:
        raise SystemExit(f"nenhuma subpasta de paciente em {dicom_root}")
    rows: list[dict] = []
    used_ids: dict[str, str] = {}
    for folder in folders:
        sid = id_map.get(folder.name) or safe_id(folder.name)
        if sid in used_ids:
            raise SystemExit(f"identificador repetido '{sid}' para {used_ids[sid]} e {folder.name}: "
                             f"use --id-map para definir nomes distintos")
        used_ids[sid] = folder.name
        dest = work / sid
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        print(f"[dcm2niix] {folder.name} -> {sid}", flush=True)
        proc = subprocess.run([exe, "-z", "y", "-b", "y", "-ba", "y", "-d", "9",
                               "-f", "%3s_%d", "-o", str(dest), str(folder)],
                              capture_output=True, text=True)
        (dest / "dcm2niix.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        if proc.returncode not in (0,):
            print(f"  aviso: dcm2niix terminou com código {proc.returncode} (ver {dest/'dcm2niix.log'})")
        series = []
        for nii in sorted(dest.glob("*.nii.gz")):
            series.append(describe(nii, folder.name, sid, out))
        if not series:
            print(f"  aviso: nenhuma série convertida em {folder.name}")
        rows += suggest_roles(series)
    inv = out / INVENTORY
    with open(inv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    n_t1 = sum(r["role"] == "t1" for r in rows)
    n_sub = len(folders)
    print(f"\n{len(rows)} séries de {n_sub} pacientes inventariadas em {inv}")
    print(f"T1 sugerido para {n_t1}/{n_sub} pacientes. Confira a coluna 'role' antes do convert.")
    return inv


def describe(nii: Path, folder: str, sid: str, out: Path) -> dict:
    meta = {}
    js = nii.with_name(nii.name[:-7] + ".json")
    if js.exists():
        meta = json.loads(js.read_text(encoding="utf-8"))
    img = nib.load(nii)
    shape = img.shape
    zooms = img.header.get_zooms()[:3]
    text = " ".join(str(meta.get(k, "")) for k in ("SeriesDescription", "ProtocolName"))
    it = meta.get("ImageType", [])
    return {
        "subject_folder": folder, "subject_id": sid, "role": "", "reason": "",
        "nifti": str(nii.relative_to(out)),
        "SeriesNumber": meta.get("SeriesNumber", ""),
        "SeriesDescription": meta.get("SeriesDescription", ""),
        "ProtocolName": meta.get("ProtocolName", ""),
        "SequenceName": meta.get("SequenceName", ""),
        "ScanningSequence": _s(meta.get("ScanningSequence", "")),
        "MRAcquisitionType": meta.get("MRAcquisitionType", ""),
        "ImageType": _s(it),
        "dim": "x".join(str(d) for d in shape),
        "voxel_mm": "x".join(f"{z:.3g}" for z in zooms),
        "RepetitionTime": meta.get("RepetitionTime", ""),
        "EchoTime": meta.get("EchoTime", ""),
        "InversionTime": meta.get("InversionTime", ""),
        "FlipAngle": meta.get("FlipAngle", ""),
        "Manufacturer": meta.get("Manufacturer", ""),
        "ManufacturersModelName": meta.get("ManufacturersModelName", ""),
        "MagneticFieldStrength": meta.get("MagneticFieldStrength", ""),
        "contrast_agent_hint": "sim" if (RE_CONTRAST.search(text) or meta.get("ContrastBolusAgent")) else "",
        "_zooms": zooms, "_shape": shape, "_text": text, "_meta": meta,
    }


def _s(v):
    return "\\".join(v) if isinstance(v, list) else str(v)


def suggest_roles(series: list[dict]) -> list[dict]:
    """Regra explícita de sugestão:
    T1: aquisição 3D; maior voxel <= 1,2 mm; volume 3D (4D excluído); imagem não derivada
        (ImageType sem DERIVED); descrição/protocolo casa com RE_T1 e não com RE_EXCL;
        TR < 3 s e TE < 10 ms quando presentes. Entre candidatos: sem indicação de
        contraste antes de com contraste; depois menor volume de voxel; depois mais cortes.
    T2: mesmas exigências de 3D/voxel/não derivada; descrição casa com RE_T2 e não com
        RE_EXCL nem RE_T1; TE >= 60 ms quando presente. Desempate: menor voxel.
    """
    def base_ok(s):
        why = []
        if str(s["MRAcquisitionType"]).upper() != "3D":
            why.append("não é 3D")
        if len(s["_shape"]) > 3 and s["_shape"][3] > 1:
            why.append("série 4D")
        if max(s["_zooms"]) > 1.2:
            why.append(f"voxel {s['voxel_mm']} > 1,2 mm")
        if "DERIVED" in s["ImageType"].upper():
            why.append("imagem derivada")
        return why

    t1c, t2c = [], []
    for s in series:
        txt = s["_text"]
        why = base_ok(s)
        excl = RE_EXCL.search(txt)
        tr, te = _f(s["RepetitionTime"]), _f(s["EchoTime"])
        if RE_T1.search(txt) and not excl:
            w1 = list(why)
            if tr is not None and tr >= 3.0:
                w1.append(f"TR {tr:.2f} s >= 3 s")
            if te is not None and te >= 0.010:
                w1.append(f"TE {te*1000:.0f} ms >= 10 ms")
            if not w1:
                t1c.append(s)
            else:
                s["reason"] = "candidato T1 recusado: " + "; ".join(w1)
        elif RE_T2.search(txt) and not excl and not RE_T1.search(txt):
            w2 = list(why)
            if te is not None and te < 0.060:
                w2.append(f"TE {te*1000:.0f} ms < 60 ms")
            if not w2:
                t2c.append(s)
            else:
                s["reason"] = "candidato T2 recusado: " + "; ".join(w2)
        elif excl:
            s["reason"] = f"excluída pela descrição ('{excl.group(0)}')"

    def vox(s):
        return float(np.prod(s["_zooms"]))

    if t1c:
        t1c.sort(key=lambda s: (s["contrast_agent_hint"] == "sim", vox(s), -int(np.prod(s["_shape"][:3]))))
        best = t1c[0]
        best["role"] = "t1"
        best["reason"] = "T1 escolhido: 3D, voxel " + best["voxel_mm"] + (
            " (atenção: indicação de contraste)" if best["contrast_agent_hint"] else "")
        for s in t1c[1:]:
            s["reason"] = "outro candidato T1 (não escolhido)"
    if t2c:
        t2c.sort(key=vox)
        t2c[0]["role"] = "t2"
        t2c[0]["reason"] = "T2 escolhido: 3D, voxel " + t2c[0]["voxel_mm"]
        for s in t2c[1:]:
            s["reason"] = "outro candidato T2 (não escolhido)"
    if not t1c and series:
        series[0]["reason"] = (series[0]["reason"] + " | " if series[0]["reason"] else "") + \
            "NENHUM T1 elegível neste paciente — escolha manualmente no CSV"
    return [{k: v for k, v in s.items() if not k.startswith("_")} for s in series]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ etapa 2: convert

def convert(out: Path, overwrite: bool = False) -> Path:
    inv = out / INVENTORY
    if not inv.exists():
        raise SystemExit(f"{inv} não encontrado — rode primeiro: enigma-dicom scan")
    with open(inv, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    inp = out / "input"
    inp.mkdir(exist_ok=True)
    (out / "deriv").mkdir(exist_ok=True)
    chosen: dict[tuple[str, str], dict] = {}
    problems = []
    for r in rows:
        role = r["role"].strip().lower()
        if not role:
            continue
        if role not in ("t1", "t2"):
            problems.append(f"{r['subject_id']}: role inválido '{role}' (use t1, t2 ou vazio)")
            continue
        key = (safe_id(r["subject_id"]), role)
        if key in chosen:
            problems.append(f"{key[0]}: mais de uma série marcada como {role}")
            continue
        chosen[key] = r
    subjects = sorted({safe_id(r["subject_id"]) for r in rows})
    for s in subjects:
        if (s, "t1") not in chosen:
            problems.append(f"{s}: nenhuma série marcada como t1 (sujeito não será convertido)")
    if any("mais de uma" in p or "inválido" in p for p in problems):
        raise SystemExit("Corrija o CSV antes de converter:\n  " + "\n  ".join(problems))

    report = []
    for (sid, role), r in sorted(chosen.items()):
        if (sid, "t1") not in chosen:
            continue
        src = out / r["nifti"]
        dst = inp / f"{sid}_{role}.nii.gz"
        if dst.exists() and not overwrite:
            status = "já existia (mantido; use --overwrite para substituir)"
        else:
            shutil.copyfile(src, dst)
            js = src.with_name(src.name[:-7] + ".json")
            if js.exists():
                shutil.copyfile(js, inp / f"{sid}_{role}.json")
            status = "copiado"
        img = nib.load(dst)
        report.append({"subject_id": sid, "role": role, "file": str(dst.relative_to(out)),
                       "source_folder": r["subject_folder"], "series": r["SeriesNumber"],
                       "description": r["SeriesDescription"], "dim": "x".join(map(str, img.shape)),
                       "voxel_mm": "x".join(f"{z:.3g}" for z in img.header.get_zooms()[:3]),
                       "orientation": "".join(nib.aff2axcodes(img.affine)), "status": status})
    rep = out / REPORT
    with open(rep, "w", newline="", encoding="utf-8") as fh:
        keys = ["subject_id", "role", "file", "source_folder", "series", "description", "dim",
                "voxel_mm", "orientation", "status"]
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(report)
    n1 = sum(r["role"] == "t1" for r in report)
    n2 = sum(r["role"] == "t2" for r in report)
    print(f"{n1} T1 e {n2} T2 em {inp}")
    for p in problems:
        print("  aviso:", p)
    print(f"relatório: {rep}")
    print("Máscaras CERES (opcionais) entram em input/ como <sujeito>_ceresmask.nii.gz.")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="enigma-dicom", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["scan", "convert", "auto"])
    ap.add_argument("--dicom", help="pasta com uma subpasta de DICOM por paciente")
    ap.add_argument("--out", required=True, help="pasta do projeto ENIGMA (cria input/ e deriv/)")
    ap.add_argument("--id-map", help="CSV com colunas folder,subject_id para renomear/anonimizar")
    ap.add_argument("--only", help="lista de pastas (separadas por vírgula) para processar só elas")
    ap.add_argument("--overwrite", action="store_true", help="substitui arquivos já existentes em input/")
    a = ap.parse_args(argv)
    out = Path(a.out).expanduser().resolve()
    if a.command in ("scan", "auto"):
        if not a.dicom:
            ap.error("--dicom é obrigatório para scan/auto")
        dicom = Path(a.dicom).expanduser().resolve()
        if out == dicom or dicom in out.parents:
            ap.error("--out não pode ficar dentro da pasta de DICOM")
        only = [x.strip() for x in a.only.split(",")] if a.only else None
        scan(dicom, out, load_id_map(Path(a.id_map).expanduser() if a.id_map else None), only)
    if a.command in ("convert", "auto"):
        convert(out, overwrite=a.overwrite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
