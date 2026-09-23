"""Optional run configuration files: YAML or TOML.

The file is read before the CLI parser is built, and every key it contains
becomes a default for the matching argument. Anything passed on the command
line still wins, so a config file is a convenience for repeated runs, not a
replacement for explicit flags.

Supported formats are detected from the file extension:

    .yaml / .yml   -> PyYAML (``pip install pyyaml``)
    .toml          -> stdlib ``tomllib`` (Python 3.11+)

Keys that do not match a known argument are ignored with a warning, so a stale
config file can never break a newer version of the tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import Logger


def load_config_file(path: Path, logger: Logger) -> dict[str, Any]:
    """Read ``path`` and return a flat ``{arg_name: value}`` dict.

    Raises :class:`ClipperError` if the file does not exist, is not a recognised
    format, or cannot be parsed.
    """
    from .util import ClipperError

    if not path.exists():
        raise ClipperError(f"Config file not found: {path}")
    if not path.is_file():
        raise ClipperError(f"Config path is not a file: {path}")

    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return _load_yaml(path, logger)
    if suffix == ".toml":
        return _load_toml(path, logger)
    raise ClipperError(
        f"Unrecognised config format: {path}. Use .yaml/.yml or .toml."
    )


def _load_yaml(path: Path, logger: Logger) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised via CLI
        from .util import ClipperError
        raise ClipperError(
            "YAML config requires PyYAML. Install it with `pip install pyyaml`."
        ) from exc

    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        from .util import ClipperError
        raise ClipperError(f"Invalid YAML in {path}: {exc}") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        from .util import ClipperError
        raise ClipperError(
            f"Config file {path} must be a mapping at the top level, got {type(raw).__name__}."
        )
    return _flatten(raw, path, logger)


def _load_toml(path: Path, logger: Logger) -> dict[str, Any]:
    import tomllib

    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        from .util import ClipperError
        raise ClipperError(f"Invalid TOML in {path}: {exc}") from exc

    return _flatten(raw, path, logger)


def _flatten(
    data: dict[str, Any],
    path: Path,
    logger: Logger,
    prefix: str = "",
) -> dict[str, Any]:
    """Walk the parsed mapping and produce ``{dotted_key: value}`` pairs.

    Nested tables are joined with a dot, matching the CLI ``--a-b`` style: a
    YAML block like ``whisper: {model: small}`` becomes ``whisper_model``.
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            from .util import ClipperError
            raise ClipperError(
                f"Config key in {path} must be a string, got {type(key).__name__}."
            )
        full = f"{prefix}{key}" if not prefix else f"{prefix}_{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, path, logger, prefix=full))
        else:
            out[full] = value
    return out


def load_template_file(path: Path) -> dict[str, Any]:
    """Read a template file and return its *nested* mapping.

    Unlike :func:`load_config_file` this deliberately does **not** flatten:
    a template's zones are a list of tables, and flattening would destroy the
    structure the template loader needs. Reusing the format detection keeps
    both file kinds consistent for the user.
    """
    from .util import ClipperError

    if not path.exists():
        raise ClipperError(f"Template nao encontrado: {path}")
    if not path.is_file():
        raise ClipperError(f"Caminho de template nao e um arquivo: {path}")

    suffix = path.suffix.lower()
    if suffix == ".toml":
        import tomllib

        try:
            with path.open("rb") as handle:
                return tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ClipperError(f"TOML invalido em {path}: {exc}") from exc
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - exercised via CLI
            raise ClipperError(
                "Template YAML exige PyYAML. Instale com `pip install pyyaml`."
            ) from exc
        try:
            with path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise ClipperError(f"YAML invalido em {path}: {exc}") from exc
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ClipperError(
                f"Template {path} precisa ser um mapa no nivel de cima."
            )
        return raw
    raise ClipperError(
        f"Formato de template nao reconhecido: {path}. Use .toml, .yaml ou .yml."
    )


def apply_defaults(
    parser,
    defaults: dict[str, Any],
    logger: Logger,
) -> None:
    """Inject ``defaults`` into ``parser`` as argparse defaults.

    Only keys that map to a real argument are forwarded; unknown keys are
    logged and dropped so a config file for a future version does not crash
    the current one.

    Matching is tolerant: a config key like ``whisper_language`` (produced by
    a nested YAML/TOML table) is accepted when the CLI dest is ``language``,
    because the ``whisper`` prefix is just the group name, not part of the
    argument.
    """
    # Build the set of valid dest names from every action in the parser.
    known: set[str] = set()
    for action in parser._actions:
        if action.dest and action.dest != "help":
            known.add(action.dest)

    filtered: dict[str, Any] = {}
    for key, value in defaults.items():
        normalized = key.replace("-", "_")
        if normalized in known:
            filtered[normalized] = value
            continue
        # Try progressively stripping leading group prefixes: a YAML block
        # ``whisper: {language: pt}`` flattens to ``whisper_language`` while
        # the CLI dest is ``language``.
        matched = False
        parts = normalized.split("_")
        for cut in range(1, len(parts)):
            stripped = "_".join(parts[cut:])
            if stripped in known:
                filtered[stripped] = value
                matched = True
                break
        if matched:
            continue
        logger.warn(f"Config key '{key}' in file is not a known argument; ignored.")
    if filtered:
        parser.set_defaults(**filtered)