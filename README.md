# newscraft

AI 驱动的内容发布工具箱。聚合 AI 领域多源热点，使用 ERNIE 模型写稿，AI Studio 生成配图，创建公众号草稿。

按需组合调用各脚本，无需额外服务。

## 安装

```bash
pip install openai pillow requests
```

## 环境变量

```bash
cp .env.example .env
# 编辑 .env 填入你的 API Keys
```

| 变量 | 必需 | 说明 |
|---|---|---|
| `AI_STUDIO_API_KEY` | 写稿 + 配图 | 百度 AI Studio API Key ([申请](https://aistudio.baidu.com/)) |
| `MP_APP_ID` | 发布草稿 | 公众号 AppID |
| `MP_APP_SECRET` | 发布草稿 | 公众号 AppSecret |
| `HTTP_PROXY` / `HTTPS_PROXY` | 可选 | 访问 Reddit/HN 等境外源 |

## 工具一览

### 热点抓取

```bash
# 抓取 AI 领域热点（HN / Reddit / arXiv / RSS / HuggingFace / 国内热榜）
python3 scripts/multisource_fetch.py --limit 30 > raw_topics.json

# 解析、排名、去重
python3 scripts/topic_parse.py --input raw_topics.json --output topics.json
```

### 文章生成

```bash
# 速递模式：生成 8-10 条分类速览（附 web 补充搜索）
python3 scripts/daily_digest.py --input topics.json --output digest_prompt.md

# 话题合并建议（避免相似话题重复）
python3 scripts/topic_merge_suggest.py --input topics.json
```

### 配图生成

```bash
# 生成 ernie-image-turbo 配图提示词
python3 scripts/gems_prepare.py --draft your_draft.md --output-dir images/

# 调用 AI Studio 生成图片
python3 scripts/gems_generate.py --prompts-dir images/ --output-dir images/

# 速递封面图（ERNIE-image 背景 + Pillow 叠字）
python3 scripts/digest_cover.py --title "每日AI速递" --date 2026-05-09 --output cover.png
```

### 质量审核

```bash
# 基础质量检查（字数、链接格式、术语规范等）
python3 scripts/quality_check.py --draft your_draft.md

# LLM 深度审稿（ERNIE-4.5 + ERNIE-5.0 双模型）
python3 scripts/llm_review.py --draft your_draft.md

# 封面图审核（检查文字渗入、色彩等）
python3 scripts/cover_review.py --image cover.png
```

### 发布

```bash
# 上传图片到公众号素材库
python3 scripts/wechat_upload_image.py --image-dir images/ --output media_ids.json

# 创建公众号草稿
python3 scripts/wechat_draft.py --final your_draft.md --images media_ids.json

# 查看公众号发布统计
python3 scripts/wechat_stats.py
```

### 辅助工具

```bash
# 表格转图片（公众号不支持 Markdown 表格）
python3 scripts/table_to_image.py --input table.md --output table.png

# 公众号 HTML 清理（去除不兼容标签）
python3 scripts/wechat_cleanup.py --input article.html

# Web 搜索补充资料
python3 scripts/web_search.py --query "your topic"

# 深度报道三阶段审稿
python3 scripts/deepreport_review.py --draft your_draft.md
```

## 典型工作流

```
multisource_fetch → topic_parse → daily_digest
                                      ↓
                               （ERNIE 写稿）
                                      ↓
                    gems_prepare → gems_generate
                                      ↓
                    quality_check / llm_review
                                      ↓
                    wechat_upload_image → wechat_draft
```

## License

MIT
