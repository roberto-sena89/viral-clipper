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
    # ``image``: path to the still. ``frame``: ignored, the renderer extracts it.
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
        if self.kind == "image" and not self.source:
            raise ClipperError(f"{where}: image exige um caminho em 'source'.")
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
    layout: str | None = None
    headline_seconds: float | None = None
    headline_align: str | None = None
    headline_font_size: int | None = None
    headline_margin_side: int | None = None
    reframe_zoom: float | None = None
    reframe_pan_x: float | None = None
    reframe_pan_y: float | None = None
    caption_box_theme: str | None = None
    progress_bar: bool | None = None
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
            margin_top=0.012,
            margin_bottom=0.012,
            margin_left=0.03,
            margin_right=0.03,
            corner_radius=0.035,
        ),
        Zone(kind="captions", fraction=0.0),
    ),
)

BUILTIN: dict[str, Template] = {
    FULL_FRAME.name: FULL_FRAME,
    SPLIT_CARD.name: SPLIT_CARD,
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


def plan_bands(template: Template, width: int, height: int) -> list[Band]:
    """Resolve every zone to a pixel rectangle, top to bottom.

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
        band_height = int(round(height * zone.fraction))
        # Give the last pixel-hungry zone whatever integer rounding left over so
        # the bands always tile the canvas exactly.
        if zone is template.zones[-1] and cursor + band_height != height:
            band_height = height - cursor
        inner_x = x = int(round(width * zone.margin_left))
        inner_y = cursor + int(round(height * zone.margin_top))
        inner_width = max(2, width - x - int(round(width * zone.margin_right)))
        inner_height = max(
            2, band_height - int(round(height * (zone.margin_top + zone.margin_bottom)))
        )
        bands.append(
            Band(
                kind=zone.kind,
                x=0,
                y=cursor,
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

def scale_into(band: Band, zone: Zone, label_in: str) -> str:
    """Build the scale/pad fragment that fits ``label_in`` inside a band.

    ``cover`` fills the band and crops the overflow (the usual choice for
    footage: no letterbox bars). ``contain`` shrinks to fit and pads with the
    zone colour, which is right for a logo that must never be cropped.
    """
    w, h = band.inner_width, band.inner_height
    if zone.fit == "contain":
        return (
            f"[{label_in}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={zone.color},setsar=1"
        )
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
            parts.append(scale_into(band, zone, source) + f"[{label}s]")
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
        "layout",
        "headline_seconds",
        "headline_align",
        "headline_font_size",
        "headline_margin_side",
        "reframe_zoom",
        "reframe_pan_x",
        "reframe_pan_y",
        "caption_box_theme",
        "progress_bar",
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
        headline_align=data.get("headline_align"),
        headline_font_size=data.get("headline_font_size"),
        headline_margin_side=data.get("headline_margin_side"),
        reframe_zoom=data.get("reframe_zoom"),
        reframe_pan_x=data.get("reframe_pan_x"),
        reframe_pan_y=data.get("reframe_pan_y"),
        caption_box_theme=data.get("caption_box_theme"),
        progress_bar=data.get("progress_bar"),
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
