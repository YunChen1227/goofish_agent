"""Playwright / browser-specific errors for the goofish platform client."""


class BrowserClosedByUserError(RuntimeError):
    """Raised when the user closed the Playwright browser tab or window while a task is running."""


def is_playwright_target_closed_error(exc: BaseException) -> bool:
    """True if *exc* is Playwright's target/context/browser closed (user or crash)."""
    if isinstance(exc, BrowserClosedByUserError):
        return False
    if type(exc).__name__ == "TargetClosedError":
        return True
    msg = str(exc).lower()
    if "target" in msg and "closed" in msg:
        return True
    if "browser has been closed" in msg:
        return True
    return False
