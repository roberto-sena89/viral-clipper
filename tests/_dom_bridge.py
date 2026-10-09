"""Ponte entre o unittest e o DOM de verdade dos testes do painel.

Os testes de markup liam o ARQUIVO e passavam regex nele. Isso media o que esta
escrito na fonte, nao o que chega ao usuario -- e o defeito ficou visivel no dia
em que o rail e o rodape saíram do HTML para o `comum.js`: os testes continuaram
verdes medindo um HTML que nao montava mais nada.

Aqui a medicao e' feita DEPOIS do JS rodar. O `tests/_dom.js` monta a pagina
com um DOM minimo (sem browser, sem dependencia nova) e devolve o que o JS
produziu; este modulo chama esse processo e traz o resultado para o Python.

Condicao de contorno: tudo passa por `node`. Se o node nao estiver no PATH o
teste e' PULADO, e nao reprovado -- a suite tem de rodar numa maquina que so'
fez `pip install -r requirements.txt`, e um vermelho por falta de ferramenta
esconde o vermelho que importa.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_DOM_JS = Path(__file__).resolve().parent / "_dom.js"

#: Onde o node guarda o resultado. Um `print` do JS so' sai por aqui: o
#: `new Function` dos scripts de pagina nao deve sujar o stdout do teste.
_SCRIPT = """
const fs = require('fs');
const path = require('path');
const { montarPagina } = require(process.env.DOM_JS);
const cfg = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(cfg.html, 'utf8');
const r = montarPagina(html, {
  dir: cfg.dir,
  pathname: cfg.pathname,
  soComum: !!cfg.soComum,
  console: { log() {}, warn() {}, error() {}, info() {} },
});
const doc = r.documento;
const d = (el) => {
  // Todos os atributos, para o teste perguntar `aria-expanded`/`aria-controls`
  // pelo mapa em vez de por `assertIn` na string: um `assertIn` tambem passa
  // quando o atributo esta' num OUTRO elemento. `el.atributos` e' um Map
  // (nome -> valor) no DOM de teste.
  const attrs = {};
  if (el.atributos) {
    if (typeof el.atributos.forEach === 'function' && !Array.isArray(el.atributos)) {
      el.atributos.forEach((v, k) => { attrs[k] = v === null ? '' : v; });
    } else {
      for (const a of el.atributos) attrs[a.nome] = a.valor == null ? '' : a.valor;
    }
  }
  return {
    tag: el.tagName,
    classe: el.className,
    id: el.id,
    href: el.getAttribute('href'),
    hidden: el.hidden,
    texto: el.textContent,
    title: el.getAttribute('title'),
    ariaCurrent: el.getAttribute('aria-current'),
    attrs,
    innerHTML: el.innerHTML,
    // A cadeia de tags acima do elemento, da raiz para o pai. E' o que permite
    // perguntar "o selo esta DENTRO de um <a>?" medindo a arvore em vez de
    // cortando a string no primeiro `</a>`.
    ancestrais: (() => {
      const nomes = [];
      let no = el.pai;
      while (no && no.tagName) { nomes.unshift(no.tagName); no = no.pai; }
      return nomes;
    })(),
  };
};
out = {
  erros: r.erros,
  scripts: r.scripts.map((s) => s.arquivo),
  titulo: doc.title,
  // O HTML depois do JS rodar. Fica por ultimo e so' quando pedido: e' grande
  // e a maioria dos testes mede elementos, nao o documento inteiro.
  html: cfg.incluirHtml ? doc.body.innerHTML : null,
};
if (cfg.seletores && cfg.seletores.length) {
  out.achados = {};
  for (const sel of cfg.seletores) {
    out.achados[sel] = doc.querySelectorAll(sel).map(d);
  }
}
if (cfg.selectoresUm && cfg.selectoresUm.length) {
  out.achadoUm = {};
  for (const sel of cfg.selectoresUm) {
    const el = doc.querySelector(sel);
    out.achadoUm[sel] = el ? d(el) : null;
  }
}
console.log(JSON.stringify(out));
"""


def _node() -> str | None:
    return shutil.which("node")


def pagina_montada(nome: str, *,
                   pathname: str | None = None,
                   seletores: tuple[str, ...] = (),
                   seletores_um: tuple[str, ...] = (),
                   so_comum: bool = True,
                   incluir_html: bool = False,
                   timeout: int = 60) -> dict:
    """Monta `web/<nome>` com o JS rodando e devolve o DOM medido.

    `seletores` conta elementos; `seletores_um` traz o primeiro (ou None). O
    que nao for pedido nao e' serializado -- o `innerHTML` de uma pagina inteira
    estoura o buffer do pipe em alguns casos, e o teste nao precisa dele.

    `incluir_html=True` pede o `body.innerHTML` DEPOIS do JS rodar, que e' o que
    permite afirmar sobre a pagina inteira (ex.: "o texto `VC` nao reapareceu em
    lugar nenhum"). Fica desligado por padrao justamente por ser grande.
    """
    node = _node()
    if not node:
        raise unittest.SkipTest("node nao esta no PATH: medicao de DOM pulada")

    if pathname is None:
        pathname = "/" if nome == "index.html" else "/" + nome.replace(".html", "")

    entrada = {
        "html": str(WEB_DIR / nome),
        "dir": str(WEB_DIR),
        "pathname": pathname,
        "soComum": so_comum,
        "seletores": list(seletores),
        "selectoresUm": list(seletores_um),
        "incluirHtml": bool(incluir_html),
    }

    proc = subprocess.run(
        [node, "-e", _SCRIPT],
        input=json.dumps(entrada),
        capture_output=True,
        text=True,
        timeout=timeout,
        # O ambiente COMPLETO, com o DOM_JS por cima. Passar um `env` so' com
        # PATH quebra o node no Windows: ele precisa de SystemRoot e companhia
        # para inicializar o CSPRNG, e a falha nao parece nada com "faltou uma
        # variavel" -- e' um assert nativo no `node::Start`.
        env={**_ambiente(), "DOM_JS": str(_DOM_JS)},
    )
    if proc.returncode != 0:
        linhas = [l.strip() for l in proc.stderr.splitlines() if l.strip()]
        raise AssertionError(
            "o DOM de teste falhou ao montar %s:\n%s" % (nome, "\n".join(linhas[:12])))

    saida = proc.stdout.strip().splitlines()
    if not saida:
        raise AssertionError(
            "o DOM de teste nao devolveu nada para %s (stderr: %s)"
            % (nome, proc.stderr[:400]))
    return json.loads(saida[-1])


def _ambiente() -> dict:
    """O ambiente do processo, sem depender de estar no PATH do shell.

    O `DOM_JS` viaja por aqui e nao como argumento porque o `-e` do node ocupa
    a linha de comando.
    """
    import os
    return dict(os.environ)
