"""Explicit read-only source probe: real SDK + stored message + NapCat get_msg.

Run with the host venv and --host-root /opt/maibot --message-id <exact ID>.
Only get_login_info/get_msg/get_file/get_group_file_url are allowed. No downloads,
model calls, QQ sends, host imports, plugin activation or live config writes.
The stored-message RPC boundary is supplied from read-only SQLite rows; adapter
responses come from a separate short-lived OneBot connection. This is not an
invocation inside the live Host/Runner, and the report says so explicitly.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tomllib
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp
from maibot_sdk.context import PluginContext


async def probe(root, mids):
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source))
    from neko_video.chat_media import ChatMediaService
    from neko_video.assets import MediaAssets
    from neko_video.config import Config
    from neko_video.plugin import NekoVideoPlugin

    cfg = tomllib.loads(
        (root / "plugins/maibot-team_snowluma-adapter/config.toml").read_text(encoding="utf-8")
    )["client"]
    # This probe never sends a credential to an arbitrary/non-loopback endpoint.
    server = cfg["server"]
    if server not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Probe requires the existing loopback NapCat connection")
    server = "[::1]" if server == "::1" else server
    endpoint = f"http://{server}:{int(cfg['port'])}"
    token = cfg.get("token", "")
    headers = {"Authorization": "Bearer " + token} if token else {}
    requests = []
    reports = []
    async with aiohttp.ClientSession(trust_env=False) as client:
        async with client.ws_connect(endpoint, headers=headers, max_msg_size=2 * 1024**2) as ws:

            async def action(name, params):
                if name not in {"get_login_info", "get_msg", "get_file", "get_group_file_url"}:
                    raise AssertionError("Probe forbids this action")
                echo = uuid4().hex
                await ws.send_json({"action": name, "params": params, "echo": echo})
                requests.append(name)
                async with asyncio.timeout(20):
                    while True:
                        event = await ws.receive_json()
                        if event.get("echo") != echo:
                            continue
                        if event.get("status") != "ok" or event.get("retcode", 0) != 0:
                            raise RuntimeError("OneBot metadata action failed: " + name)
                        return event["data"]

            login = await action("get_login_info", {})
            with sqlite3.connect(f"file:{root / 'data/MaiBot.db'}?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                for mid in mids:
                    rows = db.execute(
                        "SELECT message_id,session_id,platform,account_id,group_id,user_id,processed_plain_text "
                        "FROM mai_messages WHERE message_id=? LIMIT 2",
                        (mid,),
                    ).fetchall()
                    if len(rows) != 1:
                        raise ValueError("Message must have exactly one stored owner")
                    row = dict(rows[0])
                    assert str(login["user_id"]) == row["account_id"]
                    msg = {
                        k: row[k]
                        for k in [
                            "message_id",
                            "session_id",
                            "platform",
                            "account_id",
                            "processed_plain_text",
                        ]
                    }
                    msg["message_info"] = {
                        "group_info": {"group_id": row["group_id"]} if row["group_id"] else None,
                        "user_info": {"user_id": row["user_id"]},
                    }

                    async def rpc(method, plugin_id, payload):
                        assert method == "cap.call" and plugin_id == "neko.video"
                        if payload["capability"] == "message.get_by_id":
                            args = payload["args"]
                            assert (
                                args["chat_id"] == row["session_id"] and args["message_id"] == mid
                            )
                            return {"success": True, "message": msg}
                        assert payload["capability"] == "api.call"
                        args = payload["args"]
                        assert args["api_name"] == "adapter.napcat.action.call_data"
                        call = args["args"]
                        result = await action(call["action_name"], call["params"])
                        if call["action_name"] == "get_msg":
                            assert str(result["message_id"]) == mid
                            if row["group_id"]:
                                assert str(result["group_id"]) == row["group_id"]
                            else:
                                assert not result.get("group_id")
                                assert str(result["sender"]["user_id"]) == row["user_id"]
                        return {"success": True, "result": result}

                    sdk = PluginContext("neko.video", rpc_call=rpc)
                    config = Config()
                    config.plugin.enabled = True
                    config.media.enabled = True

                    class ProbePlugin(NekoVideoPlugin):
                        ctx = sdk

                    plugin = ProbePlugin()
                    plugin.set_plugin_config(config.model_dump())
                    # No on_load is invoked: no HTTP client, housekeeper or
                    # analysis DB is started by the plugin.
                    plugin.ready = True
                    plugin.media = SimpleNamespace(assets=MediaAssets())
                    plugin.chat_media = ChatMediaService(sdk, config, plugin.media.assets)
                    result = await plugin.chat_video(
                        source_message_id=mid, session_id=row["session_id"]
                    )
                    if not result.get("success"):
                        reports.append(
                            {"message_id": mid, "success": False, "reason": result.get("content")}
                        )
                        continue
                    asset = plugin.media.assets.peek(row["session_id"], result["media_id"])
                    reports.append(
                        {
                            "message_id": mid,
                            "success": True,
                            "chat_type": "group" if row["group_id"] else "private",
                            "source_host": urlsplit(asset.urls[0]).hostname,
                            "empty_content_digest": asset.digest == "",
                        }
                    )
    return {
        "source_sha256": hashlib.sha256((source / "neko_video/plugin.py").read_bytes()).hexdigest(),
        "real_sdk": True,
        "readonly_sqlite_message_boundary": True,
        "real_napcat_metadata": True,
        "live_host_runner_invoked": False,
        "results": reports,
        "metadata_actions": requests,
        "model_calls": 0,
        "qq_sends": 0,
        "video_downloads": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", type=Path, required=True)
    parser.add_argument("--message-id", action="append", required=True)
    args = parser.parse_args()
    if not 1 <= len(args.message_id) <= 3 or any(not mid.isdecimal() for mid in args.message_id):
        parser.error("Provide 1-3 exact numeric message IDs")
    report = asyncio.run(probe(args.host_root.resolve(), args.message_id))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(r["success"] for r in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
