"""Confere se a normalizacao de fontes ficou dentro do limite que ela promete.

A regra de `tools/normalizar_fontes.py`: um literal entra no degrau mais proximo
SE esse degrau for o mais proximo SEM EMPATE. Nao ha' um teto unico -- a escala
tem passo de 0,50px (cluster body-sm/body/cta/subtitle) a 5,00px (title ->
heading-sm), e um numero so' seria frouxo num lugar e apertado no outro. O teto
e' LOCAL: metade do menor passo que toca o literal.

Este script recalcula isso contra os valores REAIS de `web/shared.css` e
imprime o desvio de cada troca, mais os literais que ficaram de fora e por que'.

Uso:
    python tools/conferir_normalizacao.py
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

#: O MAPA e as funcoes vem do proprio normalizador. Duplicar aqui faria a
#: conferencia concordar com uma copia, nao com o que roda.
sys.path.insert(0, str(RAIZ / "tools"))
from normalizar_fontes import (  # noqa: E402
    MAPA, SEM_DEGRAU, escala_de, limite,
)


def main() -> int:
    escala = escala_de()
    if not escala:
        print("!! shared.css nao declara a escala --text-*")
        return 1

    xs = sorted(escala.values())
    print("== ESCALA (de web/shared.css) ==")
    anterior = None
    for nome in sorted(escala, key=lambda n: escala[n]):
        px = escala[nome]
        passo = "" if anterior is None else "  <- %.2fpx do anterior" % (px - anterior)
        print("  --text-%-12s %6.2fpx%s" % (nome, px, passo))
        anterior = px
    passos = [b - a for a, b in zip(xs, xs[1:])]
    print("  passo entre degraus: min %.2fpx, max %.2fpx" % (
        min(passos), max(passos)))

    print("\n== DESVIO DE CADA ENTRADA DO MAPA ==")
    print("  %-8s %-11s %8s %8s %8s %9s  %s" % (
        "literal", "-> degrau", "px antes", "px depois", "delta",
        "teto local", ""))

    fora = []
    no_teto = []
    for literal, degrau in sorted(MAPA.items(), key=lambda kv: float(kv[0])):
        if degrau not in escala:
            print("  !! degrau %s nao existe na escala" % degrau)
            continue
        antes = float(literal) * 16
        depois = escala[degrau]
        delta = abs(antes - depois)
        teto = limite(literal, escala)
        #: O teto local sozinho nao basta: o degrau escolhido tem de ser o mais
        #: proximo SEM EMPATE. Um literal pode estar dentro do teto e ainda
        #: empatar com outro degrau.
        ordenado = sorted(((k, abs(antes - escala[k])) for k in escala),
                          key=lambda kv: kv[1])
        #: O teto e' metade da separacao entre os dois degraus mais proximos.
        #: Se o literal esta' mais longe que isso do seu degrau, o segundo
        #: degrau esta' quase a' mesma distancia: nao ha' vencedor.
        empate = delta > teto
        if empate:
            marca = "EMP"
            fora.append((literal, degrau, antes, depois, delta, teto))
        elif delta > teto * 0.5:
            marca = "!! "
            no_teto.append((literal, degrau, antes, depois, delta, teto))
        else:
            marca = "ok "
        print("  %s %-8s %-11s %8.2f %8.2f %7.2fpx %8.2fpx  %s" % (
            marca, literal, "-> " + degrau, antes, depois, delta, teto,
            ("EMPATE com --text-%s (%.2fpx)" % (ordenado[1][0], ordenado[1][1]))
            if empate else ""))

    print("\n== RESULTADO ==")
    print("  entradas no mapa      : %d" % len(MAPA))
    print("  dentro do teto local  : %d" % (len(MAPA) - len(fora)))
    print("  PROBLEMA (teto/empate): %d" % len(fora))

    if no_teto:
        print("\n  Perto do teto local -- o movimento e' quase metade do passo:")
        for literal, degrau, antes, depois, delta, teto in sorted(
                no_teto, key=lambda x: -x[4]):
            print("    %-8s -> %-11s %7.2fpx -> %7.2fpx   %.2fpx (teto %.2f)"
                  % (literal, degrau, antes, depois, delta, teto))

    if fora:
        print("\n  As entradas com problema:")
        for literal, degrau, antes, depois, delta, teto in sorted(
                fora, key=lambda x: -x[4]):
            print("    %-8s -> %-11s %7.2fpx -> %7.2fpx   %.2fpx (teto %.2f)"
                  % (literal, degrau, antes, depois, delta, teto))
        print("\n  ACAO: tire do MAPA e registre em SEM_DEGRAU. Um literal que")
        print("  empata entre dois degraus nao tem 'o mais proximo'.")
        return 1

    print("\n  Toda troca esta dentro do teto local e sem empate.")

    print("\n== O QUE FICOU DE FORA (SEM_DEGRAU) ==")
    print("  %-8s %-10s %8s %8s %8s %9s  %s" % (
        "literal", "degrau", "px hoje", "px degrau", "delta", "teto local", ""))
    ruins = []
    for literal in sorted(SEM_DEGRAU, key=float):
        degrau, antes, depois, delta, segundo = SEM_DEGRAU[literal]
        if degrau not in escala:
            print("  !! %s aponta para degrau inexistente" % literal)
            continue
        real = abs(float(literal) * 16 - escala[degrau])
        teto = limite(literal, escala)
        empate = real > teto
        if not empate:
            marca = "?? "
            ruins.append(literal)
        else:
            marca = "ok "
        print("  %s %-8s %-10s %8.2f %8.2f %7.2fpx %8.2fpx  %s" % (
            marca, literal, degrau, antes, depois, real, teto,
            "EMPATE com --text-%s" % segundo if empate else ""))
        if abs(real - delta) > 0.01:
            print("      !! o delta anotado (%.2f) nao bate com a escala (%.2f)"
                  % (delta, real))
            ruins.append(literal)
    if ruins:
        print("\n  PROBLEMA: %r cabem no teto e deviam estar no MAPA." % ruins)
        return 1
    print("  (%d literais; todos passam do teto local ou empatam)" % len(SEM_DEGRAU))
    return 0


if __name__ == "__main__":
    sys.exit(main())
