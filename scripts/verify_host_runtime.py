"""Full real host loader/config/component/lifecycle QA with mocked IPC only.

Run in a fresh subprocess with MAIBOT_INSTANCE_DIR set to an isolated directory;
never point the instance at live data. No connect/handshake, model or QQ call.
"""

import argparse
import asyncio
import base64
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


async def verify(host_source, report):
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="neko-video-real-host-") as directory:
        instance = Path(directory)
        os.environ["MAIBOT_INSTANCE_DIR"] = str(instance)
        os.environ["MAIBOT_LOCALE"] = "zh-CN"
        os.environ["MAIBOT_HOST_VERSION"] = "1.3.12"
        sys.path.insert(0, str(host_source))
        from src.plugin_runtime.runner.runner_main import PluginRunner, PluginActivationStatus
        from src.plugin_runtime.runner import runner_main
        from src.plugin_runtime import ENV_HOST_VERSION

        os.environ[ENV_HOST_VERSION] = "1.3.12"
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

        async def request(method, **kwargs):
            if method == "cap.call":
                payload = kwargs["payload"]
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
            assert len(tools) == 4
            assert "llm.generate" in registration["capabilities_required"]
            assert "llm.transcribe_audio" in registration["capabilities_required"]
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
            await verify_gemini_audio()
            report.write_text(
                json.dumps(
                    {
                        "real_host_loader_config_components_lifecycle": True,
                        "tool_count": len(tools),
                        "analysis_default_enabled": False,
                        "asr_default_enabled": False,
                        "transcription_capability_registered": True,
                        "synthetic_transcription_rpc_roundtrip": True,
                        "host_gemini_native_audio_part_contract_with_mocked_generate": True,
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
