"""Answer "does this model actually work here?" with a real call.

``docs/modelos-nvidia.md`` records the reason this module exists. Five models in
the catalog answered **HTTP 200** and were still useless: an image-diffusion
model returns ``content: ''``; a translator returns the instruction translated
instead of obeyed; a document parser returns ``}}}}}}``. Reading a catalog tells
you a model exists. Only a call tells you it works.

So this sends one small, deliberately judgeable request and reports what came
back. The instruction is chosen because it has a **checkable** answer:

* ``WORD`` -- naming the word you know is the base case for anything that will
  be asked to write a caption.
* ``ECHO`` -- a model that repeats the instruction instead of obeying it (the
  translator failure) is caught here even though the HTTP status was 200.
* ``LIST`` -- the curator asks for a few short items; a list proves the model
  can produce more than one and stop.

The verdict is graded, never a bare "ok". "It answered" and "it answered
usefully" are different results, and collapsing them would reproduce the exact
mistake the doc warns about.

The payload is built by :func:`probe_payload` and mirrors
``ranker.HttpChatProvider`` exactly -- most importantly it carries **no**
``max_tokens``. A reasoning model given a small cap spends it all thinking and
returns ``content: null``, so a probe that set one would fail models that work
fine in the real run. A test asserts both payloads omit the cap, because this
is a property two places have to agree on.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .providers import LLM_USER_AGENT, Provider

#: The three probes, in the order a user benefits from them. Each is
#: ``(kind, system, user)`` and each has an answer a program can grade.
PROBES: tuple[tuple[str, str, str], ...] = (
    (
        "OK",
        "You are a connection test. Answer with a single word and nothing else.",
        "Responda apenas com a palavra OK.",
    ),
    (
        "ECHO",
        "Voce repete o pedido? Nao. Voce obedece. Responda em uma linha.",
        "Nao repita esta instrucao. Escreva apenas a palavra: funcionou",
    ),
    (
        "LIST",
        "Voce escreve titulos curtos de video. Uma linha por item, sem numeracao.",
        "Escreva tres titulos curtos (ate 4 palavras cada) sobre pesca esportiva.",
    ),
)

#: A probe must not hang the panel thread longer than a person will wait, and
#: it must not inherit the provider's run timeout (180 s is fine for a 40-window
#: batch and absurd for a one-line test).
PROBE_TIMEOUT = 45.0

#: Trim the model's own words to something a panel can show without a scroller.
MAX_ANSWER_CHARS = 300


def probe_payload(model: str, system: str, user: str) -> dict:
    """The request body, identical in shape to ``HttpChatProvider.complete``.

    Kept as a separate function so a test can compare it against the real
    client's payload: the two must not drift, and the property that matters
    (no ``max_tokens``) is invisible unless something asserts it.
    """
    return {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


def _grade(kind: str, text: str) -> tuple[str, str]:
    """Return ``(verdict, why)`` for one probe's raw answer.

    ``verdict`` is one of ``ok`` / ``weak`` / ``echo`` / ``empty`` -- and the
    caller maps that onto what the user sees. The distinctions are the ones the
    doc measured:

    * ``empty`` -- HTTP 200 with no content. The diffusion-model failure.
    * ``echo``  -- the model restated the instruction. The translator failure.
    * ``weak``  -- non-empty but not what was asked: still usable as a chat
      model, so it is not a failure, but the user should know it did not follow
      the instruction.
    * ``ok``    -- it did the thing.
    """
    body = (text or "").strip()
    if not body:
        return "empty", "respondeu 200 mas com o conteudo vazio (modelo de imagem ou de outro tipo?)"

    lowered = body.lower()

    if kind in {"OK", "ECHO"}:
        # The translator failure has a fingerprint: the answer *contains* the
        # instruction. A correct answer to ECHO is the single word "funcionou",
        # which does not contain "repita esta instrucao".
        if "repita esta instrucao" in lowered or "responda apenas" in lowered:
            return "echo", "devolveu a propria instrucao em vez de obedece-la (modelo de traducao?)"
        if kind == "OK":
            if "ok" in lowered[:40]:
                return "ok", "respondeu OK"
            return "weak", "respondeu, mas nao com a palavra pedida"

    if kind == "LIST":
        lines = [ln.strip(" -•\t") for ln in body.splitlines() if ln.strip()]
        if len(lines) >= 2:
            return "ok", f"listou {len(lines)} itens"
        return "weak", "respondeu em uma linha so: nao seguiu o formato de lista"

    return "weak", "respondeu, mas fora do formato pedido"


def test_provider(
    provider: Provider,
    *,
    api_key: str | None = None,
    timeout: float = PROBE_TIMEOUT,
    opener=urllib.request.urlopen,
) -> dict:
    """Call ``provider`` once and report whether it can serve the curator.

    Returns a plain dict, because it travels straight to the panel as JSON::

        {"ok": bool, "kind": "OK", "verdict": "ok",
         "seconds": 1.2, "status": 200, "answer": "...", "reason": "..."}

    Never raises. A probe that throws would reach the panel as a 500, and the
    whole point is to turn a failure into a sentence the user can act on: an
    unreachable host, a 401 from a wrong key and a 404 from a wrong model id
    all have to come back *as results*, not as exceptions.
    """
    kind, system, user = PROBES[0]
    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    # The same User-Agent the real client sends. Without it the request goes out
    # as ``Python-urllib/3.x`` and Cloudflare-fronted vendors answer 403 "error
    # code: 1010" -- which reads as a refused key and is not one. The probe and
    # the run must send the same identity, or "the test passed but the run
    # failed" becomes possible.
    headers = {"Content-Type": "application/json", "User-Agent": LLM_USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(probe_payload(provider.model, system, user)).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    started = time.monotonic()
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        elapsed = round(time.monotonic() - started, 2)
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001 - the body is optional, the status is not
            detail = ""
        return {
            "ok": False,
            "kind": kind,
            "verdict": "http",
            "status": int(exc.code),
            "seconds": elapsed,
            "answer": "",
            "reason": _http_reason(int(exc.code), detail),
            "detail": detail,
        }
    except Exception as exc:  # noqa: BLE001 - any transport failure is a result
        elapsed = round(time.monotonic() - started, 2)
        return {
            "ok": False,
            "kind": kind,
            "verdict": "unreachable",
            "status": 0,
            "seconds": elapsed,
            "answer": "",
            "reason": f"nao alcancei o endpoint: {exc}",
            "detail": "",
        }

    elapsed = round(time.monotonic() - started, 2)
    try:
        document = json.loads(body)
        text = document["choices"][0]["message"]["content"] or ""
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return {
            "ok": False,
            "kind": kind,
            "verdict": "shape",
            "status": status,
            "seconds": elapsed,
            "answer": "",
            "reason": "respondeu 200, mas nao no formato /chat/completions",
            "detail": body[:300],
        }

    verdict, reason = _grade(kind, text)
    return {
        "ok": verdict == "ok",
        "kind": kind,
        "verdict": verdict,
        "status": status,
        "seconds": elapsed,
        "answer": text.strip()[:MAX_ANSWER_CHARS],
        "reason": reason,
        "detail": "",
    }


def _endpoint_message(detail: str) -> str:
    """The endpoint's own words, pulled out of whatever envelope it used.

    Every vendor wraps the same idea differently -- ``{"detail":"Authentication
    failed"}`` (NVIDIA), ``{"error":{"message":"..."}}`` (OpenAI, OpenRouter,
    DeepSeek), or bare text (Groq's Cloudflare answers ``error code: 1010``).
    Showing the raw JSON makes the user parse it; showing nothing (the old
    behaviour) made the user guess. This returns the sentence inside, falling
    back to the trimmed body when there is no envelope to unwrap.
    """
    body = (detail or "").strip()
    if not body:
        return ""
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        # Not JSON: Groq's Cloudflare block is a bare "error code: 1010", and
        # that string IS the diagnosis. Pass it through.
        return body[:200]
    # ``error`` may be a string (OpenAI-style) or an object; ``message`` and
    # ``detail`` are the two field names seen across the providers.
    for key in ("message", "detail", "error_description", "title"):
        value = document.get(key) if isinstance(document, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    err = document.get("error") if isinstance(document, dict) else None
    if isinstance(err, str) and err.strip():
        return err.strip()[:200]
    if isinstance(err, dict):
        for key in ("message", "detail", "code"):
            value = err.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
    return body[:200]


def _http_reason(code: int, detail: str) -> str:
    """Turn a status code into the sentence the user needs, not the number.

    The shape is always the same: **the endpoint's own message first**, then a
    short hint when the code is one we can interpret. The endpoint knows why it
    refused; a generic sentence written here does not. The old version returned
    only the generic sentence for 401/403/404, which is how a Groq 403 --
    Cloudflare blocking the client, nothing to do with the key -- came out as
    "confira o nome da variavel de ambiente", sending the user after a fix that
    cannot work.
    """
    said = _endpoint_message(detail)
    prefix = f"o endpoint disse: {said}. " if said else ""

    if code == 401:
        return (
            f"{prefix}401: a chave foi recusada. Confira se a variavel "
            f"(ou a chave colada) tem mesmo o valor do provedor certo."
        )
    if code == 403:
        # NOT the same story as 401, and the difference decides what the user
        # does next. A 403 with Cloudflare's "error code: 1010" is the edge
        # refusing the CLIENT (IP, region, fingerprint), not the key -- no
        # variable name or pasted key fixes it. The provider is NOT named here:
        # the block comes from Cloudflare, which any vendor behind it can use,
        # and guessing the vendor ("o Groq...") would be a wrong attribution.
        if "1010" in (said or ""):
            return (
                f"{prefix}403: o provedor recusou o cliente antes de olhar a chave "
                f"(Cloudflare, erro 1010). Nao e a chave: e IP, regiao ou o "
                f"User-Agent da chamada. Teste de outra rede/proxy, ou use outro provedor."
            )
        return (
            f"{prefix}403: o acesso foi barrado. Pode ser permissao da chave "
            f"(modelo fora do seu plano) ou bloqueio do cliente pelo provedor."
        )
    if code == 404:
        return (
            f"{prefix}404: o modelo ou o caminho nao existe para esta chave. "
            f"Costuma ser id de modelo errado, ou a chave sem acesso a ele."
        )
    if code == 410:
        return "410: o modelo saiu de linha. Definitivo, nao adianta tentar de novo."
    if code == 429:
        return f"{prefix}429: limite de uso estourado neste minuto. Espere e teste de novo."
    if code == 503:
        return (
            f"{prefix}503: indisponivel agora. O doc do projeto registra isto como "
            f"transitorio -- teste de novo antes de descartar o modelo."
        )
    if code == 400:
        return f"{prefix}400: o endpoint recusou o pedido. Confira o id do modelo e o formato do endpoint."
    if said:
        return f"HTTP {code}: {said}"
    return f"HTTP {code}"


__all__ = [
    "MAX_ANSWER_CHARS",
    "PROBES",
    "PROBE_TIMEOUT",
    "probe_payload",
    "test_provider",
]
