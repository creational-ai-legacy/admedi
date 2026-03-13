"""YAML template loader for portfolio tier configs.

Reads a YAML tier template from disk, parses it via ``ruamel.yaml``
(safe mode), and validates the result into a ``PortfolioConfig`` model.

The loader is synchronous (no async I/O). If the calling orchestrator
needs non-blocking behavior, it can wrap ``load_template()`` in
``asyncio.to_thread()``.

Error handling strategy:
- ``FileNotFoundError`` propagates directly (file does not exist).
- ``ruamel.yaml.YAMLError`` is caught and re-raised as
  ``ConfigValidationError`` with a human-readable message.
- Pydantic ``ValidationError`` is caught and re-raised as
  ``ConfigValidationError`` with a human-readable message.

Examples:
    >>> from admedi.engine.loader import load_template
    >>> config = load_template("examples/shelf-sort-tiers.yaml")
    >>> config.mediator
    <Mediator.LEVELPLAY: 'levelplay'>
    >>> len(config.tiers)
    4
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML, YAMLError

from admedi.exceptions import ConfigValidationError
from admedi.models.portfolio import PortfolioConfig


def load_template(path: str | Path) -> PortfolioConfig:
    """Load and validate a YAML tier template into a PortfolioConfig.

    Reads the YAML file at ``path``, parses it with safe YAML loading
    (no arbitrary code execution), and validates the parsed data into
    a ``PortfolioConfig`` pydantic model.

    Args:
        path: File system path to the YAML tier template. Accepts
            both string paths and ``pathlib.Path`` objects.

    Returns:
        A validated ``PortfolioConfig`` instance.

    Raises:
        FileNotFoundError: If the template file does not exist.
        ConfigValidationError: If the YAML is malformed or the
            parsed data fails ``PortfolioConfig`` validation.

    Examples:
        >>> config = load_template("examples/shelf-sort-tiers.yaml")
        >>> config.schema_version
        1
    """
    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"Template file not found: {file_path}"
        )

    yaml = YAML(typ="safe")

    try:
        raw_data = yaml.load(file_path)
    except YAMLError as exc:
        raise ConfigValidationError(
            f"Malformed YAML in '{file_path.name}': {exc}",
            detail=str(exc),
        ) from exc

    if raw_data is None:
        raise ConfigValidationError(
            f"Template file '{file_path.name}' is empty or contains no YAML data"
        )

    if not isinstance(raw_data, dict):
        raise ConfigValidationError(
            f"Template file '{file_path.name}' must contain a YAML mapping "
            f"(got {type(raw_data).__name__})"
        )

    try:
        return PortfolioConfig.model_validate(raw_data)
    except ValidationError as exc:
        # Extract the first human-readable error message from pydantic
        errors = exc.errors()
        if errors:
            first_error = errors[0]
            location = " -> ".join(str(loc) for loc in first_error.get("loc", []))
            message = first_error.get("msg", str(exc))
            if location:
                friendly = f"Validation error in '{file_path.name}' at {location}: {message}"
            else:
                friendly = f"Validation error in '{file_path.name}': {message}"
        else:
            friendly = f"Validation error in '{file_path.name}': {exc}"

        raise ConfigValidationError(
            friendly,
            detail=str(exc),
        ) from exc
