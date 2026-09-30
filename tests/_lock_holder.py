"""Segura a trava de um diretorio, para outro processo provar a recusa.

Executado como processo separado por ``tests/test_lock.py``. Imprime ``TRAVADO``
assim que obtem a trava — o teste espera essa linha para saber que a trava esta
mesmo na mao antes de tentar — e depois dorme, para o processo pai poder
dispara-lo e mata-lo.

Um teste de trava que roda tudo no mesmo processo nao testa nada: o que precisa
ser provado e a exclusao **entre processos**, que e o unico caso que acontece de
verdade (um ``python -m viralclipper`` no terminal contra um render do Studio).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from viralclipper import lock


def main() -> int:
    target = Path(sys.argv[1])
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

    handle = lock.acquire(target)
    if handle is None:
        # Ja havia alguem: o teste usa isto para montar o caso "ocupado".
        print("OCUPADO", flush=True)
        return 1

    # O pid e o NOSSO, de ``os.getpid()`` — nao o que o ``lock`` gravou. Se
    # fosse o do lock, o teste estaria conferindo o codigo contra ele mesmo:
    # "a mensagem contem o que eu escrevi" nao prova que o pid gravado e o de
    # quem de fato segura a trava.
    #
    # E ele NAO e o pid que o processo pai ve em ``Popen.pid``: medido nesta
    # maquina, ``Popen.pid`` 13788 contra ``os.getpid()`` 19900, com
    # ``ppid=13788``. O ``python.exe`` do venv no Windows e um redirector, que
    # sobe o interpretador de verdade como filho. Matar o stub derruba o
    # trabalhador junto (job object) — verificado: os dois mortos e a trava
    # livre —, mas o pid que interessa na mensagem e o do trabalhador.
    print(f"TRAVADO {os.getpid()}", flush=True)
    time.sleep(seconds)
    lock.release(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
