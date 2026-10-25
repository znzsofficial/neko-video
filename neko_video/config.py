"""SDK configuration only. Domain services do not import this module."""

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    enabled: bool = Field(default=False, description="启用Neko Video；首次安装默认关闭")
    config_version: str = Field(default="1.2.0")


class ParseSection(PluginConfigBase):
    enable_ai_summary: bool = Field(
        default=True, description="读取平台AI总结；B站临时不可用时回退字幕，不保证所有视频可用"
    )
    enable_in_group: bool = Field(default=True, description="群聊分享自动附加文字资料")
    enable_in_private: bool = Field(default=True, description="私聊分享自动附加文字资料")
    cache_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    enable_douyin: bool = Field(default=True)


class Credentials(PluginConfigBase):
    sessdata: str = Field(default="")
    bili_jct: str = Field(default="")
    buvid3: str = Field(default="")
    dedeuserid: str = Field(default="")
    douyin_cookie: str = Field(default="")


class Permissions(PluginConfigBase):
    admin_users: list[str] = Field(
        default_factory=list, description="扫码/状态/登出管理员；空名单禁用这些命令"
    )


class MediaSection(PluginConfigBase):
    """视频采用本地文件引用，QQ客户端必须与插件共享同一文件系统。"""

    __ui_label__ = "看图与发送媒体"
    enabled: bool = Field(default=True, description="模型按需看图及选发媒体；不自动向QQ发送")
    fallback_token: str = Field(default="", description="第三方备选Token，仅留服务器")
    video_max_mib: int = Field(
        default=256,
        ge=0,
        description="单个视频下载上限，单位MiB；0不设单文件上限。仍保留1GiB缓存、磁盘余量和超时保护，QQ也可能拒绝过大视频。",
        json_schema_extra={
            "label": "视频大小上限（MiB，0为不限）",
            "hint": "0不设单文件上限；缓存总计1GiB、磁盘余量和QQ自身限制仍然有效。",
        },
    )


class AnalysisSection(PluginConfigBase):
    """Explicit, separately bounded visual understanding. No auto-analysis."""

    enabled: bool = Field(
        default=False, description="按需分析本聊天视频媒体编号；会调用既有vlm，默认关闭"
    )
    max_mib: int = Field(
        default=64, ge=1, le=64, description="分析下载上限MiB，独立于发送视频大小设置"
    )
    max_seconds: int = Field(
        default=120, ge=1, le=120, description="分析视频最大时长秒数，超限不截取冒充全文"
    )
    frames: int = Field(
        default=6, ge=1, le=6, description="均匀采样最多帧数，仅离散采样，不等于逐秒观看"
    )
    timeout_seconds: int = Field(
        default=90, ge=10, le=120, description="整轮期限含排队、下载、抽帧及视觉调用"
    )
    model_timeout_seconds: int = Field(
        default=35, ge=5, le=60, description="单次视觉调用等待上限，超时结果未知不重试"
    )


class ASRSection(PluginConfigBase):
    """Opt-in audio transcription through the host's existing voice task."""

    enabled: bool = Field(
        default=False, description="允许显式视频音轨转写；另有一次voice调用，默认关闭"
    )
    timeout_seconds: int = Field(
        default=35, ge=5, le=60, description="音轨转写等待上限，超时未知不重试；仍受分析总期限限制"
    )


class Config(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    parse: ParseSection = Field(default_factory=ParseSection)
    credential: Credentials = Field(default_factory=Credentials)
    permission: Permissions = Field(default_factory=Permissions)
    media: MediaSection = Field(default_factory=MediaSection)
    analysis: AnalysisSection = Field(default_factory=AnalysisSection)
    asr: ASRSection = Field(default_factory=ASRSection)
