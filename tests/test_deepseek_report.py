import sys
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import generate_deepseek_report as ds
import collect_daily_evidence as collector

class DeepSeekTests(unittest.TestCase):
    def test_peak_schedule_and_weekend(self):
        self.assertEqual(ds.peak_multiplier(datetime(2026,9,14,1,tzinfo=timezone.utc)),1)
        self.assertEqual(ds.peak_multiplier(datetime(2026,9,14,4,tzinfo=timezone.utc)),.5)
        self.assertEqual(ds.peak_multiplier(datetime(2026,9,13,2,tzinfo=timezone.utc)),.5)
    def test_cached_tokens_are_billed_separately(self):
        self.assertAlmostEqual(ds.usage_cost_usd({'prompt_tokens':1000000,'completion_tokens':1000000,'prompt_cache_hit_tokens':500000}),1.353)
        self.assertAlmostEqual(ds.usage_cost_usd({'prompt_tokens':1000000,'completion_tokens':1000000,'prompt_cache_hit_tokens':500000},.5),.6765)
    def test_impossible_usage_is_rejected(self):
        for usage in ({},{'prompt_tokens':10,'completion_tokens':1,'prompt_cache_hit_tokens':11},{'prompt_tokens':True,'completion_tokens':1}):
            with self.assertRaises(ds.shared.QwenReportError):ds.usage_cost_usd(usage)
    def test_cost_cap_prevents_request(self):
        client=ds.Client('test-key',None,.000001)
        with mock.patch.object(client,'request') as request:
            with self.assertRaises(ds.shared.QwenReportError):client.call('test','system','user',{},1000)
            request.assert_not_called()
    def test_unknown_request_outcome_keeps_cost_reservation(self):
        client=ds.Client('test-key',None)
        with mock.patch.object(client,'request',side_effect=ds.shared.QwenRequestOutcomeUnknown('timeout')):
            with self.assertRaises(ds.shared.QwenRequestOutcomeUnknown):client.call('test','system','user',{},1000)
        self.assertGreater(client.spent,0)
    def test_research_cannot_invent_a_source(self):
        with self.assertRaisesRegex(ds.shared.QwenReportError,'unknown'):
            ds.source_cards({'selections':[{'sourceId':'invented','section':ds.shared.SECTION_TITLES[0]}]},[],[],[])
    def test_discussion_does_not_become_a_product_launch(self):
        source={'id':'S001','dateBasis':'community-discussion'}
        with self.assertRaisesRegex(ds.shared.QwenReportError,'new release'):
            ds.source_cards({'selections':[{'sourceId':'S001','section':ds.shared.SECTION_TITLES[0]}]},[source],[],[])
    def test_github_section_requires_trending_observation(self):
        source={'id':'S001','dateBasis':'publisher-page'}
        with self.assertRaisesRegex(ds.shared.QwenReportError,'Trending'):
            ds.source_cards({'selections':[{'sourceId':'S001','section':ds.shared.SECTION_TITLES[5]}]},[source],[],[])
    def test_article_parser_omits_navigation_and_keeps_publication_date(self):
        parser=collector.ArticleParser()
        parser.feed('<meta property="article:published_time" content="2026-09-14"><nav>Old story</nav><main><p>Actual article</p><script>bad()</script></main>')
        self.assertEqual(parser.article_text,['Actual article'])
        self.assertEqual(parser.dates,['2026-09-14'])
    def test_source_url_with_credentials_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'credentials'):collector.fetch('https://user:pass@example.com/')

if __name__=='__main__':unittest.main()
