"""
工作总结分析模块
根据全量聊天记录推断每个人的工作内容
"""

import re
from datetime import datetime
from typing import TypedDict

from ....domain.models.data_models import TokenUsage, WorkSummary
from ....utils.logger import logger
from ...utils.template_utils import render_template
from ..utils import InfoUtils
from ..utils.structured_output_schema import JSONObject, build_work_summaries_schema
from .base_analyzer import BaseAnalyzer


class _Message(TypedDict):
    sender: str
    time: str
    content: str
    user_id: str


class WorkSummaryAnalyzer(BaseAnalyzer[WorkSummary, list[dict]]):
    """
    工作总结分析器
    从全量对话上下文中推断每个人的工作内容
    """

    def get_provider_id_key(self) -> str | None:
        """获取 Provider ID 配置键名"""
        return "work_summary_provider_id"

    def get_data_type(self) -> str:
        """获取数据类型标识"""
        return "工作总结"

    def get_max_count(self) -> int:
        """获取最大工作总结人数"""
        return self.config_manager.get_max_work_summaries()

    def get_response_schema_name(self) -> str:
        return "daily_work_summaries"

    def get_response_schema(self) -> JSONObject:
        return build_work_summaries_schema(self.get_max_count())

    def build_prompt(self, data: list[dict]) -> str:
        """
        构建工作总结提示词

        Args:
            data: 群聊消息列表

        Returns:
            提示词字符串
        """
        if not isinstance(data, list):
            logger.error(f"build_prompt 期望列表，但收到: {type(data)}")
            return ""

        if not data:
            logger.warning("build_prompt 收到空消息列表")
            return ""

        # 提取文本消息（复用 TopicAnalyzer 的消息格式）
        text_messages: list[_Message] = []
        for i, msg in enumerate(data):
            if not isinstance(msg, dict):
                continue

            try:
                sender = msg.get("sender", {})
                if not isinstance(sender, dict):
                    continue

                user_id = str(sender.get("user_id", ""))
                bot_self_ids = self.config_manager.get_bot_self_ids()

                if bot_self_ids and user_id in [str(uid) for uid in bot_self_ids]:
                    continue

                nickname = InfoUtils.get_user_nickname(self.config_manager, sender)
                msg_time = datetime.fromtimestamp(msg.get("time", 0)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                message_list = msg.get("message", [])

                text_parts = []
                for content in message_list:
                    if not isinstance(content, dict):
                        continue

                    content_type = content.get("type", "")

                    if content_type == "text":
                        text = content.get("data", {}).get("text", "").strip()
                        if text:
                            text_parts.append(text)
                    elif content_type == "at":
                        at_data = content.get("data", {})
                        at_id = at_data.get("id") or at_data.get("user_id")
                        if at_id:
                            text_parts.append(f"@{at_id}")
                    elif content_type == "reply":
                        reply_id = content.get("data", {}).get("id", "")
                        if reply_id:
                            text_parts.append(f"[回复:{reply_id}]")

                combined_text = "".join(text_parts).strip()

                if (
                    combined_text
                    and len(combined_text) > 2
                    and not combined_text.startswith("/")
                ):
                    cleaned_text = combined_text.replace("“", '"').replace("”", '"')
                    cleaned_text = cleaned_text.replace("‘", "'").replace("’", "'")
                    cleaned_text = cleaned_text.replace("\n", " ").replace("\r", " ")
                    cleaned_text = cleaned_text.replace("\t", " ")
                    cleaned_text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", cleaned_text)

                    text_messages.append(
                        {
                            "sender": nickname,
                            "time": msg_time,
                            "content": cleaned_text,
                            "user_id": str(user_id),
                        }
                    )
            except Exception as e:
                logger.error(
                    f"build_prompt 处理第 {i + 1} 条消息时出错: {e}", exc_info=True
                )
                continue

        if not text_messages:
            logger.warning("build_prompt 没有提取到有效的文本消息，返回空prompt")
            return ""

        # 使用用户提供的 ID-Only 格式
        messages_text = "\n".join(
            [
                f"[{msg['time']}] [{msg['sender']}]: {msg['content']}"
                for msg in text_messages
            ]
        )

        max_summaries = self.get_max_count()

        prompt_template = self.config_manager.get_work_summary_prompt()

        if prompt_template:
            try:
                prompt = render_template(
                    prompt_template,
                    max_summaries=max_summaries,
                    messages_text=messages_text,
                )
                logger.info("使用配置中的工作总结提示词")
                return prompt
            except Exception as e:
                logger.warning(f"应用工作总结提示词失败: {e}")

        # 内置默认 prompt
        default_prompt = (
            "你是一个工作助理，正在分析一个工作群的聊天记录。\n"
            "请根据以下24小时内的群聊消息，总结每个主要参与者的工作内容。\n\n"
            "注意：\n"
            "1. 要从全量对话中推断每个人的工作，不能只看本人的发言。\n"
            "   例如：如果A指派B做某事，这应当体现在B的工作总结中。\n"
            "2. 忽略闲聊、表情包等非工作内容。\n"
            "3. 如果某人的工作内容无法从对话中确定，请标注'未从对话中体现明确工作内容'。\n\n"
            "群聊消息：\n"
            f"{messages_text}\n\n"
            f"请为每个主要参与者输出工作总结。\n\n"
            "返回纯 JSON 数组，格式如下：\n"
            "[\n"
            "  {\n"
            '    "user_id": "123456",\n'
            '    "name": "小王",\n'
            '    "summary": "1. 负责竞品分析报告的撰写...; 2. 负责 xx 的用户调研..."\n'
            "  }\n"
            "]\n"
            "注意：返回的内容必须是纯 JSON，不要包含 markdown 代码块标记或其他格式。"
        )
        logger.info("使用内置默认工作总结提示词")
        return default_prompt

    def extract_with_regex(self, result_text: str, max_count: int) -> list[dict]:
        """
        使用正则表达式提取工作总结信息（降级方案）

        Args:
            result_text: LLM响应文本
            max_count: 最大提取数量

        Returns:
            工作总结数据列表
        """
        # 简单尝试提取 JSON 数组
        try:
            # 尝试匹配 JSON 数组
            json_match = re.search(r"\[.*?\]", result_text, re.DOTALL)
            if json_match:
                import json

                parsed = json.loads(json_match.group(0))
                if isinstance(parsed, list):
                    return parsed[:max_count]
        except Exception:
            pass
        return []

    def create_data_objects(self, data_list: list[dict]) -> list[WorkSummary]:
        """
        创建工作总结对象列表

        Args:
            data_list: 原始工作总结数据列表

        Returns:
            WorkSummary对象列表
        """
        logger.debug(
            f"create_data_objects 开始处理，输入数据数量: {len(data_list) if data_list else 0}"
        )

        try:
            summaries = []
            max_count = self.get_max_count()

            for i, summary_data in enumerate(data_list[:max_count]):
                if not isinstance(summary_data, dict):
                    logger.warning(
                        f"跳过非字典类型的工作总结数据: {type(summary_data)}"
                    )
                    continue

                try:
                    user_id = str(summary_data.get("user_id", "")).strip()
                    name = str(summary_data.get("name", "")).strip()
                    summary = str(summary_data.get("summary", "")).strip()
                    tasks = summary_data.get("tasks", [])
                    status = str(summary_data.get("status", "")).strip()

                    if not user_id or not name or not summary:
                        logger.warning(f"工作总结数据格式不完整，跳过: {summary_data}")
                        continue

                    if not isinstance(tasks, list):
                        tasks = []

                    tasks = [str(t).strip() for t in tasks if t and str(t).strip()]

                    summaries.append(
                        WorkSummary(
                            user_id=user_id,
                            name=name,
                            summary=summary,
                            tasks=tasks,
                            status=status,
                        )
                    )
                except Exception as e:
                    logger.error(
                        f"处理第 {i + 1} 条工作总结数据时出错: {e}", exc_info=True
                    )
                    continue

            logger.debug(
                f"create_data_objects 完成，创建了 {len(summaries)} 个工作总结对象"
            )
            return summaries

        except Exception as e:
            logger.error(f"创建工作总结对象失败: {e}", exc_info=True)
            return []

    async def analyze_work_summaries(
        self,
        messages: list[dict],
        umo: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[WorkSummary], TokenUsage]:
        """
        分析工作总结

        Args:
            messages: 群聊消息列表
            umo: 模型唯一标识符
            session_id: 会话ID (用于调试模式)

        Returns:
            (工作总结列表, Token使用统计)
        """
        try:
            logger.info(
                f"开始工作总结分析，消息数量: {len(messages) if messages else 0}"
            )

            if not messages:
                logger.info("没有消息，返回空结果")
                return [], TokenUsage()

            summaries, usage = await self.analyze(messages, umo, session_id)
            return summaries, usage

        except Exception as e:
            logger.error(f"工作总结分析失败: {e}", exc_info=True)
            return [], TokenUsage()
