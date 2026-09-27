"""Render real de uma faixa de texto com placa de fundo, nas tres resolucoes.

Este script responde a uma pergunta que o grafo sozinho nao responde: o
ffmpeg aceitou a textura como input e a pintou NA FAIXA, ou o render saiu
com a cor e nenhum aviso? O grafo pode estar perfeito e ainda assim o
arquivo final ter a placa errada -- o mesmo buraco que a banda tem: um
`overlay` de um still de um frame, e o `-t` do chamador.

A verificacao e por AMOSTRA DE PIXEL, e nao por "o comando passou": o ffmpeg
sai com codigo 0 mesmo quando um filtro foi ignorado, entao o unico jeito de
saber o que foi para o arquivo e ler o pixel. Sai de codigo 1 na falha.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from viralclipper import render  # noqa: E402
from viralclipper import template as tpl  # noqa: E402
from viralclipper.config import ClipConfig  # noqa: E402

PLATE = Path("web/fundo titulo/1.jpg").resolve()
# Uma cor que nao existe em nenhum dos dois modelos: se a textura nao for
# pintada, a faixa sai com o AZUL e o teste falha gritando. Escolher a cor da
# placa (vermelho) deixaria um "verde em vez de vermelho" indistinguivel de
# "vermelho certo" numa leitura apressada.
SENTINEL = "0x0000FF"

CANVASES = [(1080, 1920), (720, 1280), (1440, 2560)]


def build_clip(dest: Path) -> None:
    """Um clipe de teste: 3 segundos de cor solida, sem audio, sem legenda."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=0x20C020:s=1920x1080:d=3:r=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)],
        check=True,
    )


def sample(path: Path, y: int, x: int) -> tuple[int, int, int]:
    """A cor media de um bloco 16x16 em (x, y), como RGB.

    Tres detalhes, todos aprendidos na marra:

    * ``crop=1:1`` NAO funciona. O composite e ``yuv420p``, e a croma e
      subamostrada: o ffmpeg recusa um recorte de um pixel com "width 0 or
      height 0" e sai com codigo != 0. O bloco tem de ter pelo menos 2 px de cada
      lado; 16 deixa a media resisting a uma borda suja.
    * A media vem de um ``scale=1:1`` e nao dos bytes crus. Ler o primeiro pixel
      do bloco seria mais simples, mas escolheria UM ponto — e num still com
      textura, um ponto so e ruido.
    * O ffmpeg sai com codigo 0 mesmo quando um filtro foi ignorado, entao o
      unico jeito de saber o que foi para o arquivo e ler o pixel de volta.
    """
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"crop=16:16:{x}:{y},scale=1:1",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    )
    return tuple(out.stdout[:3])


def main() -> int:
    if not PLATE.is_file():
        print(f"placa nao encontrada: {PLATE}")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="placa_"))
    clip = tmp / "src.mp4"
    build_clip(clip)

    failures = 0
    for width, height in CANVASES:
        out = tmp / f"placa_{width}x{height}.mp4"
        work = tmp / f"work_{width}x{height}"
        # A mesma pilha do formato Viral: video, faixa de texto, imagem. A placa
        # entra na FAIXA DE TEXTO, que e a que o usuario esta vestindo.
        template = tpl.Template(
            name="viral-com-placa",
            zones=(
                tpl.Zone(kind="video", fraction=0.44),
                tpl.Zone(
                    kind="text", fraction=0.16,
                    text="ISSO AQUI VAI VIRALIZAR",
                    plate_image=str(PLATE), color=SENTINEL,
                    margin_top=0.018, margin_bottom=0.018,
                    margin_left=0.03, margin_right=0.03,
                ),
                tpl.Zone(kind="image", fraction=0.40, source=str(clip)),
            ),
            captions=False,
        )
        # `burn_captions` LIGADO de proposito: e o libass que queima a frase da
        # zona, entao e ele — e nao a placa — que decide se o texto aparece. Com
        # a legenda desligada o arquivo sairia com a textura e sem frase, e o
        # teste passaria na cor sem nunca ter provado o que importa: a frase
        # legivel sobre a textura.
        config = ClipConfig(
            url="", width=width, height=height, vertical=True,
            output_dir=tmp, work_dir=work, burn_captions=True,
        )
        render.render_clip(
            source=clip, destination=out,
            clip_start=0.0, clip_end=2.0,
            config=config, ffmpeg="ffmpeg", ffprobe="ffprobe",
            words=[], silences=None, work_dir=work, template=template,
        )

        # A amostra da placa fica NO TOPO da area interna, e nao no meio: o texto
        # e queimado no meio da faixa, e um branco lido ali seria "a textura foi
        # pintada" e "o texto cobreu a textura" com a mesma cara. A 8 px da borda
        # superior interna a linha de texto ainda nao chegou.
        interno_y = int(height * (0.44 + 0.018)) + 8
        cor = sample(out, interno_y, width // 2 - 8)
        # O azul e a sentinela: e o que aparece se a textura nao foi pintada.
        azul = (0, 0, 255)
        if cor[2] > 200 and cor[0] < 60 and cor[1] < 60:
            print(f"[{width}x{height}] FALHOU: a faixa saiu com a cor, sem a textura "
                  f"{cor} (sentinela {azul})")
            failures += 1
            continue
        # A faixa de video acima tem de continuar VERDE: a textura nao pode ter
        # vazado para cima nem para baixo, o que aconteceria se o overlay
        # cobrisse a faixa inteira em vez da area interna.
        vcor = sample(out, int(height * 0.20), width // 2 - 8)
        if vcor[1] < 0x60 or vcor[0] > 0x80:
            print(f"[{width}x{height}] FALHOU: a faixa de video foi contaminada "
                  f"pela placa ({vcor})")
            failures += 1
            continue
        print(f"[{width}x{height}] ok  placa={cor}  video={vcor}  {out.name}")

    failures += checa_pan_e_zoom(tmp)

    print(f"FALHAS: {failures}" if failures else "todas as resolucoes ok")
    return 1 if failures else 0


def _placa_de_teste(dest: Path) -> None:
    """Uma placa com degradê, para o recorte ter o que mostrar.

    A placa de verdade (`1.jpg`) e uma pincelada vermelha quasi uniforme: com o
    enquadramento certo, uma janela de corte e outra dao pixels praticamente
    iguais, e o teste passaria (ou falharia) por acaso em vez de medir. Um
    degradê muda de valor em CADA posicao, entao qualquer deslocamento da janela
    aparece logo no pixel lido.

    O degradê e diagonal de proposito: assim os DOIS eixos mudam, e um `pan` que
    so afectasse um deles — ou que nao affectasse nenhum — fica visivel.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black:s=1400x400",
         "-vf", "geq=r='(X/W)*255':g='(Y/H)*255':b='((X+Y)/(W+H))*255'",
         "-frames:v", "1", str(dest)],
        check=True,
    )


def _render_com_placa(clip: Path, work: Path, out: Path, plate: Path, **ajuste) -> None:
    """Renderiza o mesmo template de sempre, variando so o enquadramento.

    O que muda entre as chamadas e o `plate_image` que entra no TOML pelo painel
    — `zoom`, `pan_x`, `pan_y`. Todo o resto (zonas, texto, margens) fica igual,
    para que a diferenca entre duas saidas seja atribuivel ao ajuste e nao a
    outra coisa.
    """
    template = tpl.Template(
        name="viral-com-placa",
        zones=(
            tpl.Zone(kind="video", fraction=0.44),
            tpl.Zone(
                kind="text", fraction=0.16,
                text="ISSO AQUI VAI VIRALIZAR",
                plate_image=str(plate), color=SENTINEL,
                margin_top=0.018, margin_bottom=0.018,
                margin_left=0.03, margin_right=0.03,
                **ajuste,
            ),
            tpl.Zone(kind="image", fraction=0.40, source=str(clip)),
        ),
        captions=False,
    )
    config = ClipConfig(
        url="", width=1080, height=1920, vertical=True,
        output_dir=out.parent, work_dir=work, burn_captions=True,
    )
    render.render_clip(
        source=clip, destination=out,
        clip_start=0.0, clip_end=2.0,
        config=config, ffmpeg="ffmpeg", ffprobe="ffprobe",
        words=[], silences=None, work_dir=work, template=template,
    )


def checa_pan_e_zoom(tmp: Path) -> int:
    """Prova que o enquadramento da placa CHEGA no arquivo, e nao so no painel.

    Tres medidas, porque sao tres jeitos distintos de o ajuste morrer no caminho:
    o grafo, o pixel com o pan, e o pixel com o zoom.
    """
    falhas = 0
    clip = tmp / "src.mp4"
    plate = tmp / "placa_degrade.png"
    _placa_de_teste(plate)
    work = tmp / "work_pan"

    # O grafo, lido de `compose` direto. Nao por interceptar o ffmpeg: a funcao que
    # ESCREVE o filtro e a mesma que a previa usa para montar o CSS, entao o que
    # ela devolve e a verdade sobre as duas pontas.
    def grafo(**ajuste) -> str:
        z = tpl.Zone(
            kind="text", fraction=0.16, text="X", plate_image=str(plate),
            color=SENTINEL, margin_top=0.018, margin_bottom=0.018,
            margin_left=0.03, margin_right=0.03, **ajuste,
        )
        t = tpl.Template(
            name="v", zones=(tpl.Zone(kind="video", fraction=0.44), z,
                             tpl.Zone(kind="image", fraction=0.40, source="x.png")))
        return tpl.compose(t, 1080, 1920)[0]

    g_centro, g_esq = grafo(), grafo(pan_x=0.0, pan_y=0.0)
    g_dir, g_zoom = grafo(pan_x=1.0, pan_y=1.0), grafo(zoom=2.0)

    def crop_da_placa(g: str) -> str:
        """O `crop` da PLACA, e nao o da zona de video.

        A zona de video tem o crop dela tambem — `crop=1080:844`, sem expressao.
        A placa e a que usa `in_w-out_w`, que e a assinatura do pan. Procurar o
        primeiro `crop` da string checaria a zona errada e daria um FALSO
        POSITIVO sempre que o pan estivesse certo.
        """
        for pedaco in g.split(";"):
            if "in_w-out_w" in pedaco:
                return pedaco
        return ""

    c_esq, c_dir, c_centro = crop_da_placa(g_esq), crop_da_placa(g_dir), crop_da_placa(g_centro)
    if not c_esq:
        print("FALHOU: o crop da placa nem usa in_w-out_w — o pan nao entrou no grafo")
        falhas += 1
    elif "*0" not in c_esq or "*1" not in c_dir:
        print(f"FALHOU: o pan nao chegou no crop: esq={c_esq[-40:]} dir={c_dir[-40:]}")
        falhas += 1
    else:
        print(f"ok  o grafo carrega o pan no crop da placa ({c_esq.split('crop=')[1][:34]})")
    if c_centro == c_esq == c_dir:
        print("FALHOU: o crop da placa e o mesmo nos tres ajustes")
        falhas += 1

    # A banda interna tem 1080*0,94 = 1015 px, arredondada para par pelo
    # `evenFloor` do `plan_bands` — daí 2032 e não 2030 com o zoom 2. O grafo
    # tem de mostrar o dobro da LARGURA REAL da area interna, e nao um numero
    # redondo chutado: um teste que passa so quando a conta bate na cabeca nao
    # prende o comportamento, prende a suposicao.
    escala_placa = [s for s in g_zoom.split(";") if "force_original" in s]
    banda = tpl.plan_bands(tpl.Template(
        name="v", zones=(tpl.Zone(kind="video", fraction=0.44),
                         tpl.Zone(kind="text", fraction=0.16, text="X",
                                  plate_image=str(plate), color=SENTINEL,
                                  margin_top=0.018, margin_bottom=0.018,
                                  margin_left=0.03, margin_right=0.03),
                         tpl.Zone(kind="image", fraction=0.40, source="x.png"))),
        1080, 1920)[1]
    esperado = 2 * banda.inner_width
    achou = any(f"scale={esperado}:" in e for e in escala_placa)
    if not escala_placa:
        print("FALHOU: o grafo da placa nem tem scale")
        falhas += 1
    elif not achou:
        print(f"FALHOU: o zoom nao chegou no scale da placa: esperava "
              f"{esperado}, veio {[e[:20] for e in escala_placa]}")
        falhas += 1
    else:
        print(f"ok  o grafo carrega o zoom no scale da placa "
              f"({[e[:18] for e in escala_placa if str(esperado) in e][0]})")

    # O pixel. O degradee da esquerda para a direita E de cima para baixo, entao
    # qualquer deslocamento da janela muda a cor lida.
    #
    # A amostra vem das coordenadas da BANDA, nao de um numero chutado. Duas
    # armadilhas ja pagaram por isso aqui: a area interna comeca depois das
    # margens verticais (uma amostra em 0.452 cai no VIDEO verde, acima da faixa),
    # e o MEIO da faixa e onde o libass queima a frase — ali a cor lida e a do
    # TEXTO em qualquer ajuste, e o teste passaria com o pan morto. A amostra
    # vai entao no canto da area interna, 12% dentro e 30% acima.
    ax = banda.inner_x + int(banda.inner_width * 0.12)
    ay = banda.inner_y + int(banda.inner_height * 0.30)

    def amostra(out: Path) -> tuple[int, int, int]:
        return sample(out, ay, ax)

    casos = {
        "pan_topo_esq": {"pan_x": 0.0, "pan_y": 0.0},
        "pan_baixo_dir": {"pan_x": 1.0, "pan_y": 1.0},
        "neutro": {},
        "zoom": {"zoom": 2.0, "pan_x": 0.5, "pan_y": 0.5},
    }
    saidas = {}
    for nome, ajuste in casos.items():
        out = tmp / f"pan_{nome}.mp4"
        _render_com_placa(clip, work / nome, out, plate, **ajuste)
        saidas[nome] = amostra(out)

    def dist(a: tuple[int, ...], b: tuple[int, ...]) -> int:
        return sum(abs(x - y) for x, y in zip(a, b))

    if dist(saidas["pan_topo_esq"], saidas["pan_baixo_dir"]) < 25:
        print(f"FALHOU: pan nos dois cantos deu a mesma cor {saidas['pan_topo_esq']} "
              f"— o crop nao acompanhou o pan")
        falhas += 1
    else:
        print(f"ok  o pan move a placa no arquivo: topo={saidas['pan_topo_esq']} "
              f"baixo={saidas['pan_baixo_dir']}")

    if dist(saidas["neutro"], saidas["zoom"]) < 25:
        print(f"FALHOU: zoom 2.0 deu a mesma cor do sem zoom {saidas['neutro']}")
        falhas += 1
    else:
        print(f"ok  o zoom amplia no arquivo: neutro={saidas['neutro']} "
              f"zoom={saidas['zoom']}")
    return falhas


if __name__ == "__main__":
    raise SystemExit(main())