"""Classifica os literais `rem` que ainda nao tem veredito.

Ninguem decidiu se eles tem degrau. Este script faz a conta que
`test_every_bare_rem_font_size_is_mapped_or_registered` cobra: para cada valor,
o degrau mais proximo e o teto local, e o veredito que sai disso.

Uso: python tools/orfaos.py
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalizar_fontes import (  # noqa: E402
    MAPA, SEM_DEGRAU, escala_de, limite,
)

RAIZ = Path(__file__).resolve().parent.parent
PADRAO = re.compile(r"font-size:\s*([0-9]*\.?[0-9]+)rem")


def main() -> int:
    escala = escala_de()
    usos: dict[str, int] = defaultdict(int)
    onde: dict[str, list[str]] = defaultdict(list)
    total = 0
    for folha in sorted((RAIZ / "web").glob("*.css")):
        for numero, linha in enumerate(
                folha.read_text(encoding="utf-8").splitlines(), 1):
            achado = PADRAO.search(linha)
            if not achado:
                continue
            total += 1
            valor = achado.group(1)
            if valor.startswith("."):
                valor = "0" + valor
            onde[valor].append("%s:%d" % (folha.name, numero))
            if valor in MAPA or valor in SEM_DEGRAU:
                continue
            usos[valor] += 1

    print("linhas com `font-size: <n>rem`: %d" % total)
    print("valores distintos:              %d" % len(onde))
    print("sem veredito:                   %d valores, %d usos"
          % (len(usos), sum(usos.values())))
    print()
    print("%-9s %5s %9s  %-11s %7s %7s  %s"
          % ("literal", "usos", "px", "mais perto", "dist", "teto", "veredito"))
    for valor in sorted(usos, key=float):
        px = float(valor) * 16
        dist = sorted((abs(px - v), k) for k, v in escala.items())
        teto = limite(valor, escala)
        veredito = ("MAPA -> %s" % dist[0][1]) if dist[0][0] <= teto else "SEM_DEGRAU"
        print("%-9s %5d %9.2f  %-11s %7.2f %7.2f  %s"
              % (valor, usos[valor], px, dist[0][1], dist[0][0], teto, veredito))
    return 0


if __name__ == "__main__":
    sys.exit(main())
