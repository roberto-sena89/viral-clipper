"""Unit tests for :mod:`viralclipper.template`.

Everything asserted here is decided before ffmpeg runs: the band geometry and
the filtergraph string. That keeps the whole suite offline and lets a broken
composition be caught without encoding a frame.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from viralclipper import template as tpl
from viralclipper.util import ClipperError

from ._fixtures import make_config


def _text_template(**overrides):
    """O formato Meme: faixa de texto preta, vídeo reduzido, barra de identidade.

    As três frações somam 1.00 exatamente — é a mesma divisão que o wizard
    carrega da galeria, e é por isso que o vídeo é 0.58 e não o 0.74 de antes:
    a faixa de texto ocupa altura de verdade.
    """
    fields = {
        "text": "POV: voce usou o formato de meme e viralizou",
        "fraction": 0.16,
        "margin_top": 0.008,
        "margin_bottom": 0.008,
        "margin_left": 0.05,
        "margin_right": 0.05,
        "color": "black",
    }
    fields.update(overrides)
    return tpl.Template(
        name="meme",
        zones=(
            tpl.Zone(kind="text", **fields),
            tpl.Zone(kind="video", fraction=0.58),
            tpl.Zone(kind="image", fraction=0.26, source="id.png"),
        ),
    )


def _text_band(**overrides):
    """A faixa (com margens já em pixels) da zona de texto do formato Meme."""
    return tpl.plan_bands(_text_template(**overrides), 1080, 1920)[0]


class ZoneValidationTests(unittest.TestCase):
    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.Zone(kind="hologram", fraction=1.0).validate(1)
        self.assertIn("hologram", str(ctx.exception))

    def test_fraction_outside_the_band_is_refused(self):
        with self.assertRaises(ClipperError):
            tpl.Zone(kind="video", fraction=0.005).validate(1)
        with self.assertRaises(ClipperError):
            tpl.Zone(kind="video", fraction=1.5).validate(1)

    def test_an_image_zone_may_open_without_an_asset(self):
        """Zona `image` sem `source` é estado VÁIDO, não erro de digitação.

        É assim que um formato da galeria abre: a barra de identidade do Meme e o
        print do X nascem sem arquivo, e o renderer degrada a faixa para o próprio
        clipe. Recusar no `validate` travava o wizard antes de o usuário escolher
        o asset — e o aviso "precisa de um caminho" não tinha correção possível,
        porque o caminho é opcional por definição.
        """
        for source in (None, ""):
            with self.subTest(source=source):
                # Não levanta: o ponto do teste é justamente não levantar.
                tpl.Zone(kind="image", fraction=0.4, source=source).validate(2)

    def test_a_zone_of_another_kind_still_needs_nothing_hidden(self):
        """Só a `image` ficou sem exigência; as demais seguem com a de sempre."""
        with self.assertRaises(ClipperError):
            tpl.Zone(kind="hologram", fraction=1.0).validate(1)

    def test_margins_may_not_swallow_the_band(self):
        with self.assertRaises(ClipperError):
            tpl.Zone(kind="video", fraction=1.0, margin_top=0.6, margin_bottom=0.5).validate(1)

    def test_text_zone_without_words_is_refused(self):
        # Faixa preta sem nada é indistinguível de um defeito no renderizador: o
        # usuário olharia o vídeo, veria um retângulo preto e não saberia se o
        # texto sumiu ou se ele nunca foi escrito.
        for empty in ("", "   ", "\n\t "):
            with self.subTest(text=repr(empty)):
                with self.assertRaises(ClipperError) as ctx:
                    tpl.Zone(kind="text", fraction=1.0, text=empty).validate(1)
                self.assertIn("text", str(ctx.exception))

    def test_text_zone_refuses_a_size_that_would_not_fit(self):
        # `text_size` é fração da ALTURA do quadro (medida vertical). Acima de
        # 0.4 uma linha sozinha já não cabe na faixa; zero é texto invisível.
        for size in (0.0, 0.4 + 1e-9, 0.9, -0.1):
            with self.subTest(size=size):
                with self.assertRaises(ClipperError):
                    tpl.Zone(kind="text", fraction=1.0, text="oi", text_size=size).validate(1)

    def test_text_zone_refuses_an_unknown_alignment(self):
        # O alinhamento vira o dígito do `\an`: um valor fora da lista não tem
        # âncora, e o libass desenharia no canto por conta própria.
        with self.assertRaises(ClipperError) as ctx:
            tpl.Zone(kind="text", fraction=1.0, text="oi", text_align="justify").validate(1)
        self.assertIn("justify", str(ctx.exception))
        with self.assertRaises(ClipperError) as ctx:
            tpl.Zone(kind="text", fraction=1.0, text="oi", text_valign="baseline").validate(1)
        self.assertIn("baseline", str(ctx.exception))

    def test_text_zone_refuses_an_offset_beyond_the_canvas(self):
        # O deslocamento é fração do quadro: 1.0 já é o quadro inteiro. Passar
        # disso é sempre engano de unidade (px colado no campo de fração).
        for axis in ("text_dx", "text_dy"):
            with self.subTest(axis=axis):
                with self.assertRaises(ClipperError):
                    tpl.Zone(kind="text", fraction=1.0, text="oi", **{axis: 1.5}).validate(1)

    def test_text_zone_refuses_a_negative_outline(self):
        with self.assertRaises(ClipperError):
            tpl.Zone(kind="text", fraction=1.0, text="oi", text_outline=-0.01).validate(1)


class TemplateValidationTests(unittest.TestCase):
    def test_zones_must_sum_to_one(self):
        broken = tpl.Template(
            name="broken",
            zones=(
                tpl.Zone(kind="video", fraction=0.6),
                tpl.Zone(kind="frame", fraction=0.3),
            ),
        )
        with self.assertRaises(ClipperError) as ctx:
            broken.validate()
        self.assertIn("0.9000", str(ctx.exception))

    def test_float_rounding_does_not_break_the_sum(self):
        # 0.55 + 0.45 is not exactly 1.0 in binary floating point; the check
        # has to tolerate that or a hand-written template fails for no reason.
        tpl.Template(
            name="ok",
            zones=(
                tpl.Zone(kind="video", fraction=0.55),
                tpl.Zone(kind="frame", fraction=0.45),
            ),
        ).validate()

    def test_caption_zone_must_come_last(self):
        bad = tpl.Template(
            name="bad",
            zones=(
                tpl.Zone(kind="captions", fraction=0.0),
                tpl.Zone(kind="video", fraction=1.0),
            ),
        )
        with self.assertRaises(ClipperError) as ctx:
            bad.validate()
        self.assertIn("ultima", str(ctx.exception))

    def test_only_one_video_zone(self):
        bad = tpl.Template(
            name="bad",
            zones=(
                tpl.Zone(kind="video", fraction=0.5),
                tpl.Zone(kind="video", fraction=0.5),
            ),
        )
        with self.assertRaises(ClipperError):
            bad.validate()

    def test_empty_template_is_refused(self):
        with self.assertRaises(ClipperError):
            tpl.Template(name="empty", zones=()).validate()

    def test_a_text_zone_counts_in_the_sum(self):
        # A faixa de texto é PIXEL: o motor pinta a placa, então ela ocupa altura
        # como qualquer outra zona. Só `captions` fica de fora da soma — quem a
        # posiciona é o libass, não o compositor. Deixá-la fora aqui faria a faixa
        # preta comer a altura do vídeo sem que ninguém reclamasse.
        _text_template().validate()  # 0.16 + 0.58 + 0.26 = 1.00, não levanta
        short = tpl.Template(
            name="curto",
            zones=(
                tpl.Zone(kind="text", fraction=0.16, text="oi"),
                tpl.Zone(kind="video", fraction=0.58),
            ),
        )
        with self.assertRaises(ClipperError) as ctx:
            short.validate()
        self.assertIn("0.7400", str(ctx.exception))


class PlanBandsTests(unittest.TestCase):
    def test_full_frame_is_exactly_the_canvas(self):
        bands = tpl.plan_bands(tpl.FULL_FRAME, 1080, 1920)
        self.assertEqual(len(bands), 1)
        band = bands[0]
        self.assertEqual((band.y, band.height), (0, 1920))
        self.assertEqual((band.inner_width, band.inner_height), (1080, 1920))

    def test_bands_tile_the_canvas_without_a_seam(self):
        for height in (1920, 1080, 1280, 999):
            bands = [b for b in tpl.plan_bands(tpl.SPLIT_CARD, 1080, height) if b.kind != "captions"]
            covered = 0
            for band in bands:
                self.assertEqual(band.y, covered, f"gap before {band.kind} at h={height}")
                covered += band.height
            self.assertEqual(covered, height, f"bands do not reach the bottom at h={height}")

    def test_a_non_integral_split_still_tiles(self):
        # 1080 * 0.62 does not round cleanly; the leftover pixel has to land
        # somewhere rather than becoming a one-pixel black seam.
        odd = tpl.Template(
            name="odd",
            zones=(
                tpl.Zone(kind="video", fraction=0.333),
                tpl.Zone(kind="frame", fraction=0.333),
                tpl.Zone(kind="solid", fraction=0.334),
            ),
        )
        bands = tpl.plan_bands(odd, 1080, 1920)
        self.assertEqual(sum(b.height for b in bands), 1920)
        cursor = 0
        for band in bands:
            self.assertEqual(band.y, cursor)
            cursor += band.height

    def test_margins_shrink_the_inner_rectangle_in_pixels(self):
        band = tpl.plan_bands(tpl.SPLIT_CARD, 1080, 1920)[1]
        # margin_left 0.03 of 1080 = 32px, and the same on the right.
        self.assertEqual(band.inner_x, 32)
        self.assertEqual(band.inner_width, 1080 - 64)

    def test_captions_band_is_full_canvas_and_last(self):
        bands = tpl.plan_bands(tpl.SPLIT_CARD, 1080, 1920)
        self.assertEqual(bands[-1].kind, "captions")
        self.assertEqual(bands[-1].height, 1920)


def _produced_size(fragment: str) -> tuple[int, int]:
    """O tamanho que o fragmento do still realmente produz.

    ``contain`` termina em ``pad=W:H``, ``cover`` em ``crop=W:H`` (o ``crop`` é
    quem decide, porque ele é o último a mexer no tamanho) e um ``cover`` sem
    crop fica no próprio ``scale=W:H``.
    """
    match = re.search(r"(?:crop|pad)=(\d+):(\d+)", fragment)
    if match is None:
        match = re.search(r"scale=(\d+):(\d+)", fragment)
    return int(match.group(1)), int(match.group(2))


def _mask_size(fragment: str) -> tuple[int, int]:
    match = re.search(r"s=(\d+)x(\d+)", fragment)
    return int(match.group(1)), int(match.group(2))


class ChromaGridTests(unittest.TestCase):
    """Todo retângulo que o motor desenha cai na grade do yuv420p.

    O composite é yuv420p, então as duas plantas de croma têm metade da
    resolução. Um retângulo que começa ou termina em linha ímpar sangra uma linha
    da própria cor no vizinho de cima ou de baixo — em TODA fronteira do
    empilhamento, não só na última — e uma largura ímpar trunca a última coluna
    desenhada. Com offsets, alturas e medidas internas pares o composite sai
    exato. A medição que estabeleceu isso está em ``band_parity_check.py``, que
    precisa de ffmpeg; aqui a mesma regra é travada sem binário nenhum.
    """

    CANVASES = ((1080, 1920), (720, 1280), (1080, 1080), (1920, 1080), (608, 1080))

    def _templates(self):
        yield from tpl.BUILTIN.values()
        # Frações que arredondam para ÍMPAR de propósito, e empilhamentos de 1, 3
        # e 5 zonas: a regra não pode depender do tamanho do empilhamento. As
        # margens também são ímpares em pixels (0,045 de 1080 são 48,6; 0,007 de
        # 1920 são 13,4) — é justamente onde o arredondamento erraria.
        yield tpl.Template(
            name="uma-zona",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
        )
        yield tpl.Template(
            name="tres-zonas",
            zones=(
                tpl.Zone(
                    kind="text", fraction=0.333, text="POV: teste", color="black",
                    margin_top=0.007, margin_bottom=0.007,
                    margin_left=0.045, margin_right=0.045,
                ),
                tpl.Zone(kind="video", fraction=0.333),
                tpl.Zone(
                    kind="image", fraction=0.334, source="id.png",
                    margin_top=0.013, margin_bottom=0.013,
                    margin_left=0.031, margin_right=0.031,
                ),
            ),
        )
        yield tpl.Template(
            name="cinco-zonas",
            zones=(
                tpl.Zone(kind="solid", fraction=0.15, color="#111111"),
                tpl.Zone(kind="video", fraction=0.41),
                tpl.Zone(kind="solid", fraction=0.11, color="#222222"),
                tpl.Zone(
                    kind="frame", fraction=0.21,
                    margin_top=0.009, margin_bottom=0.009,
                ),
                tpl.Zone(
                    kind="image", fraction=0.12, source="id.png",
                    margin_top=0.011, margin_bottom=0.011,
                ),
            ),
        )
        yield tpl.Template(
            name="com-legenda",
            zones=(
                tpl.Zone(kind="video", fraction=0.57),
                tpl.Zone(
                    kind="image", fraction=0.43, source="id.png",
                    margin_top=0.015, margin_bottom=0.015,
                ),
                tpl.Zone(kind="captions", fraction=0.0),
            ),
        )

    def test_every_band_lands_on_the_chroma_grid(self):
        for template in self._templates():
            for width, height in self.CANVASES:
                for band in tpl.plan_bands(template, width, height):
                    if band.kind == "captions":
                        continue
                    with self.subTest(
                        template=template.name, canvas=f"{width}x{height}", kind=band.kind
                    ):
                        for axis in (
                            "y", "height", "inner_x", "inner_y",
                            "inner_width", "inner_height",
                        ):
                            self.assertEqual(
                                getattr(band, axis) % 2,
                                0,
                                f"{axis} ímpar em {template.name} @ {width}x{height}",
                            )

    def test_the_inner_box_never_leaves_its_band(self):
        """O pixel de arredondamento sai da MARGEM, nunca da imagem.

        Se o retângulo interno passasse do fim da faixa, a última linha
        desenhada cairia na zona de baixo e o defeito voltaria por outro
        caminho — por isso a folga é tirada do gutter, que é o que uma margem
        significa.
        """
        for template in self._templates():
            for width, height in self.CANVASES:
                for band in tpl.plan_bands(template, width, height):
                    if band.kind == "captions":
                        continue
                    with self.subTest(
                        template=template.name, canvas=f"{width}x{height}", kind=band.kind
                    ):
                        self.assertGreaterEqual(band.inner_x, 0)
                        self.assertLessEqual(band.inner_x + band.inner_width, width)
                        self.assertGreaterEqual(band.inner_y, band.y)
                        self.assertLessEqual(
                            band.inner_y + band.inner_height, band.y + band.height
                        )

    def test_the_grid_does_not_cost_the_zone_more_than_a_pixel(self):
        """Arredondar não pode encolher a zona: a folga é de no máximo 1 px.

        Uma faixa de 307 px que virasse 288 seria um bug de layout disfarçado de
        arredondamento. A última faixa com altura fica de fora: ela absorve a
        sobra do empilhamento inteiro para não deixar costura, e é o que
        ``test_the_leftover_lands_in_the_last_band`` confere.
        """
        for template in self._templates():
            for width, height in self.CANVASES:
                pixel = [
                    b for b in tpl.plan_bands(template, width, height)
                    if b.kind != "captions"
                ]
                for band in pixel[:-1]:
                    zone = band.zone
                    with self.subTest(
                        template=template.name, canvas=f"{width}x{height}", kind=band.kind
                    ):
                        self.assertLessEqual(
                            abs(band.height - round(height * zone.fraction)), 1
                        )
                for band in pixel:
                    zone = band.zone
                    with self.subTest(
                        template=template.name, canvas=f"{width}x{height}",
                        kind=band.kind, inner=True,
                    ):
                        self.assertLessEqual(
                            abs(band.inner_x - round(width * zone.margin_left)), 1
                        )
                        self.assertLessEqual(
                            abs(
                                band.inner_width
                                - (width - round(width * zone.margin_left)
                                   - round(width * zone.margin_right))
                            ),
                            1,
                        )
                        self.assertLessEqual(
                            abs(
                                band.inner_height
                                - (
                                    band.height
                                    - round(height * (zone.margin_top + zone.margin_bottom))
                                )
                            ),
                            1,
                        )

    def test_the_leftover_lands_in_the_last_band(self):
        """A sobra de arredondamento vai para a última faixa com altura.

        É o que impede uma costura de 1 px (preta, contra fundo claro) entre
        duas zonas. O preço é que a última faixa pode ficar alguns pixels maior
        que a fração pedida — mas nunca maior que o número de zonas, que é
        quanto cada arredondamento consegue deixar para trás.
        """
        for template in self._templates():
            for width, height in self.CANVASES:
                bands = tpl.plan_bands(template, width, height)
                pixel = [b for b in bands if b.kind != "captions"]
                if not pixel:
                    continue
                with self.subTest(template=template.name, canvas=f"{width}x{height}"):
                    cursor = 0
                    for band in bands:
                        if band.kind != "captions":
                            self.assertEqual(band.y, cursor)
                            cursor += band.height
                    self.assertEqual(cursor, height)
                    last = pixel[-1]
                    rest = sum(b.height for b in pixel[:-1])
                    self.assertEqual(last.height, height - rest)
                    self.assertLessEqual(
                        abs(last.height - round(height * last.zone.fraction)),
                        len(pixel),
                    )

    def test_an_odd_canvas_keeps_the_odd_pixel_in_the_last_band(self):
        """Canvas ímpar: uma faixa fica ímpar, e é a última.

        Não existe ladrilhamento de alturas pares que cubra 1079 px, então
        alguém tem de ficar com o pixel ímpar. Ele fica na ÚLTIMA faixa, que é
        onde o defeito custa menos: a borda de baixo dela é o fim do quadro, sem
        zona nenhuma abaixo para sangrar. (Um canvas ímpar nem é codificável em
        yuv420p pelo libx264 — este é o estado em que um ``--width`` torto chega
        antes de o encoder reclamar.)
        """
        for template in self._templates():
            bands = [
                b for b in tpl.plan_bands(template, 1080, 1079) if b.kind != "captions"
            ]
            if not bands:
                continue
            odd = [b for b in bands if b.height % 2]
            with self.subTest(template=template.name):
                self.assertEqual([b.kind for b in odd], [bands[-1].kind])

    def test_the_mask_is_the_same_size_as_the_still_it_masks(self):
        """A máscara e o still têm de sair do MESMO tamanho, ou o render MORRE.

        ``alphamerge`` recusa dois frames de tamanhos diferentes — "Input frame
        sizes do not match" — e derruba o filtergraph inteiro: não é uma linha
        errada, é nenhum arquivo. E é o que acontecia com altura interna ímpar,
        porque o ``crop`` do ``scale_into`` encaixa o still na grade de croma
        (1016x452) enquanto a máscara continua 1016x453.

        As duas medidas saem do mesmo :class:`~viralclipper.template.Band`, então
        o teste compara o tamanho que cada fragmento do grafo produz — e o faz
        com raio de canto em toda zona de still, que é o caminho que quebrava.
        """
        for template in self._templates():
            rounded = replace(
                template,
                zones=tuple(
                    replace(zone, corner_radius=0.04)
                    if zone.kind in {"image", "frame"}
                    else zone
                    for zone in template.zones
                ),
            )
            for width, height in self.CANVASES:
                for band in tpl.plan_bands(rounded, width, height):
                    if band.kind not in {"image", "frame"}:
                        continue
                    with self.subTest(
                        template=template.name, canvas=f"{width}x{height}", kind=band.kind
                    ):
                        still = _produced_size(tpl.scale_into(band, band.zone, "0:v"))
                        mask = _mask_size(tpl.rounded_mask(band, band.zone))
                        self.assertEqual(still, mask)


class TextZoneTests(unittest.TestCase):
    """Onde as palavras de uma zona ``text`` caem no quadro, em pixels.

    É o único número que a prévia do painel e o libass compartilham: o ``\\an``
    sozinho não diz onde o texto para (ele só diz QUAL PONTO do bloco as
    coordenadas nomeiam), então a âncora resolvida é o que precisa de trava.
    """

    # `\an` é o dígito do teclado numérico: a coluna vem do alinhamento
    # horizontal, a linha do vertical contada DE BAIXO. Trocar os dois é o erro
    # natural — e o texto passaria a crescer para o lado oposto.
    ANCHORS = {
        ("left", "bottom"): 1, ("center", "bottom"): 2, ("right", "bottom"): 3,
        ("left", "middle"): 4, ("center", "middle"): 5, ("right", "middle"): 6,
        ("left", "top"): 7, ("center", "top"): 8, ("right", "top"): 9,
    }

    def test_every_alignment_gets_its_numpad_anchor(self):
        for (align, valign), an in self.ANCHORS.items():
            with self.subTest(align=align, valign=valign):
                band = _text_band(text_align=align, text_valign=valign)
                self.assertEqual(tpl.text_anchor(band, band.zone, 1080, 1920)[2], an)

    def test_the_anchor_sits_on_the_edge_the_alignment_names(self):
        # O alinhamento nomeia a borda do RETÂNGULO INTERNO: é ele que o texto
        # encosta. Ancorar na borda da faixa colaria as palavras na emenda com a
        # zona de baixo e a margem deixaria de ser goteira.
        for align, valign, x, y in (
            ("left", "top", "inner_x", "inner_y"),
            ("right", "bottom", "right", "bottom"),
            ("center", "middle", "center_x", "center_y"),
        ):
            with self.subTest(align=align, valign=valign):
                band = _text_band(text_align=align, text_valign=valign)
                got_x, got_y, _ = tpl.text_anchor(band, band.zone, 1080, 1920)
                self.assertEqual(
                    (got_x, got_y),
                    (_edge(band, x), _edge(band, y)),
                    f"{align}/{valign} não ancorou na borda interna",
                )

    def test_the_offsets_are_a_share_of_the_canvas(self):
        # Fração do QUADRO, não da faixa: é a unidade que sobrevive a mudar a
        # altura da faixa, e é a que os campos em px do painel convertem.
        base = _text_band()
        moved = _text_band(text_dx=0.1, text_dy=-0.05)
        x0, y0, _ = tpl.text_anchor(base, base.zone, 1080, 1920)
        x1, y1, _ = tpl.text_anchor(moved, moved.zone, 1080, 1920)
        self.assertEqual(x1 - x0, 108)
        self.assertEqual(y1 - y0, -96)

    def test_the_offsets_compose_with_the_alignment(self):
        # O deslocamento é um AJUSTE por cima da âncora, não um substituto: com
        # os dois eixos zerados a âncora tem de continuar onde o alinhamento diz.
        for align in ("left", "center", "right"):
            with self.subTest(align=align):
                plain = _text_band(text_align=align)
                zero = _text_band(text_align=align, text_dx=0.0, text_dy=0.0)
                self.assertEqual(
                    tpl.text_anchor(plain, plain.zone, 1080, 1920),
                    tpl.text_anchor(zero, zero.zone, 1080, 1920),
                )

    def test_the_anchor_is_whole_pixels(self):
        # `\pos` aceita fração, mas o painel mostra inteiro e a leitura em px do
        # ponto cruz compara com este número: um float aqui faria os dois
        # discordarem no último dígito.
        band = _text_band()
        for value in tpl.text_anchor(band, band.zone, 1080, 1920):
            self.assertIsInstance(value, int)

    def test_the_text_band_is_planned_like_any_other(self):
        # A zona de texto entra no empilhamento normal: a faixa tem topo e altura
        # reais e as margens viram o retângulo interno, igual ao `solid`.
        #
        # Os números são PARES: o composite é yuv420p e um retângulo que começa
        # ou termina em linha ímpar sangra uma linha da própria cor na faixa
        # vizinha. 0,16 de 1920 são 307,2 px, e a faixa desce para 306 — a conta
        # está em `test_every_band_lands_on_the_chroma_grid`.
        bands = tpl.plan_bands(_text_template(), 1080, 1920)
        self.assertEqual([b.kind for b in bands], ["text", "video", "image"])
        band = bands[0]
        self.assertEqual((band.y, band.height), (0, 306))
        self.assertEqual(band.inner_x, 54)
        self.assertEqual(band.inner_y, 14)
        # As bandas continuam ladrilhando: nada de fresta preta entre elas.
        cursor = 0
        for item in bands:
            self.assertEqual(item.y, cursor)
            cursor += item.height
        self.assertEqual(cursor, 1920)


def _edge(band, which: str) -> int:
    """A coordenada nomeada do retângulo interno da faixa, em pixels."""
    if which == "inner_x":
        return band.inner_x
    if which == "inner_y":
        return band.inner_y
    if which == "right":
        return band.inner_x + band.inner_width
    if which == "bottom":
        return band.inner_y + band.inner_height
    if which == "center_x":
        return band.inner_x + band.inner_width / 2
    return band.inner_y + band.inner_height / 2


class ComposeTests(unittest.TestCase):
    def test_full_frame_needs_no_overlay(self):
        graph, label = tpl.compose(tpl.FULL_FRAME, 1080, 1920)
        self.assertNotIn("overlay", graph)
        self.assertEqual(label, "[c0]")

    def test_split_card_stacks_video_and_still(self):
        graph, label = tpl.compose(tpl.SPLIT_CARD, 1080, 1920)
        self.assertIn("overlay", graph)
        # The 'frame' zone draws a still extracted from the clip, which is a
        # separate input, so the graph must reference [1:v] rather than nesting
        # the running composite inside its own poster.
        self.assertIn("[1:v]", graph)
        self.assertEqual(label, "[c1]")

    def test_a_still_zone_never_reads_the_running_composite(self):
        graph, _ = tpl.compose(tpl.SPLIT_CARD, 1080, 1920)
        # The frame band's scale stage must be fed by the still input, not [c0].
        self.assertNotIn("[c0]scale=", graph)

    def test_a_video_zone_below_another_zone_still_reads_the_clip(self):
        """O clipe alimenta a zona de vídeo ONDE ela estiver na pilha.

        Com a fonte vinda do composto, um vídeo ABAIXO de outra zona — o caso de
        uma faixa de texto no topo, que é a razão de a zona ``text`` existir —
        desenhava o fundo do template na própria faixa e o clipe saía do grafo: o
        render virava um retângulo colorido, sem a filmagem em lugar nenhum, e
        nada reclamava. Pior: o composto passava a alimentar dois filtros ao mesmo
        tempo, e para essa forma o split implícito do ffmpeg liga a entrada
        principal do ``overlay`` ao stream errado (medido: as linhas que o
        overlay não cobre saem com a cor do input 0, não do composto).
        """
        graph, _ = tpl.compose(_text_template(), 1080, 1920)
        # 58% de 1920 = 1114: a faixa de vídeo, e ela lê o clipe.
        self.assertIn("[0:v]scale=1080:1114", graph)
        self.assertEqual(graph.count("[0:v]"), 1)
        # O composto alimenta UM filtro (o overlay), nunca dois.
        self.assertNotIn("[c0]scale=", graph)

    def test_every_template_with_a_video_zone_uses_the_clip(self):
        # Um grafo que não referencia o clipe monta um quadro bonito sem a
        # filmagem dentro. Nada no render reclama disso — o clipe simplesmente
        # não aparece —, então a trava tem de ser aqui.
        for name, template in (("split-card", tpl.SPLIT_CARD), ("meme", _text_template())):
            with self.subTest(template=name):
                graph, _ = tpl.compose(template, 1080, 1920)
                self.assertIn("[0:v]", graph, f"{name} não usa o clipe")

    def test_image_zone_consumes_the_next_input(self):
        with_image = tpl.Template(
            name="with-image",
            zones=(
                tpl.Zone(kind="video", fraction=0.6),
                tpl.Zone(kind="image", fraction=0.4, source="logo.png"),
            ),
        )
        graph, _ = tpl.compose(with_image, 1080, 1920)
        self.assertIn("[1:v]", graph)

    def test_two_image_zones_use_two_inputs_in_order(self):
        with_two = tpl.Template(
            name="two-images",
            zones=(
                tpl.Zone(kind="video", fraction=0.5),
                tpl.Zone(kind="image", fraction=0.25, source="a.png"),
                tpl.Zone(kind="image", fraction=0.25, source="b.png"),
            ),
        )
        graph, _ = tpl.compose(with_two, 1080, 1920)
        self.assertIn("[1:v]", graph)
        self.assertIn("[2:v]", graph)

    def test_contain_fits_and_pads_cover_fills(self):
        contain = tpl.Template(
            name="contain",
            zones=(tpl.Zone(kind="image", fraction=1.0, source="l.png", fit="contain"),),
        )
        graph, _ = tpl.compose(contain, 1080, 1920)
        self.assertIn("force_original_aspect_ratio=decrease", graph)
        self.assertIn("pad=", graph)

        cover = tpl.Template(
            name="cover",
            zones=(tpl.Zone(kind="image", fraction=1.0, source="l.png", fit="cover"),),
        )
        graph, _ = tpl.compose(cover, 1080, 1920)
        self.assertIn("force_original_aspect_ratio=increase", graph)

    def test_rounded_zone_builds_a_mask(self):
        rounded = tpl.Template(
            name="rounded",
            zones=(
                tpl.Zone(kind="video", fraction=0.6),
                tpl.Zone(kind="image", fraction=0.4, source="c.png", corner_radius=0.04),
            ),
        )
        graph, _ = tpl.compose(rounded, 1080, 1920)
        self.assertIn("alphamerge", graph)
        self.assertIn("gblur", graph)
        self.assertIn("lutyuv", graph)

    def test_solid_zone_paints_a_colour(self):
        solid = tpl.Template(
            name="solid",
            zones=(
                tpl.Zone(kind="video", fraction=0.8),
                tpl.Zone(kind="solid", fraction=0.2, color="0x112233"),
            ),
        )
        graph, _ = tpl.compose(solid, 1080, 1920)
        self.assertIn("color=c=0x112233", graph)

    def test_text_zone_paints_a_plate_and_takes_no_input(self):
        # A placa é o que dá legibilidade sobre qualquer coisa, e é o MESMO
        # caminho do `solid`: as palavras entram depois, pelo libass, que é a
        # única etapa que conhece fonte. Se a zona de texto pedisse um input
        # próprio, todos os seguintes andariam um para a frente e a barra de
        # identidade passaria a ler o arquivo errado.
        graph, _ = tpl.compose(_text_template(), 1080, 1920)
        self.assertIn("color=c=black:s=972x274", graph)  # a faixa interna, não a do quadro
        self.assertEqual(graph.count("[1:v]"), 1)
        self.assertIn("[1:v]scale=1080:500", graph)  # o still da identidade, não o texto

    def test_caption_only_template_is_a_passthrough(self):
        only = tpl.Template(name="caps", zones=(tpl.Zone(kind="captions", fraction=0.0),))
        graph, label = tpl.compose(only, 1080, 1920)
        self.assertIn("null", graph)
        self.assertEqual(label, "[c0]")

    def test_the_base_plate_is_an_infinite_colour_source(self):
        # A one-frame plate (``d=1``) caps overlay at a single frame and
        # truncates the entire clip to 1 frame. The plate must run indefinitely
        # so ffmpeg's -t stays the only length authority.
        graph, _ = tpl.compose(tpl.SPLIT_CARD, 1080, 1920)
        plate = [p for p in graph.split(";") if p.startswith("color=") and "s=1080x1920" in p]
        self.assertEqual(len(plate), 1)
        self.assertNotIn(":d=", plate[0])

    def test_overlay_does_not_use_shortest(self):
        # shortest=1 would end the composite as soon as the shorter input stops
        # (the still), producing a clip that is one frame long.
        graph, _ = tpl.compose(tpl.SPLIT_CARD, 1080, 1920)
        self.assertNotIn("shortest=1", graph)

    def test_graph_brackets_are_balanced(self):
        for name in tpl.BUILTIN:
            graph, _ = tpl.compose(tpl.BUILTIN[name], 1080, 1920)
            self.assertEqual(graph.count("["), graph.count("]"), f"unbalanced in {name}")

    def test_full_frame_with_reframe_produces_crop(self):
        """Um full-frame com reframe passa a usar o crop com expressao.

        Antes do passo 1, a chave era lida so no caminho legado
        (``_layout_filter`` / ``render.py``) e sumcia no compose. Com a
        correcao, ``scale_into`` recebe ``reframe_*`` e o zoom/pan chegam
        ao filtro mesmo nele.
        """
        framed = tpl.Template(
            name="framed",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            reframe_zoom=2,
            reframe_pan_x=0.25,
            reframe_pan_y=0.75,
        )
        graph, _ = tpl.compose(framed, 1080, 1920)
        self.assertIn("scale=2160:3840", graph, "o zoom 2x nao chegou no scale")
        self.assertIn("crop=1080:1920:x=(in_w-out_w)*0.25", graph, "o pan_x nao chegou no crop")
        self.assertIn("y=(in_h-out_h)*0.75", graph, "o pan_y nao chegou no crop")

    def test_split_card_with_reframe_applies_to_video_zone(self):
        """split-card (2 zonas, compoe) passa a obedecer o reframe da zona de video.

        E o unico template embutido que compoe: antes do passo 1, o reframe
        da zona de video era descartado porque ``compose`` nunca olhava a
        chave. Agora ``scale_into`` aplica, e o [0:v] escala com o crop certo.
        """
        framed = tpl.replace(tpl.SPLIT_CARD, reframe_zoom=1.5, reframe_pan_x=0.3, reframe_pan_y=0.7)
        graph, _ = tpl.compose(framed, 1080, 1920)
        # A zona de video ocupa 62% da altura (plan_bands arredonda para 1190px
        # por causa do residual). Zoom 1.5 escala a largura para 1620px.
        self.assertIn("scale=1620:1785", graph, "o zoom 1.5 nao foi aplicado ao video")
        self.assertIn("crop=1080:1190:x=(in_w-out_w)*0.3", graph, "o pan_x do video nao chegou")
        self.assertIn("y=(in_h-out_h)*0.7", graph, "o pan_y do video nao chegou")

    def test_full_frame_neutral_still_identity_graph(self):
        """full-frame sem reframe continua produzindo o grafo de identidade.

        Garante que a mudanca nao quebra o invariante FULL_FRAME: o template
        so com a zona de video ocupando o canvas inteiro nao pode passar a
        gerar um crop com expressao quando nao ha reframe.
        """
        graph, _ = tpl.compose(tpl.FULL_FRAME, 1080, 1920)
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", graph)
        self.assertNotIn("in_w-out_w", graph)

    def test_reframe_does_not_leak_into_still_zones(self):
        """reframe_* so vale para a zona de video; stills continuam usando zone.zoom.

        Uma zona image com zoom=1.5 e um template com reframe_zoom=2: o
        still deve usar 1.5 (zone.zoom), nao 2 (reframe_zoom).
        """
        framed = tpl.Template(
            name="mixed",
            zones=(
                tpl.Zone(kind="video", fraction=0.6),
                tpl.Zone(kind="image", fraction=0.4, source="logo.png", zoom=1.5, pan_x=0.25, pan_y=0.75),
            ),
            reframe_zoom=2,
            reframe_pan_x=0.3,
            reframe_pan_y=0.7,
        )
        graph, _ = tpl.compose(framed, 1080, 1920)
        # Video zona: scale com zoom 2 (reframe_zoom)
        self.assertIn("scale=2160:", graph)
        # Image zona: scale com zoom 1.5 (zone.zoom), nao 2
        self.assertIn("scale=1620:", graph)
        # pan_y da imagem: 0.75 (zone.pan_y), nao 0.7 (reframe_pan_y)
        # O video usa 0.7 (reframe_pan_y) e a imagem usa 0.75 (zone.pan_y).
        # Confirmado: o crop do image tem *0.75*, o do video tem *0.7.
        self.assertIn("crop=1080:768:x=(in_w-out_w)*0.25:y=(in_h-out_h)*0.75", graph)
        self.assertIn("crop=1080:1152:x=(in_w-out_w)*0.3:y=(in_h-out_h)*0.7", graph)


class FromDictTests(unittest.TestCase):
    def _payload(self, **overrides):
        data = {
            "name": "meu-template",
            "zones": [
                {"kind": "video", "fraction": 0.6},
                {"kind": "frame", "fraction": 0.4},
            ],
        }
        data.update(overrides)
        return data

    def test_a_valid_mapping_becomes_a_template(self):
        parsed = tpl.from_dict(self._payload())
        self.assertEqual(parsed.name, "meu-template")
        self.assertEqual(len(parsed.zones), 2)
        self.assertEqual(parsed.zones[0].kind, "video")

    def test_unknown_top_level_key_is_refused(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.from_dict(self._payload(zones=[{"kind": "video", "fraction": 1.0}], colour="red"))
        self.assertIn("colour", str(ctx.exception))

    def test_unknown_zone_key_is_refused(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.from_dict(
                self._payload(zones=[{"kind": "video", "fraction": 1.0, "blur": 3}])
            )
        self.assertIn("blur", str(ctx.exception))

    def test_zone_without_kind_is_refused(self):
        with self.assertRaises(ClipperError):
            tpl.from_dict(self._payload(zones=[{"fraction": 1.0}]))

    def test_zone_without_fraction_is_refused(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.from_dict(self._payload(zones=[{"kind": "video"}]))
        self.assertIn("fraction", str(ctx.exception))

    def test_empty_zone_list_is_refused(self):
        with self.assertRaises(ClipperError):
            tpl.from_dict(self._payload(zones=[]))

    def test_template_name_falls_back_to_the_stem(self):
        data = self._payload(zones=[{"kind": "video", "fraction": 1.0}])
        del data["name"]
        self.assertEqual(tpl.from_dict(data, name="do_arquivo").name, "do_arquivo")

    def test_a_text_zone_round_trips_its_own_keys(self):
        # O wizard escreve estes nomes no .toml; se um deles não voltasse pelo
        # `from_dict`, o arquivo baixado descreveria outra coisa que não a prévia.
        parsed = tpl.from_dict(self._payload(zones=[
            {"kind": "text", "fraction": 0.2, "text": "POV: olha isso",
             "text_size": 0.04, "text_color": "#facc15", "text_align": "left",
             "text_valign": "top", "text_dx": 0.02, "text_dy": -0.01,
             "text_bold": False, "text_uppercase": True, "text_outline": 0.003},
            {"kind": "video", "fraction": 0.8},
        ]))
        zone = parsed.zones[0]
        self.assertEqual(zone.text, "POV: olha isso")
        self.assertEqual(zone.text_size, 0.04)
        self.assertEqual(zone.text_color, "#facc15")
        self.assertEqual((zone.text_align, zone.text_valign), ("left", "top"))
        self.assertEqual((zone.text_dx, zone.text_dy), (0.02, -0.01))
        self.assertFalse(zone.text_bold)
        self.assertTrue(zone.text_uppercase)
        self.assertEqual(zone.text_outline, 0.003)


class TemplateFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc_template_"))

    def test_toml_round_trip(self):
        path = self.tmp / "canal.toml"
        path.write_text(
            "name = \"canal\"\n"
            "caption_preset = \"neon\"\n"
            "[[zones]]\n"
            "kind = \"video\"\n"
            "fraction = 0.62\n"
            "[[zones]]\n"
            "kind = \"frame\"\n"
            "fraction = 0.38\n"
            "margin_left = 0.03\n",
            encoding="utf-8",
        )
        loaded = tpl.load_template(path)
        self.assertEqual(loaded.name, "canal")
        self.assertEqual(loaded.caption_preset, "neon")
        self.assertEqual(len(loaded.zones), 2)
        self.assertAlmostEqual(loaded.zones[1].margin_left, 0.03)

    def test_the_switches_the_panel_writes_all_load(self):
        """Toda chave que o painel de templates escreve é conhecida do motor.

        ``from_dict`` rejeita chave desconhecida em vez de ignorar, e é o
        comportamento certo para um arquivo escrito à mão. Mas o painel
        ``.toml`` é gerado por código, então uma chave que ele emita e o motor
        não conheça não é erro de usuário: é o arquivo inteiro recusado, com
        todo o trabalho do template perdido. Este teste carrega o arquivo exato
        que a página produz com a legenda desligada e um headline escrito à mão.
        """
        path = self.tmp / "painel.toml"
        path.write_text(
            "name = \"meme\"\n"
            "caption_preset = \"ultra-impact\"\n"
            "captions = false\n"
            "caption_box_theme = \"light\"\n"
            "headline_seconds = 3.0\n"
            "headline_text = \"VOCE USOU O FORMATO DE MEME\"\n"
            "headline_align = \"center\"\n"
            "progress_bar = true\n"
            "[[zones]]\n"
            "kind = \"text\"\n"
            "fraction = 0.16\n"
            "text = \"POV: voce viralizou\"\n"
            "[[zones]]\n"
            "kind = \"video\"\n"
            "fraction = 0.58\n"
            "[[zones]]\n"
            "kind = \"image\"\n"
            "fraction = 0.26\n"
            "source = \"id.png\"\n",
            encoding="utf-8",
        )
        loaded = tpl.load_template(path)
        self.assertFalse(loaded.captions)
        self.assertEqual(loaded.headline_text, "VOCE USOU O FORMATO DE MEME")
        self.assertEqual(loaded.caption_preset, "ultra-impact")
        # E o arquivo carregado realmente desliga a legenda no motor.
        config = make_config()
        self.assertEqual(tpl.apply_to_config(config, loaded).caption_style, "none")

    def test_captions_only_accepts_a_real_boolean(self):
        """``captions = "sim"`` é erro, não ``True``.

        O campo é um switch de três estados (``None`` = o chamador decide), e
        passar por ``bool()`` transformaria o erro de digitação no resultado
        oposto ao desejado, sem nenhuma pista do porquê.
        """
        data = {
            "name": "x",
            "captions": "sim",
            "zones": [{"kind": "video", "fraction": 1.0}],
        }
        with self.assertRaises(ClipperError):
            tpl.from_dict(data)

    def test_captions_absent_stays_absent(self):
        loaded = tpl.from_dict(
            {
                "name": "x",
                "zones": [{"kind": "video", "fraction": 1.0}],
            }
        )
        self.assertIsNone(loaded.captions)

    def test_yaml_round_trip(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML nao instalado")
        path = self.tmp / "canal.yaml"
        path.write_text(
            "name: canal\n"
            "zones:\n"
            "  - kind: video\n"
            "    fraction: 1.0\n",
            encoding="utf-8",
        )
        loaded = tpl.load_template(path)
        self.assertEqual(loaded.zones[0].kind, "video")

    def test_missing_file_raises(self):
        with self.assertRaises(ClipperError):
            tpl.load_template(self.tmp / "nao-existe.toml")

    def test_unknown_extension_raises(self):
        path = self.tmp / "canal.ini"
        path.write_text("name = x", encoding="utf-8")
        with self.assertRaises(ClipperError):
            tpl.load_template(path)


class MemePovFileTests(unittest.TestCase):
    """``templates/meme-pov.toml`` é o formato Meme, e o motor tem que montá-lo.

    O arquivo é a versão que o painel baixa, e a prévia da página promete a
    mesma divisão. A trava é a GEOMETRIA, não a presença do arquivo: um
    ``fraction`` que alguém ajuste faria o render trocar a faixa de lugar sem
    que nada reclamasse, e o vídeo deixaria de caber onde a prévia mostra.
    """

    PATH = Path(__file__).resolve().parents[1] / "templates" / "meme-pov.toml"

    # (kind, y, height) em 1080x1920. As frações são frações do canvas e caem em
    # altura par (o composite yuv420p exige), então os pixels não são o obvious:
    # 22% de 1920 = 422,4 -> 422, 52% = 998,4 -> 998, 26% = 499,2 -> 498, e a
    # sobra de 2px vai para a última faixa, fechando em 500. Soma 1920.
    ESPERADO = (("text", 0, 422), ("video", 422, 998), ("image", 1420, 500))

    def setUp(self):
        self.tpl = tpl.load_template(self.PATH)

    def test_o_arquivo_carrega_e_valida(self):
        self.tpl.validate()
        self.assertEqual(self.tpl.name, "meme-pov")
        self.assertEqual(self.tpl.caption_preset, "ultra-impact")

    def test_a_geometria_e_a_mesma_da_previa(self):
        bands = [b for b in tpl.plan_bands(self.tpl, 1080, 1920) if b.kind != "captions"]
        for band, (kind, y, height) in zip(bands, self.ESPERADO):
            with self.subTest(zona=kind):
                self.assertEqual(band.kind, kind)
                self.assertEqual(band.y, y)
                self.assertEqual(band.height, height)

    def test_o_video_ocupa_52_porque_a_faixa_de_texto_tem_altura(self):
        """A fração do vídeo é consequência, não escolha.

        A faixa de texto é altura própria: é ela que encolhe o vídeo dos 100%
        para 52%. Se alguém "acertar" o vídeo para 0.74 sem mexer na faixa, as
        zonas passam de 1.0 e o motor recusa o arquivo — mas se mexer nos dois,
        o formato muda de propósito e o render sai diferente da prévia sem erro.
        """
        pixel = [z for z in self.tpl.zones if z.kind != "captions"]
        self.assertAlmostEqual(sum(z.fraction for z in pixel), 1.0, places=6)
        video = self.tpl.video_zone
        text = [z for z in pixel if z.kind == "text"][0]
        self.assertEqual(video.fraction, 0.52)
        self.assertEqual(text.fraction, 0.22)

    def test_a_faixa_do_pov_tem_respiro_para_o_texto(self):
        """22%, e não 16%: abaixo disso o texto sai DA placa preta.

        Medido na prévia nas três resoluções que o painel oferece: a 16% a placa
        tem 306px para um corpo de 73px, e com o ``text_dy`` de 46px o texto
        encostava na borda — 2px para fora em 1080x1920 e 10px em 720x1280. A 20%
        cabia nas duas maiores, mas em 720x1280 (onde o texto quebra em QUATRO
        linhas) sobrava só 2px. 22% é o menor valor que passa nas três.

        A trava é a folga, não a fração: ela diz que a placa precisa de altura
        para o texto e o deslocamento, que é a razão de o número existir.
        """
        band = [b for b in tpl.plan_bands(self.tpl, 1080, 1920) if b.kind == "text"][0]
        # Corpo do texto: text_size é fração da altura, mais a entrelinha (1,25).
        corpo = round(band.zone.text_size * 1920 * 1.25)
        folga = band.height - corpo
        self.assertGreaterEqual(
            folga, band.zone.text_dy * 1920 + 8,
            "a faixa do POV nao tem respiro para o texto e o deslocamento dele",
        )

    def test_a_faixa_de_texto_tem_o_texto_do_modelo(self):
        """Zona `text` sem texto é placa preta vazia — e o validador recusa.

        O texto é o que o motor queima, então o arquivo precisa carregar a frase;
        um campo vazio aqui passaria pela galeria (que só desenha) e falharia
        só no render, com o clipe já gravado.
        """
        text = [z for z in self.tpl.zones if z.kind == "text"][0]
        self.assertIn("POV", text.text)
        self.assertEqual(text.text_align, "center")
        self.assertEqual(text.text_valign, "middle")
        # 0.038 da altura = 73px num quadro de 1920.
        self.assertAlmostEqual(text.text_size * 1920, 73, delta=1)

    def test_o_pov_desce_46px_porque_e_o_modelo(self):
        """O `text_dy` do POV é ajuste do modelo, não resto de arrasto.

        Com ``an=5`` o libass centraliza a CAIXA da fonte, e a caixa tem mais
        altura acima da linha-base do que abaixo: o centro visual do texto fica
        acima do centro geométrico da faixa. Os 46px descendem esse desnível.

        A trava importa porque o valor é pequeno e pareceria descuido: zerado, o
        render sai com o texto uns 3% mais alto e ninguém reclama — só fica
        diferente da prévia que o painel promete.
        """
        text = [z for z in self.tpl.zones if z.kind == "text"][0]
        self.assertAlmostEqual(text.text_dy, 0.024, places=4)
        self.assertEqual(text.text_dx, 0.0)
        # 0.024 do canvas = 46px num quadro de 1920.
        self.assertAlmostEqual(text.text_dy * 1920, 46, delta=0.5)

    def test_a_ancora_desce_46px_da_area_interna(self):
        """O que o render usa: a âncora, e ela tem de carregar o mesmo deslocamento.

        O offset é medido a partir do centro da ÁREA INTERNA, não da faixa: o
        `text_anchor` resolve o ponto de alinhamento depois de tirar as margens,
        para que elas sejam o respiro dentro do qual o texto vive. A faixa tem
        14px de respiro em cima, então a âncora medida contra a faixa daria 44 e
        contra a área interna dá 46 — os 46px do modelo.
        """
        band = [b for b in tpl.plan_bands(self.tpl, 1080, 1920) if b.kind == "text"][0]
        x, y, an = tpl.text_anchor(band, band.zone, 1080, 1920)
        self.assertEqual(an, 5)
        # 422 e não 422,4: o composite yuv420p exige altura par.
        self.assertEqual(band.height, 422)
        centro_interno = band.inner_y + band.inner_height / 2
        self.assertAlmostEqual(y - centro_interno, 46, delta=0.5)
        # E continua no eixo horizontal, senão o texto sai da placa preta.
        self.assertEqual(x, 540)

    def test_a_legenda_e_a_ultima_zona(self):
        """O ``captions`` precisa vir por último, e ``validate`` cobra isso.

        A faixa de legenda é desenhada depois das outras justamente para poder
        passar por cima delas; fora de ordem, a zona de cima a cobriria.
        """
        self.assertEqual(self.tpl.zones[-1].kind, "captions")
        # A zona de legenda não conta na soma das frações.
        self.assertEqual(self.tpl.zones[-1].fraction, 0.0)

    def test_a_chave_captions_e_aceita_pelo_arquivo(self):
        """Descomentar ``captions = false`` no arquivo tem que funcionar.

        A linha está comentada no arquivo entregue porque a legenda entra por
        padrão, mas é a mesma chave que o painel escreve. Se o motor deixasse de
        aceitar, o único jeito de desligar a legenda seria reescrever o arquivo
        inteiro a mão.
        """
        tmp = Path(tempfile.mkdtemp(prefix="vc_meme_")) / "mudo.toml"
        source = self.PATH.read_text(encoding="utf-8").replace(
            "# captions = false", "captions = false"
        )
        tmp.write_text(source, encoding="utf-8")
        loaded = tpl.load_template(tmp)
        self.assertFalse(loaded.captions)


class GetTemplateTests(unittest.TestCase):
    def test_builtins_load_by_name(self):
        for name in tpl.BUILTIN:
            self.assertEqual(tpl.get_template(name).name, name)

    def test_lookup_is_case_and_space_tolerant(self):
        self.assertEqual(tpl.get_template("  Split-Card ").name, "split-card")

    def test_unknown_name_lists_the_choices(self):
        with self.assertRaises(ClipperError) as ctx:
            tpl.get_template("nao-existe")
        message = str(ctx.exception)
        self.assertIn("full-frame", message)
        self.assertIn("split-card", message)


class ExpandVariationsTests(unittest.TestCase):
    def test_no_axes_returns_the_base_template(self):
        self.assertEqual(tpl.expand_variations(tpl.SPLIT_CARD), [tpl.SPLIT_CARD])

    def test_one_preset_one_layout_is_a_single_variant(self):
        variants = tpl.expand_variations(
            tpl.SPLIT_CARD, caption_presets=["neon"], layouts=["focus"]
        )
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].caption_preset, "neon")
        self.assertEqual(variants[0].layout, "focus")
        self.assertEqual(variants[0].name, "split-card__neon-focus")

    def test_axes_multiply(self):
        variants = tpl.expand_variations(
            tpl.SPLIT_CARD,
            caption_presets=["neon", "karaoke", "minimal"],
            layouts=["focus", "blur"],
        )
        self.assertEqual(len(variants), 6)
        self.assertEqual(len({v.name for v in variants}), 6)

    def test_variants_inherit_the_base_zones(self):
        variants = tpl.expand_variations(tpl.SPLIT_CARD, caption_presets=["neon"])
        self.assertEqual(variants[0].zones, tpl.SPLIT_CARD.zones)

    def test_blank_axis_entries_are_ignored(self):
        # Blank entries are dropped; with every entry dropped the axis falls
        # back to the template's own value, so exactly one variant remains -
        # the base composition re-encoded, not a duplicate.
        variants = tpl.expand_variations(
            tpl.SPLIT_CARD, caption_presets=["neon", "", "  "]
        )
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].caption_preset, "neon")

    def test_all_blank_axes_fall_back_to_the_base_values(self):
        base = tpl.Template(
            name="base",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            caption_preset="neon",
            layout="focus",
        )
        variants = tpl.expand_variations(base, caption_presets=["", " "], layouts=[""])
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].caption_preset, "neon")
        self.assertEqual(variants[0].layout, "focus")


class ApplyToConfigTests(unittest.TestCase):
    def test_none_fields_leave_the_config_alone(self):
        config = make_config(caption_preset="karaoke", layout="focus")
        quiet = tpl.Template(name="quiet", zones=(tpl.Zone(kind="video", fraction=1.0),))
        result = tpl.apply_to_config(config, quiet)
        self.assertIs(result, config)

    def test_set_fields_win_over_the_config(self):
        config = make_config(caption_preset="karaoke", layout="focus")
        loud = tpl.Template(
            name="loud",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            caption_preset="ultra-impact",
            layout="blur",
            progress_bar=True,
        )
        result = tpl.apply_to_config(config, loud)
        self.assertEqual(result.caption_preset, "ultra-impact")
        self.assertEqual(result.layout, "blur")
        self.assertTrue(result.progress_bar)
        # Untouched fields survive the replace.
        self.assertEqual(result.url, config.url)

    def test_the_original_config_is_not_mutated(self):
        config = make_config(caption_preset="karaoke")
        loud = tpl.Template(
            name="loud",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            caption_preset="neon",
        )
        tpl.apply_to_config(config, loud)
        self.assertEqual(config.caption_preset, "karaoke")

    def test_captions_false_turns_the_burned_words_off(self):
        """``captions = false`` é ``caption_style = "none"``.

        Desligar a legenda não é escolher outro visual: o preset continua no
        arquivo e continua pintando o headline e as faixas de texto. O que o
        motor precisa é do switch que ``build_captions`` consulta.
        """
        config = make_config()
        off = tpl.Template(
            name="mudo",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            caption_preset="ultra-impact",
            captions=False,
        )
        result = tpl.apply_to_config(config, off)
        self.assertEqual(result.caption_style, "none")
        # O preset NÃO é o que foi desligado: o headline continua saindo com ele.
        self.assertEqual(result.caption_preset, "ultra-impact")

    def test_an_absent_switch_leaves_the_chosen_style_alone(self):
        config = make_config(caption_style="block")
        quiet = tpl.Template(name="quiet", zones=(tpl.Zone(kind="video", fraction=1.0),))
        self.assertEqual(tpl.apply_to_config(config, quiet).caption_style, "block")

    def test_captions_true_never_guesses_a_style(self):
        """Religar pelo template não pode chutar "karaoke".

        Não existe valor "volta ao que eu tinha" no ``caption_style``, e chutar um
        apagaria um ``--caption-style block`` escolhido na linha de comando sem
        o usuário ter pedido nada. Quem religa é o painel, omitindo a chave.
        """
        config = make_config(caption_style="block")
        back_on = tpl.Template(
            name="ligada",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            captions=True,
        )
        self.assertEqual(tpl.apply_to_config(config, back_on).caption_style, "block")

    def test_the_headline_text_rides_along_only_when_written(self):
        """String vazia é "deixa o motor derivar", não "apaga o que eu digitei"."""
        config = make_config(headline_text="do comando")
        said = tpl.Template(
            name="com-frase",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            headline_text="minha frase",
        )
        blank = tpl.Template(
            name="sem-frase",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            headline_text="",
        )
        self.assertEqual(tpl.apply_to_config(config, said).headline_text, "minha frase")
        self.assertEqual(tpl.apply_to_config(config, blank).headline_text, "do comando")

    def test_headline_align_rides_along_when_set(self):
        config = make_config()
        aligned = tpl.Template(
            name="aligned",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            headline_align="right",
        )
        result = tpl.apply_to_config(config, aligned)
        self.assertEqual(result.headline_align, "right")

    def test_headline_align_rejects_unknown_values(self):
        bad = tpl.Template(
            name="bad",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            headline_align="middle",
        )
        with self.assertRaises(ClipperError):
            bad.validate()

    def test_box_theme_rides_along_when_set(self):
        config = make_config()
        themed = tpl.Template(
            name="themed",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            caption_box_theme="dark",
        )
        result = tpl.apply_to_config(config, themed)
        self.assertEqual(result.caption_box_theme, "dark")

    def test_box_theme_rejects_unknown_values(self):
        bad = tpl.Template(
            name="bad",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            caption_box_theme="sepia",
        )
        with self.assertRaises(ClipperError):
            bad.validate()

    def test_headline_size_and_margins_ride_along_when_set(self):
        config = make_config()
        sized = tpl.Template(
            name="sized",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            headline_font_size=120,
            headline_margin_side=120,
        )
        result = tpl.apply_to_config(config, sized)
        self.assertEqual(result.headline_font_size, 120)
        self.assertEqual(result.headline_margin_side, 120)

    def test_headline_size_and_margins_reject_bad_values(self):
        with self.assertRaises(ClipperError):
            tpl.Template(
                name="bad",
                zones=(tpl.Zone(kind="video", fraction=1.0),),
                headline_font_size=0,
            ).validate()
        with self.assertRaises(ClipperError):
            tpl.Template(
                name="bad",
                zones=(tpl.Zone(kind="video", fraction=1.0),),
                headline_margin_side=-1,
            ).validate()

    def test_reframe_rides_along_when_set(self):
        config = make_config()
        framed = tpl.Template(
            name="framed",
            zones=(tpl.Zone(kind="video", fraction=1.0),),
            reframe_zoom=2,
            reframe_pan_x=0.25,
            reframe_pan_y=0.75,
        )
        result = tpl.apply_to_config(config, framed)
        self.assertEqual(result.reframe_zoom, 2)
        self.assertEqual(result.reframe_pan_x, 0.25)
        self.assertEqual(result.reframe_pan_y, 0.75)

    def test_reframe_rejects_bad_values(self):
        with self.assertRaises(ClipperError):
            tpl.Template(
                name="bad",
                zones=(tpl.Zone(kind="video", fraction=1.0),),
                reframe_zoom=0.5,
            ).validate()
        with self.assertRaises(ClipperError):
            tpl.Template(
                name="bad",
                zones=(tpl.Zone(kind="video", fraction=1.0),),
                reframe_pan_x=2,
            ).validate()

    def test_zone_zoom_pan_reaches_the_compose_graph(self):
        template = tpl.Template(
            name="zoomed",
            zones=(
                tpl.Zone(kind="frame", fraction=1.0, zoom=2, pan_x=0.25, pan_y=0.75),
                tpl.Zone(kind="captions", fraction=0.0),
            ),
        )
        graph, _ = tpl.compose(template, 1080, 1920)
        self.assertIn("crop=1080:1920:x=(in_w-out_w)*0.25:y=(in_h-out_h)*0.75", graph)

    def test_zone_zoom_pan_rejects_bad_values(self):
        with self.assertRaises(ClipperError):
            tpl.Zone(kind="frame", fraction=1.0, zoom=0.5).validate(1)
        with self.assertRaises(ClipperError):
            tpl.Zone(kind="frame", fraction=1.0, pan_x=2).validate(1)


class PlateImageIsGoneTests(unittest.TestCase):
    """``plate_image`` foi removida do motor, e o recusa e o que sobra.

    A chave existia para a pagina de templates, que saiu: ela escolhia a placa
    de uma faixa e escrevia o caminho no ``.toml``. Nao havia outra fonte de
    uso — `web/fundo titulo` era a pasta de imagens que a pagina oferecia, e
    o motor so resolvia o caminho que ela apontasse.

    Este teste nao mede o que sobrou; mede o que acontece com quem ainda tem a
    chave. Quem guardou um ``.toml`` com `plate_image` precisa de uma falha que
    diga o nome da chave, e nao de um ``TypeError`` ou de uma faixa que sai
    muda. E o risco real de remover uma chave: sem esta verificacao, o
    caminho e silencioso, e a descoberta acontece no primeiro render do
    usuario.
    """

    #: O que um ``.toml`` antigo traz. Escrito inteiro, e nao montado com
    #: concatenacao, porque o teste E o exemplo que o README ja nao tem mais.
    COM_CHAVE = """name = "com-placa"

[[zones]]
kind = "text"
fraction = 0.2
color = "black"
plate_image = "web/fundo titulo/1.jpg"
text = "ISSO AQUI VAI VIRALIZAR"
"""

    def test_a_file_that_still_names_the_key_is_refused(self):
        p = Path(tempfile.mkdtemp()) / "com-placa.toml"
        p.write_text(self.COM_CHAVE, encoding="utf-8")
        with self.assertRaises(ClipperError) as erro:
            tpl.load_template(p)
        # A mensagem tem que NOMEAR a chave. "Chave desconhecida na zona: X" e o
        # que permite ao usuario saber o que tirar do arquivo dele; uma falha
        # generica deixaria o template inteiro como suspeito.
        self.assertIn("plate_image", str(erro.exception))

    def test_the_key_is_not_on_the_zone_anymore(self):
        self.assertFalse(
            hasattr(tpl.Zone, "plate_image"),
            "a chave continua no dataclass: passaria a carregar e a validar, "
            "e so nao faria nada")
        self.assertNotIn("plate_image", [f.name for f in
                                         __import__("dataclasses").fields(tpl.Zone)])

    def test_the_key_is_not_in_the_files_accepted_set(self):
        """A lista de chaves conhecidas do arquivo tambem perdeu a entrada.

        `from_dict` mantem o conjunto do que aceita. Uma chave esquecida nessa
        lista, com o campo ja fora do dataclass, passaria pelo `from_dict` e
        falharia mais tarde, no `Zone(...)`, com um erro que fala de tipo e nao
        de template. A lista e a primeira coisa a conferir depois de remover
        uma chave.
        """
        import inspect

        fonte = inspect.getsource(tpl.from_dict)
        self.assertNotIn("plate_image", fonte)

    def test_nothing_in_the_repo_still_points_at_the_removed_folder(self):
        """Nem o README, nem o config de exemplo, nem um script de conferencia.

        As imagens sairam junto com a chave, e qualquer arquivo que ainda aponte
        para `web/fundo titulo` esta apontando para o vazio. A busca e no disco
        inteiro e nao so nos arquivos versionados, porque um script esquecido
        na raiz e exatamente o tipo de coisa que nao aparece no `git status`.
        """
        raiz = Path(__file__).resolve().parent.parent
        for caminho in sorted(raiz.glob("*.py")):
            with self.subTest(arquivo=caminho.name):
                self.assertNotIn("fundo titulo", caminho.read_text("utf-8"),
                                 f"{caminho.name} ainda aponta para a pasta "
                                 "que foi removida")
        readme = raiz / "README.md"
        self.assertNotIn("fundo titulo", readme.read_text("utf-8"))
        self.assertNotIn("plate_image", readme.read_text("utf-8"))
        self.assertFalse((raiz / "placa_check.py").exists(),
                         "placa_check.py existia so para medir plate_image")


class DescribeTests(unittest.TestCase):
    def test_description_mentions_every_band(self):
        text = tpl.describe(tpl.SPLIT_CARD, 1080, 1920)
        self.assertIn("video", text)
        self.assertIn("frame", text)
        self.assertIn("captions", text)
        self.assertIn("1080x1920", text)

    def test_the_description_names_where_the_text_lands(self):
        # O `\an` sozinho não diz onde o texto para — ele só diz qual ponto do
        # bloco as coordenadas nomeiam. A linha resolve a âncora em px, que é o
        # MESMO número que a prévia do painel desenha: é assim que se confere a
        # janela contra o motor sem gravar um vídeo.
        text = tpl.describe(_text_template(), 1080, 1920)
        band = _text_band()
        x, y, an = tpl.text_anchor(band, band.zone, 1080, 1920)
        self.assertIn(f"texto ancorado em ({x},{y}) an={an}", text)
        self.assertIn("text", text)


if __name__ == "__main__":
    unittest.main()
