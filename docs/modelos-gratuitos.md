# Provedores de tier gratuito — o que dá para usar sem cartão

Levantado em **2026-10-02** contra a documentação de cada fornecedor. Companheiro
de `modelos-nvidia.md`, mas com uma diferença de método que importa:

> Os modelos do NIM foram **medidos** — cada linha saiu de uma chamada real ao
> endpoint. Os de aqui são **lidos da documentação oficial**, com a URL da fonte
> anotada. Ainda não passaram por uma chamada de verdade neste projeto.

Essa distinção não é preciosismo: é a diferença entre "o fornecedor diz que
funciona" e "funcionou aqui". Antes de confiar num deles para uma rodada longa,
faça uma chamada.

## O que foi acrescentado a `viralclipper/providers.py`

| nome | URL base | id do modelo | env var |
|---|---|---|---|
| `groq` | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` | `GROQ_API_KEY` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-3.8-flash` | `GEMINI_API_KEY` |
| `openrouter` | `https://openrouter.ai/api/v1` | `openrouter/free` | `OPENROUTER_API_KEY` |
| `deepseek` | `https://api.deepseek.com` | `deepseek-flash` | `DEEPSEEK_API_KEY` |

Todos falam o formato `/chat/completions`, que é o que permite um cliente só
cobrir a tabela inteira.

## Groq — o melhor tier gratuito medido

Fonte: `console.groq.com/docs/rate-limits` (aba **Free Plan Limits**) e
`console.groq.com/docs/openai` para a URL base.

Limites do plano gratuito, por modelo:

| modelo | RPM | RPD | TPM |
|---|---|---|---|
| `openai/gpt-oss-120b` | 30 | **1.000** | 8.000 |
| `openai/gpt-oss-20b` | 30 | **1.000** | 8.000 |
| `qwen/qwen3.8-27b` | 30 | **1.000** | 8.000 |

**O RPD é o limite que manda, não o RPM.** Um vídeo de 40 janelas são 40
chamadas; em 1.000/dia isso dá cerca de **25 vídeos por dia**. É folgado para
uso pessoal e é o único tier gratuito aqui com número publicado e alto.

O módulo usa `openai/gpt-oss-120b` por ser o maior dos três com o mesmo teto.
Trocar para `openai/gpt-oss-20b` só muda a qualidade — os limites são idênticos.

> A Groq converte `temperature=0` em `1e-8`. Se alguma vez precisar de
> determinismo real, peça um float pequeno em vez de zero.

## Google Gemini — gratuito, mas sem número publicado

Fonte: `ai.google.dev/gemini-api/docs/pricing` (coluna *Free Tier*) e
`ai.google.dev/gemini-api/docs/openai` para a URL base.

`gemini-3.8-flash` aparece como **"Free of charge"** na tabela de preços, junto
com boa parte da família 3.x e 2.5.

**O RPM e o RPD não são mais públicos.** A página de rate limits não traz mais
a tabela: ela aponta para o painel do AI Studio, que mostra o limite da *sua*
conta. Por isso a `note` do provider diz isso em vez de inventar um número —
uma nota com número velho é pior que uma nota sem número.

Como conferir o seu: `aistudio.google.com/rate-limit`.

> A URL base leva barra no fim (`.../v1beta/openai/`) e o caminho tem `v1beta`,
> não `v1`. É a única da tabela nesse formato.

## OpenRouter — 50/dia, a não ser que você compre créditos

Fonte: `openrouter.ai/docs/faq` e `openrouter.ai/docs/quickstart`.

- **50 requisições/dia** para modelos gratuitos, sem comprar nada.
- **1.000/dia** depois de comprar **10 créditos**.
- O slug `openrouter/free` é um **roteador** que escolhe sozinho um modelo
  gratuito — foi o que ficou na tabela, porque não depende de decorar slug.
- Modelos gratuitos individuais levam o sufixo `:free` no slug.

50/dia é o teto mais baixo dos quatro: dá **um vídeo por dia**, no máximo. Serve
para testar, não para operar. O caminho para virar útil é comprar os 10 créditos.

## DeepSeek — não é gratuito, e a armadilha do id

Fonte: `api-docs.deepseek.com`.

Não tem tier gratuito. Entrou na tabela por outro motivo: é o **mesmo modelo**
que a entrada `deepseek-flash` do NIM já serve, mas direto do fornecedor.

> **O id do modelo é diferente nos dois.**
> - NIM: `deepseek-ai/deepseek-v4.1-flash`
> - API do próprio DeepSeek: `deepseek-flash`
>
> Copiar um id para o outro lugar é um 404 que se lê como "modelo
> descontinuado". Travado por `test_the_two_deepseek_entries_stay_distinct`.

O DeepSeek também aceita `deepseek-v4-pro`. E a URL base **não tem `/v1`** — é o
único host da tabela nesse formato, também travado por teste.

## Cerebras — pesquisado e NÃO incluído

A Cerebras tem tier gratuito e é a inferência mais rápida do mercado, então era
candidata óbvia. **Não entrou porque a URL base não foi confirmada.**

A documentação mostra o caminho (`POST /v1/chat/completions`) e usa o SDK
próprio, mas **nunca escreve o `base_url` completo**. O padrão óbvio seria
`https://api.cerebras.ai/v1`, mas isso é inferência, não leitura.

A regra desta tabela é não adivinhar URL: um `base_url` errado falha no momento
da requisição, no meio de um pipeline, com uma mensagem que não nomeia o
conserto. Para incluir, confirme a URL na documentação e aí é uma linha.

## Como acrescentar um provedor

É uma entrada de dict em `viralclipper/providers.py`. Nada mais no código muda.
Os testes que a entrada nova precisa passar sozinha:

- `test_every_provider_declares_what_it_needs` — nome, URL http, modelo.
- `test_every_base_url_is_the_openai_shape` — `.../v1`, `.../v1beta/openai`, ou
  um host nu declarado por nome.
- `test_every_provider_note_says_what_it_costs` — a `note` não pode ser vazia:
  o painel só consegue avisar o custo por ela.

## Fontes

| fornecedor | URL base | limites |
|---|---|---|
| Groq | `console.groq.com/docs/openai` | `console.groq.com/docs/rate-limits` |
| Gemini | `ai.google.dev/gemini-api/docs/openai` | `ai.google.dev/gemini-api/docs/pricing` · `aistudio.google.com/rate-limit` |
| OpenRouter | `openrouter.ai/docs/quickstart` | `openrouter.ai/docs/faq` |
| DeepSeek | `api-docs.deepseek.com` | — (sem tier gratuito) |
