# Documentação

Índice de `docs/`. A regra que organiza esta pasta:

> **`docs/` na raiz = vivo e atemporal.** Um contrato que precisa continuar
> verdadeiro, um mapa que precisa continuar certo.
> **`docs/historico/` = datado e imutável.** Um número com data, um retrato do
> dia, uma decisão já tomada. Não se corrige o passado — arquiva-se.

## Vivo (leia primeiro)

| Arquivo                              | Para quem          | O que responde                                             |
| ------------------------------------ | ------------------ | ---------------------------------------------------------- |
| [`arquitetura.md`](arquitetura.md)   | quem mexe no motor | o fluxo, o mapa de módulos, contratos, concorrência, exit codes |
| [`painel-web.md`](painel-web.md)     | quem mexe no painel| as 4 páginas, rotas, CSP/porta, guarda, `ajustes.toml`     |
| [`configuracao.md`](configuracao.md) | quem configura     | TOML vs YAML, a armadilha do `[table]`, os grupos de chaves |
| [`curador.md`](curador.md)           | quem escreve prompt| o contrato do prompt do curador                            |

## Retratos de provedores (datados, mas úteis)

| Arquivo                                              | O que é                        |
| ---------------------------------------------------- | ------------------------------ |
| [`modelos-gratuitos.md`](modelos-gratuitos.md)       | retrato dos LLMs gratuitos     |
| [`modelos-nvidia.md`](modelos-nvidia.md)             | retrato dos modelos NVIDIA     |

Estes envelhecem: o preço e o limite mudam. Leia como foto, não como contrato.

## Histórico (datado, imutável)

| Arquivo                                                                          | Data       |
| -------------------------------------------------------------------------------- | ---------- |
| [`historico/auditoria-2026-10-07.md`](historico/auditoria-2026-10-07.md)         | 2026-10-07 |
| [`historico/spec-2026-09-27-editor-faixas-template-design.md`](historico/spec-2026-09-27-editor-faixas-template-design.md) | 2026-09-27 |

A auditoria contém o diagnóstico completo (arquitetura, UX/UI, segurança, custo),
os números medidos no dia e o que já foi corrigido. **Parcialmente desatualizada
por desenho** — ela narra um estado que o código já superou em pontos. Os
contratos que sobrevivem dela foram destilados para os documentos vivos acima.

## Raiz do repositório

- [`../README.md`](../README.md) — a porta de entrada do produto (instalação,
  uso, opções, presets).
- [`../HISTORICO.md`](../HISTORICO.md) — a narrativa do projeto.
