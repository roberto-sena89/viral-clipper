"""Integration tests for the panel's HTTP surface.

Every other test of `web/server.py` drives a handler method directly, with a
hand-built fake socket. That proves the logic but never the two things that
actually break for the user:

  * that a route is reachable at the address the browser types -- a route that
    exists in the source but is shadowed by an earlier `if` still answers 404;
  * that the origin guard runs on the REAL socket, before the route does.

Here the server is started for real on ``127.0.0.1:0`` and the requests travel
over TCP, so a change in the dispatch order, in the guard or in the response
headers shows up as a failing status code instead of a passing unit test.

Port 0 is deliberate: the OS hands out a free port, so the test can never
collide with a panel the user already has open on 7755 -- and it still proves
the guard's port check, which compares against the port the socket is bound to
rather than the constant.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from viralclipper import __version__ as VIRALCLIPPER_VERSION
from web import server


class PanelIntegrationTests(unittest.TestCase):
    """One real server for the whole class; each request is its own fixture."""

    @classmethod
    def setUpClass(cls) -> None:
        # The handler logs every request to stderr. That noise would drown the
        # test output, so it is silenced for the duration of the class.
        cls._real_log_message = server.Handler.log_message
        server.Handler.log_message = lambda *args, **kwargs: None
        cls.httpd = server.UiServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=10)
        server.Handler.log_message = cls._real_log_message

    # -- helpers ---------------------------------------------------------

    def _request(self, method, path, body=None, host=None, extra=None):
        """One request on its own connection, closed at the end.

        ``host`` is settable on purpose: `http.client` only fills the Host
        header in when it is absent, so passing one is how the guard gets
        tested from the outside instead of by calling it directly.
        """
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            headers = {}
            if host is not None:
                headers["Host"] = host
            if extra:
                headers.update(extra)
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            # Lower-cased: `send_header` preserves the case it was given, and
            # the test should not fail because a header was spelled differently.
            sent = {name.lower(): value for name, value in response.getheaders()}
            return response.status, sent, raw
        finally:
            conn.close()

    def _json(self, method, path, body=None, host=None, extra=None):
        status, headers, raw = self._request(method, path, body=body, host=host, extra=extra)
        return status, headers, json.loads(raw.decode("utf-8"))

    def _origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- the contract ----------------------------------------------------

    def test_status_answers_the_jobs_and_clips_contract(self):
        """`/api/status` e' o batimento do painel: `{jobs, clips}` e nada mais.

        O download, o arquivo e o run tem endpoints proprios; se um deles
        voltasse para dentro do /status, a pagina passaria a depender de um
        payload que cresce sem contrato.
        """
        status, _headers, payload = self._json("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertIsInstance(payload, dict)
        for chave in ("jobs", "clips"):
            with self.subTest(chave=chave):
                self.assertIn(chave, payload)
                self.assertIsInstance(payload[chave], list)

    def test_run_without_a_url_is_refused_with_400(self):
        """400 e nao 404: a rota existe e REJEITA a entrada.

        E' o que distingue "o endpoint sumiu" de "o endpoint recusou" -- um 404
        aqui significaria que `/api/run` deixou de ser alcancavel.
        """
        status, _headers, payload = self._json("POST", "/api/run", body={"options": {}})
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("error"), "url is required")

    def test_a_blank_url_is_refused_too(self):
        """Espaco em branco nao e' url: o `.strip()` vale para o servidor tambem."""
        status, _headers, payload = self._json(
            "POST", "/api/run", body={"options": {"url": "   "}}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("error"), "url is required")

    def test_an_unknown_route_is_404(self):
        status, _headers, payload = self._json("GET", "/api/nao-existe")
        self.assertEqual(status, 404)
        self.assertEqual(payload.get("error"), "not found")

    # -- a capa do arquivamento (rota GET, sem _handle_*) ------------------
    #
    # Esta rota nao tem um `_handle_*`: ela e' um `if` dentro do `do_GET`, entao
    # nenhum teste de handler consegue alcanca-la. So' o socket prova que ela
    # esta no despacho -- e que nao foi sombreada por um `if` anterior.

    def _thumb_cache(self) -> Path:
        """A temp cache dir, so the test never writes to the user's real one."""
        cache = Path(tempfile.mkdtemp(prefix="thumb-cache-"))
        self.addCleanup(shutil.rmtree, cache, ignore_errors=True)
        return cache

    def _archive_state(self, items):
        """Pin the archive state for one request.

        `_ARCHIVE_SLOT` is pinned to ``None`` on purpose: it is what makes the
        generated file name deterministic (``arch-perfil-<code>.jpg``), instead
        of depending on whichever username a previous test left behind.
        """
        return mock.patch.dict(
            server._state,
            {server._ARCHIVE_SLOT: None, "archive_items_full": items},
            clear=False,
        )

    def test_the_archive_thumbnail_refuses_a_bad_index(self):
        """400, nao 500: o indice vem da query, que o navegador monta.

        `?i=` vazio e `?i=abc` sao o mesmo caso para o servidor -- e o ausente
        tambem. Um 500 aqui apareceria como "erro inesperado" no card.
        """
        for query in ("?i=abc", "", "?i=", "?i=1.5"):
            with self.subTest(query=query):
                status, _headers, payload = self._json(
                    "GET", f"/api/scrap/archive/thumb{query}"
                )
                self.assertEqual(status, 400)
                self.assertEqual(payload.get("error"), "bad index")

    def test_the_archive_thumbnail_without_an_item_is_404(self):
        """Sem arquivamento (ou posicao fora da lista): 404 JSON.

        A capa e' decoracao -- o card fica sem imagem, nunca derruba a lista de
        progresso com um 500.
        """
        with self._archive_state([]):
            status, _headers, payload = self._json("GET", "/api/scrap/archive/thumb?i=0")
        self.assertEqual(status, 404)
        self.assertEqual(payload.get("error"), "no thumbnail")

    def test_the_archive_thumbnail_404s_when_the_item_has_no_remote_thumbnail(self):
        """Sem URL `http...` nao existe cache possivel: 404, nao um caminho inventado."""
        with self._archive_state([{"thumbnail": "", "code": "CODE"}]):
            status, _headers, payload = self._json("GET", "/api/scrap/archive/thumb?i=0")
        self.assertEqual(status, 404)
        self.assertEqual(payload.get("error"), "no thumbnail")

    def test_the_archive_thumbnail_is_served_when_the_file_exists(self):
        """O caminho de sucesso: os bytes da capa, com content-type de imagem.

        O nome e' derivado do usuario + codigo do item, nao do caminho pedido --
        e' o que impede a rota de virar um leitor de arquivo qualquer.
        """
        cache = self._thumb_cache()
        (cache / "arch-perfil-CODE.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        with self._archive_state(
            [{"thumbnail": "https://cdn.example/x.jpg", "code": "CODE"}]
        ), mock.patch.object(server, "_thumb_dir", return_value=cache):
            status, headers, raw = self._request("GET", "/api/scrap/archive/thumb?i=0")

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "image/jpeg")
        self.assertEqual(raw, b"\xff\xd8\xff\xe0fake-jpeg")

    # -- the guard, on the real socket -----------------------------------

    def test_a_foreign_host_is_refused_on_get(self):
        status, _headers, payload = self._json("GET", "/api/status", host="evil.com")
        self.assertEqual(status, 403)
        self.assertEqual(payload.get("error"), "host not allowed")

    def test_a_foreign_host_is_refused_before_the_route_runs(self):
        """403 e nao 400 -- e o codigo e' o que prova a ORDEM.

        O POST abaixo tem entrada invalida de proposito. Se a guarda ficasse
        depois do despacho, o handler receberia o corpo e responderia 400 com
        "url is required". Responder 403 e' a prova de que a requisicao de uma
        pagina estranha morre antes de chegar na rota.
        """
        status, _headers, payload = self._json(
            "POST", "/api/run", body={"options": {}}, host="evil.com"
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload.get("error"), "host not allowed")

    def test_a_foreign_host_with_our_port_is_still_refused(self):
        """Acertar a porta nao basta: o nome tambem tem de ser este."""
        status, _headers, payload = self._json(
            "GET", "/api/status", host=f"evil.com:{self.port}"
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload.get("error"), "host not allowed")

    def test_a_foreign_origin_is_refused(self):
        """O `Origin` e' conferido quando vem -- e' o que pega o CSRF.

        Uma pagina de fora consegue acertar o `Host` (ela digita o endereco
        certo), mas nao consegue mentir o `Origin`: o navegador poe o dela.
        """
        status, _headers, payload = self._json(
            "GET", "/api/status", extra={"Origin": "http://evil.com"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload.get("error"), "origin not allowed")

    def test_our_own_origin_is_accepted(self):
        """O contra-espelho: a guarda nao pode recusar o proprio painel."""
        status, _headers, _payload = self._json(
            "GET", "/api/status", extra={"Origin": self._origin()}
        )
        self.assertEqual(status, 200)

    # -- headers on every response ---------------------------------------

    def test_every_response_carries_the_csp(self):
        """A CSP vai em TODA resposta, JSON incluido.

        Uma politica aplicada so' nos caminhos HTML e' uma que a proxima rota
        JSON escapa em silencio -- e o `/api/...` e' justamente o que o JS
        chama.
        """
        casos = (
            ("GET", "/api/status", None),
            ("POST", "/api/run", {"options": {}}),
            ("GET", "/api/nao-existe", None),
            ("GET", "/", None),
        )
        for method, path, body in casos:
            with self.subTest(method=method, path=path):
                status, headers, _raw = self._request(method, path, body=body)
                self.assertIn("content-security-policy", headers)
                self.assertIn("default-src 'self'", headers["content-security-policy"])
                self.assertEqual(headers.get("x-content-type-options"), "nosniff")
                self.assertNotIn(status, (500,), "resposta 500 num caminho simples")

    def test_the_panel_page_is_served_as_html(self):
        """`GET /` entrega a pagina, e nao o JSON de erro.

        E' a unica prova de que o painel sobe de verdade: o arquivo existe, o
        `WEB_DIR` esta certo e a rota esta na frente do 404.
        """
        status, headers, raw = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type", ""))
        self.assertIn(b"<html", raw.lower())

    # -- saude do processo -------------------------------------------------

    def test_health_reports_the_process_and_the_port_it_is_really_on(self):
        """`port` tem de ser a porta do SOCKET, nao a constante `PORT`.

        O painel aceita `--port N`. Um probe que reporta 7755 enquanto escuta
        em outro lugar e' pior que nenhum probe: ele mente com confianca.
        Aqui o servidor esta em `:0`, entao a unica resposta certa e' a porta
        que o sistema operacional deu.
        """
        status, _headers, payload = self._json("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("version"), VIRALCLIPPER_VERSION)
        self.assertEqual(payload.get("pid"), os.getpid())
        self.assertEqual(payload.get("port"), self.port)
        self.assertEqual(payload.get("host"), server.HOST)
        self.assertIn("free_mb", payload)

    def test_health_flags_a_disk_that_is_too_full_to_render(self):
        with mock.patch.object(server, "_disk_free_bytes", return_value=0):
            status, _headers, payload = self._json("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("free_mb"), 0.0)
        self.assertFalse(payload.get("room_for_a_render"))
        self.assertEqual(payload.get("min_free_mb"), server.MIN_FREE_MB)

    def test_health_admits_when_it_could_not_measure_the_disk(self):
        """Sem medida, `free_mb` e' `None` -- e isso NAO vira alarme falso.

        Uma medicao que falhou nao pode bloquear um render: bloquear por falta
        de informacao e' um defeito maior que o que este portao evita.
        """
        with mock.patch.object(server, "_disk_free_bytes", return_value=None):
            status, _headers, payload = self._json("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertIsNone(payload.get("free_mb"))
        self.assertTrue(payload.get("room_for_a_render"))

    def test_health_answers_even_with_nothing_running(self):
        """`/api/health` nao depende do estado dos jobs -- sao perguntas diferentes.

        `/api/status` fala do trabalho e pode estar vazio; a saude do processo
        nao muda por isso. Se as duas fossem a mesma rota, um probe so' ficaria
        verde enquanto houvesse trabalho.
        """
        with mock.patch.dict(server._state, {"jobs": [], "clips": []}, clear=False):
            health, _h, _p = self._json("GET", "/api/health")
            status_code, _h2, job_state = self._json("GET", "/api/status")
        self.assertEqual(health, 200)
        self.assertEqual(status_code, 200)
        self.assertEqual(job_state.get("jobs"), [])
        self.assertNotIn("jobs", self._json("GET", "/api/health")[2])

    # -- o portao de disco do /api/run -------------------------------------

    def test_run_is_refused_before_it_starts_when_the_disk_is_full(self):
        """507, nao um job que morre no meio do encode.

        `_disk_free_bytes` e' desviado para um valor minusculo -- o teste nao
        pode encher o disco de verdade. E `_run_job` fica patcheado para provar
        que o pipeline NAO chegou a comecar.
        """
        with mock.patch.object(server, "_disk_free_bytes",
                               return_value=10 * 1024 * 1024), \
                mock.patch.object(server, "_run_job") as ran:
            status, _headers, payload = self._json(
                "POST", "/api/run", body={"options": {"url": "https://youtu.be/x"}}
            )
        self.assertEqual(status, 507)
        self.assertIn("espaco em disco insuficiente", payload.get("error", ""))
        self.assertEqual(payload.get("min_free_mb"), server.MIN_FREE_MB)
        self.assertLess(payload.get("free_mb"), server.MIN_FREE_MB)
        ran.assert_not_called()

    def test_a_request_without_a_url_is_refused_before_the_disk_is_even_read(self):
        """A ordem: a url e' validada primeiro, o disco depois.

        Se o disco viesse primeiro, um pedido sem url receberia 507 num servidor
        com disco cheio -- um erro que aponta para o lugar errado. O mock existe
        so' para provar que ele NAO foi consultado.
        """
        with mock.patch.object(server, "_disk_free_bytes", return_value=0) as measured:
            status, _headers, payload = self._json("POST", "/api/run", body={"options": {}})
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("error"), "url is required")
        measured.assert_not_called()

    def test_a_render_still_starts_when_there_is_room(self):
        """O contra-espelho: com espaco, o pedido chega ao `_run_job`.

        Sem este, o portao poderia estar recusando TUDO e a suite ficaria verde.
        """
        with mock.patch.object(server, "_disk_free_bytes",
                               return_value=10 * 1024 * 1024 * 1024), \
                mock.patch.object(server, "_run_job",
                                  return_value={"clips": [], "log_lines": []}) as ran:
            status, _headers, payload = self._json(
                "POST", "/api/run", body={"options": {"url": "https://youtu.be/x"}}
            )
        ran.assert_called_once()
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("clips"), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
