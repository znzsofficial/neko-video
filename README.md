# Neko Video

**让麦麦看懂分享，而不是只看见一条链接。**

为 MaiBot 提供 B站/抖音资料读取、抖音图文看图和按需媒体发送。模型看到的是实际图片预览，不是让它凭图片地址猜画面；已有资料直接用于聊天，不额外群发解析卡片。

[下载安装包](https://github.com/znzsofficial/neko-video/releases/latest) · [安全与隐私](SECURITY.md) · [更新记录](CHANGELOG.md)

## 使用起来

- **分享视频**：自动补充标题、作者和平台可提供的总结、章节等文字资料。没有总结就明确缺失，不冒充看过视频。
- **“看看这篇图文里的图片”**：麦麦按需读取抖音图片，每次最多4张，可以继续看后面的图片。需要支持视觉的Planner模型。
- **“把第2张原图发给我”**：按本聊天里的媒体编号发送选中的原文件，不拿压缩预览充当原图。
- **“发这个小视频/实况”**：下载并校验后再交给QQ发送，单项上限8MiB。不会自动下载整套图集或长视频。

> 视频目前只提供封面、资料和原媒体发送，**不包含完整视频抽帧理解或转写**。能看到封面不等于看过视频。模型是否调用工具、平台是否返回资料，都不保证每次成功。

### 四个短提示工具

| 工具 | 用途 |
| --- | --- |
| `neko_video_bilibili` | B站资料与平台AI总结 |
| `neko_video_douyin` | 抖音平台总结、高光与章节 |
| `neko_video_read` | 抖音图文图片、视频封面与媒体编号 |
| `neko_video_send` | 在QQ发送选中的原图、小视频或实况 |

自动处理只附加**文字资料**，不会自动发QQ或逐条调用第三方媒体接口。看图和发送由模型按需选择。工具说明保持短句：每项≤45字，全部四项≤150字，由测试约束。

## 安装与启用

要求 **MaiBot ≥1.3.9、SDK ≥2.12、Python ≥3.12**。

1. 从[Release](https://github.com/znzsofficial/neko-video/releases/latest)下载ZIP，按同页`checksums.txt`核对SHA256。
2. 解压到`plugins/neko-video/`，确保`plugin.py`、`_manifest.json`和`neko_video/`在同一插件根目录；由宿主安装manifest声明的依赖。
3. 在WebUI检查解析范围、凭据及管理员名单，然后启用。首次安装**默认关闭**，不是安装后自动开始解析。
4. 需要图文看图时，开启`parse.enable_douyin`和`media.enabled`，并确认Planner支持视觉。

插件ID为`neko.video`，不是旧内容理解插件的同名更新。不要同时启用两个插件的自动附加。项目不会自动搬运旧Cookie或删除旧数据；迁移时先备份，再将适用配置和凭据复制到新插件的独立数据目录。

## 配置

通常在WebUI修改即可。以下为简化示例，**不要直接覆盖已有配置**：

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
admin_users = [] # 填可信发送者ID；空名单禁止登录管理命令

[media]
enabled = true # 仍受总插件开关及enable_douyin控制
fallback_token = "" # 可用服务器私有凭据文件代替

[credential]
sessdata = ""
bili_jct = ""
buvid3 = ""
dedeuserid = ""
douyin_cookie = ""
```

### 登录与凭据

| 命令 | 用途 |
| --- | --- |
| `/neko-video login` | 管理员扫码登录B站 |
| `/neko-video status` | 管理员检查B站登录状态 |
| `/neko-video logout` | 管理员清除本插件扫码凭据 |

- 管理员名单为空时拒绝以上命令；不根据“最近说话的人”猜身份。名单不限制模型的普通资料工具使用。
- B站扫码凭据保存在本插件`data_dir/credential.json`，优先于配置Cookie。登出只清扫码文件；配置仍有Cookie时会明确提示继续清理。
- 抖音Cookie只用于官方接口。游客标识可存于`douyin-visitor.json`，不会把账号Cookie发给第三方解析器。
- 备选媒体接口Token放在`media.fallback_token`，或`data_dir/douyin-media-secrets.json`的`fallback_token`字段，后者应0600。**不要提交Token、Cookie或扫码链接到GitHub。**

## 清晰度、大小与投递限制

| 项目 | 限制 |
| --- | --- |
| 模型看图 | 每次≤4张；`image_start`从0起翻页；最多处理前64项 |
| 压缩预览 | 长边≤1280px、每张≤256KiB，整批原始预览≤1MiB；Base64另计 |
| 原媒体 | 单项≤8MiB，不自动转码或拆片绕过限制 |
| 图片解码 | ≤2000万像素；动图≤100帧且累计≤4000万像素，预览取首帧 |
| 媒体编号 | 仅当前聊天、10分钟有效；每聊天24项、全局128项，仅缓存元数据 |
| 请求期限 | 看图45秒、发送55秒，包含并发2槽的排队时间 |

原图发送会重新下载并核对SHA256；原文件变了就要求重读，不发送旧预览替代。小字可能在预览压缩中丢失，QQ也可能再次压缩或转码。MP4仅检查容器头，不等于完整解码或保证QQ可播。

在途、已发送或投递未确认的编号不会自动重发。平台确认请求不等于收件人已成功显示。视频二进制只用于本次投递，不保存到聊天数据库或再塞进模型历史。配置更新/卸载会取消请求并清理编号，普通聊天历史不删除；宿主16MiB聚合Hook限制仍然存在。

## 第三方接口与隐私

官方资料与第三方媒体解析使用不同客户端，后者只接收公开分享链接或视频ID，**不接收B站/抖音账号Cookie**。第三方接口不属于本项目，可能失效、限流或返回不完整内容。

当前主媒体解析接口使用**明文HTTP**，沿途可能读取或修改请求和响应；只用于公开内容。不能接受该信任模型时关闭`media.enabled`，保留官方文字资料功能。下载媒体时会校验并固定公网IP、逐跳检查重定向，拒绝内网、凭据URL及非80/443端口；这些防护不等于认证第三方返回内容。详见[SECURITY.md](SECURITY.md)。

## 开发与来源

SDK接入、配置、官方解析、凭据、存储、展示和媒体投递分别位于`neko_video/`中的独立模块；`plugin.py`仅负责宿主发现。测试使用模拟网络和合成媒体，不登录账号、不发QQ、不调用模型。

```bash
python -m pip install -e . ruff
python -m unittest discover -s tests -t . -q
ruff check .
ruff format --check .
python scripts/build_release.py
```

本项目为独立仓库，保留派生代码的作者和来源说明，不把独立建库等同于全部原创。采用MIT许可，见[LICENSE](LICENSE)与[NOTICE](NOTICE.md)。
