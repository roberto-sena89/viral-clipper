"""Mede como o painel declara tamanho de texto e cor.

Existe como arquivo pelo mesmo motivo de `breakpoints.py`: o heredoc do Git
Bash come as barras invertidas das expressoes, entao um `re` escrito na linha
de comando volta vazio e o levantamento conclui "nenhum uso" com centenas na
frente. Rodar daqui leva ao mesmo numero sempre.

Uso:
    python tools/tokens.py
"""
import re
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FONT = re.compile(r"font-size:\s*([^;}]+)")
VAR = re.compile(r"var\((--[a-z0-9-]+)")
DEF = re.compile(r"^\s*(--[a-z0-9-]+)\s*:", re.M)


def texto() -> str:
    return "".join(
        p.read_text(encoding="utf-8") for p in sorted((RAIZ / "web").glob("*.css"))
    )


def main():
    t = texto()
    definidas = set(DEF.findall(t))
    usos = Counter(VAR.findall(t))
    mortas = sorted(d for d in definidas if not usos.get(d))

    print("custom properties definidas:", len(definidas))
    print("usadas em var():", len([d for d in definidas if usos.get(d)]))
    print("definidas e nunca usadas:", len(mortas))
    print()
    for d in mortas:
        print("   ", d)

    print()
    print("--- tamanhos de fonte ---")
    fixos = [v.strip() for v in FONT.findall(t)]
    com_token = [v for v in fixos if "var(" in v]
    literais = [v for v in fixos if "var(" not in v]
    print("declaracoes de font-size:", len(fixos))
    print("  via token (var(--text-*)):", len(com_token))
    print("  literais:", len(literais))
    conta = Counter(literais)
    for v, n in sorted(conta.items(), key=lambda kv: -kv[1])[:20]:
        print("    %4dx  %s" % (n, v))

    print()
    print("--- literais contra a escala de `shared.css` ---")
    #: Os degraus, em rem. Base 14px, como documenta o shared.css.
    escala = {
        "2xs": 0.6875, "caption": 0.8, "body-sm": 0.84375, "body": 0.875,
        "cta": 0.90625, "subtitle": 0.9375, "title": 1.0625,
        "heading-sm": 1.375, "heading": 1.5,
    }
    por_valor = Counter()
    for valor in literais:
        achado = re.fullmatch(r"([0-9.]+)rem", valor)
        if achado:
            por_valor[float(achado.group(1))] += 1

    exatos, perto, longe = [], [], []
    for valor, vezes in sorted(por_valor.items()):
        if valor in escala.values():
            exatos.append((valor, vezes))
            continue
        melhor = min(escala, key=lambda k: abs(escala[k] - valor))
        dif = abs(escala[melhor] - valor) * 14
        (perto if dif <= 0.6 else longe).append((valor, vezes, melhor, round(dif, 2)))

    print("valores literais distintos:", len(por_valor),
          "| usos:", sum(por_valor.values()))
    print()
    print("batem EXATO num degrau (troca segura por var()):")
    for valor, vezes in exatos:
        nome = [k for k, x in escala.items() if x == valor][0]
        print("    %8srem  %3dx  -> --text-%s" % (valor, vezes, nome))
    print("    subtotal: %d usos" % sum(v for _, v in exatos))
    print()
    print("a <=0,6px de um degrau (arredondamento):")
    for valor, vezes, melhor, dif in perto:
        print("    %8srem  %3dx  -> --text-%s  (dif %spx)"
              % (valor, vezes, melhor, dif))
    print("    subtotal: %d usos" % sum(v for v, _, _, _ in perto))
    print()
    print("sem degrau equivalente (precisa decisao):")
    print("    %d valores, %d usos"
          % (len(longe), sum(v for v, _, _, _ in longe)))
    for valor, vezes, melhor, dif in longe[:16]:
        print("    %8srem  %3dx  (mais perto --text-%s, dif %spx)"
              % (valor, vezes, melhor, dif))
    return 0


if __name__ == "__main__":
    sys.exit(main())
