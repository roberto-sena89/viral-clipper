"""The Windows launcher, and the failure it used to hide.

``web_viral_clipper.cmd`` does ``cd /d "%~dp0"`` and then runs
``.venv\\Scripts\\python.exe``. A virtualenv is *per checkout*, so in a second
copy of the repository (a git worktree, a clone) that path does not exist -- and
``call`` on a missing executable only prints an error. The window then falls
through to ``pause`` and the user sees a console that looks like it started.

The damage is not the missing start: it is what is still on the port. Any server
already listening on 7755 keeps answering with the files of *its* directory, so
the panel looks like "the old version", the new page 404s, and the code on disk
looks correct. This happened for real with the Ajustes page: the checkout served
1809 bytes of ``comum.js`` while the worktree on disk had 22779.

So the launcher fails loudly now, and these tests pin that.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "web_viral_clipper.cmd"

#: The interpreter path the launcher hands to ``call``.
VENV_PYTHON = r".venv\Scripts\python.exe"

#: The server it starts, relative to the repo root.
SERVER = r"web\server.py"


def launcher_bytes() -> bytes:
    return LAUNCHER.read_bytes()


def launcher_text() -> str:
    return launcher_bytes().decode("utf-8")


class LauncherTests(unittest.TestCase):
    def test_the_launcher_exists_at_the_repo_root(self) -> None:
        self.assertTrue(LAUNCHER.is_file(), f"{LAUNCHER} nao existe")

    def test_it_runs_from_its_own_directory(self) -> None:
        """``%~dp0`` is what makes the shortcut work from any cwd.

        Without it the launcher depends on how it was invoked, and a double
        click and a shell command would start servers pointed at different
        directories.
        """
        self.assertIn('cd /d "%~dp0"', launcher_text())

    def test_it_checks_for_the_venv_before_calling_it(self) -> None:
        """The regression this file exists for.

        A bare ``call ".venv\\Scripts\\python.exe" ...`` on a checkout with no
        venv exits 0 after printing an error, leaving any older server on the
        port answering. The launcher must test the path first.
        """
        text = launcher_text()
        guard = re.search(
            r'if not exist "([^"]+)"\s*\(', text, re.IGNORECASE
        )
        self.assertIsNotNone(
            guard,
            'o launcher nao testa o .venv antes de chamar: um checkout sem '
            'ambiente cai no pause com um servidor antigo ainda na porta',
        )
        self.assertEqual(
            guard.group(1),
            VENV_PYTHON,
            "o guard precisa testar exatamente o interpretador que o call usa, "
            "senao um caminho passa e o outro falha",
        )

    def test_the_guard_aborts_with_a_nonzero_code(self) -> None:
        """Nonzero so a caller (or a user's script) can tell it did not start."""
        text = launcher_text()
        self.assertRegex(text, r"exit /b [1-9]", "o guard precisa sair com erro")

    def test_the_guard_does_not_reach_the_call(self) -> None:
        """The guard has to run *before* the interpreter, not after.

        Checked by position rather than by parsing batch: ``exit /b`` inside the
        guard block is what stops the flow, and that is only true if the block
        precedes the ``call``.
        """
        text = launcher_text()
        guard_at = text.upper().find("IF NOT EXIST")
        call_at = text.upper().find("CALL ")
        self.assertGreater(guard_at, -1, "sem guard")
        self.assertGreater(call_at, -1, "sem call")
        self.assertLess(guard_at, call_at, "o guard esta depois do call")

    def test_the_guard_tells_the_user_what_to_do(self) -> None:
        """An error that only says "missing" costs the user the search we did.

        The message has to name the directory it looked in, the interpreter to
        build the environment with, and the manifest to install from.
        """
        text = launcher_text().lower()
        self.assertIn("%~dp0", text.split("if not exist")[1])
        self.assertRegex(
            text,
            r"-m venv \.venv",
            "o guard nao diz como criar o ambiente",
        )
        self.assertIn(
            "-m pip install -r requirements-dev.txt",
            text,
            "o guard nao diz de onde instalar as dependencias",
        )
        self.assertRegex(
            text,
            r"py -3\.13",
            "o guard precisa nomear um interpretador compativel (o projeto e' "
            "3.11+, e o ambiente verificado e' 3.13)",
        )

    def test_the_guard_names_the_optional_extras(self) -> None:
        """Requirements-dev alone is not the verified environment.

        ``curl_cffi`` is what makes Instagram work at all and
        ``opencv-python-headless<5`` is what ``--layout focus`` falls back
        silently without. A fresh venv built from the manifest alone loses both,
        and nothing in the suite says so.
        """
        text = launcher_text().lower()
        self.assertIn("curl_cffi", text)
        self.assertIn("opencv-python-headless", text)

    def test_the_guard_warns_about_the_stale_server(self) -> None:
        """The confusing half of the symptom, worth naming explicitly."""
        lowered = launcher_text().lower()
        self.assertIn("servidor", lowered)
        self.assertRegex(
            lowered,
            r"outra\s+copia|outro\s+checkout|ja tem um servidor|continua",
            "o guard nao explica que um servidor de outra copia segue no ar",
        )

    def test_it_still_starts_the_real_server(self) -> None:
        """The fix must not have replaced the launch with only a check."""
        self.assertIn(f'call "{VENV_PYTHON}" {SERVER}', launcher_text())

    def test_the_launcher_is_lf(self) -> None:
        """Repo convention: no ``.gitattributes``, ``core.autocrlf=false``.

        ``.cmd`` files run under CRLF too, but a text-mode write on Windows
        rewrites every line and buries the real change -- the same trap
        ``test_line_endings.py`` documents for the web assets.
        """
        data = launcher_bytes()
        crlf = data.count(b"\r\n")
        lf = data.count(b"\n") - crlf
        self.assertEqual(
            (crlf, lf),
            (0, data.count(b"\n")),
            "web_viral_clipper.cmd ficou CRLF ou misto; o repositorio o guarda "
            "em LF. Se foi Path.write_text() no Windows, reescreva com "
            "write_bytes().",
        )


if __name__ == "__main__":
    unittest.main()
