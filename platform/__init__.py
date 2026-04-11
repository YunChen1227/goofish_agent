from __future__ import annotations

from goofish_agent.models.enums import PlatformType
from goofish_agent.platform.base import (
    PLATFORM_CONFIGS,
    PlatformClient,
    PlatformConfig,
)


def create_platform_client(
    platform: PlatformType | str,
    config_override: PlatformConfig | None = None,
) -> PlatformClient:
    """Factory that returns the right PlatformClient for the given platform type."""
    if isinstance(platform, str):
        platform = PlatformType(platform)

    config = config_override or PLATFORM_CONFIGS.get(platform.value)
    if config is None:
        raise ValueError(
            f"No built-in config for platform '{platform.value}'. "
            f"Pass a PlatformConfig via config_override."
        )

    from goofish_agent.goofish_platform.client import GoofishClient

    if platform == PlatformType.GOOFISH:
        return GoofishClient(config)

    # For other platforms, use GoofishClient with platform-specific config as a
    # generic Playwright scraper.  Each platform can later get its own subclass
    # with custom parsers / auth logic.
    return GoofishClient(config)


__all__ = [
    "PlatformClient",
    "PlatformConfig",
    "PLATFORM_CONFIGS",
    "create_platform_client",
]
