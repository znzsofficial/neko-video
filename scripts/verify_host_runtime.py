"""Full real host loader/config/component/lifecycle QA with mocked IPC only.

Run in a fresh subprocess with MAIBOT_INSTANCE_DIR set to an isolated directory;
never point the instance at live data. No connect/handshake, model or QQ call.
"""

import argparse
import asyncio
import base64
from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


async def verify_gemini_audio():
    # Real deployed host method and google Part builders, but no HTTP client or
    # supplier invocation. This checks native audio content, not ASR quality.
    from src.llm_models.model_client import gemini_client

    generate = AsyncMock(return_value=object())
    fake = SimpleNamespace(
        api_provider=SimpleNamespace(base_url="http://127.0.0.1:25522/v1beta"),
        _resolve_model_identifier=lambda name, params: (name, None),
        _build_generation_config=lambda **kwargs: {},
        client=SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        ),
    )
    request = SimpleNamespace(
        model_info=SimpleNamespace(model_identifier="synthetic-gemini"),
        audio_base64=base64.b64encode(b"synthetic-wav-part").decode("ascii"),
        max_tokens=256,
        extra_params={},
    )
    with patch.object(
        gemini_client, "_default_normal_response_parser", return_value=("synthetic", None)
    ):
        answer = await gemini_client.GeminiClient._execute_audio_transcription_request(
            fake, request
        )
    assert answer == ("synthetic", None)
    generate.assert_awaited_once()
    arguments = generate.call_args.kwargs
    assert arguments["model"] == "synthetic-gemini"
    parts = arguments["contents"][0].parts
    assert parts[1].inline_data.mime_type == "audio/wav"
    assert parts[1].inline_data.data == b"synthetic-wav-part"


async def verify_bilibili_subtitle_tool(runner, meta):
    """Real Runner/SDK/tool/service path; only Bili I/O boundaries are synthetic."""
    from bilibili_api.exceptions import ResponseCodeException
    from src.plugin_runtime.protocol.envelope import Envelope

    official = importlib.import_module(
        type(meta.instance).__module__.rsplit(".", 1)[0] + ".official"
    )
    resource = SimpleNamespace(
        get_info=AsyncMock(
            return_value={
                "bvid": "BV1234567890",
                "cid": 123,
                "title": "合成资料",
                "videos": 1,
                "owner": {"name": "synthetic"},
                "duration": 60,
            }
        ),
        get_ai_conclusion=AsyncMock(side_effect=ResponseCodeException(-504, "synthetic-private")),
        get_subtitle=AsyncMock(
            return_value={
                "subtitles": [
                    {"lan": "zh-CN", "subtitle_url": "https://aisubtitle.hdslb.com/synthetic"}
                ]
            }
        ),
    )
    text = "这是一段足够长的合成字幕正文，用于隔离宿主工具链验证，不调用真实平台或模型，也不会发送QQ消息。"
    with (
        patch.object(official, "Video", return_value=resource),
        patch.object(official, "fetch_subtitle", AsyncMock(return_value=text)),
        patch.object(meta.instance.credentials, "bilibili", AsyncMock(return_value=object())),
    ):
        response = await runner._handle_invoke(
            Envelope(
                request_id=2,
                message_type="request",
                method="plugin.invoke_tool",
                plugin_id="neko.video",
                payload={"component_name": "neko_video_bilibili", "args": {"link": "BV1234567890"}},
            )
        )
    assert response.error is None, response.error
    result = response.payload["result"]
    assert result["success"] and text in result["content"], result
    assert "服务调用超时（code=-504）" in result["content"]
    assert "字幕摘录" in result["content"] and "未读取画面或音频" in result["content"]
    assert "[平台AI总结]" not in result["content"] and "synthetic-private" not in result["content"]
    resource.get_ai_conclusion.assert_awaited_once()
    resource.get_subtitle.assert_awaited_once()


async def verify(host_source, report):
    root = Path(__file__).resolve().parents[1]
    with ExitStack() as cleanup:
        directory = cleanup.enter_context(
            tempfile.TemporaryDirectory(prefix="neko-video-real-host-")
        )
        instance = Path(directory).resolve()
        cleanup.callback(os.chdir, Path.cwd())
        os.chdir(instance)
        os.environ["MAIBOT_INSTANCE_DIR"] = str(instance)
        os.environ["MAIBOT_LOCALE"] = "zh-CN"
        os.environ["MAIBOT_HOST_VERSION"] = "1.3.15"
        sys.path.insert(0, str(host_source))
        from src.plugin_runtime.runner.runner_main import PluginRunner, PluginActivationStatus
        from src.plugin_runtime.runner import runner_main
        from src.plugin_runtime import ENV_HOST_VERSION
        from src.plugin_runtime.protocol.envelope import Envelope
        from src.common.database.database import engine
        from src.common.logger import close_handlers

        cleanup.callback(engine.dispose)
        cleanup.callback(close_handlers)

        os.environ[ENV_HOST_VERSION] = "1.3.15"
        assert runner_main._PROJECT_ROOT == instance, "Refuse live host instance paths"
        plugins = instance / "plugins"
        target = plugins / "neko-video"
        target.mkdir(parents=True)
        for name in ["plugin.py", "__init__.py", "_manifest.json"]:
            shutil.copyfile(root / name, target / name)
        shutil.copytree(
            root / "neko_video", target / "neko_video", ignore=shutil.ignore_patterns("__pycache__")
        )
        # Keep config minimal-but-valid to exercise genuine host normalization
        # and current config_version handling, not only Pydantic model import.
        (target / "config.toml").write_text(
            '[plugin]\nenabled=true\nconfig_version="1.0.0"\n[media]\nvideo_max_mib=0\n',
            encoding="utf-8",
        )
        runner = PluginRunner(
            host_address="unix:///unused-neko-video-qa.sock",
            session_token="synthetic-token",
            plugin_dirs=[str(plugins)],
        )
        registrations = []
        transcriptions = []
        chat_calls = []
        chat_message = {
            "message_id": "123456",
            "session_id": "synthetic-group",
            "platform": "qq",
            "account_id": "789",
            "processed_plain_text": "[视频] 文件: test.mp4，大小: 12",
            "message_info": {"group_info": {"group_id": "123"}, "user_info": {"user_id": "456"}},
        }

        async def request(method, **kwargs):
            if method == "cap.call":
                payload = kwargs["payload"]
                if payload["capability"] == "message.get_by_id":
                    assert payload["args"]["chat_id"] == "synthetic-group"
                    assert payload["args"]["message_id"] == "123456"
                    chat_calls.append("message.get_by_id")
                    # Host response is still wrapped at this RPC boundary. The
                    # real Runner/SDK must unwrap it before the tool sees it.
                    return SimpleNamespace(
                        error=None, payload={"result": {"success": True, "message": chat_message}}
                    )
                if payload["capability"] == "api.call":
                    args = payload["args"]
                    assert args["api_name"] == "adapter.napcat.action.call_data"
                    assert args["args"] == {
                        "action_name": "get_msg",
                        "params": {"message_id": "123456"},
                    }
                    chat_calls.append("get_msg")
                    return SimpleNamespace(
                        error=None,
                        payload={
                            "result": {
                                "success": True,
                                "result": {
                                    "message_id": "123456",
                                    "message": [
                                        {
                                            "type": "video",
                                            "data": {"url": "https://synthetic.invalid/video.mp4"},
                                        }
                                    ],
                                },
                            }
                        },
                    )
                assert payload["capability"] == "llm.transcribe_audio"
                args = payload["args"]
                assert args["audio_base64"] == "c3ludGhldGlj" and args["task_name"] == "voice"
                assert not args.get("model") and not args.get("model_name")
                assert set(args) <= {"audio_base64", "task_name", "model", "model_name"}
                assert kwargs["timeout_ms"] == 35000
                transcriptions.append(payload)
                return SimpleNamespace(
                    error=None,
                    payload={"result": {"success": True, "text": "synthetic transcript"}},
                )
            if method not in [
                "plugin.bootstrap",
                "plugin.register_components",
                "plugin.unregister",
            ]:
                raise AssertionError("Unexpected real capability/RPC in isolated test: " + method)
            if method == "plugin.register_components":
                registrations.append(kwargs["payload"])
            return SimpleNamespace(error=None, payload={"accepted": True})

        runner._rpc_client.send_request = AsyncMock(side_effect=request)
        metas = runner._loader.discover_and_load([str(plugins)], extra_available={})
        assert not runner._loader.failed_plugins, runner._loader.failed_plugins
        assert len(metas) == 1
        meta = metas[0]
        prepared = runner._prepare_plugin_activation(meta)
        assert prepared is None and not meta.config_error, meta.config_error
        status = await runner._activate_plugin(meta)
        assert status == PluginActivationStatus.LOADED and not meta.config_error
        try:
            assert meta.instance.ready is True
            assert meta.instance.config.plugin.config_version == "1.2.0"
            assert meta.instance.config.media.video_max_mib == 0
            assert meta.instance.config.analysis.enabled is False
            assert meta.instance.config.asr.enabled is False
            assert len(registrations) == 1
            registration = registrations[0]
            tools = [
                item for item in registration["components"] if item["component_type"] == "TOOL"
            ]
            assert len(tools) == 5
            assert "llm.generate" in registration["capabilities_required"]
            assert "llm.transcribe_audio" in registration["capabilities_required"]
            assert {"message.get_by_id", "api.call"} <= set(registration["capabilities_required"])
            assert callable(meta.instance.ctx.llm.transcribe_audio)
            answer = await meta.instance._transcribe_audio("c3ludGhldGlj")
            assert answer == {"success": True, "text": "synthetic transcript"}
            assert len(transcriptions) == 1
            assert not registration["config_error"]
            default_cfg = registration["default_config"]
            assert default_cfg["plugin"]["config_version"] == "1.2.0"
            # Regression negative: missing config_version must be a config error,
            # not an apparent successful manager-only registration.
            bad = runner._normalize_plugin_config(meta.instance, {"plugin": {"enabled": True}})
            assert bad.config_error
            previous = runner._normalize_plugin_config(
                meta.instance,
                {
                    "plugin": {"enabled": True, "config_version": "1.1.0"},
                    "media": {"video_max_mib": 0},
                    "analysis": {"enabled": True},
                },
            )
            assert not previous.config_error
            assert previous.normalized_config["analysis"]["enabled"] is True
            assert previous.normalized_config["asr"]["enabled"] is False
            assert previous.normalized_config["media"]["video_max_mib"] == 0
            invocation = Envelope(
                request_id=1,
                message_type="request",
                method="plugin.invoke_tool",
                plugin_id="neko.video",
                payload={
                    "component_name": "neko_video_chat",
                    "args": {"source_message_id": "123456", "session_id": "synthetic-group"},
                },
            )
            response = await runner._handle_invoke(invocation)
            assert response.error is None, response.error
            registered = response.payload["result"]
            assert registered["success"], registered
            assert chat_calls == ["message.get_by_id", "get_msg"]
            assert (
                meta.instance.media.assets.peek("synthetic-group", registered["media_id"]).digest
                == ""
            )
            await verify_gemini_audio()
            await verify_bilibili_subtitle_tool(runner, meta)
            report.write_text(
                json.dumps(
                    {
                        "real_host_loader_config_components_lifecycle": True,
                        "tool_count": len(tools),
                        "analysis_default_enabled": False,
                        "asr_default_enabled": False,
                        "transcription_capability_registered": True,
                        "synthetic_transcription_rpc_roundtrip": True,
                        "chat_video_real_runner_sdk_rpc_roundtrip": True,
                        "host_gemini_native_audio_part_contract_with_mocked_generate": True,
                        "bilibili_subtitle_fallback_real_runner_tool_synthetic_source": True,
                        "analysis_enabled_migration_preserved_asr_off": True,
                        "existing_unlimited_send_setting_preserved": True,
                        "missing_config_version_negative_test": True,
                        "host_instance_isolated": True,
                        "model_requests": 0,
                        "qq_sends": 0,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        finally:
            await runner._invoke_plugin_on_unload(meta)
            assert meta.instance.ready is False
        print("REAL_HOST_PLUGIN_RUNTIME_QA_OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-source", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.host_source.resolve(), args.report.resolve()))


if __name__ == "__main__":
    main()
