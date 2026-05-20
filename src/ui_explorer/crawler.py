from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

from playwright.async_api import BrowserContext, Page, async_playwright


LOGIN_TEXT = re.compile(r"\b(log in|login|sign in|signin|continue with|sso)\b", re.I)
AUTH_URL = re.compile(r"\b(login|signin|sign-in|auth|oauth|sso|saml|okta|auth0|cognito)\b", re.I)
ERROR_TEXT = re.compile(
    r"\b(error|failed|failure|exception|something went wrong|unable to load|"
    r"not found|unauthorized|forbidden|server error|timeout)\b",
    re.I,
)
UNSAFE_LABEL = re.compile(
    r"\b(delete|remove|archive|destroy|submit|save|send|pay|purchase|buy|"
    r"checkout|logout|log out|sign out|disconnect|deactivate|disable)\b",
    re.I,
)


@dataclass
class CrawlConfig:
    app_url: str
    llm_endpoint: str | None = None
    llm_model: str = "llama3.1:8b"
    visual_exploration: bool = False
    vision_endpoint: str | None = None
    vision_model: str | None = None
    profile_dir: str = ".ui-explorer-profile"
    artifact_dir: str = "artifacts"
    max_routes: int = 25
    max_clicks_per_route: int = 8
    max_seconds: int = 3600
    login_timeout_seconds: int = 900


@dataclass
class ConsoleEntry:
    type: str
    text: str
    location: dict[str, Any] | None = None


@dataclass
class NetworkEntry:
    url: str
    method: str
    status: int | None = None
    failure: str | None = None


@dataclass
class ElementEntry:
    role: str
    label: str
    href: str | None = None


@dataclass
class PageRecord:
    url: str
    title: str
    screenshot: str
    visible_errors: list[str] = field(default_factory=list)
    console: list[ConsoleEntry] = field(default_factory=list)
    network: list[NetworkEntry] = field(default_factory=list)
    links: list[ElementEntry] = field(default_factory=list)
    controls: list[ElementEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    visual_notes: list[str] = field(default_factory=list)


class Explorer:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
        self.start = self._normalize(config.app_url)
        self.start_url_parts = urlparse(self.start)
        self.allowed_origin = f"{self.start_url_parts.scheme}://{self.start_url_parts.netloc}"
        self.started_at = time.monotonic()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = Path(config.artifact_dir) / f"run-{timestamp}"
        self.screenshot_dir = self.run_dir / "screenshots"
        self.records: list[PageRecord] = []
        self.to_visit: list[str] = [self.start]
        self.visited: set[str] = set()
        self.console_buffer: list[ConsoleEntry] = []
        self.network_buffer: list[NetworkEntry] = []

    async def run(self) -> None:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                self.config.profile_dir,
                headless=False,
                viewport={"width": 1440, "height": 1000},
            )
            page = context.pages[0] if context.pages else await context.new_page()
            self._wire_observers(page)

            print(f"Opening {self.start}")
            await page.goto(self.start, wait_until="domcontentloaded")
            await self._settle_page(page)
            if await self._pause_for_login_if_needed(page):
                await page.goto(self.start, wait_until="domcontentloaded")
                await self._settle_page(page)

            while self.to_visit and len(self.visited) < self.config.max_routes:
                if time.monotonic() - self.started_at > self.config.max_seconds:
                    print("Time limit reached; writing report.")
                    break
                url = self.to_visit.pop(0)
                if url in self.visited or not self._is_allowed(url):
                    continue
                await self._visit(context, page, url)

            await self._write_outputs()
            await context.close()

        print(f"Done. Report: {self.run_dir / 'report.md'}")

    def _wire_observers(self, page: Page) -> None:
        page.on(
            "console",
            lambda message: self.console_buffer.append(
                ConsoleEntry(
                    type=message.type,
                    text=message.text,
                    location=message.location,
                )
            ),
        )
        page.on("response", lambda response: asyncio.create_task(self._record_response(response)))
        page.on("requestfailed", lambda request: self.network_buffer.append(
            NetworkEntry(url=request.url, method=request.method, failure=request.failure)
        ))

    async def _record_response(self, response: Any) -> None:
        if response.status >= 400:
            self.network_buffer.append(
                NetworkEntry(url=response.url, method=response.request.method, status=response.status)
            )

    async def _settle_page(self, page: Page) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            await page.wait_for_timeout(1200)

    async def _pause_for_login_if_needed(self, page: Page) -> bool:
        if not await self._looks_like_login(page) and self._same_start_location(page.url):
            return False
        if not await self._looks_like_login(page) and self._is_allowed(page.url):
            return False

        print("")
        print("Login or auth redirect detected.")
        print("Please complete login in the visible browser window.")
        print(f"I will continue when the browser returns to: {self.allowed_origin}{self.start_url_parts.path}")
        print("")

        deadline = time.monotonic() + self.config.login_timeout_seconds
        while time.monotonic() < deadline:
            await page.wait_for_timeout(1000)
            if self._same_start_location(page.url) and not await self._looks_like_login(page):
                print("Login appears complete. Continuing crawl.")
                return True

        raise TimeoutError("Timed out waiting for login to return to the app URL.")

    async def _visit(self, context: BrowserContext, page: Page, url: str) -> None:
        self.console_buffer = []
        self.network_buffer = []
        print(f"[{len(self.visited) + 1}/{self.config.max_routes}] Visiting {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await self._settle_page(page)
        if await self._pause_for_login_if_needed(page):
            await page.goto(url, wait_until="domcontentloaded")
            await self._settle_page(page)
        self.visited.add(url)
        await self._capture_page(page)
        await self._click_safe_controls(context, page, url)

    async def _capture_page(self, page: Page) -> None:
        index = len(self.records) + 1
        screenshot_name = f"{index:03d}.png"
        screenshot_path = self.screenshot_dir / screenshot_name
        await page.screenshot(path=screenshot_path, full_page=True)

        title = await page.title()
        links = await self._collect_links(page)
        controls = await self._collect_controls(page)
        visible_errors = await self._visible_error_text(page)
        notes = []
        visual_notes = []
        if visible_errors:
            notes.append(f"Visible error-like text found: {len(visible_errors)} item(s).")
        if self.console_buffer:
            notes.append(f"Console messages captured: {len(self.console_buffer)}.")
        if self.network_buffer:
            notes.append(f"Failed/error network events captured: {len(self.network_buffer)}.")

        if self.config.visual_exploration:
            visual_notes.append(
                await asyncio.to_thread(
                    self._request_visual_notes,
                    screenshot_path,
                    page.url,
                    title,
                    links,
                    controls,
                    visible_errors,
                )
            )

        for link in links:
            if link.href and self._is_allowed(link.href) and link.href not in self.visited:
                self._enqueue(link.href)

        self.records.append(
            PageRecord(
                url=page.url,
                title=title,
                screenshot=str(screenshot_path),
                visible_errors=visible_errors,
                console=list(self.console_buffer),
                network=list(self.network_buffer),
                links=links,
                controls=controls,
                notes=notes,
                visual_notes=visual_notes,
            )
        )

    async def _click_safe_controls(self, context: BrowserContext, page: Page, baseline_url: str) -> None:
        candidates = await self._safe_click_candidates(page)
        for candidate in candidates[: self.config.max_clicks_per_route]:
            if time.monotonic() - self.started_at > self.config.max_seconds:
                return
            label = candidate.get("label") or candidate.get("text") or ""
            selector = candidate["selector"]
            if not label.strip() or UNSAFE_LABEL.search(label):
                continue
            try:
                before_url = page.url
                await page.locator(selector).first.click(timeout=1500)
                await page.wait_for_timeout(800)
                after_url = self._normalize(page.url)
                if self._is_allowed(after_url) and after_url not in self.visited:
                    self._enqueue(after_url)
                if after_url != self._normalize(before_url):
                    await page.goto(baseline_url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(500)
                else:
                    await self._capture_transient_state(page, label)
            except Exception as exc:
                self.records[-1].notes.append(f"Click failed for '{label[:80]}': {exc.__class__.__name__}")

    async def _capture_transient_state(self, page: Page, label: str) -> None:
        errors = await self._visible_error_text(page)
        if errors:
            self.records[-1].notes.append(
                f"After clicking '{label[:80]}', visible error-like text appeared: {errors[:3]}"
            )
        if self.console_buffer:
            self.records[-1].notes.append(
                f"After clicking '{label[:80]}', console messages were captured: {len(self.console_buffer)}."
            )
        if self.network_buffer:
            self.records[-1].notes.append(
                f"After clicking '{label[:80]}', failed/error network events were captured: {len(self.network_buffer)}."
            )

    async def _collect_links(self, page: Page) -> list[ElementEntry]:
        links = await page.locator("a[href]").evaluate_all(
            """els => els.slice(0, 200).map(a => ({
                role: 'link',
                label: (a.innerText || a.getAttribute('aria-label') || a.href || '').trim(),
                href: a.href
            }))"""
        )
        return [
            ElementEntry(role="link", label=item["label"][:160], href=self._normalize(item["href"]))
            for item in links
            if item.get("href") and self._is_allowed(item["href"])
        ]

    async def _collect_controls(self, page: Page) -> list[ElementEntry]:
        controls = await page.locator(
            "button, [role=button], [role=tab], [role=menuitem], input, select, textarea"
        ).evaluate_all(
            """els => els.slice(0, 200).map(el => ({
                role: el.getAttribute('role') || el.tagName.toLowerCase(),
                label: (
                  el.innerText ||
                  el.getAttribute('aria-label') ||
                  el.getAttribute('placeholder') ||
                  el.getAttribute('name') ||
                  el.value ||
                  ''
                ).trim()
            }))"""
        )
        return [
            ElementEntry(role=item["role"], label=item["label"][:160])
            for item in controls
            if item.get("label")
        ]

    async def _safe_click_candidates(self, page: Page) -> list[dict[str, str]]:
        return await page.locator("button, [role=button], [role=tab], [role=menuitem]").evaluate_all(
            """els => els.slice(0, 80).map((el, i) => {
                if (!el.dataset.uiExplorerId) el.dataset.uiExplorerId = `ui-explorer-${Date.now()}-${i}`;
                const label = (
                  el.innerText ||
                  el.getAttribute('aria-label') ||
                  el.getAttribute('title') ||
                  el.getAttribute('href') ||
                  ''
                ).trim();
                return { selector: `[data-ui-explorer-id="${el.dataset.uiExplorerId}"]`, label };
            })"""
        )

    async def _visible_error_text(self, page: Page) -> list[str]:
        texts = await page.locator("body").evaluate(
            """body => Array.from(body.querySelectorAll('*'))
                .filter(el => {
                  const style = window.getComputedStyle(el);
                  const rect = el.getBoundingClientRect();
                  const role = el.getAttribute('role');
                  const isLeafish = el.children.length === 0 || role === 'alert' || role === 'status';
                  return style &&
                    isLeafish &&
                    style.visibility !== 'hidden' &&
                    style.display !== 'none' &&
                    rect.width > 0 &&
                    rect.height > 0;
                })
                .map(el => (el.innerText || '').trim())
                .filter(Boolean)
                .slice(0, 1000)"""
        )
        seen: set[str] = set()
        matches: list[str] = []
        for text in texts:
            compact = " ".join(text.split())
            if len(compact) > 240:
                continue
            if compact not in seen and ERROR_TEXT.search(compact):
                seen.add(compact)
                matches.append(compact)
        return matches[:25]

    async def _looks_like_login(self, page: Page) -> bool:
        try:
            if AUTH_URL.search(page.url):
                return True
            password_count = await page.locator("input[type=password]").count()
            body_text = await page.locator("body").inner_text(timeout=1500)
            return password_count > 0 or bool(LOGIN_TEXT.search(body_text[:4000]))
        except Exception:
            return False

    async def _write_outputs(self) -> None:
        payload = {
            "config": asdict(self.config),
            "started_at": datetime.now().isoformat(),
            "visited_count": len(self.visited),
            "queued_count": len(self.to_visit),
            "records": [asdict(record) for record in self.records],
        }
        json_path = self.run_dir / "inventory.json"
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        llm_notes = None
        if self.config.llm_endpoint:
            llm_notes = await asyncio.to_thread(self._request_llm_notes, payload)

        report = self._render_markdown(payload, llm_notes)
        (self.run_dir / "report.md").write_text(report, encoding="utf-8")

    def _request_llm_notes(self, payload: dict[str, Any]) -> str:
        compact_records = []
        for record in payload["records"]:
            compact_records.append(
                {
                    "url": record["url"],
                    "title": record["title"],
                    "visible_errors": record["visible_errors"][:10],
                    "console": record["console"][:10],
                    "network": record["network"][:10],
                    "notes": record["notes"][:10],
                    "controls": record["controls"][:25],
                    "links": record["links"][:25],
                }
            )
        body = {
            "model": self.config.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You review deterministic web-app crawl results. "
                        "Return concise follow-up notes, likely bugs, and suggested next crawl improvements."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"records": compact_records}, ensure_ascii=False),
                },
            ],
            "temperature": 0.2,
        }
        request = Request(
            self.config.llm_endpoint or "",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            return f"LLM review failed: {exc}"

    def _request_visual_notes(
        self,
        screenshot_path: Path,
        url: str,
        title: str,
        links: list[ElementEntry],
        controls: list[ElementEntry],
        visible_errors: list[str],
    ) -> str:
        endpoint = self.config.vision_endpoint or self.config.llm_endpoint
        model = self.config.vision_model or self.config.llm_model
        if not endpoint:
            return "Visual exploration skipped: no --vision-endpoint or --llm-endpoint was provided."

        data_url = self._image_data_url(screenshot_path)
        context = {
            "url": url,
            "title": title,
            "visible_error_text": visible_errors[:10],
            "links": [asdict(link) for link in links[:30]],
            "controls": [asdict(control) for control in controls[:40]],
        }
        body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are visually reviewing screenshots from a headed web-app crawler. "
                        "Be concise and practical. Focus on visible UI regions, likely navigation "
                        "affordances, modals/overlays, error states, empty/loading states, confusing "
                        "layout, and follow-up targets for a human tester. Do not invent data that "
                        "is not visible."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Review this page screenshot for visual exploration notes. "
                                f"DOM/context summary:\n{json.dumps(context, ensure_ascii=False)}"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ],
                },
            ],
            "temperature": 0.2,
        }
        request = Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            return f"Visual review failed: {exc}"

    def _image_data_url(self, path: Path) -> str:
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _render_markdown(self, payload: dict[str, Any], llm_notes: str | None) -> str:
        lines = [
            "# UI Explorer Report",
            "",
            f"- App URL: `{self.start}`",
            f"- Visited routes: {payload['visited_count']}",
            f"- Remaining queue: {payload['queued_count']}",
            "",
            "## Follow-up Notes",
            "",
        ]
        all_notes = [
            note
            for record in self.records
            for note in record.notes
        ]
        if all_notes:
            lines.extend(f"- {note}" for note in all_notes[:50])
        else:
            lines.append("- No obvious crawler-generated notes.")
        if llm_notes:
            lines.extend(["", "## LLM Review", "", llm_notes.strip()])
        lines.extend(["", "## Pages", ""])
        for index, record in enumerate(self.records, start=1):
            lines.extend(
                [
                    f"### {index}. {record.title or '(untitled)'}",
                    "",
                    f"- URL: `{record.url}`",
                    f"- Screenshot: `{record.screenshot}`",
                    f"- Links found: {len(record.links)}",
                    f"- Controls found: {len(record.controls)}",
                    f"- Visible error-like text: {len(record.visible_errors)}",
                    f"- Console messages: {len(record.console)}",
                    f"- Failed/error network events: {len(record.network)}",
                ]
            )
            for error in record.visible_errors[:5]:
                lines.append(f"  - Visible: {error}")
            for entry in record.network[:5]:
                label = entry.status if entry.status is not None else entry.failure
                lines.append(f"  - Network: {label} {entry.method} {entry.url}")
            if record.visual_notes:
                lines.extend(["", "#### Visual Notes", ""])
                for note in record.visual_notes:
                    lines.append(note.strip())
            lines.append("")
        return "\n".join(lines)

    def _enqueue(self, url: str) -> None:
        normalized = self._normalize(url)
        if normalized not in self.visited and normalized not in self.to_visit:
            self.to_visit.append(normalized)

    def _normalize(self, url: str) -> str:
        absolute = urljoin(self.config.app_url, url)
        stripped, _fragment = urldefrag(absolute)
        parsed = urlparse(stripped)
        path = parsed.path or "/"
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.scheme}://{parsed.netloc}{path}{query}"

    def _is_allowed(self, url: str) -> bool:
        parsed = urlparse(self._normalize(url))
        if f"{parsed.scheme}://{parsed.netloc}" != self.allowed_origin:
            return False
        return parsed.scheme in {"http", "https"}

    def _same_start_location(self, url: str) -> bool:
        parsed = urlparse(self._normalize(url))
        return (
            f"{parsed.scheme}://{parsed.netloc}" == self.allowed_origin
            and parsed.path == (self.start_url_parts.path or "/")
        )
