from __future__ import annotations

import importlib.util
import hashlib
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


qwen = load_script("generate_qwen_report")


def evidence_card(
    card_id: str,
    section: str,
    *,
    priority_ids: list[str] | None = None,
    match_terms: list[str] | None = None,
) -> dict:
    match_terms = match_terms or []
    facts = "这张证据卡记录了可核验的新进展，并明确保留必要限定和适用边界。"
    if match_terms:
        facts += " 匹配词为" + "、".join(match_terms) + "。"
    return {
        "id": card_id,
        "section": section,
        "title": f"{card_id} 对应的独立事件",
        "facts": facts,
        "publishedAt": "",
        "sources": [
            {
                "name": f"{card_id} 官方来源",
                "url": f"https://news.example-{card_id.lower()}.com/item",
            }
        ],
        "priorityIds": priority_ids or [],
        "matchTerms": match_terms,
    }


def valid_main() -> tuple[list[dict], dict]:
    counts = [4, 4, 4, 2, 2, 4]
    cards: list[dict] = []
    sections: list[dict] = []
    card_number = 1
    for section_index, (title, _minimum, _maximum) in enumerate(qwen.SECTION_POLICY):
        items = []
        for item_index in range(counts[section_index]):
            card_id = f"E{card_number:03d}"
            priority_ids = ["lab:grok-4-6"] if card_number == 1 else []
            match_terms = ["Grok", "4.6"] if card_number == 1 else []
            cards.append(
                evidence_card(
                    card_id,
                    title,
                    priority_ids=priority_ids,
                    match_terms=match_terms,
                )
            )
            headline = f"{card_id} 可核验的新进展"
            if card_number == 1:
                headline = "Grok 4.6 可核验的新进展"
            summary = "证据卡记录了可核验的新进展，并明确保留必要限定和适用边界。"
            expanded = card_number == 1
            if title in qwen.MAPPING_SECTIONS or expanded:
                summary += "对你的映射：本周先做小规模验证，再根据结果决定是否接入。"
            items.append(
                {
                    "headline": headline,
                    "summary": summary,
                    "expanded": expanded,
                    "evidenceIds": [card_id],
                }
            )
            card_number += 1
        sections.append({"title": title, "items": items})
    return cards, {
        "oneLiner": "📌 今日一句话：可靠性、创作工具与小团队工作流同时出现新信号，先验证再扩张。",
        "sections": sections,
    }


class ResponseParsingTests(unittest.TestCase):
    def test_search_result_url_without_completed_extractor_is_not_evidence(self) -> None:
        url = "https://vendor.test/release"
        response = {
            "output": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {
                        "query": "AI news",
                        "sources": [
                            {
                                "title": "Official release",
                                "url": f"{url}?utm_source=x#top",
                            }
                        ],
                    },
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"evidence": [], "url": "https://invented.test/"}',
                        }
                    ],
                },
            ]
        }
        sources = qwen.tool_source_map(response)
        cards = qwen.merge_research_cards(
            [],
            {
                "evidence": [
                    {
                        "section": qwen.SECTION_TITLES[0],
                        "title": "Only discovered by search",
                        "facts": "Search discovery alone must not establish the factual claims on this evidence card.",
                        "publishedAt": "2026-08-24",
                        "sources": [{"name": "Official", "url": url}],
                    }
                ]
            },
            sources,
        )
        self.assertEqual(cards, [])
        self.assertIn("\"evidence\"", qwen.response_output_text(response))

    def test_completed_matching_extractor_preserves_excerpt_on_evidence_card(self) -> None:
        url = "https://vendor.test/release"
        excerpt = (
            "The official release page confirms the product launch and explicitly "
            "states the current availability boundary."
        )
        model_facts = (
            "The launch is available only as a limited preview, according to the "
            "source selected for this evidence card."
        )
        response = {
            "output": [
                {
                    "type": "web_extractor_call",
                    "status": "completed",
                    "urls": [url],
                    "output": excerpt,
                }
            ]
        }
        sources = qwen.tool_source_map(response)
        cards = qwen.merge_research_cards(
            [],
            {
                "evidence": [
                    {
                        "section": qwen.SECTION_TITLES[0],
                        "title": "Extractor-backed official release",
                        "facts": model_facts,
                        "publishedAt": "2026-08-24",
                        "sources": [{"name": "Official", "url": url}],
                    }
                ]
            },
            sources,
        )
        self.assertEqual(len(cards), 1)
        self.assertIn(excerpt, json.dumps(cards[0], ensure_ascii=False))

    def test_multi_url_extractor_output_is_not_attributed_to_every_page(self) -> None:
        response = {
            "output": [
                {
                    "type": "web_extractor_call",
                    "status": "completed",
                    "urls": ["https://one.test/item", "https://two.test/item"],
                    "output": "A combined output cannot prove which page said which fact.",
                }
            ]
        }
        self.assertEqual(qwen.tool_source_map(response), {})
        self.assertEqual(qwen.completed_extractor_call_count(response), 0)

    def test_search_discovery_requires_a_successful_direct_page_fetch(self) -> None:
        url = "https://official.test/releases/new-model"
        response = {
            "output": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {
                        "sources": [{"title": "Official release", "url": url}]
                    },
                }
            ]
        }
        discovered = qwen.web_search_source_map(response)
        self.assertEqual(set(discovered), {url})
        page = {
            "url": url,
            "outputSummary": "The official page directly confirms the release and its current limits.",
            "outputSha256": "a" * 64,
        }
        with mock.patch.object(qwen, "fetch_public_page", return_value=page):
            hydrated = qwen.direct_fetch_source_map(discovered, [url], 30)
        self.assertEqual(hydrated[url]["retrievalMethod"], "direct-http")
        self.assertEqual(hydrated[url]["extractorOutputSha256"], "a" * 64)

    def test_direct_page_fetch_rejects_non_public_targets(self) -> None:
        for url in (
            "http://127.0.0.1/private",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/private",
        ):
            with self.subTest(url=url):
                with self.assertRaises(qwen.QwenReportError):
                    qwen.validate_public_source_url(url)

    def test_accepts_fenced_json_and_rejects_missing_object(self) -> None:
        self.assertEqual(
            qwen.parse_json_object('```json\n{"evidence": []}\n```'),
            {"evidence": []},
        )
        with self.assertRaises(qwen.QwenReportError):
            qwen.parse_json_object("no structured result")
        with self.assertRaises(qwen.QwenReportError):
            qwen.parse_json_object('preface {"evidence": []} trailing text')

    def test_research_parser_accepts_array_and_common_wrappers(self) -> None:
        card = {
            "section": qwen.SECTION_TITLES[0],
            "title": "一条完整证据",
            "facts": "足够完整的证据事实",
            "sources": [{"name": "官方", "url": "https://official.test/item"}],
        }
        self.assertEqual(
            qwen.parse_research_document(json.dumps([card]))["evidence"], [card]
        )
        self.assertEqual(
            qwen.parse_research_document(json.dumps({"cards": [card]}))["evidence"],
            [card],
        )

    def test_usage_cost_counts_reasoning_only_once_as_output(self) -> None:
        usage = {
            "prompt_tokens": 10_000,
            "completion_tokens": 5_000,
            "prompt_tokens_details": {"cached_tokens": 2_000},
            "completion_tokens_details": {"reasoning_tokens": 3_000},
        }
        # 8k uncached input + 2k cached input + 5k total output.
        self.assertAlmostEqual(qwen.usage_cost_cny(usage), 0.0568)
        metadata = qwen.response_metadata(
            {
                "id": "request-1",
                "model": "qwen3.7-plus-2026-05-26",
                "usage": {
                    **usage,
                    "x_tools": {"web_search": {"count": 3}},
                },
            },
            "qwen3.7-plus",
            include_web_search=True,
        )
        self.assertEqual(metadata["requestId"], "request-1")
        self.assertEqual(metadata["webSearchCount"], 3)
        self.assertAlmostEqual(metadata["webSearchCostCny"], 0.012)
        self.assertAlmostEqual(metadata["estimatedCostCny"], 0.0688)
        with self.assertRaises(qwen.QwenReportError):
            qwen.response_metadata(
                {"model": "qwen3.7-plus", "usage": usage},
                "qwen3.7-plus",
            )

    def test_research_requests_reserve_at_least_observed_cost_floor(self) -> None:
        self.assertEqual(qwen.research_request_reservation_cny("short prompt"), 0.50)
        self.assertEqual(
            qwen.main_research_minimum_reservation_cny(),
            len(qwen.SECTION_POLICY) * 0.50,
        )

    def test_numeric_grounding_normalizes_date_leading_zeroes(self) -> None:
        self.assertEqual(
            qwen.normalized_numbers("2026-08-24 与 8 月 24 日，4.60%"),
            {"2026", "8", "24", "4.6%"},
        )


class EvidenceTests(unittest.TestCase):
    def test_github_trending_parser_freezes_exact_repository_urls(self) -> None:
        parser = qwen.GitHubTrendingParser()
        parser.feed(
            '<article class="Box-row"><h2><a href="/openai/codex">'
            'openai / codex</a></h2><a href="/noise/link">noise</a></article>'
        )
        self.assertEqual(parser.repositories, {"https://github.com/openai/codex"})

    def test_research_cards_must_use_returned_tool_urls(self) -> None:
        official_url = "https://official.test/release"
        source_map = qwen.tool_source_map(
            {
                "output": [
                    {
                        "type": "web_extractor_call",
                        "status": "completed",
                        "urls": [official_url],
                        "output": (
                            "The official page confirms the release, its current "
                            "capabilities, and the stated availability limits."
                        ),
                    }
                ]
            }
        )
        document = {
            "evidence": [
                {
                    "section": qwen.SECTION_TITLES[0],
                    "title": "有效官方发布事件",
                    "facts": "官方页面明确说明了发布内容、当前能力和仍然存在的限制条件，并给出了可以逐项复核的产品细节。",
                    "publishedAt": "2026-08-24",
                    "sources": [
                        {"name": "Official", "url": official_url}
                    ],
                },
                {
                    "section": qwen.SECTION_TITLES[0],
                    "title": "模型自行编造的来源",
                    "facts": "这条内容虽然很长，但链接并没有出现在实际搜索工具返回的数据里。",
                    "publishedAt": "2026-08-24",
                    "sources": [
                        {"name": "Invented", "url": "https://invented.test/post"}
                    ],
                },
            ]
        }
        cards = qwen.merge_research_cards([], document, source_map)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["sources"][0]["url"], official_url)

    def test_artificial_analysis_sources_are_reserved_for_attachment(self) -> None:
        url = "https://artificialanalysis.ai/leaderboards/models"
        cards = qwen.merge_research_cards(
            [],
            {
                "evidence": [
                    {
                        "section": qwen.SECTION_TITLES[0],
                        "title": "榜单变化不应进入主刊",
                        "facts": "这段事实足够长，但应由确定性 attachment 管理而不是模型主刊。",
                        "publishedAt": "2026-08-24",
                        "sources": [{"name": "AA", "url": url}],
                    }
                ]
            },
            {url: {"name": "AA", "url": url}},
        )
        self.assertEqual(cards, [])

    def test_waytoagi_and_artificial_analysis_urls_are_reserved_for_attachments(self) -> None:
        reserved_urls = (
            "https://artificialanalysis.ai/methodology/intelligence-benchmarking",
            "https://www.waytoagi.com/zh/blog/news-20260824",
            "https://waytoagi.feishu.cn/wiki/AbCdEfGhIj",
        )
        for url in reserved_urls:
            with self.subTest(url=url):
                facts = (
                    "This extractor excerpt is deliberately long enough to form a card, "
                    "but the URL belongs to a deterministic attachment source."
                )
                response = {
                    "output": [
                        {
                            "type": "web_extractor_call",
                            "status": "completed",
                            "urls": [url],
                            "output": facts,
                        }
                    ]
                }
                cards = qwen.merge_research_cards(
                    [],
                    {
                        "evidence": [
                            {
                                "section": qwen.SECTION_TITLES[0],
                                "title": "Reserved deterministic attachment item",
                                "facts": facts,
                                "publishedAt": "2026-08-24",
                                "sources": [{"name": "Reserved", "url": url}],
                            }
                        ]
                    },
                    qwen.tool_source_map(response),
                )
                self.assertEqual(cards, [])

    def test_seed_card_is_immutable_when_model_reuses_its_source_url(self) -> None:
        url = "https://official.test/release"
        original_facts = "Official input confirms only that a limited preview is available."
        seed = {
            "id": "P001",
            "section": qwen.SECTION_TITLES[0],
            "title": "Trusted priority seed",
            "facts": original_facts,
            "publishedAt": "2026-08-24",
            "sources": [{"name": "Official", "url": url}],
            "priorityIds": ["vendor:preview"],
            "matchTerms": ["Preview"],
        }
        injected_facts = (
            "The model additionally claims a dramatic cost reduction and a full "
            "production launch, neither of which appears in the trusted seed."
        )
        source_map = qwen.tool_source_map(
            {
                "output": [
                    {
                        "type": "web_extractor_call",
                        "status": "completed",
                        "urls": [url],
                        "output": injected_facts,
                    }
                ]
            }
        )
        cards = qwen.merge_research_cards(
            [seed],
            {
                "evidence": [
                    {
                        "section": qwen.SECTION_TITLES[0],
                        "title": "Model-authored supplement",
                        "facts": injected_facts,
                        "publishedAt": "2026-08-24",
                        "sources": [{"name": "Official", "url": url}],
                    }
                ]
            },
            source_map,
        )
        trusted = next(card for card in cards if card["id"] == "P001")
        self.assertEqual(trusted["facts"], original_facts)
        self.assertNotIn(injected_facts, trusted["facts"])

    def test_rejects_old_future_and_missing_publication_dates(self) -> None:
        invalid_dates = ("2026-08-20", "2026-08-25", "")

        for published_at in invalid_dates:
            with self.subTest(published_at=published_at or "missing"):
                policy_by_title = {
                    title: maximum
                    for title, _minimum, maximum in qwen.SECTION_POLICY
                }

                def fake_api_post(_base_url, endpoint, _api_key, payload, _timeout):
                    self.assertEqual(endpoint, "responses")
                    match = re.search(r"本次调用只研究板块“(.+?)”", payload["input"])
                    self.assertIsNotNone(match)
                    assert match is not None
                    title = match.group(1)
                    evidence = []
                    extractor_calls = []
                    for index in range(policy_by_title[title]):
                        url = (
                            f"https://freshness-{qwen.SECTION_TITLES.index(title)}-"
                            f"{index}.example.com/item"
                        )
                        facts = (
                            "The extracted page contains a complete factual description "
                            "and an explicit limitation for this independent event."
                        )
                        evidence.append(
                            {
                                "section": title,
                                "title": f"Independent dated event {index}",
                                "facts": facts,
                                "publishedAt": published_at,
                                "sources": [{"name": "Official", "url": url}],
                            }
                        )
                        extractor_calls.append(
                            {
                                "type": "web_extractor_call",
                                "status": "completed",
                                "urls": [url],
                                "output": facts,
                            }
                        )
                    return {
                        "id": f"request-{qwen.SECTION_TITLES.index(title)}",
                        "model": "qwen3.7-plus",
                        "status": "completed",
                        "usage": {"input_tokens": 100, "output_tokens": 100},
                        "output": [
                            *extractor_calls,
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": json.dumps({"evidence": evidence}),
                                    }
                                ],
                            },
                        ],
                    }

                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    reported = root / "reported.md"
                    reported.write_text("", "utf-8")
                    arguments = SimpleNamespace(
                        date="2026-08-24",
                        generated_at="2026-08-24T02:17:00Z",
                        reported=reported,
                        artifact_dir=root / "artifacts",
                        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                        model="qwen3.7-plus",
                        research_timeout=30,
                        cost_cap_cny=3.0,
                    )
                    with mock.patch.object(qwen, "api_post", side_effect=fake_api_post):
                        with self.assertRaises(qwen.QwenReportError):
                            qwen.research(
                                arguments, "secret", [], datetime(2026, 8, 24)
                            )

    def test_github_trending_rejects_non_exact_repository_page(self) -> None:
        url = "https://github.com/example/project/issues/123"
        facts = (
            "The extracted issue discusses an AI repository, but an issue URL does "
            "not prove the repository is present on today's GitHub Trending list."
        )
        response = {
            "output": [
                {
                    "type": "web_extractor_call",
                    "status": "completed",
                    "urls": [url],
                    "output": facts,
                }
            ]
        }
        cards = qwen.merge_research_cards(
            [],
            {
                "evidence": [
                    {
                        "section": qwen.SECTION_TITLES[5],
                        "title": "Not an exact repository page",
                        "facts": facts,
                        "publishedAt": "2026-08-24",
                        "sources": [{"name": "GitHub issue", "url": url}],
                    }
                ]
            },
            qwen.tool_source_map(response),
        )
        self.assertEqual(cards, [])

    def test_research_runs_one_auditable_call_per_section(self) -> None:
        policy_by_title = {title: (minimum, maximum) for title, minimum, maximum in qwen.SECTION_POLICY}

        def fake_api_post(_base_url, endpoint, _api_key, payload, _timeout):
            if endpoint != "responses":
                raise AssertionError(f"unexpected endpoint: {endpoint}")
            match = re.search(r"本次调用只研究板块“(.+?)”", payload["input"])
            if match is None or match.group(1) not in qwen.SECTION_TITLES:
                raise AssertionError("research prompt does not identify one target section")
            title = match.group(1)
            minimum, _maximum = policy_by_title[title]
            evidence = []
            sources = []
            for index in range(minimum + 1):
                facts = "官方原文提供了足够完整的可核验事实，也明确写出了适用范围、时间边界与必要限制。"
                if title == qwen.SECTION_TITLES[5]:
                    url = f"https://github.com/example-org/ai-repo-{index}"
                    facts += " 该仓库出现在当日 GitHub Trending 列表。"
                else:
                    url = (
                        f"https://source-{qwen.SECTION_TITLES.index(title)}-"
                        f"{index}.test/item"
                    )
                sources.append({"title": "官方来源", "url": url})
                evidence.append(
                    {
                        "section": title,
                        "title": f"{title} 的独立证据条目 {chr(65 + index)}",
                        "facts": facts,
                        "publishedAt": "2026-08-24",
                        "sources": [{"name": "官方来源", "url": url}],
                    }
                )
            return {
                "id": f"request-{qwen.SECTION_TITLES.index(title)}",
                "model": "qwen3.7-plus",
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 100},
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {"sources": sources},
                    },
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps({"evidence": evidence})}
                        ],
                    },
                ],
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reported = root / "reported.md"
            reported.write_text("", "utf-8")
            arguments = SimpleNamespace(
                date="2026-08-24",
                generated_at="2026-08-24T02:17:00Z",
                reported=reported,
                artifact_dir=root / "artifacts",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen3.7-plus",
                research_timeout=30,
                cost_cap_cny=3.0,
            )
            def fake_fetch(url, _timeout):
                return {
                    "url": url,
                    "outputSummary": "2026-08-24，官方原文提供了足够完整的可核验事实，也明确写出适用范围、时间边界与必要限制。",
                    "outputSha256": hashlib.sha256(url.encode()).hexdigest(),
                }

            with mock.patch.object(qwen, "api_post", side_effect=fake_api_post):
                with mock.patch.object(
                    qwen, "fetch_public_page", side_effect=fake_fetch
                ):
                    cards, diagnostics = qwen.research(
                        arguments, "secret", [], datetime(2026, 8, 24)
                    )
        self.assertEqual(len(diagnostics["calls"]), 6)
        self.assertGreaterEqual(len(cards), sum(item[1] for item in qwen.SECTION_POLICY))
        self.assertEqual(set(diagnostics["sectionCounts"]), set(qwen.SECTION_TITLES))

    def test_search_only_research_directly_fetches_and_retries_sparse_results(self) -> None:
        title, minimum, _maximum = qwen.SECTION_POLICY[0]
        api_attempt = 0

        def fake_api_post(_base_url, endpoint, _api_key, payload, _timeout):
            nonlocal api_attempt
            self.assertEqual(endpoint, "responses")
            self.assertEqual(payload["tools"], [{"type": "web_search"}])
            api_attempt += 1
            count = minimum - 1 if api_attempt == 1 else 2
            offset = 0 if api_attempt == 1 else 10
            evidence = []
            sources = []
            for index in range(count):
                url = f"https://official-{offset + index}.test/release"
                sources.append({"title": "Official release", "url": url})
                evidence.append(
                    {
                        "section": title,
                        "title": f"第 {offset + index} 个独立发布事件",
                        "facts": "官方原文直接确认了这次发布，并且明确写出当前的可用范围、限制和后续验证条件。",
                        "publishedAt": "2026-08-24",
                        "sources": [{"name": "Official", "url": url}],
                    }
                )
            return {
                "id": f"search-request-{api_attempt}",
                "model": "qwen3.7-plus",
                "status": "completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 100,
                    "x_tools": {"web_search": {"count": 1}},
                },
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {"sources": sources},
                    },
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps({"evidence": evidence}),
                            }
                        ],
                    },
                ],
            }

        def fake_fetch(url, _timeout):
            return {
                "url": url,
                "outputSummary": "2026-08-24，官方页面直接确认这次发布以及当前可用范围，并保留了必要的限制条件。",
                "outputSha256": hashlib.sha256(url.encode()).hexdigest(),
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reported = root / "reported.md"
            reported.write_text("", "utf-8")
            arguments = SimpleNamespace(
                date="2026-08-24",
                generated_at="2026-08-24T02:17:00Z",
                reported=reported,
                artifact_dir=root / "artifacts",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen3.7-plus",
                research_timeout=30,
                cost_cap_cny=1.0,
            )
            with mock.patch.object(qwen, "SECTION_POLICY", (qwen.SECTION_POLICY[0],)):
                with mock.patch.object(qwen, "api_post", side_effect=fake_api_post):
                    with mock.patch.object(
                        qwen, "fetch_public_page", side_effect=fake_fetch
                    ):
                        cards, diagnostics = qwen.research(
                            arguments, "secret", [], datetime(2026, 8, 24)
                        )
        self.assertEqual(api_attempt, 2)
        self.assertEqual(len(cards), minimum + 1)
        self.assertEqual(diagnostics["directFetchCount"], minimum + 1)
        self.assertEqual(diagnostics["completedExtractorCallCount"], 0)


class CompilationTests(unittest.TestCase):
    @staticmethod
    def valid_factual_audit(cards: list[dict], editor_document: dict) -> dict:
        keys = qwen.audit_item_keys(editor_document)
        return {
            "draftSha256": qwen.editor_document_sha256(editor_document),
            "findings": [
                {
                    "key": key,
                    "verdict": "supported",
                    "reason": "证据直接支持。",
                    "evidenceQuotes": [
                        {
                            "evidenceId": card["id"],
                            "quote": card["facts"],
                        }
                    ],
                }
                for key, card in zip(keys, cards)
            ],
            "oneLiner": {
                "verdict": "supported",
                "reason": "只概括已核验条目。",
                "supportingItemKeys": keys[:3],
            },
        }

    def test_factual_audit_allows_two_quotes_from_one_cited_card(self) -> None:
        cards, editor_document = valid_main()
        audit = self.valid_factual_audit(cards, editor_document)
        audit["findings"][1]["evidenceQuotes"] = [
            {
                "evidenceId": cards[1]["id"],
                "quote": "这张证据卡记录了可核验的新进展",
            },
            {
                "evidenceId": cards[1]["id"],
                "quote": "并明确保留必要限定和适用边界",
            },
        ]

        qwen.validate_factual_audit(audit, editor_document, cards)

    def test_factual_audit_still_requires_every_cited_card(self) -> None:
        cards, editor_document = valid_main()
        editor_document["sections"][0]["items"][1]["evidenceIds"] = [
            cards[1]["id"],
            cards[2]["id"],
        ]
        audit = self.valid_factual_audit(cards, editor_document)

        with self.assertRaisesRegex(qwen.QwenReportError, "every cited evidence card"):
            qwen.validate_factual_audit(audit, editor_document, cards)

    def test_factual_audit_requires_one_pass_for_every_item(self) -> None:
        cards, editor_document = valid_main()
        keys = qwen.audit_item_keys(editor_document)
        audit = {
            "draftSha256": qwen.editor_document_sha256(editor_document),
            "findings": [
                {
                    "key": key,
                    "verdict": "supported",
                    "reason": "证据直接支持。",
                    "evidenceQuotes": [
                        {
                            "evidenceId": card["id"],
                            "quote": card["facts"],
                        }
                    ],
                }
                for key, card in zip(keys, cards)
            ],
            "oneLiner": {
                "verdict": "supported",
                "reason": "只概括已核验条目。",
                "supportingItemKeys": keys[:3],
            },
        }
        qwen.validate_factual_audit(audit, editor_document, cards)

        audit["findings"][3]["verdict"] = "unsupported"
        audit["findings"][3]["reason"] = "稿件把测试状态写成了全面可用。"
        with self.assertRaisesRegex(qwen.QwenReportError, "S1I4"):
            qwen.validate_factual_audit(audit, editor_document, cards)

        audit["findings"] = audit["findings"][:-1]
        with self.assertRaisesRegex(qwen.QwenReportError, "exactly once"):
            qwen.validate_factual_audit(audit, editor_document, cards)

    def test_factual_audit_does_not_trust_web_researcher_facts(self) -> None:
        card = evidence_card("W001", qwen.SECTION_TITLES[0])
        card["facts"] = "研究模型凭空声称产品已全面可用。"
        card["extractorOutputs"] = [
            {"outputSummary": "官方原文只说这是小范围邀请测试。"}
        ]
        trusted = qwen.trusted_audit_text(card)
        self.assertNotIn("全面可用", trusted)
        self.assertIn("邀请测试", trusted)

    def test_irrelevant_true_quote_cannot_back_unsupported_strong_claim(self) -> None:
        cards, editor_document = valid_main()
        card = next(item for item in cards if item["id"] == "E002")
        card["id"] = "W002"
        card["facts"] = "研究模型声称该产品已在全球无条件全面上线。"
        card["extractorOutputs"] = [
            {"outputSummary": "官方原文只确认该功能正在小范围邀请测试。"}
        ]
        item = editor_document["sections"][0]["items"][1]
        item["evidenceIds"] = ["W002"]
        item["headline"] = "该产品已在全球无条件全面上线"
        item["summary"] = (
            "稿件把邀请测试升级成了没有范围限制的既成事实，"
            "这与冻结原文的限定明显冲突。"
        )
        with self.assertRaisesRegex(qwen.QwenReportError, "claim markers"):
            qwen.compile_sections(editor_document, cards, "main")

    def test_unrelated_factual_claim_fails_topical_grounding(self) -> None:
        cards, editor_document = valid_main()
        item = editor_document["sections"][0]["items"][1]
        item["headline"] = "该公司赢得一项重要的行业大奖"
        item["summary"] = "稿件声称评审机构已经确认获奖结果，但证据卡并未记录这件事。"
        with self.assertRaisesRegex(qwen.QwenReportError, "topically grounded"):
            qwen.compile_sections(editor_document, cards, "main")

    def test_grounded_prefix_cannot_dilute_an_unrelated_suffix(self) -> None:
        cards, editor_document = valid_main()
        item = editor_document["sections"][0]["items"][1]
        item["summary"] = (
            "证据卡记录了可核验的新进展，并明确保留必要限定和适用边界，"
            "同时公司迁往海边并种植花草。"
        )
        with self.assertRaisesRegex(qwen.QwenReportError, "topically grounded"):
            qwen.compile_sections(editor_document, cards, "main")

    def test_audit_rejects_a_verbatim_but_irrelevant_quote(self) -> None:
        cards, editor_document = valid_main()
        keys = qwen.audit_item_keys(editor_document)
        cards[1]["facts"] += " 天气预报说明天将会下雨。"
        findings = []
        for index, (key, card) in enumerate(zip(keys, cards)):
            quote = "天气预报说明天将会下雨。" if index == 1 else card["facts"]
            findings.append(
                {
                    "key": key,
                    "verdict": "supported",
                    "reason": "证据直接支持。",
                    "evidenceQuotes": [
                        {"evidenceId": card["id"], "quote": quote}
                    ],
                }
            )
        audit = {
            "draftSha256": qwen.editor_document_sha256(editor_document),
            "findings": findings,
            "oneLiner": {
                "verdict": "supported",
                "reason": "只概括已核验条目。",
                "supportingItemKeys": keys[:3],
            },
        }
        with self.assertRaisesRegex(qwen.QwenReportError, "not topically grounded"):
            qwen.validate_factual_audit(audit, editor_document, cards)

    def test_factual_audit_rejects_forged_quote_and_wrong_hash(self) -> None:
        cards, editor_document = valid_main()
        keys = qwen.audit_item_keys(editor_document)
        findings = [
            {
                "key": key,
                "verdict": "supported",
                "reason": "证据支持。",
                "evidenceQuotes": [
                    {"evidenceId": card["id"], "quote": card["facts"]}
                ],
            }
            for key, card in zip(keys, cards)
        ]
        audit = {
            "draftSha256": "0" * 64,
            "findings": findings,
            "oneLiner": {
                "verdict": "supported",
                "reason": "概括已核验条目。",
                "supportingItemKeys": keys[:2],
            },
        }
        with self.assertRaisesRegex(qwen.QwenReportError, "draft hash"):
            qwen.validate_factual_audit(audit, editor_document, cards)
        audit["draftSha256"] = qwen.editor_document_sha256(editor_document)
        audit["findings"][0]["evidenceQuotes"][0]["quote"] = "这是伪造的证据摘录。"
        with self.assertRaisesRegex(qwen.QwenReportError, "not present"):
            qwen.validate_factual_audit(audit, editor_document, cards)

    def test_factual_audit_reserves_budget_before_api_call(self) -> None:
        cards, editor_document = valid_main()
        arguments = SimpleNamespace(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen3.7-plus",
            editor_timeout=30,
            cost_cap_cny=1.0,
        )
        with mock.patch.object(qwen, "api_post") as api_post:
            with self.assertRaisesRegex(qwen.QwenReportError, "reservation"):
                qwen.factual_audit(
                    arguments,
                    "secret",
                    editor_document,
                    cards,
                    spent_cost_cny=0.99,
                )
        api_post.assert_not_called()

    def test_editor_reserves_budget_before_api_call(self) -> None:
        cards, _editor_document = valid_main()
        arguments = SimpleNamespace(
            date="2026-08-24",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen3.7-plus",
            editor_timeout=30,
            cost_cap_cny=1.0,
        )
        with mock.patch.object(qwen, "api_post") as api_post:
            with self.assertRaisesRegex(qwen.QwenReportError, "editor.*reservation"):
                qwen.edit(
                    arguments,
                    "secret",
                    cards,
                    "main",
                    spent_cost_cny=0.99,
                )
        api_post.assert_not_called()

    def test_compiles_evidence_backed_main_and_copies_sources(self) -> None:
        cards, editor_document = valid_main()
        sections = qwen.compile_sections(editor_document, cards, "main")
        self.assertEqual(len(sections), 6)
        first = sections[0]["items"][0]
        self.assertEqual(first["priorityIds"], ["lab:grok-4-6"])
        self.assertTrue(first["sources"][0]["name"].endswith("（单一来源）"))
        self.assertNotIn("evidenceIds", first)

    def test_rejects_new_number_and_reused_evidence(self) -> None:
        cards, editor_document = valid_main()
        editor_document["sections"][0]["items"][1]["headline"] = "凭空新增 99 项能力"
        with self.assertRaisesRegex(qwen.QwenReportError, "unsupported numbers"):
            qwen.compile_sections(editor_document, cards, "main")

        cards, editor_document = valid_main()
        editor_document["sections"][0]["items"][1]["evidenceIds"] = ["E001"]
        with self.assertRaisesRegex(qwen.QwenReportError, "reused"):
            qwen.compile_sections(editor_document, cards, "main")

    def test_rejects_numbers_borrowed_or_obscured_by_format(self) -> None:
        cases = (
            {
                "name": "claim borrows 24 from publication date",
                "facts": "官方只确认该能力已发布，没有公布任何数量。",
                "publishedAt": "2026-08-24",
                "claim": "它现在已支持 24 个模型。",
            },
            {
                "name": "H200 is not grounded by H100",
                "facts": "官方原文只说明工作负载运行于 H100，未提到其他芯片。",
                "publishedAt": "",
                "claim": "该工作负载已全面改用 H200。",
            },
            {
                "name": "Qwen3.8 is not grounded by Qwen2.8",
                "facts": "官方页面只描述 Qwen2.8 的当前能力与限制。",
                "publishedAt": "",
                "claim": "产品已经升级到 Qwen3.8。",
            },
            {
                "name": "Chinese numerals require grounding too",
                "facts": "官方页面只宣布了新能力，并未给出任何数量。",
                "publishedAt": "",
                "claim": "它现在已支持二十四个模型。",
            },
        )
        for case in cases:
            with self.subTest(case=case["name"]):
                cards, editor_document = valid_main()
                card = next(item for item in cards if item["id"] == "E002")
                card["facts"] = case["facts"]
                card["publishedAt"] = case["publishedAt"]
                editor_document["sections"][0]["items"][1]["summary"] += case[
                    "claim"
                ]
                with self.assertRaises(qwen.QwenReportError):
                    qwen.compile_sections(editor_document, cards, "main")

    def test_main_low_budget_fails_before_any_model_or_trending_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            priority = root / "priority.json"
            builders = root / "builders.json"
            reported = root / "reported.md"
            priority.write_text('{"candidates": []}', "utf-8")
            builders.write_text('{"x": [], "podcasts": []}', "utf-8")
            reported.write_text("", "utf-8")
            arguments = SimpleNamespace(
                date="2026-08-24",
                generated_at="2026-08-24T02:17:00Z",
                priority=priority,
                builders=builders,
                reported=reported,
                artifact_dir=root / "artifacts",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen3.7-plus",
                research_timeout=30,
                editor_timeout=30,
                cost_cap_cny=1.0,
            )
            with mock.patch.dict(qwen.os.environ, {"DASHSCOPE_API_KEY": "secret"}):
                with mock.patch.object(qwen, "api_post") as api_post:
                    with mock.patch.object(
                        qwen, "fetch_github_trending_repositories"
                    ) as fetch_trending:
                        with mock.patch.object(qwen, "edit") as edit:
                            with self.assertRaisesRegex(
                                qwen.QwenReportError,
                                "main research minimum reservation",
                            ):
                                qwen.run(arguments)
            api_post.assert_not_called()
            fetch_trending.assert_not_called()
            edit.assert_not_called()

    def test_addendum_bypasses_main_research_budget_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            (artifact_dir / "2026-08-24-1.json").write_text(
                json.dumps(
                    {
                        "date": "2026-08-24 星期一",
                        "label": "第一期",
                        "generatedAt": "2026-08-24T02:17:00Z",
                        "oneLiner": "已有主刊。",
                        "sections": [],
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )
            priority = root / "priority.json"
            builders = root / "builders.json"
            reported = root / "reported.md"
            priority.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "id": "vendor:alpha-preview",
                                "required": True,
                                "title": "Alpha Preview",
                                "summary": "官方确认 Alpha Preview 开放了小范围测试。",
                                "details": "当前仍保留明确的适用范围和必要限制。",
                                "url": "https://vendor.example/release",
                                "officialSource": "Vendor",
                                "publishedAt": "2026-08-24",
                                "matchTerms": ["Alpha Preview"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )
            builders.write_text('{"x": [], "podcasts": []}', "utf-8")
            reported.write_text("", "utf-8")
            arguments = SimpleNamespace(
                date="2026-08-24",
                generated_at="2026-08-24T02:17:00Z",
                priority=priority,
                builders=builders,
                reported=reported,
                artifact_dir=artifact_dir,
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen3.7-plus",
                research_timeout=30,
                editor_timeout=30,
                cost_cap_cny=1.0,
            )
            edit = mock.Mock(side_effect=qwen.QwenReportError("addendum editor reached"))
            with mock.patch.dict(qwen.os.environ, {"DASHSCOPE_API_KEY": "secret"}):
                with mock.patch.object(qwen, "research") as research:
                    with mock.patch.object(
                        qwen, "fetch_github_trending_repositories"
                    ) as fetch_trending:
                        with mock.patch.object(qwen, "edit", edit):
                            with self.assertRaisesRegex(
                                qwen.QwenReportError, "addendum editor reached"
                            ):
                                qwen.run(arguments)
            research.assert_not_called()
            fetch_trending.assert_not_called()
            edit.assert_called_once()

    def test_seventeen_section_minimum_cards_never_reach_editor(self) -> None:
        cards: list[dict] = []
        card_number = 1
        for title, minimum, _maximum in qwen.SECTION_POLICY:
            for _index in range(minimum):
                cards.append(evidence_card(f"E{card_number:03d}", title))
                card_number += 1
        self.assertEqual(len(cards), 17)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            priority = root / "priority.json"
            builders = root / "builders.json"
            reported = root / "reported.md"
            priority.write_text('{"candidates": []}', "utf-8")
            builders.write_text('{"x": [], "podcasts": []}', "utf-8")
            reported.write_text("", "utf-8")
            arguments = SimpleNamespace(
                date="2026-08-24",
                generated_at="2026-08-24T02:17:00Z",
                priority=priority,
                builders=builders,
                reported=reported,
                artifact_dir=root / "artifacts",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen3.7-plus",
                research_timeout=30,
                editor_timeout=30,
                cost_cap_cny=3.0,
                trending_repositories={"https://github.com/example/repository"},
            )
            research_diagnostics = {
                "estimatedCostCny": 0,
                "evidenceCardCount": len(cards),
                "sectionCounts": {
                    title: minimum
                    for title, minimum, _maximum in qwen.SECTION_POLICY
                },
            }
            edit_spy = mock.Mock(
                return_value=(
                    {"oneLiner": "📌 今日一句话：证据容量不足。", "sections": []},
                    {
                        "estimatedCostCny": 0,
                        "tokenCostCny": 0,
                    },
                )
            )
            with mock.patch.dict(qwen.os.environ, {"DASHSCOPE_API_KEY": "secret"}):
                with mock.patch.object(
                    qwen,
                    "research",
                    return_value=(cards, research_diagnostics),
                ):
                    with mock.patch.object(qwen, "edit", edit_spy):
                        with self.assertRaises(qwen.QwenReportError):
                            qwen.run(arguments)
            edit_spy.assert_not_called()

    def test_addendum_rejects_expanded_items(self) -> None:
        card = evidence_card(
            "P001",
            qwen.SECTION_TITLES[0],
            priority_ids=["vendor:alpha-preview"],
            match_terms=["Alpha Preview"],
        )
        editor_document = {
            "oneLiner": "📌 补刊：Alpha Preview 是今日新增的强制候选。",
            "sections": [
                {
                    "title": qwen.SECTION_TITLES[0],
                    "items": [
                        {
                            "headline": "Alpha Preview 发布了新能力",
                            "summary": (
                                "官方材料确认该预览已经开放，同时保留了明确的适用范围。"
                                "对你的映射：本周只做小规模验证。"
                            ),
                            "expanded": True,
                            "evidenceIds": ["P001"],
                        }
                    ],
                }
            ],
        }
        with self.assertRaises(qwen.QwenReportError):
            qwen.compile_sections(editor_document, [card], "addendum")

    def test_priority_item_cannot_mix_unrelated_evidence(self) -> None:
        priority_card = evidence_card(
            "P001",
            qwen.SECTION_TITLES[0],
            priority_ids=["vendor:alpha-preview"],
            match_terms=["Alpha Preview"],
        )
        unrelated_card = evidence_card("E002", qwen.SECTION_TITLES[0])
        editor_document = {
            "oneLiner": "📌 补刊：Alpha Preview 是今日新增的强制候选。",
            "sections": [
                {
                    "title": qwen.SECTION_TITLES[0],
                    "items": [
                        {
                            "headline": "Alpha Preview 发布了新能力",
                            "summary": (
                                "官方材料确认该预览已经开放，同时保留了明确的适用范围，"
                                "并要求用户先完成风险评估再接入生产环境。"
                            ),
                            "expanded": False,
                            "evidenceIds": ["P001", "E002"],
                        }
                    ],
                }
            ],
        }
        with self.assertRaises(qwen.QwenReportError):
            qwen.compile_sections(
                editor_document, [priority_card, unrelated_card], "addendum"
            )

    def test_writes_non_fallback_artifact_and_append_only_archive(self) -> None:
        cards, editor_document = valid_main()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_dir = root / "content" / "artifacts"
            reported = root / "content" / "reported.md"
            arguments = SimpleNamespace(
                date="2026-08-24",
                generated_at="2026-08-24T02:17:00Z",
                artifact_dir=artifact_dir,
                reported=reported,
            )
            output = qwen.build_artifact(
                arguments, editor_document, cards, "main"
            )
            document = json.loads(output.read_text("utf-8"))
            self.assertNotIn("fallback", document)
            self.assertNotIn("自动恢复版", document["label"])
            self.assertIn(document["label"], reported.read_text("utf-8"))
            self.assertEqual(len(list(artifact_dir.glob("*.json"))), 1)


class WorkflowContractTests(unittest.TestCase):
    def test_qwen_is_primary_and_deterministic_builder_is_fallback(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "daily-report.yml").read_text(
            "utf-8"
        )
        self.assertIn("secrets.DASHSCOPE_API_KEY", workflow)
        self.assertIn("scripts/generate_qwen_report.py", workflow)
        self.assertIn("qwen3.7-plus", workflow)
        self.assertIn("DAILY_REPORT_COST_CAP_CNY: '3.0'", workflow)
        self.assertIn("scripts/build_fallback_report.py", workflow)
        self.assertIn("@github/copilot", workflow)
        self.assertIn("steps.qwen.outcome != 'success'", workflow)
        self.assertIn(
            "set -euo pipefail\n          {\n            timeout --signal=TERM",
            workflow,
        )
        self.assertLess(
            workflow.index("scripts/generate_qwen_report.py"),
            workflow.index("@github/copilot"),
        )
        self.assertIn("cron: '17 23 * * *'", workflow)
        self.assertIn("cron: '47 23 * * *'", workflow)
        self.assertIn("cron: '47 0 * * *'", workflow)
        self.assertIn("cron: '17 1 * * *'", workflow)
        self.assertIn("cron: '17 2 * * *'", workflow)

    def test_deadline_route_skips_models_and_has_an_independent_watchdog(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "daily-report.yml").read_text(
            "utf-8"
        )
        watchdog = (
            ROOT / ".github" / "workflows" / "daily-report-watchdog.yml"
        ).read_text("utf-8")
        route_selector = workflow.index(
            "      - name: Select deadline-aware production route"
        )
        qwen_workspace = workflow.index(
            "      - name: Prepare isolated Qwen workspace", route_selector
        )
        fallback_workspace = workflow.index(
            "      - name: Prepare isolated no-key fallback workspace", qwen_workspace
        )
        qwen_condition = workflow[route_selector:qwen_workspace + 400]
        fallback_condition = workflow[fallback_workspace:fallback_workspace + 500]
        self.assertIn("steps.route.outputs.selected == 'quality'", qwen_condition)
        self.assertIn("steps.route.outputs.selected == 'quality'", fallback_condition)
        self.assertIn('route_now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"', workflow)
        self.assertIn("daily-ai-report-deadline-watchdog", watchdog)
        self.assertNotIn("daily-ai-report-deadline-watchdog", workflow)
        self.assertIn("cancel-in-progress: true", watchdog)
        self.assertIn("09:40 Asia/Shanghai", watchdog)
        self.assertIn("gh workflow run daily-report.yml", watchdog)
        self.assertIn("-f route=deadline", watchdog)
        self.assertIn("actions: write", watchdog)
        self.assertIn("inputs.route == 'deadline'", workflow)

    def test_qwen_failure_is_not_masked_by_tee_or_later_validators(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "daily-report.yml").read_text(
            "utf-8"
        )
        start = workflow.index("      - name: Research and edit with Qwen3.7 Plus")
        end = workflow.index("      - name: Preflight Qwen candidate", start)
        qwen_step = workflow[start:end]
        self.assertIn("run: |\n          set -euo pipefail", qwen_step)
        self.assertIn("scripts/generate_qwen_report.py", qwen_step)
        self.assertIn("} 2>&1 | tee", qwen_step)

    def test_acceptance_route_is_qwen_then_no_key_then_deterministic(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "daily-report.yml").read_text(
            "utf-8"
        )
        start = workflow.index("      - name: Accept verified model output")
        end = workflow.index("      - name: Re-run deterministic quality gates", start)
        accept_step = workflow[start:end]
        qwen_branch = accept_step.index('if [ "$QWEN_OUTCOME" = "success" ]')
        no_key_branch = accept_step.index(
            'elif [ "$FALLBACK_AGENT_OUTCOME" = "success" ]'
        )
        deterministic_branch = accept_step.index(
            'python3 scripts/build_fallback_report.py', no_key_branch
        )
        self.assertLess(qwen_branch, no_key_branch)
        self.assertLess(no_key_branch, deterministic_branch)

    def test_every_scheduled_run_checks_artificial_analysis(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "daily-report.yml").read_text(
            "utf-8"
        )
        schedule = workflow.index("  schedule:")
        collector = workflow.index("node scripts/fetch_artificial_analysis.mjs")
        input_gate = workflow.index(
            "python3 scripts/validate_artificial_analysis_run.py", collector
        )
        model_decision = workflow.index(
            "      - name: Decide whether the Qwen editor is needed"
        )
        final_gate = workflow.index(
            "python3 scripts/validate_artificial_analysis_run.py", model_decision
        )
        snapshot_sync = workflow.index(
            "python3 scripts/sync_artificial_analysis_state.py", final_gate
        )
        self.assertLess(schedule, collector)
        self.assertLess(collector, input_gate)
        self.assertLess(input_gate, model_decision)
        self.assertLess(model_decision, final_gate)
        self.assertLess(final_gate, snapshot_sync)


if __name__ == "__main__":
    unittest.main()
