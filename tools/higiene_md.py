"""Tira espaco no fim de linha e garante newline final nos markdown dados.

Existe como arquivo pelo mesmo motivo de `breakpoints.py`: escrever isto com
`sed`/`perl` na linha de comando depende de escape que o heredoc do Git Bash
come. Aqui a regra e' visivel e roda igual toda vez.

Uso:
    python tools/higiene_md.py <arquivo.md> [<arquivo.md> ...]
"""
import sys
from pathlib import Path


def limpar(caminho: Path) -> tuple[int, bool]:
    """Devolve (linhas com espaco removido, se o newline final foi acrescentado)."""
    bruto = caminho.read_bytes()
    texto = bruto.decode("utf-8")
    # `splitlines` e `"\n".join` normalizam de passagem: o CR do CRLF volta
    # junto do "\r\n" reconstruido abaixo. Trabalhamos sobre as linhas e
    # decidimos o terminador pelo que ja' estava no arquivo.
    crlf = bruto.count(b"\r\n") > bruto.count(b"\n") / 2
    fim = "\r\n" if crlf else "\n"
    linhas = texto.splitlines()

    removidos = 0
    saida = []
    for linha in linhas:
        limpa = linha.rstrip()
        if limpa != linha:
            removidos += 1
        saida.append(limpa)

    corpo = fim.join(saida) + fim
    acrescentou = not bruto.endswith((b"\n", b"\r"))
    caminho.write_bytes(corpo.encode("utf-8"))
    return removidos, acrescentou


def main() -> int:
    for arg in sys.argv[1:]:
        caminho = Path(arg)
        if not caminho.is_file():
            print("nao existe: %s" % caminho)
            return 1
        removidos, acrescentou = limpar(caminho)
        print(
            "%s: %d linha(s) com espaco no fim | newline final: %s"
            % (caminho, removidos, "acrescentado" if acrescentou else "ja' havia")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
