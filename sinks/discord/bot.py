import logging
import re
import time
from pydantic import BaseModel
from typing import Any, Dict, List, Optional, Set

import discord
from discord import Message

from agent.agent import run_chat

logger = logging.getLogger(__name__)

class UserInfo(BaseModel):
    id: str
    name: str
    isAdmin: bool = False

class ChatRequest(BaseModel):
    message: str
    history: List[HistoryMessage]
    platformContext: str
    images: List[str]
    user: UserInfo

class HistoryMessage(BaseModel):
    authorName: str
    content: str
    isBot: bool
    imageUrls: List[str]

class DiscordBot(discord.Client):
    def __init__(self, cfg, agent_cfg):

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)

        self.cfg = cfg
        self.agent_cfg = agent_cfg
        self._user_counts = {}
        self._user_windows = {}

    async def on_ready(self):
        await self.change_presence(activity=discord.Game(name="Listening to messages..."))
        logger.info(f"Logged in as {self.user}")

    async def on_message(self, message):
        if not self._should_reply(message):
            return
        self._log_message(message)

        if self._has_keyword(message.content) or self._is_mentioned(message):
            try:
                emoji = self.cfg.react_emoji
                await message.add_reaction(emoji)
            except Exception as e:
                logger.exception("No reaction emoji set. Skipping...")
                pass

        try:
            async with message.channel.typing():
                emoji_ctx = self._build_emoji_context(message.guild)
                history = await self._build_history(message)
                req = self._build_chat_request(message, history, emoji_ctx)
                resp = await self._call_agent(req)
                await self._send_reply(message, resp)
        except Exception as e:
            logger.exception("Agent error: %s", e)
            await message.channel.send("There was an error generating a reply.")

    def _should_reply(self, message):
        if message.author.bot:
            return False
        if self.user and message.author.id == self.user.id:
            return False
        if str(message.author.id) in self.cfg["bot_ids"]:
            return False
        if self.cfg["allowed_channels"] and str(message.channel.id) not in self.cfg["allowed_channels"]:
            return False

        mentioned = self.user in message.mentions if self.user else False
        if not mentioned and not self._has_keyword(message.content):
            return False
        if str(message.author.id) == self.cfg["admin_user_id"]:
            return True

        # 3 messages per user per minute
        now = time.time()
        uid = str(message.author.id)
        window = self._user_windows.get(uid, 0)
        if now - window >= 60:
            self._user_counts[uid] = 0
            self._user_windows[uid] = now
        count = self._user_counts.get(uid, 0)
        if count >= 3:
            return False
        self._user_counts[uid] = count + 1
        return True

    def _has_keyword(self, content: str) -> bool:
        lower = content.lower()
        for word in self.cfg["trigger_words"]:
            if re.search(rf'\b{re.escape(word)}\b', lower):
                return True
        return False

    def _is_mentioned(self, message: Message) -> bool:
        if self.user is None:
            return False
        return self.user in message.mentions

    def _build_chat_request(self, message, history, emoji_ctx):
        is_admin = str(message.author.id) == self.cfg["admin_user_id"]
        images = []
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                images.append(att.url)
        for embed in message.embeds:
            if embed.image and embed.image.url:
                images.append(embed.image.url)
        return ChatRequest(
            message = message.content,
            history = history,
            platformContext = emoji_ctx,
            images = images,
            user = UserInfo(
                id = str(message.author.id),
                name = message.author.name,
                isAdmin = is_admin,
            )
        )

    async def _build_history(self, message):
        history: List[HistoryMessage] = []
        try:
            past = [
                msg
                async for msg in message.channel.history(
                    limit=self.cfg["history_limit"], before=message
                )
            ]
            for m in reversed(past):
                imgs: List[str] = []
                for att in m.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        imgs.append(att.url)
                for embed in m.embeds:
                    if embed.image and embed.image.url:
                        imgs.append(embed.image.url)
                is_own_message = self.user is not None and m.author.id == self.user.id
                history.append(
                    HistoryMessage(
                        authorName=m.author.name,
                        content=m.content,
                        isBot=is_own_message,
                        imageUrls=imgs,
                    )
                )
        except Exception as e:
            logger.warning("Failed to build history: %s", e)
        return history

    def _build_emoji_context(self, guild: Optional[discord.Guild]) -> str:
        if guild is None:
            return ""
        lines = ["## AVAILABLE SERVER EMOJIS", "Use these in messages with the exact format shown.\n"]
        for emoji in guild.emojis:
            if emoji.animated:
                lines.append(f"<a:{emoji.name}:{emoji.id}>")
            else:
                lines.append(f"<:{emoji.name}:{emoji.id}>")
        stickers = guild.stickers
        if stickers:
            lines.append("\n## AVAILABLE SERVER STICKERS")
            lines.append("You cannot send stickers directly, but you can reference them by name.\n")
            for s in stickers:
                desc = f" — {s.description}" if s.description else ""
                lines.append(f"{s.name}{desc}")
        return "\n".join(lines)

    async def _call_agent(self, req):
        history = []
        for h in req.history:
            history.append(
                {
                    "authorName": h.authorName,
                    "content": h.content,
                    "isBot": h.isBot,
                    "imageUrls": h.imageUrls,
                }
            )

        user = {
            "id": req.user.id,
            "name": req.user.name,
            "isAdmin": req.user.isAdmin
        }
        reply = await run_chat(
            req.message,
            history,
            req.images,
            req.platformContext,
            user,
            self.agent_cfg
        )
        return reply.get("reply")

    async def _send_reply(self, message, text) -> None:
        for chunk in self._split(text, 2000):
            await message.reply(chunk)

    async def send_alert(self, text):
        channel = self.get_channel(int(self.cfg["notification_channel_id"]))
        for chunk in self._split(text, 2000):
            await channel.send(chunk)

    def _split(self, text, limit):
        chunks: List[str] = []
        while len(text) > limit:
            cut = text.rfind("\n", 0, limit)
            if cut < 0:
                cut = limit
            chunks.append(text[:cut])
            text = text[cut:].lstrip()
        if text:
            chunks.append(text)
        return chunks

    def _log_message(self, message):
        logger.info("------ New Message ------")
        logger.info("'%s' from %s in channel %s", message.content, message.author.name, message.channel.id)
        for att in message.attachments:
            logger.info("Attachment: %s %s", att.content_type, att.url)
        for embed in message.embeds:
            logger.info("Embed: %s %s", embed.type, embed.url)
        logger.info("------ End Message ------")
