from __future__ import annotations

import asyncio
import random

from playwright.async_api import Page


class AntiDetect:
    @staticmethod
    async def random_delay(min_s: float = 1.0, max_s: float = 5.0) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    @staticmethod
    async def human_type(page: Page, selector: str, text: str) -> None:
        await page.click(selector)
        for char in text:
            await page.keyboard.type(char, delay=random.randint(50, 200))

    @staticmethod
    async def bezier_move(page: Page, x: float, y: float, steps: int = 20) -> None:
        start_x = random.randint(300, 600)
        start_y = random.randint(200, 400)
        cp_x = start_x + (x - start_x) * random.uniform(0.2, 0.4) + random.randint(-50, 50)
        cp_y = start_y + (y - start_y) * random.uniform(0.2, 0.4) + random.randint(-50, 50)
        for i in range(steps + 1):
            t = i / steps
            bx = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * cp_x + t**2 * x
            by = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * cp_y + t**2 * y
            await page.mouse.move(bx, by)
            await asyncio.sleep(random.uniform(0.01, 0.03))

    @staticmethod
    async def human_click(page: Page, selector: str) -> None:
        box = await page.locator(selector).bounding_box()
        if not box:
            await page.click(selector)
            return
        x = box["x"] + box["width"] / 2 + random.randint(-3, 3)
        y = box["y"] + box["height"] / 2 + random.randint(-3, 3)
        await AntiDetect.bezier_move(page, x, y)
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await page.mouse.up()

    @staticmethod
    async def smooth_scroll(page: Page, distance: int = 500) -> None:
        scrolled = 0
        while scrolled < distance:
            step = random.randint(50, 150)
            await page.mouse.wheel(0, step)
            scrolled += step
            await asyncio.sleep(random.uniform(0.05, 0.2))

    @staticmethod
    async def random_browse_pause() -> None:
        await asyncio.sleep(random.uniform(3, 15))
