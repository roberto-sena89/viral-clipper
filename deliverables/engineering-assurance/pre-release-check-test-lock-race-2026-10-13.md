# Verificação de correção — corrida no helper de trava (`tests/test_lock.py`)

**Data**: 2026-10-13 (fechado em 2026-10-14 UTC)
**Fluxo**: 1 — Revisão de código (escopo: o helper de trava e os testes vizinhos)
**Membros participantes**: Cody (code-reviewer) · Tessa (testing-expert) — convocados em paralelo

---

## 📌 TL;DR (resumo executivo)

- **O defeito era do teste, não do produto.** Os dois testes de liberação da trava liam `lock.acquire()` **uma única vez** logo após matar o detentor e exigiam a trava já livre. Quem solta a trava é o handle do **filho** fechando, e o kernel faz isso **depois** de `process.wait()` retornar.
- **A corrida é reproduzível, não "sorte de timing".** Sob 8 núcleos ocupados, o comportamento antigo falhou **12/12** rodadas; o conserto, **0/12**.
- **A revisão independente aprovou** e acrescentou 1 🟠 (o prazo não tinha teste) e 2 🟡 pré-existentes — **todos fechados**.
- **Suíte completa: `1623 passed, 1478 subtests passed, 0 failed` em 2:01.** Verde, com o conserto aplicado.
- **Nada pendente.** 4 commits, todos empurrados.

---

## 🎯 Cartão de conclusão

| Item | Conteúdo |
|------|---------|
| Avaliação geral | 🟢 **Aprovado** — corrigido, verificado por A/B, revisado por dois membros e com a suíte verde |
| Itens bloqueantes | **0** |
| Achados | 🔴 0 · 🟠 1 · 🟡 4 · 🟢 1 — **todos fechados** (o 🟢 é de outra sessão, com prova) |
| Ações-chave | 4 — **todas concluídas** |
| Próximo passo | **nenhum** |

---

## 🔍 Achados (por severidade, deduplicados)

| # | Sev. | Categoria | Local | Descrição | Situação | Fonte |
|---|------|-----------|-------|-----------|----------|-------|
| 1 | 🟠 | Cobertura | `tests/test_lock.py` (`espera_livre`) | O prazo é a **única** coisa que pega "a trava nunca solta" — e apagar o prazo **não derrubava nenhum teste da suíte** | ✅ **Corrigido** (`b9aed9e`): `test_espera_livre_desists_at_the_deadline_with_a_live_holder` | Tessa |
| 2 | 🟡 | Coerência | `tests/test_lock.py:53-54` | 10 s do helper vs. 20 s dos `wait` — **dois números soltos para a MESMA espera** | ✅ **Corrigido** (`b9aed9e`): `TEARDOWN_TIMEOUT` / `ESPERA_TIMEOUT` | Cody |
| 3 | 🟡 | O teste mentia | `tests/test_lock.py` (`..._after_the_holder_exits`) | Com o corpo `pass`, o `finally` **sempre** matava o filho: os dois testes exercitavam a mesma morte à força, apesar de o nome do primeiro prometer saída limpa | ✅ **Corrigido** (`4f5c4b2`): `process.wait()` dentro do bloco + a medição no docstring | Cody + Tessa |
| 4 | 🟡 | Robustez | `holder()` (`readline()`) | `readline()` sem prazo **pendurava** a suíte se o filho ficasse vivo e calado | ✅ **Corrigido** (`4f5c4b2`): `_le_linha()` com thread daemon e prazo | Tessa |
| 5 | 🟡 | Verificação | suíte completa | O último verde rodara com o código **antigo** | ✅ **Feito**: `1623 passed, 1478 subtests, 0 failed` em 2:01 | Diretor |
| 6 | 🟢 | Árvore compartilhada | `tests/test_launcher.py` (outra sessão) | O teste novo dela lê `REPO_ROOT / "reiniciar_servidor.cmd"`, que está **untracked** | ⏸️ **Não é meu** — fecha quando ela commitar o teste e o `.cmd` juntos; não há defeito no HEAD | Diretor |
| 7 | 🟠 | Regressão externa | `tests/test_download.py:725` | A alteração **não commitada** de outra sessão em `download.py` moveu `http error 403` para antes do genérico; o teste alimentava uma entrada que casa **os dois** e exigia `"Retry"` maiúsculo | ✅ **Corrigido** (`36afb6f`): a entrada passou a ser uma interrupção de verdade | Diretor |

**Ressalva de escopo (deliberada)**: `espera_livre` foi aplicado **só** aos dois testes de liberação. O teste de **recusa** continua com `acquire` seco — ali o detentor está **vivo** e a recusa é **síncrona**; polling mascararia um defeito real. Tessa confirmou o recorte.

---

## 🧪 Evidência medida

### A/B sob carga (o experimento que decide)

8 núcleos ocupados, rodando **somente** os dois testes de liberação:

| Braço | Rodadas | Falhas |
|-------|---------|--------|
| ANTIGO (`acquire` seco) | 12 | **12** |
| CORRIGIDO (`espera_livre`) | 12 | **0** |
| CORRIGIDO, versão final (`4f5c4b2`) | 8 | **0** |

### Sondas executáveis (`_scratch/`)

| Sonda | O que prova |
|---|---|
| `verifica_trava.py` | Detentor **vivo** → `None` em 1,05 s (respeita o prazo); **morto** → handle em 0,01 s |
| `mede_teardown.py` | O stub **espera** o filho (`wait()` = 1,05 s para um filho que dorme 1,0 s) e a trava cai **21 ms** depois — o atraso assíncrono só existe no caminho da **morte** |
| `prova_saida_limpa.py` | Corpo antigo (`pass`) → rc do stub **1** (morto); corpo novo (`wait`) → rc **0** (saiu sozinho) |
| `prova_readline.py` | `readline` cru **bloqueia** além de 3 s; `_le_linha` devolve `""` em **3,00 s** |
| `prova_download_403.py` | Com a ordem do HEAD a mensagem tem `Retry`; com a nova, não — a **ordem** é a causa do #7 |
| `carga_trava.py` | A/B sob carga (`--reverter` = braço de controle) |
| `sabotagem_trava.py` | 5 mutações in-suite + 2 alvos de timeout, com restauração byte a byte |

### Sabotagem — 5/5 quedas

| Mutação | Resultado |
|---|---|
| A1 — helper devolve `None` sempre | **2 failed** ✓ |
| A2 — asserção invertida | **1 failed** ✓ |
| A3 — `espera_livre` trocado por `None` | **1 failed** ✓ |
| B1 — prazo ansioso (desiste na 1ª falha) | **1 failed** — pego pelo **teste novo** ✓ |
| E1 — `_le_linha` devolve `""` sempre | **3 failed** ✓ |
| "sem prazo" (laço infinito) | não cai: **trava**. Pego em **dois** alvos — sonda e teste novo, mortos por timeout externo ✓ |

Restauração conferida por sha256 a cada mutação.

### Suítes

| Alvo | Resultado |
|---|---|
| `tests/test_lock.py` isolado | **14 passed** |
| `tests/test_download.py` isolado | **86 passed** (antes: 1 failed, 85 passed) |
| Suíte completa, código antigo, carga alta (`qWsbz2`) | `2 failed, 1620 passed, 1478 subtests` em 50:41 |
| Suíte completa, código antigo, carga moderada (`QhS5SD`) | `1622 passed, 1478 subtests` em 1:01:32 |
| Suíte completa, com o conserto (`b9aed9e`) | `1622 passed, 1478 subtests, 1 failed` em 4:23 — a falha era a externa (#7) |
| **Suíte completa, final (`4f5c4b2`)** | **`1623 passed, 1478 subtests, 0 failed` em 2:01** |

> **Ressalva sobre a duração.** Medidas de 2:01 a 4:23 numa máquina ociosa, contra o baseline de ~29 min
> registrado no `MEMORY.md`. As **contagens de testes são idênticas** em todas, então nada foi pulado.
> Não consigo reconstruir sob que carga o baseline foi anotado — **o baseline merece nova medição**.

### Commits (todos empurrados)

| Commit | Conteúdo |
|---|---|
| `ddef1ef` | `espera_livre` + os dois testes passam a usá-lo (+31/−3) |
| `b9aed9e` | teste do prazo + orçamento único (+32/−4) |
| `36afb6f` | `test_download`: a interrupção deixa de ser testada com um 403 na frase (+10/−1) |
| `4f5c4b2` | `test_lock`: fecha os dois achados pré-existentes (+43/−3) |

Todos validados no índice: 1 arquivo cada, 0 marcadores de outra sessão, nenhum arquivo nos dois lados, nenhum `MM`.

---

## 🗣️ Pareceres dos membros

**Cody (code-reviewer)** — *Aprovar*. Crítico: nenhum. Alto: nenhum.
- 🟡 Orçamento de espera dividido (10 s vs. 20 s) — **fechado**.
- 🟢 **Não mascara defeito do produto**: trava genuinamente presa ainda falha o `assertIsNotNone` após 10 s.
- 🟢 Prazo monotônico correto, sem vazamento de descritor no caminho de falha, anotação e posição de módulo adequadas.
- 🟢 O `sleep` de 50 ms é polling, CPU desprezível. Nada incorreto para Python 3.13/Windows.

**Tessa (testing-expert)** — *Entregar, mas adicionar o teste do prazo antes*.
- 🟢 Estratégia sólida; polling corretamente restrito aos dois testes de liberação.
- 🟢 Polling é o instrumento certo — **não existe evento aguardável** (a trava é do neto; só se tem o `Popen` do stub).
- 🟠 Teste faltante do contrato do prazo — **fechado**.
- 🟡 `holder` sempre matava com `seconds=0.2`; `readline()` sem timeout — **ambos fechados**.

---

## ✅ Lista de ações

| # | Ação | Responsável | Urgência | Situação |
|---|------|-------------|----------|----------|
| 1 | Empurrar os commits | Diretor | P0 | ✅ concluído |
| 2 | Suíte completa com o conserto | Tessa / Diretor | P1 | ✅ concluído — verde |
| 3 | Corrigir a regressão externa (#7) | Diretor | P1 | ✅ concluído |
| 4 | Fechar os 🟡 pré-existentes (#3 e #4) | Tessa / Diretor | P2 | ✅ concluído |

---

## ⚠️ Pendências e limitações

- **Nenhuma pendência de execução.** O que sobra é aviso, não tarefa.
- **#6 é de outra sessão**: o teste dela depende de `reiniciar_servidor.cmd`, hoje **untracked**. Não há defeito no HEAD (o teste dela não está commitado); fecha sozinho quando ela commitar os dois juntos.
- **A árvore ainda tem 3 arquivos de outra sessão** (`tests/test_launcher.py`, `viralclipper/download.py`, `web/index.js`) — não encostei em nenhum. O `download.py` continua com a mudança **não commitada** que motivou o #7; a correção do teste é **independente da ordem**, então passa com ela commitada ou revertida.
- **O baseline de duração da suíte está errado** e foi anotado como tal no `MEMORY.md`.
- **`_scratch/` é gitignored**: as sete sondas não são versionadas; a evidência está no corpo deste relatório e nos commits.

---

## 📚 Fontes e índice de produções

- Commits: `ddef1ef`, `b9aed9e`, `36afb6f`, `4f5c4b2` — todos em `origin/master`.
- Sondas: `_scratch/verifica_trava.py`, `mede_teardown.py`, `prova_saida_limpa.py`, `prova_readline.py`, `prova_download_403.py`, `carga_trava.py`, `sabotagem_trava.py`.
- Logs: `_scratch/suite_b9aed9e.log`, `_scratch/suite_final.log`.
- Cody (code-reviewer) — produção bruta: parecer acima (aprovado, sem crítico/alto).
- Tessa (testing-expert) — produção bruta: parecer acima (entregar com o teste do prazo).

---

> Este relatório foi gerado por colaboração de IA da equipe de garantia de engenharia. Decisões críticas devem ser revisadas por um responsável humano.
