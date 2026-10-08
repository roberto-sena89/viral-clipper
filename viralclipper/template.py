"""Video templates: a named 9:16 composition that survives across runs.

A ``ClipConfig`` answers "how do I cut one clip". A template answers "what does
every clip from this channel look like". The difference matters because the
look is the part you iterate on: you keep one template per channel (or per
format) and re-render the whole back catalogue against it, instead of re-picking
a layout and a preset on every run.

The composition is expressed as **zones**. Each zone is a horizontal band of the
final canvas with a height fraction and a content source:

    video   the clip itself, cropped/scaled to the band
    image   a still (a logo, a channel card, a movie-poster frame)
    frame   a still extracted from the clip itself, at a chosen timestamp
    solid   a flat coloured band (a separator, a low-third plate)
    text    a flat coloured band carrying burned text (a POV banner, a lower third)
    captions the caption band; always the last zone, always full width

Zones stack top to bottom and their fractions must sum to 1.0. The default
template is a single ``video`` zone covering the whole canvas, which reproduces
the pre-template rendering exactly: enabling templates must not silently change
what the tool already produced.

A ``text`` zone is the one kind whose *content* is not a pixel source: the band
is painted as a flat plate (its ``color``, black by default) by the composer, and
the words are burned by libass in the caption stage, which is the only stage that
knows about fonts. :func:`text_anchor` resolves where - the band decides the
region, ``text_align``/``text_valign`` pick the anchor inside it, and
``text_dx``/``text_dy`` nudge it from there. Keeping that arithmetic here (and
not in the renderer) is what makes it testable without ffmpeg, and it is the same
number the web preview has to draw.

Everything here is pure string building and dataclass validation, so the whole
module is testable without ffmpeg. The one function that touches disk is
:func:`extract_frame`, which shells out to ffmpeg under a caller-supplied
working directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .util import ClipperError

# Zone kinds. ``frame`` and ``image`` both resolve to a still; they differ only
# in where the still comes from, which the renderer decides, not the composer.
ZONE_KINDS = ("video", "image", "frame", "solid", "text", "captions")

# How a still is fitted inside its band.
FIT_MODES = ("cover", "contain")

# Where the burned text of a ``text`` zone sits inside its band.
TEXT_ALIGNS = ("left", "center", "right")
TEXT_VALIGNS = ("top", "middle", "bottom")

# A zone fraction is meaningless below this; refusing it early beats producing a
# 2-pixel band that ffmpeg crops to nothing.
MIN_ZONE_FRACTION = 0.02

# ``text_size`` is a share of the CANVAS height, so a template keeps its
# proportions at any resolution: 0.05 is 96 px at 1920 and 64 px at 1280.
MIN_TEXT_SIZE = 0.005
MAX_TEXT_SIZE = 0.4
# ``text_dx``/``text_dy`` are shares of the canvas too. A full canvas of travel in
# each direction is the useful ceiling: past that the text is off-frame, and the
# validator refusing it is a better answer than a render with nothing visible.
MAX_TEXT_OFFSET = 1.0


@dataclass(frozen=True)
class Zone:
    """One horizontal band of the composed canvas.

    ``fraction`` is the band height as a share of the canvas, so a template is
    resolution independent: the same 0.55/0.45 split renders at 1080x1920 and
    at 720x1280 without being rewritten.

    ``margin_*`` are fractions of the *canvas* width/height, not of the band,
    because that is what a designer means by "leave a 4% gutter".
    """

    kind: str
    fraction: float
    # ``image``: path to the still. Empty is a valid state, not a typo: a gallery
    # template opens before the user has the asset (the Meme's identity bar, the
    # X card's tweet print), and the renderer degrades an asset-less band to the
    # clip rather than refusing the template. ``frame``: ignored, the renderer
    # extracts the still from the clip.
    source: str | None = None
    # Where inside the clip the ``frame`` still is taken from, in seconds from
    # the start of the clip. 0.0 is the classic "first frame as poster" look.
    frame_at: float = 0.0
    fit: str = "cover"
    # Flat colour for ``solid``, and the letterbox colour for ``contain``.
    color: str = "black"
    # Fractions of the canvas, applied inside the band.
    margin_top: float = 0.0
    margin_bottom: float = 0.0
    margin_left: float = 0.0
    margin_right: float = 0.0
    # Deslocamento da FAIXA inteira, em fracao da altura do quadro: 0 e a posicao
    # do empilhamento (a soma das fracoes acima), positivo desce, negativo sobe.
    #
    # E o que faz a zona de texto descer sem arrastar as outras. O empilhamento
    # da posicao — cada faixa comeca onde a anterior acaba — e o que impede o
    # usuario de escolher ONDE a faixa fica: mudar a fracao move tudo abaixo, e
    # nao da para "puxar a faixa do meio para baixo" sem trocar a ordem das
    # zonas. Aqui a faixa sai do lugar e a area que ela deixa vira fundo do
    # template, que e a mesma conta que as margens ja faziam.
    band_dy: float = 0.0
    # Rounded corners for a still band, in canvas-width fraction. 0 keeps it
    # square; ffmpeg has no cheap rounded-rect crop, so a rounded zone is drawn
    # by compositing the still over a colour plate with an alpha mask.
    corner_radius: float = 0.0
    # Manual reframe inside a still band (image/frame): zoom >= 1 magnifies
    # around the pan point; pan 0..1 slides the crop window. None = layout.
    zoom: float | None = None
    pan_x: float | None = None
    pan_y: float | None = None
    # ``text``: the words burned into the band, and how they sit in it.
    # ``text_size`` and ``text_outline`` are shares of the canvas HEIGHT (a
    # vertical measurement, so the canvas height is what they scale with);
    # ``text_dx``/``text_dy`` are shares of the canvas width/height and are the
    # fine positioning, applied on top of the alignment.
    text: str = ""
    text_size: float = 0.05
    text_color: str = "#ffffff"
    text_align: str = "center"
    text_valign: str = "middle"
    text_dx: float = 0.0
    text_dy: float = 0.0
    text_bold: bool = True
    text_uppercase: bool = False
    text_outline: float = 0.0

    def validate(self, index: int) -> None:
        where = f"zona {index} ({self.kind})"
        if self.kind not in ZONE_KINDS:
            known = ", ".join(ZONE_KINDS)
            raise ClipperError(f"{where}: tipo desconhecido. Escolha entre: {known}.")
        # The caption band occupies no height of its own - libass positions the
        # text absolutely - so it is the one kind exempt from the fraction floor.
        if self.kind != "captions" and not MIN_ZONE_FRACTION <= self.fraction <= 1.0:
            raise ClipperError(
                f"{where}: fraction deve ficar entre {MIN_ZONE_FRACTION} e 1.0, "
                f"recebido {self.fraction}."
            )
        if self.fit not in FIT_MODES:
            known = ", ".join(FIT_MODES)
            raise ClipperError(f"{where}: fit deve ser {known}.")
        # Nao ha exigencia de ``source`` numa zona ``image``, de proposito: sem
        # arquivo e um estado VALIDO, e nao um erro de digitacao. E o estado em
        # que um formato da galeria abre, antes de o usuario ter o asset (a barra
        # de identidade do Meme, o print do X). Recusar aqui fazia o wizard acusar
        # "uma zona de imagem precisa de um caminho" e travar o passo ate o
        # usuario escolher um arquivo — e inventar um caminho no catalogo so
        # trocaria esse aviso por um render falhando depois do download. Sem asset
        # o renderer degrada para o proprio clipe, que e a mesma politica que ele
        # ja aplica a um arquivo que nao existe (``render._template_stills``).
        if self.kind == "frame" and self.frame_at < 0:
            raise ClipperError(f"{where}: frame_at nao pode ser negativo.")
        if self.kind == "text":
            # An empty text zone is a black band with nothing in it. That is
            # never what someone meant to write, and on screen it is
            # indistinguishable from a bug in the renderer.
            if not self.text.strip():
                raise ClipperError(f"{where}: zona 'text' exige o texto em 'text'.")
            if not MIN_TEXT_SIZE <= self.text_size <= MAX_TEXT_SIZE:
                raise ClipperError(
                    f"{where}: text_size deve ficar entre {MIN_TEXT_SIZE} e "
                    f"{MAX_TEXT_SIZE} (fracao da altura), recebido {self.text_size}."
                )
            if self.text_align not in TEXT_ALIGNS:
                known = ", ".join(TEXT_ALIGNS)
                raise ClipperError(
                    f"{where}: text_align deve ser {known}, recebido {self.text_align!r}."
                )
            if self.text_valign not in TEXT_VALIGNS:
                known = ", ".join(TEXT_VALIGNS)
                raise ClipperError(
                    f"{where}: text_valign deve ser {known}, recebido {self.text_valign!r}."
                )
            for axis in ("text_dx", "text_dy"):
                value = getattr(self, axis)
                if not -MAX_TEXT_OFFSET <= value <= MAX_TEXT_OFFSET:
                    raise ClipperError(
                        f"{where}: {axis} precisa ficar entre "
                        f"{-MAX_TEXT_OFFSET} e {MAX_TEXT_OFFSET}, recebido {value}."
                    )
            if self.text_outline < 0:
                raise ClipperError(
                    f"{where}: text_outline nao pode ser negativo, "
                    f"recebido {self.text_outline}."
                )
        if self.corner_radius < 0:
            raise ClipperError(f"{where}: corner_radius nao pode ser negativo.")
        if self.zoom is not None and self.zoom < 1:
            raise ClipperError(f"{where}: zoom precisa ser 1 ou maior.")
        for axis in ("pan_x", "pan_y"):
            value = getattr(self, axis)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ClipperError(f"{where}: {axis} precisa ficar entre 0 e 1.")
        total_margin = self.margin_top + self.margin_bottom
        if total_margin >= 1.0:
            raise ClipperError(
                f"{where}: margens verticais somam {total_margin:.2f} da faixa; "
                "nao sobra imagem."
            )
        # O deslocamento e uma fracao do QUADRO, e o limite nao e arbitrario: uma
        # faixa so pode descer ate a base do quadro e subir ate o topo sem sair.
        # `band_dy` nao consegue respeitar isso sozinho (nao conhece a posicao nem
        # a altura da faixa), entao este e o teto grosso e a checagem fina fica em
        # `plan_bands` — que e onde os dois numeros existem.
        if not -1.0 <= self.band_dy <= 1.0:
            raise ClipperError(
                f"{where}: band_dy precisa ficar entre -1 e 1 (fracao da altura "
                f"do quadro), recebido {self.band_dy}."
            )


@dataclass(frozen=True)
class Template:
    """A named composition plus the caption preset it was designed around.

    ``caption_preset`` lives on the template rather than only on the config
    because the band height and the preset's ``margin_v`` have to agree: a
    preset tuned for a full-height frame pushes its captions off a band that
    only occupies the bottom 22% of the canvas.
    """

    name: str
    description: str = ""
    zones: tuple[Zone, ...] = ()
    caption_preset: str | None = None
    # ``False`` desliga a legenda queimada do clip. E um campo separado do
    # ``caption_preset`` porque o desligamento nao e um visual: e o
    # ``caption_style = "none"`` do config, e o preset continua valendo para o
    # texto das faixas de headline. ``None`` nao mexe em nada.
    captions: bool | None = None
    layout: str | None = None
    headline_seconds: float | None = None
    headline_text: str | None = None
    headline_align: str | None = None
    headline_font_size: int | None = None
    headline_margin_side: int | None = None
    # Vertical top margin for headline as fraction of canvas height (0.0-1.0).
    # Keeps the hook clear of the notch/Dynamic Island. Default ~0.06 (~115px @ 1920).
    headline_margin_top_ratio: float | None = None
    reframe_zoom: float | None = None
    reframe_pan_x: float | None = None
    reframe_pan_y: float | None = None
    caption_box_theme: str | None = None
    progress_bar: bool | None = None
    # Fraction of canvas height reserved as a bottom safe zone (0.0-1.0).
    # When set, auto-configures caption_margin_v_ratio to sit above this zone.
    # TikTok ~0.10 (200px @ 1920), Reels ~0.10, Shorts ~0.08.
    safe_zone_ratio: float | None = None
    # Background painted before any zone is drawn. Only visible where a zone
    # uses ``contain`` or carries a margin.
    background: str = "black"

    def validate(self) -> None:
        if not self.name:
            raise ClipperError("Template sem nome.")
        if not self.zones:
            raise ClipperError(f"Template '{self.name}' nao tem zonas.")
        # Structural checks first: "the caption zone must be last" is a far more
        # useful message than "fraction 0.0 out of range" on a zone the user
        # simply put in the wrong place.
        caption_zones = [z for z in self.zones if z.kind == "captions"]
        if len(caption_zones) > 1:
            raise ClipperError(
                f"Template '{self.name}': no maximo uma zona 'captions'."
            )
        if caption_zones and self.zones[-1].kind != "captions":
            # The caption band is painted last so it can overlap the zones
            # above it; anywhere else it would be covered by them.
            raise ClipperError(
                f"Template '{self.name}': a zona 'captions' precisa ser a ultima."
            )
        video_zones = [z for z in self.zones if z.kind == "video"]
        if len(video_zones) > 1:
            raise ClipperError(
                f"Template '{self.name}': no maximo uma zona 'video'."
            )
        for index, zone in enumerate(self.zones, start=1):
            zone.validate(index)
        # Only the pixel-bearing zones are measured: 'captions' is positioned
        # absolutely by libass and deliberately carries fraction 0.0, so a
        # captions-only template is legal and sums to zero.
        total = sum(zone.fraction for zone in self.zones if zone.kind != "captions")
        if self.zones and all(z.kind == "captions" for z in self.zones):
            return
        # 1e-6 rather than an exact compare: the fractions are written by hand
        # in a config file and 0.55 + 0.45 is not exactly 1.0 in binary floats.
        if abs(total - 1.0) > 1e-6:
            raise ClipperError(
                f"Template '{self.name}': as zonas somam {total:.4f} da altura; "
                "precisam somar exatamente 1.0."
            )
        if self.headline_align is not None and self.headline_align not in {"left", "center", "right"}:
            raise ClipperError(
                f"Template '{self.name}': headline_align precisa ser left, center ou right."
            )
        if self.headline_font_size is not None and self.headline_font_size <= 0:
            raise ClipperError(
                f"Template '{self.name}': headline_font_size precisa ser maior que zero."
            )
        if self.headline_margin_side is not None and self.headline_margin_side < 0:
            raise ClipperError(
                f"Template '{self.name}': headline_margin_side nao pode ser negativo."
            )
        if self.reframe_zoom is not None and self.reframe_zoom < 1:
            raise ClipperError(
                f"Template '{self.name}': reframe_zoom precisa ser 1 ou maior."
            )
        for axis in ("reframe_pan_x", "reframe_pan_y"):
            value = getattr(self, axis)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ClipperError(
                    f"Template '{self.name}': {axis} precisa ficar entre 0 e 1."
                )
        if self.caption_box_theme is not None and self.caption_box_theme not in {"light", "dark"}:
            raise ClipperError(
                f"Template '{self.name}': caption_box_theme precisa ser light ou dark."
            )
        if self.captions is not None and not isinstance(self.captions, bool):
            raise ClipperError(
                f"Template '{self.name}': captions precisa ser true ou false."
            )
        if self.headline_margin_top_ratio is not None and not 0.0 <= self.headline_margin_top_ratio <= 1.0:
            raise ClipperError(
                f"Template '{self.name}': headline_margin_top_ratio precisa ficar entre 0 e 1."
            )
        if self.safe_zone_ratio is not None and not 0.0 <= self.safe_zone_ratio <= 1.0:
            raise ClipperError(
                f"Template '{self.name}': safe_zone_ratio precisa ficar entre 0 e 1."
            )

    @property
    def video_zone(self) -> Zone | None:
        for zone in self.zones:
            if zone.kind == "video":
                return zone
        return None

    @property
    def caption_zone(self) -> Zone | None:
        for zone in self.zones:
            if zone.kind == "captions":
                return zone
        return None


# The identity template: one video zone over the whole canvas. Rendering with
# it must produce byte-comparable geometry to rendering with no template at all.
FULL_FRAME = Template(
    name="full-frame",
    description="Video ocupa o quadro inteiro — o visual classico do tool",
    zones=(Zone(kind="video", fraction=1.0),),
)

# The ViceScale-style split: video on top, a branded still below, captions
# burned over the lower part of the video band.
SPLIT_CARD = Template(
    name="split-card",
    description="Video em cima, cartao/logo embaixo — visual de canal",
    zones=(
        Zone(kind="video", fraction=0.62),
        Zone(
            kind="frame",
            fraction=0.38,
            fit="cover",
            # Respiro em cima e em baixo: separa o video do cartao e evita
            # colisao com a barra de progresso/legenda da plataforma.
            margin_top=0.012,
            margin_bottom=0.04,
            margin_left=0.03,
            margin_right=0.03,
            corner_radius=0.035,
        ),
        Zone(kind="captions", fraction=0.0),
    ),
)

# Lower third template: video takes top 2/3, text/banner takes bottom 1/3
LOWER_THIRD = Template(
    name="lower-third",
    description="Video em duas partes superiores, texto/banner na terceira inferior",
    zones=(
        Zone(kind="video", fraction=0.67),
        Zone(
            kind="text",
            fraction=0.33,
            text="Seu texto aqui",
            text_size=0.06,
            text_align="center",
            text_valign="middle",
            color="black",
            text_color="#ffffff",
            text_bold=True,
        ),
    ),
)

# Three-part template: video (50%), image/logo (25%), captions/text (25%)
THREE_PART = Template(
    name="three-part",
    description="Video (50%), imagem/logo (25%), legenda/texto (25%)",
    zones=(
        Zone(kind="video", fraction=0.50),
        Zone(
            kind="image",
            fraction=0.25,
            fit="contain",
            margin_top=0.02,
            margin_bottom=0.02,
            margin_left=0.02,
            margin_right=0.02,
        ),
        Zone(
            kind="text",
            fraction=0.25,
            text="Legenda informativa",
            text_size=0.05,
            text_align="center",
            text_valign="middle",
            color="darkred",
            text_color="#ffffff",
            text_bold=True,
        ),
    ),
)

BUILTIN: dict[str, Template] = {
    FULL_FRAME.name: FULL_FRAME,
    SPLIT_CARD.name: SPLIT_CARD,
    LOWER_THIRD.name: LOWER_THIRD,
    THREE_PART.name: THREE_PART,
}


def get_template(name: str) -> Template:
    """Look up a built-in template by name, listing choices when wrong."""
    key = (name or "").strip().lower()
    template = BUILTIN.get(key)
    if template is None:
        known = ", ".join(sorted(BUILTIN))
        raise ClipperError(f"Template desconhecido: '{name}'. Escolha entre: {known}.")
    return template


# --- pixel geometry -------------------------------------------------------


@dataclass(frozen=True)
class Band:
    """A zone resolved to pixels on the final canvas."""

    kind: str
    x: int
    y: int
    width: int
    height: int
    # The drawing area once the zone's own margins are removed.
    inner_x: int
    inner_y: int
    inner_width: int
    inner_height: int
    zone: Zone


def _even(value: float) -> int:
    """Snap a pixel measure DOWN to an even number.

    The composite is ``yuv420p``: its chroma planes are half resolution, so every
    rectangle a zone draws has to land on that grid. Measured on a real render
    (``band_parity_check.py``), a rectangle that starts or ends on an odd row
    bleeds one row of its own colour into the neighbour above or below, and it
    does so at *every* boundary of the stack, not only at the bottom of it; an
    odd width silently truncates the last drawn column instead. With every band
    offset, every band height and every inner edge even, the composite comes out
    bit-exact - which is why this rounds the geometry instead of the heights.

    Rounding down rather than up keeps the inner rectangle INSIDE the margins
    the zone asked for: a 15px gutter becomes 14, never 16.
    """
    whole = int(value)
    return whole - (whole % 2)


def plan_bands(template: Template, width: int, height: int) -> list[Band]:
    """Resolve every zone to a pixel rectangle, top to bottom.

    Every rectangle lands on the yuv420p chroma grid - see :func:`_even` for why
    that is a correctness rule and not a cosmetic one. A canvas with an odd
    dimension cannot be tiled by even bands, so there the last band keeps the odd
    height it was left with: an odd row of one band costs two rows of chroma
    somewhere, while a one-pixel gap would cost a visible seam.

    The caption zone is special: it occupies no height of its own (libass
    positions it absolutely), so it resolves to the full canvas and is emitted
    last.
    """
    bands: list[Band] = []
    cursor = 0
    for zone in template.zones:
        if zone.kind == "captions":
            bands.append(
                Band(
                    kind="captions",
                    x=0,
                    y=0,
                    width=width,
                    height=height,
                    inner_x=0,
                    inner_y=0,
                    inner_width=width,
                    inner_height=height,
                    zone=zone,
                )
            )
            continue
        band_height = _even(round(height * zone.fraction))
        # Give the last pixel-hungry zone whatever integer rounding left over so
        # the bands always tile the canvas exactly. The leftover is even whenever
        # the canvas is, so the band stays on the chroma grid.
        # O `band_dy` tira a faixa do empilhamento: `cursor` segue somando como se
        # nada tivesse movido (e as faixas de baixo NAO se mexem — e o ponto), e
        # so o retangulo desta sai do lugar. A area que ela deixa vaza mostra o
        # fundo do template, pelo mesmo motivo que uma margem vazia mostra.
        #
        # O deslocamento e limitado ao alcance real: a faixa nao pode descer alem
        # da base nem subir acima do topo. Cortar aqui — em vez de deixar o
        # `overlay` do ffmpeg receber uma `y` negativa — e o que evita um render
        # que sai com codigo 0 e uma faixa cortada sem aviso.
        dy = round(height * zone.band_dy)
        band_y = _even(cursor + dy)
        band_y = min(max(band_y, 0), max(0, height - band_height))
        if zone is template.zones[-1] and cursor + band_height != height:
            band_height = height - cursor
        # Each of the four inner measures is snapped to the grid on its own.
        # Snapping the two edges instead would spend a pixel at each end, and the
        # box is already measured from the band's own top-left; two even numbers
        # add up to an even number, so the opposite edges land on the grid for
        # free. The margins are the gutter, so the pixel of rounding error comes
        # out of the gutter and never out of the picture.
        inner_x = x = _even(round(width * zone.margin_left))
        # O interno acompanha a faixa: com o `band_dy`, a margem de cima e a
        # respiro somam sobre a posicao nova, e nao sobre a do empilhamento.
        inner_y = _even(band_y + round(height * zone.margin_top))
        inner_width = max(2, _even(width - x - round(width * zone.margin_right)))
        inner_height = max(
            2,
            _even(band_height - round(height * (zone.margin_top + zone.margin_bottom))),
        )
        bands.append(
            Band(
                kind=zone.kind,
                x=0,
                y=band_y,
                width=width,
                height=band_height,
                inner_x=inner_x,
                inner_y=inner_y,
                inner_width=inner_width,
                inner_height=inner_height,
                zone=zone,
            )
        )
        cursor += band_height

    # Residual rounding (a 1-pixel gap) is invisible in practice but shows up as
    # a black seam against a light background, so absorb it into the last
    # pixel-bearing band instead of leaving it.
    if cursor != height:
        for index in range(len(bands) - 1, -1, -1):
            if bands[index].kind != "captions":
                band = bands[index]
                bands[index] = replace(band, height=band.height + (height - cursor))
                break
    return bands


# ASS numpad alignment digits: the column is 1..3 from the left, the row is 0/3/6
# from the bottom, so ``top``+``right`` is 9 and ``bottom``+``left`` is 1.
_AN_COLUMN = {"left": 1, "center": 2, "right": 3}
_AN_ROW = {"bottom": 0, "middle": 3, "top": 6}


def text_anchor(band: Band, zone: Zone, width: int, height: int) -> tuple[int, int, int]:
    """Resolve the text of a ``text`` zone to ``(x, y, an)``, in frame pixels.

    ``an`` is the ASS numpad digit that names which point of the text block the
    coordinates refer to, so the text grows away from its anchor instead of
    always to the right: with ``text_align='right'`` the block's right edge sits
    on the anchor, which is what keeps a right-aligned banner clear of the edge
    when the words get longer.

    The anchor is resolved against the band's INNER rectangle (margins already
    removed), so the zone's margins are the gutter the text lives inside, and
    ``text_dx``/``text_dy`` nudge from there. Coordinates are absolute frame
    pixels - ASS ``\\pos`` is absolute, and it is the only way to place text
    anywhere other than the frame's own edges.
    """
    if zone.text_align == "left":
        x = band.inner_x
    elif zone.text_align == "right":
        x = band.inner_x + band.inner_width
    else:
        x = band.inner_x + band.inner_width / 2
    if zone.text_valign == "top":
        y = band.inner_y
    elif zone.text_valign == "bottom":
        y = band.inner_y + band.inner_height
    else:
        y = band.inner_y + band.inner_height / 2
    x += zone.text_dx * width
    y += zone.text_dy * height
    an = _AN_ROW[zone.text_valign] + _AN_COLUMN[zone.text_align]
    return int(round(x)), int(round(y)), an


# --- filtergraph ----------------------------------------------------------

def scale_into(
    band: Band,
    zone: Zone,
    label_in: str,
    *,
    reframe_zoom: float | None = None,
    reframe_pan_x: float | None = None,
    reframe_pan_y: float | None = None,
) -> str:
    """Build the scale/pad fragment that fits ``label_in`` inside a band.

    ``cover`` fills the band and crops the overflow (the usual choice for
    footage: no letterbox bars). ``contain`` shrinks to fit and pads with the
    zone colour, which is right for a logo that must never be cropped.

    For a ``video`` zone, ``reframe_zoom`` / ``reframe_pan_x`` / ``reframe_pan_y``
    override the zone-level ``zoom`` / ``pan_x`` / ``pan_y``: the template-level
    reframe keys are the ones the web panel writes for the clip band, and they
    must land in the same place whether the template composes or not.
    """
    w, h = band.inner_width, band.inner_height
    if zone.fit == "contain":
        return (
            f"[{label_in}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={zone.color},setsar=1"
        )
    if zone.kind == "video":
        zoom = reframe_zoom if reframe_zoom is not None else (zone.zoom or 1.0)
        pan_x = reframe_pan_x if reframe_pan_x is not None else (zone.pan_x if zone.pan_x is not None else 0.5)
        pan_y = reframe_pan_y if reframe_pan_y is not None else (zone.pan_y if zone.pan_y is not None else 0.5)
    else:
        zoom = zone.zoom or 1.0
        pan_x = zone.pan_x if zone.pan_x is not None else 0.5
        pan_y = zone.pan_y if zone.pan_y is not None else 0.5
    if zoom != 1 or pan_x != 0.5 or pan_y != 0.5:
        scaled_w, scaled_h = int(round(w * zoom)), int(round(h * zoom))
        return (
            f"[{label_in}]scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:x=(in_w-out_w)*{pan_x:g}:y=(in_h-out_h)*{pan_y:g},setsar=1"
        )
    return (
        f"[{label_in}]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1"
    )


def rounded_mask(band: Band, zone: Zone) -> str:
    """Filter fragment producing a rounded-rect alpha mask labelled ``[mask]``.

    ffmpeg has no rounded-corner primitive, so the mask is drawn: a white
    rectangle blurred and then contrast-crushed back to a hard edge. The blur
    radius is derived from the requested radius, which is what makes one number
    in the config produce a consistent look at any canvas size.
    """
    radius = max(1, int(round(band.inner_width * zone.corner_radius)))
    # Blur must stay odd for a centred kernel; round up to be safe.
    blur = radius if radius % 2 else radius + 1
    return (
        f"color=c=black:s={band.inner_width}x{band.inner_height}:d=1,"
        f"format=gray,"
        f"drawbox=x={radius}:y=0:w={band.inner_width - 2 * radius}"
        f":h={band.inner_height}:color=white:t=fill,"
        f"drawbox=x=0:y={radius}:w={band.inner_width}"
        f":h={band.inner_height - 2 * radius}:color=white:t=fill,"
        f"gblur=sigma={blur / 2:.2f},"
        f"lutyuv=y='if(gt(val,128),255,0)'"
        f"[mask]"
    )


def compose(
    template: Template,
    width: int,
    height: int,
    *,
    video_input: str = "0:v",
    image_input_start: int = 1,
) -> tuple[str, str]:
    """Build the zone-composition filtergraph.

    Returns ``(graph, output_label)`` where ``output_label`` names the composed
    canvas. The caller appends its own caption/progress/audio stages, so the
    composer never needs to know whether captions are on.

    ``video_input`` is the label the clip arrives on. It is a parameter rather
    than a hardcoded ``0:v`` because a jump cut replaces the clip stream with a
    trimmed one (``[vraw]``); addressing ``0:v`` there would silently bypass the
    cut. ``image_input_start`` is the first input index holding a still, since
    the caller may have consumed inputs before them.

    The first pixel-bearing zone becomes the canvas itself (a flat plate plus
    its content); every later zone is overlaid on top of it. That keeps the
    graph one overlay shorter than starting from a black plate, and for the
    identity template it degenerates to a plain relabel.
    """
    template.validate()
    bands = plan_bands(template, width, height)

    parts: list[str] = []
    current: str | None = None
    image_index = image_input_start

    for index, band in enumerate(bands):
        zone = band.zone
        if zone.kind == "captions":
            # Captions are burned later, over the composed canvas, so the
            # composer treats the band as a no-op pass-through.
            continue

        # Every zone draws exactly one of three things: the clip, a still, or a
        # flat plate - never the canvas built so far. A ``video`` zone in
        # particular ALWAYS reads the clip input, wherever it sits in the stack.
        #
        # Deriving it from ``current`` meant that a video zone below another zone
        # (a POV band above the footage, which is the whole point of a ``text``
        # zone) drew the template background into its own band and dropped the
        # clip from the graph altogether - the render came out as a coloured
        # rectangle with the footage nowhere. It also made the composite feed two
        # filters at once, and ffmpeg's implicit split for that shape wires the
        # overlay's main input to the wrong stream.
        source = f"{video_input}"
        # ``image`` and ``frame`` both draw a still, which is always a separate
        # ffmpeg input - never the canvas built so far. Feeding a still zone the
        # running composite would nest the video band inside its own poster.
        if zone.kind in {"image", "frame"}:
            source = f"{image_index}:v"
            image_index += 1

        label = f"z{index}"
        first = current is None

        if zone.kind == "solid" or zone.kind == "text":
            # A text zone's band is a flat plate like ``solid``: the words are
            # burned later by libass, which is the only stage that knows about
            # fonts. The plate is what makes the text legible over anything, and
            # it is the reason the zone carries a ``color``.
            parts.append(
                f"color=c={zone.color}:s={band.inner_width}x{band.inner_height}"
                f":d=1,format=yuva420p,setsar=1[{label}s]"
            )
            drawn = f"{label}s"
        else:
            parts.append(
                scale_into(
                    band, zone, source,
                    reframe_zoom=template.reframe_zoom,
                    reframe_pan_x=template.reframe_pan_x,
                    reframe_pan_y=template.reframe_pan_y,
                ) + f"[{label}s]"
            )
            drawn = f"{label}s"
            if zone.corner_radius > 0:
                mask_label = f"m{index}"
                parts.append(rounded_mask(band, zone).replace("[mask]", f"[{mask_label}]"))
                parts.append(f"[{label}s][{mask_label}]alphamerge[{label}a]")
                drawn = f"{label}a"

        if first and zone.kind == "video" and band.inner_width == width and band.inner_height == height:
            # The identity template: the clip already fills the canvas, so a
            # straight relabel reproduces the pre-template geometry exactly.
            parts.append(f"[{drawn}]setpts=PTS-STARTPTS[c{index}]")
            current = f"c{index}"
            continue

        if first:
            # Establish the canvas: a full-size plate of the template background
            # with this zone's content composited into its band rectangle.
            #
            # The plate is an infinite colour source (``d`` omitted) rather than
            # a one-frame one. A 1-frame plate would cap ``overlay`` at a single
            # frame and truncate the whole clip, and asking the composited zone
            # to pad itself is fragile when that zone is a single extracted
            # still. An infinite plate makes ffmpeg's ``-t`` the length
            # authority, which is exactly what the caller already sets.
            plate = f"bg{index}"
            parts.append(
                f"color=c={template.background}:s={width}x{height}:r=30[{plate}]"
            )
            parts.append(
                f"[{plate}][{drawn}]overlay={band.inner_x}:{band.inner_y}"
                f":eof_action=repeat:shortest=0[c{index}]"
            )
            current = f"c{index}"
        else:
            parts.append(
                f"[{current}][{drawn}]overlay={band.inner_x}:{band.inner_y}:format=auto"
                f"[c{index}]"
            )
            current = f"c{index}"

    if current is None:
        # A template whose only zone is ``captions``: nothing to compose.
        parts.append(f"[{video_input}]null[c0]")
        current = "c0"

    return ";".join(parts), f"[{current}]"


# --- template files -------------------------------------------------------


def _optional_bool(data: dict[str, Any], key: str) -> bool | None:
    """Read a boolean switch, keeping "absent" distinct from "false".

    A three-state switch (``None`` = the caller decides) cannot go through
    ``bool()``: a typo like ``captions = "sim"`` would quietly become ``True``.
    Anything that is not a real bool is a hard error, same as an unknown key.
    """
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, bool):
        raise ClipperError(f"'{key}' precisa ser true ou false, recebido {value!r}.")
    return value


def from_dict(data: dict[str, Any], *, name: str | None = None) -> Template:
    """Build a template from a parsed config mapping.

    Unknown keys are rejected rather than ignored: a typo in a template file
    silently changing nothing is worse than a hard error, because the user sees
    a render that quietly ignored half of what they wrote.
    """
    known = {
        "name",
        "description",
        "zones",
        "caption_preset",
        "captions",
        "layout",
        "headline_seconds",
        "headline_text",
        "headline_align",
        "headline_font_size",
        "headline_margin_side",
        "headline_margin_top_ratio",
        "reframe_zoom",
        "reframe_pan_x",
        "reframe_pan_y",
        "caption_box_theme",
        "progress_bar",
        "safe_zone_ratio",
        "background",
    }
    unknown = sorted(set(data) - known)
    if unknown:
        raise ClipperError(
            "Chave desconhecida no template: " + ", ".join(unknown) + "."
        )
    raw_zones = data.get("zones")
    if not isinstance(raw_zones, list) or not raw_zones:
        raise ClipperError("Template precisa de uma lista 'zones' nao vazia.")
    zones: list[Zone] = []
    for entry in raw_zones:
        if not isinstance(entry, dict):
            raise ClipperError("Cada zona precisa ser uma tabela/mapa.")
        zone_known = {
            "kind",
            "fraction",
            "source",
            "frame_at",
            "fit",
            "color",
            "margin_top",
            "margin_bottom",
            "margin_left",
            "margin_right",
            "band_dy",
            "corner_radius",
            "zoom",
            "pan_x",
            "pan_y",
            "text",
            "text_size",
            "text_color",
            "text_align",
            "text_valign",
            "text_dx",
            "text_dy",
            "text_bold",
            "text_uppercase",
            "text_outline",
        }
        zone_unknown = sorted(set(entry) - zone_known)
        if zone_unknown:
            raise ClipperError(
                "Chave desconhecida na zona: " + ", ".join(zone_unknown) + "."
            )
        if "kind" not in entry:
            raise ClipperError("Toda zona precisa de 'kind'.")
        if "fraction" not in entry:
            raise ClipperError(f"Zona '{entry['kind']}' precisa de 'fraction'.")
        zones.append(Zone(**entry))

    template = Template(
        name=name or str(data.get("name") or "template"),
        description=str(data.get("description") or ""),
        zones=tuple(zones),
        caption_preset=data.get("caption_preset"),
        layout=data.get("layout"),
        headline_seconds=data.get("headline_seconds"),
        headline_text=data.get("headline_text"),
        headline_align=data.get("headline_align"),
        headline_font_size=data.get("headline_font_size"),
        headline_margin_side=data.get("headline_margin_side"),
        headline_margin_top_ratio=data.get("headline_margin_top_ratio"),
        reframe_zoom=data.get("reframe_zoom"),
        reframe_pan_x=data.get("reframe_pan_x"),
        reframe_pan_y=data.get("reframe_pan_y"),
        caption_box_theme=data.get("caption_box_theme"),
        progress_bar=data.get("progress_bar"),
        safe_zone_ratio=data.get("safe_zone_ratio"),
        captions=_optional_bool(data, "captions"),
        background=str(data.get("background") or "black"),
    )
    template.validate()
    return template


def load_template(path: str | Path) -> Template:
    """Load a template from a ``.toml`` or ``.yaml``/``.yml`` file."""
    from . import config_file

    target = Path(path)
    if not target.exists():
        raise ClipperError(f"Template nao encontrado: {target}")
    # Reuse the config loader so both paths agree on formats and error text.
    data = config_file.load_template_file(target)
    return from_dict(data, name=data.get("name") or target.stem)


# --- variations -----------------------------------------------------------


def expand_variations(
    base: Template,
    *,
    caption_presets: list[str] | None = None,
    layouts: list[str] | None = None,
) -> list[Template]:
    """Cross a template with alternative presets and layouts.

    This is the whole point of templating: one analysis pass over a video
    yields one set of windows, and the render stage multiplies it into a matrix
    without re-scoring anything. Re-encoding is the only cost, and the encode
    was going to happen anyway.
    """
    presets = [p.strip() for p in (caption_presets or []) if p and p.strip()]
    layout_choices = [l.strip() for l in (layouts or []) if l and l.strip()]
    if not presets and not layout_choices:
        return [base]

    variants: list[Template] = []
    for preset in presets or [base.caption_preset]:
        for layout in layout_choices or [base.layout]:
            suffix = "-".join(part for part in (preset, layout) if part)
            variants.append(
                replace(
                    base,
                    name=f"{base.name}__{suffix}",
                    caption_preset=preset,
                    layout=layout,
                )
            )
    return variants


def apply_to_config(config, template: Template):
    """Return a ``ClipConfig`` with the template's overrides applied.

    Only ``None`` fields on the template are left alone, so a template can
    override a config value but never has to repeat the defaults.
    """
    overrides: dict[str, Any] = {}
    if template.caption_preset is not None:
        overrides["caption_preset"] = template.caption_preset
    if template.layout is not None:
        overrides["layout"] = template.layout
    if template.headline_seconds is not None:
        overrides["headline_seconds"] = template.headline_seconds
    if template.headline_text:
        # Só string CHEIA. Um campo vazio no .toml significa "deixa o motor
        # derivar do corte" (o `headline_text` do config é None justamente para
        # isso), então uma string vazia aqui passaria a apagar o texto que o
        # usuário digitou na linha de comando.
        overrides["headline_text"] = template.headline_text
    if template.headline_align is not None:
        overrides["headline_align"] = template.headline_align
    if template.headline_font_size is not None:
        overrides["headline_font_size"] = template.headline_font_size
    if template.headline_margin_side is not None:
        overrides["headline_margin_side"] = template.headline_margin_side
    if template.reframe_zoom is not None:
        overrides["reframe_zoom"] = template.reframe_zoom
    if template.reframe_pan_x is not None:
        overrides["reframe_pan_x"] = template.reframe_pan_x
    if template.reframe_pan_y is not None:
        overrides["reframe_pan_y"] = template.reframe_pan_y
    if template.caption_box_theme is not None:
        overrides["caption_box_theme"] = template.caption_box_theme
    if template.progress_bar is not None:
        overrides["progress_bar"] = template.progress_bar
    if template.captions is not None and not template.captions:
        # Desligar e sempre uma ordem, e o motor tem um valor pronto para isso:
        # ``caption_style = "none"``. Religar pelo template NAO e, porque não
        # existe valor "volta ao que eu tinha" — chutar "karaoke" apagaria um
        # ``--caption-style block`` escolhido na linha de comando sem o usuário
        # ter pedido. Quem religa é o painel da web, omitindo a chave.
        overrides["caption_style"] = "none"
    if template.safe_zone_ratio is not None:
        overrides["caption_margin_v_ratio"] = 1.0 - template.safe_zone_ratio
    if template.headline_margin_top_ratio is not None:
        overrides["headline_margin_top_ratio"] = template.headline_margin_top_ratio
    if not overrides:
        return config
    return replace(config, **overrides)


def describe(template: Template, width: int, height: int) -> str:
    """Human-readable summary of the resolved composition."""
    lines = [f"{template.name} @ {width}x{height}"]
    if template.description:
        lines.append(f"  {template.description}")
    for band in plan_bands(template, width, height):
        if band.kind == "captions":
            lines.append("  captions  full canvas (positioned by libass)")
            continue
        lines.append(
            f"  {band.kind:<8} y={band.y:>4} h={band.height:>4}  "
            f"inner {band.inner_width}x{band.inner_height} at "
            f"({band.inner_x},{band.inner_y})  fit={band.zone.fit}"
        )
        if band.kind == "text":
            x, y, an = text_anchor(band, band.zone, width, height)
            # The anchor is what the render actually uses, so it is what the user
            # needs to see: ``an`` alone would not say where the text lands.
            lines.append(
                f"           texto ancorado em ({x},{y}) an={an} "
                f"size={band.zone.text_size:.3f} align={band.zone.text_align}"
                f"/{band.zone.text_valign}"
            )
    return "\n".join(lines)
