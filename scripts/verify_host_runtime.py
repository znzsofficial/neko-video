"""Full real host loader/config/component/lifecycle QA with mocked IPC only.

Run in a fresh subprocess with MAIBOT_INSTANCE_DIR set to an isolated directory;
never point the instance at live data. No connect/handshake, model or QQ call.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock


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

        async def request(method, **kwargs):
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
            assert meta.instance.config.plugin.config_version == "1.1.0"
            assert meta.instance.config.media.video_max_mib == 0
            assert meta.instance.config.analysis.enabled is False
            assert len(registrations) == 1
            registration = registrations[0]
            tools = [
                item for item in registration["components"] if item["component_type"] == "TOOL"
            ]
            assert len(tools) == 4
            assert "llm.generate" in registration["capabilities_required"]
            assert not registration["config_error"]
            default_cfg = registration["default_config"]
            assert default_cfg["plugin"]["config_version"] == "1.1.0"
            # Regression negative: missing config_version must be a config error,
            # not an apparent successful manager-only registration.
            bad = runner._normalize_plugin_config(meta.instance, {"plugin": {"enabled": True}})
            assert bad.config_error
            report.write_text(
                json.dumps(
                    {
                        "real_host_loader_config_components_lifecycle": True,
                        "tool_count": len(tools),
                        "analysis_default_enabled": False,
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
