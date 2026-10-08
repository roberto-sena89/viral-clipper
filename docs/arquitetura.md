# Arquitetura do viral-clipper

Documento **vivo**: descreve contratos que precisam continuar verdadeiros. Um
número com data pertence ao `historico/`, não aqui. Se este arquivo e o código
divergirem, o código está certo — conserte o arquivo.

## O fluxo

```
download → audio → transcribe → score → ranker(opcional) → render → report
```

| Estágio      | Módulo                              | O que faz                                                                 |
| ------------ | ----------------------------------- | ------------------------------------------------------------------------- |
| download     | `download.py`                       | yt-dlp, uma passada (`full`) ou por trecho (`sections`); "pula se existe" |
| audio        | `audio.py`                          | extrai mono, mede loudness e silêncios (sinais de energia)                |
| transcribe   | `transcribe.py`                     | faster-whisper local; cache por conteúdo; `--transcript` pula o estágio   |
| score        | `score.py`                          | pontua cada janela com sinais **absolutos** em [0,1]                      |
| ranker       | `ranker.py` (opcional)              | curador LLM: reordena, escreve headline e hashtags                        |
| render       | `render.py` + `render_task.py`      | FFmpeg; legendas queimadas, reframe, template                             |
| report       | `report.py` + `viral_report.py`     | `clips.json`/`clips.md` e o `viral_report.md` editorial                   |

## Mapa de módulos

```
cli.py ─────────────────► pipeline.py ──► download / audio / transcribe / score
  │                            │           ranker / render / report / viral_report
  │                            └──► render_task.py ──► render.py ──► template.py
  ├──► config.py (ClipConfig)  ──► caption_presets / providers / template
  ├──► batch.py (SQLite)  ├──► lock.py  ├──► archive.py  └──► ig_profile.py
```

Dois imports são **tardios** de propósito, para quebrar ciclos:
`providers.py` (dentro de `_build`) e `user_providers.py`. Não os mova para o
topo sem conferir o ciclo.

## Degradação graciosa

A regra que sustenta o desenho: **módulo opcional nunca é fatal**.

- `reframe` e `ranker` degradam e **logam aviso**; o run termina.
- Uma dependência opcional é detectada pelo **ARTEFATO** que ela produz (ex.: o
  XML do cascade de faces), nunca pelo `import` — um import que existe não prova
  que o binário/modelo está lá.
- `hybrid` cai para `audio` se a transcrição falhar; a retentativa única existe
  só para falha de alocação.

### Exit codes

| Código | Significado                                                              |
| ------ | ------------------------------------------------------------------------ |
| 0      | sucesso                                                                  |
| 1      | erro genérico                                                            |
| 4      | **perda de transcrição** — `cli.py`; sem ele o run terminava 0 entregando clipes sem legenda e com score baixo, indistinguível de sucesso |
| 5      | lock de `output_dir` já tomado por outro processo                        |
| 130    | interrompido (Ctrl-C)                                                    |

## Contratos

- **Só dataclasses.** Nenhum Pydantic, `TypedDict` ou `NamedTuple`.
- A validação é centralizada: `ClipConfig.validate`, `Template.validate`,
  `Zone.validate`, `user_providers.validate`.
- **`config_from_args` filtra `vars(args)` pelos campos da dataclass.** Uma flag
  nova na CLI não consegue quebrar a montagem da configuração — se não houver
  campo correspondente, ela é ignorada.
- **Sinais de score são absolutos em [0,1]**, com `_BOUNDED_SIGNALS` /
  `_BANDED_SIGNALS` obrigando uma **política** para cada sinal novo. Nunca
  normalizar por percentil: isso tornaria o score incomparável entre vídeos.
- **`score.py` separa `score_windows()` de `pick_windows()`** (portão +
  não-sobreposição). É exatamente essa separação que deixa o curador entrar no
  meio sem reescrever a seleção.

### Onde estender

| Quero…                      | Onde mexer                                                                       |
| --------------------------- | -------------------------------------------------------------------------------- |
| um sinal novo de score      | `score.py`: entra em `WEIGHTS` **e** ganha política em `_BOUNDED`/`_BANDED_SIGNALS` |
| um preset de legenda        | `caption_presets.py`; a CLI valida contra `PRESETS` e o README lista              |
| um provedor de LLM          | `providers.py` (factory) ou `user_providers.py` (do usuário, via painel)          |
| um template de composição   | `templates/*.toml`; `--list-templates` mostra os nomes                            |

## Concorrência e idempotência

- **Render paralelo** com `ProcessPoolExecutor`; `limit_native_threads()` é
  chamado **antes** de criar o pool. Sem isso, cada worker abre o próprio pool
  BLAS e estoura a RAM — detalhe não óbvio, não remova.
- **Lock por byte-range no offset 4096** de `<output>/.viralclipper.lock`
  (`lock.py`), mantido durante todo o run. O arquivo **nunca é apagado** (há
  justificativa de corrida no comentário); o pid gravado é só uma dica.
- **Persistência por artefato**, não por store de job único: `clips.json`,
  `clips.md`, `viral_report.md`, cache de transcrição e de veredito do curador
  (chave derivada do conteúdo). O modo batch tem manifesto próprio em **SQLite**
  (`batch.py`), com estados `pending/running/done/failed` e recuperação de
  `running` órfão.

### O que já é idempotente — e o que não é

| Estágio       | Proteção                                                        |
| ------------- | --------------------------------------------------------------- |
| download      | "pula se existe"                                                |
| transcrição   | cache por conteúdo                                              |
| curador (LLM) | cache por janela (a chave inclui o hash do prompt)              |
| **render**    | **nenhuma** — `render_clip` apaga e reencoda o destino sempre   |

O render é o único estágio sem proteção de idempotência. Consequência conhecida:
se o pool paralelo levantar exceção, o retry sequencial re-renderiza **todas**
as tarefas, inclusive as que os workers já concluíram — um run de 5 clipes pode
fazer 5 encodes extras.

## Segurança

O modelo de ameaça é **uma pessoa, em loopback**. Dentro dele:

- Nenhum `shell=True`, `os.system` ou `eval`: todo comando externo é uma lista
  de argumentos via `subprocess`.
- Segredos (`cookies*`, `provedores-usuario.toml`, `config.toml/yaml`, `.env`,
  `ajustes.toml`) estão no `.gitignore` e **não** são rastreados.
- O painel tem guarda anti-DNS-rebinding em GET/POST/PUT, CSP com
  `frame-ancestors 'none'`, `nosniff` e sem CORS. Ver `painel-web.md`.

## Histórico

A auditoria datada que originou boa parte deste texto está em
[`historico/auditoria-2026-10-07.md`](historico/auditoria-2026-10-07.md). Ela
contém o retrato do dia, os números medidos e o que já foi corrigido — leia-a
como registro, não como contrato.
