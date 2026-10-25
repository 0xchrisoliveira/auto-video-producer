# Gaps funcionais e segurança — relatório de revisão

Revisão do pipeline feita em 2026-10 com dois objetivos:

1. **Resultados mais certeiros** — onde o vídeo final podia sair errado
   (áudio fora de nível, clipe em loop, tema classificado errado, duração
   estourada, exportação interrompida por texto com vírgula).
2. **Segurança da aplicação** — o que uma pessoa mal-intencionada ou um
   descuido local (chave em arquivo legível, comando inyectado) poderia
   explorar.

Este documento lista o que foi encontrado e o que foi corrigido. As
verificações foram feitas com pipeline real (assets sintéticos + narração
sintética) e testes unitários dos módulos de classificação.

---

## 1. Gaps funcionais encontrados e corrigidos

### 1.1 Seleção de assets ignorava a duração

| Gap | Impacto no vídeo | Correção |
| --- | --- | --- |
| `pick_least_used_video` não recebia a duração necessária do trecho | Vídeos de 4 s usados em trechos de 10 s → loop curto e repetitivo, o pior sinal visual do pipeline | `min_duration` por trecho; cascata de relaxamento (categoria + orientação + duração → qualquer) em vez de pular tudo de uma vez |
| `pick_least_used_music(min_duration)` era sempre chamado com `0.0` | Trilha de 15 s em loop numa narração de 3 min → loop audível | Passa `narration_duration`; só cai para música curta com aviso |
| Trechos planejados podiam excluir a biblioteca | Mesmo clipe repetido 2–3 vezes colado | `count_available_videos()` limita os trechos; excedentes são **fundidos** (menores vizinhos) antes do planejamento |
| Arquivo de asset apagado continuava `ativo=1` no SQLite | Render quebrava no meio (ffmpeg: arquivo não encontrado) | `scan_assets` marca como inativo o que sumiu do disco; seleção re-tenta e `mark_*_inactive` |
| Arquivo substituído no disco mantinha metadados velhos | Duração/orientação erradas na seleção | Coluna `mtime` (migração automática de schema) + reextração de metadados |
| `select_background_videos` não validava existência do arquivo | Idem | Confere no disco antes de incrementar uso |

### 1.2 Classificação de tema (acurácia)

| Gap | Impacto | Correção |
| --- | --- | --- |
| `re.findall(re.escape(kw))` casava dentro de palavras | "março"/"marinheiro" pontuavam o tema **natureza** (`mar`) | Correspondência por **fronteira de palavra** (`(?<!\w)kw(?!\w)`) |
| LLM podia devolver `Natureza`/`Céu` com acento/caixa diferentes da pasta | Categorias descartadas silenciosamente → fallback errado | Comparação normalizada (caixa + acentos) com mapeamento para o nome canônico |
| Resposta do LLM sem JSON limpo (cerca de código, texto extra) | Exceção crua, pipeline caía direto no fallback genérico | `_parse_llm_json` tolera cercas e extrai o objeto |
| Nenhum piso de confiança | Classificação com "confidence 0.02" era aceita | `classification.min_confidence` (padrão 0.5) + cascata: LLM → palavras-chave → biblioteca inteira |
| Lista de categorias vazia virava "sorteio qualquer" sem log | Tema errado sem diagnóstico | Cascata explícita com log do critério usado |
| `themes.<x>.categories` podia ser string | Crash em runtime | Coerção para lista |

### 1.3 Áudio

| Gap | Impacto | Correção |
| --- | --- | --- |
| `audio_analysis` media loudness (LUFS/true peak) e **nunca usava** | Vídeos com volumes muito diferentes entre si (TTS × música × normalização) | Normalização linear na mixagem: ganho = `target_lufs − medido`, limitado por `target_tp` e por ±24 dB (`audio_mix.normalize_loudness`, `target_lufs`, `target_tp`) |
| Narração de duração 0 / ilegível passava adiante | ffmpeg falhava depois, com erro confuso | `analyze_narration` valida metadados e duração > 0 na hora |

### 1.4 Vídeo e exportação

| Gap | Impacto | Correção |
| --- | --- | --- |
| `-ss` **depois** de `-i` | Seek lento: todo o arquivo era decodificado até o corte (minutos em clipes longos) | `-ss` antes de `-i` (seek rápido) |
| Concat reencodava todos os trechos sempre | Render mais lento sem necessidade | Tenta `-c copy`, valida duração com `ffprobe` (tolerância 0,5 s) e só reencoda se falhar |
| `camera_movement.intensity` existia no config mas era ignorado | Config mentia sobre o resultado | Intensidade 0–2 aplicada na amplitude de zoom e na folga de pan (aliases `"subtle"`, `"strong"` antigos continuam válidos) |
| `color_correction.temperature` existia no config mas era ignorado (o valor `"warm"` nem era número) | Config morta | `colorbalance` derivado da temperatura −1..+1 (aliases `warm`/`neutral`/`cool`) |
| `video.export_bitrate_min/max` declarados e ignorados | Sem controle de tamanho/qualidade | `-minrate`/`-maxrate` + `-bufsize` (janela VBV; sem ela `-maxrate` tem efeito limitado) |
| `drawtext=text='...'` inline | **Qualquer** `,` `%` `:` `'` no texto do canal quebrava a sintaxe e derrubava a exportação inteira | Texto vai em arquivo temporário: `textfile=` + `expansion=none` (removido depois do render) |
| `overlay.position` declarado e ignorado | Sem controle de onde a marca aparece | 9 posições implementadas (`top|center|bottom` × `left|center|right`), valor inválido cai para `bottom-right` com aviso |
| Overlay com texto vazio ainda emitia `drawtext` vazio | Erro de filtro | Sem logo e sem texto → overlay pulado |
| `-shortest` + imagem da tela final em `-loop 1` (stream infinito) | **Vídeo 1,6 s mais longo que a narração** (medido: 15,6 s para 14 s) e média de bitrate caindo | `-t <duração da narração>` explícito na saída |
| Título da thumbnail com tamanho fixo | Título longo estourava a largura da imagem | Fonte reduzida até caber em 90 % da largura; se ainda não couber, trunca com reticências |
| `_next_project_id` = `len(logos)+1` | Após uma falha no meio, o próximo projeto **sobrescrevia** relatório/vídeo do anterior | Procura o primeiro sequencial livre do dia (logs e `output/`) |
| `move_processed_audio` movia para o mesmo nome | Sobrescrevia narração anterior homônima | Nome único (`nome001.wav`, `nome002.wav`…) |
| `ffprobe` sem timeout | Arquivo corrompido travava o pipeline/GUI para sempre | Timeout de 60 s com mensagem clara |
| ffmpeg sem `-nostdin` | Poderia ficar esperando input do terminal em situações de erro | `-nostdin` sempre presente |
| Valores de config não numéricos | `float("warm")` → `ValueError` no meio do render | `Config.get_float` com aliases e fallback documentado |
| Fontes de dados divergentes | `standardize_video`/`exporter` assumiam 1920×1080 por padrão enquanto o resto usava 1080×1920 | Padrões alinhados (1080×1920 = valor do `config.yaml.example`) |

### 1.5 Configuração morta / dependências órfãs

| Item | Situação | Correção |
| --- | --- | --- |
| Seção `llm.*` | Nenhum código a usava (README prometia "geração de roteiro") | Novo comando `python main.py generate "<tópico>"` (`core/script_generator.py`) com validação de endpoint, chave e resposta — testado contra um servidor OpenAI-compatível local |
| `thumbnail.preferred_keywords` | Nenhum leitor no código | Removido do exemplo e dos READMEs |
| `pysubs2` | Dependência sem `import` em lugar nenhum | Removida de `requirements.txt` |
| Seção `youtube.*` | Nenhum uploader no código | Mantida e documentada como **ponto de extensão** |
| ETAPA 10 (legendas) | O planejamento prevê legendas, mas nada as gera | O exporter já aceita `.ass` quando existir; geração automática fica como melhoria futura (ver §3) |

---

## 2. Segurança

### 2.1 Verificado e confirmado seguro

| Verificação | Resultado |
| --- | --- |
| `shell=True`, `os.system`, `os.popen` | **Nenhum** — todos os processos são `subprocess` com lista de argumentos |
| Injeção de SQL | Consultas usam placeholders `?`; os únicos trechos interpolados são `IN (?,?)` gerados pelo próprio código |
| Chaves/API no repositório | Nenhuma; `config.yaml` é ignorado pelo git e o exemplo só tem placeholders |
| Conteúdo específico de nicho | Nenhum (whitelabel verificado por varredura) |

### 2.2 Riscos encontrados e corrigidos

| Risco | Severidade | Correção |
| --- | --- | --- |
| **Prompt injection** — o texto do roteiro (que pode vir de terceiros) ia crudo no prompt do classificador, junto das instruções de sistema | Média | Texto isolado em `<TEXTO>…</TEXTO>` com regra explícita de ignorar pedidos contidos nele; resposta limitada às categorias válidas da biblioteca |
| **Segredos no arquivo de config** — `api_key` em claro num arquivo normalmente `0644` | Média | Expansão de `${VAR}` em qualquer valor do YAML + aviso automático quando o arquivo contém segredo real e está legível por outros usuários (`chmod 600`) |
| **Endpoint não validado** (`classification.endpoint`, `llm.endpoint`) podia apontar para `file://`/scheme arbitrário | Média | Somente `http`/`https` são aceitos; chave não configurada/placeholder nunca é enviada |
| **Quebra de YAML pela GUI** — `config_writer` gravava o valor cru: uma string contendo `\n` injetava chaves novas no `config.yaml` | Média | Valores com quebra de linha/tabulação são recusados; aspas e contra-barra escapadas corretamente |
| **Textos de comando no filtro do ffmpeg** — nome do canal com vírgula/aspas/`%` quebrava o filtro (e a correção ingênua de escape era frágil) | Baixa/Média | Texto passa por arquivo (`textfile` + `expansion=none`), nunca pela linha de comando do filtro |
| **ffmpeg aguardando stdin** | Baixa | `-nostdin` em todas as execuções |
| **`ffprobe` sem timeout** (arquivo malicioso/corrompido prendia o processo) | Baixa | Timeout de 60 s |
| **Resposta de LLM processada sem validação** (`json.loads` direto em texto externo) | Baixa | Parser tolerante e validado; falha vira fallback, nunca exceção não tratada |

### 2.3 Recomendações que continuam do lado de fora do código

1. **Chaves só via ambiente** — use `api_key: "${MINHA_CHAVE}"` e exporte a
   variável; rode `chmod 600 config.yaml` (o próprio pipeline avisa).
2. **Dependências** — `omnivoice`, `edge-tts` e `soundfile` são baixadas do
   PyPI; em ambiente sensível, fixe versões e revise o CHANGELOG
   (`pip install -r requirements.txt` + `pip freeze`).
3. **Watcher** — `main.py watch` processa qualquer `.wav` que aparecer em
   `input/audio/`; mantenha essa pasta gravável só para você
   (`chmod 700 input/audio`).
4. **Ativos de terceiros** — vídeos/músicas baixados de internet entram no
   pipeline de encode; valide a origem/licença antes de publicar.
5. **Upload do YouTube** — ainda não implementado; quando for, trate
   `client_secrets.json`/`token.json` como segredos (fora do git, `0600`).

---

## 3. Melhorias ainda em aberto

| Melhoria | Por que importa | Esforço |
| --- | --- | --- |
| Legendas automáticas (ETAPA 10) | Ganho grande de retenção em vídeo vertical; o exporter já queima `.ass` | Médio — exige timings por palavra (Edge TTS expõe *word boundaries*) |
| Sync de corte por silêncio da narração | Cortar no silêncio deixa a troca de clipe imperceptível | Médio |
| Feedback visual da classificação na GUI (tema escolhido + confiança) | Diagnóstico rápido de por que o fundo "não bateu" com o texto | Baixo |
| Retry/backoff no LLM de classificação | Evita fallback por causa de um 429 pontual | Baixo |
| Testes automatizados (pytest) cobrindo classificação, seleção e config | Impede regressão nos módulos tocados nesta revisão | Baixo/Médio |
| Salvaguarda de `min_rate` em conteúdo fácil | `-minrate` é limite suave: conteúdo muito comprimível fica abaixo (medido: barras de teste a 22 kbps com `minrate 10M`) | Baixo (usar CBR real com `nal-hrd` se for exigir tamanho mínimo) |

---

## 4. Como foi testado

- **Pipeline completo** (`main.py scan` + `main.py run`) com biblioteca
  sintética (3 vídeos 1080×1920, 1 música, narração de 14/16 s):
  saída de 16,000 s exatos, 12,5 Mbps dentro de `min/max`, 1080×1920@30,
  overlay, tela final, thumbnail e registro no banco.
- **Classificação**: LLM simulado (servidor local) devolvendo
  `["Natureza","Céu","inexistente"]` → normalização de caixa/acentos,
  corte para a biblioteca e fallback por palavras-chave validados;
  fronteira de palavra (`mar` ∉ `março`); piso de confiança; texto vazio.
- **Geração de roteiro** (`main.py generate`) contra servidor
  OpenAI-compatível local, incluindo expansão de `${OPENROUTER_API_KEY}`.
- **Duração da exportação**: reproduzido o bug do `-shortest` com imagem
  em loop e validada a correção com `-t` (14,000 s de áudio → 14,000 s
  de vídeo).
- **Compilação** de todos os módulos (`compileall`) após cada alteração.
