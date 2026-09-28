/* ---------- ESCAPEMENTO DE HTML ----------
   Uma unica Implementacao de `esc`, usada pelas duas paginas.

   Havia duas, e elas nao eram iguais:

   - o `index.js` usava um `<div>` descartavel, escrevia o valor em
     `textContent` e lia de volta o `innerHTML`. Isso escapa `&`, `<` e `>`,
     e NAO escapa aspas: o `innerHTML` de um nodo de texto nao precisa disso,
     porque nao ha aspa para fechar.
   - o `scrap.js` fazia cinco `replace` e escapava tambem `"` e `'`.

   A do scrap e a que serve. O motivo esta no uso: o scrap passa `esc(href)`
   DENTRO de um atributo (`href="..."`, `title="..."`), e ai uma aspa sem
   escapar fecha o atributo e o resto da string vira markup — o
   `server.py` escapa com `quote=True` pelo mesmo motivo, e o comentario de
   la explica o ataque. O index so usava `esc` em conteudo de texto, entao a
   versao fraca nunca chegou a falhar ali; mas ela seria um bug no dia em que
   alguem precisasse de um atributo, e a falha seria silenciosa.

   Unificar na versao forte e o que mantem o index seguro pelo mesmo motivo que
   mantem o scrap. Nao e a versao mais simples: e a versao correta.

   A ordem dos `replace` importa: `&` primeiro. Escapar `&` depois de
   `<` e `>` transformaria o `&` de `&lt;` em `&amp;lt;`, e o browser leria
   `&lt;` como texto em vez de `<`. */
(function (global) {
  'use strict';

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Os dois scripts de pagina sao IIFE com `'use strict'`, entao nao veem
  // nada do escopo de outro arquivo. O global e o que os dois alcancam, e o
  // por que o `esc` fica em `window` e nao num modulo.
  global.esc = esc;
})(window);
