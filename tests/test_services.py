import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from neko_video.cache import TTLCache
from neko_video.credentials import CredentialService
from neko_video.official import OfficialVideoService
from neko_video.parsers import basic_douyin, chapter_douyin, generation_douyin, stream_douyin
from neko_video.presentation import render
from neko_video.storage import read_json, write_json, StorageError, cookie_dict
from neko_video.transport import request_bytes, ProviderError


class PureServices(unittest.TestCase):
    def test_private_atomic_storage_and_corruption_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "credential.json"
            self.assertEqual(read_json(p), {})
            write_json(p, {"SESSDATA": "fixture-only"})
            self.assertEqual(read_json(p), {"SESSDATA": "fixture-only"})
            if os.name == "posix":
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            p.write_text("{broken", encoding="utf-8")
            with self.assertRaises(StorageError):
                write_json(p, {"SESSDATA": "replacement"})
            self.assertEqual(p.read_text(), "{broken")

    def test_cookie_formats(self):
        self.assertEqual(cookie_dict("a=1; b=x=y"), {"a": "1", "b": "x=y"})
        self.assertEqual(
            cookie_dict("#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tfixture"),
            {"SESSDATA": "fixture"},
        )
        self.assertEqual(cookie_dict("x" * 70000), {})

    def test_cache_expiry_and_incremental_eviction(self):
        now = [1]
        cache = TTLCache(2, clock=lambda: now[0])
        cache.put("a", 1, 10)
        cache.put("b", 2, 20)
        cache.put("c", 3, 30)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)
        now[0] = 40
        self.assertIsNone(cache.get("c"))

    def test_official_payload_parsers(self):
        text = "window._ROUTER_DATA = " + json.dumps(
            {
                "loaderData": {
                    "note_(id)/page": {
                        "videoInfoRes": {
                            "item_list": [
                                {
                                    "desc": "album",
                                    "author": {"nickname": "tester"},
                                    "video": {"duration": 30000},
                                }
                            ]
                        }
                    }
                }
            }
        )
        self.assertEqual(basic_douyin(text)["duration"], 30)
        chapters = chapter_douyin(
            'prefix "chapterInfo":'
            + json.dumps({"list": [{"timestamp": 12000, "desc": "part", "detail": "detail"}]})
        )
        self.assertEqual(chapters["chapters"][0]["time"], "0:12")
        payload = {
            "status_code": 0,
            "business_data": [
                {
                    "card_id": "ai_chat_message_lynx",
                    "data": {
                        "bytesync_data": [
                            json.dumps(
                                {
                                    "display": {
                                        "generation_spans": [
                                            {"text": {"content": "<b>summary</b>"}}
                                        ]
                                    }
                                }
                            )
                        ]
                    },
                }
            ],
        }
        self.assertEqual(generation_douyin(payload)["summary"], "summary")
        sse = "data: " + json.dumps(
            {
                "data": [
                    {
                        "display": {
                            "display": {"generation_spans": [{"text": {"content": "summary"}}]}
                        }
                    }
                ]
            }
        )
        self.assertEqual(stream_douyin(sse.encode()), "summary")
        self.assertIsNone(basic_douyin("not a supported page"))

    def test_presentation_is_bounded_and_does_not_invent_summary(self):
        info = {"platform": "douyin", "title": "title", "summary": "", "description": "description"}
        self.assertIn("暂无", render(info))
        info["summary"] = "x" * 20000
        self.assertLessEqual(len(render(info, injected=True)), 6000)
        self.assertLessEqual(len(render(info)), 12000)


class AsyncServices(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = SimpleNamespace(
            sessdata="", bili_jct="", buvid3="", dedeuserid="", douyin_cookie=""
        )

    async def test_official_boundary_redirect_and_size(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(302, headers={"location": "http://localhost/"})
            )
        ) as client:
            with self.assertRaises(ProviderError):
                await request_bytes(client, "GET", "https://www.douyin.com/a")
            with self.assertRaises(ProviderError):
                await request_bytes(
                    client, "GET", "https://third-party.example/a", headers={"Cookie": "fixture"}
                )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 101))
        ) as client:
            with self.assertRaises(ProviderError):
                await request_bytes(client, "GET", "https://www.douyin.com/a", limit=100)

    async def test_qr_poll_saves_only_expected_cookies(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"data": {"code": 0}},
                headers=[
                    ("set-cookie", "SESSDATA=fixture; Path=/"),
                    ("set-cookie", "unexpected=ignore; Path=/"),
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = CredentialService(self.directory.name, self.settings, client)
            service.qr_key = "fixture-key"
            self.assertIn("成功", await service.poll())
            self.assertEqual(read_json(service.credential_file), {"SESSDATA": "fixture"})
            self.settings.sessdata = "configured-fixture"
            self.assertIn("仍存在", await service.logout())
            self.assertFalse(service.credential_file.exists())

    async def test_bili_credential_cache_and_config_refresh(self):
        self.settings.sessdata = "fixture"
        credential = SimpleNamespace(check_valid=AsyncMock(return_value=True))
        async with httpx.AsyncClient() as client:
            service = CredentialService(self.directory.name, self.settings, client)
            with patch("neko_video.credentials.Credential.from_cookies", return_value=credential):
                self.assertIs(await service.bilibili(), credential)
                self.assertIs(await service.bilibili(), credential)
                credential.check_valid.assert_awaited_once()
                service.invalidate(self.settings)
                await service.bilibili()
                self.assertEqual(credential.check_valid.await_count, 2)

    async def test_official_douyin_metadata_summary_chapters(self):
        async def handler(request):
            path = request.url.path
            if "/share/video/" in path:
                body = {
                    "loaderData": {
                        "video_(id)/page": {
                            "videoInfoRes": {
                                "item_list": [
                                    {
                                        "desc": "test",
                                        "author": {"nickname": "a"},
                                        "video": {"duration": 4000},
                                    }
                                ]
                            }
                        }
                    }
                }
                return httpx.Response(200, text="window._ROUTER_DATA = " + json.dumps(body))
            if "/generation/get/" in path:
                return httpx.Response(200, json={"status_code": 0, "business_data": []})
            if "/stream/" in path:
                payload = {
                    "data": [
                        {
                            "display": {
                                "display": {
                                    "generation_spans": [{"text": {"content": "fixture summary"}}]
                                }
                            }
                        }
                    ]
                }
                return httpx.Response(200, text="data: " + json.dumps(payload) + "\n")
            return httpx.Response(
                200,
                text='"chapterInfo":' + json.dumps({"list": [{"timestamp": 0, "desc": "intro"}]}),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            settings = SimpleNamespace(
                enable_douyin=True, enable_ai_summary=True, cache_ttl_seconds=60
            )
            service = OfficialVideoService(
                settings,
                SimpleNamespace(douyin_cookie=AsyncMock(return_value="ttwid=fixture")),
                client,
            )
            result = await service.summary("7123456789012345678", "douyin")
            self.assertEqual(result["summary"], "fixture summary")
            self.assertEqual(result["chapters"][0]["desc"], "intro")
            self.assertIs(await service.summary("7123456789012345678", "douyin"), result)

    async def test_bili_summary_failure_preserves_real_metadata(self):
        video = SimpleNamespace(
            get_info=AsyncMock(
                return_value={
                    "bvid": "BV1234567890",
                    "title": "test",
                    "owner": {"name": "up"},
                    "duration": 10,
                }
            ),
            get_ai_conclusion=AsyncMock(side_effect=RuntimeError("fixture failure")),
        )
        async with httpx.AsyncClient() as client:
            settings = SimpleNamespace(
                enable_douyin=True, enable_ai_summary=True, cache_ttl_seconds=60
            )
            service = OfficialVideoService(
                settings, SimpleNamespace(bilibili=AsyncMock(return_value=object())), client
            )
            with patch("neko_video.official.Video", return_value=video):
                result = await service.summary("BV1234567890", "bilibili")
            self.assertEqual(result["title"], "test")
            self.assertEqual(result["summary"], "")
