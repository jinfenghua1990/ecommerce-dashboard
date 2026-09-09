#!/usr/bin/env python3
"""真实浏览器 UI 运行时验收。

在 CI 的全新数据库中启动生产静态站点 + FastAPI 后执行：
- 真实登录；
- 逐页访问正式侧栏路由；
- 对当前可见且 enabled 的按钮逐个点击；
- 捕获 pageerror、API 5xx、登录态丢失与点击异常；
- 单独验证退出登录。

说明：空测试库中不会渲染的行级操作按钮由静态 AST interaction check 覆盖；
本脚本负责验证“当前页面实际渲染出来的交互”在浏览器中可运行。
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

BASE = os.environ.get("BASE", "http://127.0.0.1:8000").rstrip("/")
USERNAME = os.environ.get("UI_TEST_USERNAME", "ci-admin")
PASSWORD = os.environ.get("UI_TEST_PASSWORD", "ci-only-ui-password")

ROUTES = [
    "/",
    "/sales",
    "/products",
    "/supply-chain",
    "/supply-chain/production",
    "/supply-chain/material-flow",
    "/supply-chain/in-transit",
    "/supply-chain/receiving",
    "/purchase/workbench",
    "/supply-chain/warehouses",
    "/products/inventory-goods",
    "/products/inventory-consumables",
    "/payments",
    "/profit",
    "/finance",
    "/finance/tax-accounting",
    "/finance/tax-accounting/categories",
    "/data-center-import",
    "/exceptions",
    "/automation",
    "/settings",
]

SKIP_BUTTON_TEXT = {
    "退出登录",  # 最后单独验证，避免中途丢失会话
}

FATAL_TEXT = (
    "Application error",
    "Internal Server Error",
    "Unhandled Runtime Error",
    "ChunkLoadError",
)


@dataclass
class Failure:
    route: str
    button: str
    detail: str


def label_of(page: Page, index: int) -> str:
    button = page.locator("button:visible:not([disabled])").nth(index)
    text = re.sub(r"\s+", " ", (button.inner_text(timeout=1000) or "").strip())
    if text:
        return text[:80]
    for attr in ("aria-label", "title", "name"):
        value = button.get_attribute(attr)
        if value:
            return f"[{attr}={value}]"[:80]
    return f"button#{index + 1}"


def wait_ready(page: Page) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=10000)
    try:
        page.wait_for_load_state("networkidle", timeout=3500)
    except PlaywrightTimeoutError:
        # 自动刷新/轮询页可能永远达不到 networkidle；DOM 可用即可继续。
        pass


def assert_page_healthy(page: Page, route: str) -> list[Failure]:
    failures: list[Failure] = []
    if urlparse(page.url).path.startswith("/login") and route != "/login":
        failures.append(Failure(route, "页面", "被重定向到 /login，登录态丢失"))
    try:
        body = page.locator("body").inner_text(timeout=2000)
    except Exception as exc:  # pragma: no cover - CI 诊断
        failures.append(Failure(route, "页面", f"无法读取页面内容: {type(exc).__name__}"))
        return failures
    for token in FATAL_TEXT:
        if token in body:
            failures.append(Failure(route, "页面", f"出现致命错误文案: {token}"))
    return failures


def login(page: Page) -> None:
    page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=15000)
    page.locator("#username").fill(USERNAME)
    page.locator("#password").fill(PASSWORD)
    page.get_by_role("button", name="登录", exact=True).click(timeout=5000)
    page.wait_for_url(lambda url: urlparse(url).path != "/login", timeout=10000)
    wait_ready(page)
    if urlparse(page.url).path == "/login":
        raise RuntimeError("真实登录失败")


def test_button(page: Page, route: str, index: int) -> tuple[list[Failure], bool]:
    failures: list[Failure] = []
    page.goto(f"{BASE}{route}", wait_until="domcontentloaded", timeout=15000)
    wait_ready(page)
    failures.extend(assert_page_healthy(page, route))
    if failures:
        return failures, False

    buttons = page.locator("button:visible:not([disabled])")
    if index >= buttons.count():
        return failures, False
    label = label_of(page, index)
    if label in SKIP_BUTTON_TEXT:
        return failures, False

    button = buttons.nth(index)
    before_url = page.url
    try:
        before_text = page.locator("body").inner_text(timeout=1500)
    except Exception:
        before_text = ""

    api_5xx: list[str] = []
    page_errors: list[str] = []
    signals = {"dialog": 0, "filechooser": 0, "popup": 0, "request": 0}

    def on_response(response):
        try:
            parsed = urlparse(response.url)
            base_parsed = urlparse(BASE)
            if parsed.netloc == base_parsed.netloc and parsed.path.startswith("/api/"):
                signals["request"] += 1
                if response.status >= 500:
                    api_5xx.append(f"{response.status} {parsed.path}")
        except Exception:
            pass

    def on_page_error(error):
        page_errors.append(str(error))

    def on_dialog(dialog):
        signals["dialog"] += 1
        try:
            dialog.dismiss()
        except Exception:
            pass

    def on_filechooser(_chooser):
        signals["filechooser"] += 1

    def on_popup(popup):
        signals["popup"] += 1
        try:
            popup.close()
        except Exception:
            pass

    page.on("response", on_response)
    page.on("pageerror", on_page_error)
    page.on("dialog", on_dialog)
    page.on("filechooser", on_filechooser)
    page.on("popup", on_popup)

    click_error = None
    try:
        button.scroll_into_view_if_needed(timeout=1500)
        button.click(timeout=3500)
    except PlaywrightTimeoutError as exc:
        # 某些上传控件会把系统文件选择器作为唯一副作用；有 filechooser 信号则视为已执行。
        if not signals["filechooser"]:
            click_error = f"点击超时: {exc.__class__.__name__}"
    except Exception as exc:  # pragma: no cover - CI 诊断
        click_error = f"点击异常: {type(exc).__name__}: {exc}"

    page.wait_for_timeout(450)
    if click_error:
        failures.append(Failure(route, label, click_error))
    if page_errors:
        failures.append(Failure(route, label, "pageerror: " + " | ".join(page_errors[:3])))
    if api_5xx:
        failures.append(Failure(route, label, "API 5xx: " + " | ".join(api_5xx[:5])))
    failures.extend(assert_page_healthy(page, route))

    try:
        after_text = page.locator("body").inner_text(timeout=1500)
    except Exception:
        after_text = before_text
    observable = (
        page.url != before_url
        or after_text != before_text
        or signals["request"] > 0
        or signals["dialog"] > 0
        or signals["filechooser"] > 0
        or signals["popup"] > 0
    )

    # 解绑，避免下一按钮重复累计。
    page.remove_listener("response", on_response)
    page.remove_listener("pageerror", on_page_error)
    page.remove_listener("dialog", on_dialog)
    page.remove_listener("filechooser", on_filechooser)
    page.remove_listener("popup", on_popup)
    return failures, observable


def main() -> int:
    failures: list[Failure] = []
    warnings: list[str] = []
    visited = 0
    clicked = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.set_default_timeout(5000)

        try:
            login(page)
        except Exception as exc:
            print(f"FAIL login: {type(exc).__name__}: {exc}")
            browser.close()
            return 1

        print(f"登录通过: {USERNAME}")
        for route in ROUTES:
            page.goto(f"{BASE}{route}", wait_until="domcontentloaded", timeout=15000)
            wait_ready(page)
            route_failures = assert_page_healthy(page, route)
            if route_failures:
                failures.extend(route_failures)
                continue
            visited += 1
            initial_count = page.locator("button:visible:not([disabled])").count()
            labels = []
            for i in range(initial_count):
                try:
                    labels.append(label_of(page, i))
                except Exception:
                    labels.append(f"button#{i + 1}")
            print(f"[{route}] 可见 enabled 按钮 {initial_count}: {', '.join(labels) if labels else '-'}")

            for i, label in enumerate(labels):
                if label in SKIP_BUTTON_TEXT:
                    continue
                result, observable = test_button(page, route, i)
                failures.extend(result)
                if not result:
                    clicked += 1
                    if not observable:
                        warnings.append(f"{route} :: {label}（点击后无可观测变化，静态 handler 检查已覆盖）")

        # 退出登录单独验证。
        page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=15000)
        wait_ready(page)
        logout = page.get_by_role("button", name="退出登录", exact=True)
        if logout.count() == 1:
            logout.click(timeout=4000)
            page.wait_for_url(lambda url: urlparse(url).path == "/login", timeout=8000)
            if urlparse(page.url).path != "/login":
                failures.append(Failure("/", "退出登录", "点击后未回到 /login"))
            else:
                clicked += 1
        else:
            failures.append(Failure("/", "退出登录", "未找到唯一退出登录按钮"))

        context.close()
        browser.close()

    print("\n=== UI Runtime Summary ===")
    print(f"正式页面: {visited}/{len(ROUTES)}")
    print(f"实际点击: {clicked}")
    print(f"警告: {len(warnings)}")
    for item in warnings[:30]:
        print(f"WARN {item}")
    print(f"失败: {len(failures)}")
    for item in failures:
        print(f"FAIL {item.route} :: {item.button} :: {item.detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
