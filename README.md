# Neko Video

让麦麦读取视频资料、看抖音图文图片，并按需发送原媒体的 MaiBot 插件。

独立项目，插件ID为 **`neko.video`**。不是旧插件的同名覆盖更新，也不自动读取或迁移旧插件数据。代码来源及MIT归属见[NOTICE](NOTICE.md)和[LICENSE](LICENSE)。

## 能做什么

| 工具 | 用途 |
| --- | --- |
| `neko_video_bilibili` | B站标题、作者、简介与平台AI总结 |
| `neko_video_douyin` | 抖音资料、平台AI总结、高光及章节 |
| `neko_video_read` | 抖音图文真实图片预览；视频只取封面与媒体编号 |
| `neko_video_send` | 在QQ中按编号发送选中的原图、小视频或实况 |

群聊/私聊分享可自动附加**文字资料**，不自动发QQ、不把每条分享送给第三方媒体解析器。看图由模型按需调用读取工具，需要视觉Planner。原图发送与模型预览分开；不把封面说成已看过视频，不实现完整视频抽帧/转写。

工具用途保持短句，每项不超过45字、全部四项不超过150字，由测试约束。

## 安装

需要 **MaiBot >=1.3.9、SDK >=2.12、Python >=3.12**。下载Release的`neko-video-<version>.zip`，展开后的插件根目录应同时有`plugin.py`、`_manifest.json`和`neko_video/`，放入MaiBot的`plugins/neko-video/`，由宿主安装manifest声明依赖。

首次安装**默认关闭**，在WebUI检查配置后启用。不要同时启用旧内容理解插件和本项目的自动附加，以免重复处理；但也不要在未核实配置、凭据和功能前直接删除旧插件。

简化配置示例（不要用示例覆盖已有配置）：

```toml
[plugin]
enabled = false
config_version = "1.0.0"

[parse]
enable_ai_summary = true
enable_in_group = true
enable_in_private = true
enable_douyin = true
cache_ttl_seconds = 1800

[permission]
admin_users = [] # 空名单拒绝登录管理命令；填可信发送者ID

[media]
enabled = true # 仍受总插件开关控制
fallback_token = "" # 可改用服务器私有凭据文件

[credential]
sessdata = ""
bili_jct = ""
buvid3 = ""
dedeuserid = ""
douyin_cookie = ""
```

## 登录和权限

- 管理员命令：`/neko-video login`、`/neko-video status`、`/neko-video logout`。管理员必须在`permission.admin_users`中；不通过“最近发言人”猜身份。
- B站扫码结果保存在本插件data_dir的`credential.json`，优先于配置中的Cookie。登出只删除本插件扫码凭据；若配置中仍有Cookie，会明确提示继续清理设置。
- 抖音Cookie只用于官方资料接口。缺少ttwid时可申请平台游客标识，存于`douyin-visitor.json`；不会把账号Cookie发给第三方。
- 备选媒体Token可以放在`media.fallback_token`，或本插件data_dir下`douyin-media-secrets.json`的`fallback_token`字段，文件应为0600。源码、Release、测试和示例都不包含真实Token。

## 媒体边界

- 图片每批最多4张，`image_start`从0起继续；最多处理前64项。预览长边<=1280px、单张<=256KiB，整批原始预览<=1MiB，Base64另计。小字可能不清，不能承诺原画质。
- 原文件<=8MiB；图像<=2000万像素，动图<=100帧且累计<=4000万像素。预览取首帧，原图发送重新下载并核对SHA256，不以预览冒充原图。
- 视频/实况只做MP4容器头校验，不承诺完整解码、H.265可播或QQ兼容；超过8MiB拒绝，不自动转码/拆片。视频使用本次下载的单份Base64，不让NapCat自行访问不受控URL，也不把大视频字节保存到聊天DB或再注入模型历史。
- 媒体编号绑定聊天，10分钟过期，全局128/单聊天24项，仅存元数据。跨聊天/过期/在途/已发/投递未知的编号不自动重发。平台确认请求不等于收件人显示成功，QQ仍可能压缩/转码。
- 模型读取总期限45秒、发送55秒，含并发2槽排队；逐项失败保留成功预览。配置更新/卸载取消请求并清编号；已有普通聊天不清理。宿主16MiB聚合Hook限制没有被本插件取消。
- 公网下载验证所有DNS结果并固定IP，保留Host/TLS SNI校验，重定向逐跳重查，拒绝内网/带凭据URL/非80或443端口。第三方解析器收到公开分享链接，主接口为明文HTTP；不能接受该信任模型时关闭`media.enabled`。详情见[SECURITY](SECURITY.md)。

## 架构与开发

```text
plugin.py                  # 宿主发现入口
neko_video/
  plugin.py                # SDK组件、会话和生命周期
  config.py                # WebUI配置模型
  official.py              # B站/抖音官方资料编排
  parsers.py               # 纯响应解析
  credentials.py           # 官方登录生命周期
  storage.py               # 私有原子存储
  transport.py             # 官方端点边界与响应上限
  presentation.py          # 有界文字展示
  cache.py                 # TTL元数据缓存
  media_service.py         # 看图/原媒体投递编排
  media.py                 # 匿名解析、公网下载、图片预览
  assets.py                # 聊天隔离的媒体句柄
tests/                     # 模拟网络与合成媒体，无QQ/模型调用
```

```bash
python -m pip install -e . ruff
python -m unittest discover -s tests -t . -q
ruff check .
ruff format --check .
python scripts/build_release.py
```

测试不登录真实账号、不发QQ、不调用真实模型或供应商。通过测试不代表第三方接口永久可用，或已经在所有QQ客户端端到端验收。新项目发布不等于自动迁移任何现有部署。

License: MIT，见LICENSE及NOTICE。
