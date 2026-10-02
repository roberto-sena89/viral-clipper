"""O contrato do modo automatico: ``count = 0`` e o padrao.

Estes testes existem porque um teste de mutacao mostrou que as formas mais
faceis de quebrar o modo automatico passavam em silencio: voltar ``count`` para
5, apagar as validacoes dos campos novos, ou reverter a validacao de ``count``
para "pelo menos 1". Nenhum deles derrubava assercao alguma em 2026-10-02 —
o motor estava correto e desprotegido ao mesmo tempo.

A distincao que importa: os outros testes fixam o *comportamento* de
``pick_windows`` e ``build_candidates`` com valores explicitos. Aqui fixa-se o
*default* e o *contrato* — o que acontece quando ninguem passa nada.
"""

from __future__ import annotations

import unittest

from tests._fixtures import make_config
from viralclipper import config as config_mod


class AutomaticModeIsTheDefaultTests(unittest.TestCase):
    """O default tem de ser o automatico, nao um numero calibrado a mao."""

    def test_count_defaults_to_zero(self):
        """``0`` significa "o video decide"; era ``5`` antes do modo automatico."""
        self.assertEqual(config_mod.ClipConfig.__dataclass_fields__["count"].default, 0)

    def test_a_config_with_no_count_argument_is_automatic(self):
        # make_config usa os defaults reais, entao isto prova o caminho que a
        # CLI e o painel percorrem quando ninguem pede uma contagem.
        self.assertEqual(make_config().count, 0)

    def test_the_automatic_knobs_have_the_documented_defaults(self):
        config = make_config()
        self.assertEqual(config.auto_margin, 15.0)
        self.assertEqual(config.auto_ceiling, 200)
        self.assertEqual(config.max_duration_grace, 30.0)

    def test_the_default_config_validates(self):
        """O default tem de passar pela propria validacao."""
        make_config().validate()


class AutomaticModeValidationTests(unittest.TestCase):
    """Cada campo novo recusa o valor que o quebraria."""

    def test_a_negative_count_is_refused(self):
        with self.assertRaises(ValueError):
            make_config(count=-1).validate()

    def test_zero_count_is_accepted(self):
        # A validacao antiga exigia >= 1 e mataria o modo automatico.
        make_config(count=0).validate()

    def test_a_negative_margin_is_refused(self):
        with self.assertRaises(ValueError):
            make_config(auto_margin=-1).validate()

    def test_a_ceiling_below_one_is_refused(self):
        # ``auto_ceiling`` e comparado com ``>=`` contra o tamanho da lista: um
        # zero significaria "nenhum corte", e um negativo, nada.
        with self.assertRaises(ValueError):
            make_config(auto_ceiling=0).validate()
        with self.assertRaises(ValueError):
            make_config(auto_ceiling=-5).validate()

    def test_a_negative_grace_is_refused(self):
        with self.assertRaises(ValueError):
            make_config(max_duration_grace=-1).validate()

    def test_the_error_messages_name_the_field(self):
        """Uma mensagem que nao diz qual campo falhou obriga a ler o codigo."""
        for kwargs in ({"count": -1}, {"auto_margin": -1}, {"auto_ceiling": 0},
                       {"max_duration_grace": -1}):
            campo = next(iter(kwargs))
            with self.subTest(campo=campo):
                with self.assertRaises(ValueError) as ctx:
                    make_config(**kwargs).validate()
                self.assertIn(campo, str(ctx.exception))


class HardMaxDurationTests(unittest.TestCase):
    """``max_duration`` e alvo; ``hard_max_duration`` e a parede."""

    def test_the_grace_widens_the_wall(self):
        self.assertEqual(
            make_config(max_duration=60.0, max_duration_grace=30.0).hard_max_duration,
            90.0,
        )

    def test_no_grace_keeps_the_wall_at_the_target(self):
        self.assertEqual(
            make_config(max_duration=60.0, max_duration_grace=0.0).hard_max_duration,
            60.0,
        )

    def test_a_negative_grace_cannot_shrink_the_wall(self):
        """O ``max(0.0, ...)`` protege mesmo se a validacao for contornada."""
        config = make_config(max_duration=60.0, max_duration_grace=-10.0)
        self.assertEqual(config.hard_max_duration, 60.0)

    def test_the_wall_is_never_below_max_duration(self):
        for grace in (0.0, 1.0, 30.0, 120.0):
            with self.subTest(grace=grace):
                config = make_config(max_duration=60.0, max_duration_grace=grace)
                self.assertGreaterEqual(config.hard_max_duration, config.max_duration)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
