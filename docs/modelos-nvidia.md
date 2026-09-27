# Modelos NVIDIA NIM — o que serve e o que não serve neste painel

Verificado em 2026-09-27 contra `https://integrate.api.nvidia.com/v1`, com o
caminho de código que o painel realmente usa (`_suggest_phrases` e
`_suggest_post`, via `ranker.build_provider`).

Os ids do catálogo **não** são os nomes de exibição. Todos abaixo foram
medidos, não inferidos: cada linha saiu de uma chamada real — nos **dois**
caminhos que o painel usa, `_suggest_phrases` e `_suggest_post`. O segundo é
mais exigente (parse posicional de duas linhas), e os tempos abaixo são os
dele.

## Como configurar

O painel lê a config pelo `ClipConfig` (`config.example.toml:37-45`). Não é
código, é config:

```toml
ranker = "llm"
ranker_base_url = "https://integrate.api.nvidia.com/v1"
ranker_model = "nvidia/nemotron-3-super-120b-a12b"
ranker_api_key_env = "OPENAI_API_KEY"
ranker_timeout = 180.0
ranker_requires_key = true
```

A chave vai no ambiente, nunca no arquivo:

```bash
export OPENAI_API_KEY='nvapi-...'
```

`ranker_timeout` **precisa** subir. O default é `60.0` e estourou nas
primeiras chamadas — um raciocínio de um minuto passa de sessenta segundos
com facilidade, e o erro que sai é `TimeoutError`, que não diz nada sobre o
modelo.

## Recomendado

### `nvidia/nemotron-3-super-120b-a12b` — o melhor equilíbrio

**4.9s** no `/postkit`, **7.4s** no `/phrases`. O mais rápido entre os que
entregam frase boa, e passou limpo nos dois parses. Configuração sugerida:

```toml
ranker_model = "nvidia/nemotron-3-super-120b-a12b"
```

### `google/gemma-4-31b-it` — 4.5s, saída mais curta

O mais rápido depois do Super, e o gancho mais curto: *"Será que ele
consegue escapar?"*. Bom se você quer frase que caiba na tela sem cortar.

### `meta/llama-3.2-11b-vision-instruct` — 2.0s

O mais rápido da lista toda. **Com uma ressalva real:** deu `404` na
medição e, quatro chamadas depois, quatro `200` seguidos. Está atrás de um
proxy que oscila.

### `moonshotai/kimi-k3` — 22.9s

Segue instrução pelo `system`, não pelo `user`. Passa no caminho real do
painel — mas só nesse: um teste que manda a mensagem como `user` o faz
falhar, e é fácil concluir errado que o modelo não serve.

### `openai/gpt-oss-20b` — 60.3s

Passa, mas é o mais lento do grupo bom no `/postkit`. As hashtags saíram
com maiúsculas e capitalizadas (`#MotoFuga`), o que foge do padrão das
outras.

### `z-ai/glm-5.3` — 73.0s

Escrita mais literária dos aprovados. Paga o preço da latência.

## Funcionam, com ressalva

| Id | `/phrases` | Ressalva |
|---|---|---|
| `deepseek-ai/deepseek-v4.1-flash` | 48.8s | Gastou todo o `max_tokens` numa chamada e devolveu `content: null`. **Nunca mande `max_tokens` neste modelo** — ver o docstring de `HttpChatProvider`. |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | 14.6s | `503 ResourceExhausted` intermitente: passou numa chamada, falhou na seguinte, ambos no mesmo dia. O texto é bom (*"Deslize na pista, deixe a polícia para trás!"*), mas sem retry a pessoa vê erro. |
| `meta/muse-glimmer-30b` | 35.1s | Saída descritiva, mais "legenda de vídeo" que gancho. |
| `nvidia/llama-3.1-nemotron-51b-instruct` | — | Passa, mas é a geração anterior do Super. Prefira o Super. |

## Não servem para este painel

Cinco da lista **respondem HTTP 200** e ainda assim não servem. Este é o
motivo de medir em vez de confiar no catálogo.

| Id | O que devolve | Por quê |
|---|---|---|
| `google/diffusiongemma-26b-a4b-it` | `content: ''` (vazio) | É modelo de difusão de **imagem**. Não tem completion de texto. |
| `nvidia/nemotron-parse-2.0` | `"}}}}}}}}..."` | Parser de documentos. Empurra o prompt para um output estruturado. |
| `nvidia/riva-translate-4b-instruct-v1.1` | `"Just respond with the word OK."` | Tradutor: traduz a instrução em vez de obedecê-la. |
| `nvidia/riva-translate-4b-instruct-v2` | `"Please respond only with the word OK."` | Idem, V2. |
| `nvidia/nemotron-3.5-content-safety` | `"User Safety: safe"` | Classificador de segurança. Não escreve frase. |
| `nvidia/ising-calibration-1.5-31b` | 139.1s, inventa assunto | Resposta plausível, mas 2m19s e entendeu "calibração" como qubit. Ruim custo-benefício. |

## A disponibilidade oscila

Isto vale para todos, e muda a leitura de qualquer erro:

- `503 ResourceExhausted` no Nano Omni: **transitório**. Passou em 14.6s numa
  chamada e falhou na seguinte, no mesmo dia.
- `404 Function not found` no Llama 3.2 11B Vision: **transitório**. Depois
  de aparecer, quatro chamadas seguidas deram 200 (0.9s, 0.7s, 7.8s, 19.1s).
- `410 Gone`: **definitivo**. O modelo saiu de linha — não adianta retry.
- `404 Function not found` persistente: **sem acesso da chave**. Foi o caso de
  `mistral-large-2-instruct`, `llama-3.1-nemotron-ultra-253b-v1` e outros do
  catálogo de 82. O catálogo lista muito mais do que a chave alcança.

O que **não** está implementado hoje: retry. Um `503` chega ao painel como
erro e a pessoa tenta de novo na mão. Ver `HttpChatProvider.complete` em
`viralclipper/ranker.py` — retry só para 503 e 404 transitório, nunca para
410 nem para 400.

## O que o catálogo esconde

O endpoint `/models` devolveu **82** ids, e a lista de exibição do provedor
lista menos. Medindo: dos 82, quase todos deram `404` (sem acesso desta chave)
ou `410` (fora de linha). Só os que estão neste documento responderam.

A lição prática: **não confie no catálogo, teste.** Um id que não responde
pode estar fora de linha, fora do seu plano, ou ser de outro tipo — o
`diffusiongemma` aparece no catálogo, responde 200, e devolve string vazia
porque é modelo de imagem.

## A tabela completa do catálogo

Dos 82 ids que a chave devolve, quase todos deram `404` sem acesso ou `410`
fora de linha. Os que responderam de verdade estão nas seções acima.
