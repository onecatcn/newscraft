# newscraft

AI 驱动的内容发布流水线。自动聚合 AI 领域多源热点，使用 ERNIE 模型写稿，AI Studio 生成配图，一键创建公众号草稿。

```
多源热点抓取 → 话题筛选 → ERNIE 写稿 → 配图生成 → 质量审核 → 公众号草稿
```

## 功能

- **多源抓取**：HN / Reddit / arXiv / RSS / HuggingFace / YC / 国内热榜（IT之家、36氪等）
- **两种发布模式**：每日速递（8-10 条分类速览）/ 深度文章（单话题深度写作）
- **ERNIE 写稿**：基于百度 AI Studio ERNIE 系列模型生成文章
- **AI 配图**：使用 ernie-image-turbo 自动生成封面图和正文配图
- **质量审核**：多维度自动检查 + 人工确认环节
- **公众号发布**：自动创建草稿，人工群发

## 快速开始

### 1. 安装依赖

```bash
pip install openai pillow requests
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入你的 API Keys
```

### 3. 运行

```bash
# 抓取热点
python3 scripts/multisource_fetch.py --limit 30 | python3 scripts/topic_parse.py --output topics.json

# 生成速递文章
python3 scripts/daily_digest.py --input topics.json --output digest_prompt.md

# 质量检查
python3 scripts/quality_check.py --draft your_draft.md

# 生成配图
python3 scripts/gems_prepare.py --draft your_draft.md --output-dir images/
python3 scripts/gems_generate.py --prompts-dir images/ --output-dir images/

# 发布草稿
python3 scripts/wechat_draft.py --final your_draft.md
```

### Docker 部署（自动化定时流水线）

```bash
cp .env.example .env
# 编辑 .env

docker compose up -d
```

容器内运行两个进程：
- **cron**：每日定时执行流水线（`src/orchestrator.py`）
- **HTTP**：`:8080` 接收审稿回调（`src/callback_server.py`）

## 环境变量

| 变量 | 必需 | 说明 |
|---|---|---|
| `MP_APP_ID` | publish 阶段 | 公众号 AppID |
| `MP_APP_SECRET` | publish 阶段 | 公众号 AppSecret |
| `AI_STUDIO_API_KEY` | 写稿+配图 | 百度 AI Studio API Key |
| `HTTP_PROXY` / `HTTPS_PROXY` | 可选 | 代理（访问 Reddit/HN 等） |
| `NOTIFY_WEBHOOK_URL` | 可选 | 审稿通知 Webhook URL |

> AI Studio API Key 申请：https://aistudio.baidu.com/

## 目录结构

```
newscraft/
├── scripts/          # 各阶段处理脚本
│   ├── multisource_fetch.py   # 多源热点抓取
│   ├── topic_parse.py         # 话题解析排名去重
│   ├── daily_digest.py        # 速递文章生成
│   ├── gems_prepare.py        # 配图提示词生成
│   ├── gems_generate.py       # AI 配图生成
│   ├── quality_check.py       # 质量审核
│   ├── wechat_draft.py        # 公众号草稿创建
│   └── ...
├── src/              # Docker 服务核心模块
│   ├── orchestrator.py        # 流水线状态机
│   ├── callback_server.py     # HTTP 回调服务
│   ├── notify.py              # 通知推送
│   └── config.py              # 统一配置
├── docker/           # Docker 构建文件
├── pipeline_config.json       # 流水线参数配置
└── .env.example               # 环境变量模板
```

## License

MIT
