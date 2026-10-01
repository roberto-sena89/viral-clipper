# O curador com IA: prompt próprio, headline e hashtags por corte

Este documento explica o que o curador faz, onde cada peça mora e por que o
prompt é um arquivo e não um registro num banco de dados.

## O que ele faz

Com `ranker = "llm"` ligado, o motor manda a transcrição de cada candidato a um
modelo de linguagem e recebe **três coisas numa única chamada**:

| Campo | Para que serve | Onde aparece |
| --- | --- | --- |
| cinco notas de 0 a 10 | reordena os candidatos junto com a heurística | `score` de cada janela |
| `headline` | o gancho queimado no topo do vídeo | no `.mp4`, e no `clips.md` |
| `hashtags` | tags prontas para colar no TikTok | `clips.md` e `viral_report.md` |

Uma chamada só, e não três, porque a janela já está sendo enviada e o veredito
já é cacheado por janela. Pagar duas chamadas extras pelo mesmo texto seria
pagar três vezes pela mesma leitura.

As hashtags **não** entram no vídeo. TikTok e Reels recebem hashtag como
metadado do post; uma hashtag desenhada no quadro é uma hashtag que a
plataforma ignora.

## Onde mora o prompt: um arquivo, não um banco

O prompt fica em `prompts/curador.txt`, e o painel web edita exatamente esse
arquivo.

**Por que não um banco de dados.** Um banco para guardar uma string é máquina
demais para o problema, e perde a única propriedade que importa aqui: você vai
iterar esse prompt muitas vezes, e vai precisar ver *o que mudou* quando os
cortes piorarem. Arquivo é `git diff`, é `git checkout` para voltar à versão que
funcionava, e viaja junto com o projeto para a VPS. O próprio projeto já diz
isso em `archive.py`: *"resumability comes from the filesystem, not a
database."*

O caminho do arquivo **nunca** vem da requisição. A rota grava em
`CURATOR_PROMPT_PATH` e em nenhum outro lugar — uma rota de "salvar
configuração" que aceita o destino do corpo é um gravador de arquivo arbitrário
com passos extras.

## O prompt é prosa livre; o formato de resposta é do código

O arquivo contém só as **regras**, escritas como você escreveria para uma
pessoa. O esquema JSON que o parser precisa é anexado pelo motor
(`ranker.RESPONSE_CONTRACT`).

Essa separação é o que permite colar qualquer prompt sem adaptação. Sem ela,
cada edição de texto exigiria também acertar o esquema, e um prompt que
devolvesse prosa falharia no parse com um erro que não diz nada de acionável a
quem escreveu.

## Editar o prompt invalida o cache sozinho

A chave do cache inclui o **hash do conteúdo do prompt**. Editar o arquivo faz a
próxima execução pagar por vereditos novos, em vez de reusar os do prompt
anterior.

Isso existe porque a alternativa — confiar em alguém lembrar de subir
`PROMPT_VERSION` a cada mudança de texto — é uma regra que se esquece
exatamente uma vez. O sintoma é o pior possível: a edição parece não fazer nada,
e a pessoa conclui que o recurso não funciona.

## Provedores nomeados

`viralclipper/providers.py` é uma tabela de provedores. Escolher um modelo passa
a ser um nome (`nemotron-super`) em vez de três strings que precisam ser
lembradas exatamente (URL base, id do modelo, variável da chave).

Todos falam o formato `/chat/completions` da OpenAI, que é o que um único
cliente cobre — sem SDK de fornecedor.

As latências na tabela foram **medidas**, não copiadas de catálogo. A distinção
importa: `docs/modelos-nvidia.md` registra cinco modelos que respondem HTTP 200
e mesmo assim não servem aqui. Catálogo lista o que existe; essa tabela lista o
que funciona.

Adicionar um provedor é uma entrada no dicionário. Nada mais muda.

## A chave da API nunca vai para um arquivo do projeto

O painel mostra o **nome** da variável de ambiente (`OPENAI_API_KEY`), nunca o
valor. A chave é lida do ambiente do processo que roda o servidor.

## O que depende do quê

O curador inteiro é **downstream da transcrição**. Se o whisper falhar, o
`pipeline.py` degrada para análise de áudio puro (exit code 4) e:

- não há legenda queimada, e
- não há texto para o modelo julgar, e
- portanto não há headline nem hashtags.

Um prompt bem escrito não salva uma execução sem transcrição.

## Limites que o código impõe

| Limite | Valor | Por quê |
| --- | --- | --- |
| `MAX_HEADLINE_CHARS` | 90 | acima disso a faixa precisa de uma terceira linha e come o vídeo |
| `MAX_HASHTAGS` | 10 | o TikTok para de pesar tags depois de um punhado |
| `ranker_top_n` | 24 | o custo é uma chamada por candidato por vídeo |
| `MAX_CURATOR_PROMPT_BYTES` | 64 KB | generoso para um prompt, pequeno para encher o disco |

A headline só é **queimada** com `headline_seconds > 0` (o toggle do painel).
Com o toggle desligado o texto ainda vai para o relatório — o trabalho não se
perde, ele só não altera o vídeo.

## Como usar pela linha de comando

```bash
export OPENAI_API_KEY='...'

python -m viralclipper URL \
  --ranker llm \
  --ranker-provider nemotron-super \
  --curator-prompt prompts/curador.txt \
  --headline-seconds 3
```

Ou, por arquivo de config (`--config config.toml`), que é o mesmo conjunto de
chaves — o `config.example.toml` já traz todas comentadas.

## Como saber que funcionou

O log diz quantos candidatos o modelo julgou e com que peso:

```
Ranker (nvidia/nemotron-3-super-120b-a12b) julgou 24 de 24 candidatos; peso 0.60
```

E cada clipe sai com a headline e as hashtags no `clips.md`:

```
## Clip 1 - score 71.4

- Titulo no video: ELE REVELOU O SEGREDO
- Hashtags: #viral #fyp #paravoce #cortes
```

Se as hashtags não aparecerem, o modelo respondeu sem elas — o veredito continua
válido e o score continua valendo. O log do ranker não falha por isso.
