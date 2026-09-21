"""Provider factory; every adapter exposes the same Choice contract."""

from __future__ import annotations

from typing import Protocol

from jev_decisions.config import JevConfig
from jev_decisions.provider.openrouter import OpenRouterAdapter
from jev_decisions.provider.typesafe import TypeSafeAdapter
from jev_decisions.schemas import ChoiceRequest, ProviderChoiceResponse


class ChoiceAdapter(Protocol):
    def __enter__(self) -> ChoiceAdapter: ...

    def __exit__(self, *exc_info: object) -> None: ...

    def decide(self, request: ChoiceRequest) -> ProviderChoiceResponse: ...


def create_adapter(
    config: JevConfig,
    *,
    openrouter_cls: type[OpenRouterAdapter] = OpenRouterAdapter,
) -> ChoiceAdapter:
    """Resolve only a configured provider; never infer it from request text."""
    if config.provider.name == "typesafe":
        return TypeSafeAdapter(config)
    return openrouter_cls(config)
