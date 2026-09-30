"""Unit tests for the CLI, focused on its exit codes.

The stage functions are patched out so the tests describe the contract a caller
depends on: which code comes back for which outcome.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tests._fixtures import make_analysis, make_config, make_transcript, make_word
from viralclipper import cli, report
from viralclipper.transcribe import Transcript
from viralclipper.util import Logger


class _TempOutputTestCase(unittest.TestCase):
    """Diretorio de saida **real**, em pasta temporaria.

    Antes era ``Path("out")`` com ``ensure_dir`` mockado. Isso parou de servir
    quando o ``run_single`` passou a travar o ``output_dir``: a trava precisa de
    um diretorio de verdade para existir, e criar ``out/`` na raiz do repositorio
    a cada execucao da suite seria lixo gratuito. Com um tmpdir os testes
    atravessam a trava de verdade — se ela quebrar o caminho normal, quebra aqui.

    Um tmpdir por CLASSE, com um subdiretorio por teste, e nao um por teste: o
    ``rmtree`` e a operacao mais cara da suite (medido: ~3,2 s por chamada nesta
    maquina, por causa do guarda de exclusao do sandbox), e 12 remocoes custavam
    mais que os proprios testes. O subdiretorio e criado pelo proprio
    ``lock.acquire`` — nada mais escreve nele, porque ``ensure_dir`` e todos os
    ``write_*`` do relatorio estao mockados.
    """

    _root: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._root = Path(tempfile.mkdtemp(prefix="vc-cli-"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self) -> None:
        self.tmp = self._root / self._testMethodName


def _clip(index: int, *, meets_minimum: bool) -> report.ClipRecord:
    return report.ClipRecord(
        index=index,
        start=0.0,
        end=10.0,
        duration=10.0,
        score=50.0,
        meets_minimum=meets_minimum,
        file="clip.mp4" if meets_minimum else "",
    )


#: Uma transcrição que existiu. O CLI não olha para o conteúdo, só para
#: ``is None`` — mas pedir um transcript de verdade mantém o teste honesto: se
#: alguém trocar o critério por "sem palavras", isto continua válido.
_GOOD = make_transcript([make_word(0.0, 1.0, "ola")])


class ExitCodeTests(_TempOutputTestCase):
    """Exit 3 means "a clip came out too short", and only that."""

    def run_single(
        self,
        *,
        clips,
        dry_run: bool = False,
        quiet: bool = False,
        transcript: Transcript | None = None,
    ) -> int:
        """Run ``cli.run_single`` with the pipeline stages patched out.

        ``transcript`` desce até o ``analyse`` falso porque o CLI agora olha para
        ele: com ``engine="hybrid"`` (o padrão de ``make_config``), um transcript
        ``None`` significa "a transcrição degradou" e o run sai com **4**. Os
        testes de exit 0/3 abaixo são sobre duração, não sobre degradação, então
        pedem o transcript bom — sem isto todos passariam a medir a coisa errada.
        """
        config = make_config(
            url="https://youtu.be/x",
            output_dir=self.tmp,
            dry_run=dry_run,
            quiet=quiet,
            keep_temp=True,
        )
        analysis = make_analysis(duration=120.0)
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    cli.pipeline,
                    "analyse",
                    return_value=(
                        {"id": "abc", "title": "Titulo"},
                        analysis,
                        transcript,
                        [],
                    ),
                )
            )
            stack.enter_context(patch.object(cli.pipeline, "select_windows", return_value=[]))
            stack.enter_context(
                patch.object(cli.pipeline, "render_windows", return_value=clips)
            )
            stack.enter_context(
                patch.object(cli.report, "write_json", return_value=Path("c.json"))
            )
            stack.enter_context(
                patch.object(cli.report, "write_markdown", return_value=Path("c.md"))
            )
            stack.enter_context(patch.object(cli.util, "ensure_dir", return_value=self.tmp))
            code, _run_report, _error = cli.run_single(config, Logger(quiet=True))
        return code

    def test_all_clips_long_enough_returns_zero(self):
        self.assertEqual(
            self.run_single(clips=[_clip(1, meets_minimum=True)], transcript=_GOOD), 0
        )

    def test_a_short_clip_returns_three(self):
        self.assertEqual(
            self.run_single(
                clips=[_clip(1, meets_minimum=True), _clip(2, meets_minimum=False)],
                transcript=_GOOD,
            ),
            3,
        )

    def test_the_exit_code_does_not_depend_on_quiet(self):
        """A caller in a pipeline cannot have its exit code changed by -q."""
        clips = [_clip(1, meets_minimum=False)]
        self.assertEqual(self.run_single(clips=clips, quiet=False, transcript=_GOOD), 3)
        self.assertEqual(self.run_single(clips=clips, quiet=True, transcript=_GOOD), 3)

    def test_plan_only_does_not_report_a_short_clip(self):
        """Nothing was rendered, so there is no duration to judge."""
        self.assertEqual(
            self.run_single(
                clips=[_clip(1, meets_minimum=False)], dry_run=True, transcript=_GOOD
            ),
            0,
        )

    def test_no_clips_returns_zero(self):
        self.assertEqual(self.run_single(clips=[], transcript=_GOOD), 0)


class DegradedTranscriptionTests(_TempOutputTestCase):
    """Exit 4 e a transcrição que degradou — um sucesso que não é sucesso.

    No engine ``hybrid``, quando a transcrição falha o run continua: os clipes
    saem sem legenda queimada e a seleção cai para energia de áudio. Medido, com
    o mesmo vídeo e os mesmos parâmetros: 39,3/39,3 sem transcrição contra
    68,3/68,1 com ela. Antes isto terminava com **exit 0**, indistinguível de um
    run bom, e um lote não tinha como retentar a falha transitória (memória).
    """

    def run_single(
        self, *, dry_run: bool, transcript: Transcript | None, engine: str = "hybrid"
    ) -> int:
        config = make_config(
            url="https://youtu.be/x",
            output_dir=self.tmp,
            dry_run=dry_run,
            quiet=True,
            keep_temp=True,
            engine=engine,
        )
        analysis = make_analysis(duration=120.0)
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    cli.pipeline,
                    "analyse",
                    return_value=(
                        {"id": "abc", "title": "Titulo"},
                        analysis,
                        transcript,
                        [],
                    ),
                )
            )
            stack.enter_context(patch.object(cli.pipeline, "select_windows", return_value=[]))
            stack.enter_context(
                patch.object(
                    cli.pipeline,
                    "render_windows",
                    return_value=[_clip(1, meets_minimum=True)],
                )
            )
            stack.enter_context(
                patch.object(cli.pipeline, "build_viral_report", return_value=[])
            )
            stack.enter_context(patch.object(cli.report, "write_json", return_value=Path("c.json")))
            stack.enter_context(
                patch.object(cli.report, "write_markdown", return_value=Path("c.md"))
            )
            stack.enter_context(patch.object(cli.util, "ensure_dir", return_value=self.tmp))
            code, _run_report, _error = cli.run_single(config, Logger(quiet=True))
        return code

    def test_a_degraded_transcription_returns_four(self):
        """Clipes renderizados, mas sem legenda: o lote precisa saber."""
        self.assertEqual(self.run_single(dry_run=False, transcript=None), 4)

    def test_a_plan_does_not_return_four(self):
        """Um plano não renderizou nada, então não há legenda para perder.

        Se o guarda fosse só ``transcript is None``, todo ``--plan-only`` feito
        sem transcrição sairia com 4 e o código perderia o significado.
        """
        self.assertEqual(self.run_single(dry_run=True, transcript=None), 0)

    def test_a_good_transcription_returns_zero(self):
        """O guarda não pode ser largo demais: sem isto, todo run sairia com 4."""
        self.assertEqual(self.run_single(dry_run=False, transcript=_GOOD), 0)

    def test_audio_engine_does_not_return_four(self):
        """`--engine audio` não transcreve de propósito — não degradou nada.

        Se o guarda fosse ``engine != "transcript"`` em vez de ``== "hybrid"``,
        todo run de áudio sairia com 4, e o código perderia o significado.
        """
        self.assertEqual(
            self.run_single(dry_run=False, transcript=None, engine="audio"), 0
        )

    def test_degraded_wins_over_a_short_clip(self):
        """Os dois podem ser verdade juntos; "degradado" é mais acionável.

        A duração curta é consequência da seleção degradada, não um problema
        independente — mandar retentar por ela esconderia a causa.
        """
        config = make_config(
            url="https://youtu.be/x",
            output_dir=self.tmp,
            quiet=True,
            keep_temp=True,
        )
        analysis = make_analysis(duration=120.0)
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    cli.pipeline,
                    "analyse",
                    return_value=({"id": "abc", "title": "Titulo"}, analysis, None, []),
                )
            )
            stack.enter_context(patch.object(cli.pipeline, "select_windows", return_value=[]))
            stack.enter_context(
                patch.object(
                    cli.pipeline,
                    "render_windows",
                    return_value=[_clip(1, meets_minimum=False)],
                )
            )
            stack.enter_context(patch.object(cli.report, "write_json", return_value=Path("c.json")))
            stack.enter_context(
                patch.object(cli.report, "write_markdown", return_value=Path("c.md"))
            )
            stack.enter_context(patch.object(cli.util, "ensure_dir", return_value=self.tmp))
            code, _run_report, _error = cli.run_single(config, Logger(quiet=True))
        self.assertEqual(code, 4)


class PlanOnlyManifestTests(_TempOutputTestCase):
    """``--plan-only`` nao pode apagar o manifesto do ultimo run de verdade.

    Um plano nao renderiza nada, entao todo ``record`` sai com ``file`` vazio.
    Gravar isso por cima de ``clips.json`` deixa os ``.mp4`` no disco, orfaos,
    sem nada que os indexe. Medido: depois de um ``--plan-only``, ``output/``
    tinha 3 clipes e ``clips.json`` listava 2 com ``file: ""``.
    """

    def run_single(self, *, dry_run: bool):
        config = make_config(
            url="https://youtu.be/x",
            output_dir=self.tmp,
            dry_run=dry_run,
            quiet=True,
            keep_temp=True,
        )
        analysis = make_analysis(duration=120.0)
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    cli.pipeline,
                    "analyse",
                    return_value=({"id": "abc", "title": "Titulo"}, analysis, None, []),
                )
            )
            stack.enter_context(patch.object(cli.pipeline, "select_windows", return_value=[]))
            stack.enter_context(
                patch.object(
                    cli.pipeline,
                    "render_windows",
                    return_value=[_clip(1, meets_minimum=True)],
                )
            )
            stack.enter_context(
                patch.object(cli.pipeline, "build_viral_report", return_value=[])
            )
            stack.enter_context(patch.object(cli.util, "ensure_dir", return_value=self.tmp))
            write_json = stack.enter_context(patch.object(cli.report, "write_json"))
            write_markdown = stack.enter_context(patch.object(cli.report, "write_markdown"))
            cli.run_single(config, Logger(quiet=True))
        return write_json, write_markdown

    def test_a_plan_does_not_write_the_clip_manifest(self):
        write_json, write_markdown = self.run_single(dry_run=True)
        self.assertFalse(write_json.called)
        self.assertFalse(write_markdown.called)

    def test_a_real_run_still_writes_the_clip_manifest(self):
        """O guarda nao pode ser largo demais: sem isto, nada seria gravado."""
        write_json, write_markdown = self.run_single(dry_run=False)
        self.assertTrue(write_json.called)
        self.assertTrue(write_markdown.called)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
