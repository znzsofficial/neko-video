import asyncio
import base64
import io
import json
import unittest

import httpx
from PIL import Image

from neko_video.media import (
    BACKUP,
    MAX_API_BYTES,
    MAX_IMAGE_BYTES,
    MediaError,
    api_json,
    backup_result,
    download_image,
    media_url,
    preview_image,
    preview_result,
    resolve_media,
    rich_result,
    share_target,
    streams,
)


def image_bytes(size=(80, 60)):
    out = io.BytesIO()
    Image.new("RGB", size, "red").save(out, format="PNG")
    return out.getvalue()


def fixture(total=6):
    return {
        "code": 200,
        "data": {
            "aweme_detail": {
                "desc": "图文示例",
                "author": {"nickname": "author"},
                "images": [{"url_list": [f"https://image.example/{i}.png"]} for i in range(total)],
            }
        },
    }


def resolver(host, port):
    return [(None, None, None, "", ("8.8.8.8", 0))]


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def test_only_douyin_share_urls_or_ids(self):
        self.assertEqual(share_target("7123456789012345678"), ("7123456789012345678", ""))
        self.assertEqual(
            share_target("看这个 https://www.douyin.com/note/7123456789012345678。")[0],
            "7123456789012345678",
        )
        self.assertEqual(
            share_target("分享 https://v.douyin.com/abc/")[1], "https://v.douyin.com/abc/"
        )
        for text in [
            "http://127.0.0.1/",
            "https://douyin.com.evil.test/video/1",
            "https://evil.test/?link=https://douyin.com",
            "https://user:password@www.douyin.com/video/1",
            "https://www.douyin.com:8008/a",
            "",
            "1" * 10001,
        ]:
            with self.assertRaises(MediaError):
                share_target(text)

    def test_urls_reject_private_ports_credentials_and_controls(self):
        for url in [
            "http://10.0.0.1/a",
            "http://[::ffff:127.0.0.1]/a",
            "https://localhost/a",
            "http://metadata.google.internal/a",
            "https://x.local/a",
            "https://x.test:8000/a",
            "https://user@x.test/a",
            "https://x.test/a\n",
            "file:///etc/passwd",
        ]:
            self.assertFalse(media_url(url), url)
        self.assertEqual(
            media_url("https://example.com/a?sign=1&x=2"), "https://example.com/a?sign=1&x=2"
        )

    def test_rich_images_keep_order_and_live_motion(self):
        data = fixture()
        data["data"]["aweme_detail"]["images"][1]["video"] = {
            "play_addr": {"url_list": ["https://video.example/live.mp4"]}
        }
        result = rich_result(data)
        self.assertEqual(result["image_total"], 6)
        self.assertEqual(result["images"][0]["urls"], ["https://image.example/0.png"])
        self.assertEqual(result["images"][1]["video_urls"], ["https://video.example/live.mp4"])

    def test_video_compatibility_and_bad_host_preference(self):
        video = {
            "bit_rate": [
                {
                    "bit_rate": 8000,
                    "is_h265": 1,
                    "play_addr": {"url_list": ["https://good.example/hevc"]},
                },
                {"bit_rate": 9000, "play_addr": {"url_list": ["https://v26-web.example/high"]}},
                {
                    "bit_rate": 100,
                    "play_addr": {
                        "url_list": ["https://good.example/avc", "https://good.example/avc"]
                    },
                },
            ]
        }
        self.assertEqual(streams(video)[0], "https://good.example/avc")
        self.assertEqual(len(streams(video)), 3)

    def test_backup_success_code_nulls_and_order(self):
        payload = {
            "code": "0001",
            "data": {
                "desc": "text",
                "playAddr": None,
                "cover": "null",
                "pics": ["https://img.example/1", "https://img.example/2"],
            },
        }
        result = backup_result(payload)
        self.assertEqual(result["image_total"], 2)
        self.assertEqual(result["video_urls"], [])
        self.assertEqual(result["covers"], [])
        for bad in [{}, {"code": "0001", "data": {}}, {"code": "9999", "message": "sensitive"}]:
            with self.assertRaises(MediaError):
                backup_result(bad)

    async def test_primary_two_post_requests_no_cookies(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path.endswith("redirect/"):
                return httpx.Response(
                    200, json={"code": 200, "data": {"aweme_id": "7123456789012345678"}}
                )
            return httpx.Response(200, json=fixture())

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ) as client:
            result = await resolve_media(client, "https://v.douyin.com/abc/")
        self.assertEqual(result["image_total"], 6)
        self.assertEqual([r.method for r in requests], ["POST", "POST"])
        self.assertEqual(json.loads(requests[1].content)["aweme_id"], "7123456789012345678")
        self.assertTrue(all("cookie" not in r.headers for r in requests))

    async def test_backup_query_is_encoded_and_no_secret_error_output(self):
        seen = []

        def handler(request):
            seen.append(request)
            if request.url.host != "proxy.layzz.cn":
                return httpx.Response(503)
            self.assertEqual(request.url.params["token"], "test-token")
            self.assertEqual(request.url.params["link"], "https://v.douyin.com/abc/?x=1&y=2")
            return httpx.Response(
                200, json={"code": "0001", "data": {"pics": ["https://img.example/one"]}}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ) as client:
            result = await resolve_media(client, "https://v.douyin.com/abc/?x=1&y=2", "test-token")
        self.assertEqual(result["provider"], "backup")
        self.assertEqual(len(seen), 2)
        self.assertNotIn("test-token", str(result))

    async def test_failed_api_does_not_follow_redirect(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(302, headers={"location": "http://localhost/"})
            ),
            follow_redirects=False,
        ) as client:
            with self.assertRaises(MediaError):
                await api_json(client, "GET", BACKUP)

    async def test_api_json_size_cap(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=b" " * (MAX_API_BYTES + 1))
            )
        ) as client:
            with self.assertRaises(MediaError):
                await api_json(client, "GET", BACKUP)

    async def test_image_dns_pinning_host_sni_and_byte_cap(self):
        def handler(request):
            self.assertEqual(request.url.host, "8.8.8.8")
            self.assertEqual(request.headers["host"], "cdn.example")
            self.assertEqual(request.extensions["sni_hostname"], b"cdn.example")
            self.assertEqual(request.headers.get("cookie"), "")
            return httpx.Response(200, content=image_bytes())

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            data = await download_image(client, "https://cdn.example/a", resolver=resolver)
            self.assertTrue(data.startswith(b"\x89PNG"))
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=b"x" * (MAX_IMAGE_BYTES + 1))
            )
        ) as client:
            with self.assertRaises(MediaError):
                await download_image(client, "https://cdn.example/a", resolver=resolver)

    async def test_all_dns_answers_and_redirects_are_checked(self):
        def mixed(host, port):
            return resolver(host, port) + [(None, None, None, "", ("127.0.0.1", 0))]

        seen = []

        def handler(request):
            seen.append(1)
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            with self.assertRaises(MediaError):
                await download_image(client, "https://cdn.example/a", resolver=mixed)
            self.assertEqual(seen, [])
            with self.assertRaises(MediaError):
                await download_image(client, "https://cdn.example/a", resolver=resolver)
            self.assertEqual(len(seen), 1)

    def test_preview_decodes_and_caps_pixels_size(self):
        data, width, height = preview_image(image_bytes((1800, 1200)))
        self.assertEqual((width, height), (1800, 1200))
        self.assertLessEqual(len(data), 256 * 1024)
        with Image.open(io.BytesIO(data)) as im:
            self.assertLessEqual(max(im.size), 1280)
        for raw in [b"<html>not an image</html>", b"\xff\xd8fake"]:
            with self.assertRaises(MediaError):
                preview_image(raw)
        with self.assertRaises(MediaError):
            preview_image(image_bytes((5000, 5000)))

    async def test_paginated_visual_payload_and_partial_failures(self):
        async def fetch(client, url, **kwargs):
            if "/1.png" in url:
                raise MediaError("failed")
            return image_bytes()

        result = await preview_result(rich_result(fixture()), 0, None, fetch=fetch)
        self.assertEqual(len(result["content_items"]), 3)
        self.assertEqual(result["next_image_start"], 4)
        self.assertEqual(result["status"], "partial")
        self.assertEqual([x["metadata"]["image_index"] for x in result["content_items"]], [1, 3, 4])
        self.assertTrue(
            all(
                x["content_type"] == "image" and base64.b64decode(x["data"]).startswith(b"\xff\xd8")
                for x in result["content_items"]
            )
        )
        tail = await preview_result(rich_result(fixture()), 4, None, fetch=fetch)
        self.assertEqual(len(tail["content_items"]), 2)
        self.assertIsNone(tail["next_image_start"])

    async def test_video_cover_not_claimed_as_frames(self):
        result = backup_result(
            {
                "code": "0001",
                "data": {
                    "playAddr": "https://video.example/v.mp4",
                    "cover": "https://img.example/cover",
                },
            }
        )

        async def fetch(*a, **kw):
            return image_bytes()

        preview = await preview_result(result, 0, None, fetch=fetch)
        self.assertIn("未读取视频画面", preview["content"])
        self.assertIn("不是视频帧", preview["content_items"][0]["name"])

    async def test_deadline_and_cancellation(self):
        result = await preview_result(rich_result(fixture()), 0, None, deadline=0)
        self.assertEqual(result["content_items"], [])
        self.assertEqual(result["status"], "partial")

        async def cancelled(*a, **kw):
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await preview_result(rich_result(fixture()), 0, None, fetch=cancelled)


if __name__ == "__main__":
    unittest.main()
