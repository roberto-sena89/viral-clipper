"""The Ajustes page contract: what the panel owns, and how it round-trips.

The page is a thin layer over one file (``ajustes.toml``) and one tuple
(``AJUSTES_KEYS``). Everything that can silently drift lives at those two
seams, so that is what these tests pin down:

* a key that is not a real argparse dest or not a real ClipConfig field would
  be dropped on the way to the engine without a word;
* a control on the page with no key behind it would look editable and change
  nothing;
* a value that loses its type on the way through TOML would turn a toggle into
  the number 1.

None of these fail loudly at runtime, which is exactly why they are worth a
test.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from viralclipper import cli
from viralclipper import config as config_mod
from web import server

AJUSTES_HTML = server.WEB_DIR / "ajustes.html"


def page_source(name: str) -> str:
    return (server.WEB_DIR / name).read_text(encoding="utf-8")


def control_ids(markup: str) -> set[str]:
    """Every control id in ``markup``.

    Three shapes count: ``<input>``/``<select>``/``<textarea>``, and the
    ``<button class="toggle">`` switches. The switches are matched by class on
    purpose -- a plain ``<button>`` is an action (save, reload the prompt), not
    a value, and folding those in would make the parity test meaningless.
    """
    campos = set(re.findall(r"<(?:input|select|textarea)[^>]*\sid=\"([^\"]+)\"", markup))
    toggles = set(re.findall(
        r"<button[^>]*\bclass=\"[^\"]*\btoggle\b[^\"]*\"[^>]*\sid=\"([^\"]+)\"", markup,
    ))
    return campos | toggles


#: Control id on the page -> key in ``AJUSTES_KEYS``.
#:
#: ``None`` means the control carries no value of its own: it is a toggle that
#: *derives* another field (``headline-on`` decides whether ``headline_seconds``
#: is 0 or its own number). It used to cover ``transcript-url`` too, but that
#: control left the page with the pasted transcript.
CONTROLES = {
    "min-duration": "min_duration",
    "max-duration": "max_duration",
    "target-duration": "target_duration",
    "min-score": "min_score",
    "min-gap": "min_gap",
    "engine": "engine",
    "auto-margin": "auto_margin",
    "auto-ceiling": "auto_ceiling",
    "max-grace": "max_duration_grace",
    "whisper-model": "whisper_model",
    "language": "language",
    "beam-size": "beam_size",
    "cache-dir": "cache_dir",
    "vad-filter": "vad_filter",
    "transcript-cache": "transcript_cache",
    "layout": "layout",
    "caption-preset": "caption_preset",
    "caption-style": "caption_style",
    "font-size": "font_size",
    "crf": "crf",
    "target-lufs": "target_lufs",
    "workers": "workers",
    "headline-on": None,
    "headline-seconds": "headline_seconds",
    "progress-bar-on": "progress_bar",
    "jump-cut": "jump_cut",
    "loudnorm": "loudnorm",
    # Sem os sete controles `ranker-*`: o card do Curador saiu desta pagina e
    # mora so em Cortes. As chaves correspondentes sairam de AJUSTES_KEYS junto,
    # entao `test_the_controls_cover_every_key_exactly_once` fecha nos dois lados.
    #
    # Sem `curator-prompt` e `curator-prompt-path` pela mesma razao: o editor do
    # prompt do curador ficou com DONO UNICO no Estudio (etapa 3), e os dois ids
    # sairam daqui junto com o card inteiro. A entrada `None` que eles tinham
    # dizia "o servidor e' quem manda, nao este arquivo" -- o que continua
    # verdade, so' que agora nao ha nem controle nesta pagina para dizer isso.
    # O contrato de exclusividade esta em `PromptCardSingleOwnerTests`.
    #
    # Sem `transcript` nem `transcript-url`: o texto colado e' de UM video, e
    # esta pagina governa o projeto inteiro. Ele foi para o Estudio (etapa 1),
    # ao lado da URL a que pertence -- e com ele saiu o carimbo de URL que so'
    # existia porque o texto estava no escopo errado.
}


def sample_settings() -> dict:
    """One value per key, each of a type the form can actually produce."""
    return {
        "min_duration": 30.0,
        "max_duration": 60.0,
        "target_duration": 42.0,
        "min_score": 0.0,
        "min_gap": 6.0,
        "engine": "hybrid",
        "auto_margin": 15.0,
        "auto_ceiling": 200,
        "max_duration_grace": 30.0,
        "whisper_model": "small",
        "language": "pt",
        "beam_size": 1,
        "cache_dir": "output/cache/transcripts",
        "vad_filter": True,
        "transcript_cache": False,
        "layout": "focus",
        "caption_preset": "karaoke",
        "caption_style": "karaoke",
        "font_size": None,
        "crf": 20,
        "target_lufs": -14.0,
        "workers": 2,
        "headline_seconds": 0.0,
        "progress_bar": False,
        "jump_cut": False,
        "loudnorm": True,
        # As sete chaves ranker_* NAO entram: o Curador com IA saiu de Ajustes e
        # vive so na pagina Cortes. Continuam sendo campos do ClipConfig e dests
        # da CLI -- so nao sao mais ajuste que este painel persiste.
    }


class AjustesKeysAreRealTests(unittest.TestCase):
    """Every key has to survive the trip to the engine."""

    @classmethod
    def setUpClass(cls):
        cls.parser = cli.build_parser()
        cls.dests = {a.dest for a in cls.parser._actions if a.dest and a.dest != "help"}
        cls.fields = {f.name for f in __import__("dataclasses").fields(config_mod.ClipConfig)}

    def test_every_key_is_an_argparse_dest(self):
        # Not cosmetic: apply_defaults() only forwards keys that match a real
        # argument, so a typo here means the value is dropped with a warning
        # nobody reads, and the --config promise in the file header is a lie.
        faltando = [k for k in server.AJUSTES_KEYS if k not in self.dests]
        self.assertEqual(faltando, [], f"sem argumento na CLI: {faltando}")

    def test_every_key_is_a_clipconfig_field(self):
        # _options_to_config drops unknown keys silently. A name that is not a
        # dataclass field would look saved and never reach the render.
        faltando = [k for k in server.AJUSTES_KEYS if k not in self.fields]
        self.assertEqual(faltando, [], f"sem campo no ClipConfig: {faltando}")

    def test_the_keys_build_a_valid_config(self):
        settings = sample_settings()
        self.assertEqual(set(settings), set(server.AJUSTES_KEYS))
        cfg = server._options_to_config({**settings, "url": "https://example.test/v"})
        cfg.validate()
        self.assertEqual(cfg.auto_margin, 15.0)
        self.assertEqual(cfg.target_lufs, -14.0)

    def test_curator_prompt_file_is_not_a_page_key(self):
        # The page never edits that path: it saves the prompt through
        # /prompts/curador. If it were writable here, the panel could point the
        # curator at any file on disk.
        self.assertNotIn("curator_prompt_file", server.AJUSTES_KEYS)

    def test_the_ranker_keys_left_the_contract(self):
        # The AI curator is configured on the Cortes page only. Keeping the
        # keys here while the card is gone would be worse than dead weight:
        # POST /ajustes would keep writing ranker values that no page can see,
        # and the file would look like it still owns a setting it does not.
        ranker_keys = [k for k in server.AJUSTES_KEYS if k.startswith("ranker")]
        self.assertEqual(ranker_keys, [])

    def test_the_ranker_keys_are_still_real_cli_options(self):
        # Leaving the contract must not mean leaving the engine. They are still
        # ClipConfig fields and argparse dests, so `--ranker llm` and a
        # hand-written --config keep working; only the panel stopped managing
        # them. This is the assertion that would catch someone "cleaning up" the
        # fields from config.py because they are no longer in AJUSTES_KEYS.
        for key in ("ranker", "ranker_provider", "ranker_api_key_env",
                    "ranker_model", "ranker_base_url", "ranker_top_n",
                    "ranker_weight"):
            self.assertIn(key, self.dests, f"{key} sumiu da CLI")
            self.assertIn(key, self.fields, f"{key} sumiu do ClipConfig")


class AjustesTomlTests(unittest.TestCase):
    """Round-trip through the file, including the types."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "ajustes.toml"
        self._patch = mock.patch.object(server, "AJUSTES_PATH", self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_missing_file_is_not_an_error(self):
        self.assertFalse(self.path.exists())
        self.assertEqual(server._read_ajustes(), {})

    def test_broken_file_is_not_an_error(self):
        # A hand-edited file with a syntax error must degrade to "nothing
        # saved", not take the endpoint down: the user can fix it from the page.
        self.path.write_text("min_duration = = 30\n", encoding="utf-8")
        self.assertEqual(server._read_ajustes(), {})

    def test_round_trip_preserves_value_and_type(self):
        settings = sample_settings()
        self.path.write_text(server._dump_ajustes(settings), encoding="utf-8")
        volta = server._read_ajustes()

        # font_size is None in the sample and None is deliberately not written,
        # so it is the one key allowed to come back missing.
        esperadas = {k: v for k, v in settings.items() if v is not None}
        self.assertEqual(set(volta), set(esperadas))
        for key, esperado in esperadas.items():
            self.assertEqual(volta[key], esperado, f"{key} mudou de valor")
            self.assertIs(type(volta[key]), type(esperado), f"{key} mudou de tipo")

    def test_toggles_survive_as_booleans(self):
        # The bug this pins: bool is a subclass of int in Python, so a
        # serialiser that checks int first writes true as 1, and the toggle
        # reads back as a number - which is truthy in JS, so it would look
        # right until someone turned it off.
        settings = {**sample_settings(), "vad_filter": False, "loudnorm": False}
        texto = server._dump_ajustes(settings)
        self.assertIn("vad_filter = false", texto)
        self.assertIn("loudnorm = false", texto)
        volta = tomllib.loads(texto)
        self.assertIs(volta["vad_filter"], False)
        self.assertIs(volta["loudnorm"], False)

    def test_none_is_omitted_rather_than_guessed(self):
        # font_size None means "inherit the caption preset". TOML has no null,
        # so the honest move is to leave the key out and let ClipConfig keep its
        # own default - not to write 0 or "" and pin the field forever.
        texto = server._dump_ajustes({**sample_settings(), "font_size": None})
        self.assertNotIn("font_size", texto)
        self.assertNotIn("font_size", tomllib.loads(texto))

    def test_strings_are_escaped(self):
        settings = {**sample_settings(), "engine": 'we"ird\\path'}
        self.path.write_text(server._dump_ajustes(settings), encoding="utf-8")
        self.assertEqual(server._read_ajustes()["engine"], 'we"ird\\path')

    def test_a_multiline_string_survives_the_round_trip(self):
        # The defect this pins: a raw newline inside a TOML basic string makes
        # the whole file unparseable, and _read_ajustes swallows the parse
        # error and returns {}. Any string field can carry one -- `cache_dir` is
        # a path, but the serialiser must escape whatever it is handed, or a
        # single bad value silently discards EVERY other setting in the file.
        settings = {**sample_settings(), "cache_dir": "a\nb\nc"}
        self.path.write_text(server._dump_ajustes(settings), encoding="utf-8")
        volta = server._read_ajustes()
        self.assertEqual(volta["cache_dir"], "a\nb\nc")
        # The proof that nothing else was lost with it.
        self.assertEqual(volta["min_duration"], 30.0)
        self.assertEqual(volta["target_lufs"], -14.0)

    def test_every_control_character_toml_forbids_is_escaped(self):
        # \r and \t join \n in the forbidden set, and a value pasted from a
        # browser can carry all three: \r from Windows line endings, \t from an
        # indented line.
        for bruto in ("a\r\nb", "a\tb", "a\nb"):
            with self.subTest(bruto=bruto):
                settings = {**sample_settings(), "cache_dir": bruto}
                texto = server._dump_ajustes(settings)
                self.assertEqual(tomllib.loads(texto)["cache_dir"], bruto)

    def test_keys_outside_the_contract_are_ignored(self):
        # A stale file (or one written by a newer version) must not smuggle
        # url/output_dir into the panel's settings.
        self.path.write_text(
            "min_duration = 33.0\nurl = \"https://evil.test\"\noutput_dir = \"/tmp\"\n",
            encoding="utf-8",
        )
        self.assertEqual(server._read_ajustes(), {"min_duration": 33.0})

    def test_the_file_follows_the_key_order(self):
        # Stable order keeps the file diff-friendly between saves. Keys with a
        # None value are absent, so they are absent from the expectation too.
        settings = sample_settings()
        texto = server._dump_ajustes(settings)
        ordem = [
            linha.split(" = ")[0]
            for linha in texto.splitlines()
            if linha and not linha.startswith("#")
        ]
        esperado = [k for k in server.AJUSTES_KEYS if settings[k] is not None]
        self.assertEqual(ordem, esperado)

    def test_a_written_file_is_a_valid_cli_config(self):
        # The whole point of putting it at the repo root: `--config
        # ajustes.toml` has to work, or the panel and the CLI drift apart.
        from viralclipper import config_file

        class Quiet:
            def info(self, *a, **k): pass
            def warn(self, *a, **k): pass
            def error(self, *a, **k): pass

        self.path.write_text(server._dump_ajustes(sample_settings()), encoding="utf-8")
        carregado = config_file.load_config_file(self.path, Quiet())
        self.assertEqual(carregado["target_lufs"], -14.0)
        self.assertEqual(carregado["caption_preset"], "karaoke")

        # And the keys really do land on the parser, not just in the dict.
        parser = cli.build_parser()
        config_file.apply_defaults(parser, carregado, Quiet())
        args = parser.parse_args(["https://example.test/v"])
        self.assertEqual(args.target_lufs, -14.0)
        self.assertEqual(args.auto_ceiling, 200)
        self.assertEqual(args.caption_preset, "karaoke")


class AjustesPageTests(unittest.TestCase):
    """The page and the tuple have to describe the same thing."""

    @classmethod
    def setUpClass(cls):
        cls.markup = page_source("ajustes.html")
        cls.ids = control_ids(cls.markup)

    def test_every_control_on_the_page_is_declared(self):
        # A control nobody declared is a field the user can move that changes
        # nothing - the most expensive kind of bug, because it looks like it
        # worked.
        orfaos = sorted(self.ids - set(CONTROLES))
        self.assertEqual(orfaos, [], f"controles sem contrato: {orfaos}")

    def test_every_declared_control_exists_on_the_page(self):
        faltando = sorted(set(CONTROLES) - self.ids)
        self.assertEqual(faltando, [], f"contrato aponta para controles ausentes: {faltando}")

    def test_the_controls_cover_every_key_exactly_once(self):
        mapeadas = [k for k in CONTROLES.values() if k is not None]
        self.assertEqual(sorted(mapeadas), sorted(server.AJUSTES_KEYS))

    def test_the_transcript_is_not_on_this_page_anymore(self):
        # A transcricao colada saiu daqui. Ela e' texto de UM video, e esta
        # pagina governa o projeto inteiro (ajustes.toml): guardar o texto de um
        # video num arquivo que vale para todos era o escopo errado, e obrigava
        # a um carimbo de URL so' para ele nao vazar para o run do proximo video.
        # O campo (e a tabela de conferencia) moram no Estudio, ao lado da URL.
        self.assertNotIn("transcript", self.ids)
        self.assertNotIn("transcript-url", self.ids)
        self.assertNotIn("transcript_text", server.AJUSTES_KEYS)
        self.assertNotIn("transcript_text", CONTROLES.values())
        # E a pagina nao tem mais o campo em lugar nenhum: nem o id, nem a
        # etiqueta que o apresentava.
        self.assertNotIn('id="transcript"', self.markup)
        self.assertNotIn("Cole aqui a transcrição", self.markup)

    def test_the_page_has_no_duplicate_ids(self):
        todos = re.findall(r"\sid=\"([^\"]+)\"", self.markup)
        self.assertEqual(len(todos), len(set(todos)), "id repetido em ajustes.html")

    def test_every_aria_reference_resolves(self):
        ids = set(re.findall(r"\sid=\"([^\"]+)\"", self.markup))
        quebrados = []
        for attr in ("aria-describedby", "aria-labelledby", "aria-controls"):
            for m in re.finditer(attr + r"=\"([^\"]+)\"", self.markup):
                quebrados += [r for r in m.group(1).split() if r not in ids]
        self.assertEqual(quebrados, [], f"referencias aria quebradas: {quebrados}")

    def test_every_label_points_at_a_real_control(self):
        ids = set(re.findall(r"\sid=\"([^\"]+)\"", self.markup))
        for m in re.finditer(r"<label[^>]*\sfor=\"([^\"]+)\"", self.markup):
            self.assertIn(m.group(1), ids, f"label for={m.group(1)} sem controle")

    def test_the_provider_select_is_not_on_this_page_anymore(self):
        # The card moved to Cortes whole, provider select included. The
        # `data-defer-enhance` guarantee did not disappear with it -- it moved
        # too, and is asserted against index.html below. Keeping the assertion
        # here would pin an absence on this page and a guarantee on the wrong
        # one.
        self.assertNotIn("ranker-provider", self.markup)
        self.assertNotIn("Curador com IA", self.markup)

    def test_the_cortes_page_keeps_the_provider_select_contract(self):
        # Where the select actually lives now: it is filled by /providers after
        # the first paint, and enhanceSelect copies the <option> elements at the
        # moment it runs. Without the marker the rich panel would be born empty.
        cortes = page_source("index.html")
        self.assertRegex(
            cortes,
            r"<select[^>]*id=\"ranker-provider\"[^>]*data-defer-enhance",
        )
        self.assertIn("Curador com IA", cortes)


class AjustesAtomicWriteTests(unittest.TestCase):
    """O `ajustes.toml` era gravado com `write_text`, que trunca primeiro.

    Duas consequencias, e as duas custam a configuracao inteira: um travamento
    no meio da gravacao deixa um TOML invalido, e o leitor seguinte -- que
    engolia o erro de proposito -- devolvia `{}`. A pagina dizia "nada salvo
    ainda", o usuario entendia "primeira execucao", e o save seguinte reescrevia
    o arquivo com os defaults. As outras 26 chaves sumiam em silencio.

    Aqui ficam as duas metades da cura: a gravacao nao pode deixar arquivo
    parcial, e a leitura tem de CONTAR por que falhou.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "ajustes.toml"
        self._patch = mock.patch.object(server, "AJUSTES_PATH", self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # ---------- a leitura conta o motivo ----------

    def test_a_missing_file_has_no_reason_to_report(self):
        self.assertEqual(server._load_ajustes(), ({}, ""))

    def test_a_broken_file_reports_the_reason(self):
        # A rota continua de pe (nao pode cair por um arquivo torto), mas o
        # motivo viaja ate a pagina em vez de morrer aqui.
        self.path.write_text("min_duration = = 30\n", encoding="utf-8")
        settings, motivo = server._load_ajustes()
        self.assertEqual(settings, {})
        self.assertTrue(motivo, "o arquivo torto foi engolido sem motivo")
        self.assertIn("Error", motivo)

    def test_a_good_file_reports_no_reason(self):
        self.path.write_text(server._dump_ajustes({"min_duration": 30}), encoding="utf-8")
        settings, motivo = server._load_ajustes()
        self.assertEqual(motivo, "")
        self.assertEqual(settings["min_duration"], 30)

    def test_read_ajustes_still_degrades_to_empty(self):
        # O contrato antigo continua: quem so quer os valores nao ganha uma
        # excecao nova na cara.
        self.path.write_text("min_duration = = 30\n", encoding="utf-8")
        self.assertEqual(server._read_ajustes(), {})

    # ---------- a gravacao nao deixa arquivo parcial ----------

    def test_a_failed_write_leaves_the_old_file_intact(self):
        # O ponto inteiro da escrita atomica. Com `write_text` o arquivo era
        # truncado ANTES da falha, entao o antigo nao sobrevivia.
        self.path.write_text(server._dump_ajustes({"min_duration": 30}), encoding="utf-8")
        antes = self.path.read_bytes()
        # Um diretorio no lugar do `.part` faz a gravacao do temporario estourar.
        (self.tmp / "ajustes.toml.part").mkdir()
        with self.assertRaises(OSError):
            server._write_atomically(self.path, b"min_duration = 99\n")
        self.assertEqual(self.path.read_bytes(), antes, "a falha destruiu o arquivo bom")

    def test_a_good_write_replaces_and_leaves_no_part(self):
        self.path.write_text("min_duration = 30\n", encoding="utf-8")
        server._write_atomically(self.path, b"min_duration = 99\n")
        self.assertEqual(self.path.read_bytes(), b"min_duration = 99\n")
        self.assertFalse(
            (self.tmp / "ajustes.toml.part").exists(),
            "sobrou o temporario ao lado do arquivo",
        )

    def test_the_save_route_leaves_no_part_behind(self):
        h = _FakeHandler(server.Handler)
        h._handle_save_ajustes({"settings": {"min_duration": 30}})
        codigo, payload = h.sent
        self.assertEqual(codigo, 200, payload)
        self.assertEqual(
            [p.name for p in self.tmp.iterdir()], ["ajustes.toml"],
            "a rota de save deixou lixo no diretorio",
        )
        self.assertEqual(server._read_ajustes()["min_duration"], 30)

    def test_the_prompt_route_leaves_no_part_behind(self):
        prompt = self.tmp / "curador.txt"
        p = mock.patch.object(server, "CURATOR_PROMPT_PATH", prompt)
        p.start()
        self.addCleanup(p.stop)
        h = _FakeHandler(server.Handler)
        h._handle_save_curator_prompt({"text": "regras do curador\n"})
        codigo, payload = h.sent
        self.assertEqual(codigo, 200, payload)
        self.assertEqual(
            [p_.name for p_ in self.tmp.iterdir()], ["curador.txt"],
            "a rota do prompt deixou lixo no diretorio",
        )
        self.assertEqual(prompt.read_text(encoding="utf-8"), "regras do curador\n")


class PromptCardSingleOwnerTests(unittest.TestCase):
    """O editor do prompt do curador tem UM dono: o Estudio, etapa 3.

    Ele existiu nas DUAS paginas — Estudio e Ajustes — com os mesmos ids e os
    mesmos rotulos, e as copias divergiram: os rotulos diziam "Regras de
    curadoria" de um lado e "Regras do curador" do outro, os botoes diziam
    "Carregar/Salvar no arquivo" numa e outra coisa na outra, e o status da
    copia de Ajustes nascia com um texto fixo ("Prompt carregado do arquivo.")
    que o JS sobrescrevia antes de qualquer fetch — mentira no HTML, invisivel
    na tela.

    O que `prompts/curador.txt` NAO perdeu: ele continua sendo a fonte da
    verdade do motor, versionado no git, e o `CURATOR_PROMPT_PATH` do servidor
    continua decidindo o caminho (nunca vindo da requisicao). O que mudou foi
    ONDE ele se edita — e o atalho "Prompt do curador" da pagina Ajustes leva
    para la'.

    Entao o que se trava aqui deixou de ser a IGUALDADE e passou a ser a
    EXCLUSIVIDADE: os ids existem numa pagina e SO' nela, e o JS da outra nao os
    procura. O segundo lado importa tanto quanto o primeiro — um
    `$('#curator-prompt')` esquecido em `ajustes.js` devolve null, e
    `.addEventListener` sobre null estoura no load e mata o IIFE inteiro: a
    pagina de Ajustes fica sem autosave, sem resumo e sem a tabela da
    transcricao, sem dizer por que.
    """

    #: Os ids do card. O JS das duas paginas lia exatamente estes.
    IDS = ("curator-prompt", "curator-prompt-path", "btn-prompt-load",
           "btn-prompt-save", "curator-prompt-status")

    @classmethod
    def setUpClass(cls):
        cls.cortes = page_source("index.html")
        cls.ajustes = page_source("ajustes.html")
        cls.cortes_js = page_source("index.js")
        cls.ajustes_js = page_source("ajustes.js")

    def test_the_card_lives_only_in_the_studio(self):
        self.assertIn('class="card prompt-card"', self.cortes)
        self.assertNotIn('class="card prompt-card"', self.ajustes)

    def test_no_prompt_id_survives_in_ajustes(self):
        # A ancora e a CLASSE do card (`prompt-card`) e o id de cada controle,
        # nao o texto do titulo: "Prompt do curador" continua na pagina de
        # Ajustes como ROTULO do atalho que leva ao Estudio, e ancorar no texto
        # reprovaria justamente o link que faz a ponte.
        for cid in self.IDS:
            with self.subTest(id=cid):
                self.assertIn(cid, self.cortes)
                self.assertNotIn(cid, self.ajustes)

    def test_ajustes_js_does_not_read_a_control_that_left(self):
        # O par obrigatorio: tirar o markup E quem o le. O `'#id'` entre aspas e
        # o formato exato de um `$('#id')` — a mesma trava que
        # `RenderSectionOwnershipTests` usa para os controles que migraram.
        for cid in self.IDS:
            with self.subTest(id=cid):
                self.assertNotIn(f"'#{cid}'", self.ajustes_js)

    def test_the_studio_js_is_the_reader(self):
        # O outro lado da exclusividade: o dono PRECISA ler os ids, senao o card
        # fica na tela sem funcionar — e nenhum teste de markup acusaria.
        for cid in ("curator-prompt", "curator-prompt-path",
                    "btn-prompt-load", "btn-prompt-save"):
            with self.subTest(id=cid):
                self.assertIn(f"'#{cid}'", self.cortes_js)
        self.assertIn("/prompts/curador", self.cortes_js)

    def test_the_status_starts_empty(self):
        # Um estado vivo nasce vazio; quem o preenche e' a resposta do servidor.
        m = re.search(
            r"<span[^>]*id=\"curator-prompt-status\"[^>]*>(.*?)</span>",
            self.cortes, re.S,
        )
        self.assertIsNotNone(m, "span de status ausente no Estudio")
        self.assertEqual(m.group(1).strip(), "",
                         "o status nasce com texto: mentira no HTML")

    def test_the_path_field_is_readonly(self):
        # O caminho vem de `CURATOR_PROMPT_PATH` no servidor e nunca da
        # requisicao — e' o que impede o painel de virar gravador de arquivo
        # arbitrario. Um campo editavel aqui abriria essa porta.
        self.assertRegex(
            self.cortes,
            r"<input[^>]*id=\"curator-prompt-path\"[^>]*readonly",
        )

    def test_the_card_still_has_its_labels_and_buttons(self):
        # O card ficou com um dono so', mas nao pode ter ficado sem conteudo: a
        # exclusividade nao e' licenca para esvaziar o editor.
        card = self.cortes[self.cortes.index('class="card prompt-card"'):]
        card = card[:card.index("</section>")]
        for rotulo in ("Regras de curadoria", "Arquivo (a fonte da verdade)"):
            with self.subTest(rotulo=rotulo):
                self.assertIn(rotulo, card)
        for cid in ("btn-prompt-load", "btn-prompt-save"):
            with self.subTest(id=cid):
                self.assertIn(f'id="{cid}"', card)

    def test_the_ajustes_shortcut_points_at_the_new_home(self):
        # A ponte entre as paginas. O atalho antigo apontava para `#card-prompt`,
        # uma ancora que deixou de existir: o scroll-spy de `ajustes.js` so'
        # observa `a[href^="#"]`, entao ele descartaria o alvo em silencio e o
        # clique nao faria nada. Um atalho morto e' pior que nenhum.
        self.assertIn("Prompt do curador", self.ajustes)
        self.assertIn('href="/?etapa=3"', self.ajustes)
        self.assertNotIn('href="#card-prompt"', self.ajustes)


class AjustesScriptTests(unittest.TestCase):
    """O ajustes.js roda sem os controles que sairam da pagina.

    Estes testes extraem as funcoes do arquivo real e as executam com um DOM
    falso, via node. Nao ha copia da logica aqui: se houvesse, o teste passaria
    com a copia certa e o produto quebrado -- e foi exatamente um produto
    quebrado que motivou esta classe.

    O defeito que ela existe para pegar: o card do Curador saiu de ajustes.html,
    mas o JS continuou lendo `$('#ranker-model').value` e `s.ranker`. Com o
    campo ausente, `$()` devolve null e a primeira gravacao estoura com
    "Cannot read properties of null". Um teste que so le o HTML nao ve isso,
    porque o HTML esta certo e o JS e que ficou desalinhado.
    """

    @classmethod
    def setUpClass(cls):
        cls.fonte = (server.WEB_DIR / "ajustes.js").read_text(encoding="utf-8")

    def _executa(self, expressao):
        """Roda `expressao` dentro do escopo do ajustes.js, com DOM falso."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node nao esta no PATH")

        ancoras = [
            r"function num\(sel, fallback\) \{[\s\S]*?\n  \}",
            r"function toggleOn\(sel\) \{[\s\S]*?\n  \}",
            r"function labelOf\([\s\S]*?\n  \}",
            r"function summaryRow\(label, value\) \{[\s\S]*?\n  \}",
            r"function readSettings\(\) \{[\s\S]*?\n  \}",
            r"function renderSummary\(\) \{[\s\S]*?\n  \}",
        ]
        corpo = []
        for padrao in ancoras:
            m = re.search(padrao, self.fonte)
            if m is None:
                raise AssertionError(f"ancora ausente em ajustes.js: {padrao}")
            corpo.append(m.group(0))

        script = (
            "const valores = " + json.dumps({
                "#min-duration": "30", "#max-duration": "60", "#target-duration": "42",
                "#min-score": "0", "#min-gap": "6", "#auto-margin": "15",
                "#auto-ceiling": "200", "#max-grace": "30", "#beam-size": "1",
                "#font-size": "", "#crf": "20", "#target-lufs": "-14",
                "#workers": "2", "#headline-seconds": "3", "#engine": "hybrid",
                "#whisper-model": "small", "#language": "",
                "#cache-dir": "x", "#layout": "focus",
                "#caption-preset": "karaoke", "#caption-style": "karaoke",
            }) + ";\n"
            "const toggles = " + json.dumps({
                "#vad-filter": True, "#transcript-cache": True, "#headline-on": False,
                "#progress-bar-on": False, "#jump-cut": False, "#loudnorm": True,
            }) + ";\n"
            # Um seletor fora destas listas devolve null: e assim que um acesso
            # remanescente a '#ranker-*' vira erro em vez de passar batido.
            "const document = {\n"
            "  querySelector: (s) => {\n"
            "    if (s === '#summary-list') return { set innerHTML(_){}, appendChild(){} };\n"
            "    if (s in valores) return { value: valores[s], classList:{ contains: () => false } };\n"
            "    if (s in toggles) return { value:'', classList:{ contains: () => toggles[s] } };\n"
            "    return null;\n"
            "  },\n"
            "  createElement: () => ({ style:{}, textContent:'', append(){}, setAttribute(){} }),\n"
            "};\n"
            "const $ = (s) => document.querySelector(s);\n"
            "const $$ = () => [];\n"
            + "\n".join(corpo) + "\n"
            "console.log(JSON.stringify(" + expressao + "));\n"
        )
        proc = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            # O stderr do node sai assim:
            #   [eval]:21
            #   <codigo da linha>
            #                      ^
            #   TypeError: Cannot read properties of null ...
            #   <stack>
            #   Node.js v22.22.2        <- rodape, sempre a ultima
            # A linha util e a que comeca com o nome do erro.
            linhas = [l.strip() for l in proc.stderr.splitlines() if l.strip()]
            erro = next(
                (l for l in linhas if re.match(r"^[A-Za-z]*Error\b", l)),
                linhas[0] if linhas else "sem stderr",
            )
            raise AssertionError("ajustes.js estourou: " + erro)
        return json.loads(proc.stdout.strip())

    def test_the_script_reads_no_control_that_left_the_page(self):
        # O caso que reproduz o bug. Se qualquer `$('#ranker-*')` sobreviver no
        # JS, o DOM falso devolve null e isto estoura.
        chaves = self._executa("Object.keys(readSettings())")
        self.assertEqual(len(chaves), len(server.AJUSTES_KEYS))
        self.assertEqual([k for k in chaves if k.startswith("ranker")], [])

    def test_the_script_still_collects_every_remaining_key(self):
        # Nao basta nao estourar: as chaves que sobraram tem de continuar sendo
        # coletadas, senao a remocao teria levado campo junto.
        chaves = self._executa("Object.keys(readSettings())")
        self.assertEqual(sorted(chaves), sorted(server.AJUSTES_KEYS))

    def test_the_summary_renders_without_the_ranker_fields(self):
        # renderSummary() lia `s.ranker` e `s.ranker_provider`, que deixaram de
        # existir. Chave ausente em JS nao estoura sozinha, mas o teste tambem
        # cobre o caminho de render inteiro.
        self.assertEqual(self._executa("(renderSummary(), 'ok')"), "ok")


class AjustesLufsTests(unittest.TestCase):
    """The loudness field, which never reached the engine before."""

    def test_the_old_spelling_still_works(self):
        # A cached page (or any other caller) sending the old name must not be
        # silently ignored any more.
        cfg = server._options_to_config({"url": "x", "lufs": -9.5})
        self.assertEqual(cfg.target_lufs, -9.5)

    def test_the_real_field_name_works(self):
        cfg = server._options_to_config({"url": "x", "target_lufs": -9.5})
        self.assertEqual(cfg.target_lufs, -9.5)

    def test_the_new_name_wins_when_both_are_sent(self):
        cfg = server._options_to_config({"url": "x", "lufs": -9.5, "target_lufs": -20.0})
        self.assertEqual(cfg.target_lufs, -20.0)

    def test_a_string_is_coerced(self):
        cfg = server._options_to_config({"url": "x", "target_lufs": "-9.5"})
        self.assertEqual(cfg.target_lufs, -9.5)


class AjustesAutomaticModeTests(unittest.TestCase):
    """The three knobs the automatic mode added, as strings."""

    def test_they_survive_being_sent_as_strings(self):
        # auto_ceiling is compared with >= against a list length. A str would
        # raise there instead of failing validation, so it has to be coerced.
        cfg = server._options_to_config({
            "url": "x",
            "auto_ceiling": "250",
            "auto_margin": "20",
            "max_duration_grace": "45",
        })
        self.assertEqual(cfg.auto_ceiling, 250)
        self.assertIsInstance(cfg.auto_ceiling, int)
        self.assertEqual(cfg.auto_margin, 20.0)
        self.assertEqual(cfg.max_duration_grace, 45.0)

    def test_the_grace_widens_the_hard_ceiling(self):
        cfg = server._options_to_config({"url": "x", "max_duration": 60, "max_duration_grace": 30})
        self.assertEqual(cfg.hard_max_duration, 90.0)


class StudioTranscriptTests(unittest.TestCase):
    """O texto colado e' lido do formulario do Estudio, nao de ajustes.toml.

    A funcao e' extraida do index.js real e chamada com um DOM falso -- sem
    copia da logica, o teste exercita o arquivo que roda no navegador. O que ele
    trava: o texto do campo entra no payload, e campo vazio vira `null`
    (Whisper), nunca string vazia.
    """

    @classmethod
    def setUpClass(cls):
        cls.fonte = (server.WEB_DIR / "index.js").read_text(encoding="utf-8")
        m = re.search(r"function transcriptForThisRun\(\) \{[\s\S]*?\n  \}", cls.fonte)
        if m is None:
            raise AssertionError(
                "transcriptForThisRun() sumiu do index.js. Se a funcao foi "
                "renomeada, renomeie esta ancora junto -- um match que devolve "
                "None e um teste que nao testa nada."
            )
        cls.corpo = m.group(0)

    def le(self, valor):
        # O node faz o papel do navegador: extrai a funcao do disco e a chama.
        # Sem node, o teste e' pulado em vez de virar verde por acidente.
        node = shutil.which("node")
        if not node:
            self.skipTest("node nao esta no PATH")

        script = (
            "const fn = new Function('$', %s + "
            "'\\nreturn transcriptForThisRun();');" % json.dumps(self.corpo)
            + "\nconst $ = () => ({ value: %s });" % json.dumps(valor)
            + "\nconsole.log(JSON.stringify(fn($)));"
        )
        proc = subprocess.run(
            [node, "-e", script], capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise AssertionError("index.js falhou ao rodar: " + proc.stderr.strip())
        return json.loads(proc.stdout.strip())

    TEXTO = "0:03\nAbertura chocante."

    def test_the_field_goes_into_the_run(self):
        self.assertEqual(self.le(self.TEXTO), self.TEXTO)

    def test_an_empty_field_means_whisper(self):
        # Vazio = `null`, e `null` = o motor transcreve. NUNCA string vazia: ela
        # viraria uma transcricao de zero falas e faria o pipeline pular o
        # Whisper em silencio.
        self.assertIsNone(self.le(""))
        self.assertIsNone(self.le("   \n  "))


class AjustesPayloadTests(unittest.TestCase):
    """What GET /api/ajustes hands to the Cortes page."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "ajustes.toml"
        self._patch = mock.patch.object(server, "AJUSTES_PATH", self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_it_carries_the_curator_prompt_path(self):
        # index.js has to forward this into the run payload, and it is not on
        # the page, so it has to come from here or not at all.
        payload = server._ajustes_payload()
        self.assertIn("curator_prompt_file", payload)
        self.assertTrue(payload["curator_prompt_file"].endswith("curador.txt"))

    def test_it_is_empty_before_the_first_save(self):
        # One key rides along without being a setting: the curator prompt path
        # (absent from AJUSTES_KEYS on purpose). Everything else is empty until
        # something is saved.
        payload = server._ajustes_payload()
        self.assertEqual(
            {k: v for k, v in payload.items() if k != "curator_prompt_file"},
            {},
        )

    def test_the_saved_values_win_over_the_defaults(self):
        self.path.write_text("min_duration = 12.5\nauto_ceiling = 40\n", encoding="utf-8")
        payload = server._ajustes_payload()
        self.assertEqual(payload["min_duration"], 12.5)
        self.assertEqual(payload["auto_ceiling"], 40)


class _FakeHandler:
    """O minimo de um handler para chamar os metodos de rota sem socket.

    ``_send_json`` e o unico ponto de saida dos handlers, entao capturar o
    payload e o status aqui e equivalente a ler a resposta HTTP -- sem abrir
    porta, sem thread, sem dormir. O ``Host``/``Origin`` nao entram: quem
    valida isso e ``_guard_origin``, no topo do ``do_POST``, e ele tem teste
    proprio.
    """

    def __init__(self, handler_cls):
        self._handler_cls = handler_cls
        self.sent = None

    def _send_json(self, payload, code=200):
        self.sent = (code, payload)

    def __getattr__(self, name):
        # Os metodos de rota sao lookups de classe; liga-los a esta instancia
        # falsa e o que faz `self._send_json` cair no capturador acima.
        attr = getattr(self._handler_cls, name)
        return attr.__get__(self, self._handler_cls)


class CuratorPromptSaveTests(unittest.TestCase):
    """``POST /prompts/curador`` — a rota que reescreve o prompt do motor.

    Esta rota ficou sem teste enquanto era justamente a que podia estragar o
    arquivo que o motor le. O estrago que ela ja fez uma vez: gravou um arquivo
    em CRLF (o versionado e LF, entao o diff virou uma linha por linha) e
    aceitou um texto que nao mencionava o JSON de resposta -- o modelo passou a
    receber um system prompt que nao descrevia a tarefa de ranquear.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "curador.txt"
        for alvo, valor in (
            ("CURATOR_PROMPT_PATH", self.path),
            ("MAX_CURATOR_PROMPT_BYTES", 64 * 1024),
        ):
            p = mock.patch.object(server, alvo, valor)
            p.start()
            self.addCleanup(p.stop)

    def _save(self, text):
        h = _FakeHandler(server.Handler)
        h._handle_save_curator_prompt({"text": text})
        return h.sent

    def test_it_writes_the_text_to_the_servers_own_path(self):
        code, payload = self._save("regras do curador\n")
        self.assertEqual(code, 200)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "regras do curador\n")
        self.assertTrue(payload["ok"])
        self.assertEqual(Path(payload["path"]), self.path)

    def test_it_never_writes_crlf(self):
        # O arquivo versionado e LF. `write_text` no Windows transformaria
        # todo `\n` em `\r\n` e o `git diff` passaria a mostrar o arquivo
        # inteiro como alterado -- foi exatamente o que aconteceu.
        self._save("linha um\nlinha dois\nlinha tres\n")
        cru = self.path.read_bytes()
        self.assertEqual(cru.count(b"\r\n"), 0, "gravou CRLF num arquivo LF")
        self.assertEqual(cru.count(b"\n"), 3)

    def test_crlf_from_the_client_is_normalised_away(self):
        # Um textarea de origem Windows pode mandar CRLF no corpo do POST.
        # Normalizar aqui e o que impede a divergencia de nascer na rota.
        self._save("linha um\r\nlinha dois\r\n")
        cru = self.path.read_bytes()
        self.assertEqual(cru.count(b"\r\n"), 0)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "linha um\nlinha dois\n")

    def test_an_empty_body_is_refused_and_writes_nothing(self):
        # `load_curator_prompt` trata arquivo vazio como fatal. Aceitar aqui
        # transformaria um clique acidental em falha dura em toda execucao
        # seguinte, apontando para um arquivo que o usuario acha que preencheu.
        code, payload = self._save("   \n\n  ")
        self.assertEqual(code, 400)
        self.assertIn("error", payload)
        self.assertFalse(self.path.exists(), "recusou mas gravou mesmo assim")

    def test_a_non_string_body_is_refused(self):
        h = _FakeHandler(server.Handler)
        h._handle_save_curator_prompt({"text": 123})
        code, _ = h.sent
        self.assertEqual(code, 400)

    def test_it_refuses_a_prompt_over_the_byte_ceiling(self):
        with mock.patch.object(server, "MAX_CURATOR_PROMPT_BYTES", 10):
            code, payload = self._save("x" * 64)
        self.assertEqual(code, 400)
        self.assertIn("error", payload)
        self.assertFalse(self.path.exists())

    def test_it_measures_the_ceiling_in_bytes_not_characters(self):
        # Um acento custa 2 bytes em UTF-8. Contar caracteres deixaria passar
        # um arquivo que estoura o limite real de disco.
        with mock.patch.object(server, "MAX_CURATOR_PROMPT_BYTES", 10):
            code, _ = self._save("á" * 6)  # 12 bytes, 6 caracteres
        self.assertEqual(code, 400)

    def test_it_creates_the_parent_directory(self):
        self.path = self.tmp / "prompts" / "curador.txt"
        with mock.patch.object(server, "CURATOR_PROMPT_PATH", self.path):
            code, _ = self._save("conteudo\n")
        self.assertEqual(code, 200)
        self.assertTrue(self.path.is_file())

    def test_the_read_route_reports_the_same_path_this_route_writes(self):
        # Se o GET que a pagina usa para "Carregar do arquivo" e o POST usarem
        # caminhos diferentes, o usuario edita um arquivo e o motor le outro.
        self.assertEqual(
            server._curator_prompt_payload()["path"], str(server.CURATOR_PROMPT_PATH)
        )

    def test_the_read_route_does_not_pretend_a_missing_file_exists(self):
        payload = server._curator_prompt_payload()
        self.assertFalse(payload["exists"])
        self.assertEqual(payload["text"], "")
        # O caminho vai junto mesmo assim: o painel mostra "salve para criar",
        # que e uma instrucao diferente de "edite".
        self.assertTrue(payload["path"])


class CaptionGalleryTests(unittest.TestCase):
    """O preset de legenda e' escolhido numa GALERIA, nao numa lista de 36.

    O campo e' o unico do painel com 36 opcoes, e cada opcao carrega a propria
    previa — a frase de exemplo desenhada com a fonte, a cor, a caixa e o
    destaque do preset. Empilhadas no painel de lista (290px de altura), cabiam
    TRES por vez: escolher era rolar 33 previas as cegas. Em grade, as 36
    aparecem juntas e a decisao vira COMPARACAO — que e' como o mercado resolve
    (Opus Clip, Submagic).

    O que NAO mudou: o `<select id="caption-preset">` continua no HTML e
    continua sendo a FONTE DA VERDADE do valor. O `readSettings()` do
    `ajustes.js` le `$('#caption-preset').value`, e `AJUSTES_KEYS` nao sabe que
    existe uma grade. A galeria e' uma camada de apresentacao por cima — e e'
    por isso que a paridade controle<->chave de `AjustesPageTests` continua
    valendo sem uma linha de excecao.
    """

    @classmethod
    def setUpClass(cls):
        cls.comum = page_source("comum.js")
        cls.css = page_source("index.css")
        cls.html = page_source("ajustes.html")

    @staticmethod
    def _sem_comentarios_css(texto: str) -> str:
        return re.sub(r"/\*.*?\*/", "", texto, flags=re.S)

    def test_the_caption_field_is_still_a_native_select(self):
        # A fonte da verdade do valor. Se ele virar um monte de botoes, o
        # `readSettings()` perde o `$('#caption-preset').value` e a paridade
        # controle<->chave cai junto.
        m = re.search(
            r'<select[^>]*id="caption-preset"[^>]*>(.*?)</select>', self.html, re.S)
        self.assertIsNotNone(m, "o select do preset de legenda sumiu")
        opcoes = re.findall(r"<option\b", m.group(1))
        self.assertEqual(len(opcoes), 36, "o numero de presets mudou")

    def test_the_component_builds_a_gallery_for_that_field(self):
        # A grade e a busca sao construidas no `enhanceSelect`, e so' para este
        # campo. Nao ha uma segunda copia da lista de presets em lugar nenhum.
        self.assertIn("isCaptionPreset", self.comum)
        self.assertIn("cap-gallery", self.comum)
        self.assertIn("cap-search", self.comum)

    def test_the_gallery_panel_is_a_dialog_with_a_listbox_inside(self):
        # `listbox` so' admite filhos `option`/`group` — e a galeria tem um
        # campo de busca proprio. O painel vira `dialog` e a lista de opcoes
        # vive num `listbox` DENTRO dele, para o leitor de tela continuar
        # anunciando "opcao N de 36" na hora de escolher.
        self.assertIn("'dialog'", self.comum)
        self.assertIn("'cap-list'", self.comum)
        self.assertIn("'listbox'", self.comum)

    def test_the_stylesheet_grids_the_gallery(self):
        folha = self._sem_comentarios_css(self.css)
        m = re.search(r"\.cap-list\s*\{([^}]*)\}", folha)
        self.assertIsNotNone(m, "o `.cap-list` sumiu do index.css")
        self.assertIn("grid-template-columns", m.group(1),
                      "a galeria voltou a ser uma lista de uma coluna")

    def test_the_gallery_does_not_leak_into_the_other_sheets(self):
        # A classe e' do componente compartilhado; uma segunda declaracao num
        # css de pagina seria a copia que diverge.
        for nome in ("scrap.css", "shared.css"):
            with self.subTest(sheet=nome):
                self.assertNotIn(".cap-gallery",
                                 self._sem_comentarios_css(page_source(nome)))


class AjustesResumoActionsTests(unittest.TestCase):
    """As duas acoes do Resumo: um CTA primario, e o caminho de volta.

    * **UM CTA primario por tela.** O Salvar do Resumo era `btn-primary` como o
      do cabecalho. Dois botoes primarios identicos na mesma tela nao dizem
      qual e' o caminho — dizem que os dois sao, e a pessoa para para escolher.
    * **"Restaurar padroes"** e' a saida que faltava: sem ela, voltar aos
      valores de fabrica so' era possivel editando o `ajustes.toml` a mao.
      Os padroes sao lidos do DOM (`snapshotForm`) e nao de uma segunda lista:
      uma lista paralela divergiria do markup na primeira vez que alguem
      mexesse num `value=`, e a divergencia so' apareceria no dia do clique.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = page_source("ajustes.html")
        cls.js = page_source("ajustes.js")

    def test_only_the_header_save_is_primary(self):
        primarios = re.findall(
            r'<button[^>]*class="[^"]*\bbtn-primary\b[^"]*"[^>]*id="([^"]+)"', self.html)
        self.assertEqual(primarios, ["btn-save"],
                         "a pagina voltou a ter mais de um CTA primario")

    def test_the_side_save_is_still_there(self):
        self.assertRegex(self.html, r'<button[^>]*id="btn-save-side"',
                         "o Salvar do Resumo sumiu")

    def test_there_is_a_restore_action(self):
        self.assertRegex(self.html, r'<button[^>]*id="btn-restore-defaults"',
                         "o 'Restaurar padroes' sumiu da pagina")

    def test_the_restore_action_has_a_handler(self):
        # Um `<button>` sem handler fica na tela e nao faz nada — e parece
        # funcionar. E' o par obrigatorio de todo botao deste projeto.
        self.assertIn("'#btn-restore-defaults'", self.js,
                      "o 'Restaurar padroes' nao tem handler no ajustes.js")

    def test_the_defaults_come_from_the_dom_not_from_a_second_list(self):
        self.assertIn("function snapshotForm()", self.js,
                      "os padroes deixaram de ser lidos do DOM")
        self.assertIn("const PADROES = snapshotForm();", self.js,
                      "o snapshot dos padroes nao e' mais capturado")


class AjustesAdvancedDisclosureTests(unittest.TestCase):
    """Os ajustes de INFRA ficam atras de um disclosure nativo.

    A secao pede uma decisao por linha; dar aos ajustes de infra (cache de
    transcricao, workers paralelos, sobreposicoes) o mesmo peso das decisoes
    editoriais faz a pagina parecer exigir escolhas que ela nao exige. Eles vao
    para tras de um `<details>` — o disclosure NATIVO, que abre por teclado, e'
    anunciado como "expandir" pelo leitor de tela e nao custa JS.

    O que NAO pode acontecer — e o que este teste trava — e' o disclosure virar
    um jeito de ESCONDER campo do formulario. Os controles continuam no DOM e
    continuam no contrato (`CONTROLES`), com os valores que o autosave le. Se
    alguem trocar o `<details>` por remocao de markup, `AjustesPageTests` cai
    junto — mas este teste diz por que.
    """

    #: Os controles que o disclosure guarda.
    ESCONDIDOS = (
        "auto-margin", "auto-ceiling", "max-grace",
        "cache-dir", "vad-filter", "transcript-cache",
        "workers", "headline-seconds", "headline-on", "progress-bar-on", "jump-cut",
    )

    #: Os controles que decidem o RESULTADO e ficam a vista.
    A_VISTA = (
        "min-duration", "max-duration", "target-duration", "min-score",
        "min-gap", "engine", "whisper-model", "language", "beam-size",
        "layout", "caption-preset", "caption-style", "font-size", "crf",
        "target-lufs", "loudnorm",
    )

    @classmethod
    def setUpClass(cls):
        cls.html = page_source("ajustes.html")

    def _blocos(self) -> list[str]:
        """Cada `<details class="avancado">…</details>`, na ordem do documento."""
        return re.findall(r'<details class="avancado">.*?</details>', self.html, re.S)

    def test_there_is_one_disclosure_per_card(self):
        self.assertEqual(len(self._blocos()), 3,
                         "o numero de disclosures mudou (era um por card)")

    def test_every_disclosure_has_a_label(self):
        for i, bloco in enumerate(self._blocos()):
            with self.subTest(bloco=i):
                self.assertRegex(bloco, r"<summary>[^<]+</summary>",
                                 "disclosure sem rotulo: nao ha como abri-lo")

    def test_the_advanced_controls_are_inside_a_disclosure(self):
        corpo = "".join(self._blocos())
        for cid in self.ESCONDIDOS:
            with self.subTest(id=cid):
                self.assertIn('id="' + cid + '"', corpo,
                              f"`{cid}` saiu do disclosure (ou o disclosure saiu da pagina)")

    def test_the_editorial_controls_stay_visible(self):
        # O outro lado: o que decide o resultado nao pode ir para tras de um
        # clique. Duracao, motor, legendas e loudness ficam a vista.
        corpo = "".join(self._blocos())
        for cid in self.A_VISTA:
            with self.subTest(id=cid):
                self.assertNotIn('id="' + cid + '"', corpo,
                                 f"`{cid}` foi para dentro do disclosure")

    def test_the_disclosure_never_removes_a_field_from_the_contract(self):
        for cid in self.ESCONDIDOS:
            with self.subTest(id=cid):
                self.assertIn(cid, CONTROLES,
                              f"`{cid}` saiu do contrato: o disclosure virou remocao")


if __name__ == "__main__":
    unittest.main()
