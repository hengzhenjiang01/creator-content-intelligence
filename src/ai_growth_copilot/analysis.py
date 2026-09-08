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
    claim = {
        "type": "观察|推断",
        "text": "纯文本结论，不含 Markdown",
        "evidence_ids": ["输入中的内容 ID；推断至少两个"],
        "uncertainty": "推断必填；观察留空",
    }
    mechanism_fields = {
        field: claim
        for field in (
            "content_type",
            "hook_type",
            "narrative_structure",
            "product_role",
            "cta",
        )
    }
    schema = {
        "core_conclusions": [
            {
                "type": "观察|推断",
                "text": "回答内容类型、产品融入或最值得关注的跨内容机制；最多 3 条",
                "evidence_ids": ["输入中的内容 ID"],
                "uncertainty": "推断必填；观察留空",
            }
        ],
        "content_mechanisms": [
            {"content_id": "原始内容 ID", "fields": mechanism_fields}
        ],
        "lovart_strategy": {
            "can_borrow": [
                {
                    "type": "建议",
                    "text": "Lovart 可直接采用的具体表达或结构",
                    "evidence_ids": ["输入中的内容 ID"],
                }
            ],
            "worth_testing": [
                {
                    "test_variable": "一次只改变的变量",
                    "version_a": "A 版本",
                    "version_b": "B 版本",
                    "metric": "观察指标",
                    "evidence_basis": "来自样本的具体观察，不含 Markdown",
                    "evidence_ids": ["输入中的内容 ID"],
                }
            ],
        },
        "evidence_appendix": [
            {
                "content_id": "原始内容 ID",
                "source_url": "原始 URL",
                "transcript_summary": "不超过 100 字的忠实摘要",
                "data_quality_notes": ["只写该内容特有的数据质量提示"],
                "cross_modal_conflicts": [
                    "只列 caption、口播转录、视觉证据之间可明确定位的矛盾；没有或视觉缺失时为空数组"
                ],
            }
        ],
    }
    return (
        "生成 V3 精简决策型创作者内容机制分析。读者是 Lovart 海外内容运营人员。"
        "总原则：报告正文只保留证据明确且直接影响运营决策的信息。宁可少输出，也不要为了填满字段而推测。"
        "字段证据不足时省略该字段，不得写‘未知’‘可能’‘无法确认’‘现有证据未明确’等占位内容。"
        "优先回答内容采用什么机制、产品如何融入、可借鉴什么、下一步测试什么；"
        "不要写泛化的账号总结，也不要把账号默认称为竞品。\n"
        "分析单位是单条公开内容，不是账号整体。每项结论只能为观察、推断或建议：\n"
        "1. 观察只能直接来自 caption、转录、互动数据或公开元数据。\n"
        "2. 推断必须引用至少 2 条样本，并在 uncertainty 明确不确定性。\n"
        "3. 建议只能作为可测试假设，不得描述为已验证有效。\n"
        "CTA 可以观察‘评论关键词领取 prompt/link’，只能推断该设计意在驱动评论或私信触达；"
        "不得声称它提高评论、带来转化或形成私域，除非数据直接提供因果证据。\n"
        "任何互动比较必须引用至少 2 条内容，并同时考虑发布日期、观看、点赞、评论；"
        "比较文本必须明确发布时间不同、样本量小，不能将差异归因于单一内容变量。\n"
        "如果任意样本的 published_at 为空，不得写‘发布时间与曝光窗口不同’，必须改为："
        "‘发布时间信息不完整，且已知样本的曝光窗口可能不同，因此不能直接比较互动表现。’\n"
        "caption、口播转录、visual_evidence 是三种独立证据源。不得用 caption 猜测画面，"
        "不得用三张静帧补全视频流程。若它们对同一事实存在明确矛盾，必须写入对应证据卡的"
        "cross_modal_conflicts，并准确指出冲突双方；不得自行裁定哪一方正确。"
        "若视觉证据缺失，不得声称三者一致，也不得把缺失当作矛盾。\n"
        "core_conclusions 最多 3 条，只写直接影响运营选择的结论，不复述 Caption、转录和视觉细节。"
        "content_mechanisms 只输出有直接证据的 content_type、hook_type、narrative_structure、"
        "product_role、cta；不得输出目标受众、用户痛点、口播作用、视觉作用或内容交付物。"
        "每个机制字段只允许观察，不允许用推断补齐；证据不足时省略字段。\n"
        "lovart_strategy.can_borrow 最多 2 条，只输出 Lovart 可以直接执行的具体表达或结构。"
        "worth_testing 只输出 1 个优先级最高的单变量 A/B 测试，并完整给出测试变量、A 版本、B 版本、"
        "观察指标和证据依据。禁止仅凭单条样本写‘某元素可能提升互动’等宽泛建议；"
        "单条样本只能提供测试灵感，不能提供效果或因果证据。\n"
        f"重点识别产品提醒：{json.dumps(focus_products, ensure_ascii=False)}。这些名称只是检索提醒，"
        "绝不代表内容已经提及它们；没有直接证据的产品不得写进正文，模糊 OCR 或偶然出现的产品名一律忽略。\n"
        f"{small_sample_rule}\n"
        "不要输出产品合作关系判断。完整 Caption、详细转录、逐帧画面和技术诊断不进入正文。"
        "不要输出 Markdown、Markdown 链接、方括号链接或 HTML。不要在多个字段重复同一结论、"
        "数据质量提示或证据边界。只输出符合以下结构的 JSON；"
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
