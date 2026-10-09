"""Serve o estado ANTES da normalizacao tipografica nas mesmas rotas do painel.

Existe para comparar "antes x depois" no navegador. O painel de verdade
(`web/server.py`) atende `/`, `/biblioteca`, `/ajustes` e `/publicar`; um
`http.server` cru so' atende arquivo por arquivo, entao `/biblioteca` daria 404
e a captura sairia com tres rotas vazias -- comparação de nada com nada.

Uso:
    python tools/antes_substituto.py 7799 <pasta-com-os-arquivos-web>
"""
import http.server
import socketserver
import sys
from pathlib import Path

ROTAS = {
    "/": "index.html",
    "/biblioteca": "scrap.html",
    "/ajustes": "ajustes.html",
    "/publicar": "publicar.html",
}


def main() -> int:
    porta = int(sys.argv[1])
    raiz = Path(sys.argv[2]).resolve()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(raiz), **k)

        def translate_path(self, path: str) -> str:
            limpo = path.split("?")[0].split("#")[0]
            if limpo in ROTAS:
                return str(raiz / ROTAS[limpo])
            return super().translate_path(path)

        def log_message(self, *a):
            pass

    with socketserver.TCPServer(("127.0.0.1", porta), Handler) as srv:
        print("servindo %s em http://127.0.0.1:%d" % (raiz, porta), flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
