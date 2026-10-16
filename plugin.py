"""MaiBot plugin discovery entry point."""

try:
    from .neko_video.plugin import NekoVideoPlugin
except ImportError:
    from neko_video.plugin import NekoVideoPlugin


def create_plugin():
    return NekoVideoPlugin()
