"""Tests for the curator prompt file, the named providers and the text fields.

The three things this covers are the three that break silently if they regress:

* **The prompt file is honoured.** A prompt that is read but never sent is
  indistinguishable from one that works, until you read the clips.
* **Editing the prompt invalidates the cache.** Without this, the first run
  after an edit reuses the previous prompt's verdicts and the edit appears to
  do nothing - which is the exact symptom that makes a person stop trusting
  the feature.
* **A missing headline does not cost the verdict.** The score is the expensive
  part; refusing it over a missing string would throw away the useful half.

The provider is injected throughout, so nothing here touches the network.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from viralclipper import providers, ranker
from viralclipper.config import ClipConfig
from viralclipper.score import Window
from viralclipper.util import ClipperError, Logger

FULL_JSON = (
    '{"autocontido": 8, "gancho": 9, "payoff": 7, "compartilhavel": 8, '
    '"final_completo": 6, "headline": "ELE REVELOU O SEGREDO", '
    '"hashtags": "#viral #fyp #cortes", "motivo": "historia completa"}'
)

PROMPT = "Voce e um curador de cortes virais. Seja rigoroso."


class FakeProvider:
    """Provider whose response is dictated by the test."""

    name = "fake"

    def __init__(self, response: str = FULL_JSON):
        self.response = response
        self.calls = 0
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        self.prompts.append((system, user))
        return self.response


def _window(text: str, score: float = 50.0) -> Window:
    return Window(
        start=0.0, end=30.0, unit_start=0, unit_end=1, text=text, score=score
    )


class CuratorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="curator-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = Logger(quiet=True)

    def prompt_file(self, text: str = PROMPT, name: str = "curador.txt") -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def config(self, **overrides) -> ClipConfig:
        payload = {
            "url": "https://youtu.be/fixture",
            "ranker": "llm",
            "cache_dir": self.tmp,
            "ranker_requires_key": False,
        }
        payload.update(overrides)
        return ClipConfig(**payload)


class LoadPromptTests(CuratorTestCase):
    def test_reads_the_file(self):
        path = self.prompt_file()
        self.assertEqual(ranker.load_curator_prompt(path), PROMPT)

    def test_missing_file_raises_naming_the_path(self):
        missing = self.tmp / "nao-existe.txt"
        with self.assertRaises(ClipperError) as caught:
            ranker.load_curator_prompt(missing)
        self.assertIn("nao-existe.txt", str(caught.exception))

    def test_empty_file_raises_instead_of_falling_back(self):
        """Silent fallback is the worst outcome: the run succeeds, the rules
        were never sent, and nothing says so."""
        path = self.prompt_file("   \n\n  ")
        with self.assertRaises(ClipperError):
            ranker.load_curator_prompt(path)

    def test_fingerprint_follows_the_content(self):
        self.assertNotEqual(
            ranker.prompt_fingerprint("regra A"), ranker.prompt_fingerprint("regra B")
        )

    def test_fingerprint_is_stable_for_the_same_text(self):
        self.assertEqual(
            ranker.prompt_fingerprint(PROMPT), ranker.prompt_fingerprint(PROMPT)
        )


class BuildMessagesTests(CuratorTestCase):
    def test_without_a_file_the_built_in_prompt_is_used(self):
        system, user = ranker.build_messages(self.config(), _window("texto"))
        self.assertEqual(system, ranker.SYSTEM_PROMPT)
        self.assertIn("texto", user)

    def test_the_built_in_prompt_still_carries_the_contract(self):
        _, user = ranker.build_messages(self.config(), _window("texto"))
        self.assertIn(ranker.HEADLINE_KEY, user)
        self.assertIn(ranker.HASHTAGS_KEY, user)

    def test_with_a_file_the_file_becomes_the_system_message(self):
        path = self.prompt_file()
        system, user = ranker.build_messages(
            self.config(curator_prompt_file=path), _window("texto")
        )
        self.assertTrue(system.startswith(PROMPT))

    def test_the_contract_is_appended_by_the_code_not_the_author(self):
        """The whole reason a free-form prompt can be dropped in verbatim."""
        path = self.prompt_file()
        system, _ = ranker.build_messages(
            self.config(curator_prompt_file=path), _window("texto")
        )
        self.assertIn(ranker.HEADLINE_KEY, system)
        self.assertIn(ranker.HASHTAGS_KEY, system)
        self.assertNotIn(ranker.HEADLINE_KEY, PROMPT)

    def test_the_user_message_carries_the_excerpt(self):
        path = self.prompt_file()
        _, user = ranker.build_messages(
            self.config(curator_prompt_file=path), _window("trecho unico")
        )
        self.assertIn("trecho unico", user)

    def test_the_rubric_is_not_sent_twice(self):
        """The file carries the criteria; the user message is only material."""
        path = self.prompt_file()
        _, user = ranker.build_messages(
            self.config(curator_prompt_file=path), _window("texto")
        )
        self.assertNotIn("autocontido", user)


class ShippedPromptTests(unittest.TestCase):
    """O prompt versionado (`prompts/curador.txt`) contra o contrato do motor.

    O arquivo e o contrato de resposta sao escritos em lugares diferentes: um e
    texto que o usuario edita, o outro e codigo em `ranker.RESPONSE_CONTRACT`.
    Os dois vao juntos na mesma mensagem de sistema, entao quando eles discordam
    o modelo ve duas instrucoes e escolhe -- e nada avisa.

    Foi o que aconteceu com as hashtags: o arquivo dizia "De 5 a 8" e o contrato
    anexado dizia "de 5 a 10". Nao quebrava nada visivel, o que e exatamente o
    motivo de valer um teste: a divergencia so apareceria como um numero
    inesperado de hashtags num corte publicado.

    O arquivo NAO e copiado aqui de proposito. Estes testes leem o arquivo real,
    porque o defeito que eles guardam e justamente a distancia entre o arquivo
    real e o contrato real.
    """

    @classmethod
    def setUpClass(cls):
        cls.path = Path(__file__).resolve().parent.parent / "prompts" / "curador.txt"

    def test_the_shipped_prompt_exists_and_is_not_empty(self):
        self.assertTrue(self.path.is_file(), f"sumiu: {self.path}")
        self.assertTrue(
            ranker.load_curator_prompt(self.path).strip(),
            "o prompt versionado esta vazio",
        )

    def test_the_shipped_prompt_is_still_the_same_file_the_engine_loads(self):
        # O caminho que o painel serve (`CURATOR_PROMPT_PATH` em web/server.py)
        # tem de ser este. Se um dos dois mudar sozinho, o editor do painel
        # passa a salvar num arquivo que o motor nunca le.
        from web import server

        self.assertEqual(server.CURATOR_PROMPT_PATH.resolve(), self.path.resolve())

    def _ranges_in(self, text: str, word: str) -> list[tuple[int, int]]:
        """TODOS os intervalos "de N a M" que antecedem ``word``.

        Devolve uma lista, e nao o primeiro, de proposito. A primeira versao
        deste helper usava ``re.search`` e sofria do mesmo defeito que ela
        existia para pegar: um texto com duas faixas conflitantes passava no
        teste porque so a primeira era lida -- foi assim que a terceira faixa
        de hashtags do arquivo do painel escapou. Quem valida um texto tem de
        percorrer o texto inteiro.
        """
        return [
            (int(m.group(1)), int(m.group(2)))
            for m in re.finditer(rf"de (\d+) a (\d+) {word}\b", text, re.IGNORECASE)
        ]

    def test_the_hashtag_range_agrees_with_the_contract(self):
        # Duas instrucoes numericas opostas na mesma mensagem e o defeito que
        # este teste existe para pegar; a direcao (5-10) e a que o contrato
        # manda, porque e o contrato que o motor anexa.
        no_arquivo = self._ranges_in(
            ranker.load_curator_prompt(self.path), "hashtags"
        )
        no_contrato = self._ranges_in(ranker.RESPONSE_CONTRACT, "hashtags")
        self.assertTrue(no_arquivo, "o prompt nao declara a faixa de hashtags")
        self.assertTrue(no_contrato, "o contrato nao declara a faixa")
        # O contrato fala uma vez so; se passar a falar duas, e o mesmo defeito
        # de duas fontes na mesma mensagem, so que dentro do codigo.
        self.assertEqual(
            len(set(no_contrato)), 1,
            f"o proprio contrato declara faixas conflitantes: {no_contrato}",
        )
        # O arquivo pode repetir a faixa em prosa, mas nao pode CONTRADIZER.
        self.assertEqual(
            set(no_arquivo),
            set(no_contrato),
            "o prompt e o contrato discordam na faixa de hashtags: "
            f"arquivo={no_arquivo} contrato={no_contrato} -- o modelo recebe "
            "as duas instrucoes ao mesmo tempo",
        )

    def _limits_in(self, text: str, unit: str) -> set[str]:
        """Todo limite "no maximo N <unit>" do texto, como conjunto.

        Conjunto, e nao scalar, pelo mesmo motivo de ``_ranges_in``: um texto
        que diz "no maximo 10 palavras" numa linha e "no maximo 8 palavras"
        noutra tem de reprovar. Ler so a primeira ocorrencia e como nao ler.
        """
        return set(re.findall(rf"no maximo (\d+) {unit}", text, re.IGNORECASE))

    def test_the_headline_limits_agree_with_the_contract(self):
        # O mesmo defeito, no outro par: palavras e caracteres. O arquivo
        # repete os limites do contrato quase palavra por palavra, e repeticao
        # sem tranca e uma copia que envelhece.
        texto = ranker.load_curator_prompt(self.path)
        contrato = ranker.RESPONSE_CONTRACT

        for unidade in ("palavras", "caracteres"):
            with self.subTest(unidade=unidade):
                no_arquivo = self._limits_in(texto, unidade)
                no_contrato = self._limits_in(contrato, unidade)
                self.assertTrue(
                    no_arquivo, f"o prompt nao declara limite de {unidade}"
                )
                self.assertTrue(
                    no_contrato, f"o contrato nao declara limite de {unidade}"
                )
                self.assertEqual(
                    len(no_arquivo), 1,
                    f"o prompt declara limites de {unidade} conflitantes: "
                    f"{sorted(no_arquivo)}",
                )
                self.assertEqual(
                    no_arquivo, no_contrato,
                    f"limite de {unidade} da headline divergente: "
                    f"prompt={sorted(no_arquivo)} "
                    f"contrato={sorted(no_contrato)}",
                )

    def test_the_file_does_not_carry_the_response_contract_itself(self):
        # O contrato e anexado pelo codigo (build_messages). Se alguem colar o
        # JSON no arquivo, ele vai duas vezes para o modelo -- e a copia do
        # arquivo passa a ser editavel, ou seja, uma segunda fonte de verdade
        # para o formato de resposta.
        texto = ranker.load_curator_prompt(self.path)
        for chave in ("headline_alternativa", "final_completo", "autocontido"):
            with self.subTest(chave=chave):
                self.assertNotIn(
                    chave, texto,
                    f"o arquivo carrega '{chave}': o contrato seria enviado duas vezes",
                )


class CacheSaltTests(CuratorTestCase):
    def test_no_file_means_no_salt(self):
        self.assertEqual(ranker.prompt_cache_salt(self.config()), "")

    def test_a_file_contributes_a_salt(self):
        path = self.prompt_file()
        salt = ranker.prompt_cache_salt(self.config(curator_prompt_file=path))
        self.assertTrue(salt)

    def test_editing_the_prompt_changes_the_key(self):
        """The property the whole feature rests on: edit the prompt, and the
        next run pays for fresh verdicts instead of reusing the old ones."""
        first = self.prompt_file("regra antiga", name="a.txt")
        second = self.prompt_file("regra nova", name="b.txt")
        window = _window("texto")
        self.assertNotEqual(
            ranker.cache_key(window, "m", ranker.prompt_cache_salt(self.config(curator_prompt_file=first))),
            ranker.cache_key(window, "m", ranker.prompt_cache_salt(self.config(curator_prompt_file=second))),
        )

    def test_the_two_argument_key_is_unchanged(self):
        window = _window("texto")
        self.assertEqual(
            ranker.cache_key(window, "m"), ranker.cache_key(window, "m", "")
        )


class CleanHeadlineTests(unittest.TestCase):
    def test_strips_quotes_and_markdown(self):
        self.assertEqual(ranker.clean_headline('"**ELE REVELOU**"'), "ELE REVELOU")

    def test_collapses_newlines(self):
        self.assertEqual(ranker.clean_headline("linha um\nlinha dois"), "linha um linha dois")

    def test_caps_the_length(self):
        long = "PALAVRA " * 40
        self.assertLessEqual(len(ranker.clean_headline(long)), ranker.MAX_HEADLINE_CHARS)

    def test_never_leaves_a_trailing_separator(self):
        text = "A" * ranker.MAX_HEADLINE_CHARS + " resto"
        self.assertFalse(ranker.clean_headline(text).endswith((",", " ", "-")))

    def test_none_and_empty_become_empty(self):
        self.assertEqual(ranker.clean_headline(None), "")
        self.assertEqual(ranker.clean_headline("   "), "")


class CleanHashtagsTests(unittest.TestCase):
    def test_extracts_from_a_string(self):
        self.assertEqual(
            ranker.clean_hashtags("#Viral #FYP"), "#viral #fyp"
        )

    def test_accepts_a_list(self):
        self.assertEqual(
            ranker.clean_hashtags(["#viral", "#fyp"]), "#viral #fyp"
        )

    def test_deduplicates_case_insensitively(self):
        self.assertEqual(ranker.clean_hashtags("#viral #Viral #viral"), "#viral")

    def test_caps_the_count(self):
        many = " ".join(f"#tag{index}" for index in range(40))
        self.assertEqual(
            len(ranker.clean_hashtags(many).split()), ranker.MAX_HASHTAGS
        )

    def test_drops_what_is_not_a_hashtag(self):
        self.assertEqual(ranker.clean_hashtags("sem tag nenhuma"), "")

    def test_none_becomes_empty(self):
        self.assertEqual(ranker.clean_hashtags(None), "")


class ParseVerdictTextTests(unittest.TestCase):
    def test_headline_and_hashtags_are_parsed(self):
        verdict = ranker.parse_verdict(FULL_JSON)
        self.assertEqual(verdict.headline, "ELE REVELOU O SEGREDO")
        self.assertEqual(verdict.hashtags, "#viral #fyp #cortes")

    def test_a_verdict_without_them_is_still_valid(self):
        """The score is the expensive part; a missing string must not void it."""
        verdict = ranker.parse_verdict('{"gancho": 9, "payoff": 7}')
        self.assertEqual(verdict.headline, "")
        self.assertEqual(verdict.hashtags, "")
        self.assertEqual(verdict.scores["gancho"], 9.0)

    def test_no_dimension_still_raises(self):
        with self.assertRaises(ClipperError):
            ranker.parse_verdict('{"headline": "so o titulo"}')

    def test_headline_alone_does_not_satisfy_the_parse(self):
        with self.assertRaises(ClipperError):
            ranker.parse_verdict('{"hashtags": "#viral"}')


class ApplyTextTests(CuratorTestCase):
    def test_the_window_receives_both_fields(self):
        window = _window("texto")
        ranker.apply([window], self.config(), self.log, provider=FakeProvider())
        self.assertEqual(window.headline, "ELE REVELOU O SEGREDO")
        self.assertEqual(window.hashtags, "#viral #fyp #cortes")

    def test_a_model_without_a_headline_does_not_erase_an_existing_one(self):
        window = _window("texto")
        window.headline = "TITULO ANTERIOR"
        ranker.apply(
            [window],
            self.config(),
            self.log,
            provider=FakeProvider('{"gancho": 9}'),
        )
        self.assertEqual(window.headline, "TITULO ANTERIOR")

    def test_the_text_fields_do_not_land_in_the_numeric_bag(self):
        """``components`` is summed and averaged by every consumer, so a
        headline dropped in there would be a string in a numeric field. It goes
        on its own attribute instead.

        ``llm_motivo`` is the one string that predates this and still lives
        there; it is not what this test is about, so the check names the two
        fields it is.
        """
        window = _window("texto")
        ranker.apply([window], self.config(), self.log, provider=FakeProvider())
        self.assertNotIn("llm_headline", window.components)
        self.assertNotIn("llm_hashtags", window.components)
        self.assertEqual(window.headline, "ELE REVELOU O SEGREDO")
        self.assertEqual(window.hashtags, "#viral #fyp #cortes")

    def test_the_prompt_file_is_what_the_provider_receives(self):
        path = self.prompt_file("REGRA PERSONALIZADA DO SENHOR SENA")
        provider = FakeProvider()
        ranker.apply(
            [_window("texto")],
            self.config(curator_prompt_file=path),
            self.log,
            provider=provider,
        )
        system, _ = provider.prompts[0]
        self.assertIn("REGRA PERSONALIZADA DO SENHOR SENA", system)

    def test_the_new_fields_survive_the_cache_round_trip(self):
        config = self.config()
        ranker.apply([_window("mesmo")], config, self.log, provider=FakeProvider())
        window = _window("mesmo")
        second = FakeProvider()
        ranker.apply([window], config, self.log, provider=second)
        self.assertEqual(second.calls, 0)
        self.assertEqual(window.headline, "ELE REVELOU O SEGREDO")
        self.assertEqual(window.hashtags, "#viral #fyp #cortes")


class ProviderTests(unittest.TestCase):
    def test_unknown_provider_raises_with_the_list(self):
        with self.assertRaises(ClipperError) as caught:
            providers.get_provider("nao-existe")
        self.assertIn("nemotron-super", str(caught.exception))

    def test_every_provider_declares_what_it_needs(self):
        for provider in providers.list_providers():
            self.assertTrue(provider.name)
            self.assertTrue(provider.base_url.startswith("http"))
            self.assertTrue(provider.model)

    def test_apply_to_config_fills_the_three_knobs(self):
        config = providers.apply_to_config(
            ClipConfig(url="u"), "nemotron-super"
        )
        provider = providers.get_provider("nemotron-super")
        self.assertEqual(config.ranker_provider, "nemotron-super")
        self.assertEqual(config.ranker_base_url, provider.base_url)
        self.assertEqual(config.ranker_model, provider.model)

    def test_the_timeout_is_only_raised(self):
        config = providers.apply_to_config(
            ClipConfig(url="u", ranker_timeout=600.0), "openai"
        )
        self.assertEqual(config.ranker_timeout, 600.0)

    def test_a_local_provider_needs_no_key(self):
        config = providers.apply_to_config(ClipConfig(url="u"), "local")
        self.assertFalse(config.ranker_requires_key)

    def test_provider_for_config_is_none_when_unnamed(self):
        self.assertIsNone(providers.provider_for_config(ClipConfig(url="u")))

    def test_a_free_tier_provider_costs_nothing(self):
        # The point of the free entries is that a person can turn the curator
        # on without a card on file. If one of them ever needs a key it does
        # not have, the failure lands mid-pipeline, after the download.
        for name in ("groq", "gemini", "openrouter"):
            with self.subTest(provider=name):
                provider = providers.get_provider(name)
                self.assertTrue(provider.api_key_env, f"{name} sem env var")
                self.assertTrue(provider.requires_key, f"{name} deveria exigir chave")

    def test_every_base_url_is_the_openai_shape(self):
        # All of them speak /chat/completions, which is what lets one client
        # cover the whole table. Two shapes are valid: a versioned base
        # (`.../v1`, `.../v1beta/openai`) and a bare host where the vendor
        # expects the client to append the versioned path itself. DeepSeek
        # documents `https://api.deepseek.com` with no `/v1`, so the bare
        # host is allowed by name -- an unknown host without a version is
        # still a mistake, and that is what this catches.
        BARE_HOSTS = {"https://api.deepseek.com"}
        for provider in providers.list_providers():
            with self.subTest(provider=provider.name):
                url = provider.base_url.rstrip("/")
                self.assertTrue(
                    url.startswith("http"),
                    f"{provider.name}: base_url nao e http",
                )
                ok = (
                    url.endswith("/v1")
                    or "/v1beta/openai" in url
                    or url in BARE_HOSTS
                )
                self.assertTrue(
                    ok, f"{provider.name}: base_url inesperada: {provider.base_url}"
                )

    def test_the_two_deepseek_entries_stay_distinct(self):
        # The vendor's own API calls the model `deepseek-flash`; the NIM
        # catalog calls the same model `deepseek-ai/deepseek-v4.1-flash`.
        # Copying one id onto the other is a 404 that reads like the model
        # was retired, so both entries are pinned here.
        via_nim = providers.get_provider("deepseek-flash")
        via_api = providers.get_provider("deepseek")
        self.assertNotEqual(via_nim.base_url, via_api.base_url)
        self.assertNotEqual(via_nim.model, via_api.model)
        self.assertEqual(via_api.model, "deepseek-flash")
        self.assertEqual(via_nim.model, "deepseek-ai/deepseek-v4.1-flash")
        self.assertNotEqual(via_nim.api_key_env, via_api.api_key_env)

    def test_every_provider_note_says_what_it_costs(self):
        # The note is the only place the panel can tell a free model from a
        # billed one. An empty note is a silent cost.
        for provider in providers.list_providers():
            with self.subTest(provider=provider.name):
                self.assertTrue(
                    provider.note.strip(),
                    f"{provider.name} sem nota: o painel nao tem como avisar o custo",
                )


class ConfigValidationTests(CuratorTestCase):
    def test_a_missing_prompt_file_fails_before_the_download(self):
        config = self.config(curator_prompt_file=self.tmp / "nao-existe.txt")
        with self.assertRaises(ValueError):
            config.validate()

    def test_an_existing_prompt_file_validates(self):
        config = self.config(curator_prompt_file=self.prompt_file())
        config.validate()

    def test_an_unknown_provider_fails_validation(self):
        config = self.config(ranker_provider="nao-existe")
        with self.assertRaises(ClipperError):
            config.validate()

    def test_a_known_provider_validates(self):
        config = self.config(ranker_provider="openai")
        config.validate()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
