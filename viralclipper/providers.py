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
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import ClipConfig
from .util import ClipperError

#: Every provider here speaks the OpenAI ``/chat/completions`` shape, which is
#: why one client covers all of them and no vendor SDK is needed.
DEFAULT_PROVIDER = "openai"


@dataclass(frozen=True)
class Provider:
    """One named, ready-to-use LLM endpoint."""

    name: str
    label: str
    base_url: str
    model: str
    api_key_env: str
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
}


def get_provider(name: str) -> Provider:
    """Resolve a provider name, or raise with the list of the known ones."""
    key = (name or "").strip()
    if key in PROVIDERS:
        return PROVIDERS[key]
    known = ", ".join(sorted(PROVIDERS))
    raise ClipperError(f"Unknown provider '{name}'. Known providers: {known}.")


def list_providers() -> list[Provider]:
    """Every provider, ordered by name for a stable UI listing."""
    return [PROVIDERS[key] for key in sorted(PROVIDERS)]


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
    return PROVIDERS.get(name)


__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDERS",
    "Provider",
    "apply_to_config",
    "get_provider",
    "list_providers",
    "provider_for_config",
]
