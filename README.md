# Creator Content Intelligence

Creator Content Intelligence 是一个 Python 命令行 Agent，用于分析公开 Instagram Reel。它组合公开元数据与 caption、Supadata 口播转录，以及可选的视频关键帧视觉证据，生成带来源链接的创作者内容机制报告。

当前版本仅支持公开 Instagram 内容。项目未来可以扩展到 YouTube，但目前没有实现 YouTube 抓取、转录或分析能力。

当前版本只有命令行，不包含网页、数据库或部署配置。

可选视觉层需要本机安装 `ffmpeg` 和 `ffprobe`。视觉层下载的原始视频与关键帧只存在于系统临时目录，处理结束后自动清理，不会写入 `outputs/`。

## 安全模式

命令默认运行 **dry-run**：仅校验 URL、配置格式和流程，不调用任何外部 API，也不会产生付费请求。只有显式加入 `--run` 后，程序才会调用 Apify、Supadata 和 DeepSeek。

## 安装

需要 Python 3.10 或更高版本。

```bash
cd creator-content-intelligence
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 中填写自己的密钥，切勿提交该文件：

```dotenv
APIFY_TOKEN=your_apify_token
SUPADATA_API_KEY=your_supadata_api_key
DEEPSEEK_API_KEY=your_deepseek_api_key
```

## 使用

项目支持两种互斥的输入模式。两种模式默认都是 dry-run；只有显式添加 `--run` 才会访问外部 API。

### 主页模式

不需要密钥、不会访问外部服务的 dry-run：

```bash
python main.py "https://www.instagram.com/example_creator/"
```

确认配置后显式执行真实流程：

```bash
python main.py "https://www.instagram.com/example_creator/" --run
```

主页模式保持原有行为：从公开创作者主页获取最近 3 条 Reel。

### 指定 Reel 模式

指定一条已经确认相关的内容：

```bash
python main.py --reel "https://www.instagram.com/reel/REEL_ID/"
```

指定最多三条内容：

```bash
python main.py \
  --reel "https://www.instagram.com/reel/REEL_ID_1/" \
  --reel "https://www.instagram.com/p/REEL_ID_2/" \
  --reel "https://www.instagram.com/reels/REEL_ID_3/" \
  --run
```

指定模式接受 `/reel/`、`/reels/` 和 `/p/` URL，并统一规范化。程序只通过 Apify 获取这些指定内容的公开元数据，不扫描对应账号的最新内容。创作者主页 URL 与 `--reel` 不能同时使用。

可使用 `--focus-products` 提醒模型优先检查某些产品：

```bash
python main.py \
  --reel "https://www.instagram.com/reel/REEL_ID/" \
  --focus-products Lovart Higgsfield CapCut
```

focus products 只是识别提醒，不会被当作内容已经提及的产品；模型仍须列出 caption、转录或视觉证据中实际出现的其他 AI 产品。

### 可选视觉分析

视觉分析默认关闭，避免下载媒体和产生额外模型成本。只有同时提供 `--run --vision` 时，程序才会下载公开媒体并调用 `deepseek-v4-flash-vision-exp`：

```bash
python main.py \
  --reel "https://www.instagram.com/reel/REEL_ID/" \
  --focus-products Lovart Higgsfield CapCut \
  --run \
  --vision
```

不加 `--run` 可以安全检查视觉分支，不下载媒体、不调用任何外部 API：

```bash
python main.py \
  --reel "https://www.instagram.com/reel/REEL_ID/" \
  --vision
```

视觉处理规则固定如下：

1. Apify 有公开 `videoUrl` 时，临时下载视频。
2. 用本地 `ffprobe` 获取时长，`ffmpeg` 固定截取约 10%、50%、90% 三张关键帧。
3. 没有 `videoUrl` 但有 `displayUrl` 时，仅分析封面，并在报告中明确标记。
4. 没有可用媒体或视觉模型失败时，文字转录和文字分析继续执行，报告记录具体视觉缺失原因。
5. Caption、口播转录和视觉证据分开渲染；检测到冲突时不替任何证据源裁决真伪。

媒体下载使用常见的 `User-Agent`、`Accept` 和 Instagram `Referer` 请求头，区分连接与读取超时，并对临时连接错误及 429/5xx 状态进行有限重试。不会绕过登录、验证码、权限或平台访问限制。若视频下载或本地视频处理失败且存在封面，程序会降级分析封面；报告只记录是否存在媒体字段、下载对象、失败阶段、脱敏异常类别和 HTTP 状态码，不记录完整媒体 URL。

报告每次都会写入 `outputs/`，文件名包含账号名和抓取时间。V2 报告按“核心结论—横向内容机制对比—对 Lovart 的策略启示—原始证据附录”组织，报告开头包含账号 URL、抓取时间、分析范围和失败条数；每条样本及分析结论保留 Instagram 来源链接。

## Evidence-constrained reporting

报告采用证据约束规则，将结论明确区分为：

- `[观察]`：直接来自 caption、口播转录、互动数据、公开元数据或关键帧视觉证据。
- `[推断]`：基于多条样本的审慎解释，必须写出不确定性。
- `[建议]`：可测试的内容假设，不描述为已经验证有效。

Caption、口播转录和视觉证据分开保存与渲染。三者出现矛盾时，报告列出冲突，不自行判定哪一方正确。模型只返回结构化 JSON、原始 URL 与内容 ID，Markdown 链接由本地渲染器统一生成。

## 分析边界与局限性

当前分析单位是**单条公开内容**，不是账号。报告中的跨样本解释不会自动扩展为对整个账号、创作者或品牌关系的结论。

每条内容与所提及产品的关系只能标记为：

- `official`
- `creator partnership`
- `organic mention`
- `comparison`
- `unknown`

只有公开文本存在明确合作披露时，才应标记为合作关系；不能仅凭提及或标记产品账号推断合作关系。

项目存在以下限制：

- 仅处理公开可访问的 Instagram 内容，私密、登录受限或地区受限内容可能失败。
- 口播转录和视觉识别均可能出现错误，报告会保留数据质量提示。
- 视觉分析固定抽取约 10%、50%、90% 三张静帧，不能代表完整视频时间线。
- 互动数据是抓取时点的公开快照，不能用于单变量因果归因。
- 没有私信、点击、销售或转化数据时，不会声称 CTA 带来转化。
- Apify、Supadata 和 DeepSeek 会产生外部服务用量；默认 dry-run 不调用这些服务。

## 流程与失败处理

1. 主页模式通过 Apify `apify/instagram-scraper` 获取公开主页最近 3 条 Reel；指定模式只获取用户给出的 1–3 条 URL 的公开元数据。
2. Supadata 对每个 Reel URL 请求转录；异步任务会自动轮询。
3. 单条转录失败不会中断其他样本，失败原因会写入报告。
4. DeepSeek 仅分析成功转录的样本，返回不含 Markdown 的结构化 JSON。
5. 本地渲染器统一生成证据标签、V2 四段式报告和来源链接。
6. 最终 Markdown 报告写入 `outputs/`。

外部服务的输出字段或模型名可能调整。可通过 `.env` 中的 `APIFY_ACTOR_ID` 和 `DEEPSEEK_MODEL` 覆盖默认值。

## 静态检查

无需安装第三方依赖即可编译检查源代码：

```bash
python3 -m compileall -q main.py src
```

安装依赖后可执行默认 dry-run 验证命令入口。

运行 CLI 输入模式测试：

```bash
PYTHONPATH=src python3 -m unittest tests/test_cli.py
```

运行视觉规则与报告渲染测试：

```bash
PYTHONPATH=src python3 -m unittest tests/test_visual.py tests/test_report_v2.py
```
