"""Testes da trava por ``output_dir``.

O que estes testes protegem, em uma frase: dois processos apontando para o mesmo
``output_dir`` nao podem trabalhar ao mesmo tempo, porque escrevem os MESMOS
arquivos (o nome do clipe nao carrega id de execucao) e o resultado seria errado
sem erro nenhum.

Duas propriedades que so um teste **entre processos** consegue medir, e por isso
todo teste de recusa aqui sobe um processo de verdade:

1. **Recusa** — o segundo processo nao passa enquanto o primeiro esta vivo.
2. **Libera** — quando o primeiro morre, inclusive morto a forca, a trava cai.
   Esta e a razao de a trava ser do SO e nao um arquivo com pid: aqui o caminho
   normal de parada e ``taskkill /T /F``, e um lock por pid ficaria velho depois
   de CADA cancelamento, matando a execucao seguinte numa trava fantasma.

Ha ainda um teste de regressao para uma armadilha medida nesta maquina:
``msvcrt.locking`` trava a partir da posicao ATUAL do arquivo. Dois processos
travando offsets diferentes (5 e 10) receberam os **dois** a trava. Como o
detentor escreve o pid no arquivo, o arquivo fica maior entre uma abertura e
outra — ou seja, sem fixar o offset a recusa simplesmente nao acontece. E por
isso que a recusa e testada com o arquivo **ja contendo** o pid do detentor.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tests._fixtures import make_analysis, make_config
from viralclipper import cli, lock
from viralclipper.util import ClipperError, Logger

REPO_ROOT = Path(__file__).resolve().parent.parent
HOLDER = REPO_ROOT / "tests" / "_lock_holder.py"


def _child_env() -> dict[str, str]:
    """Ambiente do processo filho, com a raiz do repo no ``PYTHONPATH``.

    ``cwd=REPO_ROOT`` nao basta: rodando ``python tests/_lock_holder.py``, o
    Python poe no ``sys.path`` o diretorio **do script** (``tests/``), nao o
    diretorio de trabalho — e o filho morre com ``ModuleNotFoundError:
    viralclipper``. Medido; o teste falhou alto porque o helper mostra o stderr
    do filho em vez de so dizer "nao travou".
    """
    existing = os.environ.get("PYTHONPATH", "")
    joined = os.pathsep.join(filter(None, [str(REPO_ROOT), existing]))
    return {**os.environ, "PYTHONPATH": joined}


@contextlib.contextmanager
def holder(directory: Path, seconds: float = 30.0) -> Iterator[tuple[subprocess.Popen, int]]:
    """Sobe um processo que segura a trava, e garante que ele morra no fim.

    Produz ``(processo, pid)``. O ``pid`` e o que o **trabalhador** reporta de
    si mesmo, e nao o ``Popen.pid``: medido nesta maquina, ``Popen.pid`` 13788
    contra ``os.getpid()`` 19900 com ``ppid=13788`` — o ``python.exe`` do venv
    no Windows e um redirector, que sobe o interpretador de verdade como filho.
    Matar o stub derruba o trabalhador junto (verificado: os dois mortos e a
    trava livre), mas quem segura a trava e o filho.
    """
    process = subprocess.Popen(
        [sys.executable, str(HOLDER), str(directory), str(seconds)],
        cwd=str(REPO_ROOT),
        env=_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        line = process.stdout.readline() if process.stdout else ""
        if "TRAVADO" not in line:
            process.wait(timeout=20)
            detail = process.stderr.read() if process.stderr else ""
            raise AssertionError(
                f"o processo auxiliar nao travou: {line!r} / stderr: {detail!r}"
            )
        yield process, int(line.split()[1])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=20)


class LockTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vc-lock-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class AcquireTests(LockTestCase):
    """O caminho normal, num processo so."""

    def test_acquire_creates_the_output_dir(self):
        """A trava precisa de um lugar para existir; criar e responsabilidade dela."""
        target = self.tmp / "ainda-nao-existe"
        handle = lock.acquire(target)
        self.addCleanup(lock.release, handle)
        self.assertTrue(target.is_dir())
        self.assertTrue(lock.lock_path(target).exists())

    def test_release_frees_the_lock_for_the_next_acquire(self):
        """Sem isto, a segunda execucao morreria numa trava fantasma."""
        first = lock.acquire(self.tmp)
        lock.release(first)
        second = lock.acquire(self.tmp)
        self.addCleanup(lock.release, second)
        self.assertIsNotNone(second)

    def test_the_lock_file_survives_release(self):
        """Apagar o arquivo abriria a corrida de inodes: dois passariam.

        A trava do SO e por inode. Se A solta e apaga o nome enquanto B ja abriu
        o caminho, C cria um inode novo e trava — e B e C mandam ao mesmo tempo.
        Um arquivo parado em output/ e mais barato que essa corrida.
        """
        handle = lock.acquire(self.tmp)
        lock.release(handle)
        self.assertTrue(lock.lock_path(self.tmp).exists())

    def test_release_tolerates_none(self):
        lock.release(None)  # nao pode explodir

    def test_holder_pid_is_none_before_anyone_locks(self):
        self.assertIsNone(lock.holder_pid(self.tmp))

    def test_holder_pid_is_our_pid_while_we_hold_it(self):
        handle = lock.acquire(self.tmp)
        self.addCleanup(lock.release, handle)
        import os

        self.assertEqual(lock.holder_pid(self.tmp), os.getpid())


class CrossProcessTests(LockTestCase):
    """As duas propriedades que exigem dois processos de verdade."""

    def test_a_second_process_is_refused_while_the_first_holds(self):
        """A recusa, com o arquivo JA contendo o pid do detentor.

        O conteudo no arquivo e o ponto: e ele que desloca a posicao de abertura
        do segundo processo. Um lock que nao fixasse o offset travaria um byte
        diferente do primeiro e **passaria** — medido, offsets 5 e 10 ao mesmo
        tempo, os dois receberam a trava.
        """
        with holder(self.tmp):
            self.assertTrue(
                lock.lock_path(self.tmp).read_text(encoding="utf-8").strip(),
                "o detentor tem de ter escrito o pid: e ele que desloca a posicao",
            )
            self.assertIsNone(lock.acquire(self.tmp))

    def test_the_refusal_message_names_the_holder(self):
        """A mensagem tem de dizer QUEM segura e QUAL pasta — senao nao ajuda.

        O pid comparado e o que o trabalhador reporta de ``os.getpid()``, nao o
        que o proprio ``lock`` gravou. Comparar com o valor gravado seria
        conferir o codigo contra ele mesmo.
        """
        with holder(self.tmp) as (_process, pid):
            message = lock.busy_message(self.tmp)
            self.assertIn(str(pid), message, "tem de nomear quem esta segurando")
            self.assertIn(str(self.tmp), message, "tem de nomear a pasta ocupada")

    def test_the_lock_is_free_again_after_the_holder_exits(self):
        with holder(self.tmp, seconds=0.2):
            pass
        handle = lock.acquire(self.tmp)
        self.addCleanup(lock.release, handle)
        self.assertIsNotNone(handle)

    def test_killing_the_holder_releases_the_lock(self):
        """A propriedade que justifica trava do SO em vez de arquivo com pid.

        ``kill()`` no Windows e ``TerminateProcess`` — o mesmo que o
        ``taskkill /T /F`` que o Studio usa no botao Parar. Um lock por pid
        ficaria velho aqui e a execucao seguinte morreria numa trava fantasma.
        """
        with holder(self.tmp, seconds=30) as (process, _pid):
            self.assertIsNone(lock.acquire(self.tmp), "deveria estar ocupado")
            process.kill()
            process.wait(timeout=20)

        handle = lock.acquire(self.tmp)
        self.addCleanup(lock.release, handle)
        self.assertIsNotNone(handle, "a morte do detentor tem de soltar a trava")


class CliExitCodeTests(LockTestCase):
    """O contrato que o Studio e o lote consomem."""

    def test_a_busy_output_dir_returns_five(self):
        """A recusa chega como codigo 5, nao como excecao nem como exit 1.

        O ``run_single`` checa a trava ANTES de qualquer trabalho, entao o
        caminho de recusa nao mocka pipeline nenhum. Mas o ``analyse`` e
        trocado por uma explosao de proposito: sem isso, uma regressao na trava
        faz o teste **seguir para o pipeline de verdade** e tentar baixar da
        internet — foi o que aconteceu ao mutar o ``seek`` fora, e o log mostrou
        um ``yt-dlp`` rodando dentro de um teste de unidade. Um teste nao pode
        alcancar a rede, nem quando falha.
        """
        handle = lock.acquire(self.tmp)
        self.addCleanup(lock.release, handle)
        config = make_config(url="https://youtu.be/x", output_dir=self.tmp)

        with patch.object(
            cli.pipeline,
            "analyse",
            side_effect=AssertionError("a trava falhou: o run seguiu para o pipeline"),
        ):
            code, run_report, error = cli.run_single(config, Logger(quiet=True))

        self.assertEqual(code, 5)
        self.assertIsNone(run_report, "recusar nao pode devolver relatorio")
        self.assertIn("pid", error)

    def test_a_free_output_dir_is_not_refused(self):
        """O guarda nao pode ser largo demais: sem isto, nada rodaria.

        A trava e liberada no fim, entao uma segunda chamada sequencial precisa
        passar. Mockado no minimo: so o suficiente para o run nao tocar a rede.
        """
        config = make_config(
            url="https://youtu.be/x", output_dir=self.tmp, quiet=True, keep_temp=True
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    cli.pipeline,
                    "analyse",
                    return_value=({"id": "abc", "title": "T"}, make_analysis(), None, []),
                )
            )
            stack.enter_context(patch.object(cli.pipeline, "select_windows", return_value=[]))
            stack.enter_context(patch.object(cli.pipeline, "render_windows", return_value=[]))
            stack.enter_context(patch.object(cli.pipeline, "build_viral_report", return_value=[]))
            stack.enter_context(patch.object(cli.report, "write_json", return_value=Path("c.json")))
            stack.enter_context(
                patch.object(cli.report, "write_markdown", return_value=Path("c.md"))
            )
            stack.enter_context(patch.object(cli.util, "ensure_dir", return_value=self.tmp))
            code, _report, _error = cli.run_single(config, Logger(quiet=True))

        self.assertNotEqual(code, 5)

    def test_the_lock_is_released_even_when_the_run_raises(self):
        """Um run que estoura nao pode deixar a pasta travada para sempre.

        O ``hold`` libera no ``finally``; sem isso, um erro qualquer deixaria o
        usuario com um ``output/`` inutilizavel ate reiniciar a maquina.
        """
        config = make_config(url="https://youtu.be/x", output_dir=self.tmp, quiet=True)
        with patch.object(cli.pipeline, "analyse", side_effect=ClipperError("boom")):
            code, _report, _error = cli.run_single(config, Logger(quiet=True))

        self.assertEqual(code, 1)
        handle = lock.acquire(self.tmp)
        self.addCleanup(lock.release, handle)
        self.assertIsNotNone(handle, "a trava tem de ter sido liberada no finally")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
