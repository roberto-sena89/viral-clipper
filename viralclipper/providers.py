"""Named LLM providers, so choosing a model is a name and not a URL.

Before this module the endpoint and the model were two free-text fields in the
web panel and two flags on the CLI. That works exactly once: the second time
you switch providers you have to remember the base URL, the exact model id and
which environment variable holds the key. Getting any of the three wrong is
invisible until the run is already downloading a video.

A provider is a name that resolves to those three things plus a measured note.
``nvidia/nemotron-3-super-120b-a12b`` is not a name a person remembers; it is a
string they copy. ``nemotron-super`` is a name they remember.

Every entry below was measured against the real endpoint, not copied from a
catalog. That distinction matters more than it sounds: ``docs/modelos-nvidia.md``
records five models that answer HTTP 200 and are still useless here (an image
diffusion model returns an empty completion; a translator translates the
instruction instead of obeying it). A catalog lists what exists; this table
lists what works.

Adding a provider is a dict entry. Nothing else in the codebase changes.

There are two tables, and the difference is who vetted the entry. The dict below
is the reviewed one: every ``note`` in it is a measurement. The panel writes a
second table to ``provedores-usuario.toml`` (see
:mod:`viralclipper.user_providers`) for endpoints the user already has and does
not want to wait on a commit for. Both are read through :func:`get_provider` and
:func:`list_providers`, so nothing downstream knows which table an entry came
from -- with one exception: a name collision resolves to the reviewed entry.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import ClipConfig
from .util import ClipperError

#: Every provider here speaks the OpenAI ``/chat/completions`` shape, which is
#: why one client covers all of them and no vendor SDK is needed.
DEFAULT_PROVIDER = "openai"

#: The User-Agent both HTTP clients send. Without it, Python's urllib identifies
#: itself as ``Python-urllib/3.x`` and Cloudflare -- which fronts a number of
#: these vendors, not just the obvious ones -- answers **403 "error code: 1010"**
#: before the request ever reaches the API. The key is not even looked at, so
#: the failure looks like a refused key and is not one. Measured 2026-10-05
#: against ``vyceai.com``: identical 403 with and without the key, HTTP 200 with
#: any normal User-Agent. This is why the constant lives next to the table and
#: both ``ranker.HttpChatProvider`` and ``provider_probe`` read it: the run and
#: the test must agree, or "the test passes but the run 403s" becomes possible.
LLM_USER_AGENT = "viral-clipper/1.0 (+https://github.com/)"


@dataclass(frozen=True)
class Provider:
    """One named, ready-to-use LLM endpoint."""

    name: str
    label: str
    base_url: str
    model: str
    #: The **name** of the environment variable that holds the key. This is what
    #: the CLI reads, and what keeps a key out of the versioned table.
    api_key_env: str
    #: A key pasted straight into the panel, kept in the user's own
    #: (gitignored) ``provedores-usuario.toml``. The built-in table never sets
    #: it -- a literal secret in tracked source is exactly the thing
    #: ``api_key_env`` exists to avoid. It lives here, not in an env var, for
    #: the case ``api_key_env`` cannot serve: a machine where the user cannot or
    #: will not export a variable before opening the panel. ``api_key_env``
    #: remains authoritative when both are set, so exporting a variable still
    #: overrides a key saved from a browser.
    api_key: str = ""
    #: Local endpoints (Ollama, LM Studio) usually accept any key, so a missing
    #: one must not abort the run.
    requires_key: bool = True
    #: Reasoning models routinely pass 60 s. The default is raised here because
    #: a timeout is the one failure the user cannot diagnose from the message.
    timeout: float = 180.0
    #: Measured latency and the reason to pick or avoid this one. Shown in the
    #: panel, because a model that takes 73 s per window is a real decision.
    note: str = ""


PROVIDERS: dict[str, Provider] = {
    "openai": Provider(
        name="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        note="Padrao historico do projeto. Rapido e barato.",
    ),
    "nemotron-super": Provider(
        name="nemotron-super",
        label="NVIDIA NIM - Nemotron 3 Super 120B",
        base_url="https://integrate.api.nvidia.com/v1",
        model="nvidia/nemotron-3-super-120b-a12b",
        api_key_env="OPENAI_API_KEY",
        note="O melhor equilibrio medido: 4.9s na legenda, 7.4s nas frases.",
    ),
    "gemma-4-31b": Provider(
        name="gemma-4-31b",
        label="NVIDIA NIM - Gemma 4 31B",
        base_url="https://integrate.api.nvidia.com/v1",
        model="google/gemma-4-31b-it",
        api_key_env="OPENAI_API_KEY",
        note="4.5s, e o gancho mais curto dos aprovados: cabe na tela sem cortar.",
    ),
    "kimi-k3": Provider(
        name="kimi-k3",
        label="NVIDIA NIM - Kimi K3",
        base_url="https://integrate.api.nvidia.com/v1",
        model="moonshotai/kimi-k3",
        api_key_env="OPENAI_API_KEY",
        note="22.9s. Segue instrucao pelo system, nao pelo user: funciona aqui.",
    ),
    "deepseek-flash": Provider(
        name="deepseek-flash",
        label="NVIDIA NIM - DeepSeek V4.1 Flash",
        base_url="https://integrate.api.nvidia.com/v1",
        model="deepseek-ai/deepseek-v4.1-flash",
        api_key_env="OPENAI_API_KEY",
        note="48.8s. Nunca envie max_tokens: o modelo gasta tudo pensando e devolve null.",
    ),
    "local": Provider(
        name="local",
        label="Local (Ollama / LM Studio)",
        base_url="http://127.0.0.1:11434/v1",
        model="llama3.1",
        api_key_env="",
        requires_key=False,
        note="Sem custo por chamada e a transcricao nao sai da maquina. Exige o servidor ligado.",
    ),
    # ------------------------------------------------------------------
    # Free tiers measured against the vendor docs in 2026-10-02.
    #
    # These exist for the reason the module docstring gives: a free tier is
    # the difference between "the curator is an option" and "the curator is
    # a bill". Each ``note`` carries the number that decides, because a
    # rate limit the user cannot see is a run that dies mid-pipeline.
    # ------------------------------------------------------------------
    "groq": Provider(
        name="groq",
        label="Groq (free tier)",
        base_url="https://api.groq.com/openai/v1",
        model="openai/gpt-oss-120b",
        api_key_env="GROQ_API_KEY",
        # Free tier, per the vendor's rate-limit page: 30 RPM / 1K RPD /
        # 8K TPM for this model. The RPD is the binding limit -- a 40-window
        # video is 40 calls, so roughly 25 videos a day.
        note="Free: 1K req/dia e 8K tokens/min. 30 req/min. O limite diario e o que manda.",
    ),
    "gemini": Provider(
        name="gemini",
        label="Google Gemini (free tier)",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-3.8-flash",
        api_key_env="GEMINI_API_KEY",
        # Google stopped publishing the exact RPM/RPD on the rate-limits
        # page -- it now points at the AI Studio dashboard per project, and
        # the pricing page only marks the model "free of charge". The note
        # says so rather than inventing a number.
        note="Free tier do Google (modelo marcado como gratuito). Sem RPM/RPD publicado: confira no AI Studio.",
    ),
    "openrouter": Provider(
        name="openrouter",
        label="OpenRouter (modelos :free)",
        base_url="https://openrouter.ai/api/v1",
        model="openrouter/free",
        api_key_env="OPENROUTER_API_KEY",
        # The :free catalog variant and the openrouter/free router both cost
        # nothing; 50 requests/day without credits, 1000 after buying 10.
        note="50 req/dia sem creditos (1K apos comprar 10). O roteador /free escolhe um modelo gratuito.",
    ),
    "deepseek": Provider(
        name="deepseek",
        label="DeepSeek (API propria, paga)",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api_key_env="DEEPSEEK_API_KEY",
        # Not a free tier -- listed because it is the same model the NIM
        # entry serves, at the vendor's own price and without the NIM
        # detour. Note the model id differs between the two: NIM wants
        # ``deepseek-ai/deepseek-v4.1-flash``, the direct API wants
        # ``deepseek-flash``. Copying one onto the other is a 404.
        note="Sem tier gratuito. Mesmo modelo do NIM, direto do fornecedor. Id do modelo e diferente do NIM.",
    ),
}


def _table() -> dict[str, Provider]:
    """The built-in entries plus the ones the panel saved, built-ins winning.

    Imported here rather than at module level: ``user_providers`` imports
    :class:`Provider` from this module, so a top-level import would be circular.
    The same lazy-import shape ``config`` and ``ranker`` already use.

    A name collision resolves to the built-in on purpose. The user's file is
    theirs to edit, but a hand-written entry called ``openai`` that silently
    redirected every run to another endpoint is the kind of surprise that costs
    a day -- and the panel refuses to save over a built-in name for the same
    reason (see ``web/server.py``).
    """
    from . import user_providers

    table = dict(PROVIDERS)
    for provider in user_providers.load():
        table.setdefault(provider.name, provider)
    return table


def get_provider(name: str) -> Provider:
    """Resolve a provider name, or raise with the list of the known ones."""
    key = (name or "").strip()
    table = _table()
    if key in table:
        return table[key]
    known = ", ".join(sorted(table))
    raise ClipperError(f"Unknown provider '{name}'. Known providers: {known}.")


def list_providers() -> list[Provider]:
    """Every provider, ordered by name for a stable UI listing."""
    table = _table()
    return [table[key] for key in sorted(table)]


def apply_to_config(config: ClipConfig, name: str) -> ClipConfig:
    """Return a copy of ``config`` with the provider's three knobs filled in.

    The caller keeps whatever it already set for anything the provider does not
    define. The timeout is only raised, never lowered: a caller that already
    asked for a longer budget knows something about its own network that the
    table does not.
    """
    provider = get_provider(name)
    return replace(
        config,
        ranker_provider=provider.name,
        ranker_base_url=provider.base_url,
        ranker_model=provider.model,
        ranker_api_key_env=provider.api_key_env or config.ranker_api_key_env,
        ranker_requires_key=provider.requires_key,
        ranker_timeout=max(config.ranker_timeout, provider.timeout),
    )


def provider_for_config(config: ClipConfig) -> Provider | None:
    """The provider a config points at, or ``None`` when it points at none.

    A config built by hand (the CLI, a TOML file) may set ``base_url`` and
    ``model`` directly without naming a provider. That stays valid: this
    function only answers when a name is present, and its absence is not an
    error.
    """
    name = getattr(config, "ranker_provider", "") or ""
    if not name:
        return None
    return _table().get(name)


__all__ = [
    "DEFAULT_PROVIDER",
    "LLM_USER_AGENT",
    "PROVIDERS",
    "Provider",
    "apply_to_config",
    "get_provider",
    "list_providers",
    "provider_for_config",
]
