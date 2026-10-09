"""Conserta o README.md: tira o texto de conversa colado e os espacos no fim.

Dois defeitos, os dois de colagem:

1. Um trecho de resposta de assistente ("Fica em aberto, se voce quiser...")
   entrou no meio do arquivo, entre "Any OpenAI-compatible endpoint works" e
   "## How it works". Sao as linhas ~133-145, com linhas em branco entre cada
   fragmento porque a colagem trouxe os paragrafos do chat.

2. Praticamente toda linha termina com dois espacos. Em Markdown, dois espacos
   no fim significam quebra de linha FORCADA -- entao cada paragrafo do README
   esta pedindo quebra onde nao devia, e o `git diff` mostra espaco invisivel
   em toda alteracao de linha.

Uso:
    python tools/limpar_readme.py --ver    # mostra o que faria
    python tools/limpar_readme.py          # grava
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
README = RAIZ / "README.md"

#: O bloco colado comeca nesta frase e termina antes do proximo titulo. Os
#: fragmentos de codigo em crase vieram como paragrafos separados, por isso o
#: marcador e' o inicio e o fim, e nao uma lista de linhas.
INICIO = "Fica em aberto, se você quiser: a segunda metade da #10"
FIM = "## How it works"


def limpar(texto: str) -> tuple[str, dict[str, int]]:
    linhas = texto.split("\n")
    relatorio = {"bloco": 0, "espacos": 0, "linhas": len(linhas)}

    #: 1. Remove o bloco colado, de INICIO ate' a linha anterior a FIM.
    ini = fim = None
    for i, linha in enumerate(linhas):
        if ini is None and linha.strip().startswith(INICIO):
            ini = i
        elif ini is not None and linha.strip() == FIM:
            fim = i
            break
    if ini is not None and fim is not None:
        #: A linha em branco imediatamente antes de FIM tambem sai, para nao
        #: deixar dois brancos seguidos.
        corte_fim = fim
        while corte_fim > ini and not linhas[corte_fim - 1].strip():
            corte_fim -= 1
        relatorio["bloco"] = corte_fim - ini
        linhas = linhas[:ini] + linhas[fim:]

    #: 2. Tira o espaco em branco no fim de CADA linha. E' o que faz o par de
    #: espacos virar quebra forcada sem querer.
    saida = []
    for linha in linhas:
        limpa = linha.rstrip()
        #: Em Markdown, dois espacos no fim sao quebra de linha PROPOSITAL.
        #: Nao ha' nenhuma no arquivo que seja de proposito -- as que existem
        #: sao colagem. Se um dia houver, o autor escreve a quebra assim mesmo
        #: e este script precisa de uma lista de excecao; ate' la', tirar.
        if limpa != linha:
            relatorio["espacos"] += 1
        saida.append(limpa)

    return "\n".join(saida), relatorio


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", action="store_true", help="so' mostra, nao grava")
    args = ap.parse_args()

    bruto = README.read_bytes()
    texto = bruto.decode("utf-8")
    novo, rel = limpar(texto)

    print("linhas no arquivo : %d" % rel["linhas"])
    print("linhas do bloco   : %d" % rel["bloco"])
    print("linhas com espaco : %d" % rel["espacos"])
    print("bytes antes       : %d" % len(bruto))
    print("bytes depois      : %d" % len(novo.encode("utf-8")))

    if novo == texto:
        print("\n(nada a fazer)")
        return 0
    if args.ver:
        print("\n(--ver: nada gravado)")
        return 0
    #: `write_bytes` e nao `write_text`: no Windows o `write_text` converte LF
    #: em CRLF e o commit vira um diff de arquivo inteiro.
    README.write_bytes(novo.encode("utf-8"))
    print("\ngravado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
