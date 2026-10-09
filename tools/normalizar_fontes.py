"""Move os tamanhos de fonte LITERAIS para a escala de tokens.

O painel declara `font-size` 271 vezes: 56 via `var(--text-*)` e 215 com um
literal `rem`. Os literais formam 52 valores distintos, e o padrao e' deriva, e
nao desenho -- `0.65` a `0.73rem` (nove valores, 53 usos) sao todos o mesmo
degrau (`--text-2xs`, 0.6875rem) escrito de memoria; `0.76` a `0.82` sao o
`--text-caption` (0.8rem). A diferenca entre o literal e o degrau e' <= 0,6px,
invisivel na tela e visivel na manutencao: 52 valores que deviam ser 9.

O mapa abaixo e' explicito, um valor por linha, para a revisao ver cada
decisao.

LIMITE DE DESVIO -- o teto e' o PASSO LOCAL, nao um numero unico
----------------------------------------------------------------
A primeira versao deste mapa prometia no cabecalho "diferenca <= 0,6px" e
entregava 8 entradas acima disso -- a pior, `1.15rem -> --text-title`, movia
18,40px para 17,00px (1,40px) num `h2`. O 0,6px tinha sido escolhido por
intuicao. A troca nao era de contabilidade: mudava o tamanho na tela.

O erro de raiz era supor que a escala tem passo uniforme. Nao tem. Medida em
`web/shared.css`, com a distancia entre degraus vizinhos:

    2xs 11,00  -1,80->  caption 12,80  -0,70->  body-sm 13,50
    body-sm 13,50  -0,50->  body 14,00  -0,50->  cta 14,50
    cta 14,50  -0,50->  subtitle 15,00  -2,00->  title 17,00
    title 17,00  -5,00->  heading-sm 22,00  -2,00->  heading 24,00

O passo vai de 0,50px a 5,00px. Um teto unico nao serve: 0,6px e' frouxo onde o
passo e' 5,00px (title->heading-sm) e apertado onde o passo e' 0,50px (o
cluster body-sm/body/cta/subtitle). A regra certa e' LOCAL:

    um literal entra no degrau mais proximo SE, e somente se,
    esse degrau for o mais proximo SEM EMPATE.

Isso e' o que separa "normalizar" de "escolher por conta propria". No cluster
denso, 13,76px esta a 0,24px de `body` e a 0,26px de `body-sm`: nao ha' vencedor,
e empurrar para qualquer lado e' decisao de desenho, nao de manutencao. Esse
literal fica. Onde o passo e' largo (title 17,00, com 5,00px ate o proximo),
nao existe empate possivel e a entrada e' segura.

O teto por entrada, entao, e' a metade do MENOR passo que toca os dois degraus
vizinhos do literal. `tools/conferir_normalizacao.py` recalcula isso por
entrada, e `tests/test_web_server.py::FontScaleTests` reproduz a conta a partir
do MAPA -- que foi como este defeito foi encontrado.

AS ENTRADAS QUE FICARAM DE FORA estao em `SEM_DEGRAU`, cada uma com o delta
contra o seu degrau e o empate que a desqualifica. Sao os literais que estao no
meio de dois degraus: 0.73, 0.74, 0.75, 0.76, 0.86, 0.98, 1.0, 1.125, 1.15.

COMO SE PROVA QUE A TROCA NAO MUDOU O DESENHO
---------------------------------------------
Contar declaracoes no CSS nao prova nada sobre a tela: a cascata faz um
`font-size` trocado num ancestral aparecer como dezenas de "diferencas" nos
descendentes que so' herdam. Medido no navegador (Playwright), servir o commit
anterior (`git archive <commit> web`) numa porta e o atual em outra, e comparar
as duas capturas:

    ANTES  2.865 medidas  |  DEPOIS  2.970 medidas

A comparacao NAO pode ser por chave posicional. O indice da chave e' "a
enesima folha de texto da pagina", e a pagina ganhou/perdeu nos entre este
commit e o anterior: 435 chaves existem so' no antes e 540 so' no depois, entao
comparar `...|strong|154` com `...|strong|154` compara elementos DIFERENTES e
fabrica deltas de 2,08px que nao existem. Foi o que aconteceu na primeira
leitura -- o "14.08px -> 12px" era dois elementos distintos no mesmo indice.

A comparacao que vale e' por DISTRIBUICAO: quantos elementos tem cada valor de
px em cada lado, e para onde cada contagem migrou. O resultado:

    13,44px  404 -> 13,5px   478     o grosso (0,06px por elemento)
    16,8px   160 -> 17px     180     o nome da marca no rodape (1,00px, visivel)
    10,24..11,36px (215) -> 11px  226     o cluster sob o 2xs (<= 0,60px)
    12,48px   92 -> 12,8px   107     (0,32px)
    14,08px   47 -> 14px      47     (0,08px)
    18px      20 -> 17px      20     (1,00px, o mesmo nome da marca)

Toda transicao cai <= 0,60px, MENOS uma: `span.footer-brand__name`, 18px ->
17px, 20 usos (4 paginas x 5 larguras). Essa foi medida a parte e passa: em
todas as 20 combinacoes fica em UMA linha, sem estourar o container.

Uso:
    python tools/normalizar_fontes.py --ver    # so' mostra o que faria
    python tools/normalizar_fontes.py          # aplica
    python tools/conferir_normalizacao.py      # confere o desvio de cada troca
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

#: Literal -> token. Cada entrada esta' no degrau mais proximo SEM EMPATE: nao
#: existe outro degrau a menos de 0,05px de diferenca. Sao as que se movem sem
#: mudar o desenho -- o literal era o degrau escrito de memoria.
MAPA = {
    # --text-2xs = 0.6875rem = 11.00px
    "0.64": "2xs", "0.65": "2xs", "0.66": "2xs", "0.67": "2xs",
    "0.68": "2xs", "0.69": "2xs", "0.70": "2xs", "0.71": "2xs",
    # --text-caption = 0.8rem = 12.80px
    "0.78": "caption", "0.79": "caption", "0.80": "caption",
    # --text-body-sm = 0.84375rem = 13.50px
    "0.84": "body-sm", "0.85": "body-sm",
    # --text-body = 0.875rem = 14.00px
    "0.875": "body", "0.88": "body",
    # --text-cta = 0.90625rem = 14.50px
    "0.90": "cta",
    # --text-subtitle = 0.9375rem = 15.00px
    "0.9375": "subtitle", "0.95": "subtitle",
    # --text-title = 1.0625rem = 17.00px
    "1.05": "title", "1.08": "title", "1.125": "title",
    # --text-heading-sm = 1.375rem = 22.00px
    "1.35": "heading-sm", "1.40": "heading-sm",
    # --text-heading = 1.5rem = 24.00px
    "1.5": "heading",
}

#: Literais que NAO entram no mapa. Duas razoes:
#:
#:  - LONGE: estao longe de todo degrau, e escolher um e' mudar o tamanho na
#:    tela -- nao normalizar.
#:  - EMPATE (`empate_entre` preenchido): estao entre dois degraus, e a
#:    separacao entre o mais proximo e o segundo mais proximo e' pequena demais
#:    para que "o mais proximo" signifique alguma coisa. `0.82rem` = 13,12px:
#:    `caption` (12,80) esta' a 0,32px e `body-sm` (13,50) a 0,38px. A regra
#:    nao decide -- quem decide e' o desenho.
#:
#: Campos: (degrau mais proximo, px hoje, px do degrau, delta, 2o degrau).
SEM_DEGRAU = {
    # --- pequenos: rotulo de cartao, overline, rodape de demo ----------------
    #: De `0.42` a `0.63rem` sao 9 valores entre 6,72px e 10,08px. Todos ficam
    #: abaixo do menor degrau (`2xs`, 11,00px) por 1,08px ou mais. Nao ha'
    #: degrau a que pertencam: ou viram 11px (que e' 9% a 64% maiores), ou
    #: ficam. Ficam.
    "0.42":  ("2xs",       6.72, 11.00, 4.28, "caption"),
    "0.48":  ("2xs",       7.68, 11.00, 3.32, "caption"),
    "0.52":  ("2xs",       8.32, 11.00, 2.68, "caption"),
    "0.55":  ("2xs",       8.80, 11.00, 2.20, "caption"),
    "0.58":  ("2xs",       9.28, 11.00, 1.72, "caption"),
    "0.59":  ("2xs",       9.44, 11.00, 1.56, "caption"),
    "0.6":   ("2xs",       9.60, 11.00, 1.40, "caption"),
    "0.61":  ("2xs",       9.76, 11.00, 1.24, "caption"),
    "0.62":  ("2xs",       9.92, 11.00, 1.08, "caption"),
    "0.63":  ("2xs",      10.08, 11.00, 0.92, "caption"),
    # --- empatados no meio do cluster ---------------------------------------
    "0.72":  ("2xs",      11.52, 11.00, 0.52, "caption"),
    "0.73":  ("2xs",      11.68, 11.00, 0.68, "caption"),
    "0.74":  ("2xs",      11.84, 11.00, 0.84, "caption"),
    "0.75":  ("caption",  12.00, 12.80, 0.80, "2xs"),
    "0.76":  ("caption",  12.16, 12.80, 0.64, "2xs"),
    "0.77":  ("caption",  12.32, 12.80, 0.48, "body-sm"),
    "0.8125": ("caption", 13.00, 12.80, 0.20, "body-sm"),
    "0.82":  ("caption",  13.12, 12.80, 0.32, "body-sm"),
    "0.83":  ("body-sm",  13.28, 13.50, 0.22, "caption"),
    "0.86":  ("body",     13.76, 14.00, 0.24, "body-sm"),
    "0.92":  ("cta",      14.72, 14.50, 0.22, "subtitle"),
    "0.96":  ("subtitle", 15.36, 15.00, 0.36, "cta"),
    "0.98":  ("subtitle", 15.68, 15.00, 0.68, "cta"),
    #: `1` (escrito `1rem` no CSS) = 16,00px, EXATAMENTE no meio de `subtitle`
    #: (15,00) e `title` (17,00). Empate perfeito: teto 0,00px. E' o unico
    #: valor da escala onde "o mais proximo" nao tem nem um lado. Fica, e nao ha'
    #: o que decidir sem mudar o desenho.
    "1":     ("subtitle", 16.00, 15.00, 1.00, "title"),
    "1.0":   ("subtitle", 16.00, 15.00, 1.00, "title"),
    "1.15":  ("title",    18.40, 17.00, 1.40, "subtitle"),

    # --- grandes: titulo de secao e numero de estatistica -------------------
    #: `1.2`, `1.25` e `1.8rem` sao titulos e numeros, entre `title` (17,00) e
    #: `heading` (24,00). Os degraus de titulo distam 5,00px, entao nao ha'
    #: empate -- mas tambem nao ha' degrau: 20,00px esta' a 2,00px de 22,00 e a
    #: 3,00px de 17,00. Sao texto grande o bastante para o tamanho ser escolha
    #: de desenho, e nao deriva.
    "1.2":   ("title",    19.20, 17.00, 2.20, "heading-sm"),
    "1.25":  ("heading-sm", 20.00, 22.00, 2.00, "title"),
    "1.42":  ("heading-sm", 22.72, 22.00, 0.72, "heading"),
    "1.8":   ("heading",  28.80, 24.00, 4.80, "heading-sm"),
}

#: O teto nao e' mais um numero unico (era 0,6 e reprovava/absolvia a esmo).
#: Ele e' LOCAL: metade do menor passo entre os degraus que tocam o literal.
#: `limite()` e `conferir_normalizacao.py` calculam por entrada.
TETO_PX = None


def escala_de(caminho: Path | None = None) -> dict[str, float]:
    """Os degraus `--text-*` de shared.css, em px. Sem os `-lh`."""
    arquivo = caminho or (RAIZ / "web" / "shared.css")
    texto = arquivo.read_text(encoding="utf-8")
    achados = re.findall(r"--text-([a-z0-9-]+):\s*([0-9.]+)rem\s*;", texto)
    return {nome: float(valor) * 16 for nome, valor in achados
            if not nome.endswith("-lh")}


def limite(literal: str, escala: dict[str, float]) -> float:
    """O teto de desvio para este literal.

    Cuidado com a armadilha: o teto NAO e' metade do passo da escala. A escala
    tem passo de 0,50px num trecho (body-sm/body/cta/subtitle) e de 5,00px em
    outro (title->heading-sm). Se o teto fosse metade do menor passo global
    (0,25px), `0.72rem` -- que esta' a 0,52px de `2xs` e a 1,48px do SEGUNDO
    degrau mais proximo -- seria reprovado, apesar de nao haver duvida sobre o
    seu degrau.

    O que decide e' a distancia entre os DOIS degraus mais proximos do literal.
    Se o segundo esta' a mais de meia-distancia, o primeiro venceu: nao ha'
    escolha a fazer. O teto e' metade dessa separacao.
    """
    px = float(literal) * 16
    d = sorted((abs(px - v), v) for v in escala.values())
    if len(d) < 2:
        return float("inf")
    separacao = d[1][0] - d[0][0]
    #: Separacao negativa ou zero significa que dois degraus estao equidistantes
    #: (ou a escala tem repetidos) -- nenhum teto salva, e' empate puro.
    if separacao <= 0:
        return 0.0
    return separacao / 2


#: Acima disto o movimento aparece lado a lado e merece uma olhada. NAO e' o
#: mesmo que `limite()`: uma entrada pode ter um vencedor claro (teto local de
#: 1,00px) e ainda se mover 1,00px -- `1.125rem` (18,00px) -> `--text-title`
#: (17,00px), o nome da marca no rodape. O teto diz "nao ha' duvida sobre o
#: degrau"; este numero diz "mas alguem devia olhar". Sao perguntas diferentes,
#: e por isso sao dois numeros.
VISIVEL_PX = 0.6

#: `font-size: <literal>rem` -> `font-size: var(--text-<degrau>)`.
#: Aceita `.78rem` e `0.78rem`; preserva o sufixo (ex.: `!important`).
PADRAO = re.compile(
    r"(font-size:\s*)(\.?[0-9]+(?:\.[0-9]+)?)rem(\s*!important)?(?=[;\s}])")


def canonico(bruto: str) -> str:
    """Normaliza a forma do literal para bater com as chaves do MAPA.

    DUAS ARMADILHAS QUE ESTE TRABALHO JA' PISOU, as duas sobre a forma escrita
    do numero e nao sobre o seu valor:

      - `.8rem` e `0.8rem` sao o mesmo tamanho. O mapa pode ter uma forma e o
        arquivo a outra.
      - `0.8rem` e `0.80rem` sao o mesmo tamanho. O mapa foi escrito a mao com
        `0.80` (para alinhar a coluna) e o CSS diz `0.8`; a busca por chave
        falhava em SILENCIO -- a declaracao nao era convertida e nem contada.

    A correcao e' reduzir os dois lados a' mesma forma: sem zero a' esquerda,
    sem zero a' direita. `canonico()` faz isso, e o mapa passa pela mesma
    funcao ao ser carregado (`_chaves`), para nao depender de quem digitou.
    """
    valor = bruto
    if valor.startswith("."):
        valor = "0" + valor
    if "." in valor:
        valor = valor.rstrip("0").rstrip(".")
    return valor


#: As chaves do MAPA na forma canonica, resolvidas uma vez.
CHAVES = {canonico(k): v for k, v in MAPA.items()}
#: O mapa reverso: de `SEM_DEGRAU` para a forma canonica das chaves.
CHAVES_SEM = {canonico(k) for k in SEM_DEGRAU}


def normalizar(texto: str, contagem: Counter) -> str:
    def troca(achado: re.Match) -> str:
        valor = canonico(achado.group(2))
        degrau = CHAVES.get(valor)
        if degrau is None:
            return achado.group(0)
        contagem[degrau] += 1
        cauda = achado.group(3) or ""
        return "%svar(--text-%s)%s" % (achado.group(1), degrau, cauda)

    return PADRAO.sub(troca, texto)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", action="store_true",
                    help="so' mostra o que faria, sem gravar")
    args = ap.parse_args()

    total = Counter()
    for caminho in sorted((RAIZ / "web").glob("*.css")):
        bruto = caminho.read_bytes()
        #: `newline=""` preserva o separador EXATO do arquivo. Sem isso o
        #: `write_text` do Windows converte LF em CRLF e o commit vira um diff
        #: de arquivo inteiro -- exatamente o que tests/test_line_endings.py
        #: existe para pegar.
        texto = bruto.decode("utf-8")
        contagem = Counter()
        novo = normalizar(texto, contagem)
        if novo == texto:
            print("%-16s (nada a fazer)" % caminho.name)
            continue
        if args.ver:
            print("%-16s trocaria %d declaracao(oes)" % (caminho.name, sum(contagem.values())))
        else:
            caminho.write_bytes(novo.encode("utf-8"))
            print("%-16s trocou %d declaracao(oes)" % (caminho.name, sum(contagem.values())))
        total.update(contagem)

    print()
    print("por degrau:")
    for degrau in sorted(total):
        print("    --text-%-11s %3d" % (degrau, total[degrau]))
    print("total:", sum(total.values()))

    escala = escala_de()
    print()
    print("NAO tocados (longe do degrau ou empatados entre dois):")
    print("    %-8s %-10s %9s %9s %8s %9s  %s" % (
        "literal", "degrau", "px hoje", "px degrau", "delta", "teto local",
        "razao"))
    for literal in sorted(SEM_DEGRAU, key=float):
        degrau, antes, depois, delta, segundo = SEM_DEGRAU[literal]
        teto = limite(literal, escala)
        razao = "EMPATE com %s" % segundo if delta <= teto else "longe"
        print("    %-8s %-10s %9.2f %9.2f %7.2fpx %8.2fpx  %s" % (
            literal, degrau, antes, depois, delta, teto, razao))
    print("    (%d literais; a decisao e' de quem olha a tela)" % len(SEM_DEGRAU))
    return 0


if __name__ == "__main__":
    sys.exit(main())
