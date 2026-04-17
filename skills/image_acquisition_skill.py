"""图片获取 Skill.

品相鉴定需要真正把商品图拿到本地喂给 VLM。但二手平台 (尤其闲鱼) 的 CDN
对直链访问做了严格的防盗链 / Token 校验，有时 ``httpx.get`` 会拿到 403
或 1x1 像素占位图。本 Skill 提供 4 级降级策略：

    Strategy 1  直接 HTTP 下载
    Strategy 2  带 Referer / UA 的 HTTP 下载 (绕过简单防盗链)
    Strategy 3  Playwright: 打开详情页 → 定位 <img> 元素 → 元素级截图
    Strategy 4  OS 原生截图命令
                  * macOS   : screencapture
                  * Linux   : gnome-screenshot / scrot / grim
                  * Windows : powershell System.Drawing

每张图独立尝试所有策略，任一成功即记录并转 base64 给 VLM 使用。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import platform as _py_platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import httpx
from loguru import logger

from goofish_agent.skills.base import BaseSkill, SkillResult

if TYPE_CHECKING:
    from playwright.async_api import Page


Method = Literal["http_direct", "http_with_referer", "playwright_element", "os_screenshot"]


@dataclass
class ImageAcquisitionInput:
    """图片获取 Skill 入参.

    Attributes:
        image_urls:   商品原始图片 URL 列表.
        product_url:  商品详情页 URL (用作 Referer + Playwright / 截图的目标).
        page:         可选的 Playwright Page；若为空，Strategy 3/4 会跳过.
        save_dir:     本地保存目录；默认走 ``media_cache/assessment``.
        max_images:   最多处理图片数 (避免超大列表).
    """

    image_urls: list[str]
    product_url: str = ""
    page: "Page | None" = None
    save_dir: Path | None = None
    max_images: int = 6


@dataclass
class ImageAcquisitionResult(SkillResult):
    """图片获取 Skill 结果.

    Attributes:
        local_paths:   成功获取的本地文件路径.
        base64_images: 对应的 base64 编码 (直接塞给 VLM 的 data URL).
        methods:       每张图使用的策略 (与 local_paths 对齐).
        failed_urls:   全部策略都失败的 URL.
    """

    local_paths: list[Path] = field(default_factory=list)
    base64_images: list[str] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    failed_urls: list[str] = field(default_factory=list)


class ImageAcquisitionSkill(BaseSkill):
    """Skill: 多策略获取商品图片到本地，兜底保证 VLM 品相鉴定有图可看."""

    name = "image_acquisition"
    description = "多策略获取商品图片 (HTTP 下载 / 带防盗链下载 / 元素截图 / OS 截屏)"

    DEFAULT_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    HTTP_TIMEOUT = 15.0

    def __init__(self, default_save_dir: Path | None = None) -> None:
        self._default_save_dir = default_save_dir or Path("media_cache/assessment")
        self._default_save_dir.mkdir(parents=True, exist_ok=True)

    async def execute(self, params: ImageAcquisitionInput) -> ImageAcquisitionResult:  # type: ignore[override]
        save_dir = params.save_dir or self._default_save_dir
        save_dir.mkdir(parents=True, exist_ok=True)

        urls = params.image_urls[: params.max_images]
        self._log_info(
            f"开始获取 {len(urls)} 张图片 | 商品页: {params.product_url[:60]!r} | "
            f"save_dir={save_dir}"
        )

        result = ImageAcquisitionResult(success=True)
        for idx, url in enumerate(urls, 1):
            path, method = await self._acquire_one(
                url=url,
                product_url=params.product_url,
                page=params.page,
                save_dir=save_dir,
                idx=idx,
            )
            if path is None:
                result.failed_urls.append(url)
                self._log_warning(f"[{idx}/{len(urls)}] ✗ 全部策略均失败: {url[:80]}")
                continue

            try:
                b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            except Exception as e:
                self._log_warning(f"[{idx}/{len(urls)}] ✗ 读取本地文件失败 {path}: {e}")
                result.failed_urls.append(url)
                continue

            result.local_paths.append(path)
            result.base64_images.append(b64)
            result.methods.append(method)
            self._log_info(
                f"[{idx}/{len(urls)}] ✓ 获取成功 | 策略={method} | "
                f"大小={path.stat().st_size} B | 文件={path.name}"
            )

        result.success = len(result.local_paths) > 0
        if not result.success:
            result.error = "no_image_acquired"

        self._log_info(
            f"✨ 图片获取完成 | 成功 {len(result.local_paths)}/{len(urls)} | "
            f"失败 {len(result.failed_urls)} | "
            f"策略分布: {self._summarize_methods(result.methods)}"
        )
        return result

    # ------------------------------------------------------------------
    # Per-image fallback chain
    # ------------------------------------------------------------------
    async def _acquire_one(
        self,
        *,
        url: str,
        product_url: str,
        page: "Page | None",
        save_dir: Path,
        idx: int,
    ) -> tuple[Path | None, Method]:
        basename = self._hash_filename(url, idx)

        # Strategy 1: 直接 HTTP
        try:
            path = await self._http_download(
                url=url, dest=save_dir / f"{basename}_direct.jpg", referer=None
            )
            if path and self._looks_like_image(path):
                return path, "http_direct"
        except Exception as e:
            self._log_warning(f"[{idx}] http_direct 失败: {e}")

        # Strategy 2: 带 Referer
        if product_url:
            try:
                path = await self._http_download(
                    url=url,
                    dest=save_dir / f"{basename}_referer.jpg",
                    referer=product_url,
                )
                if path and self._looks_like_image(path):
                    return path, "http_with_referer"
            except Exception as e:
                self._log_warning(f"[{idx}] http_with_referer 失败: {e}")

        # Strategy 3: Playwright 元素截图
        if page is not None:
            try:
                path = await self._playwright_element_shot(
                    page=page, url=url, dest=save_dir / f"{basename}_elem.png"
                )
                if path and self._looks_like_image(path):
                    return path, "playwright_element"
            except Exception as e:
                self._log_warning(f"[{idx}] playwright_element 失败: {e}")

        # Strategy 4: OS 原生截屏
        try:
            path = await self._os_screenshot(dest=save_dir / f"{basename}_os.png")
            if path and self._looks_like_image(path):
                return path, "os_screenshot"
        except Exception as e:
            self._log_warning(f"[{idx}] os_screenshot 失败: {e}")

        return None, "http_direct"  # method 字段被 failed_urls 分支忽略

    # ------------------------------------------------------------------
    # Strategy 1 & 2
    # ------------------------------------------------------------------
    async def _http_download(
        self, *, url: str, dest: Path, referer: str | None
    ) -> Path | None:
        headers = {"User-Agent": self.DEFAULT_UA}
        if referer:
            parsed = urlparse(referer)
            headers["Referer"] = referer
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        async with httpx.AsyncClient(
            timeout=self.HTTP_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                self._log_warning(f"http {resp.status_code} for {url[:80]}")
                return None
            dest.write_bytes(resp.content)
        return dest

    # ------------------------------------------------------------------
    # Strategy 3 — Playwright element screenshot
    # ------------------------------------------------------------------
    async def _playwright_element_shot(
        self, *, page: "Page", url: str, dest: Path
    ) -> Path | None:
        # 尝试按 src / data-src 精确匹配，再退化为首张可见图
        selectors = [
            f'img[src="{url}"]',
            f'img[data-src="{url}"]',
            f'img[src*="{Path(urlparse(url).path).name}"]',
        ]
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if await locator.count() == 0:
                    continue
                await locator.scroll_into_view_if_needed(timeout=2_000)
                await locator.screenshot(path=str(dest), timeout=5_000)
                return dest
            except Exception:
                continue

        # 最后兜底：第一张大尺寸 img
        try:
            any_img = page.locator("img").first
            if await any_img.count() > 0:
                await any_img.scroll_into_view_if_needed(timeout=2_000)
                await any_img.screenshot(path=str(dest), timeout=5_000)
                return dest
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Strategy 4 — OS screenshot
    # ------------------------------------------------------------------
    async def _os_screenshot(self, *, dest: Path) -> Path | None:
        os_name = _py_platform.system().lower()
        if os_name == "darwin":
            cmd = ["screencapture", "-x", str(dest)]
        elif os_name == "linux":
            if shutil.which("gnome-screenshot"):
                cmd = ["gnome-screenshot", "-f", str(dest)]
            elif shutil.which("scrot"):
                cmd = ["scrot", str(dest)]
            elif shutil.which("grim"):
                cmd = ["grim", str(dest)]
            else:
                self._log_warning("Linux 无可用截图命令 (gnome-screenshot/scrot/grim)")
                return None
        elif os_name == "windows":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
                "$g=[System.Drawing.Graphics]::FromImage($bmp); "
                f"$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size); "
                f"$bmp.Save('{dest}'); $g.Dispose(); $bmp.Dispose()"
            )
            cmd = ["powershell", "-NoProfile", "-Command", ps]
        else:
            self._log_warning(f"未知 OS: {os_name}")
            return None

        self._log_info(f"OS 截屏命令: {cmd[0]} → {dest.name}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            self._log_warning(
                f"OS 截屏失败 rc={proc.returncode}: {stderr.decode(errors='replace')[:200]}"
            )
            return None
        return dest if dest.exists() else None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _hash_filename(url: str, idx: int) -> str:
        h = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
        return f"img{idx:02d}_{h}"

    @staticmethod
    def _looks_like_image(path: Path) -> bool:
        """简单校验：文件非空且不是 1x1 占位图 (< 500B 视为异常)."""
        try:
            return path.exists() and path.stat().st_size >= 500
        except Exception:
            return False

    @staticmethod
    def _summarize_methods(methods: list[Method]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in methods:
            counts[m] = counts.get(m, 0) + 1
        return counts
