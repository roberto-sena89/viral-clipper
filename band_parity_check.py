"""Prova, no ffmpeg de verdade, que nenhuma linha do composite sai errada.

O composite e ``yuv420p``, entao as duas plantas de croma tem metade da
resolucao: todo retangulo que uma zona desenha tem de cair nessa grade. Um
retangulo que comeca ou termina em linha IMPAR sangra uma linha da propria cor na
faixa vizinha de cima ou de baixo, em TODA fronteira do empilhamento (nao so na
ultima), e uma largura impar trunca a ultima coluna desenhada.

Este script monta o grafo de verdade (``template.compose``), renderiza UM quadro
com quatro cores distinguiveis (fundo, placa de texto, clipe e still) e compara
linha a linha — e a borda de cada still coluna a coluna — com o que o plano de
bandas mandou desenhar. Nao e teoria: o numero que importa e o que sai do ffmpeg.

Rodar: ``python band_parity_check.py``. Sai com codigo 1 se sobrar uma linha
errada, entao serve de check depois de mexer em ``plan_bands``.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from viralclipper import template as tpl

WIDTH, HEIGHT = 1080, 1920

# Cores dos insumos. O fundo do template NAO e preto de proposito: com fundo
# preto e placa de texto preta, uma linha de margem que vazasse a placa para o
# fundo (ou o contrario) seria invisivel para a comparacao. A geometria nao muda
# por causa disso — o que este script mede e a grade de croma.
CLIP = (59, 130, 246)     # azul
STILL = (249, 115, 22)    # laranja
PLATE = (0, 0, 0)         # preto: a placa da faixa de texto
BACKGROUND = (127, 127, 127)  # cinza: o fundo do template

# Tolerancia por canal. O que se procura e uma linha inteira com a cor do vizinho
# (diferenca grande), nao ruido de conversao.
TOLERANCE = 24


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def make_inputs(work: Path) -> tuple[Path, Path]:
    """Um clipe azul solido e um still laranja solido."""
    clip = work / "clip.mp4"
    still = work / "still.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={_hex(CLIP)}:s=1920x1080:r=30:d=1",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={_hex(STILL)}:s=1080x1080:d=1",
         "-frames:v", "1", str(still)],
        check=True,
    )
    return clip, still


def render(graph: str, out_label: str, inputs: list[Path]) -> bytes:
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for item in inputs:
        cmd += ["-i", str(item)]
    cmd += [
        "-filter_complex", graph,
        "-map", out_label, "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    return subprocess.run(cmd, check=True, capture_output=True).stdout


def still_inputs(template: tpl.Template, clip: Path, still: Path) -> list[Path]:
    """Um input por zona que desenha um still, na ordem em que o grafo os le.

    ``compose`` numera os stills a partir de 1, e o renderer de verdade resolve um
    arquivo por zona (``_template_stills``). Aqui todas apontam para o mesmo
    arquivo laranja: o que se mede e a geometria do retangulo, nao o conteudo.
    """
    count = sum(1 for zone in template.zones if zone.kind in {"image", "frame"})
    return [clip] + [still] * count


def content_colour(band: tpl.Band) -> tuple[int, int, int]:
    if band.kind == "video":
        return CLIP
    if band.kind in {"image", "frame"}:
        return STILL
    return PLATE


def expected_row(bands: list[tpl.Band], y: int) -> tuple[int, int, int]:
    """A cor que o plano manda desenhar na linha ``y``.

    Dentro do retangulo INTERNO da faixa e a cor do conteudo dela; fora (ou seja,
    na margem) e o fundo do template.
    """
    for band in bands:
        if band.kind == "captions":
            continue
        if not band.y <= y < band.y + band.height:
            continue
        if band.inner_y <= y < band.inner_y + band.inner_height:
            return content_colour(band)
        return BACKGROUND
    return BACKGROUND


def diff_rows(raw: bytes, bands: list[tpl.Band]) -> list[tuple[int, tuple, tuple]]:
    bad = []
    for y in range(HEIGHT):
        offset = y * WIDTH * 3 + (WIDTH // 2) * 3
        got = tuple(raw[offset: offset + 3])
        want = expected_row(bands, y)
        if max(abs(a - b) for a, b in zip(got, want)) > TOLERANCE:
            bad.append((y, got, want))
    return bad


def diff_columns(raw: bytes, bands: list[tpl.Band]) -> list[tuple[str, int, tuple, tuple]]:
    """Confere as COLUNAS da linha do meio de cada still.

    A largura impar nao sangra linha nenhuma: ela trunca o retangulo, e o defeito
    so aparece nas colunas da borda. Medir so o centro (x=540) escondia isso.

    Numa faixa ``contain`` o letterbox (a cor da zona) mora DENTRO do retangulo
    interno, entao a borda de dentro nao e o still. O que da para travar ali e o
    miolo (tem de ser conteudo) junto com as colunas FORA do retangulo (tem de ser
    fundo) — e esse par que um retangulo deslocado desmancha.
    """
    problems: list[tuple[str, int, tuple, tuple]] = []
    for band in bands:
        if band.kind not in {"image", "frame"}:
            continue
        y = band.inner_y + band.inner_height // 2
        row = y * WIDTH * 3
        if band.zone.fit == "contain":
            checks = (
                (band.inner_x + band.inner_width // 2, STILL),
                (band.inner_x - 1, BACKGROUND),
                (band.inner_x + band.inner_width, BACKGROUND),
            )
        else:
            checks = tuple(
                (x, STILL if band.inner_x <= x < band.inner_x + band.inner_width else BACKGROUND)
                for x in (band.inner_x, band.inner_x + band.inner_width - 1,
                          band.inner_x - 1, band.inner_x + band.inner_width)
            )
        for x, want in checks:
            if not 0 <= x < WIDTH:
                # Fora do quadro nao ha coluna para medir: uma zona sem margem
                # encosta nas duas bordas, e `x=-1` no Python le a ultima coluna
                # da LINHA de cima.
                continue
            got = tuple(raw[row + x * 3: row + x * 3 + 3])
            if max(abs(a - b) for a, b in zip(got, want)) > TOLERANCE:
                problems.append((band.kind, x, got, want))
    return problems


def report(name: str, template: tpl.Template, raw: bytes, bands: list[tpl.Band]) -> bool:
    print(f"\n=== {name} @ {WIDTH}x{HEIGHT} ===")
    for band in bands:
        if band.kind == "captions":
            print("   captions  full canvas")
            continue
        print(
            f"   {band.kind:<8} y={band.y:>4} h={band.height:>4} "
            f"inner {band.inner_width}x{band.inner_height} "
            f"at ({band.inner_x},{band.inner_y})"
        )
    odd = [
        f"{band.kind}.{axis}"
        for band in bands
        for axis in ("y", "height", "inner_x", "inner_y", "inner_width", "inner_height")
        if getattr(band, axis) % 2
    ]
    if odd:
        print(f"   GEOMETRIA IMPAR: {', '.join(odd)}")
    bad = diff_rows(raw, bands)
    cols = diff_columns(raw, bands)
    if not bad and not cols:
        print("   OK — nenhuma linha e nenhuma coluna errada")
        return not odd
    for y, got, want in bad[:8]:
        print(f"   linha errada y={y} {got} (esperado {want})")
    for kind, x, got, want in cols[:8]:
        print(f"   coluna errada x={x} em {kind} {got} (esperado {want})")
    return False


def build(name: str, zones: tuple[tpl.Zone, ...]) -> tpl.Template:
    return tpl.Template(name=name, zones=zones, background=_hex(BACKGROUND))


def cases() -> list[tuple[str, tpl.Template]]:
    """Os formatos reais mais os que ja falharam.

    ``0,26`` e ``0,25`` da zona de imagem sao os dois que motivaram a regra: a
    altura de 499 px e a de 480 px perdiam linha, entao o defeito nunca foi a
    altura e sim o offset.
    """
    meme = lambda fraction: build(
        f"meme-{fraction}",
        (
            tpl.Zone(kind="text", fraction=0.16, text="POV: teste", color=_hex(PLATE),
                     margin_top=0.008, margin_bottom=0.008,
                     margin_left=0.05, margin_right=0.05),
            tpl.Zone(kind="video", fraction=1.0 - 0.16 - fraction),
            tpl.Zone(kind="image", fraction=fraction, source="id.png",
                     margin_top=0.012, margin_bottom=0.012,
                     margin_left=0.03, margin_right=0.03, corner_radius=0.035),
        ),
    )
    return [
        ("meme 0,26 (a altura impar do relato)", meme(0.26)),
        ("meme 0,25 (altura par, tambem perdia)", meme(0.25)),
        ("x (imagem contain em cima)", build(
            "x",
            (
                tpl.Zone(kind="image", fraction=0.34, source="tweet.png", fit="contain",
                         margin_top=0.012, margin_bottom=0.012,
                         margin_left=0.03, margin_right=0.03, corner_radius=0.035),
                tpl.Zone(kind="video", fraction=0.66),
            ),
        )),
        ("split-card (built-in, zona de frame)", build(
            "split-card",
            (
                tpl.Zone(kind="video", fraction=0.62),
                tpl.Zone(kind="frame", fraction=0.38, fit="cover",
                         margin_top=0.012, margin_bottom=0.012,
                         margin_left=0.03, margin_right=0.03, corner_radius=0.035),
            ),
        )),
        ("cinco zonas (tiling irregular)", build(
            "cinco",
            (
                tpl.Zone(kind="solid", fraction=0.15, color=_hex(PLATE)),
                tpl.Zone(kind="video", fraction=0.41),
                tpl.Zone(kind="solid", fraction=0.11, color=_hex(PLATE),
                         margin_top=0.007, margin_bottom=0.007),
                tpl.Zone(kind="frame", fraction=0.21, fit="cover",
                         margin_top=0.009, margin_bottom=0.009),
                tpl.Zone(kind="image", fraction=0.12, source="id.png",
                         margin_top=0.011, margin_bottom=0.011),
            ),
        )),
    ]


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="vc_band_parity_"))
    clip, still = make_inputs(work)
    failures = 0
    for name, template in cases():
        graph, out = tpl.compose(template, WIDTH, HEIGHT)
        raw = render(graph, out, still_inputs(template, clip, still))
        if not report(name, template, raw, tpl.plan_bands(template, WIDTH, HEIGHT)):
            failures += 1
    print(f"\nwork: {work}")
    if failures:
        print(f"FALHOU em {failures} caso(s)")
        return 1
    print("todos os casos passaram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
