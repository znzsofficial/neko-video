import asyncio
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from bilibili_api.exceptions import NetworkException, ResponseCodeException

from neko_video.bilibili_text import (
    MAX_SUBTITLE_BYTES,
    fetch_subtitle,
    read_failure,
    subtitle_excerpt,
    subtitle_url,
)
from neko_video.cache import TTLCache
from neko_video.config import Config
from neko_video.media import MediaError, download_chunks
from neko_video.official import OfficialVideoService
from neko_video.plugin import NekoVideoPlugin
from neko_video.presentation import render

BV = "BV1234567890"
INFO = {
    "bvid": BV,
    "cid": 123,
    "title": "合成视频",
    "owner": {"name": "合成作者"},
    "duration": 100,
    "videos": 1,
}
TEXT = "这是一段足够长的合成字幕正文，只在隔离测试中作为已有内容依据，不访问真实平台，不使用模型总结，也不发送任何消息。"
ROWS = {"subtitles": [{"lan": "zh-CN", "subtitle_url": "https://aisubtitle.hdslb.com/test"}]}


class BilibiliTextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient()
        self.addAsyncCleanup(self.client.aclose)
        self.resource = SimpleNamespace(
            get_info=AsyncMock(return_value=INFO),
            get_ai_conclusion=AsyncMock(side_effect=ResponseCodeException(-504, "SECRET")),
            get_subtitle=AsyncMock(return_value=ROWS),
        )
        self.credentials = SimpleNamespace(bilibili=AsyncMock(return_value=object()))
        self.settings = SimpleNamespace(
            enable_ai_summary=True, enable_douyin=True, cache_ttl_seconds=1800
        )
        self.service = OfficialVideoService(self.settings, self.credentials, self.client)
        for name, value in [
            ("neko_video.official.Video", self.resource),
            ("neko_video.official.fetch_subtitle", TEXT),
        ]:
            target = patch(name, return_value=value)
            mock = target.start()
            self.addCleanup(target.stop)
            if name.endswith("fetch_subtitle"):
                self.fetch = mock

    async def test_minus504_returns_labeled_subtitle_not_an_ai_summary(self):
        result = await self.service.summary(BV, "bilibili")
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["subtitle_excerpt"], TEXT)
        self.assertEqual(result["content_source"], "subtitle")
        self.assertEqual(result["summary_status"], "temporary")
        displayed = render(result)
        self.assertIn("服务调用超时（code=-504）", displayed)
        self.assertIn("字幕摘录", displayed)
        self.assertIn("未读取画面或音频", displayed)
        self.assertNotIn("[平台AI总结]", displayed)
        self.assertNotIn("SECRET", displayed)
        self.assertIs(await self.service.summary(BV, "bilibili"), result)
        self.fetch.assert_awaited_once()

    async def test_real_summary_does_not_read_subtitles(self):
        self.resource.get_ai_conclusion.side_effect = None
        self.resource.get_ai_conclusion.return_value = {"model_result": {"summary": TEXT}}
        result = await self.service.summary(BV, "bilibili")
        self.assertEqual(result["content_source"], "platform_ai")
        self.assertEqual(result["summary"], TEXT)
        self.resource.get_subtitle.assert_not_awaited()
        self.fetch.assert_not_awaited()

    async def test_no_summary_uses_subtitles_and_prefers_chinese(self):
        self.resource.get_ai_conclusion.side_effect = None
        self.resource.get_ai_conclusion.return_value = {"code": 1, "model_result": {}}
        self.resource.get_subtitle.return_value = {
            "subtitles": [
                {"lan": "en", "subtitle_url": "https://aisubtitle.hdslb.com/en"},
                ROWS["subtitles"][0],
            ]
        }
        result = await self.service.summary(BV, "bilibili")
        self.assertEqual(result["summary_status"], "not_provided")
        self.fetch.assert_awaited_once_with(ROWS["subtitles"][0]["subtitle_url"])

    async def test_auth_risk_and_rate_limit_never_try_subtitle_endpoint(self):
        errors = [ResponseCodeException(c, "SECRET") for c in (-101, -102, -403, -412, -352, -509)]
        errors += [NetworkException(c, "SECRET") for c in (401, 403, 412, 429)]
        for error in errors:
            with self.subTest(error=type(error).__name__, code=str(error)):
                self.service.cache.clear()
                self.resource.get_ai_conclusion.side_effect = error
                result = await self.service.summary(BV, "bilibili")
                self.assertEqual(result["summary_status"], "blocked")
                self.assertEqual(result["content_source"], "metadata_only")
                self.assertNotIn("SECRET", render(result))
                self.resource.get_subtitle.assert_not_awaited()
                self.fetch.assert_not_awaited()

    async def test_network_or_sdk_timeout_falls_back_without_retry(self):
        for error in [
            TimeoutError("SECRET"),
            httpx.ReadTimeout("SECRET"),
            NetworkException(503, "SECRET"),
        ]:
            with self.subTest(error=type(error).__name__):
                self.service.cache.clear()
                self.resource.get_ai_conclusion.reset_mock()
                self.resource.get_ai_conclusion.side_effect = error
                result = await self.service.summary(BV, "bilibili")
                self.assertEqual(result["content_source"], "subtitle")
                self.resource.get_ai_conclusion.assert_awaited_once()
                self.assertNotIn("SECRET", render(result))

    async def test_empty_failure_uses_short_backoff_then_can_recover(self):
        now = [100]
        self.service.cache = TTLCache(clock=lambda: now[0])
        self.resource.get_subtitle.return_value = {"subtitles": []}
        first = await self.service.summary(BV, "bilibili")
        self.assertIn("当前只有视频基本资料", render(first))
        self.assertIs(await self.service.summary(BV, "bilibili"), first)
        self.resource.get_ai_conclusion.assert_awaited_once()
        self.resource.get_ai_conclusion.side_effect = None
        self.resource.get_ai_conclusion.return_value = {"model_result": {"summary": TEXT}}
        now[0] += 31
        recovered = await self.service.summary(BV, "bilibili")
        self.assertEqual(recovered["summary"], TEXT)
        self.assertEqual(self.resource.get_ai_conclusion.await_count, 2)

    async def test_disabled_or_no_login_never_reads_subtitle_or_imports_another_account(self):
        self.settings.enable_ai_summary = False
        result = await self.service.summary(BV, "bilibili")
        self.assertEqual(result["summary_status"], "disabled")
        self.credentials.bilibili.assert_not_awaited()
        self.service.cache.clear()
        self.settings.enable_ai_summary = True
        self.credentials.bilibili.return_value = None
        result = await self.service.summary(BV, "bilibili")
        self.assertEqual(result["summary_status"], "not_logged_in")
        self.resource.get_ai_conclusion.assert_not_awaited()
        self.resource.get_subtitle.assert_not_awaited()
        self.fetch.assert_not_awaited()

    async def test_cancelled_summary_propagates_without_fallback_or_cache(self):
        self.resource.get_ai_conclusion.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.service.summary(BV, "bilibili")
        self.resource.get_subtitle.assert_not_awaited()
        self.assertEqual(self.service.cache.items, {})

    async def test_cancelled_subtitle_propagates_without_cache(self):
        self.fetch.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.service.summary(BV, "bilibili")
        self.assertEqual(self.service.cache.items, {})

    async def test_unexpected_error_is_visible_not_fabricated_as_absence(self):
        self.resource.get_ai_conclusion.side_effect = RuntimeError("SECRET")
        result = await self.service.summary(BV, "bilibili")
        self.assertIn("读取失败", render(result))
        self.assertNotIn("暂无", render(result))
        self.assertNotIn("SECRET", render(result))
        self.resource.get_subtitle.assert_not_awaited()

    async def test_multipart_does_not_use_first_part_subtitle_as_whole_video(self):
        self.resource.get_info.return_value = {**INFO, "videos": 2}
        result = await self.service.summary(BV, "bilibili")
        self.assertIn("多P视频暂不回退字幕", render(result))
        self.resource.get_subtitle.assert_not_awaited()

    async def test_subtitle_error_or_short_body_never_becomes_successful_content(self):
        for answer in ["", MediaError("SECRET")]:
            with self.subTest(answer=str(answer)):
                self.service.cache.clear()
                self.fetch.side_effect = answer if isinstance(answer, Exception) else None
                self.fetch.return_value = answer if not isinstance(answer, Exception) else ""
                result = await self.service.summary(BV, "bilibili")
                self.assertEqual(result["content_source"], "metadata_only")
                self.assertNotIn("SECRET", render(result))

    async def test_tool_and_auto_hook_keep_provenance_without_model_or_send(self):
        class Plugin(NekoVideoPlugin):
            config = Config()

        plugin = Plugin()
        plugin.ready = True
        plugin.config.plugin.enabled = True
        plugin.official = self.service
        result = await plugin.bilibili(link=BV)
        self.assertTrue(result["success"])
        self.assertIn("字幕摘录", result["content"])
        message = {"processed_plain_text": BV, "raw_message": [], "message_info": {}}
        before = copy.deepcopy(message)
        hooked = await plugin.inject(message=message)
        self.assertIn(
            "未读取画面或音频", hooked["modified_kwargs"]["message"]["processed_plain_text"]
        )
        self.assertEqual(message, before)

    def test_long_content_retains_warning_and_provenance_in_bounded_block(self):
        shown = render(
            {
                "platform": "bilibili",
                "title": "T" * 2000,
                "subtitle_excerpt": "S" * 6000,
                "summary_status": "temporary",
                "coverage": "未读取画面或音频",
                "read_notices": ["摘要服务调用超时"],
            },
            injected=True,
        )
        self.assertLessEqual(len(shown), 6000)
        self.assertIn("未读取画面或音频", shown)
        self.assertIn("摘要服务调用超时", shown)
        self.assertIn("字幕摘录", shown)


class SubtitleSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_url_allowlist_rejects_private_injection_non_https_and_lookalikes(self):
        for value in [
            "http://aisubtitle.hdslb.com/x",
            "https://127.0.0.1/x",
            "https://hdslb.com.evil.test/x",
            "https://user:secret@hdslb.com/x",
            "https://hdslb.com:8443/x",
            "https://hdslb.com/x#private",
            "file:///etc/passwd",
            "https://hdslb.com/x\n",
            None,
        ]:
            with self.subTest(value=value), self.assertRaises(MediaError):
                subtitle_url(value)
        self.assertEqual(subtitle_url("//aisubtitle.hdslb.com/x"), "https://aisubtitle.hdslb.com/x")

    def test_sampling_covers_start_middle_end_and_rejects_invalid_or_short_rows(self):
        payload = {
            "body": [{"from": i, "to": i + 1, "content": f"段落{i}:" + TEXT} for i in range(101)]
        }
        sampled = subtitle_excerpt(payload)
        self.assertIn("段落0:", sampled)
        self.assertIn("段落100:", sampled)
        self.assertTrue("段落48:" in sampled or "段落52:" in sampled)
        self.assertLessEqual(len(sampled), 5500)
        self.assertEqual(subtitle_excerpt({"body": [{"from": 0, "to": 1, "content": "太短"}]}), "")
        self.assertEqual(
            subtitle_excerpt({"body": [{"from": float("nan"), "to": 1, "content": TEXT}]}), ""
        )
        with self.assertRaises(MediaError):
            subtitle_excerpt({"body": "invalid"})

    async def test_public_dns_is_pinned_and_client_is_anonymous_redirects_disabled(self):
        requests, options = [], []
        original_client = httpx.AsyncClient

        async def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"body": [{"from": 0, "to": 1, "content": TEXT}]})

        def factory(**kwargs):
            options.append(kwargs)
            return original_client(transport=httpx.MockTransport(handler), **kwargs)

        def chunks(client, url, **kwargs):
            return download_chunks(
                client,
                url,
                resolver=lambda *_: [(None, None, None, None, ("1.1.1.1", 0))],
                **kwargs,
            )

        with (
            patch("neko_video.bilibili_text.httpx.AsyncClient", side_effect=factory),
            patch("neko_video.bilibili_text.download_chunks", side_effect=chunks),
            patch(
                "neko_video.media.socket.getaddrinfo",
                return_value=[(None, None, None, None, ("192.168.1.2", 0))],
            ),
        ):
            result = await fetch_subtitle("//aisubtitle.hdslb.com/x?sign=fixture")
        self.assertIn(TEXT, result)
        self.assertEqual(len(requests), 1)
        self.assertFalse(options[0]["follow_redirects"])
        self.assertFalse(options[0]["trust_env"])
        self.assertEqual(requests[0].url.host, "1.1.1.1")
        self.assertEqual(requests[0].headers["host"], "aisubtitle.hdslb.com")
        self.assertEqual(requests[0].headers.get("cookie"), "")
        self.assertNotIn("authorization", requests[0].headers)
        self.assertEqual(requests[0].extensions["sni_hostname"], b"aisubtitle.hdslb.com")

    async def test_subtitle_redirect_is_rejected_not_followed(self):
        requests = []
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (
                    requests.append(request)
                    or httpx.Response(302, headers={"location": "https://evil.test/"})
                )
            )
        ) as client:
            with patch("neko_video.media.socket.getaddrinfo", return_value=[]):
                with self.assertRaises(MediaError):
                    async for _ in download_chunks(
                        client,
                        "https://aisubtitle.hdslb.com/x",
                        resolver=lambda *_: [(None, None, None, None, ("1.1.1.1", 0))],
                        allow_redirects=False,
                    ):
                        self.fail("must not consume redirect")
        self.assertEqual(len(requests), 1)

    async def test_subtitle_download_rejects_oversize_and_closes_stream(self):
        closed = []

        async def oversized(*_, **__):
            try:
                yield b"x" * (MAX_SUBTITLE_BYTES + 1)
            finally:
                closed.append(True)

        with patch("neko_video.bilibili_text.download_chunks", side_effect=oversized):
            with self.assertRaises(MediaError):
                await fetch_subtitle("https://aisubtitle.hdslb.com/x")
        self.assertEqual(closed, [True])

    def test_raw_provider_errors_never_escape_in_diagnostics(self):
        for error in [
            ResponseCodeException(-504, "SECRET"),
            NetworkException(403, "SECRET"),
            httpx.ReadTimeout("SECRET"),
            ValueError("SECRET"),
        ]:
            self.assertNotIn("SECRET", read_failure(error, "读取")[1])
