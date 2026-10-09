"""Mede o inventario de breakpoints do painel, lendo as folhas de estilo.

Existe como arquivo (e nao como `python -c` com regex no shell) porque o
heredoc do Git Bash come as barras invertidas: `max-width` chegava como
`max-width` sem o escape esperado e todo `findall` voltava vazio -- o que
faria este levantamento concluir "nenhum breakpoint" com 68 na frente.
"""
import re
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PADRAO = re.compile(r"(?:min|max)-width:\s*(\d+)px")


def ocorrencias():
    """(valor, arquivo, linha, texto) de cada `min|max-width` em `@media`.

    A varredura carrega o estado "estou dentro de /* */" de linha para linha.
    Nao da' para filtrar linha a linha: quase todo comentario deste repo abre em
    uma linha e continua nas seguintes, e e' numa dessas continuacoes que mora o
    `@media (max-width: 1280px)` citado em prosa. Filtrando so' a linha, a prosa
    entrava na contagem como media query de verdade -- 43 achados onde havia 41.
    """
    for f in sorted((RAIZ / "web").glob("*.css")):
        dentro = False
        for i, linha in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            visivel, dentro = _fora_do_comentario(linha, dentro)
            if "@media" not in visivel:
                continue
            for m in PADRAO.finditer(visivel):
                yield int(m.group(1)), f.name, i, visivel.strip()


def _fora_do_comentario(linha: str, dentro: bool) -> tuple[str, bool]:
    """Devolve (parte da linha que e' codigo, se a proxima linha abre comentada)."""
    saida, resto = [], linha
    while resto:
        if dentro:
            fim = resto.find("*/")
            if fim < 0:
                return "".join(saida), True
            resto, dentro = resto[fim + 2 :], False
            continue
        inicio = resto.find("/*")
        if inicio < 0:
            saida.append(resto)
            break
        saida.append(resto[:inicio])
        resto, dentro = resto[inicio + 2 :], True
    return "".join(saida), dentro


def main():
    achados = list(ocorrencias())
    valores = Counter(v for v, _, _, _ in achados)
    print("media queries com width:", len(achados))
    print("valores distintos:", len(valores))
    print()
    for v in sorted(valores):
        print("%5dpx  %2d uso(s)" % (v, valores[v]))
        for valor, nome, i, _ in achados:
            if valor != v:
                continue
            print("         %s:%d  %s" % (nome, i, seletor(nome, i)))
    return 0


def seletor(arquivo: str, linha: int) -> str:
    """O primeiro seletor depois da `@media`, para dizer o que ela governa.

    Usa o mesmo estado de comentario da varredura: a linha que abre o `@media`
    pode ser a ultima de um bloco `/* */`, e o seletor procurado vem depois.
    """
    linhas = (RAIZ / "web" / arquivo).read_text(encoding="utf-8").splitlines()
    dentro = False
    for k, bruta in enumerate(linhas, 1):
        visivel, dentro = _fora_do_comentario(bruta, dentro)
        if k < linha:
            continue
        s = visivel.strip()
        if s and not s.startswith(("@", "}")):
            return s.split("{")[0].strip()[:52]
    return ""


if __name__ == "__main__":
    sys.exit(main())
