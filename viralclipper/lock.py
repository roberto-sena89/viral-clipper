"""Trava exclusiva por diretorio de saida.

Dois processos do mesmo pipeline apontando para o mesmo ``output_dir`` se
atropelam **em silencio**, por dois motivos que se somam:

- O nome do clipe publicado e deterministico (``render_task.py``:
  ``{video_id}_{position:02d}_{stem}{variant}_{start:06d}.mp4``) — nao carrega id
  de execucao. Duas execucoes do mesmo video com os mesmos parametros escrevem os
  **mesmos** ``clips.json``, ``clips.md``, ``viral_report.md`` e cada ``.mp4``.
- ``work_path()`` e ``output_dir/_work`` para qualquer execucao, sem id. Os dois
  processos escrevem os mesmos ``source_audio.webm`` e ``analysis.wav``.

Medido, ao disparar dois planos em paralelo: um run analisou 1127 s de um video
de 793,5 s, porque reaproveitou o audio que o outro acabara de escrever. Passou
por todos os guardas seguintes. E como a publicacao e um rename atomico, nem
arquivo truncado sobra para denunciar a colisao — so o resultado errado.

**Por que trava do sistema operacional, e nao um arquivo com o pid.** O caminho
NORMAL de parada aqui e matar o processo a forca (``taskkill /T /F``, para nao
deixar ffmpeg orfao). Um lock por pid ficaria velho depois de **cada**
cancelamento, e a execucao seguinte morreria numa trava fantasma — pior que nao
ter trava. A trava do SO se libera quando o processo morre, inclusive em kill.

**O arquivo de trava nunca e apagado.** Apagar abre uma corrida classica: A fecha
o descritor (soltando a trava) e apaga o nome; nesse intervalo B abre o caminho,
cria e trava um inode; A apaga o nome; C cria um inode novo e trava — e agora B e
C seguram travas de inodes **diferentes**, ambos achando que mandam. Um arquivo
parado em ``output/`` (que ja e gitignored) e mais barato que essa corrida.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import IO

LOCK_FILENAME = ".viralclipper.lock"

# A trava e uma FAIXA DE BYTES, e o offset tem de ser o MESMO em todo processo.
# `msvcrt.locking` trava a partir da posicao ATUAL do arquivo: sem fixar o
# offset, cada processo trava um byte diferente e **todos passam**. Medido nesta
# maquina: dois processos travando os offsets 5 e 10 ao mesmo tempo, os dois
# receberam a trava. Seria uma trava que nao trava — o pior desfecho possivel,
# porque da a sensacao de protecao sem nenhuma.
#
# E o offset e ALTO de proposito. Trava de faixa no Windows bloqueia tambem a
# LEITURA da faixa por outro processo: travar o byte 0 impediria quem falhou de
# ler o pid e dizer quem esta segurando. Medido: com o offset 4096 travado, a
# leitura dos bytes iniciais continua funcionando.
_LOCK_OFFSET = 4096


def lock_path(output_dir: Path) -> Path:
    """Caminho do arquivo de trava dentro de ``output_dir``."""
    return Path(output_dir) / LOCK_FILENAME


def acquire(output_dir: Path) -> IO[str] | None:
    """Tenta travar ``output_dir``.

    Devolve o descritor — mantenha-o vivo, fechar solta a trava — ou ``None``
    quando outra execucao esta viva.

    Nunca espera. Recusar e melhor que esperar: quem chamou sabe o que fazer com
    a mensagem, enquanto um ``sleep`` ate liberar vira um travamento sem
    explicacao. O diretorio e criado aqui porque a trava precisa de um lugar
    para existir.
    """
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    handle = lock_path(directory).open("a+", encoding="utf-8")
    try:
        handle.seek(_LOCK_OFFSET)
        _lock(handle)
    except OSError:
        handle.close()
        return None
    _write_pid(handle)
    return handle


def release(handle: IO[str] | None) -> None:
    """Solta a trava. Tolerante a ``None`` para simplificar o chamador."""
    if handle is None:
        return
    with contextlib.suppress(OSError):
        handle.close()


def holder_pid(output_dir: Path) -> int | None:
    """Pid de quem segura a trava, ou ``None`` se nao der para saber.

    E uma **pista**, nao a verdade: a verdade e a trava do SO. Pode ficar velho
    na janela entre travar e escrever o pid, e e por isso que a mensagem de
    ocupado diz "provavelmente".
    """
    try:
        text = lock_path(output_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def busy_message(output_dir: Path) -> str:
    """O que dizer quando a trava esta ocupada — com o custo, nao so o fato."""
    pid = holder_pid(output_dir)
    who = f" (provavelmente pid {pid})" if pid else ""
    return (
        f"Outra execucao ja esta usando {output_dir}{who}. Duas execucoes no "
        "mesmo diretorio de saida escrevem os MESMOS arquivos — o nome dos "
        "clipes nao carrega id de execucao, e o diretorio de trabalho "
        "(output/_work) tambem e compartilhado — entao a ultima a terminar "
        "sobrescreve a outra sem erro nenhum. Espere essa terminar (ou pare-a) "
        "e rode de novo, ou use -o/--output para outra pasta."
    )


@contextlib.contextmanager
def hold(output_dir: Path) -> Iterator[str | None]:
    """Segura a trava de ``output_dir`` durante o bloco.

    Produz ``None`` quando conseguiu, ou a mensagem de ocupado quando nao — o
    chamador decide o codigo de saida, porque so ele sabe o que devolver.
    """
    handle = acquire(output_dir)
    if handle is None:
        yield busy_message(output_dir)
        return
    try:
        yield None
    finally:
        release(handle)


def _lock(handle: IO[str]) -> None:
    """Trava exclusiva e nao bloqueante no descritor. Levanta ``OSError`` se ocupado."""
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _write_pid(handle: IO[str]) -> None:
    """Grava o pid no inicio do arquivo, so como pista para a mensagem.

    Escrito **depois** de obter a trava, para nao sobrescrever o pid de quem
    esta segurando. O modo ``a+`` so escreve no fim do arquivo, entao truncar
    antes e o que faz o pid novo cair no comeco.
    """
    with contextlib.suppress(OSError):
        handle.seek(0)
        handle.truncate(0)
        handle.write(f"{os.getpid()}\n")
        handle.flush()
