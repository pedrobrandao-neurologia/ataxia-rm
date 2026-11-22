export const HELP_HTML = `
<h2>Como funciona</h2>
<p>O processamento pesado (segmentação, registro, normalização) roda no seu computador, em Python,
com o <b>SUITPy</b> (cerebelo) e o <b>Spinal Cord Toolbox</b> (medula). Este app, que roda no navegador, faz o resto:
organiza os sujeitos, gera o <code>config.json</code> e o script, marca os discos C2/C3 e C3/C4, revisa e
corrige as máscaras e mostra os resultados. Nenhuma imagem sai do seu computador.</p>

<h2>1. Instalar (uma vez, no Mac)</h2>
<pre><code># Python 3.10 a 3.12
python3 -m venv ~/enigma-env
source ~/enigma-env/bin/activate
pip install -e /caminho/para/enigma-suit-sct/python

# Spinal Cord Toolbox: siga o instalador oficial em https://spinalcordtoolbox.com
# e confira com:
sct_check_dependencies</code></pre>
<p>O SUITPy usa bastante memória na etapa de isolamento do cerebelo (no teste, passou de 6 GB por exame);
rode um sujeito por vez.</p>

<h2>2. Organizar a pasta do projeto</h2>
<pre><code>meu_projeto/
  input/
    sub01_t1.nii.gz
    sub01_t2.nii.gz          (opcional)
    sub01_ceresmask.nii.gz   (opcional: máscara externa do cerebelo)
    sub02_t1.nii.gz
    ...</code></pre>
<p>Na aba <b>Projeto</b>, abra a pasta, preencha centro e grupo e clique em
<b>Salvar config.json e run_enigma.sh</b>.</p>

<h2>3. Rodar</h2>
<pre><code>cd meu_projeto
bash run_enigma.sh check     # confere instalação e arquivos
bash run_enigma.sh atlas     # baixa o atlas SUIT de lóbulos (uma vez, precisa de internet)
bash run_enigma.sh etapa1    # cerebelo até a máscara; medula até a segmentação</code></pre>
<p>Depois da etapa 1:</p>
<ul>
  <li><b>Discos C2–C4</b>: marque os rótulos 3 (disco C2/C3) e 4 (disco C3/C4) na ponta posterior do disco.
    Dá para marcar antes da etapa 1, porque só usa o T1.</li>
  <li><b>Revisão de máscaras</b>: confira a máscara do cerebelo (<code>pcereb</code>) e a segmentação da medula;
    apague inclusões indevidas (ex.: seio transverso), aprove ou exclua o sujeito.
    Se excluir alguém, salve o config.json de novo.</li>
</ul>
<pre><code>bash run_enigma.sh etapa2    # normalização SUIT, registro PAM50, métricas, CQ e tabelas</code></pre>
<p>Para refazer um sujeito a partir de uma etapa:</p>
<pre><code>bash run_enigma.sh refazer suit --subjects sub07 --from suit_normalize
bash run_enigma.sh refazer sct  --subjects sub07 --from sct_register</code></pre>

<h2>4. Resultados</h2>
<p>Ficam em <code>deriv/results/</code>: <code>sct_spinal_cord.csv</code> (área e excentricidade por nível),
<code>suit_rois_long.csv</code> (GM e WM por lóbulo/núcleo do atlas SUIT), <code>suit_native_volumes.csv</code>,
<code>results.json</code> (lido pela aba Resultados) e <code>deriv/qc/covariance_wd1.csv</code>.
As imagens para análise voxel a voxel são <code>deriv/suit/&lt;sujeito&gt;/s3wd&lt;sujeito&gt;_t1_seg1.nii.gz</code>
(GM modulada e suavizada no espaço SUIT). O relatório de CQ do próprio SCT fica em <code>deriv/sct_qc/index.html</code>.</p>

<h2>Diferenças em relação ao ENIGMA-Ataxia</h2>
<ul>
  <li><b>Cerebelo:</b> SUITPy (isolamento por U-Net + normalização ANTs SyN) e segmentação de tecidos por ANTs Atropos,
    no lugar de SUIT 3.2 + SPM12 + DARTEL. A modulação usa o jacobiano da transformação afim + não linear, como o
    <code>jactransf=1</code> do ENIGMA. Os valores <b>não são comparáveis</b> numericamente aos do artigo de 2021.</li>
  <li><b>Máscara externa (CERES):</b> mesma expressão do ENIGMA, <code>((externa&gt;0)×GM + WM) &gt; 0,1</code>.
    Sem máscara externa, a máscara do SUITPy entra no lugar.</li>
  <li><b>Medula:</b> mesmos comandos e parâmetros do SCT usados pelo ENIGMA. A versão do SCT instalada hoje é mais nova
    que a 4.2.2 do artigo; <code>sct_deepseg_sc</code> está marcado como obsoleto no SCT 7.x.</li>
  <li><b>Níveis:</b> C1–C4 por padrão (como no artigo); o script do repositório extraía C2–C3.</li>
  <li><b>Volume intracraniano:</b> não é calculado aqui (no ENIGMA veio do CAT12).</li>
</ul>
`;
