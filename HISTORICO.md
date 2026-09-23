# Historico do viral-clipper — recuperado do reflog do Git

> O repositorio `.git` perdeu seus objetos (os arquivos `.pack` desapareceram
> em 2026-09-22 ~21:03). O reflog sobreviveu e e a unica copia da linha do
> tempo. Este arquivo preserva esse registro.

> Reconstruido em 2026-09-22.

## Branch `master` (24 commits)

| # | Data | SHA | Mensagem |
| - | ---- | --- | -------- |
| 1 | 2026-09-21 14:17 | `3d43f2a77` | feat: scoring absoluto, reframe por rosto, ranker semantico e modo batch |
| 2 | 2026-09-21 14:33 | `0dff7744c` | fix: sobreviver a falta de memoria no whisper e no pool de render |
| 3 | 2026-09-21 15:31 | `752ed87ba` | fix: relatorio quebrado no render paralelo e limpeza de temporarios fatal |
| 4 | 2026-09-21 15:32 | `57b1e3a6a` | docs: numeros de throughput no README e nota sobre a fronteira de processo |
| 5 | 2026-09-21 21:12 | `8eeda1014` | feat: interface web local e cache de transcricao reutilizavel entre runs |
| 6 | 2026-09-21 21:12 | `3bbb6cab5` | feat: headline de hook queimada, barra de progresso e legenda fora da zona de UI |
| 7 | 2026-09-21 22:19 | `d3ea886ac` | fix: aviso precoce quando min-score fica inalcancavel sem transcricao |
| 8 | 2026-09-22 07:36 | `cbfaa871e` | fix: stdio utf-8 para nao crashar com titulos com combining marks |
| 9 | 2026-09-22 08:01 | `f906557d6` | fix: server web tolera console morto e impede bind duplicado na porta |
| 10 | 2026-09-22 15:03 | `5240ab0dd` | feat: dropdowns customizados com descricoes e painel via portal no body |
| 11 | 2026-09-22 15:55 | `68ac894ce` | feat: importacao de transcricao pronta e relatorio de viralizacao por corte |
| 12 | 2026-09-22 15:55 | `51465fa53` | test: cobertura do stdio tolerante a console morto e do resolve de paths da galeria |
| 13 | 2026-09-22 16:10 | `73a575cf1` | feat: normalizacao da transcricao colada com minutos ordenados e falas alinhadas |
| 14 | 2026-09-22 16:27 | `7b1b70897` | fix: galeria distingue corte analisado de clip renderizado e expoe render sob demanda |
| 15 | 2026-09-22 16:38 | `cf1d9d772` | feat: listagem da pasta de saida na galeria para arquivos renomeados e de runs antigos |
| 16 | 2026-09-22 16:43 | `3a5d86138` | fix: titulo do gancho desligado por padrao e controle na interface web |
| 17 | 2026-09-22 16:55 | `ab2c07a29` | fix: titulo do gancho quebra linha e nao corta a frase no meio |
| 18 | 2026-09-22 17:43 | `861ba0f89` | fix: barra de progresso desligada por padrao, com opt-in na interface |
| 19 | 2026-09-22 18:38 | `f058a1a9a` | fix: falha de download do yt-dlp mostra a causa real em vez de exit code 1 |
| 20 | 2026-09-22 19:46 | `642e3e32d` | feat: presets de legenda prontos (karaoke, bold-box, minimal, neon, block-dark, mono) |
| 21 | 2026-09-22 20:10 | `182df22db` | feat: previa visual dos modelos de legenda no dropdown de preset |
| 22 | 2026-09-22 20:32 | `0ddc39adb` | chore: ignora imagens de teste visual locais |

## Branch `workbuddy/master-57848403`

| # | Data | SHA | Mensagem |
| - | ---- | --- | -------- |
| 1 | 2026-09-22 20:12 | `182df22db` | branch: Created from master |

## O que se perdeu

Os 24 commits acima existiam como objetos Git e foram removidos. Sem os
arquivos `.pack`, os SHAs acima nao navegam: `git show 3d43f2a` falha com
`Not a valid object name`. A arvore do ultimo commit (`0ddc39a`) tambem se
perdeu — sobraram o objeto do commit e nada abaixo dele.

O codigo-fonte em si nunca esteve em risco: os arquivos sempre viveram no
disco, fora do `.git`. O que se perdeu foi o *historico* — quem mudou o que,
quando e por que — nao o estado atual do projeto.
