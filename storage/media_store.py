from pathlib import Path

import httpx

from goofish_agent.config.settings import get_settings


class MediaStore:
    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or get_settings().media_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def download(self, url: str, filename: str) -> Path:
        dest = self.base_dir / filename
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return dest

    def get_path(self, filename: str) -> Path:
        return self.base_dir / filename
