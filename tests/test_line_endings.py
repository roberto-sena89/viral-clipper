"""Line-ending invariants for the web panel.

On Windows, a text-mode write turns ``\n`` into ``\r\n``. One
``Path.write_text()`` inside an edit script is therefore enough to rewrite every
line of a file: a twenty-line change lands as a whole-file diff and review
becomes impossible. Nothing breaks when that happens -- the HTML still renders
and the JS still parses -- so only a test can catch it.

The convention pinned here is the one the repository already had before the
panel was split into pages: everything the browser parses under ``web/`` is LF,
and the Python server is CRLF. The trailing newline is deliberately *not*
asserted, because it varies per file in ``HEAD`` -- ``index.html`` and
``scrap.js`` have none, ``index.js`` does -- so a missing one is the
repository's state, not a regression, and adding one produces a spurious diff
line.

Measured on the master checkout (LF unless noted): ``web/*.html|js|css`` LF,
``web/server.py`` CRLF. Without a trailing newline: ``index.html``,
``scrap.js``, ``tests/test_web_server.py``. A quick proof that no line changed
only by separator: ``git diff --numstat`` must equal
``git diff --ignore-cr-at-eol --numstat``.

The suite's own Python files are covered too. They were not, and that was the
gap: an edit script rewrote ``tests/test_web_server.py`` with
``Path.write_text()`` on Windows, turning all 7,974 lines CRLF. The
``web/``-only tests stayed green -- they never look at ``tests/`` -- and the
damage surfaced as a 15,677-line diff on a 333-line change. The review cost is
the whole point of this module, so the guard now reaches the file that got hit.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from web import server

WEB_DIR = server.WEB_DIR
TESTS_DIR = Path(__file__).resolve().parent
ROOT = WEB_DIR.parent
BROWSER_SUFFIXES = {".html", ".js", ".css"}
SERVER = WEB_DIR / "server.py"


def separator(data: bytes) -> str:
    """Name the line separator a file uses, counted in bytes.

    Audit with Python, never with ``grep -c $'\\r'``: in Git Bash (MSYS) the
    escape is mangled before grep sees it, and it reports CRLF on files that are
    pure LF.
    """
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf and lf:
        return "MIXED"
    if crlf:
        return "CRLF"
    if lf:
        return "LF"
    return "EMPTY"


def browser_assets() -> list[Path]:
    """Every file under ``web/`` that the browser parses."""
    return sorted(
        path
        for path in WEB_DIR.rglob("*")
        if path.is_file() and path.suffix in BROWSER_SUFFIXES
    )


def suite_files() -> list[Path]:
    """Every Python file of the suite itself.

    E' o conjunto que faltava: o arquivo que sofreu a conversao foi
    ``tests/test_web_server.py``, e nenhum teste olhava para ``tests/``.
    """
    return sorted(path for path in TESTS_DIR.glob("*.py") if path.is_file())


def fix_hint(path: Path, found: str) -> str:
    return (
        f"{path.relative_to(WEB_DIR.parent)} esta {found}, esperado LF. "
        "Os assets do navegador sao LF neste repositorio. Se o arquivo foi "
        "gravado com Path.write_text() no Windows, o \\n virou \\r\\n: reescreva "
        "com Path.write_bytes() e rode de novo."
    )


class WebLineEndingTests(unittest.TestCase):
    def test_every_browser_asset_is_lf(self) -> None:
        assets = browser_assets()
        self.assertTrue(assets, f"nenhum asset de navegador encontrado em {WEB_DIR}")
        for path in assets:
            data = path.read_bytes()
            if not data:
                continue  # arquivo vazio nao tem separador
            with self.subTest(asset=path.name):
                found = separator(data)
                self.assertEqual(found, "LF", fix_hint(path, found))

    def test_no_test_file_mixes_separators(self) -> None:
        """Os testes nao podem ficar com separadores MISTURADOS.

        Nao se exige LF aqui: cinco arquivos de teste ja' sao CRLF no
        repositorio (``test_archive``, ``test_pipeline``, ``test_render``,
        ``test_transcript_import``, ``test_viral_report``), e normaliza-los
        so' para satisfazer um teste produziria o diff gigante que este modulo
        existe para evitar. O que se exige e' COERENCIA: um arquivo LF ou um
        arquivo CRLF, nunca os dois.

        Existe por um caso real: um script de edicao regravou
        ``tests/test_web_server.py`` com ``Path.write_text()`` no Windows e as
        7.974 linhas viraram CRLF -- um diff de 15.677 linhas numa mudanca de
        333. Um arquivo MISTO (parte CRLF, parte LF) e' o sinal mais claro de
        que uma escrita parcial aconteceu no meio.
        """
        arquivos = suite_files()
        self.assertTrue(arquivos, f"nenhum teste encontrado em {TESTS_DIR}")
        for path in arquivos:
            data = path.read_bytes()
            if not data:
                continue
            with self.subTest(teste=path.name):
                found = separator(data)
                self.assertNotEqual(
                    found, "MIXED",
                    f"tests/{path.name} tem separadores misturados (CRLF e LF "
                    "no mesmo arquivo). Uma escrita parcial no Windows faz "
                    "isso: reescreva com Path.write_bytes() e rode de novo.")

    def test_the_files_edited_by_the_common_chrome_work_are_lf(self) -> None:
        """CONGELA o separador dos arquivos que esta migracao tocou.

        Estes estavam LF em HEAD e tem de continuar LF -- inclusive
        ``tests/test_web_server.py``, que foi convertido para CRLF por um
        ``Path.write_text()`` no meio do trabalho e so' apareceu como um diff
        de 15.677 linhas. Uma regra geral ("todo teste e' LF") nao serve porque
        cinco arquivos do repositorio ja' sao CRLF; o que faz sentido e' fixar
        o estado dos que tem de permanecer LF, um a um.
        """
        alvos = {
            "tests/test_web_server.py": "LF",
            "tests/test_line_endings.py": "LF",
            "tests/_dom_bridge.py": "LF",
            "tests/_dom.js": "LF",
        }
        for relativo, esperado in sorted(alvos.items()):
            path = ROOT / relativo
            with self.subTest(arquivo=relativo):
                self.assertTrue(path.is_file(), f"{relativo} nao existe")
                found = separator(path.read_bytes())
                self.assertEqual(
                    found, esperado,
                    f"{relativo} esta {found}, esperado {esperado}. Se o "
                    "arquivo foi gravado com Path.write_text() no Windows, o "
                    "\\n virou \\r\\n: reescreva com Path.write_bytes() e rode "
                    "de novo. Um diff de arquivo inteiro esconde a mudanca "
                    "real de quem revisa.")

    def test_no_browser_asset_mixes_separators(self) -> None:
        for path in browser_assets():
            with self.subTest(asset=path.name):
                self.assertNotEqual(separator(path.read_bytes()), "MIXED", str(path))

    def test_the_server_keeps_its_crlf(self) -> None:
        found = separator(SERVER.read_bytes())
        self.assertEqual(
            found,
            "CRLF",
            f"web/server.py esta {found}, esperado CRLF -- e' assim que o "
            "repositorio o guarda. Nao normalize este arquivo para LF.",
        )

    def test_the_pages_added_by_the_panel_split_are_lf(self) -> None:
        """The two files created when Ajustes was carved out of index.html.

        Both were first written CRLF by the edit script; this pins the repair so
        the same mistake on a future page fails here instead of in review.
        """
        for name in ("ajustes.html", "ajustes.js"):
            path = WEB_DIR / name
            with self.subTest(page=name):
                self.assertTrue(path.is_file(), f"{name} nao existe em {WEB_DIR}")
                found = separator(path.read_bytes())
                self.assertEqual(found, "LF", fix_hint(path, found))


if __name__ == "__main__":
    unittest.main()
