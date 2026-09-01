import json
from typing import Any

import requests

from .models import ReelSample


class AnalysisError(RuntimeError):
    pass


ALLOWED_RELATIONSHIPS = {"official", "creator partnership", "organic mention", "comparison", "unknown"}


class DeepSeekAnalyzer:
    ENDPOINT = "https://api.deepseek.com/chat/completions"

    def __init__(self, api_key: str, model: str, timeout: int = 120) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def analyze(
        self,
        account_url: str,
        samples: list[ReelSample],
        focus_products: list[str] | None = None,
    ) -> dict[str, Any]:
        successful = [sample for sample in samples if sample.transcript or sample.visual_evidence]
        if not successful:
            raise AnalysisError("没有成功转录或取得视觉证据的 Reel，无法调用 DeepSeek 分析")
        source_data = [_sample_payload(sample) for sample in successful]
        prompt = _build_prompt(account_url, source_data, focus_products or [])
        try:
            response = requests.post(
                self.ENDPOINT,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是证据约束的创作者内容研究员。只使用提供的数据，严格区分观察、推断和建议。"
                                "仅输出合法 JSON，不输出 Markdown。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            raise AnalysisError(f"DeepSeek 请求或响应解析失败：{_safe_error(exc)}") from exc
        if not isinstance(result, dict):
            raise AnalysisError("DeepSeek 返回的 JSON 顶层必须是对象")
        return result


def _sample_payload(sample: ReelSample) -> dict[str, Any]:
    relationship = sample.product_relationship
    if relationship not in ALLOWED_RELATIONSHIPS:
        relationship = "unknown"
    return {
        "content_id": sample.reel.shortcode or sample.reel.url.rstrip("/").split("/")[-1],
        "source_url": sample.reel.url,
        "author_username": sample.reel.author_username or None,
        "published_at": sample.reel.published_at.isoformat() if sample.reel.published_at else None,
        "caption": sample.reel.caption,
        "likes": sample.reel.likes,
        "comments": sample.reel.comments,
        "views": sample.reel.views,
        "transcript": sample.transcript.text if sample.transcript else None,
        "transcript_language": sample.transcript.language if sample.transcript else None,
        "visual_evidence": sample.visual_evidence,
        "visual_error": sample.visual_error,
        "product_relationship": relationship,
    }


def _build_prompt(account_url: str, source_data: list[dict[str, Any]], focus_products: list[str]) -> str:
    sample_count = len(source_data)
    small_sample_rule = (
        "样本少于 5 条。禁止使用‘证明’‘说明’‘高传播力要素’‘主力钩子’‘表现最佳内容’等确定性措辞；"
        "只能使用‘本次样本中互动量最高’‘值得进一步测试’‘不能单独归因’等审慎表述。"
        if sample_count < 5
        else "仍须避免超出证据的确定性归因。"
    )
    schema = {
        "evidence_cards": [
            {
                "content_id": "原始内容 ID",
                "source_url": "原始 URL",
                "mentioned_products": ["只列 caption 或转录实际提及的 AI 产品"],
                "hook": "开场 Hook 原文或忠实概括",
                "content_deliverable": "内容实际提供或承诺提供的交付物",
                "cta_original": "caption 或转录中的 CTA 原文；没有则为空字符串",
                "transcript_summary": "不超过 100 字的忠实摘要",
                "data_quality_notes": ["转录或字段质量提示"],
                "cross_modal_conflicts": [
                    "只列 caption、口播转录、视觉证据之间可明确定位的矛盾；没有或视觉缺失时为空数组"
                ],
                "product_relationship": "official|creator partnership|organic mention|comparison|unknown",
            }
        ],
        "sections": [
            {
                "title": "内容主题与结构等，不含风险与建议",
                "claims": [
                    {
                        "type": "观察|推断",
                        "text": "纯文本结论，不含 Markdown",
                        "evidence_ids": ["至少一个内容 ID；推断至少两个"],
                        "uncertainty": "推断必填，观察留空",
                    }
                ],
            }
        ],
        "interaction_comparisons": [
            {
                "type": "观察|推断",
                "text": "审慎比较；不得单因素归因",
                "evidence_ids": ["至少两个内容 ID"],
                "uncertainty": "发布时间不同且样本量小，不能将差异归因于单一内容变量",
            }
        ],
        "risks": [
            {"type": "观察|推断", "text": "风险或样本局限", "evidence_ids": [], "uncertainty": ""}
        ],
        "recommendations": [
            {
                "hypothesis": "待测试假设",
                "content_format": "建议内容形式",
                "why_test": "为什么值得测试，纯文本",
                "evidence_ids": ["引用的内容 ID"],
                "cannot_confirm": "不能从当前样本确认什么",
            }
        ],
    }
    return (
        "请分析公开内容样本的内容机制，而不是把账号默认称为竞品。\n"
        "分析单位是单条公开内容，不是账号整体。每项结论只能为观察、推断或建议：\n"
        "1. 观察只能直接来自 caption、转录、互动数据或公开元数据。\n"
        "2. 推断必须引用至少 2 条样本，并在 uncertainty 明确不确定性。\n"
        "3. 建议只能作为可测试假设，不得描述为已验证有效。\n"
        "CTA 可以观察‘评论关键词领取 prompt/link’，只能推断该设计意在驱动评论或私信触达；"
        "不得声称它提高评论、带来转化或形成私域，除非数据直接提供因果证据。\n"
        "互动比较必须引用至少 2 条内容；渲染器会展示每条的发布日期、观看、点赞、评论。"
        "比较文本必须明确发布时间不同、样本量小，不能单因素归因。\n"
        "产品关系仅可为 official、creator partnership、organic mention、comparison、unknown；"
        "没有明确公开合作披露时使用 unknown。\n"
        "caption、口播转录、visual_evidence 是三种独立证据源。不得用 caption 猜测画面，"
        "不得用三张静帧补全视频流程。若它们对同一事实存在明确矛盾，必须写入对应证据卡的"
        "cross_modal_conflicts，并准确指出冲突双方；不得自行裁定哪一方正确。"
        "若视觉证据缺失，不得声称三者一致，也不得把缺失当作矛盾。\n"
        f"重点识别产品提醒：{json.dumps(focus_products, ensure_ascii=False)}。这些名称只是检索提醒，"
        "绝不代表内容已经提及它们；mentioned_products 必须同时列出证据中实际出现的其它 AI 产品，"
        "没有实际文本证据的 focus product 不得写入 mentioned_products。\n"
        f"{small_sample_rule}\n"
        "不要输出 Markdown、Markdown 链接、方括号链接或 HTML。只输出符合以下结构的 JSON；"
        "source_url 必须原样返回，evidence_ids 只能使用输入 content_id。\n"
        f"JSON 结构示例：{json.dumps(schema, ensure_ascii=False)}\n"
        f"输入上下文（不得据此概括整个账号）：{account_url}\n"
        f"样本数：{sample_count}\n"
        f"样本 JSON：{json.dumps(source_data, ensure_ascii=False, indent=2)}"
    )


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, requests.RequestException):
        return f"HTTP {exc.response.status_code}" if exc.response is not None else "网络错误"
    return exc.__class__.__name__
