"""Unit tests for :mod:`viralclipper.ranker`.

The provider is injected, so nothing here touches the network. The tests cover
the three things that make the ranker safe to enable in a pipeline: parsing a
messy model response, blending without losing the absolute scale, and failing
softly when the model or the transport misbehaves.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from viralclipper import providers, ranker
from viralclipper.config import ClipConfig
from viralclipper.score import Window
from viralclipper.util import ClipperError, Logger

FULL_JSON = (
    '{"autocontido": 8, "gancho": 9, "payoff": 7, '
    '"compartilhavel": 8, "final_completo": 6, "motivo": "historia completa"}'
)


class FakeProvider:
    """Provider whose response (or failure) is dictated by the test."""

    name = "fake"

    def __init__(self, response: str = FULL_JSON, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = 0
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        self.prompts.append((system, user))
        if self.error is not None:
            raise self.error
        return self.response


def _window(text: str, score: float, start: float = 0.0) -> Window:
    return Window(
        start=start,
        end=start + 30.0,
        unit_start=0,
        unit_end=1,
        text=text,
        score=score,
        components={"hook_start": 0.5},
    )


class RankerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ranker-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = Logger(quiet=True)

    def config(self, **overrides) -> ClipConfig:
        payload = {
            "url": "https://youtu.be/fixture",
            "ranker": "llm",
            "cache_dir": self.tmp,
            "ranker_requires_key": False,
        }
        payload.update(overrides)
        return ClipConfig(**payload)


class ParseVerdictTests(unittest.TestCase):
    def test_plain_json(self):
        verdict = ranker.parse_verdict(FULL_JSON)
        self.assertEqual(verdict.scores["gancho"], 9.0)
        self.assertEqual(verdict.reason, "historia completa")

    def test_fenced_json(self):
        verdict = ranker.parse_verdict(f"```json\n{FULL_JSON}\n```")
        self.assertEqual(verdict.scores["payoff"], 7.0)

    def test_json_inside_prose(self):
        verdict = ranker.parse_verdict(f"Claro! Aqui esta:\n{FULL_JSON}\nEspero ter ajudado.")
        self.assertEqual(verdict.scores["autocontido"], 8.0)

    def test_values_are_clamped_to_the_scale(self):
        verdict = ranker.parse_verdict('{"gancho": 99, "payoff": -4}')
        self.assertEqual(verdict.scores["gancho"], 10.0)
        self.assertEqual(verdict.scores["payoff"], 0.0)

    def test_unknown_keys_are_ignored(self):
        verdict = ranker.parse_verdict('{"gancho": 5, "carisma": 10}')
        self.assertEqual(set(verdict.scores), {"gancho"})

    def test_non_numeric_values_are_skipped(self):
        verdict = ranker.parse_verdict('{"gancho": "alto", "payoff": 6}')
        self.assertEqual(set(verdict.scores), {"payoff"})

    def test_overall_is_the_mean(self):
        verdict = ranker.parse_verdict(FULL_JSON)
        self.assertAlmostEqual(verdict.overall, (8 + 9 + 7 + 8 + 6) / 5, places=6)

    def test_missing_every_dimension_raises(self):
        with self.assertRaises(ClipperError):
            ranker.parse_verdict('{"motivo": "nada"}')

    def test_no_json_at_all_raises(self):
        with self.assertRaises(ClipperError):
            ranker.parse_verdict("desculpe, nao posso ajudar")

    def test_unbalanced_json_raises(self):
        with self.assertRaises(ClipperError):
            ranker.parse_verdict('{"gancho": 5, ')

    def test_every_declared_dimension_is_parsed(self):
        document = {dimension: 5 for dimension in ranker.DIMENSIONS}
        verdict = ranker.parse_verdict(json.dumps(document))
        self.assertEqual(set(verdict.scores), set(ranker.DIMENSIONS))


class CacheKeyTests(unittest.TestCase):
    def test_same_window_and_model_agree(self):
        window = _window("texto", 50.0)
        self.assertEqual(
            ranker.cache_key(window, "m"), ranker.cache_key(_window("texto", 50.0), "m")
        )

    def test_text_changes_the_key(self):
        self.assertNotEqual(
            ranker.cache_key(_window("a", 50.0), "m"),
            ranker.cache_key(_window("b", 50.0), "m"),
        )

    def test_model_changes_the_key(self):
        window = _window("a", 50.0)
        self.assertNotEqual(ranker.cache_key(window, "m1"), ranker.cache_key(window, "m2"))

    def test_heuristic_score_does_not_change_the_key(self):
        """The verdict is about the text, not about the heuristic's opinion."""
        self.assertEqual(
            ranker.cache_key(_window("a", 10.0), "m"),
            ranker.cache_key(_window("a", 90.0), "m"),
        )


class BuildProviderTests(unittest.TestCase):
    def test_disabled_returns_none(self):
        self.assertIsNone(ranker.build_provider(self._config(ranker="none")))

    def _config(self, **overrides):
        payload = {"url": "u", "ranker_requires_key": False}
        payload.update(overrides)
        return ClipConfig(**payload)

    def test_unknown_ranker_raises(self):
        with self.assertRaises(ClipperError):
            ranker.build_provider(self._config(ranker="magic"))

    def test_missing_key_raises_when_required(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ClipperError):
                ranker.build_provider(
                    self._config(ranker="llm", ranker_requires_key=True)
                )

    def test_key_from_environment_is_accepted(self):
        with patch.dict("os.environ", {"MY_KEY": "secret"}):
            provider = ranker.build_provider(
                self._config(
                    ranker="llm", ranker_requires_key=True, ranker_api_key_env="MY_KEY"
                )
            )
        self.assertEqual(provider.api_key, "secret")

    def test_local_endpoint_without_key(self):
        with patch.dict("os.environ", {}, clear=True):
            provider = ranker.build_provider(self._config(ranker="llm"))
        self.assertIsNone(provider.api_key)


class UserAgentTests(unittest.TestCase):
    """O cliente do run e o da sonda tem de mandar a MESMA identidade.

    O 403 que o usuario viu nao era a chave: o urllib, sem User-Agent explicito,
    vai como ``Python-urllib/3.x`` e o Cloudflare na frente do endpoint recusa o
    cliente antes de olhar a chave ("error code: 1010"). Se a sonda mandasse um
    User-Agent e o run nao, o teste passaria verde e a execucao daria 403 -- por
    isso a constante e compartilhada, e nao dois literais parecidos.
    """

    def test_the_run_client_sends_a_real_user_agent(self):
        provider = ranker.HttpChatProvider(
            base_url="https://x.test/v1", model="m", api_key="k"
        )
        headers = provider._headers()
        self.assertEqual(headers["User-Agent"], providers.LLM_USER_AGENT)
        self.assertNotIn("Python-urllib", headers["User-Agent"])

    def test_the_user_agent_does_not_replace_the_authorization(self):
        """O ganho nao pode custar o header que autentica."""
        provider = ranker.HttpChatProvider(
            base_url="https://x.test/v1", model="m", api_key="segredo"
        )
        headers = provider._headers()
        self.assertEqual(headers["Authorization"], "Bearer segredo")

    def test_both_clients_read_the_same_constant(self):
        """A sonda importa ``LLM_USER_AGENT``; o run tambem. Um so valor."""
        from viralclipper import provider_probe

        self.assertIs(
            provider_probe.LLM_USER_AGENT, providers.LLM_USER_AGENT
        )


class ApplyTests(RankerTestCase):
    def test_disabled_ranker_is_a_no_op(self):
        provider = FakeProvider()
        windows = [_window("a", 50.0), _window("b", 40.0)]
        judged = ranker.apply(
            windows, self.config(ranker="none"), self.log, provider=provider
        )
        self.assertEqual(judged, 0)
        self.assertEqual(provider.calls, 0)
        self.assertEqual([w.score for w in windows], [50.0, 40.0])

    def test_every_candidate_is_judged_and_reweighted(self):
        provider = FakeProvider()
        windows = [_window("a", 50.0), _window("b", 40.0)]
        judged = ranker.apply(windows, self.config(), self.log, provider=provider)
        self.assertEqual(judged, 2)
        self.assertEqual(provider.calls, 2)
        expected = 50.0 * 0.4 + 7.6 * 10.0 * 0.6
        self.assertAlmostEqual(windows[0].score, round(expected, 2), places=2)

    def test_heuristic_score_is_preserved_in_the_components(self):
        windows = [_window("a", 50.0)]
        ranker.apply(windows, self.config(), self.log, provider=FakeProvider())
        self.assertEqual(windows[0].components["heuristic_score"], 50.0)

    def test_model_dimensions_land_in_the_components(self):
        windows = [_window("a", 50.0)]
        ranker.apply(windows, self.config(), self.log, provider=FakeProvider())
        self.assertIn("llm_overall", windows[0].components)
        self.assertIn("llm_gancho", windows[0].components)
        self.assertIn("llm_motivo", windows[0].components)

    def test_weight_zero_keeps_the_heuristic_score(self):
        windows = [_window("a", 50.0)]
        ranker.apply(
            windows, self.config(ranker_weight=0.0), self.log, provider=FakeProvider()
        )
        self.assertAlmostEqual(windows[0].score, 50.0, places=2)

    def test_weight_one_uses_only_the_model(self):
        windows = [_window("a", 5.0)]
        ranker.apply(
            windows, self.config(ranker_weight=1.0), self.log, provider=FakeProvider()
        )
        self.assertAlmostEqual(windows[0].score, 76.0, places=2)

    def test_only_the_top_n_candidates_are_sent(self):
        provider = FakeProvider()
        windows = [_window(f"w{index}", float(index)) for index in range(10)]
        judged = ranker.apply(
            windows, self.config(ranker_top_n=3), self.log, provider=provider
        )
        self.assertEqual(judged, 3)
        self.assertEqual(provider.calls, 3)
        # The three highest scoring windows were the ones judged.
        judged_texts = {window.text for window in windows if "llm_overall" in window.components}
        self.assertEqual(judged_texts, {"w9", "w8", "w7"})

    def test_unjudged_candidates_are_pushed_below_every_judged_one(self):
        windows = [_window(f"w{index}", float(index)) for index in range(10)]
        ranker.apply(windows, self.config(ranker_top_n=3), self.log, provider=FakeProvider())
        unjudged = [window for window in windows if "llm_overall" not in window.components]
        self.assertEqual(len(unjudged), 7)
        for window in unjudged:
            self.assertEqual(window.score, -1.0)

    def test_verdict_is_cached_and_the_provider_is_not_called_twice(self):
        provider = FakeProvider()
        config = self.config()
        ranker.apply([_window("mesmo texto", 50.0)], config, self.log, provider=provider)
        second = FakeProvider()
        judged = ranker.apply(
            [_window("mesmo texto", 50.0)], config, self.log, provider=second
        )
        self.assertEqual(judged, 1)
        self.assertEqual(second.calls, 0)

    def test_provider_failure_leaves_the_heuristic_scores_intact(self):
        windows = [_window("a", 50.0), _window("b", 40.0)]
        judged = ranker.apply(
            windows,
            self.config(),
            self.log,
            provider=FakeProvider(error=ClipperError("boom")),
        )
        self.assertEqual(judged, 0)
        self.assertEqual([w.score for w in windows], [50.0, 40.0])

    def test_a_bad_response_for_one_window_does_not_stop_the_others(self):
        class FlakyProvider(FakeProvider):
            def complete(self, system: str, user: str) -> str:
                self.calls += 1
                if "quebrado" in user:
                    return "nao vou responder json"
                return FULL_JSON

        windows = [_window("quebrado", 50.0), _window("ok", 40.0)]
        judged = ranker.apply(windows, self.config(), self.log, provider=FlakyProvider())
        self.assertEqual(judged, 1)
        self.assertEqual(windows[0].score, 50.0)
        self.assertGreater(windows[1].score, 0.0)

    def test_corrupt_cache_entry_is_a_miss(self):
        config = self.config()
        window = _window("texto", 50.0)
        key = ranker.cache_key(window, config.ranker_model)
        directory = ranker.resolve_cache_dir(config)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{key}.json").write_text("{not json", encoding="utf-8")

        provider = FakeProvider()
        judged = ranker.apply([window], config, self.log, provider=provider)
        self.assertEqual(judged, 1)
        self.assertEqual(provider.calls, 1)
        self.assertTrue((directory / f"{key}.json").exists())

    def test_empty_candidate_list(self):
        self.assertEqual(ranker.apply([], self.config(), self.log, provider=FakeProvider()), 0)

    def test_zero_top_n_judges_nothing(self):
        windows = [_window("a", 50.0)]
        judged = ranker.apply(
            windows, self.config(ranker_top_n=0), self.log, provider=FakeProvider()
        )
        self.assertEqual(judged, 0)
        self.assertEqual(windows[0].score, 50.0)

    def test_cache_dir_follows_the_explicit_cache_dir(self):
        config = self.config()
        self.assertEqual(ranker.resolve_cache_dir(config), self.tmp / "rank")

    def test_cache_dir_falls_back_outside_the_work_dir(self):
        # Não pode cair em `_work`: `cli.py` apaga esse diretório no `finally`,
        # então um cache lá dentro nunca sobrevive para ser reusado — e com
        # `--ranker llm` isso significa pagar as chamadas de novo a cada rodada.
        # Este teste chamava-se "falls_back_to_the_work_dir" e afirmava o
        # comportamento errado.
        config = ClipConfig(url="u", output_dir=self.tmp / "out", ranker="llm")
        resolved = ranker.resolve_cache_dir(config)
        self.assertEqual(resolved, self.tmp / "out" / "cache" / "rank")
        self.assertNotIn("_work", resolved.parts, "o cache não pode morar no scratch")


class BlendTests(unittest.TestCase):
    def test_midpoint_weight_averages_both_scales(self):
        verdict = ranker.Verdict(scores={"gancho": 10.0})
        self.assertAlmostEqual(ranker._blend(50.0, verdict, 0.5), 75.0, places=6)

    def test_weight_is_clamped(self):
        verdict = ranker.Verdict(scores={"gancho": 10.0})
        self.assertEqual(ranker._blend(50.0, verdict, 5.0), 100.0)
        self.assertEqual(ranker._blend(50.0, verdict, -1.0), 50.0)


class _CapturingLogger(Logger):
    """Logger que guarda TODAS as linhas, inclusive as que ``quiet`` suprimiria.

    ``Logger._emit`` devolve cedo quando ``quiet`` e o prefixo nao e ``!``, entao
    um teste de progresso com ``Logger(quiet=True)`` nao veria nada. Gravar antes
    do ``super()`` e o que torna a linha observavel sem tornar o teste barulhento.
    """

    def __init__(self) -> None:
        super().__init__(quiet=True)
        self.lines: list[str] = []

    def _emit(self, prefix: str, message: str) -> None:
        self.lines.append(f"{prefix} {message}")
        super()._emit(prefix, message)


class _TimedProvider:
    """Provider que registra o intervalo de cada chamada e pode falhar numa delas.

    O ``delay`` existe para que a sobreposicao seja observavel: sem ele a
    chamada termina antes de a proxima comecar e o pico medido seria 1 mesmo
    com o pool funcionando.
    """

    name = "timed"

    def __init__(self, delay: float = 0.05, fail_when_text_contains: str = "") -> None:
        self.delay = delay
        self.fail_when_text_contains = fail_when_text_contains
        self.calls = 0
        self.spans: list[tuple[float, float]] = []
        self._guard = threading.Lock()

    def complete(self, system: str, user: str) -> str:
        with self._guard:
            self.calls += 1
        started = time.perf_counter()
        time.sleep(self.delay)
        finished = time.perf_counter()
        with self._guard:
            self.spans.append((started, finished))
        if self.fail_when_text_contains and self.fail_when_text_contains in user:
            raise ClipperError("falha simulada no provedor")
        return FULL_JSON


def _peak_concurrency(spans: list[tuple[float, float]]) -> int:
    """Maior numero de chamadas simultaneas, por varredura de eventos.

    Desempate por delta: ``-1`` antes de ``+1`` no mesmo instante, porque uma
    chamada que termina quando outra comeca nao se sobrepoe a ela.
    """
    events: list[tuple[float, int]] = []
    for started, finished in spans:
        events.append((started, 1))
        events.append((finished, -1))
    events.sort()
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


class RankerConcurrencyTests(RankerTestCase):
    """O laco do curador era serial: 24 chamadas eram 24 latencias somadas.

    Estes testes travam a CONCORRENCIA, nao o tempo: um teste de relogio seria
    flaky numa maquina carregada. O que importa e que as chamadas se sobreponham
    e que o resultado nao mude por causa disso.
    """

    def test_the_calls_overlap_instead_of_queueing(self):
        provider = _TimedProvider(delay=0.05)
        windows = [_window(f"w{index}", float(index)) for index in range(24)]
        ranker.apply(
            windows, self.config(ranker_concurrency=6), self.log, provider=provider
        )
        self.assertEqual(provider.calls, 24)
        self.assertGreater(
            _peak_concurrency(provider.spans), 1,
            "as chamadas continuam uma atras da outra: o laco voltou a ser serial",
        )

    def test_concurrency_one_reproduces_the_serial_behaviour(self):
        provider = _TimedProvider(delay=0.02)
        windows = [_window(f"w{index}", float(index)) for index in range(6)]
        ranker.apply(
            windows, self.config(ranker_concurrency=1), self.log, provider=provider
        )
        self.assertEqual(provider.calls, 6)
        self.assertEqual(_peak_concurrency(provider.spans), 1)

    def test_the_ceiling_is_respected(self):
        provider = _TimedProvider(delay=0.05)
        windows = [_window(f"w{index}", float(index)) for index in range(24)]
        ranker.apply(
            windows, self.config(ranker_concurrency=3), self.log, provider=provider
        )
        pico = _peak_concurrency(provider.spans)
        # Duas direcoes no mesmo teste: acima de 1 prova que o pool esta em uso
        # (com o laco serial o pico e 1), e ate 3 prova que o teto nao foi furado.
        self.assertGreater(pico, 1, "o pool nao esta sendo usado")
        self.assertLessEqual(pico, 3, "o teto de concorrencia foi furado")

    def test_the_result_does_not_depend_on_the_concurrency(self):
        def run(concurrency: int) -> list[float]:
            windows = [_window(f"w{index}", float(index)) for index in range(8)]
            ranker.apply(
                windows,
                self.config(ranker_concurrency=concurrency),
                self.log,
                provider=FakeProvider(),
            )
            return [window.score for window in windows]

        self.assertEqual(run(1), run(6))

    def test_every_shortlisted_window_is_still_judged(self):
        provider = _TimedProvider(delay=0.01)
        windows = [_window(f"w{index}", float(index)) for index in range(24)]
        judged = ranker.apply(
            windows, self.config(ranker_concurrency=6), self.log, provider=provider
        )
        self.assertEqual(judged, 24)
        self.assertEqual(provider.calls, 24)
        self.assertTrue(all("llm_overall" in window.components for window in windows))

    def test_one_failing_window_does_not_cost_the_others(self):
        provider = _TimedProvider(delay=0.01, fail_when_text_contains="w3")
        windows = [_window(f"w{index}", float(index)) for index in range(8)]
        judged = ranker.apply(
            windows, self.config(ranker_concurrency=4), self.log, provider=provider
        )
        self.assertEqual(judged, 7)
        perdida = next(window for window in windows if window.text == "w3")
        self.assertNotIn("llm_overall", perdida.components)
        # A janela que falhou mantem o score heuristico: a rede nao e culpa dela.
        self.assertAlmostEqual(perdida.score, 3.0, places=2)

    def test_a_cached_window_never_reaches_the_pool(self):
        provider = FakeProvider()
        config = self.config(ranker_concurrency=6)
        ranker.apply([_window("texto cacheado", 50.0)], config, self.log, provider=provider)
        segundo = _TimedProvider(delay=0.01)
        ranker.apply([_window("texto cacheado", 50.0)], config, self.log, provider=segundo)
        self.assertEqual(segundo.calls, 0)
        self.assertEqual(segundo.spans, [])


class RankerProgressTests(RankerTestCase):
    """A espera era longa E opaca: o laco nao dizia nada ate o fim."""

    def test_the_stage_reports_one_line_per_window(self):
        log = _CapturingLogger()
        windows = [_window(f"w{index}", float(index)) for index in range(8)]
        ranker.apply(
            windows,
            self.config(ranker_concurrency=4),
            log,
            provider=_TimedProvider(delay=0.01),
        )
        progresso = [line for line in log.lines if "/8" in line]
        self.assertTrue(progresso, f"nenhuma linha de progresso; log={log.lines}")
        # A ultima linha tem de fechar a contagem, senao o usuario fica sem saber
        # se terminou.
        self.assertTrue(any("8/8" in line for line in progresso), progresso)

    def test_a_single_window_does_not_print_a_progress_bar(self):
        log = _CapturingLogger()
        ranker.apply(
            [_window("so uma", 50.0)],
            self.config(ranker_concurrency=6),
            log,
            provider=_TimedProvider(delay=0.01),
        )
        self.assertFalse([line for line in log.lines if "/1" in line], log.lines)


class RankerConcurrencyConfigTests(unittest.TestCase):
    def test_zero_is_rejected(self):
        with self.assertRaises(ValueError):
            ClipConfig(url="u", ranker_concurrency=0).validate()

    def test_the_default_is_above_one(self):
        self.assertGreater(ClipConfig(url="u").ranker_concurrency, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
