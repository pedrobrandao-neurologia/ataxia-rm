# ENIGMA SUIT + SCT — pipeline em Python + PWA

Tradução para Python do processamento do ENIGMA-Ataxia (repositório
[Harding-Lab/enigma-ataxia](https://github.com/Harding-Lab/enigma-ataxia)) para:

- **cerebelo** — etapas do SUIT, reimplementadas com **SUITPy** (Diedrichsen Lab, 2026) + ANTsPy;
- **medula cervical** — mesmos comandos e parâmetros do **Spinal Cord Toolbox** usados pelo ENIGMA.

O processamento pesado roda no computador local (`python/`). O PWA (`pwa/`) roda no navegador e faz
a organização dos sujeitos, gera o `config.json` e o `run_enigma.sh`, marca os discos C2/C3 e C3/C4,
revisa/edita máscaras e mostra os resultados. Nenhuma imagem sai do computador.

## Estrutura

```
python/enigma_pipeline/
  suit.py     etapas do cerebelo (SUITPy)
  sct.py      etapas da medula (CLI do SCT)
  labels.py   marcações do PWA -> NIfTI de rótulos (-ldisc)
  qc.py       covariância espacial (tradução do cg_check_cov.m)
  results.py  atlas SUIT por região + tabelas + results.json
  cli.py      comando enigma-pipeline
python/tests/ testes com dados sintéticos
pwa/          app (GitHub Pages via .github/workflows/pages.yml)
```

## Correspondência com o código MATLAB do ENIGMA

| ENIGMA (MATLAB) | Aqui | Observação |
|---|---|---|
| `CreateFolderSUIT.m` | `suit.prepare` | mesma convenção `<subj>_t1`, `_t2`, `_ceresmask` |
| `RunSuitT1andT2.m` (coreg SPM NMI) | `suit.prepare` | ANTs Rigid (Mattes MI) |
| `suit_isolate_seg` | `suit.isolate` + `suit.segment` | SUITPy (U-Net) + ANTs Atropos para GM/WM |
| ImCalc `((CERES>0).*GM + WM) > 0.1` | `suit.make_mask` | mesma expressão; sem máscara externa usa a do SUITPy |
| edição manual da máscara (FSLeyes) | PWA, aba Revisão | borracha/pincel, original salvo como `*_orig` |
| `suit_normalize_dartel` | `suit.normalize` | SUITPy: ANTs SyN para o template SUIT |
| `suit_reslice_dartel` (`jactransf=1`) | `suit.reslice_modulate` | modulação pelo jacobiano afim + não linear |
| suavização 3 mm (Harding 2021) | `suit.smooth` | `s3wd<subj>_t1_seg1.nii.gz` |
| `cg_check_cov.m` | `qc.check_covariance` | por centro, limiar média − 2 DP |
| `sct_deepseg_sc -c t1` | `sct.segment` | opção `sct_deepseg spinalcord` |
| `sct_label_utils -create-viewer 3,4` | PWA, aba Discos (ou `interactive_labels`) | |
| `sct_register_to_template -param ...` | `sct.register` | parâmetros idênticos |
| `sct_warp_template` | `sct.warp_template` | |
| `sct_process_segmentation -vert 2:3 -perlevel 1` | `sct.metrics` | padrão `1:4` (C1–C4, como no artigo) |

## Diferenças importantes

- Os valores do cerebelo **não são comparáveis** aos do ENIGMA-Ataxia 2021 (SUIT 3.2 / SPM12 / DARTEL):
  isolamento, segmentação de tecidos e normalização usam outros algoritmos.
- A segmentação GM/WM por Atropos foi testada só no template MNI (imagem média, com folhas cerebelares
  borradas); a acurácia em T1 reais precisa ser conferida na aba Revisão. Há a opção de usar mapas de GM/WM
  próprios (`tissue_seg: "external"`).
- O SCT instalado hoje (7.x) é mais novo que o 4.2.2 do artigo; `sct_deepseg_sc` está marcado como obsoleto.
- Volume intracraniano não é calculado.

## Instalação e uso

Ver a aba **Instalação e uso** do PWA. Resumo:

```bash
python3 -m venv ~/enigma-env && source ~/enigma-env/bin/activate
pip install -e python/
# instalar o Spinal Cord Toolbox: https://spinalcordtoolbox.com
cd meu_projeto && bash run_enigma.sh check && bash run_enigma.sh atlas
bash run_enigma.sh etapa1   # depois: marcar discos e revisar máscaras no PWA
bash run_enigma.sh etapa2
```

## Referências

- Harding IH et al. Ann Neurol 2021;90:570–583. doi:10.1002/ana.26200
- Rezende TJR et al. Mov Disord 2023;38:45–56. doi:10.1002/mds.29261
- Wang Y et al. SUITPy. Imaging Neuroscience 2026. doi:10.1162/IMAG.a.1323
- De Leener B et al. SCT. NeuroImage 2017;145:24–43.
