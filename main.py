from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from typing import Dict
from datetime import datetime
import time
import asyncio

@register("chat_pro", "Twinkle", "AstrBot 多功能插件 - 支持 LLM 自主撤回消息", "1.0.0")
class ChatProPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 存储最近发送的消息 ID，格式: {unified_msg_origin: [(message_id, timestamp), ...]}
        self.sent_messages: Dict[str, list] = {}
        # 最多保留每个会话的最近 20 条消息记录
        self.max_messages_per_session = 20

    async def initialize(self):
        """插件初始化"""
        logger.info("ChatPro 插件已初始化 - LLM 自主撤回功能已启用")
        logger.info("AI 可以通过发送 [recall] 来撤回上一条消息")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def detect_recall_keyword(self, event: AstrMessageEvent):
        """检测 AI 发送的 [recall] 关键词并自动撤回上一条消息"""
        message_str = event.message_str.strip()
        
        # 检查是否是 [recall] 关键词
        if message_str.lower() == "[recall]":
            umo = event.unified_msg_origin
            platform_name = event.get_platform_name()
            
            # 检查是否支持撤回功能
            if platform_name not in ["aiocqhttp"]:
                logger.warning(f"当前平台 {platform_name} 暂不支持消息撤回功能")
                return
            
            # 检查是否有消息记录
            if umo not in self.sent_messages or len(self.sent_messages[umo]) < 2:
                logger.warning("没有足够的消息记录可以撤回")
                return
            
            try:
                # 获取最后两条消息 ID
                # -1 是当前的 [recall] 消息，-2 是要撤回的上一条消息
                if len(self.sent_messages[umo]) >= 2:
                    recall_msg_id = self.sent_messages[umo][-1][0]  # [recall] 消息本身
                    target_msg_id = self.sent_messages[umo][-2][0]  # 要撤回的上一条消息
                    
                    # 调用 QQ 协议端 API 撤回消息
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    if isinstance(event, AiocqhttpMessageEvent):
                        client = event.bot
                        
                        # 撤回上一条消息
                        payloads = {"message_id": target_msg_id}
                        ret1 = await client.api.call_action('delete_msg', **payloads)
                        logger.info(f"成功撤回目标消息 {target_msg_id}，返回: {ret1}")
                        
                        # 稍等一下再撤回 [recall] 本身
                        await asyncio.sleep(0.5)
                        
                        # 撤回 [recall] 关键词消息
                        payloads = {"message_id": recall_msg_id}
                        ret2 = await client.api.call_action('delete_msg', **payloads)
                        logger.info(f"成功撤回 [recall] 消息 {recall_msg_id}，返回: {ret2}")
                        
                        # 从记录中移除这两条消息
                        if len(self.sent_messages[umo]) >= 2:
                            self.sent_messages[umo].pop()  # 移除 [recall]
                            self.sent_messages[umo].pop()  # 移除上一条消息
                        
                        # 停止事件传播，避免其他插件处理 [recall]
                        event.stop_event()
                        
            except Exception as e:
                logger.error(f"撤回消息失败: {e}")

    @filter.after_message_sent()
    async def record_sent_message(self, event: AstrMessageEvent):
        """记录发送的消息 ID，以便后续撤回"""
        try:
            umo = event.unified_msg_origin
            platform_name = event.get_platform_name()
            
            # 只记录支持撤回的平台
            if platform_name not in ["aiocqhttp"]:
                return
            
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if isinstance(event, AiocqhttpMessageEvent):
                # 初始化会话的消息列表
                if umo not in self.sent_messages:
                    self.sent_messages[umo] = []
                
                # 尝试从消息事件中获取 message_id
                # 注意：这里需要根据实际的消息发送结果来获取 message_id
                # 暂时使用占位符，实际使用时需要调整
                message_id = getattr(event.message_obj, 'message_id', None)
                
                if message_id:
                    timestamp = time.time()
                    self.sent_messages[umo].append((message_id, timestamp))
                    
                    # 保持消息列表大小在限制内
                    if len(self.sent_messages[umo]) > self.max_messages_per_session:
                        self.sent_messages[umo].pop(0)
                    
                    logger.debug(f"记录消息 ID: {message_id}，会话: {umo}")
                
        except Exception as e:
            logger.error(f"记录消息 ID 失败: {e}")

    @filter.command("recall")
    async def manual_recall(self, event: AstrMessageEvent):
        """手动撤回上一条消息"""
        umo = event.unified_msg_origin
        platform_name = event.get_platform_name()
        
        # 检查是否支持撤回功能
        if platform_name not in ["aiocqhttp"]:
            yield event.plain_result(f"当前平台 {platform_name} 暂不支持消息撤回功能")
            return
        
        # 检查是否有消息记录
        if umo not in self.sent_messages or not self.sent_messages[umo]:
            yield event.plain_result("没有可以撤回的消息记录")
            return
        
        try:
            # 获取最后一条消息 ID
            message_id, timestamp = self.sent_messages[umo][-1]
            
            # 调用 QQ 协议端 API 撤回消息
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
            if isinstance(event, AiocqhttpMessageEvent):
                client = event.bot
                payloads = {"message_id": message_id}
                ret = await client.api.call_action('delete_msg', **payloads)
                
                # 从记录中移除已撤回的消息
                self.sent_messages[umo].pop()
                
                logger.info(f"手动撤回消息 {message_id}，返回: {ret}")
                yield event.plain_result("✅ 已成功撤回上一条消息")
            else:
                yield event.plain_result("消息类型错误，无法撤回")
                
        except Exception as e:
            logger.error(f"撤回消息失败: {e}")
            yield event.plain_result(f"❌ 撤回消息失败: {str(e)}")

    @filter.command("list_messages")
    async def list_sent_messages(self, event: AstrMessageEvent):
        """列出当前会话最近发送的消息记录"""
        umo = event.unified_msg_origin
        
        if umo not in self.sent_messages or not self.sent_messages[umo]:
            yield event.plain_result("当前会话没有消息记录")
            return
        
        messages = self.sent_messages[umo]
        msg_list = []
        for i, (msg_id, timestamp) in enumerate(reversed(messages), 1):
            time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
            msg_list.append(f"{i}. 消息ID: {msg_id} (发送时间: {time_str})")
        
        result_text = f"📝 最近发送的 {len(messages)} 条消息：\n" + "\n".join(msg_list)
        yield event.plain_result(result_text)

    @filter.command("help")
    async def show_help(self, event: AstrMessageEvent):
        """显示撤回功能使用帮助"""
        help_text = """
📖 LLM 自主撤回功能使用指南

🤖 AI 使用方式：
在你的 AI 人格提示词中添加：
"当你需要撤回上一条消息时，发送 [recall]"

AI 发送 [recall] 后，会自动撤回上一条消息和 [recall] 本身。

👤 用户手动命令：
- /recall - 手动撤回上一条消息
- /list_messages - 查看消息历史
- /help - 显示此帮助

⚠️ 使用示例：
用户：1+1等于几？
AI：1+1等于3
AI：[recall]
[上一条消息被撤回]
AI：抱歉，1+1等于2

✅ 支持平台：QQ (aiocqhttp)
"""
        yield event.plain_result(help_text.strip())

    async def terminate(self):
        """插件销毁时的清理工作"""
        logger.info("ChatPro 插件已卸载")
        self.sent_messages.clear()
