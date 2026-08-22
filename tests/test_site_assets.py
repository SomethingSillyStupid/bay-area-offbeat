from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
INDEX = SITE / "index.html"
STYLES = SITE / "styles.css"
APP = SITE / "app.js"


class MarkupProbe(HTMLParser):
    """Collect enough HTML structure for dependency-free static checks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.starts: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.starts.append((tag, dict(attrs)))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        self.text.append(data)

    def elements(self, tag: str) -> list[dict[str, str | None]]:
        return [attrs for found_tag, attrs in self.starts if found_tag == tag]


class IndexMarkupTests(unittest.TestCase):
    def load_markup(self) -> tuple[str, MarkupProbe]:
        self.assertTrue(INDEX.is_file(), "site/index.html must exist")
        source = INDEX.read_text(encoding="utf-8")
        probe = MarkupProbe()
        probe.feed(source)
        return source, probe

    def test_page_wires_local_assets_and_required_sections(self) -> None:
        source, probe = self.load_markup()
        links = probe.elements("link")
        scripts = probe.elements("script")
        sections = {attrs.get("id") for attrs in probe.elements("section")}

        self.assertIn("Bay Area Offbeat", source)
        self.assertTrue(
            any(
                attrs.get("rel") == "stylesheet" and attrs.get("href") == "styles.css"
                for attrs in links
            )
        )
        self.assertTrue(
            any(attrs.get("src") == "app.js" and "defer" in attrs for attrs in scripts)
        )
        self.assertEqual(
            {"this-week", "next-week", "radar"},
            {"this-week", "next-week", "radar"} & sections,
        )
        self.assertTrue(any(attrs.get("id") == "main-content" for attrs in probe.elements("main")))
        self.assertTrue(probe.elements("header"))
        self.assertTrue(probe.elements("footer"))

    def test_page_has_a_stable_canonical_url_and_crawl_metadata(self) -> None:
        _, probe = self.load_markup()
        canonical_url = "https://somethingsillystupid.github.io/bay-area-offbeat/"
        links = probe.elements("link")
        metas = probe.elements("meta")

        self.assertTrue(
            any(
                attrs.get("rel") == "canonical" and attrs.get("href") == canonical_url
                for attrs in links
            )
        )
        self.assertTrue(
            any(
                attrs.get("name") == "robots" and attrs.get("content") == "index,follow"
                for attrs in metas
            )
        )

    def test_public_crawl_assets_are_present_and_reference_the_canonical_page(self) -> None:
        canonical_url = "https://somethingsillystupid.github.io/bay-area-offbeat/"
        robots = SITE / "robots.txt"
        sitemap = SITE / "sitemap.xml"

        self.assertTrue(robots.is_file(), "site/robots.txt must exist")
        self.assertTrue(sitemap.is_file(), "site/sitemap.xml must exist")
        self.assertIn("User-agent: *", robots.read_text(encoding="utf-8"))
        self.assertIn("Sitemap:", robots.read_text(encoding="utf-8"))
        self.assertIn(canonical_url, robots.read_text(encoding="utf-8"))
        self.assertIn(canonical_url, sitemap.read_text(encoding="utf-8"))

    def test_editorial_copy_sets_scope_and_change_disclaimer(self) -> None:
        source, probe = self.load_markup()
        visible_text = " ".join(" ".join(probe.text).split())

        self.assertIn("This week", visible_text)
        self.assertIn("Next week", visible_text)
        self.assertIn("On the radar", visible_text)
        self.assertRegex(visible_text, r"(?i)short list|selective")
        self.assertRegex(visible_text, r"(?i)event details can change")
        self.assertRegex(visible_text, r"(?i)check (?:with )?(?:the )?organizers")
        self.assertIn('id="updated-at"', source)

    def test_skip_link_and_no_script_fallback_are_present(self) -> None:
        source, probe = self.load_markup()
        skip_links = [
            attrs
            for attrs in probe.elements("a")
            if "skip-link" in (attrs.get("class") or "").split()
        ]
        self.assertTrue(any(attrs.get("href") == "#main-content" for attrs in skip_links))
        self.assertIn("<noscript", source.lower())
        self.assertRegex(source, r"(?is)<noscript.*?JavaScript is needed.*?</noscript>")
        self.assertRegex(
            source,
            r'(?is)<noscript.*?<a[^>]+href=["\']data/current\.json["\']',
        )

    def test_markup_has_no_remote_assets_or_forbidden_growth_copy(self) -> None:
        source, probe = self.load_markup()
        asset_urls = [
            attrs.get("href")
            for attrs in probe.elements("link")
            if attrs.get("rel") == "stylesheet"
        ] + [attrs.get("src") for attrs in probe.elements("script")]
        self.assertFalse(any((url or "").startswith(("http://", "https://", "//")) for url in asset_urls))
        self.assertFalse(probe.elements("img"))

        growth_copy = (
            "sign up",
            "signup",
            "subscribe",
            "submit your event",
            "follow us",
            "share this",
            "affiliate",
            "sponsor",
            "advertis",
            "buy tickets",
            "add to calendar",
        )
        lowered = re.sub(r"\s+", " ", source.casefold())
        for marker in growth_copy:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, lowered)


class JavaScriptSourceTests(unittest.TestCase):
    def load_script(self) -> str:
        self.assertTrue(APP.is_file(), "site/app.js must exist")
        return APP.read_text(encoding="utf-8")

    def test_fetches_canonical_document_and_checks_its_schema(self) -> None:
        source = self.load_script()

        self.assertIn("data/current.json", source)
        self.assertRegex(source, r"\bfetch\s*\(")
        self.assertIn("response.ok", source)
        for field in ("schema_version", "generated_at", "timezone", "events"):
            with self.subTest(field=field):
                self.assertIn(field, source)
        self.assertIn("America/Los_Angeles", source)
        self.assertIn("validateDocument", source)
        self.assertRegex(source, r"\.catch\s*\(")

    def test_week_bucketing_uses_pacific_calendar_dates_from_generated_at(self) -> None:
        source = self.load_script()

        classifier = re.search(
            r"function classifyEvents\(documentValue\) \{(?P<body>.*?)\n\}\n\nfunction isNonEmptyString",
            source,
            re.DOTALL,
        )

        self.assertIn("generated_at", source)
        self.assertIn("Intl.DateTimeFormat", source)
        self.assertIn("formatToParts", source)
        self.assertIn("Date.UTC", source)
        self.assertIn("getUTCDay", source)
        self.assertIn("setUTCDate", source)
        self.assertIn("classifyEvents", source)
        self.assertIn("thisWeekEnd", source)
        self.assertIn("nextWeekEnd", source)
        self.assertIn("event.radar === true", source)
        self.assertIsNotNone(classifier)
        self.assertNotIn("Date.now", classifier.group("body"))  # type: ignore[union-attr]
        self.assertNotRegex(source, r"new\s+Date\s*\(\s*\)")
        self.assertNotRegex(source, r"(?:7|14)\s*\*\s*24\s*\*\s*60\s*\*\s*60")

    def test_client_refuses_stale_editions_using_the_public_freshness_contract(self) -> None:
        source = self.load_script()

        self.assertIn("MAX_EDITION_AGE_MS", source)
        self.assertRegex(source, r"36\s*\*\s*60\s*\*\s*60\s*\*\s*1000")
        self.assertIn("assertFreshTimestamp", source)
        self.assertIn("last_verified_at", source)
        self.assertIn("Date.now()", source)
        self.assertRegex(source, r"assertFreshTimestamp\(documentValue\.generated_at")
        self.assertRegex(source, r"assertFreshTimestamp\(event\.last_verified_at")

    def test_public_date_and_grouping_api_is_exposed_without_loading_in_nonbrowser_runtimes(self) -> None:
        source = self.load_script()

        self.assertIn("BayAreaOffbeat", source)
        self.assertIn("module.exports", source)
        self.assertIn('typeof document !== "undefined"', source)
        for helper in (
            "parseInstant",
            "calendarDateFromInstant",
            "addCalendarDays",
            "calendarKey",
            "mondayFor",
            "compareEvents",
            "classifyEvents",
            "formatUpdatedAt",
        ):
            with self.subTest(helper=helper):
                self.assertRegex(source, rf"\b{helper}\b")

    def test_events_are_sorted_grouped_and_rendered_with_required_fields(self) -> None:
        source = self.load_script()

        self.assertIn("compareEvents", source)
        self.assertRegex(source, r"\.sort\s*\(\s*compareEvents\s*\)")
        self.assertIn("groupByDate", source)
        self.assertIn('createElement("h3")', source)
        self.assertIn('createElement("time")', source)
        for field in (
            "title",
            "starts_at",
            "ends_at",
            "all_day",
            "city",
            "neighborhood",
            "price_note",
            "official_url",
            "source_name",
            "why",
            "tags",
            "last_verified_at",
        ):
            with self.subTest(field=field):
                self.assertIn(f"event.{field}", source)
        self.assertIn("All day", source)
        self.assertIn("Why it’s on the list", source)
        self.assertIn("Details", source)

    def test_events_render_as_compact_rows_with_separate_schedule_and_body_regions(self) -> None:
        source = self.load_script()

        self.assertIn("createEventRow", source)
        self.assertIn('article.className = "event-row"', source)
        self.assertIn('body.className = "event-row-body"', source)
        self.assertIn('topline.className = "event-row-topline"', source)
        self.assertIn("cards.append(createEventRow(event, 4))", source)
        self.assertNotIn("createEventCard", source)

    def test_same_time_ties_use_validated_ascii_event_ids(self) -> None:
        source = self.load_script()
        comparator = re.search(
            r"function compareEvents\(left, right\) \{(?P<body>.*?)\n\}\n\nfunction formatUpdatedAt",
            source,
            re.DOTALL,
        )

        self.assertIsNotNone(comparator)
        body = comparator.group("body")  # type: ignore[union-attr]
        self.assertIn("left.id", body)
        self.assertIn("right.id", body)
        self.assertNotIn("title", body)
        self.assertNotIn("localeCompare", body)
        self.assertRegex(source, r"!isNonEmptyString\(event\.id\)")

    def test_payload_is_inserted_only_with_safe_dom_apis(self) -> None:
        source = self.load_script()

        self.assertIn("textContent", source)
        self.assertIn("createElement", source)
        for unsafe_api in (
            "innerHTML",
            "outerHTML",
            "insertAdjacentHTML",
            "document.write",
            "eval(",
        ):
            with self.subTest(unsafe_api=unsafe_api):
                self.assertNotIn(unsafe_api, source)

    def test_organizer_links_are_https_and_open_safely(self) -> None:
        source = self.load_script()

        self.assertIn('protocol !== "https:"', source)
        self.assertIn('target = "_blank"', source)
        self.assertIn('rel = "noopener noreferrer"', source)

    def test_failures_and_empty_sections_have_honest_accessible_states(self) -> None:
        source = self.load_script()

        self.assertIn('setAttribute("role", "alert")', source)
        self.assertRegex(source, r"(?i)couldn.t load|could not load")
        self.assertIn("data/current.json", source)
        self.assertIn("No picks", source)
        self.assertIn("replaceChildren", source)


class StylesheetSourceTests(unittest.TestCase):
    def load_styles(self) -> str:
        self.assertTrue(STYLES.is_file(), "site/styles.css must exist")
        return STYLES.read_text(encoding="utf-8")

    def test_styles_include_mobile_layout_and_overflow_guards(self) -> None:
        source = self.load_styles()

        self.assertIn("box-sizing: border-box", source)
        self.assertRegex(source, r"(?s)html\s*\{[^}]*overflow-x:\s*hidden")
        self.assertRegex(source, r"(?s)body\s*\{[^}]*margin:\s*0")
        self.assertIn("overflow-wrap: anywhere", source)
        self.assertIn("min-width: 0", source)
        self.assertRegex(source, r"@media\s*\([^)]*max-width")
        self.assertRegex(source, r"(?s)\.event-row\s*\{[^}]*padding")

    def test_event_list_uses_dense_divider_separated_rows_not_cards(self) -> None:
        source = self.load_styles()
        list_rule = re.search(r"\.event-list\s*\{(?P<body>.*?)\n\}", source, re.DOTALL)
        row_rule = re.search(r"\.event-row\s*\{(?P<body>.*?)\n\}", source, re.DOTALL)

        self.assertIsNotNone(list_rule)
        self.assertIsNotNone(row_rule)
        self.assertIn("display: block", list_rule.group("body"))  # type: ignore[union-attr]
        row_body = row_rule.group("body")  # type: ignore[union-attr]
        self.assertIn("grid-template-columns", row_body)
        self.assertIn("border-top", row_body)
        self.assertNotIn("box-shadow", row_body)
        self.assertNotIn("background", row_body)
        self.assertRegex(
            source,
            r"(?s)@media\s*\(max-width:\s*46rem\).*?\.event-row\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)",
        )

    def test_skip_link_and_keyboard_focus_are_visibly_styled(self) -> None:
        source = self.load_styles()

        self.assertRegex(source, r"(?s)\.skip-link\s*\{[^}]*(?:position|transform)")
        self.assertRegex(source, r"(?s)\.skip-link:focus(?:-visible)?\s*\{")
        self.assertRegex(source, r"(?s):focus-visible\s*\{[^}]*outline:")
        self.assertIn("outline-offset", source)

    def test_keyboard_focus_has_contrast_on_dark_header_and_footer(self) -> None:
        source = self.load_styles()
        match = re.search(r"--focus-on-dark:\s*(#[0-9a-fA-F]{6});", source)

        self.assertIsNotNone(match)
        focus_color = match.group(1)  # type: ignore[union-attr]

        def relative_luminance(color: str) -> float:
            channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                channel / 12.92
                if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4
                for channel in channels
            ]
            return sum(weight * channel for weight, channel in zip((0.2126, 0.7152, 0.0722), linear))

        for surface in ("#183f32", "#18201c"):
            lighter, darker = sorted((relative_luminance(focus_color), relative_luminance(surface)), reverse=True)
            with self.subTest(surface=surface):
                self.assertGreaterEqual((lighter + 0.05) / (darker + 0.05), 3.0)

        self.assertRegex(
            source,
            r"(?s)\.site-header\s+:focus-visible\s*,\s*\.site-footer\s+:focus-visible\s*\{[^}]*outline-color:\s*var\(--focus-on-dark\)",
        )

    def test_normal_and_visited_links_have_explicit_colors(self) -> None:
        source = self.load_styles()

        self.assertRegex(source, r"(?s)a:link\s*\{[^}]*color:")
        self.assertRegex(source, r"(?s)a:visited\s*\{[^}]*color:")
        self.assertRegex(source, r"(?s)\.site-header\s+a:link[^}]*color:")
        self.assertRegex(source, r"(?s)\.site-header\s+a:visited[^}]*color:")
        self.assertRegex(source, r"(?s)\.site-footer\s+a:link[^}]*color:")
        self.assertRegex(source, r"(?s)\.site-footer\s+a:visited[^}]*color:")

    def test_reduced_motion_and_hidden_states_are_respected(self) -> None:
        source = self.load_styles()

        self.assertRegex(source, r"@media\s*\(prefers-reduced-motion:\s*reduce\)")
        self.assertRegex(
            source,
            r"(?s)@media\s*\(prefers-reduced-motion:\s*reduce\).*?transition",
        )
        self.assertRegex(source, r"(?s)\[hidden\]\s*\{[^}]*display:\s*none\s*!important")


if __name__ == "__main__":
    unittest.main()
