"""Linha de comando.

  enigma-pipeline check   --config config.json     # confere instalação e arquivos
  enigma-pipeline suit    --config config.json     # cerebelo (SUITPy)
  enigma-pipeline sct     --config config.json     # medula (Spinal Cord Toolbox)
  enigma-pipeline qc      --config config.json     # covariância espacial por centro
  enigma-pipeline collect --config config.json     # tabelas + results.json para o PWA
  enigma-pipeline all     --config config.json     # tudo, na ordem

Opções: --subjects sub01,sub02  --from suit_normalize  --until sct_segment  --force
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .common import Config, setup_logging
from . import suit, sct, qc, results

log = logging.getLogger("enigma_pipeline")


def _select(cfg: Config, subjects: str | None):
    if not subjects:
        return cfg.subjects
    want = {x.strip() for x in subjects.split(",") if x.strip()}
    sel = [s for s in cfg.subjects if s.id in want]
    missing = want - {s.id for s in sel}
    if missing:
        log.warning("sujeitos não encontrados no config: %s", ", ".join(sorted(missing)))
    return sel


def _batch(name: str, module, cfg: Config, args) -> list[str]:
    steps = [n for n, _ in module.STEPS]
    start = args.start if args.start in steps else None
    until = args.until if args.until in steps else None
    failed = []
    subs = _select(cfg, args.subjects)
    for i, s in enumerate(subs, 1):
        log.info("[%s %d/%d] %s", name, i, len(subs), s.id)
        t0 = time.time()
        try:
            module.run_subject(cfg, s, force=args.force, until=until, start=start)
            log.info("  ok (%.0f s)", time.time() - t0)
        except Exception as e:
            log.error("  FALHOU %s: %s", s.id, e)
            failed.append(s.id)
    return failed


def cmd_check(cfg: Config) -> int:
    ok = True
    for mod in ("ants", "SUITPy", "nibabel", "scipy"):
        try:
            m = __import__(mod)
            log.info("✔ %s %s", mod, getattr(m, "__version__", ""))
        except ImportError:
            ok = False
            log.error("✘ %s não instalado (pip install -e python/)", mod)
    if cfg.sct.get("enabled", True):
        try:
            log.info("✔ SCT: %s", sct.check_installation())
        except RuntimeError as e:
            ok = False
            log.error("✘ %s", e)
    log.info("%d sujeitos no config (%d excluídos na revisão)", len(cfg.subjects), len(cfg.excluded))
    for s in cfg.subjects:
        miss = [str(p) for p in (s.t1, s.t2, s.ext_mask) if p is not None and not p.exists()]
        if miss:
            ok = False
            log.error("✘ %s: arquivo(s) ausente(s): %s", s.id, ", ".join(miss))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="enigma-pipeline", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "suit", "sct", "qc", "collect", "all",
                                        "fetch-atlas"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--subjects", help="lista separada por vírgula (padrão: todos)")
    ap.add_argument("--from", dest="start", help="refaz a partir desta etapa")
    ap.add_argument("--until", help="para depois desta etapa")
    ap.add_argument("--force", action="store_true", help="refaz etapas já concluídas")
    args = ap.parse_args(argv)

    cfg = Config.load(args.config)
    setup_logging(cfg.output_dir)
    log.info("projeto %s | entrada %s | saída %s", cfg.project, cfg.input_dir, cfg.output_dir)

    if args.command == "check":
        return cmd_check(cfg)
    if args.command == "fetch-atlas":
        results.fetch_atlas(cfg)
        return 0
    failed: list[str] = []
    if args.command in ("suit", "all") and cfg.suit.get("enabled", True):
        failed += _batch("SUIT", suit, cfg, args)
    if args.command in ("sct", "all") and cfg.sct.get("enabled", True):
        failed += _batch("SCT", sct, cfg, args)
    if args.command in ("qc", "all") and cfg.suit.get("enabled", True):
        qc.run_qc(cfg)
    if args.command in ("collect", "all"):
        results.collect(cfg)
    if failed:
        log.error("sujeitos com falha: %s (ver pipeline.log e status.json)",
                  ", ".join(sorted(set(failed))))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
