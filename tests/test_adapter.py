import asyncio
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from maibot_sdk.components import collect_components

from neko_video.config import Config
from neko_video.plugin import NekoVideoPlugin


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

        class Plugin(NekoVideoPlugin):
            config = Config()
            ctx = SimpleNamespace(
                paths=SimpleNamespace(data_dir=self.directory.name),
                logger=SimpleNamespace(info=lambda *a: None),
                send=SimpleNamespace(
                    image=AsyncMock(return_value=True), text=AsyncMock(return_value=True)
                ),
            )

        self.plugin = Plugin()
        await self.plugin.on_load()
        self.addAsyncCleanup(self.plugin.on_unload)

    async def test_new_install_defaults_disabled_and_admin_commands_fail_closed(self):
        self.assertFalse(self.plugin.enabled())
        self.assertFalse((await self.plugin.read(link="https://v.douyin.com/test/"))["success"])
        self.plugin.config.plugin.enabled = True
        self.assertFalse(self.plugin.admin("123", {}))
        self.assertFalse((await self.plugin.login(stream_id="chat", user_id="123"))[0])
        self.plugin.ctx.send.image.assert_not_awaited()

    async def test_components_and_description_budget(self):
        components = collect_components(self.plugin)
        tools = [x for x in components if x["type"] == "TOOL"]
        self.assertEqual(len(tools), 4)
        self.assertTrue(all(x["name"].startswith("neko_video_") for x in tools))
        descriptions = [x["metadata"].get("description", "") for x in tools]
        self.assertTrue(all(len(x) <= 45 for x in descriptions))
        self.assertLessEqual(sum(map(len, descriptions)), 150)
        sender = next(x for x in tools if x["name"] == "neko_video_send")
        self.assertEqual(sender["metadata"]["platforms"], ["qq"])
        manifest = json.loads(
            (Path(__file__).parents[1] / "_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["id"], "neko.video")

    async def test_host_style_package_discovery_entrypoint(self):
        import importlib.util
        import sys
        import types

        root = Path(__file__).parents[1]
        package = types.ModuleType("neko_video_discovery_test")
        package.__path__ = [str(root)]
        sys.modules[package.__name__] = package
        spec = importlib.util.spec_from_file_location(
            package.__name__ + ".plugin", root / "plugin.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            discovered = module.create_plugin()
            self.assertEqual(
                len([item for item in collect_components(discovered) if item["type"] == "TOOL"]), 4
            )
        finally:
            for name in list(sys.modules):
                if name.startswith(package.__name__):
                    del sys.modules[name]

    async def test_hook_copies_input_and_skips_duplicate(self):
        self.plugin.config.plugin.enabled = True
        self.plugin.official.summary = AsyncMock(
            return_value={"platform": "douyin", "title": "test", "summary": "fixture"}
        )
        message = {
            "processed_plain_text": "https://v.douyin.com/test/",
            "message_info": {},
            "raw_message": [],
        }
        original = copy.deepcopy(message)
        result = await self.plugin.inject(message=message)
        updated = result["modified_kwargs"]["message"]
        self.assertEqual(message, original)
        self.assertIn("fixture", updated["processed_plain_text"])
        self.assertEqual(await self.plugin.inject(message=updated), {"action": "continue"})

    async def test_hook_timeout_preserves_input(self):
        self.plugin.config.plugin.enabled = True
        self.plugin.official.summary = AsyncMock(side_effect=TimeoutError)
        message = {"processed_plain_text": "url", "raw_message": []}
        self.assertEqual(await self.plugin.inject(message=message), {"action": "continue"})
        self.assertEqual(message["raw_message"], [])

    async def test_config_update_cancels_media_and_replaces_services(self):
        self.plugin.config.plugin.enabled = True
        old = self.plugin.media
        old.slots = asyncio.Semaphore(0)
        task = asyncio.create_task(
            self.plugin.read(link="https://v.douyin.com/test/", stream_id="chat")
        )
        await asyncio.sleep(0)
        self.assertIn(task, old.tasks)
        await self.plugin.on_config_update("self", {}, "test")
        self.assertTrue(task.cancelled())
        self.assertTrue(old.closed)
        self.assertIsNot(self.plugin.media, old)
        self.assertFalse(self.plugin.media.assets.items)

    async def test_command_sender_is_not_guessed_from_recent_messages(self):
        self.plugin.config.permission.admin_users = ["123"]
        self.assertTrue(self.plugin.admin("qq:123", {}))
        self.assertFalse(
            self.plugin.admin("", {"message": {"message_info": {"user_info": {"user_id": "456"}}}})
        )
        self.assertFalse(self.plugin.admin("", {}))
