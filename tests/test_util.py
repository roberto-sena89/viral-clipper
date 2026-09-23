"""Unit tests for :mod:`viralclipper.util`."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from viralclipper import util


class LimitNativeThreadsTests(unittest.TestCase):
    """The cap that keeps a render pool from dying of BLAS allocation failures."""

    def test_sets_every_blas_and_openmp_variable(self):
        with patch.dict(os.environ, {}, clear=True):
            util.limit_native_threads()
            for name in util._NATIVE_THREAD_VARS:
                self.assertEqual(os.environ[name], "1")

    def test_accepts_a_custom_thread_count(self):
        with patch.dict(os.environ, {}, clear=True):
            util.limit_native_threads(2)
            self.assertEqual(os.environ["OPENBLAS_NUM_THREADS"], "2")
            self.assertEqual(os.environ["MKL_NUM_THREADS"], "2")

    def test_a_value_the_user_already_set_is_respected(self):
        with patch.dict(os.environ, {"OPENBLAS_NUM_THREADS": "4"}, clear=True):
            util.limit_native_threads()
            self.assertEqual(os.environ["OPENBLAS_NUM_THREADS"], "4")
            self.assertEqual(os.environ["OMP_NUM_THREADS"], "1")

class ScrubProxyEnvTests(unittest.TestCase):
    """Ambient proxy settings must not reach yt-dlp.

    A host process (an IDE, a VPN shim) can export HTTP_PROXY for its own
    traffic. The child inherits it and the download then fails with a generic
    "Unable to extract data", because the proxy - not the site - answered.
    """

    def test_every_proxy_variable_is_dropped(self):
        env = {name: "http://127.0.0.1:65511" for name in util._PROXY_VARS}
        env["PATH"] = "/usr/bin"
        cleaned = util.scrub_proxy_env(env)
        for name in util._PROXY_VARS:
            self.assertNotIn(name, cleaned)
        self.assertEqual(cleaned["PATH"], "/usr/bin")

    def test_the_opt_out_keeps_them(self):
        env = {name: "http://p:1" for name in util._PROXY_VARS}
        env[util.KEEP_PROXY_ENV] = "1"
        cleaned = util.scrub_proxy_env(env)
        for name in util._PROXY_VARS:
            self.assertIn(name, cleaned)

    def test_the_opt_out_must_be_exactly_one(self):
        """An empty value must not be read as consent."""
        env = {util.KEEP_PROXY_ENV: "", "HTTPS_PROXY": "http://p:1"}
        self.assertNotIn("HTTPS_PROXY", util.scrub_proxy_env(env))

    def test_a_real_child_process_does_not_see_the_proxy(self):
        """End-to-end: the scrubbing has to survive the actual spawn."""
        script = "import os; print(os.environ.get('HTTPS_PROXY', 'ausente'))"
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:65511"}):
            proc = util.run([sys.executable, "-c", script])
        self.assertIn("ausente", proc.stdout)


class RunStreamingCapturedTests(unittest.TestCase):
    """Long commands must stream output and keep a copy for diagnostics."""

    def test_success_returns_code_and_output(self):
        code, output = util.run_streaming_captured(
            [sys.executable, "-c", "print('progresso ok')"], echo=False
        )
        self.assertEqual(code, 0)
        self.assertIn("progresso ok", output)

    def test_failure_still_returns_the_output(self):
        code, output = util.run_streaming_captured(
            [sys.executable, "-c", "import sys; print('ERROR: Video unavailable'); sys.exit(1)"],
            echo=False,
        )
        self.assertEqual(code, 1)
        self.assertIn("ERROR: Video unavailable", output)

    def test_echo_does_not_swallow_the_output(self):
        # echo=True writes through to stdout; the copy must be identical
        code, output = util.run_streaming_captured(
            [sys.executable, "-c", "print('barra de progresso')"], echo=True
        )
        self.assertEqual(code, 0)
        self.assertIn("barra de progresso", output)


class ConfigureStdioTests(unittest.TestCase):
    """The UTF-8 reconfiguration that keeps combining marks from killing runs."""

    def test_reconfigures_both_streams(self):
        fake_out, fake_err = MagicMock(), MagicMock()
        with patch.object(sys, "stdout", fake_out), patch.object(sys, "stderr", fake_err):
            util.configure_stdio()
        for fake in (fake_out, fake_err):
            fake.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_a_broken_stdout_never_aborts_the_pipeline(self):
        # A dead console on Windows raises OSError errno 22 on print/flush.
        with patch("builtins.print", side_effect=OSError(22, "Invalid argument")):
            util.Logger().info("ola")  # must not raise

    def test_streams_without_reconfigure_are_left_alone(self):
        class DumbStream:
            pass

        with patch.object(sys, "stdout", DumbStream()), patch.object(sys, "stderr", DumbStream()):
            util.configure_stdio()  # must not raise




