from __future__ import annotations

from app.core.config import Settings
from app.integrations.oauth.base import OAuthProvider
from app.integrations.oauth.google import GoogleOAuthProvider


class OAuthRegistry:
    def __init__(self, providers: list[OAuthProvider]) -> None:
        self._providers = {provider.id: provider for provider in providers}

    def get(self, provider_id: str) -> OAuthProvider | None:
        return self._providers.get(provider_id)

    def all(self) -> list[OAuthProvider]:
        return list(self._providers.values())

    def register(self, provider: OAuthProvider) -> None:
        self._providers[provider.id] = provider


def build_oauth_registry(settings: Settings) -> OAuthRegistry:
    return OAuthRegistry([GoogleOAuthProvider(settings)])
