from __future__ import annotations

from cryptography.fernet import Fernet
from loguru import logger


class CredentialCrypto:
    def __init__(self, key: str | None = None) -> None:
        if key is None:
            from goofish_agent.config.settings import get_settings
            key = get_settings().encryption_key
        if key is None:
            key = Fernet.generate_key().decode()
            logger.warning("No encryption key configured – generated ephemeral key. Set GOOFISH_ENCRYPTION_KEY for persistence.")
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, data: str) -> str:
        return self._fernet.encrypt(data.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
