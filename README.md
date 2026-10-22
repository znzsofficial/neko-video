# Neko Video

**让麦麦看懂分享，而不是只看见一条链接。**

为 MaiBot 提供 B站/抖音资料读取、抖音图文看图、显式视频采样理解、可选音轨转写和按需媒体发送。当前源码1.3.1修复配置变动绕过未知请求防重和分享跳转正文缓冲；保留1.3.0的ASR独立开关，视频分析和ASR均默认关闭。发布页版本和实际部署另行核实，不因源码更新自动开启付费调用。

[下载安装包](https://github.com/znzsofficial/neko-video/releases/latest) · [安全与隐私](SECURITY.md) · [更新记录](CHANGELOG.md)

## 使用起来

- **分享视频**：自动补充标题、作者和平台可提供的总结、章节等文字资料。没有总结就明确缺失，不冒充看过视频。
- **“看看这篇图文里的图片”**：麦麦按需读取抖音图片，每次最多4张，可以继续看后面的图片。需要支持视觉的Planner模型。
- **“把第2张原图发给我”**：按本聊天里的媒体编号发送选中的原文件，不拿压缩预览充当原图。
- **“发这个视频/实况”**：流式下载、校验容器后交给本机QQ上传，不再把整个视频塞进RPC，因此没有旧8MiB门槛。不会自动下载整套图集。

> 普通读取仍只提供封面/资料。只有管理员开启视频分析、且模型显式使用已有视频编号时才抽帧分析；最多6帧，不是逐秒观看。音轨还需独立开启ASR并显式include_audio=true，原调用仍不听音频。聊天直接发送的视频/.mp4附件尚未接入，不接受任意URL或服务器路径。

### 四个短提示工具

| 工具 | 用途 |
| --- | --- |
| `neko_video_bilibili` | B站资料与平台AI总结 |
| `neko_video_douyin` | 抖音平台总结、高光与章节 |
| `neko_video_read` | 默认图文/封面及编号；显式视频采样分析 |
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
config_version = "1.2.0"

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
video_max_mib = 256 # 单视频上限；0为不限，仍受磁盘/缓存/超时及QQ限制

[analysis]
enabled = false # 独立付费分析开关，默认关闭
max_mib = 64 # 分析独立上限，不受发送video_max_mib=0影响，最大64
max_seconds = 120 # 超长明确拒绝，不截前半段冒充完整理解
frames = 6 # 最多6个近似时间点，极短视频更少
timeout_seconds = 90 # 总期限含排队/下载/解码/模型等待
model_timeout_seconds = 35 # 模型等待，不自动重复请求

[asr]
enabled = false # 另需include_audio=true；复用宿主voice任务，不新增或复制Key
timeout_seconds = 35 # ASR等待上限；仍在analysis.timeout_seconds整轮期限内

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

## 显式视频理解（1.2.0起，1.3.0可选ASR）

先普通`neko_video_read(link="抖音分享链接")`取得**视频/实况**media_id，再：

```text
neko_video_read(analyze_video=true, media_id="本聊天视频编号")
```

分析时不填link或image_start，不把封面图片编号当视频。正文较长时返回next_analysis_offset，保持analyze_video=true/media_id不变并将其填入analysis_offset续读；续读只取已成功缓存，没有缓存不会开始新的模型调用。此调用不发送QQ，不消耗或重置该编号的发送状态。编号仍按聊天隔离/10分钟过期，已经发送或投递未知的编号也不因此被重发。图文预览、自动资料附加、原媒体发送的默认行为和原Cookie/Token/供应商不变。

- 每次仅使用编号中第一个已解析资源地址，不自动换URL/供应商。沿用公网DNS固定/逐跳地址校验、下载字节/磁盘余量/缓存上限；分析文件在独立`analysis/video-spool`，完成后删除，异常残留仍受原spool额度及TTL约束。
- ffprobe检查有限时长/流数量/尺寸/≤1678万原始像素；仅MP4/MOV输入。ffmpeg均匀离散采样，长边≤960，每帧≤180KiB、总计≤1080KiB；近似seek位置不是精确帧PTS，不能覆盖快速动作、全部字幕或未采样剧情。只用私有受控文件、参数数组、file/pipe协议；Linux子进程1GiB地址空间/CPU20秒/64文件描述符，父进程输出和时间另有限制，取消杀进程。**不是OS文件系统沙箱**，不支持模型给本地路径或任意网络视频。
- 最多4个在途/排队请求、单分析槽、排队也计总deadline。结果缓存按视频SHA256＋聊天＋分析/音频选项隔离；未知请求防重只按聊天＋视频SHA256，不受超时、采样或音频选项变更影响，且优先于旧成功缓存。同编号同配置的成功结果直接读缓存，换编号相同字节与选项也不再请求模型，跨聊天不共享内容。当前宿主模型参数变更不主动清成功缓存，回执明确`cache_hit`。
- 不要求音频时最多**1次SDK llm.generate调用**，既有vlm任务、2500输出tokens，不传整段聊天/人设。显式ASR最多额外1次宿主llm.transcribe_audio，见下节。宿主自身模型切换/供应商重试策略未被插件改写，因此不是“上游绝对仅1次”的保证；取消RPC也不保证上游撤销或退费。
- 模型调用前SQLite写入unknown准入，失败/超时/取消/重载后均不自动重放；未知状态及成功缓存按尝试创建时间保留1小时，最多128条。队满/损坏DB明确失败且不删库；成功结果单条≤32KiB、完整模型正文≤24000个UTF-8字节，单次最多输出6000字符并提供缓存续读，不悄悄丢尾部。目录0700/记录0600，DB最多2048页，记录仅聊天/内容/选项摘要key及分析结果，不保存原URL/视频。
- 1.3.1保留原四列`analysis`表，以独立`analysis_identity`表记录防重身份，两表同事务准入。过渡五列表会在单事务中复制全部原记录和身份、恢复四列；任何失败完整回滚，映射冲突/额外索引或触发器拒绝自动迁移，不删库。旧源码四值INSERT仍兼容，但**不具备新版防重或时钟保护语义**；正式安全回滚必须使用兼容修补构建，不能直接回退旧源码作为安全方案。
- 正常时钟下，旧版未知key无法反解聊天/内容，保守阻止所有分析（包括成功缓存读取），直到该旧尝试原有的一小时期限届满；新版未知记录仅阻止相同聊天/内容。改配置或重载不会刷新期限。旧源码新增的无映射记录亦按旧未知处理。
- 所有缓存读取/准入/完成前校验`created`：NULL（包括SQLite存入NaN）、非数值、非有限数、负值或超过当前墙钟5秒的未来值均拒绝；不静默过期、不清异常证据。进程内墙钟相对单调钟累计偏移超过5秒（包括前跳3601秒）或重载后墙钟低于上次持久观测超过5秒，持久记录故障并停止所有分析/缓存读取，重启及调回时钟不自动解除。需管理员停用分析、保留数据库副本并离线核查时间与记录，使用经审查的修复流程恢复；不得删库绕过防重。
- **跨重启时钟前跳、停机期间跳变及未观测过的时钟变化不保证可识别**：新进程没有旧单调钟基线，无法区分真实停机超过一小时与墙钟前跳；此时仍按可信墙钟计算一小时期限，存在提前再准入边界。依赖稳定系统时钟；不能接受此边界时保持分析关闭，不能把TTL视为绝对计费幂等保证。
- 上述1小时是逻辑有效期，过期记录的物理清理在后续准入时执行，无新请求时可能延后，不承诺即时擦除磁盘数据。同编号缓存命中代表复用先前已核验字节的分析，不会重新下载来保证远端此刻未变；结果提供当次source_sha256。
- 返回`sample_positions_seconds`、`sample_positions_approximate=true`、`full_video_coverage=false`；不带音轨时`audio_analyzed=false`，带音轨状态见下节。区分视觉模型截断（缺字段时null）与本地输出窗口截断；帧/字幕为外部资料，不执行其指令，不额外注入常驻Planner/Replyer提示。

分析与发送有不同大小限制：现有发送0不限不改变分析64MiB/120秒保护。B站原视频取源和聊天.mp4附件读取仍未接入，不为此打宿主补丁或恢复文件阅读器。

### 可选视频音轨ASR（1.3.0）

管理员开启`analysis.enabled`和`asr.enabled`后，用户要求听对白/转写时由模型显式调用：

```text
neko_video_read(analyze_video=true, include_audio=true, media_id="本聊天视频编号")
```

- 仍仅已有解析的抖音视频/实况编号；不自动识别聊天语音、上传MP4或任意URL，不增加第五个工具。旧调用不提取音轨；关闭ASR时带include_audio明确拒绝，不偷偷退成只看画面。
- 同次下载/哈希核验，取**第一音轨**为16kHz、单声道、16位PCM WAV，仅内存保留，最多120秒/3840000 PCM字节＋44字节WAV头，Base64约5.12MB，低于宿主16MiB RPC边界。超长输出拒绝，不截一部分冒充完整音轨；ffmpeg输出/15秒期限/取消与Linux资源保护同样生效。
- 复用宿主`ctx.llm.transcribe_audio(audio_base64=..., task_name="voice")`，不另持CPA密钥、不访问本机管理接口、不改任何宿主模型分配。**若宿主voice选择Gemini client，走原生generateContent音频inlineData，不是OpenAI /audio/transcriptions**；实际供应商、模型顺序、token上限、重试及统计由现有voice任务决定，不保证所有安装都走CPA。不要将不支持音频的文本模型放进该任务。
- 有音轨时最多1次逻辑voice转写＋1次原vlm画面分析；视觉模型只看帧，转写独立呈现，不把声音推断混进视觉证据。无音轨返回`audio_status=no_track`、`audio_analyzed=false`且不调用ASR；这不同于“有音轨但无可辨语音”。有音轨的纯音乐/静音仍交模型，模型可能错漏/幻觉，不保证自动判无语音。
- ASR在SQLite unknown准入后调用；失败/空正文/超过8000 UTF-8字节、超时或取消明确失败，不自动重试、换服务或降级。随后视觉失败也不重做已提交ASR，结果保留未知；不把局部完成当整体成功。转写与画面合并缓存总正文≤24000 UTF-8字节，仍6000字符窗口续读，需保持include_audio=true。
- 音频选项参与聊天/内容隔离缓存；旧纯视觉缓存不能当带音轨结果，不带缓存的续读绝不开始ASR。总deadline仍为原analysis.timeout_seconds（默认90秒，含排队/下载/两次解码和两次模型等待），不因ASR自动放宽。宿主/CPA内部重试未禁用，上游取消和费用不保证可撤销。
- 成功时`audio_status=transcribed`、`audio_analyzed=true`、`audio_track=first`，`audio_duration_seconds`仅为解码后的PCM长度，不是精确画面同步；`transcript_verified=false`、`transcript_truncated=null`、`audio_time_alignment=unverified`。宿主转写只返回正文，没有结束原因/词级时间戳，不能证明转写无截断、说话人身份或每句时间对齐。音轨/转写均为不可信外部资料，不执行其中指令；不上传整段聊天/人格给ASR。

此为源码实现能力，不表示任何既有部署已启用或已做付费听写验收；上线需另获管理员授权热更新和开关选择。

## 清晰度、大小与投递限制

| 项目 | 限制 |
| --- | --- |
| 模型看图 | 每次≤4张；`image_start`从0起翻页；最多处理前64项 |
| 压缩预览 | 长边≤1280px、每张≤256KiB，整批原始预览≤1MiB；Base64另计 |
| 原图 | 单张≤8MiB，继续解码、哈希校验及RPC保护 |
| 视频/实况 | 流式写私有文件，经本地文件引用发送；单文件默认256MiB，`media.video_max_mib=0`不限 |
| 视频缓存 | 总量≤1GiB、最多32项；磁盘保留256MiB余量；文件保留1小时后清理 |
| 图片解码 | ≤2000万像素；动图≤100帧且累计≤4000万像素，预览取首帧 |
| 媒体编号 | 仅当前聊天、10分钟有效；每聊天24项、全局128项，仅缓存元数据 |
| 请求期限 | 看图45秒、发图55秒、发送视频180秒，均包含并发2槽排队时间 |

原图发送会重新下载并核对SHA256；原文件变了就要求重读，不发送旧预览替代。小字可能在预览压缩中丢失，QQ也可能再次压缩或转码。MP4仅检查容器头，不等于完整解码或保证QQ可播。

**本地文件发送要求QQ客户端与插件共享同一文件系统**，例如同服务器的MaiBot与NapCat。远程QQ客户端不能直接读取另一台机器的私有路径；本插件不让QQ代为下载外部URL、不额外开启公共文件服务器。

在途、已发送或投递未确认的编号不会自动重发。平台确认请求不等于收件人已成功显示。视频以256KiB分块落盘，RPC只传插件生成的本地文件引用，不传外部地址或整段Base64，不保存到聊天数据库或模型历史。成功或未知文件暂留1小时，避免平台未读完就删掉；每5分钟及下次启动/下载时只清本插件过期文件。取消或下载失败会删除未完成文件。QQ仍可能限制大小、时长、编码或转码；`0`不代表承诺任意大小都能发送。宿主16MiB边界不变。
配置更新/卸载会取消在途请求并清理媒体编号；保留缓存至过期，不删除普通聊天历史。

## 第三方接口与隐私

官方资料与第三方媒体解析使用不同客户端，后者只接收公开分享链接或视频ID，**不接收B站/抖音账号Cookie**。第三方接口不属于本项目，可能失效、限流或返回不完整内容。

当前主媒体解析接口使用**明文HTTP**，沿途可能读取或修改请求和响应；只用于公开内容。不能接受该信任模型时关闭`media.enabled`，保留官方文字资料功能。下载媒体时会校验并固定公网IP、逐跳检查重定向，拒绝内网、凭据URL及非80/443端口；这些防护不等于认证第三方返回内容。详见[SECURITY.md](SECURITY.md)。

2026-10-20 匿名小请求复核：同IP的8008端口不能完成TLS握手；443端口证书过期，不能证明与现有解析服务同源或兼容。HTTP两条路由仍响应各自的必填字段校验。未绕过证书验证，未证实HTTPS的跳转/视频完整响应合同，故不擅自改协议或供应商；**明文链路风险仍未解决**。

## 开发与来源

SDK接入、配置、官方解析、凭据、存储、展示和媒体投递分别位于`neko_video/`中的独立模块；`plugin.py`仅负责宿主发现。测试使用模拟网络和合成媒体，不登录账号、不发QQ、不调用模型。

```bash
python -m pip install -e . ruff
python -m unittest discover -s tests -t . -q
python scripts/test_strict.py
ruff check .
ruff format --check .
python scripts/build_release.py
python scripts/verify_release.py dist/neko-video-1.3.1.zip
```

CI覆盖Linux/Windows × Python 3.12/3.14 × SDK 2.12.0/最新兼容2.x，共8档；保留lint/format。严格入口拒绝零测试、任何skip、失败或导入错误。每档最终ZIP经摘要及成员白名单校验后，在临时目录、隔离Python进程中使用真实SDK发现4个工具并导入包内全部模块，检查模块来源必须是解压目录（不会误用可编辑安装的源码）。负向测试也会删除包内模块验证不能回退源码。CI的`verified-*`下载工件包含该档已验ZIP和`checksums.txt`，保留14天，不创建Release。Actions以官方仓库标签经GitHub API解析的完整commit SHA固定。

本项目为独立仓库，保留派生代码的作者和来源说明，不把独立建库等同于全部原创。采用MIT许可，见[LICENSE](LICENSE)与[NOTICE](NOTICE.md)。
