"""Named caption looks for the burned-in subtitles.

The render layer exposes raw knobs (font, size, outline, box, highlight color);
a creator should not have to pick each one by hand. A preset bundles the whole
look so the decision is made once, per channel. The defaults follow the
consensus for short-video captions: bold sans-serif, white fill, thin dark
outline, 2-5 words on screen at a time, in the lower-middle safe zone.

Every field of the preset can still be overridden per run; ``None`` in the
config means "take the preset value".
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .util import ClipperError

# ASS colours are &HAABBGGRR (alpha, then B, G, R). White is &H00FFFFFF,
# yellow &H0000FFFF, neon green &H0088FF00, black &H00000000.


@dataclass(frozen=True)
class CaptionPreset:
    """Everything one caption style needs to render."""

    name: str
    description: str
    font: str
    font_size: int
    # BorderStyle 1 = outline + drop shadow; 3 = opaque box behind the text.
    border_style: int = 1
    outline_width: float = 6.0
    shadow_depth: float = 3.0
    primary_color: str = "&H00FFFFFF"
    outline_color: str = "&H00000000"
    back_color: str = "&H80000000"
    # The opaque box (BorderStyle 3) is painted by libass with the OUTLINE
    # colour, not the back colour - so a boxed preset names its box here and
    # the render moves it into the outline slot.
    box_color: str | None = None
    bold: bool = True
    italic: bool = False
    margin_v: int = 640
    words_per_line: int = 3
    highlight_color: str = "&H0000FFFF"
    highlight_scale: int = 112
    uppercase: bool = True
    fade_in_ms: int = 40
    fade_out_ms: int = 30


PRESETS: dict[str, CaptionPreset] = {
    # The shipped default: white bold text, thin dark outline, yellow karaoke
    # highlight. This is the proven "safe" look from the short-video consensus.
    "karaoke": CaptionPreset(
        name="karaoke",
        description="Karaokê clássico — branco com destaque amarelo por palavra",
        font="Arial Black",
        font_size=84,
    ),
    # Platform default for TikTok/Reels: same karaoke language, but tuned to
    # the networks' rules — 2 words per pop for retention, and a bottom
    # margin that sits above the progress bar, video caption and action rail.
    "social": CaptionPreset(
        name="social",
        description="Padrão TikTok/Reels — pop amarelo de 2 palavras na zona segura",
        font="Arial Black",
        font_size=88,
        margin_v=560,
        words_per_line=2,
    ),
    # White text on a dark, semi-transparent box: the right answer when the
    # footage is light or busy and an outline alone cannot separate the text.
    "bold-box": CaptionPreset(
        name="bold-box",
        description="Texto branco sobre caixa escura — legível em qualquer fundo",
        font="Arial Black",
        font_size=80,
        border_style=3,
        outline_width=14.0,
        shadow_depth=0.0,
        box_color="&HB3141417",
        words_per_line=4,
    ),
    # Minimal: sentence case, lighter, small outline and shadow. For channels
    # that want the captions to whisper instead of shout.
    "minimal": CaptionPreset(
        name="minimal",
        description="Discreto — caixa de frase, contorno fino, sem caixa",
        font="Arial",
        font_size=64,
        outline_width=2.0,
        shadow_depth=1.5,
        words_per_line=4,
        highlight_scale=106,
        uppercase=False,
    ),
    # Neon: green highlight with a slightly deeper shadow. Pops on dark,
    # cinematic footage without a box.
    "neon": CaptionPreset(
        name="neon",
        description="Destaque verde neon em fundo escuro — alto contraste",
        font="Arial Black",
        font_size=84,
        outline_width=5.0,
        shadow_depth=4.0,
        highlight_color="&H0088FF00",
        highlight_scale=114,
    ),
    # Dark block: opaque bar across the line. The strongest separation when the
    # footage has bright highlights right where the captions sit.
    "block-dark": CaptionPreset(
        name="block-dark",
        description="Bloco escuro opaco — separação máxima do vídeo",
        font="Arial Black",
        font_size=78,
        border_style=3,
        outline_width=16.0,
        shadow_depth=0.0,
        box_color="&HE6101014",
        words_per_line=4,
    ),
    # Mono: a monospaced face for tech, code and developer channels.
    "mono": CaptionPreset(
        name="mono",
        description="Monoespaçada — canais de tecnologia e código",
        font="Consolas",
        font_size=76,
        outline_width=4.0,
        shadow_depth=2.5,
        highlight_color="&H0088FF00",
        words_per_line=4,
    ),

    # --- high-impact vibrant looks -------------------------------------------
    # Short-video retention favors one strong highlight against white: these
    # presets keep the proven structure (bold sans, dark outline, safe zone)
    # and rotate the vibrant accent and box treatments.
    "fire": CaptionPreset(
        name="fire",
        description="Destaque laranja-fogo — energia e urgência",
        font="Arial Black",
        font_size=84,
        outline_width=6.0,
        shadow_depth=3.5,
        highlight_color="&H000055FF",
    ),
    "magenta-pop": CaptionPreset(
        name="magenta-pop",
        description="Destaque magenta vivo — pop e irreverente",
        font="Arial Black",
        font_size=84,
        outline_width=5.0,
        shadow_depth=4.0,
        highlight_color="&H00CC00FF",
    ),
    "cyan-pop": CaptionPreset(
        name="cyan-pop",
        description="Destaque ciano elétrico — moderno e frio",
        font="Arial Black",
        font_size=84,
        outline_width=5.0,
        shadow_depth=3.0,
        highlight_color="&H00FFE500",
    ),
    "lime-hit": CaptionPreset(
        name="lime-hit",
        description="Destaque lima ácido — juvenil, alto contraste",
        font="Arial Black",
        font_size=84,
        outline_width=5.0,
        shadow_depth=3.5,
        highlight_color="&H0000FFCC",
    ),
    "blood": CaptionPreset(
        name="blood",
        description="Destaque vermelho sangue, contorno grosso — drama e choque",
        font="Impact",
        font_size=88,
        outline_width=8.0,
        shadow_depth=4.0,
        highlight_color="&H002D2DFF",
    ),
    "gold-box": CaptionPreset(
        name="gold-box",
        description="Destaque dourado sobre caixa escura — autoridade e premium",
        font="Arial Black",
        font_size=80,
        border_style=3,
        outline_width=14.0,
        shadow_depth=0.0,
        box_color="&HB3141417",
        highlight_color="&H0000D7FF",
        words_per_line=4,
    ),
    "candy": CaptionPreset(
        name="candy",
        description="Caixa branca, texto escuro, destaque rosa — doce e claro",
        font="Arial Black",
        font_size=80,
        border_style=3,
        outline_width=14.0,
        shadow_depth=0.0,
        primary_color="&H00141414",
        box_color="&HCCF4F4F4",
        highlight_color="&H00A56FFF",
        words_per_line=4,
    ),
    "violet-vibe": CaptionPreset(
        name="violet-vibe",
        description="Destaque violeta com sombra funda — criativo e noturno",
        font="Arial Black",
        font_size=84,
        outline_width=5.0,
        shadow_depth=4.5,
        highlight_color="&H00FF6BB2",
    ),
    "ice-blue": CaptionPreset(
        name="ice-blue",
        description="Destaque azul-gelo em caixa escura fina — limpo e tecnico",
        font="Arial",
        font_size=72,
        border_style=3,
        outline_width=10.0,
        shadow_depth=0.0,
        box_color="&HCC14181F",
        highlight_color="&H00FFCC66",
        words_per_line=4,
    ),
    "sunset": CaptionPreset(
        name="sunset",
        description="Destaque laranja-pôr-do-sol — quente sem gritar",
        font="Impact",
        font_size=88,
        outline_width=7.0,
        shadow_depth=3.5,
        highlight_color="&H00007AFF",
    ),
    "bubble": CaptionPreset(
        name="bubble",
        description="Caixa azul-noite, destaque amarelo — conversa e podcast",
        font="Segoe UI",
        font_size=80,
        border_style=3,
        outline_width=13.0,
        shadow_depth=0.0,
        box_color="&HD61A1E33",
        highlight_color="&H0000F2FF",
        words_per_line=4,
    ),
    "ultra-impact": CaptionPreset(
        name="ultra-impact",
        description="Impact gigante, contorno grosso — o máximo de impacto",
        font="Impact",
        font_size=96,
        outline_width=9.0,
        shadow_depth=5.0,
        highlight_color="&H0000FFFF",
        highlight_scale=116,
        words_per_line=2,
    ),
    "slim": CaptionPreset(
        name="slim",
        description="Narrow com destaque ciano — compacto e informativo",
        font="Arial Narrow",
        font_size=74,
        outline_width=3.0,
        shadow_depth=2.0,
        highlight_color="&H00FFE500",
        words_per_line=5,
    ),
    "cobalt": CaptionPreset(
        name="cobalt",
        description="Caixa escura com destaque ciano — corporativo afiado",
        font="Segoe UI",
        font_size=78,
        border_style=3,
        outline_width=12.0,
        shadow_depth=0.0,
        box_color="&HE6101218",
        highlight_color="&H00FFE500",
        words_per_line=4,
    ),
    "pop-box": CaptionPreset(
        name="pop-box",
        description="Caixa amarela, texto escuro, destaque vermelho — chamativo",
        font="Arial Black",
        font_size=80,
        border_style=3,
        outline_width=14.0,
        shadow_depth=0.0,
        primary_color="&H00141414",
        box_color="&HCC00F2FF",
        highlight_color="&H002D2DFF",
        words_per_line=4,
    ),

    # --- article fonts -------------------------------------------------------
    # The Piktochart/Streamlabs/Kapwing consensus: bold sans-serif faces with
    # open counters win on small screens; screen-designed serifs (Merriweather,
    # Arvo) serve slower, editorial content. Fonts below either ship with
    # Windows or are free on Google Fonts - install the Google ones from
    # fonts.google.com, otherwise libass falls back to a system face and the
    # look will not match the preview.
    "roboto-bold": CaptionPreset(
        name="roboto-bold",
        description="Roboto — padrão dos shorts e do YouTube, destaque ciano",
        font="Roboto",
        font_size=80,
        outline_width=5.0,
        shadow_depth=3.0,
        highlight_color="&H00FFE500",
        words_per_line=4,
    ),
    "inter-bold": CaptionPreset(
        name="inter-bold",
        description="Inter — legibilidade máxima em tela pequena, destaque lima",
        font="Inter",
        font_size=78,
        outline_width=5.0,
        shadow_depth=3.0,
        highlight_color="&H0000FFCC",
        words_per_line=4,
    ),
    "poppins-bold": CaptionPreset(
        name="poppins-bold",
        description="Poppins — geométrica e redonda, destaque magenta",
        font="Poppins",
        font_size=80,
        outline_width=5.0,
        shadow_depth=3.5,
        highlight_color="&H00CC00FF",
        words_per_line=4,
    ),
    "montserrat-bold": CaptionPreset(
        name="montserrat-bold",
        description="Montserrat — estilo TikTok, destaque dourado",
        font="Montserrat",
        font_size=80,
        outline_width=5.0,
        shadow_depth=3.0,
        highlight_color="&H0000D7FF",
        words_per_line=4,
    ),
    "dm-sans": CaptionPreset(
        name="dm-sans",
        description="DM Sans — minimalismo suíço, destaque laranja",
        font="DM Sans",
        font_size=76,
        outline_width=4.0,
        shadow_depth=3.0,
        highlight_color="&H000055FF",
        words_per_line=4,
    ),
    "cabin-bold": CaptionPreset(
        name="cabin-bold",
        description="Cabin — aberta e amigável, destaque ciano claro",
        font="Cabin",
        font_size=78,
        outline_width=4.0,
        shadow_depth=2.5,
        highlight_color="&H00FFE500",
        words_per_line=4,
    ),
    "verdana-bold": CaptionPreset(
        name="verdana-bold",
        description="Verdana — x-height gigante, destaque amarelo",
        font="Verdana",
        font_size=74,
        outline_width=4.0,
        shadow_depth=3.0,
        highlight_color="&H0000FFFF",
        words_per_line=4,
    ),
    "trebuchet-bold": CaptionPreset(
        name="trebuchet-bold",
        description="Trebuchet MS — humanista e limpa, destaque vermelho",
        font="Trebuchet MS",
        font_size=78,
        outline_width=4.0,
        shadow_depth=3.0,
        highlight_color="&H002D2DFF",
        words_per_line=4,
    ),
    "tahoma-bold": CaptionPreset(
        name="tahoma-bold",
        description="Tahoma — estreita e legível, destaque ciano",
        font="Tahoma",
        font_size=78,
        outline_width=4.0,
        shadow_depth=2.5,
        highlight_color="&H00FFE500",
        words_per_line=4,
    ),
    "calibri-bold": CaptionPreset(
        name="calibri-bold",
        description="Calibri — clara e moderna, destaque lima",
        font="Calibri",
        font_size=78,
        outline_width=4.0,
        shadow_depth=3.0,
        highlight_color="&H0000FFCC",
        words_per_line=4,
    ),
    "franklin-bold": CaptionPreset(
        name="franklin-bold",
        description="Franklin Gothic — condensada clássica, destaque fogo",
        font="Franklin Gothic Medium",
        font_size=80,
        outline_width=5.0,
        shadow_depth=3.5,
        highlight_color="&H000055FF",
        words_per_line=4,
    ),
    "segoe-black": CaptionPreset(
        name="segoe-black",
        description="Segoe UI — moderna nativa, destaque violeta",
        font="Segoe UI",
        font_size=80,
        outline_width=5.0,
        shadow_depth=3.5,
        highlight_color="&H00FF6BB2",
        words_per_line=4,
    ),
    "helvetica-classic": CaptionPreset(
        name="helvetica-classic",
        description="Helvetica — o amarelo clássico do cinema",
        font="Helvetica",
        font_size=80,
        outline_width=2.5,
        shadow_depth=2.0,
        highlight_color="&H0000FFFF",
        words_per_line=4,
    ),
    "merriweather-black": CaptionPreset(
        name="merriweather-black",
        description="Merriweather — serifada editorial para ritmo lento",
        font="Merriweather",
        font_size=78,
        outline_width=3.0,
        shadow_depth=2.5,
        highlight_color="&H0000D7FF",
        words_per_line=5,
    ),
    "arvo-bold": CaptionPreset(
        name="arvo-bold",
        description="Arvo — serifada de telão para entrevistas",
        font="Arvo",
        font_size=78,
        outline_width=3.0,
        shadow_depth=2.5,
        highlight_color="&H0000FFFF",
        words_per_line=5,
    ),
}


def get_preset(name: str) -> CaptionPreset:
    """Look up a preset by name, with a helpful error listing the choices."""
    key = (name or "").strip().lower()
    preset = PRESETS.get(key)
    if preset is None:
        known = ", ".join(sorted(PRESETS))
        raise ClipperError(f"Preset de legenda desconhecido: '{name}'. Escolha entre: {known}.")
    return preset


def resolve(config) -> CaptionPreset:
    """Resolve the active caption style: preset first, explicit overrides win.

    Any ``None`` field on the config means "use the preset's value"; a set
    field is an explicit choice and beats the preset.
    """
    base = get_preset(config.caption_preset)
    box_theme = config.caption_box_theme
    boxed_base = base.border_style == 3
    return replace(
        base,
        font=config.font if config.font is not None else base.font,
        font_size=config.font_size if config.font_size is not None else base.font_size,
        primary_color=(
            base.primary_color
            if box_theme is None
            else ("&H00FFFFFF" if box_theme == "dark" else "&H00141414")
        ),
        border_style=base.border_style if box_theme is None else 3,
        outline_width=(
            base.outline_width
            if box_theme is None or boxed_base
            else 14.0
        ),
        shadow_depth=base.shadow_depth if box_theme is None else 0.0,
        box_color=(
            base.box_color
            if box_theme is None
            else ("&HB3141417" if box_theme == "dark" else "&H00F2F2F2")
        ),
        margin_v=config.caption_margin_v
        if config.caption_margin_v is not None
        else base.margin_v,
        words_per_line=(
            config.caption_words_per_line
            if config.caption_words_per_line is not None
            else base.words_per_line
        ),
        highlight_color=(
            config.highlight_color if config.highlight_color is not None else base.highlight_color
        ),
        uppercase=(
            config.uppercase_captions if config.uppercase_captions is not None else base.uppercase
        ),
    )