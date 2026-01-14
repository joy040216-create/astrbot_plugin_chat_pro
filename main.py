import asyncio
import re
from collections import deque
from typing import Dict

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Plain


class SessionState:
    """会话状态管理"""
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.sent_messages = deque(maxlen=20)  # 最多保留20条消息ID
        self.pending_recalls = []  # 待撤回的消息ID列表


class StateManager:
    """全局状态管理器"""
    _sessions: Dict[str, SessionState] = {}

    @classmethod
    def get_session(cls, session_id: str) -> SessionState:
        if session_id not in cls._sessions:
            cls._sessions[session_id] = SessionState(session_id)
        return cls._sessions[session_id]


@register("chat_pro", "Twinkle", "AstrBot 多功能插件 - 支持 LLM 自主撤回消息", "1.0.0")
class ChatProPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    @filter.on_decorating_result()
    async def detect_and_process_recall(self, event: AstrMessageEvent):
        """在消息发送前检测并处理 [recall] 标记"""
        result = event.get_result()
        if not result or not result.chain:
            return

        # 只处理最后一个Plain组件
        if not result.chain or not isinstance(result.chain[-1], Plain):
            return

        seg = result.chain[-1]
        text = seg.text

        # 检查是否包含 [recall] 标记（不区分大小写）
        if '[recall]' in text.lower():
            logger.info(f"检测到 [recall] 标记: {text}")

            # 移除 [recall] 标记
            cleaned_text = re.sub(r'\[recall\]', '', text, flags=re.IGNORECASE).strip()

            if cleaned_text:
                # 更新消息内容
                seg.text = cleaned_text
                logger.info(f"将发送并撤回消息: {cleaned_text}")

                # 标记需要撤回
                event._need_recall = True
            else:
                # 如果移除后没内容，阻止发送
                event.set_result(event.plain_result(""))
                logger.info("移除 [recall] 后无内容，已阻止发送")

    @filter.after_message_sent()
    async def handle_recall_after_sent(self, event: AstrMessageEvent):
        """消息发送后处理撤回逻辑"""
        # 只处理QQ平台
        if event.get_platform_name() != "aiocqhttp":
            return

        # 检查是否需要撤回
        if not hasattr(event, '_need_recall') or not event._need_recall:
            return

        try:
            # 尝试获取message_id
            message_id = None
            result = event.get_result()

            # 方法1: 从result.metadata获取
            if result and hasattr(result, 'metadata') and result.metadata:
                message_id = result.metadata.get('message_id')
                if message_id:
                    logger.info(f"从 metadata 获取到 message_id: {message_id}")

            # 方法2: 从message_obj获取
            if not message_id and hasattr(event, 'message_obj'):
                message_id = getattr(event.message_obj, 'message_id', None)
                if message_id:
                    logger.info(f"从 message_obj 获取到 message_id: {message_id}")

            if message_id:
                # 等待消息发送完成
                await asyncio.sleep(0.5)

                # 执行撤回
                try:
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    if isinstance(event, AiocqhttpMessageEvent):
                        client = event.bot
                        ret = await client.api.call_action('delete_msg', message_id=message_id)
                        logger.info(f"成功撤回消息 {message_id}, 返回: {ret}")
                    else:
                        logger.warning("消息类型不是 AiocqhttpMessageEvent，无法撤回")
                except Exception as e:
                    logger.error(f"撤回消息 {message_id} 失败: {e}", exc_info=True)
            else:
                logger.warning("无法获取 message_id，撤回失败")

        except Exception as e:
            logger.error(f"处理撤回逻辑时出错: {e}", exc_info=True)

    @filter.command("recall_help")
    async def show_help(self, event: AstrMessageEvent):
        """显示使用帮助"""
        help_text = """📖 LLM 自主撤回功能

🤖 AI 使用方式：
在消息末尾添加 [recall] 标记即可自动撤回

示例：
AI: "我爱你[recall]"
→ 发送"我爱你"后立即撤回

⚠️ 注意事项：
- 仅支持 QQ 平台 (aiocqhttp)
- [recall] 标记会被自动移除
- 撤回延迟约 0.5 秒

💡 使用场景：
- 发送了错误信息需要撤回
- 测试消息后立即清理
- 临时展示信息后撤回"""
        yield event.plain_result(help_text.strip())

    async def terminate(self):
        """插件卸载时的清理"""
        logger.info("ChatPro 插件已卸载")
        StateManager._sessions.clear()
