import sys
import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generate_deepseek_report as ds
import collect_daily_evidence as collector


class DeepSeekTests(unittest.TestCase):
    def test_peak_schedule_and_weekend(self):
        self.assertEqual(
            ds.peak_multiplier(datetime(2026, 9, 14, 1, tzinfo=timezone.utc)), 1
        )
        self.assertEqual(
            ds.peak_multiplier(datetime(2026, 9, 14, 4, tzinfo=timezone.utc)), 0.5
        )
        self.assertEqual(
            ds.peak_multiplier(datetime(2026, 9, 13, 2, tzinfo=timezone.utc)), 0.5
        )

    def test_cached_tokens_are_billed_separately(self):
        self.assertAlmostEqual(
            ds.usage_cost_usd(
                {
                    "prompt_tokens": 1000000,
                    "completion_tokens": 1000000,
                    "prompt_cache_hit_tokens": 500000,
                }
            ),
            1.353,
        )
        self.assertAlmostEqual(
            ds.usage_cost_usd(
                {
                    "prompt_tokens": 1000000,
                    "completion_tokens": 1000000,
                    "prompt_cache_hit_tokens": 500000,
                },
                0.5,
            ),
            0.6765,
        )

    def test_impossible_usage_is_rejected(self):
        for usage in (
            {},
            {
                "prompt_tokens": 10,
                "completion_tokens": 1,
                "prompt_cache_hit_tokens": 11,
            },
            {"prompt_tokens": True, "completion_tokens": 1},
        ):
            with self.assertRaises(ds.shared.QwenReportError):
                ds.usage_cost_usd(usage)

    def test_cost_cap_prevents_request(self):
        client = ds.Client("test-key", None, 0.000001)
        with mock.patch.object(client, "request") as request:
            with self.assertRaises(ds.shared.QwenReportError):
                client.call("test", "system", "user", {}, 1000)
            request.assert_not_called()

    def test_unknown_request_outcome_keeps_cost_reservation(self):
        client = ds.Client("test-key", None)
        with mock.patch.object(
            client,
            "request",
            side_effect=ds.shared.QwenRequestOutcomeUnknown("timeout"),
        ):
            with self.assertRaises(ds.shared.QwenRequestOutcomeUnknown):
                client.call("test", "system", "user", {}, 1000)
        self.assertGreater(client.spent, 0)

    def test_partial_http_response_is_sanitized_as_unknown(self):
        client = ds.Client("private-test-secret", None)
        opener = mock.MagicMock()
        opener.open.return_value.__enter__.return_value.read.side_effect = (
            ds.http.client.IncompleteRead(b"private-test-secret")
        )
        with mock.patch.object(ds.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ds.shared.QwenRequestOutcomeUnknown) as raised:
                client.request("chat/completions", {})
        self.assertNotIn("private-test-secret", str(raised.exception))

    def test_lost_response_retries_identical_payload_and_retains_reservation(self):
        client = ds.Client("test-key", None)
        response = {
            "model": ds.MODEL,
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
        }
        with (
            mock.patch.object(
                client,
                "request",
                side_effect=[ds.shared.QwenRequestOutcomeUnknown("partial"), response],
            ) as request,
            mock.patch.object(ds.time, "sleep"),
        ):
            self.assertEqual(client.call("test", "system", "user", {}, 1000), {})
        self.assertEqual(request.call_args_list[0], request.call_args_list[1])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["status"], "outcome-unknown")
        self.assertGreater(client.spent, client.calls[1]["estimatedCostUsd"])

    def test_research_cannot_invent_a_source(self):
        with self.assertRaisesRegex(ds.shared.QwenReportError, "unknown"):
            ds.source_cards(
                {
                    "selections": [
                        {"sourceId": "invented", "section": ds.shared.SECTION_TITLES[0]}
                    ]
                },
                [],
                [],
                [],
            )

    def test_discussion_does_not_become_a_product_launch(self):
        source = {"id": "S001", "dateBasis": "community-discussion"}
        with self.assertRaisesRegex(ds.shared.QwenReportError, "new release"):
            ds.source_cards(
                {
                    "selections": [
                        {"sourceId": "S001", "section": ds.shared.SECTION_TITLES[0]}
                    ]
                },
                [source],
                [],
                [],
            )

    def test_github_section_requires_trending_observation(self):
        source = {"id": "S001", "dateBasis": "publisher-page"}
        with self.assertRaisesRegex(ds.shared.QwenReportError, "Trending"):
            ds.source_cards(
                {
                    "selections": [
                        {"sourceId": "S001", "section": ds.shared.SECTION_TITLES[5]}
                    ]
                },
                [source],
                [],
                [],
            )

    def test_article_parser_omits_navigation_and_keeps_publication_date(self):
        parser = collector.ArticleParser()
        parser.feed(
            '<meta property="article:published_time" content="2026-09-14"><nav>Old story</nav><main><p>Actual article</p><script>bad()</script></main>'
        )
        self.assertEqual(parser.article_text, ["Actual article"])
        self.assertEqual(parser.dates, ["2026-09-14"])

    def test_source_url_with_credentials_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "credentials"):
            collector.fetch("https://user:pass@example.com/")

    def test_provider_errors_never_echo_the_secret(self):
        client = ds.Client("private-test-secret", None)
        error = ds.urllib.error.HTTPError(
            ds.BASE_URL, 401, "error", {}, io.BytesIO(b"private-test-secret")
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(ds.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ds.shared.QwenReportError) as raised:
                client.request("models")
        self.assertNotIn("private-test-secret", str(raised.exception))
        self.assertIn("HTTP 401", str(raised.exception))

    def test_feed_rejects_old_and_future_items(self):
        xml = b"<rss><channel><item><title>old</title><link>https://example.com/old</link><pubDate>Mon, 07 Sep 2026 01:00:00 GMT</pubDate></item><item><title>future</title><link>https://example.com/future</link><pubDate>Tue, 15 Sep 2026 01:00:00 GMT</pubDate></item></channel></rss>"
        with mock.patch.object(
            collector,
            "fetch",
            return_value=("https://example.com/rss", xml, "application/rss+xml"),
        ):
            self.assertEqual(
                collector.feed_entries(
                    "Example",
                    "https://example.com/rss",
                    datetime(2026, 9, 14, 1, tzinfo=timezone.utc),
                ),
                [],
            )

    def test_natural_chinese_translation_does_not_need_english_in_every_clause(self):
        ds.validate_translated_claim(
            "ExampleCloud 新增批处理功能",
            "ExampleCloud 支持批量处理任务，用户可以集中查看结果。",
            "ExampleCloud supports batch processing and lets users view results together.",
        )

    def test_fraction_and_percentage_point_translations_are_numeric_equivalents(self):
        self.assertEqual(ds.scalar_numbers("一半"), ds.scalar_numbers("half"))
        self.assertEqual(
            ds.scalar_numbers("1.25 个百分点"), ds.scalar_numbers("1.25 pp")
        )

    def test_translated_numbers_and_invented_entities_are_rejected(self):
        for headline in ("ExampleCloud 支持 900 个任务", "ImaginaryVendor 推出新功能"):
            with self.assertRaises(ds.shared.QwenReportError):
                ds.validate_translated_claim(
                    headline,
                    "ExampleCloud 可以批量处理任务。",
                    "ExampleCloud supports 9 tasks.",
                )

    def test_audit_override_still_rejects_forged_quotes_and_hashes(self):
        text = "ExampleCloud supports batch processing and lets users view results together."
        cards = [
            {
                "id": "S001",
                "title": "ExampleCloud",
                "facts": text,
                "publishedAt": "2026-09-14",
                "sources": [],
                "priorityIds": [],
                "matchTerms": [],
            }
        ]
        draft = {
            "oneLiner": "ExampleCloud 支持批处理。",
            "sections": [
                {
                    "title": "Test",
                    "items": [
                        {
                            "headline": "ExampleCloud 新增批处理",
                            "summary": "ExampleCloud 可以批量处理任务并查看结果。",
                            "evidenceIds": ["S001"],
                            "expanded": False,
                        }
                    ],
                }
            ],
        }
        key = ds.shared.audit_item_keys(draft)[0]
        good = {
            "draftSha256": ds.shared.editor_document_sha256(draft),
            "findings": [
                {
                    "key": key,
                    "verdict": "supported",
                    "reason": "Supported by the quoted source.",
                    "evidenceQuotes": [{"evidenceId": "S001", "quote": text}],
                }
            ],
            "oneLiner": {
                "verdict": "supported",
                "reason": "Supported by the item.",
                "supportingItemKeys": [key],
            },
        }
        ds.shared.validate_factual_audit(
            good, draft, cards, grounding_checker=ds.semantic_grounding_error
        )
        for mutation in ("hash", "quote", "verdict"):
            bad = json.loads(json.dumps(good))
            if mutation == "hash":
                bad["draftSha256"] = "0" * 64
            if mutation == "quote":
                bad["findings"][0]["evidenceQuotes"][0]["quote"] = (
                    "ExampleCloud supports a fabricated capability."
                )
            if mutation == "verdict":
                bad["findings"][0]["verdict"] = "unsupported"
            with self.assertRaises(ds.shared.QwenReportError):
                ds.shared.validate_factual_audit(
                    bad, draft, cards, grounding_checker=ds.semantic_grounding_error
                )


if __name__ == "__main__":
    unittest.main()
