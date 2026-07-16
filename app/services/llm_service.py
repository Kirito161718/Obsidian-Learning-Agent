import logging

from openai import OpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是企业文档 RAG 知识库问答助手。你的回答必须严格基于“参考资料”。

回答规则：
1. 只使用参考资料中明确出现的信息，不要使用外部知识，不要猜测。
2. 如果参考资料不足以回答问题，直接回答“根据当前知识库资料，无法确定”，不要补充推测。
3. 如果只能回答一部分，请明确说明哪些部分可以确定，哪些部分无法确定。
4. 回答要简洁、准确，优先使用条目化结构；不要输出与问题无关的背景说明。
5. 关键结论后必须标注来源，例如 [来源1]、[来源2]；同一句话使用了多个资料时可以标注多个来源。
6. 不要编造文件名、页码、数量、接口、配置、时间、负责人等参考资料中不存在的信息。
7. 如果不同来源存在冲突，请指出冲突，并分别标注来源。
"""

QUERY_REWRITE_SYSTEM_PROMPT = """你是企业知识库 RAG 检索查询改写器。你的任务是把用户问题改写成更适合 embedding 检索的短查询。

改写规则：
1. 保留原问题的核心意图、关键实体、产品名、接口名、配置项、状态名、文件名、时间和约束条件。
2. 删除寒暄、语气词、重复表达和不影响检索的泛化词。
3. 将口语化问题改写为关键词更集中的检索短语。
4. 不要回答问题，不要解释，不要生成多条候选，不要补充用户没有提供的信息。
5. 如果原问题已经足够清晰，只做轻微规范化。
6. 只输出一行改写后的查询文本，不要加引号、编号、Markdown 或前后缀。
"""


class LLMServiceUnavailable(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.deepseek_api_key:
            logger.warning("DeepSeek client initialization failed: missing API key")
            raise LLMServiceUnavailable("DEEPSEEK_API_KEY is required")
        self.model = settings.deepseek_model
        self.client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)

    def generate_answer(self, question: str, contexts: list[dict]) -> str:
        context_text = self._format_contexts(contexts)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"参考资料：\n{context_text}\n\n"
                    f"用户问题：{question}\n\n"
                    "请根据参考资料回答，并在关键结论后标注来源。"
                ),
            },
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
            )
        except Exception as exc:
            self._log_llm_error("generate_answer", exc)
            raise LLMServiceUnavailable("LLM service is temporarily unavailable") from exc
        return response.choices[0].message.content or "根据当前知识库资料，无法确定"

    def rewrite_query(self, question: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": QUERY_REWRITE_SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                temperature=0,
            )
        except Exception as exc:
            self._log_llm_error("rewrite_query", exc)
            raise LLMServiceUnavailable("LLM service is temporarily unavailable") from exc
        return response.choices[0].message.content or question

    def _format_contexts(self, contexts: list[dict]) -> str:
        if not contexts:
            return "无可用参考资料。"
        lines = []
        for index, item in enumerate(contexts, start=1):
            payload = item.get("payload", {})
            filename = payload.get("filename", "unknown")
            page_no = payload.get("page_no") or "unknown"
            text = payload.get("text", "")
            lines.append(f"[来源{index}] 文件：{filename}，页码：{page_no}\n{text}")
        return "\n\n".join(lines)

    def _log_llm_error(self, operation: str, exc: Exception) -> None:
        message = str(exc).replace("\n", " ")[:300]
        logger.warning(
            "DeepSeek %s failed: error_type=%s, message=%s",
            operation,
            type(exc).__name__,
            message,
        )
