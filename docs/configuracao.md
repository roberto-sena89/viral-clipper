# Configuração

Documento **vivo**. Descreve as duas formas de configurar o motor: flags de CLI
e arquivo de config. Números são verificáveis pelo código (`ClipConfig`,
`config_file.py`); se divergirem, conserte o arquivo.

## Precedência

```
defaults do código  <  arquivo de config  <  flag de CLI
```

A flag de CLI **sempre** vence. O arquivo é conveniência para runs repetidos, não
substituto de flags explícitas.

O arquivo é lido **antes** de o parser da CLI ser construído; cada chave vira o
default do argumento correspondente. **Chave desconhecida é ignorada com aviso** —
um arquivo velho nunca quebra uma versão nova da ferramenta.

## Os dois formatos

O formato é detectado pela **extensão**:

| Extensão        | Parser             | Dependência                |
| --------------- | ------------------ | -------------------------- |
| `.toml`         | `tomllib` (stdlib) | nenhuma (Python 3.11+)     |
| `.yaml` / `.yml`| PyYAML             | `pip install pyyaml`       |

```powershell
viralclipper "URL" --config config.toml
viralclipper "URL" --config config.yaml
```

Os dois exemplos versionados (`config.example.toml`, `config.example.yaml`)
trazem as mesmas chaves e comentários. **Escolha um formato e fique nele** — o
motor aceita os dois, mas manter os dois em sincronia é trabalho manual.

## A armadilha do `[table]` no TOML

Em TOML, um cabeçalho `[table]` vale para **toda** chave que o segue, até o
próximo cabeçalho. Um bloco `[whisper]` colocado no meio do arquivo engole
silenciosamente **todo o resto** das configurações.

Por isso o exemplo usa **chaves pontuadas** (`whisper.model = "small"`), que são
independentes de posição:

```toml
# ERRADO — tudo depois daqui vira chave de [whisper]
[whisper]
model = "small"
min_duration = 30.0   # <- isto agora é whisper.min_duration

# CERTO — chave pontuada, posição não importa
whisper.model = "small"
min_duration = 30.0
```

### Como o arquivo vira argumento

`_flatten` percorre o mapa e produz pares `{chave.pontuada: valor}`; tabelas
aninhadas são unidas por ponto, **casando com o estilo `--a-b` da CLI**. Assim
`whisper.model` no arquivo é o mesmo que `--whisper-model` na linha de comando.

Um caso de alias é tratado em `apply_defaults`: `whisper: {language: pt}` achata
para `whisper_language`, mas o destino na CLI é `language` — o alias é resolvido
ali, não no arquivo.

## Grupos de chaves

Do `config.example.toml` (o motor tem **88 campos** em `ClipConfig`; abaixo, os
grupos que importam no dia a dia):

| Grupo          | Chaves-chave                                                                 |
| -------------- | ---------------------------------------------------------------------------- |
| **seleção**    | `min_duration`, `max_duration`, `target_duration`, `count` (0 = automático), `engine`, `min_gap`, `auto_margin`, `auto_ceiling`, `max_duration_grace`, `min_score`, `pad_start`, `pad_end` |
| **transcrição**| `whisper.model`, `whisper.device`, `whisper.compute_type`, `whisper.language`, `whisper.beam_size`, `whisper.vad_filter` |
| **cache**      | `cache_dir`, `transcript_cache`                                              |
| **curador**    | `ranker`, `ranker_provider`, `ranker_model`, `ranker_base_url`, `ranker_api_key_env`, `ranker_requires_key`, `ranker_top_n`, `ranker_concurrency`, `ranker_weight`, `ranker_timeout`, `curator_prompt_file` |
| **áudio**      | `min_silence`, `hop_ms`, `silence_db`                                        |
| **download**   | `download_mode`, `max_height`, `cookies_from_browser`, `extra_ytdlp_args`, `metadata_language` |
| **render**     | `vertical`, `layout`, `width`, `height`, `burn_captions`, `caption_style`, `caption_words_per_line`, `font`, `font_size`, `caption_margin_v`, `uppercase_captions`, `highlight_color`, `headline_seconds`, `progress_bar`, `loudnorm`, `target_lufs`, `jump_cut`, `crf`, `preset`, `audio_bitrate`, `threads` |
| **templates**  | `template`, `variant_presets`, `variant_layouts`                             |
| **execução**   | `parallel`, `workers`                                                        |
| **diversos**   | `ffmpeg`, `ffprobe`, `quiet`, `verbose`, `keep_temp`, `dry_run`              |

Notas que evitam surpresa:

- **`count = 0`** liga o modo automático: o vídeo decide quantos clipes rende,
  aceitando toda janela dentro de `auto_margin` pontos da melhor daquele vídeo.
  Um número volta ao teto fixo.
- **`ranker_top_n`** é o **teto de custo** — uma chamada de modelo por candidato
  por vídeo. `ranker_concurrency` (padrão 6) é quantas ficam em voo; `1` volta a
  ser estritamente serial.
- **`min_score = 0`** desliga o portão de qualidade e sempre entrega os `count`
  melhores.
- **`threads = 2`** não é timidez: o libx264 aloca buffer por thread, e "uma por
  núcleo" é como um render 1080x1920 estoura a RAM de uma máquina de 8 GB.
- **`extra_ytdlp_args`** é repassado cru ao yt-dlp. Não é injeção de comando (não
  há shell), mas é superfície de flags arbitrárias sem validação.

## `ajustes.toml` (o painel) é outro arquivo

O painel de Ajustes tem o **próprio** `ajustes.toml` (no `.gitignore`), com
**27 chaves** (`AJUSTES_KEYS`) — um subconjunto do motor. Não confunda com
`config.toml`: são arquivos separados, com ciclos de vida separados. O contrato
de escrita (atômica, malformado avisa em vez de resetar) está em
[`painel-web.md`](painel-web.md).

## Provedores de LLM

`ranker_provider` aceita um nome de `viralclipper/providers.py` (ex.:
`nemotron-super`, `gemma-4-31b`, `kimi-k3`, `deepseek-flash`, `openai`, `local`),
que preenche `base_url`, `model`, `api_key_env` e `requires_key` de uma vez.
Deixá-lo vazio mantém as três chaves manuais (`ranker_model`, `ranker_base_url`,
`ranker_api_key_env`) como autoritativas.

O provedor **do usuário** (adicionado pelo painel) vive em
`provedores-usuario.toml`, também no `.gitignore`. Retratos datados dos gratuitos
estão em [`modelos-gratuitos.md`](modelos-gratuitos.md) e
[`modelos-nvidia.md`](modelos-nvidia.md); o contrato do prompt do curador está em
[`curador.md`](curador.md).

## Ver também

- [`arquitetura.md`](arquitetura.md) — o pipeline que consome estas chaves.
- [`painel-web.md`](painel-web.md) — a superfície editável do painel (27 chaves).
