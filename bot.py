import os
import asyncio
import logging
import time
import random
import json
import hashlib
import re
import aiohttp
import yt_dlp
import urllib.parse as urllib_parse
from datetime import timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, UserNotParticipantError, FloodWaitError
from telethon.tl.functions.channels import (
    InviteToChannelRequest, EditAdminRequest, EditPhotoRequest,
    GetParticipantRequest, JoinChannelRequest
)
from telethon.tl.functions.messages import AddChatUserRequest, SendReactionRequest
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest, DeletePhotosRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    ChatAdminRights, ChatBannedRights, ReactionEmoji,
    ChannelParticipantCreator, ChannelParticipantAdmin, ChannelParticipant,
    MessageEntityCustomEmoji, MessageEntityBold, MessageEntityItalic
)
from telethon.tl.custom import Button
from telethon.extensions import markdown

# --- LOGGING ---
logging.basicConfig(
    format='[%(levelname)s %(asctime)s] %(name)s: %(message)s',
    level=logging.WARNING
)
logger = logging.getLogger("USERHOSTSystem")

# --- PREMIUM EMOJI & FONT UTILS ---
PREMIUM_EMOJI_IDS = [
    5085022089103016925, 6129828611055689014, 5244837092042750681,
    5447644880824181073, 5447410659077661506, 5231200819986047254,
    5260293700088511294, 6318817991102305543, 6127636064610818291,
    6124902618574625426, 5098265504796115765, 5084613633418199991,
    5116517977637782262, 5116116063188157350, 5118588409572492943,
    5125286720308249351,
]
_premium_emoji_index = 0

def add_premium_emoji(text):
    global _premium_emoji_index
    plain_text, entities = markdown.parse(text)
    if not PREMIUM_EMOJI_IDS:
        return plain_text, entities
    emoji_id = PREMIUM_EMOJI_IDS[_premium_emoji_index % len(PREMIUM_EMOJI_IDS)]
    _premium_emoji_index += 1
    placeholder = "⭐"
    offset = len(plain_text.encode("utf-16-le")) // 2
    plain_text += placeholder
    entities.append(MessageEntityCustomEmoji(
        offset=offset,
        length=len(placeholder.encode("utf-16-le")) // 2,
        document_id=emoji_id
    ))
    return plain_text, entities

def _bold_italic_text(value):
    if not isinstance(value, str) or not value:
        return value
    protected = []
    pattern = re.compile(r'https?://\S+|`[^`]*`|@[A-Za-z0-9_]+')
    def hold(match):
        protected.append(match.group(0))
        return f'\x00{chr(0xE000 + len(protected)-1)}\x00'
    value = pattern.sub(hold, value)
    normal = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    styled = ("𝘼𝘽𝘾𝘿𝙀𝙁𝙂𝙃𝙄𝙅𝙆𝙇𝙈𝙉𝙊𝙋𝙌𝙍𝙎𝙏𝙐𝙑𝙒𝙓𝙔𝙕"
              "𝙖𝙗𝙘𝙙𝙚𝙛𝙜𝙝𝙞𝙟𝙠𝙡𝙢𝙣𝙤𝙥𝙦𝙧𝙨𝙩𝙪𝙫𝙬𝙭𝙮𝙯"
              "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵")
    value = value.translate(str.maketrans(normal, styled))
    for i, item in enumerate(protected):
        value = value.replace(f'\x00{chr(0xE000 + i)}\x00', item)
    return value

def get_styled_text_and_entities(text):
    """Helper jo text ko bold-italic banata hai aur premium emoji lagata hai"""
    plain_text, entities = add_premium_emoji(text)
    styled_text = _bold_italic_text(plain_text)
    total_units = len(styled_text.encode("utf-16-le")) // 2
    if total_units:
        entities.extend([
            MessageEntityBold(offset=0, length=total_units),
            MessageEntityItalic(offset=0, length=total_units),
        ])
    return styled_text, entities

def get_wizard_text_and_entities(text):
    """Yeh specifically Wizard (Hosting) ke messages ko format karega screenshot ke mutabik"""
    lines = text.split('\n')
    styled_lines = []
    for line in lines:
        styled_lines.append(_bold_italic_text(line))
    styled_text = "\n".join(styled_lines)
    
    # Premium emoji attach karna
    plain_text, entities = add_premium_emoji(styled_text)
    
    # Bold-Italic entities apply karna
    total_units = len(plain_text.encode("utf-16-le")) // 2
    if total_units:
        entities.extend([
            MessageEntityBold(offset=0, length=total_units),
            MessageEntityItalic(offset=0, length=total_units),
        ])
    return plain_text, entities

# --- MANAGER BOT CONFIG ---
BOT_TOKEN = "8443746675:AAHl4qeUXwF5VsTik9mFm34CTVpJ303vBdE"
ADMIN_CHAT_ID = 8935687148
API_ID = 31926961
API_HASH = "3b11a16e17cbc0d8f76db75928fc7db6"

USER_REG_DATA = {}
ACTIVE_USERBOTS = {}
HOSTED_USERS = set()
HOSTED_USER_ENTITIES = {}
FORCED_CHANNELS = []

BAN_FILE = "banned_users.json"

def load_banned_users():
    try:
        with open(BAN_FILE, "r", encoding="utf-8") as f:
            return {int(uid) for uid in json.load(f)}
    except (OSError, ValueError, TypeError):
        return set()

BANNED_USERS = load_banned_users()

def save_banned_users():
    with open(BAN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(BANNED_USERS), f)

CUSTOM_AUTO_REPLY_TEXTS = [
    "🍁⃝💙⃝⋆🕊️𝙆ɪʟᴀss 𝙈ᴛᴛ 𝙍ɴᴅʏ 𝙈ᴀᴀ 𝙆E 𝙇ᴀᴅᴋᴇ 🤣🤣🍁⃝💙⃝⋆🕊️",
    "💀💙⃝⋆🤣𝘽ᴀᴀᴘ 𝙆ᴇ 𝙎ᴀᴍɴᴇ 𝙃ᴀᴡᴀʙᴀᴀᴢɪ 𝙆ᴀʀᴇɢᴀ 𝙍ɴᴅʏᴋᴇ 𝙇ᴀᴅᴋᴇ 🤣☝️ ִֶָ𓂃 ࣪ ִֶָ👑་༘࿐",
    "𝙍ɴᴅʏᴋᴇ 𝘽ᴀᴀᴘ 𝘽ᴏʟ 𝙈ᴀᴀꜰ 𝙆ᴀʀᴅᴜɴɢᴀ😭🤣🤣☝️🦢   ᯓᡣ𐭩______ 𓂃☁︎ 𓂃",
    "꧁☠︎︎༒︎☢︎︎༆𝙆ʏᴏ 𝙍ᴇ 𝙈ᴀᴅᴀʀᴄʜᴏᴅ 𝘽ᴀɴᴀᴜ 𝙏ᴜᴊʜᴇ 𝙁ʏᴛᴇʀ 𝘽ʜᴀɢᴡᴀɴ 𝘽ᴏʟ 𝙏ᴏ 𝙅ᴀɴᴇ 𝘿ᴜɴɢᴀ 🤣 𓂃☁︎ ִֶָ𓂃 ࣪ ִֶָ👑་༘࿐",
    "𓂃☁︎ 𝘼ᴀɢᴀʏᴀ 𝘼ᴘɴɪ 𝙈ᴀᴀ 𝘾ʜᴜᴅᴀɴᴇ 𝙆ʏᴏ 𝙍ᴇ 𝙍ᴀɴᴅɪᴋᴇ 🤣🤣",
    "⋆⭒˚.⋆𝘿ᴜɴɪᴀ 𝙈ᴇ 𝙎ɪʀꜰ 𝙀ᴋ 𝘽ʜᴀɢᴡᴀɴ 𝙃 𝙒ᴏ 𝙎ʜɪᴠA 𝘽ʜᴀɢᴡᴀɴ 𝙃ᴀɪ 𝙐sᴋᴏ 𝘽ʜᴀɢᴡᴀɴ 𝘽ᴏʟ 𝙏ᴏʜ 𝙏ᴇʀʏ 𝙈ᴀᴀ 𝘾ʜᴏᴅᴋᴇ 𝙏ᴜᴊʜᴇ 𝙅ᴀᴀɴᴇ 𝘿ᴜɴɢᴀ 🤣☝️",
]

NC_TEXTS = [
    "🍁⃝💙⃝⋆🕊️𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ɪ 𝐂ʜᴜᴅᴀɪ 𝐌ᴇ 𝐒ʜɪᴠᴀ 𝐊ᴀ 𝐋ᴜɴᴅ 𝐏ʜᴀs 𝐆ᴀʏᴀ 🤣🤣🍁⃝💙⃝⋆🕊️",
    "💀💙⃝⋆🤣𝐑ɴᴅʏᴋᴇ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴏ 𝐌ᴀɪ 𝐂ʜᴏᴅ ᴅᴜɴɢᴀ 𝐀ᴜʀ 𝐓ᴜ 𝐁ᴀᴀᴘ 𝐁ᴏʟᴛᴀ 𝐏ʜɪʀᴇɢᴀ 🤣☝️ ִֶָ𓂃 ࣪ ִֶָ👑་༘࿐",
    "𝙍ɴᴅʏᴋᴇ 𝐓ᴇʀᴀ 𝐁ᴀᴀᴘ 𝐁ᴏʟ 𝐌ᴀᴀꜰ 𝐊ᴀʀᴅᴜɴɢᴀ 😭🤣🤣☝️🦢   ᯓᡣ𐭩______ 𓂃☁︎ 𓂃",
    "꧁☠︎︎༒︎☢︎︎༆𝐊ʏᴏ 𝐑ᴇ 𝐌ᴀᴅᴀʀᴄʜᴏᴅ 𝐁ᴀɴᴀᴜ 𝐓ᴜᴊʜᴇ 𝐅ʏᴛᴇʀ 𝐁ʜᴀɢᴡᴀɴ 𝐁ᴏʟ 𝐓ᴏ 𝐉ᴀɴᴇ 𝐃ᴜɴɢᴀ 🤣 𓂃☁︎ ִֶָ𓂃 ࣪ ִֶָ👑་༘࿐",
    "𓂃☁︎ 𝐀ᴀɢᴀʏᴀ 𝐀ᴘɴɪ 𝐌ᴀᴀ 𝐂ʜᴜᴅᴀɴᴇ 𝐊ʏᴏ 𝐑ᴇ 𝐑ᴀɴᴅɪᴋᴇ 🤣🤣",
    "⋆⭒˚.⋆𝐃ᴜɴɪᴀ 𝐌ᴇ 𝐒ɪʀꜰ 𝐄ᴋ 𝐁ʜᴀɢᴡᴀɴ 𝐇 𝐖ᴏ 𝐒ʜɪᴠᴀ 𝐁ʜᴀɢᴡᴀɴ 𝐇ᴀɪ 𝐔sᴋᴏ 𝐁ʜᴀɢᴡᴀɴ 𝐁ᴏʟ 𝐓ᴏʜ 𝐓ᴇʀʏ 𝐌ᴀᴀ 𝐂ʜᴏᴅᴋᴇ 𝐓ᴜᴊʜᴇ 𝐉ᴀᴀɴᴇ 𝐃ᴜɴɢᴀ 🤣☝️",
    "꧁ঔৣ☠︎᭄🦢𝐒ᴜɴ 𝐑ᴏ 𝐌ᴀᴛ 𝐑ᴀɴᴅɪʏᴋᴇ 𝐀ʙʜɪ 𝐓ᴏ 𝐓ᴇʀɪ 𝐂ᴜᴅᴀɪ 𝐁ʜɪ 𝐏ᴜʀɪ 𝐍ᴀʜɪ 𝐇ᴜɪ ཧᜰ꙰ꦿ",
    "亗 ●────────𝐁ʜᴀɢᴡᴀɴ 𝐁ᴏʟ 𝐑ᴀɴᴅɪʏᴋᴇ༒࿐ 𓂃✍",
    "ᜰ꙰ꦿ➢𝐅ʏᴛᴇʀ 𝐁ᴀɴᴇɢᴀ 𝐑ᴜᴋ 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐂ʜᴏᴅɴᴇ 𝐓ᴇʀᴀ 𝐁ᴀᴀᴘ 𝐀ʀᴀ 𝐇ᴀɪ 😭☝️ᯓᡣ𐭩______ 𓂃☁︎ 𓂃",
    "🔞 𝐓ᴇʀɪ 𝐌ᴀᴀ 𝐊ᴀ 𝐁ʜᴏsᴅᴀ 𝐑ɴᴅʏᴋᴇ 🤣🔥 𝐒ʜɪᴠᴀ 𝐊ᴀ 𝐋ᴜɴᴅ 𝐓ᴇʀᴇ 𝐌ᴜʜ 𝐌ᴇ 🤡",
]

COMMAND_EMOJIS = ["💖", "⚡", "❤️", "🩷", "💛", "🧡", "💙", "💜", "💘", "❤️‍🔥", "☠️", "💀", "🖤", "💚", "🩵", "🤎", "🤍"]
_command_emoji_index = 0

def _decorate_command_text(value):
    global _command_emoji_index
    if not isinstance(value, str) or not value.strip():
        return value
    emoji = COMMAND_EMOJIS[_command_emoji_index % len(COMMAND_EMOJIS)]
    _command_emoji_index += 1
    return f"{_bold_italic_text(value)} {emoji}"

def _install_userbot_font(client):
    if getattr(client, '_shiva_font_installed', False):
        return
    original_send_message = client.send_message
    original_send_file = client.send_file
    original_edit_message = client.edit_message

    async def send_message_font(*args, **kwargs):
        args = list(args)
        if len(args) > 1 and isinstance(args[1], str):
            args[1] = _decorate_command_text(args[1])
        elif isinstance(kwargs.get('message'), str):
            kwargs['message'] = _decorate_command_text(kwargs['message'])
        return await original_send_message(*args, **kwargs)

    async def send_file_font(*args, **kwargs):
        args = list(args)
        if len(args) > 2 and isinstance(args[2], str):
            args[2] = _decorate_command_text(args[2])
        elif isinstance(kwargs.get('caption'), str):
            kwargs['caption'] = _decorate_command_text(kwargs['caption'])
        return await original_send_file(*args, **kwargs)

    async def edit_message_font(*args, **kwargs):
        args = list(args)
        if len(args) > 2 and isinstance(args[2], str):
            args[2] = _decorate_command_text(args[2])
        elif isinstance(kwargs.get('text'), str):
            kwargs['text'] = _decorate_command_text(kwargs['text'])
        return await original_edit_message(*args, **kwargs)

    client.send_message = send_message_font
    client.send_file = send_file_font
    client.edit_message = edit_message_font
    client._shiva_font_installed = True


def register_userbot_handlers(client):
    _install_userbot_font(client)
    SAVED_BOTS = []
    ACTIVE_LOOPS = {}
    AUTO_REPLY_TARGETS = {}
    AUTO_REPLY_TEXTS_OVERRIDE = {}
    AUTO_REACT_TARGETS = {}
    LOCAL_MUTES = set()
    GLOBAL_MUTES = set()
    REACT_ALL_TASKS = {}
    PROTECTED_CHATS = set()
    LOCKED_RIGHTS = {}
    AFK_USERS = {}
    GHOSTED = set()

    try:
        _session_fingerprint = hashlib.sha256(client.session.save().encode("utf-8")).hexdigest()[:16]
    except Exception:
        _session_fingerprint = str(getattr(client, "session", "default")).replace("/", "_")[:16]

    SUDO_FILE = f"sudo_users_{_session_fingerprint}.json"
    try:
        with open(SUDO_FILE, "r", encoding="utf-8") as f:
            SUDO_USERS = {int(uid) for uid in json.load(f)}
    except (OSError, ValueError, TypeError):
        SUDO_USERS = set()

    def save_sudo_users():
        try:
            with open(SUDO_FILE, "w", encoding="utf-8") as f:
                json.dump(sorted(SUDO_USERS), f)
        except OSError:
            pass

    START_TIME = time.time()

    PROFILE_BACKUP_FILE = f"profile_backup_{_session_fingerprint}.json"
    PROFILE_BACKUP_PHOTO = f"profile_backup_{_session_fingerprint}.jpg"

    async def _load_profile_backup():
        try:
            with open(PROFILE_BACKUP_FILE, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except (OSError, ValueError, TypeError):
            return None

    ANTIDEL_ENABLED = False
    ANTIDEL_CACHE = {}
    WATCH_SPAM_CONFIG = {}
    GROUP_PFP_POOL = []
    PFP_ROTATE_TASK = None

    async def check_auth(event):
        if event.out:
            return True
        if event.sender_id in SUDO_USERS:
            return True
        return False

    async def edit_or_reply(event, text):
        try:
            styled_text, entities = get_styled_text_and_entities(text)
            if event.out:
                try:
                    await event.edit(styled_text, formatting_entities=entities, parse_mode=None)
                except Exception:
                    await event.reply(styled_text, formatting_entities=entities, parse_mode=None)
            else:
                await event.reply(styled_text, formatting_entities=entities, parse_mode=None)
        except Exception:
            if event.out:
                try:
                    await event.edit(text)
                except Exception:
                    await event.reply(text)
            else:
                await event.reply(text)

    # ============ MENU (ONLY MENU WITHOUT FONT) ============
    @client.on(events.NewMessage(pattern=r'^xshiva$'))
    async def shiva_help_handler(event):
        if not await check_auth(event):
            return
        help_text = (
            "╔═══━━━─── • 🩷 • ───━━━═══╗\n"
            "𓆩 𝑺𝑯𝑰𝑽𝑨 𝑼𝑺𝑬𝑹𝑩𝑶𝑻 𓆪\n"
            "╚═══━━━─── • 🩷 • ───━━━═══╝\n\n"
            "┏━━━━━━━ ⚡ 𝑺𝑷𝑨𝑴 & 𝑨𝑼𝑻𝑶 𝑹𝑬𝑷𝑳𝒀\n┃\n"
            "┃  ❖ `xslidespam <text>`\n"
            "┃  ❖ `xspm <text>`\n"
            "┃  ❖ `xms <text>`\n"
            "┃  ❖ `xx <name>` — reply\n"
            "┃  ❖ `xdelay <seconds>`\n"
            "┃  ❖ `xswipe <text>`\n"
            "┃  ❖ `xareply <text>`\n"
            "┃  ❖ `xreply`\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "┏━━━━━━━ 🛑 𝑺𝑻𝑶𝑷 / 𝑪𝑳𝑬𝑨𝑵\n┃\n"
            "┃  ❖ `xstopslidespam`\n"
            "┃  ❖ `xstopms`\n"
            "┃  ❖ `xstopspam`\n"
            "┃  ❖ `xstopx`\n"
            "┃  ❖ `xstopswipe`\n"
            "┃  ❖ `xstopareply`\n"
            "┃  ❖ `xstopreply`\n"
            "┃  ❖ `xstopall`\n"
            "┃  ❖ `xkillall`\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "┏━━━━━━━ 🔞 𝑮𝑨𝑨𝑳𝑰 (NC) 𝑴𝑶𝑫𝑬\n┃\n"
            "┃  ❖ `xnc` — reply karke gaali loop ON\n"
            "┃  ❖ `xsnc` — reply karke gaali loop OFF\n"
            "┃  ❖ `xnc <user_id>` — usko gaali bhejo\n"
            "┃  ❖ `xnc1` — khud ko random gaali\n"
            "┃  ❖ `xraid` — random gaali/roast\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "┏━━━━━━━ 🎭 𝑹𝑬𝑨𝑪𝑻𝑰𝑶𝑵 𝑳𝑨𝑩\n┃\n"
            "┃  ❖ `xreact <emoji>`\n"
            "┃  ❖ `xminereact <emoji>`\n"
            "┃  ❖ `xreactall <emoji>`\n"
            "┃  ❖ `xstopreact`\n"
            "┃  ❖ `xstopminereact`\n"
            "┃  ❖ `xstopreactall`\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "┏━━━━━━━ 🛡️ 𝑮𝑹𝑶𝑼𝑷 𝑺𝑯𝑰𝑬𝑳𝑫\n┃\n"
            "┃  ❖ `xkick`  ❖ `xban`  ❖ `xunban`\n"
            "┃  ❖ `xsilence`  ❖ `xunsilence`\n"
            "┃  ❖ `xmuteall`  ❖ `xunmuteall`\n"
            "┃  ❖ `xprotect`  ❖ `xunprotect`\n"
            "┃  ❖ `xpromote`  ❖ `xdemote`\n"
            "┃  ❖ `xlock <type>`\n"
            "┃  ❖ `xunlock <type>`\n"
            "┃  ❖ `xlocks`\n"
            "┃  ❖ `xjoin <link>`\n"
            "┃  ❖ `xleave`\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "┏━━━━━━━ 👤 𝑷𝑹𝑶𝑭𝑰𝑳𝑬 / 𝑺𝑻𝑨𝑻𝑼𝑺\n┃\n"
            "┃  ❖ `xinfo`\n"
            "┃  ❖ `xcp`  ❖ `xnrml`\n"
            "┃  ❖ `xafk [reason]`\n"
            "┃  ❖ `xback`\n"
            "┃  ❖ `xghost`\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "┏━━━━━━━ 🧹 𝑪𝑳𝑬𝑨𝑵𝑬𝑹\n┃\n"
            "┃  ❖ `xdelmine`\n"
            "┃  ❖ `xdelall`\n"
            "┃  ❖ `xpurge`\n"
            "┃  ❖ `xpurgefrom`\n"
            "┃  ❖ `xpurgetto`\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "┏━━━━━━━ 🤖 𝑩𝑶𝑻'𝑺 𝑪𝑶𝑵𝑻𝑹𝑶𝑳\n┃\n"
            "┃  ❖ `xaddbots`\n"
            "┃  ❖ `xdelbots`\n"
            "┃  ❖ `xlistbots`\n"
            "┃  ❖ `xinvitebots`\n"
            "┃  ❖ `xpromotebots`\n"
            "┃  ❖ `xremovebots`\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "┏━━━━━━━ 👑 𝑶𝑾𝑵𝑬𝑹 𝑪𝑶𝑵𝑻𝑹𝑶𝑳\n┃\n"
            "┃  ❖ `xsudo`\n"
            "┃  ❖ `xdelsudo`\n"
            "┃  ❖ `xsudolist`\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "┏━━━━━━━ 🎵 𝑴𝑬𝑫𝑰𝑨\n┃\n"
            "┃  ❖ `xsong <name>`\n"
            "┃  ❖ `xig <link>`\n"
            "┃  ❖ `xpt <link>`\n"
            "┃  ❖ `xfb <link>`\n"
            "┃  ❖ `xyt <link>`\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "    𓆩✯𓆪 𝑺𝒉𝒊𝒗𝒂. 𓆩✯𓆪"
        )
        # Menu ke liye direct reply use kiya gaya hai taaki font apply na ho
        if event.out:
            await event.edit(help_text, parse_mode='md')
        else:
            await event.reply(help_text, parse_mode='md')

    # ============ PROFILE COPY / RESTORE ============
    @client.on(events.NewMessage(pattern=r'^xcp$'))
    async def copy_profile_handler(event):
        if not await check_auth(event):
            return
        if not event.out:
            return
        reply = await event.get_reply_message()
        if not reply or not reply.sender_id:
            return await edit_or_reply(event, "⚠️ Reply to a user's message and use `xcp`.")
        try:
            target = await client.get_entity(reply.sender_id)
            full = await client(GetFullUserRequest(target))
            target_about = getattr(full.full_user, "about", "") or ""
            target_first = getattr(target, "first_name", "") or ""
            target_last = getattr(target, "last_name", "") or ""
            backup = await _load_profile_backup()
            if backup is None:
                me = await client.get_me()
                me_full = await client(GetFullUserRequest(me))
                original_photo = await client.download_profile_photo("me", file=PROFILE_BACKUP_PHOTO)
                backup = {
                    "first_name": getattr(me, "first_name", "") or "",
                    "last_name": getattr(me, "last_name", "") or "",
                    "about": getattr(me_full.full_user, "about", "") or "",
                    "had_photo": bool(original_photo),
                }
                with open(PROFILE_BACKUP_FILE, "w", encoding="utf-8") as fp:
                    json.dump(backup, fp, ensure_ascii=False)
            await client(UpdateProfileRequest(
                first_name=target_first or " ",
                last_name=target_last,
                about=target_about
            ))
            photo_path = await client.download_profile_photo(
                target, file=f"profile_copy_{_session_fingerprint}.jpg"
            )
            if photo_path:
                uploaded = await client.upload_file(photo_path)
                await client(UploadProfilePhotoRequest(file=uploaded))
                try:
                    os.remove(photo_path)
                except OSError:
                    pass
            await edit_or_reply(
                event,
                "✅ Profile copy ho gaya: name + bio" +
                (" + photo." if photo_path else ". Target ki photo available nahi thi.") +
                "\nRestore ke liye `xnrml` bhejo."
            )
        except Exception as exc:
            await edit_or_reply(event, f"❌ Profile copy fail: `{exc}`")

    @client.on(events.NewMessage(pattern=r'^xnrml$'))
    async def restore_profile_handler(event):
        if not await check_auth(event):
            return
        if not event.out:
            return
        backup = await _load_profile_backup()
        if not backup:
            return await edit_or_reply(event, "⚠️ No backup. Use `xcp` first.")
        try:
            await client(UpdateProfileRequest(
                first_name=backup.get("first_name") or " ",
                last_name=backup.get("last_name") or "",
                about=backup.get("about") or ""
            ))
            if backup.get("had_photo") and os.path.exists(PROFILE_BACKUP_PHOTO):
                uploaded = await client.upload_file(PROFILE_BACKUP_PHOTO)
                await client(UploadProfilePhotoRequest(file=uploaded))
            elif not backup.get("had_photo"):
                photos = await client.get_profile_photos("me", limit=1)
                if photos:
                    await client(DeletePhotosRequest(id=[photos[0]]))
            for path in (PROFILE_BACKUP_FILE, PROFILE_BACKUP_PHOTO):
                try:
                    os.remove(path)
                except OSError:
                    pass
            await edit_or_reply(event, "✅ Original profile restored.")
        except Exception as exc:
            await edit_or_reply(event, f"❌ Restore fail: `{exc}`")

    # ============ INFO / AFK ============
    @client.on(events.NewMessage(pattern=r'^xinfo$'))
    async def xinfo_handler(event):
        if not await check_auth(event):
            return
        try:
            me = await client.get_me()
            full = await client(GetFullUserRequest(me))
            about = getattr(full.full_user, "about", "") or "—"
            await edit_or_reply(
                event,
                f"👤 Name: {me.first_name or ''} {me.last_name or ''}\n"
                f"🆔 ID: {me.id}\n"
                f"📛 Username: @{me.username if me.username else '—'}\n"
                f"📱 Phone: +{me.phone if me.phone else '—'}\n"
                f"📝 Bio: {about}"
            )
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")

    @client.on(events.NewMessage(pattern=r'^xafk(?:\s+(.+))?$'))
    async def xafk_handler(event):
        if not await check_auth(event):
            return
        reason = (event.pattern_match.group(1) or "AFK").strip()
        me = await client.get_me()
        AFK_USERS[me.id] = reason
        GHOSTED.discard(me.id)
        await edit_or_reply(event, f"💤 AFK set: {reason}")

    @client.on(events.NewMessage(pattern=r'^xback$'))
    async def xback_handler(event):
        if not await check_auth(event):
            return
        me = await client.get_me()
        AFK_USERS.pop(me.id, None)
        GHOSTED.discard(me.id)
        await edit_or_reply(event, "👋 Back online.")

    @client.on(events.NewMessage(pattern=r'^xghost$'))
    async def xghost_handler(event):
        if not await check_auth(event):
            return
        me = await client.get_me()
        GHOSTED.add(me.id)
        await edit_or_reply(event, "👻 Ghost mode ON.")

    # ============ SPAM / AUTO REPLY ============
    async def _spam_worker(chat_id, text, delay=0.5):
        while True:
            try:
                await client.send_message(chat_id, text)
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1)

    @client.on(events.NewMessage(pattern=r'^(?:xspam|xslidespam|xspm|xms)\s+(.+)$'))
    async def spam_handler(event):
        if not await check_auth(event):
            return
        text = event.pattern_match.group(1)
        chat_id = event.chat_id
        key = f"spam_{chat_id}_{time.time()}"
        ACTIVE_LOOPS[key] = asyncio.create_task(_spam_worker(chat_id, text, 0.5))
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.NewMessage(pattern=r'^xx\s+(.+)$'))
    async def xx_loop(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        name = event.pattern_match.group(1).strip()
        key = f"lock_{event.chat_id}_{reply.sender_id}"
        ACTIVE_LOOPS[key] = name
        await edit_or_reply(event, f"🔒 Loop active: *{name}*")

    @client.on(events.NewMessage(pattern=r'^(?:xd|xdelay)\s+([0-9.]+)(?:\s+(.+))?$'))
    async def delay_handler(event):
        if not await check_auth(event):
            return
        try:
            delay = float(event.pattern_match.group(1))
            text = event.pattern_match.group(2) or "⏳ ..."
            chat_id = event.chat_id
            key = f"delay_{chat_id}_{time.time()}"
            ACTIVE_LOOPS[key] = asyncio.create_task(
                _spam_worker(chat_id, text, max(0.3, min(delay, 60.0)))
            )
            await edit_or_reply(event, f"⏱️ Delay loop started: `{delay}s`")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")

    @client.on(events.NewMessage(pattern=r'^xswipe\s+(.+)$'))
    async def xswipe_handler(event):
        if not await check_auth(event):
            return
        text = event.pattern_match.group(1)
        chat_id = event.chat_id
        key = f"swipe_{chat_id}_{time.time()}"
        async def worker():
            while True:
                try:
                    m = await client.send_message(chat_id, text)
                    await asyncio.sleep(0.8)
                    try:
                        await m.delete()
                    except Exception:
                        pass
                    await asyncio.sleep(0.4)
                except asyncio.CancelledError:
                    break
                except Exception:
                    await asyncio.sleep(1)
        ACTIVE_LOOPS[key] = asyncio.create_task(worker())
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.NewMessage(pattern=r'^(?:xar|xreply)$'))
    async def start_auto_reply(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        key = f"ar_{event.chat_id}_{reply.sender_id}"
        AUTO_REPLY_TARGETS[key] = 0
        await edit_or_reply(event, "🤖 Auto-reply ON.")

    @client.on(events.NewMessage(pattern=r'^xareply\s+(.+)$'))
    async def xareply_handler(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        text = event.pattern_match.group(1).strip()
        key = f"ar_{event.chat_id}_{reply.sender_id}"
        AUTO_REPLY_TARGETS[key] = 0
        AUTO_REPLY_TEXTS_OVERRIDE[key] = text
        await edit_or_reply(event, "🤖 Auto-reply ON with custom text.")

    # ============ GAALI (NC) FEATURE ============
    @client.on(events.NewMessage(pattern=r'^xnc$'))
    async def xnc_handler(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Kisi user ke message pe reply karke `xnc` bhejo!")
        key = f"nc_{event.chat_id}_{reply.sender_id}"
        AUTO_REPLY_TARGETS[key] = 0
        await edit_or_reply(
            event,
            f"🔞 Gaali mode ON for user `{reply.sender_id}`!\n"
            f"Ab yeh jitna bolega, utni gaali khayega 🤣\n"
            f"Band karne ke liye: `xsnc` (reply karke)"
        )

    @client.on(events.NewMessage(pattern=r'^xsnc$'))
    async def xsnc_handler(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Kisi user ke message pe reply karke `xsnc` bhejo!")
        key = f"nc_{event.chat_id}_{reply.sender_id}"
        AUTO_REPLY_TARGETS.pop(key, None)
        await edit_or_reply(event, f"🛑 Gaali mode OFF for user `{reply.sender_id}`.")

    @client.on(events.NewMessage(pattern=r'^xnc\s+(\d+)$'))
    async def xnc_user_by_id(event):
        if not await check_auth(event):
            return
        try:
            target_id = int(event.pattern_match.group(1))
            text = random.choice(NC_TEXTS)
            await event.reply(f"[{target_id}]\n{text}")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")

    @client.on(events.NewMessage(pattern=r'^xnc1$'))
    async def xnc_random(event):
        if not await check_auth(event):
            return
        await edit_or_reply(event, random.choice(NC_TEXTS))

    # ============ STOP ============
    @client.on(events.NewMessage(pattern=r'^(xsar|xstopareply|xstopreply)$'))
    async def stop_auto_reply(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        key = f"ar_{event.chat_id}_{reply.sender_id}"
        AUTO_REPLY_TARGETS.pop(key, None)
        AUTO_REPLY_TEXTS_OVERRIDE.pop(key, None)
        await edit_or_reply(event, "🛑 Auto-reply stopped.")

    @client.on(events.NewMessage(pattern=r'^(xsspam|xstopspam|xstopslidespam|xstopms)$'))
    async def stop_spam(event):
        if not await check_auth(event):
            return
        chat_id = event.chat_id
        count = 0
        for k, t in list(ACTIVE_LOOPS.items()):
            if k.startswith("spam_") and f"_{chat_id}_" in k:
                if isinstance(t, asyncio.Task):
                    t.cancel()
                del ACTIVE_LOOPS[k]
                count += 1
        await edit_or_reply(event, f"🛑 Stopped {count} spam task(s).")

    @client.on(events.NewMessage(pattern=r'^xstopx$'))
    async def xstopx_handler(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        key = f"lock_{event.chat_id}_{reply.sender_id}"
        if key in ACTIVE_LOOPS:
            del ACTIVE_LOOPS[key]
            await edit_or_reply(event, "🔓 Loop stopped.")
        else:
            await edit_or_reply(event, "ℹ️ No loop for this user.")

    @client.on(events.NewMessage(pattern=r'^xstopswipe$'))
    async def xstopswipe_handler(event):
        if not await check_auth(event):
            return
        chat_id = event.chat_id
        count = 0
        for k, t in list(ACTIVE_LOOPS.items()):
            if k.startswith("swipe_") and f"_{chat_id}_" in k:
                if isinstance(t, asyncio.Task):
                    t.cancel()
                del ACTIVE_LOOPS[k]
                count += 1
        await edit_or_reply(event, f"🛑 Stopped {count} swipe task(s).")

    @client.on(events.NewMessage(pattern=r'^(xstopall|xkillall)$'))
    async def stopall(event):
        nonlocal PFP_ROTATE_TASK
        if not await check_auth(event):
            return
        for t in list(ACTIVE_LOOPS.values()):
            if isinstance(t, asyncio.Task):
                t.cancel()
        ACTIVE_LOOPS.clear()
        AUTO_REPLY_TARGETS.clear()
        AUTO_REPLY_TEXTS_OVERRIDE.clear()
        AUTO_REACT_TARGETS.clear()
        for t in list(REACT_ALL_TASKS.values()):
            if isinstance(t, asyncio.Task):
                t.cancel()
        REACT_ALL_TASKS.clear()
        LOCAL_MUTES.clear()
        GLOBAL_MUTES.clear()
        if PFP_ROTATE_TASK:
            PFP_ROTATE_TASK.cancel()
            PFP_ROTATE_TASK = None
        await edit_or_reply(event, "🛑 All tasks stopped.")

    # ============ REACTION LAB ============
    @client.on(events.NewMessage(pattern=r'^(xart|xreact)\s*(.*)$'))
    async def start_auto_react(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        emoji = (event.pattern_match.group(2) or "").strip() or "🔥"
        key = f"art_{event.chat_id}_{reply.sender_id}"
        AUTO_REACT_TARGETS[key] = emoji
        await edit_or_reply(event, f"❤️ Auto-react on: {emoji}")

    @client.on(events.NewMessage(pattern=r'^(xsart|xstopreact)$'))
    async def stop_auto_react(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        key = f"art_{event.chat_id}_{reply.sender_id}"
        AUTO_REACT_TARGETS.pop(key, None)
        await edit_or_reply(event, "🛑 Auto-react stopped.")

    @client.on(events.NewMessage(pattern=r'^xminereact\s+(.+)$'))
    async def xminereact_handler(event):
        if not await check_auth(event):
            return
        emoji = event.pattern_match.group(1).strip()
        msg_id = event.id
        chat_id = event.chat_id
        key = f"minereact_{chat_id}_{msg_id}"
        async def worker():
            while True:
                try:
                    await client(SendReactionRequest(
                        peer=chat_id, msg_id=msg_id,
                        reaction=[ReactionEmoji(emoticon=emoji)]
                    ))
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass
                await asyncio.sleep(0.7)
        REACT_ALL_TASKS[key] = asyncio.create_task(worker())
        await edit_or_reply(event, f"🎭 Reacting `{emoji}` on your message.")

    @client.on(events.NewMessage(pattern=r'^xreactall\s+(.+)$'))
    async def xreactall_handler(event):
        if not await check_auth(event):
            return
        emoji = event.pattern_match.group(1).strip()
        chat_id = event.chat_id
        key = f"reactall_{chat_id}_{time.time()}"
        async def worker():
            try:
                async for m in client.iter_messages(chat_id, limit=50):
                    try:
                        await client(SendReactionRequest(
                            peer=chat_id, msg_id=m.id,
                            reaction=[ReactionEmoji(emoticon=emoji)]
                        ))
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        pass
                    await asyncio.sleep(0.7)
            except Exception:
                pass
        REACT_ALL_TASKS[key] = asyncio.create_task(worker())
        await edit_or_reply(event, f"🎭 Reacting `{emoji}` on recent 50 messages.")

    @client.on(events.NewMessage(pattern=r'^xstopminereact$'))
    async def xstopminereact_handler(event):
        if not await check_auth(event):
            return
        chat_id = event.chat_id
        count = 0
        for k, t in list(REACT_ALL_TASKS.items()):
            if k.startswith("minereact_") and f"_{chat_id}_" in k:
                if isinstance(t, asyncio.Task):
                    t.cancel()
                del REACT_ALL_TASKS[k]
                count += 1
        await edit_or_reply(event, f"🛑 Stopped {count} minereact task(s).")

    @client.on(events.NewMessage(pattern=r'^xstopreactall$'))
    async def xstopreactall_handler(event):
        if not await check_auth(event):
            return
        chat_id = event.chat_id
        count = 0
        for k, t in list(REACT_ALL_TASKS.items()):
            if k.startswith("reactall_") and f"_{chat_id}_" in k:
                if isinstance(t, asyncio.Task):
                    t.cancel()
                del REACT_ALL_TASKS[k]
                count += 1
        await edit_or_reply(event, f"🛑 Stopped {count} reactall task(s).")

    # ============ GROUP SHIELD ============
    async def _target_user(event):
        reply = await event.get_reply_message()
        if reply and reply.sender_id:
            return reply.sender_id
        return None

    async def _delete_cmd(event):
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.NewMessage(pattern=r'^xkick$'))
    async def xkick_handler(event):
        if not await check_auth(event):
            return
        uid = await _target_user(event)
        if not uid:
            return await edit_or_reply(event, "❌ Reply to a user!")
        try:
            await client.kick_participant(event.chat_id, uid)
            await edit_or_reply(event, "👢 Kicked.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")
        await _delete_cmd(event)

    @client.on(events.NewMessage(pattern=r'^xban$'))
    async def xban_handler(event):
        if not await check_auth(event):
            return
        uid = await _target_user(event)
        if not uid:
            return await edit_or_reply(event, "❌ Reply to a user!")
        try:
            await client.edit_permissions(event.chat_id, uid, view_messages=False)
            await edit_or_reply(event, "⛔ Banned.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")
        await _delete_cmd(event)

    @client.on(events.NewMessage(pattern=r'^xunban$'))
    async def xunban_handler(event):
        if not await check_auth(event):
            return
        uid = await _target_user(event)
        if not uid:
            return await edit_or_reply(event, "❌ Reply to a user!")
        try:
            await client.edit_permissions(event.chat_id, uid, view_messages=True)
            await edit_or_reply(event, "✅ Unbanned.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")
        await _delete_cmd(event)

    @client.on(events.NewMessage(pattern=r'^xsilence$'))
    async def xsilence_handler(event):
        if not await check_auth(event):
            return
        uid = await _target_user(event)
        if not uid:
            return await edit_or_reply(event, "❌ Reply to a user!")
        try:
            await client.edit_permissions(event.chat_id, uid, send_messages=False)
            await edit_or_reply(event, "🔇 Silenced.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")
        await _delete_cmd(event)

    @client.on(events.NewMessage(pattern=r'^xunsilence$'))
    async def xunsilence_handler(event):
        if not await check_auth(event):
            return
        uid = await _target_user(event)
        if not uid:
            return await edit_or_reply(event, "❌ Reply to a user!")
        try:
            await client.edit_permissions(event.chat_id, uid, send_messages=True)
            await edit_or_reply(event, "🔊 Unsilenced.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")
        await _delete_cmd(event)

    @client.on(events.NewMessage(pattern=r'^xmuteall$'))
    async def xmuteall_handler(event):
        if not await check_auth(event):
            return
        try:
            rights = ChatBannedRights(
                until_date=None, send_messages=True,
                send_media=True, send_stickers=True,
                send_gifs=True, send_games=True,
                send_inline=True, embed_links=True
            )
            await client.edit_permissions(event.chat_id, rights=rights)
            await edit_or_reply(event, "🔇 Group muted.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")

    @client.on(events.NewMessage(pattern=r'^xunmuteall$'))
    async def xunmuteall_handler(event):
        if not await check_auth(event):
            return
        try:
            rights = ChatBannedRights(until_date=None)
            await client.edit_permissions(event.chat_id, rights=rights)
            await edit_or_reply(event, "🔊 Group unmuted.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")

    @client.on(events.NewMessage(pattern=r'^xprotect$'))
    async def xprotect_handler(event):
        if not await check_auth(event):
            return
        PROTECTED_CHATS.add(event.chat_id)
        await edit_or_reply(event, "🛡️ Protection ON.")

    @client.on(events.NewMessage(pattern=r'^xunprotect$'))
    async def xunprotect_handler(event):
        if not await check_auth(event):
            return
        PROTECTED_CHATS.discard(event.chat_id)
        await edit_or_reply(event, "🛡️ Protection OFF.")

    @client.on(events.NewMessage(pattern=r'^xpromote$'))
    async def xpromote_handler(event):
        if not await check_auth(event):
            return
        uid = await _target_user(event)
        if not uid:
            return await edit_or_reply(event, "❌ Reply to a user!")
        try:
            rights = ChatAdminRights(
                change_info=True, delete_messages=True, ban_users=True,
                invite_users=True, pin_messages=True, manage_call=True,
                add_admins=False, anonymous=False, manage_topics=True,
                post_messages=True, edit_messages=True, other=True
            )
            await client(EditAdminRequest(channel=event.chat_id, user_id=uid,
                                          admin_rights=rights, rank="Admin"))
            await edit_or_reply(event, "👑 Promoted.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")
        await _delete_cmd(event)

    @client.on(events.NewMessage(pattern=r'^xdemote$'))
    async def xdemote_handler(event):
        if not await check_auth(event):
            return
        uid = await _target_user(event)
        if not uid:
            return await edit_or_reply(event, "❌ Reply to a user!")
        try:
            await client(EditAdminRequest(channel=event.chat_id, user_id=uid,
                                          admin_rights=ChatAdminRights(), rank=""))
            await edit_or_reply(event, "⬇️ Demoted.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")
        await _delete_cmd(event)

    LOCK_TYPES = {
        "messages": dict(send_messages=True),
        "media":    dict(send_media=True),
        "stickers": dict(send_stickers=True),
        "gifs":     dict(send_gifs=True),
        "games":    dict(send_games=True),
        "inline":   dict(send_inline=True),
        "links":    dict(embed_links=True),
        "all":      dict(send_messages=True, send_media=True, send_stickers=True,
                         send_gifs=True, send_games=True, send_inline=True,
                         embed_links=True),
    }

    @client.on(events.NewMessage(pattern=r'^xlock\s+(\w+)$'))
    async def xlock_handler(event):
        if not await check_auth(event):
            return
        kind = event.pattern_match.group(1).lower()
        if kind not in LOCK_TYPES:
            return await edit_or_reply(event, f"❌ Types: {', '.join(LOCK_TYPES)}")
        try:
            rights = ChatBannedRights(until_date=None, **LOCK_TYPES[kind])
            await client.edit_permissions(event.chat_id, rights=rights)
            LOCKED_RIGHTS.setdefault(event.chat_id, set()).add(kind)
            await edit_or_reply(event, f"🔒 Locked: {kind}")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")

    @client.on(events.NewMessage(pattern=r'^xunlock\s+(\w+)$'))
    async def xunlock_handler(event):
        if not await check_auth(event):
            return
        kind = event.pattern_match.group(1).lower()
        try:
            kwargs = {k: False for k in LOCK_TYPES.get(kind, {}).keys()}
            rights = ChatBannedRights(until_date=None, **kwargs)
            await client.edit_permissions(event.chat_id, rights=rights)
            if event.chat_id in LOCKED_RIGHTS:
                LOCKED_RIGHTS[event.chat_id].discard(kind)
            await edit_or_reply(event, f"🔓 Unlocked: {kind}")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")

    @client.on(events.NewMessage(pattern=r'^xlocks$'))
    async def xlocks_handler(event):
        if not await check_auth(event):
            return
        cur = LOCKED_RIGHTS.get(event.chat_id, set())
        if not cur:
            return await edit_or_reply(event, "🔓 Nothing locked here.")
        await edit_or_reply(event, "🔒 Locked: " + ", ".join(sorted(cur)))

    @client.on(events.NewMessage(pattern=r'^xjoin\s+(.+)$'))
    async def xjoin_handler(event):
        if not await check_auth(event):
            return
        link = event.pattern_match.group(1).strip()
        try:
            await client(JoinChannelRequest(link))
            await edit_or_reply(event, "✅ Joined.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")

    @client.on(events.NewMessage(pattern=r'^xleave$'))
    async def xleave_handler(event):
        if not await check_auth(event):
            return
        try:
            await client.delete_dialog(event.chat_id)
            await edit_or_reply(event, "👋 Left.")
        except Exception as e:
            await edit_or_reply(event, f"❌ {e}")

    # ============ CLEANER ============
    @client.on(events.NewMessage(pattern=r'^xdelmine$'))
    async def delmine_handler(event):
        if not await check_auth(event):
            return
        chat_id = event.chat_id
        cmd_id = event.id
        deleted = 0
        batch = []
        async def flush():
            nonlocal batch, deleted
            if not batch:
                return
            ids = batch
            batch = []
            while True:
                try:
                    await client.delete_messages(chat_id, ids, revoke=True)
                    deleted += len(ids)
                    return
                except FloodWaitError as w:
                    await asyncio.sleep(w.seconds + 1)
        try:
            async for m in client.iter_messages(chat_id, from_user='me'):
                if m.out and m.id != cmd_id:
                    batch.append(m.id)
                    if len(batch) >= 100:
                        await flush()
            await flush()
            await client.send_message(chat_id, f"🗑️ Deleted {deleted} of your messages.")
        except Exception as e:
            await client.send_message(chat_id, f"❌ Delmine stopped: {e}")

    @client.on(events.NewMessage(pattern=r'^xdelall$'))
    async def xdelall_handler(event):
        if not await check_auth(event):
            return
        try:
            rights = await client.get_permissions(event.chat_id, "me")
            if not getattr(rights, "delete_messages", False):
                return await edit_or_reply(event, "❌ Need delete permission.")
        except Exception as e:
            return await edit_or_reply(event, f"❌ {e}")
        chat_id = event.chat_id
        cmd_id = event.id
        deleted = 0
        batch = []
        async def flush():
            nonlocal batch, deleted
            if not batch:
                return
            ids = batch
            batch = []
            while True:
                try:
                    await client.delete_messages(chat_id, ids, revoke=True)
                    deleted += len(ids)
                    return
                except FloodWaitError as w:
                    await asyncio.sleep(w.seconds + 1)
        try:
            async for m in client.iter_messages(chat_id, limit=500):
                if m.id != cmd_id:
                    batch.append(m.id)
                    if len(batch) >= 100:
                        await flush()
            await flush()
            await client.send_message(chat_id, f"🗑️ Deleted {deleted} messages.")
        except Exception as e:
            await client.send_message(chat_id, f"❌ Delall stopped: {e}")

    @client.on(events.NewMessage(pattern=r'^xpurge$'))
    async def xpurge_handler(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a message to purge from it to here.")
        chat_id = event.chat_id
        start_id = reply.id
        end_id = event.id
        try:
            await client.delete_messages(chat_id, list(range(start_id, end_id + 1)), revoke=True)
        except FloodWaitError as w:
            await asyncio.sleep(w.seconds + 1)
            try:
                await client.delete_messages(chat_id, list(range(start_id, end_id + 1)), revoke=True)
            except Exception as e:
                return await client.send_message(chat_id, f"❌ Purge fail: {e}")
        except Exception as e:
            return await client.send_message(chat_id, f"❌ Purge fail: {e}")

    @client.on(events.NewMessage(pattern=r'^xpurgefrom$'))
    async def xpurgefrom_handler(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a start message.")
        PURGE_ANCHOR = getattr(client, "_purge_anchor", {})
        PURGE_ANCHOR[event.chat_id] = reply.id
        client._purge_anchor = PURGE_ANCHOR
        await edit_or_reply(event, f"📍 Purge from set (msg id {reply.id}). Now send `xpurgetto`.")

    @client.on(events.NewMessage(pattern=r'^xpurgetto$'))
    async def xpurgetto_handler(event):
        if not await check_auth(event):
            return
        anchor = getattr(client, "_purge_anchor", {}).get(event.chat_id)
        if not anchor:
            return await edit_or_reply(event, "❌ Use `xpurgefrom` on a message first.")
        chat_id = event.chat_id
        try:
            await client.delete_messages(chat_id, list(range(anchor, event.id + 1)), revoke=True)
        except Exception as e:
            return await client.send_message(chat_id, f"❌ Purge fail: {e}")
        getattr(client, "_purge_anchor", {}).pop(chat_id, None)

    # ============ BOTS CONTROL ============
    @client.on(events.NewMessage(pattern=r'^xaddbots\s*(.*)$'))
    async def save_bots_handler(event):
        if not await check_auth(event):
            return
        nonlocal SAVED_BOTS
        text = event.pattern_match.group(1).strip()
        if not text:
            return await edit_or_reply(event, "⚠️ Example: `xaddbots @bot1 @bot2`")
        SAVED_BOTS = [b if b.startswith('@') else f"@{b}" for b in text.split()]
        await edit_or_reply(event, f"💎 Saved {len(SAVED_BOTS)} bots.")

    @client.on(events.NewMessage(pattern=r'^xlistbots$'))
    async def list_bots_handler(event):
        if not await check_auth(event):
            return
        if not SAVED_BOTS:
            return await edit_or_reply(event, "⚠️ No bots saved.")
        await edit_or_reply(event, "📋 Saved bots:\n" + "\n".join([f"• `{b}`" for b in SAVED_BOTS]))

    @client.on(events.NewMessage(pattern=r'^xdelbots\s*(.*)$'))
    async def xdelbots_handler(event):
        if not await check_auth(event):
            return
        nonlocal SAVED_BOTS
        text = event.pattern_match.group(1).strip()
        if not text:
            SAVED_BOTS = []
            return await edit_or_reply(event, "🗑️ All saved bots cleared.")
        to_remove = [b if b.startswith('@') else f"@{b}" for b in text.split()]
        SAVED_BOTS = [b for b in SAVED_BOTS if b not in to_remove]
        await edit_or_reply(event, f"🗑️ Removed {len(to_remove)}. Now {len(SAVED_BOTS)} left.")

    @client.on(events.NewMessage(pattern=r'^xinvitebots$'))
    async def invite_bots_handler(event):
        if not await check_auth(event):
            return
        if not SAVED_BOTS:
            return await edit_or_reply(event, "⚠️ Use `xaddbots` first.")
        msg = await event.reply("⚡ Adding bots...")
        count = 0
        for b in SAVED_BOTS:
            try:
                ent = await client.get_entity(b)
                if event.is_group:
                    await client(InviteToChannelRequest(event.chat_id, [ent]))
                count += 1
            except Exception:
                pass
            await asyncio.sleep(1.0)
        await msg.edit(f"✅ Added {count} bots.")

    @client.on(events.NewMessage(pattern=r'^xpromotebots$'))
    async def promote_bots_handler(event):
        if not await check_auth(event):
            return
        if not SAVED_BOTS:
            return await edit_or_reply(event, "⚠️ Use `xaddbots` first.")
        msg = await event.reply("👑 Promoting bots...")
        count = 0
        rights = ChatAdminRights(
            change_info=True, post_messages=True, edit_messages=True,
            delete_messages=True, ban_users=True, invite_users=True, pin_messages=True
        )
        for b in SAVED_BOTS:
            try:
                ent = await client.get_entity(b)
                await client(EditAdminRequest(
                    channel=event.chat_id, user_id=ent,
                    admin_rights=rights, rank="HOST"
                ))
                count += 1
            except Exception:
                pass
            await asyncio.sleep(1.0)
        await msg.edit(f"✅ Promoted {count} bots.")

    @client.on(events.NewMessage(pattern=r'^xremovebots$'))
    async def xremovebots_handler(event):
        if not await check_auth(event):
            return
        if not SAVED_BOTS:
            return await edit_or_reply(event, "⚠️ Use `xaddbots` first.")
        msg = await event.reply("👢 Removing bots...")
        count = 0
        for b in SAVED_BOTS:
            try:
                ent = await client.get_entity(b)
                await client.kick_participant(event.chat_id, ent)
                count += 1
            except Exception:
                pass
            await asyncio.sleep(1.0)
        await msg.edit(f"✅ Removed {count} bots.")

    # ============ SUDO ============
    @client.on(events.NewMessage(pattern=r'^xsudo$'))
    async def add_sudo_handler(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        SUDO_USERS.add(reply.sender_id)
        save_sudo_users()
        await edit_or_reply(event, f"👑 Added `{reply.sender_id}` to sudo.")

    @client.on(events.NewMessage(pattern=r'^xdelsudo$'))
    async def del_sudo_handler(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        SUDO_USERS.discard(reply.sender_id)
        save_sudo_users()
        await edit_or_reply(event, "👑 Removed from sudo.")

    @client.on(events.NewMessage(pattern=r'^(?:xlist\s+sudo|xsudolist)$'))
    async def list_sudo_handler(event):
        if not await check_auth(event):
            return
        if not SUDO_USERS:
            return await edit_or_reply(event, "⚠️ No sudo users.")
        await edit_or_reply(event, "👑 Sudo:\n" + "\n".join([f"• `{u}`" for u in SUDO_USERS]))

    # ============ MISC: antidel, watchspam, gpfp ============
    @client.on(events.NewMessage(pattern=r'xantidel\s+(on|off)'))
    async def toggle_antidel(event):
        nonlocal ANTIDEL_ENABLED
        if not await check_auth(event):
            return
        mode = event.pattern_match.group(1).lower()
        ANTIDEL_ENABLED = (mode == "on")
        await edit_or_reply(event, f"🛡️ Anti-Delete: {ANTIDEL_ENABLED}")

    @client.on(events.MessageDeleted())
    async def antidel_handler(event):
        if not ANTIDEL_ENABLED:
            return
        for msg_id in event.deleted_ids:
            if msg_id in ANTIDEL_CACHE:
                cached = ANTIDEL_CACHE[msg_id]
                try:
                    await client.send_message(
                        cached['chat_id'],
                        f"🛡️ [ANTI-DELETE]\n\n{cached['text']}"
                    )
                except Exception:
                    pass

    @client.on(events.NewMessage(pattern=r'xwatchspam(?:\s+(.+)|$)'))
    async def set_watchspam(event):
        if not await check_auth(event):
            return
        args = event.pattern_match.group(1)
        if not args:
            return await edit_or_reply(event, "⚠️ Usage: `xwatchspam 3 5`")
        parts = args.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            lim, sec = int(parts[0]), int(parts[1])
            WATCH_SPAM_CONFIG[event.chat_id] = {"limit": lim, "sec": sec, "logs": {}}
            await edit_or_reply(event, f"👁️ WatchSpam: {lim} msgs / {sec}s")

    @client.on(events.NewMessage(pattern=r'xunwatchspam'))
    async def stop_watchspam(event):
        if not await check_auth(event):
            return
        WATCH_SPAM_CONFIG.pop(event.chat_id, None)
        await edit_or_reply(event, "🛑 WatchSpam disabled.")

    @client.on(events.NewMessage(pattern=r'xsetgpfp'))
    async def set_group_pfp(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply or not reply.media:
            return await edit_or_reply(event, "❌ Reply to an image.")
        msg = await event.reply("🔄 Updating...")
        photo = await reply.download_media()
        try:
            await client(EditPhotoRequest(
                channel=event.chat_id,
                photo=await client.upload_file(photo)
            ))
            await msg.edit("✅ Updated.")
        except Exception as e:
            await msg.edit(f"❌ {e}")

    @client.on(events.NewMessage(pattern=r'xautogpfp\s+([0-9]+)'))
    async def start_auto_gpfp(event):
        nonlocal PFP_ROTATE_TASK
        if not await check_auth(event):
            return
        sec = int(event.pattern_match.group(1))
        if sec < 10:
            return await edit_or_reply(event, "⚠️ Min 10s.")
        reply = await event.get_reply_message()
        if reply and reply.media:
            photo = await reply.download_media()
            GROUP_PFP_POOL.append(photo)
        if not GROUP_PFP_POOL:
            return await edit_or_reply(event, "❌ Reply to image first.")
        async def worker():
            idx = 0
            while True:
                try:
                    p = GROUP_PFP_POOL[idx % len(GROUP_PFP_POOL)]
                    await client(EditPhotoRequest(
                        channel=event.chat_id,
                        photo=await client.upload_file(p)
                    ))
                    idx += 1
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass
                await asyncio.sleep(sec)
        if PFP_ROTATE_TASK:
            PFP_ROTATE_TASK.cancel()
        PFP_ROTATE_TASK = asyncio.create_task(worker())
        await edit_or_reply(event, f"🖼️ Auto PFP every {sec}s.")

    @client.on(events.NewMessage(pattern=r'xstopgpfp'))
    async def stop_auto_gpfp(event):
        nonlocal PFP_ROTATE_TASK
        if not await check_auth(event):
            return
        if PFP_ROTATE_TASK:
            PFP_ROTATE_TASK.cancel()
            PFP_ROTATE_TASK = None
        await edit_or_reply(event, "🛑 Auto PFP stopped.")

    # ============ MEDIA DOWNLOADS ============
    async def fetch_media_url(api_url):
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(api_url, headers=headers, timeout=20) as resp:
                    if resp.status == 200:
                        try:
                            return await resp.json()
                        except Exception:
                            return await resp.text()
            except Exception:
                pass
        return None

    @client.on(events.NewMessage(pattern=r'xig\s+(.+)'))
    async def download_instagram(event):
        if not await check_auth(event):
            return
        url = event.pattern_match.group(1).strip()
        msg = await event.reply("📥 Downloading IG...")
        api = f"https://api.siputzx.my.id/api/d/igdl?url={urllib_parse.quote(url)}"
        res = await fetch_media_url(api)
        media_url = None
        if isinstance(res, dict) and res.get("status") and res.get("data"):
            try:
                media_url = res["data"][0]["url"] if isinstance(res["data"], list) else res["data"].get("url")
            except Exception:
                pass
        if media_url:
            await client.send_file(event.chat_id, media_url, caption="✨ Downloaded.")
            await msg.delete()
        else:
            await msg.edit("❌ Failed.")

    @client.on(events.NewMessage(pattern=r'xpt\s+(.+)'))
    async def download_pinterest(event):
        if not await check_auth(event):
            return
        url = event.pattern_match.group(1).strip()
        msg = await event.reply("📥 Downloading Pinterest...")
        api = f"https://api.siputzx.my.id/api/d/pinterest?url={urllib_parse.quote(url)}"
        res = await fetch_media_url(api)
        media_url = None
        if isinstance(res, dict) and res.get("status") and res.get("data"):
            media_url = res["data"].get("url") or res["data"].get("download")
        if media_url:
            await client.send_file(event.chat_id, media_url, caption="✨ Downloaded.")
            await msg.delete()
        else:
            await msg.edit("❌ Failed.")

    @client.on(events.NewMessage(pattern=r'xfb\s+(.+)'))
    async def download_facebook(event):
        if not await check_auth(event):
            return
        url = event.pattern_match.group(1).strip()
        msg = await event.reply("📥 Downloading FB...")
        api = f"https://api.siputzx.my.id/api/d/fbdl?url={urllib_parse.quote(url)}"
        res = await fetch_media_url(api)
        media_url = None
        if isinstance(res, dict) and res.get("status") and res.get("data"):
            media_url = res["data"].get("hd") or res["data"].get("sd")
        if media_url:
            await client.send_file(event.chat_id, media_url, caption="✨ Downloaded.")
            await msg.delete()
        else:
            await msg.edit("❌ Failed.")

    @client.on(events.NewMessage(pattern=r'xyt\s+(.+)'))
    async def download_youtube(event):
        if not await check_auth(event):
            return
        url = event.pattern_match.group(1).strip()
        msg = await event.reply("📥 Fetching YouTube...")
        api = f"https://api.siputzx.my.id/api/d/youtube?url={urllib_parse.quote(url)}"
        res = await fetch_media_url(api)
        dl_url, title = None, "YouTube Video"
        if isinstance(res, dict) and res.get("status") and res.get("data"):
            data = res["data"]
            dl_url = data.get("dl") or data.get("download") or data.get("url")
            title = data.get("title", "YouTube Video")
        if dl_url:
            await client.send_file(event.chat_id, dl_url, caption=f"🎬 {title}")
            await msg.delete()
        else:
            await msg.edit("❌ Failed.")

    @client.on(events.NewMessage(pattern=r'^xsong\s+(.+)$'))
    async def xsong_handler(event):
        if not await check_auth(event):
            return
        query = event.pattern_match.group(1).strip()
        if not query:
            return await edit_or_reply(event, "🎵 Example: `xsong Tum Hi Ho`")
        status = await event.reply("🔎 Searching song...")
        temp_dir = None
        try:
            import tempfile, glob, shutil
            temp_dir = tempfile.mkdtemp(prefix="shiva_xsong_")
            options = {
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": os.path.join(temp_dir, "song.%(ext)s"),
                "noplaylist": True, "quiet": True, "no_warnings": True,
                "socket_timeout": 40, "retries": 2,
                "max_filesize": 45 * 1024 * 1024,
            }
            cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE", "").strip()
            if cookies_file and os.path.isfile(cookies_file):
                options["cookiefile"] = cookies_file
            def _download_song():
                last_error = None
                for prefix in ("ytsearch1:", "scsearch1:"):
                    try:
                        with yt_dlp.YoutubeDL(options) as ydl:
                            search = ydl.extract_info(f"{prefix}{query}", download=False)
                            entries = (search or {}).get("entries") or []
                            result = next((e for e in entries if e), None)
                            if not result:
                                continue
                            target = result.get("webpage_url") or result.get("url")
                            downloaded = ydl.extract_info(target, download=True)
                            requested = (downloaded or {}).get("requested_downloads") or []
                            candidates = [i.get("filepath") for i in requested if i.get("filepath")]
                            candidates += glob.glob(os.path.join(temp_dir, "song.*"))
                            song_path = next((f for f in candidates if f and os.path.isfile(f) and not f.endswith(('.part', '.ytdl'))), None)
                            if song_path:
                                return song_path, (downloaded or {}).get("title") or query
                    except Exception as exc:
                        last_error = exc
                        for leftover in glob.glob(os.path.join(temp_dir, "song.*")):
                            try:
                                os.remove(leftover)
                            except OSError:
                                pass
                if last_error:
                    raise last_error
                return None, query
            song_path, title = await asyncio.to_thread(_download_song)
            if not song_path:
                await status.edit("❌ Not found.")
                return
            await client.send_file(event.chat_id, song_path, caption=f"🎵 {title}", force_document=False)
            await status.delete()
        except Exception as exc:
            await status.edit(f"❌ {type(exc).__name__}: {str(exc)[:200]}")
        finally:
            if temp_dir:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)

    # ============ MISC FUN COMMANDS ============
    @client.on(events.NewMessage(pattern=r'^(xtictactoe|xrps.*|xroll|xflip|xfact|xai.*|xping|xuptime|xraid)$'))
    async def misc_commands(event):
        if not await check_auth(event):
            return
        text = event.raw_text
        if text.startswith("xping"):
            start = time.time()
            msg = await event.reply("🏓 Pinging...")
            await msg.edit(f"🏓 Pong! {round((time.time()-start)*1000)}ms")
        elif text.startswith("xuptime"):
            await edit_or_reply(event, f"⏳ Uptime: {str(timedelta(seconds=int(time.time()-START_TIME)))}")
        elif text.startswith("xtictactoe"):
            board = ["1","2","3","4","5","6","7","8","9"]
            b = random.choice([i for i in range(9) if board[i].isdigit()])
            board[b] = "❌"
            u = random.choice([i for i in range(9) if board[i].isdigit()])
            board[u] = "⭕"
            msg = (
                f"🎮 TIC-TAC-TOE\n\n"
                f"` {board[0]} ` | ` {board[1]} ` | ` {board[2]} `\n"
                f"` {board[3]} ` | ` {board[4]} ` | ` {board[5]} `\n"
                f"` {board[6]} ` | ` {board[7]} ` | ` {board[8]} `\n\n"
                f"✨ Status: DRAW!"
            )
            await edit_or_reply(event, msg)
        elif text.startswith("xrps"):
            move = text.replace("xrps", "").strip().lower()
            choices = ["stone", "paper", "scissor"]
            if move not in choices:
                return await edit_or_reply(event, "⚠️ Example: `xrps stone`")
            bot = random.choice(choices)
            emojis = {"stone": "🪨 Stone", "paper": "📄 Paper", "scissor": "✂️ Scissor"}
            if move == bot:
                result = "🤝 TIE!"
            elif (move=="stone" and bot=="scissor") or (move=="paper" and bot=="stone") or (move=="scissor" and bot=="paper"):
                result = "🎉 YOU WIN!"
            else:
                result = "🤖 BOT WINS!"
            await edit_or_reply(event, f"✂️ RPS\nYou: {emojis[move]}\nBot: {emojis[bot]}\n\n{result}")
        elif text.startswith("xroll"):
            await edit_or_reply(event, f"🎲 Rolled: `{random.randint(1,6)}`")
        elif text.startswith("xflip"):
            await edit_or_reply(event, f"🪙 {random.choice(['Heads','Tails'])}")
        elif text.startswith("xfact"):
            facts = ["Honey never spoils!", "Octopus has 3 hearts!", "Bananas are berries!"]
            await edit_or_reply(event, f"💡 {random.choice(facts)}")
        elif text.startswith("xraid"):
            await edit_or_reply(event, random.choice(NC_TEXTS))
        elif text.startswith("xai"):
            q = text.replace("xai", "").strip()
            if not q:
                return await edit_or_reply(event, "⚠️ Example: `xai Python kya hai`")
            loading = await event.reply("🧠 Thinking...")
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"https://text.pollinations.ai/{urllib_parse.quote(q)}?model=openai", timeout=15) as r:
                        res = await r.text()
                        await loading.edit(res or "❌ Empty.")
            except Exception:
                await loading.edit("❌ AI server busy.")

    # ============ MONITOR ============
    @client.on(events.NewMessage())
    async def monitor(event):
        try:
            chat_id, sender_id = event.chat_id, event.sender_id
            me = await client.get_me()
            if ANTIDEL_ENABLED and event.text:
                ANTIDEL_CACHE[event.id] = {'chat_id': chat_id, 'text': event.text}
            if chat_id in WATCH_SPAM_CONFIG and sender_id != me.id:
                cfg = WATCH_SPAM_CONFIG[chat_id]
                now = time.time()
                logs = cfg["logs"].get(sender_id, [])
                logs = [t for t in logs if now - t < cfg["sec"]]
                logs.append(now)
                cfg["logs"][sender_id] = logs
                if len(logs) > cfg["limit"]:
                    try:
                        await event.delete()
                        return
                    except Exception:
                        pass
            art_key = f"art_{chat_id}_{sender_id}"
            if art_key in AUTO_REACT_TARGETS:
                try:
                    await client(SendReactionRequest(
                        peer=chat_id, msg_id=event.id,
                        reaction=[ReactionEmoji(emoticon=AUTO_REACT_TARGETS[art_key])]
                    ))
                except Exception:
                    pass
            if sender_id == me.id or event.out:
                return
            if sender_id in GHOSTED:
                return
            if sender_id in AFK_USERS:
                try:
                    await event.reply(f"💤 AFK: {AFK_USERS[sender_id]}")
                except Exception:
                    pass
            if sender_id in GLOBAL_MUTES or (chat_id, sender_id) in LOCAL_MUTES:
                try:
                    await event.delete()
                except Exception:
                    pass
                return
            lock_key = f"lock_{chat_id}_{sender_id}"
            if lock_key in ACTIVE_LOOPS:
                try:
                    await event.reply(ACTIVE_LOOPS[lock_key])
                except Exception:
                    pass
                return
            nc_key = f"nc_{chat_id}_{sender_id}"
            if nc_key in AUTO_REPLY_TARGETS:
                idx = AUTO_REPLY_TARGETS[nc_key]
                try:
                    await event.reply(NC_TEXTS[idx])
                except Exception:
                    pass
                AUTO_REPLY_TARGETS[nc_key] = (idx + 1) % len(NC_TEXTS)
                return
            ar_key = f"ar_{chat_id}_{sender_id}"
            if ar_key in AUTO_REPLY_TARGETS:
                if ar_key in AUTO_REPLY_TEXTS_OVERRIDE:
                    text = AUTO_REPLY_TEXTS_OVERRIDE[ar_key]
                    try:
                        await event.reply(text)
                    except Exception:
                        pass
                else:
                    idx = AUTO_REPLY_TARGETS[ar_key]
                    try:
                        await event.reply(CUSTOM_AUTO_REPLY_TEXTS[idx])
                    except Exception:
                        pass
                    AUTO_REPLY_TARGETS[ar_key] = (idx + 1) % len(CUSTOM_AUTO_REPLY_TEXTS)
        except Exception:
            pass

    @client.on(events.NewMessage(pattern=r'^xm$'))
    async def local_mute(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        LOCAL_MUTES.add((event.chat_id, reply.sender_id))
        await edit_or_reply(event, "🔇 Locally muted.")

    @client.on(events.NewMessage(pattern=r'^xgm$'))
    async def global_mute(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        GLOBAL_MUTES.add(reply.sender_id)
        await edit_or_reply(event, "🔇 Globally muted.")

    @client.on(events.NewMessage(pattern=r'^xsm$'))
    async def stop_mute(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if not reply:
            return await edit_or_reply(event, "❌ Reply to a user!")
        uid = reply.sender_id
        LOCAL_MUTES.discard((event.chat_id, uid))
        GLOBAL_MUTES.discard(uid)
        await edit_or_reply(event, "🔊 Unmuted.")


# ==================== MANAGER BOT ====================
bot = TelegramClient(
    'manager_bot_session',
    API_ID,
    API_HASH,
    connection_retries=-1,
    retry_delay=5
)


async def check_forced_channels(event, user_id):
    if user_id in BANNED_USERS and user_id != ADMIN_CHAT_ID:
        try:
            await event.answer("⛔ Banned.", alert=True)
        except Exception:
            try:
                await event.respond("⛔ Banned.")
            except Exception:
                pass
        return False
    if not FORCED_CHANNELS:
        return True
    not_joined = []
    for ch in FORCED_CHANNELS:
        try:
            clean_ch = ch.strip()
            if "t.me/" in clean_ch:
                clean_ch = "@" + clean_ch.split("/")[-1]
            elif not clean_ch.startswith("@") and not clean_ch.startswith("-"):
                clean_ch = "@" + clean_ch
            entity = await bot.get_entity(clean_ch)
            await bot(GetParticipantRequest(entity, user_id))
        except UserNotParticipantError:
            not_joined.append(ch)
        except Exception:
            pass
    if not_joined:
        buttons = []
        for ch in not_joined:
            ch_clean = ch.replace('https://t.me/', '').replace('t.me/', '').replace('@', '')
            buttons.append([Button.url("📢 JOIN CHANNEL", url=f"https://t.me/{ch_clean}")])
        buttons.append([Button.inline("🔄 CHECK JOIN", data="check_join")])
        buttons.append([Button.inline("🔙 BACK", data="main_menu")])
        try:
            await event.edit("*Please join required channels.*", buttons=buttons)
        except Exception:
            await event.respond("*Please join required channels.*", buttons=buttons)
        return False
    return True


@bot.on(events.NewMessage(pattern=r'^/ban(?:\s+(\d+))?$'))
async def ban_user_command(event):
    if not event.is_private or event.sender_id != ADMIN_CHAT_ID:
        return
    raw_id = event.pattern_match.group(1)
    if not raw_id:
        await event.reply("Usage: /ban USER_ID")
        return
    user_id = int(raw_id)
    if user_id == ADMIN_CHAT_ID:
        await event.reply("❌ Cannot ban owner.")
        return
    BANNED_USERS.add(user_id)
    save_banned_users()
    USER_REG_DATA.pop(user_id, None)
    active_client = ACTIVE_USERBOTS.pop(user_id, None)
    if active_client is not None:
        try:
            await active_client.disconnect()
        except Exception:
            pass
    await event.reply(f"⛔ User {user_id} banned.")


@bot.on(events.NewMessage(pattern=r'^/unban(?:\s+(\d+))?$'))
async def unban_user_command(event):
    if not event.is_private or event.sender_id != ADMIN_CHAT_ID:
        return
    raw_id = event.pattern_match.group(1)
    if not raw_id:
        await event.reply("Usage: /unban USER_ID")
        return
    user_id = int(raw_id)
    if user_id in BANNED_USERS:
        BANNED_USERS.remove(user_id)
        save_banned_users()
        await event.reply(f"✅ {user_id} unbanned.")
    else:
        await event.reply(f"ℹ️ Not banned.")


@bot.on(events.NewMessage(pattern=r'^/banned$'))
async def list_banned_users_command(event):
    if not event.is_private or event.sender_id != ADMIN_CHAT_ID:
        return
    if not BANNED_USERS:
        await event.reply("✅ No banned users.")
        return
    await event.reply("⛔ Banned:\n" + "\n".join(str(u) for u in sorted(BANNED_USERS)))


@bot.on(events.NewMessage(pattern=r'^/terminate(?:\s+(\d+))?$'))
async def terminate_hosted_userbot_command(event):
    if not event.is_private or event.sender_id != ADMIN_CHAT_ID:
        return
    raw_id = event.pattern_match.group(1)
    if not raw_id:
        await event.reply("Usage: /terminate USER_ID")
        return
    user_id = int(raw_id)
    if user_id == ADMIN_CHAT_ID:
        await event.reply("❌ Cannot terminate owner.")
        return
    c = ACTIVE_USERBOTS.get(user_id)
    if c is None:
        await event.reply(f"ℹ️ No active userbot for {user_id}.")
        return
    try:
        await c.disconnect()
    except Exception as exc:
        logger.warning("Disconnect failed for %s: %s", user_id, exc)
    finally:
        ACTIVE_USERBOTS.pop(user_id, None)
    try:
        await bot.send_message(user_id, "🛑 Owner terminated your userbot. Send /start to host again.")
    except Exception:
        pass
    await event.reply(f"✅ Terminated {user_id}.")


@bot.on(events.NewMessage(pattern=r'/start'))
async def bot_start(event):
    if not event.is_private:
        return
    uid = event.sender_id
    sender = await event.get_sender()
    if uid in BANNED_USERS and uid != ADMIN_CHAT_ID:
        await event.reply("⛔ You are banned.")
        return
    if uid in USER_REG_DATA:
        del USER_REG_DATA[uid]
    if uid != ADMIN_CHAT_ID:
        username_str = f"@{sender.username}" if (sender and sender.username) else f"No Username (ID: `{uid}`)"
        try:
            await bot.send_message(
                ADMIN_CHAT_ID,
                f"🆕 New user started:\n👤 {username_str}\n🆔 `{uid}`"
            )
        except Exception:
            pass
    if not await check_forced_channels(event, uid):
        return
    HOSTED_USERS.add(uid)
    HOSTED_USER_ENTITIES[uid] = sender
    if uid == ADMIN_CHAT_ID:
        buttons = [
            [Button.inline("ʜᴏꜱᴛ ʏᴏᴜʀ ʙᴏᴛꜱ 🎀", data="host_bot")],
            [Button.inline("ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ ➕", data="add_channel"),
             Button.inline("ʀᴇᴍᴏᴠᴇ ᴄʜᴀɴɴᴇʟ ➖", data="remove_channel")],
            [Button.inline("ᴠɪᴇᴡ ᴄʜᴀɴɴᴇʟꜱ 📋", data="list_channels"),
             Button.inline("ʟɪꜱᴛ ᴍᴇᴍʙᴇʀꜱ 👥", data="list_members")],
            [Button.inline("ɢᴜɪᴅᴇ 📖", data="guide"), Button.inline("ʜᴇʟᴘ 🤎", data="help")],
            [Button.inline("ꜱᴜᴘᴘᴏʀᴛ ❤️", data="support")],
            [Button.inline("ʙʀᴏᴀᴅᴄᴀꜱᴛ 📢", data="broadcast")],
            [Button.inline("ʟᴏɢᴏᴜᴛ 💙", data="logout")],
        ]
    else:
        buttons = [
            [Button.inline("ʜᴏꜱᴛ ʏᴏᴜʀ ʙᴏᴛꜱ 🎀", data="host_bot")],
            [Button.inline("ɢᴜɪᴅᴇ 📖", data="guide"), Button.inline("ʜᴇʟᴘ 🤎", data="help")],
            [Button.inline("ꜱᴜᴘᴘᴏʀᴛ ❤️", data="support")],
            [Button.inline("ʟᴏɢᴏᴜᴛ 💙", data="logout")],
        ]
    welcome_text = (
        "✨ Welcome to SHIVA USERBOT! ✨\n\n"
        "Click below to safely deploy your userbot. Your session remains completely secure. 💛"
    )
    await event.reply(welcome_text, buttons=buttons)


@bot.on(events.CallbackQuery(data=b'main_menu'))
async def main_menu_callback(event):
    await event.answer()
    uid = event.sender_id
    if uid in USER_REG_DATA:
        del USER_REG_DATA[uid]
    if not await check_forced_channels(event, uid):
        return
    if uid == ADMIN_CHAT_ID:
        buttons = [
            [Button.inline("ʜᴏꜱᴛ ʏᴏᴜʀ ʙᴏᴛꜱ 🎀", data="host_bot")],
            [Button.inline("ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ ➕", data="add_channel"),
             Button.inline("ʀᴇᴍᴏᴠᴇ ᴄʜᴀɴɴᴇʟ ➖", data="remove_channel")],
            [Button.inline("ᴠɪᴇᴡ ᴄʜᴀɴɴᴇʟꜱ 📋", data="list_channels"),
             Button.inline("ʟɪꜱᴛ ᴍᴇᴍʙᴇʀꜱ 👥", data="list_members")],
            [Button.inline("ɢᴜɪᴅᴇ 📖", data="guide"), Button.inline("ʜᴇʟᴘ 🤎", data="help")],
            [Button.inline("ꜱᴜᴘᴘᴏʀᴛ ❤️", data="support")],
            [Button.inline("ʙʀᴏᴀᴅᴄᴀꜱᴛ 📢", data="broadcast")],
            [Button.inline("ʟᴏɢᴏᴜᴛ 💙", data="logout")],
        ]
    else:
        buttons = [
            [Button.inline("ʜᴏꜱᴛ ʏᴏᴜʀ ʙᴏᴛꜱ 🎀", data="host_bot")],
            [Button.inline("ɢᴜɪᴅᴇ 📖", data="guide"), Button.inline("ʜᴇʟᴘ 🤎", data="help")],
            [Button.inline("ꜱᴜᴘᴘᴏʀᴛ ❤️", data="support")],
            [Button.inline("ʟᴏɢᴏᴜᴛ 💙", data="logout")],
        ]
    welcome_text = (
        "✨ Welcome to SHIVA BOT! ✨\n\n"
        "Click below to safely deploy your userbot. 💛"
    )
    try:
        await event.edit(welcome_text, buttons=buttons)
    except Exception:
        await event.respond(welcome_text, buttons=buttons)


@bot.on(events.CallbackQuery(data=b'host_bot'))
async def host_callback(event):
    await event.answer()
    uid = event.sender_id
    if not await check_forced_channels(event, uid):
        return
    USER_REG_DATA[uid] = {'step': 'api_id'}
    buttons = [[Button.inline("❌ CANCEL / BACK 💎", data="main_menu")]]
    await event.edit(
        "👋 Welcome to SHIVA USERBOT! 🚀\n\n👉 Send your API ID (number):",
        buttons=buttons
    )


@bot.on(events.CallbackQuery(data=b'check_join'))
async def check_join_callback(event):
    await event.answer("Checking...")
    uid = event.sender_id
    if await check_forced_channels(event, uid):
        await event.edit(
            "*Thank you for joining!* ✨\n\nWelcome!",
            buttons=[[Button.inline("🔙 BACK", data="main_menu")]]
        )
    else:
        await event.answer("❌ Join all channels first!", alert=True)


@bot.on(events.CallbackQuery(data=b'guide'))
async def guide_callback(event):
    await event.answer()
    uid = event.sender_id
    if not await check_forced_channels(event, uid):
        return
    await event.edit(
        "📖 *GUIDE*\n\nGet API ID and HASH from ` my.telegram.org ` ✨",
        buttons=[[Button.inline("🔙 BACK", data="main_menu")]]
    )


@bot.on(events.CallbackQuery(data=b'help'))
async def help_callback(event):
    await event.answer()
    uid = event.sender_id
    if not await check_forced_channels(event, uid):
        return
    await event.edit(
        "🤎 *HELP*\n\nContact support for hosting issues. @cxxwio ✨",
        buttons=[[Button.inline("🔙 BACK", data="main_menu")]]
    )


@bot.on(events.CallbackQuery(data=b'support'))
async def support_callback(event):
    await event.answer()
    uid = event.sender_id
    if not await check_forced_channels(event, uid):
        return
    USER_REG_DATA[uid] = {'step': 'support_msg'}
    await event.edit(
        "❤️ *Please type your issue:*",
        buttons=[[Button.inline("🔙 BACK", data="main_menu")]]
    )


@bot.on(events.CallbackQuery(data=b'add_channel'))
async def add_channel_callback(event):
    if event.sender_id != ADMIN_CHAT_ID:
        await event.answer("❌ Not owner!", alert=True)
        return
    await event.answer()
    USER_REG_DATA[event.sender_id] = {'step': 'add_channel_input'}
    await event.edit(
        "➕ *Send channel username or link:*",
        buttons=[[Button.inline("🔙 BACK", data="main_menu")]]
    )


@bot.on(events.CallbackQuery(data=b'remove_channel'))
async def remove_channel_callback(event):
    if event.sender_id != ADMIN_CHAT_ID:
        await event.answer("❌ Not owner!", alert=True)
        return
    await event.answer()
    if not FORCED_CHANNELS:
        await event.edit(
            "⚠️ No channels added!",
            buttons=[[Button.inline("🔙 BACK", data="main_menu")]]
        )
        return
    USER_REG_DATA[event.sender_id] = {'step': 'remove_channel_input'}
    ch_list = "\n".join([f"• `{ch}`" for ch in FORCED_CHANNELS])
    await event.edit(
        f"➖ *Current:*\n{ch_list}\n\n👉 *Send channel to remove:*",
        buttons=[[Button.inline("🔙 BACK", data="main_menu")]]
    )


@bot.on(events.CallbackQuery(data=b'list_channels'))
async def list_channels_callback(event):
    if event.sender_id != ADMIN_CHAT_ID:
        await event.answer("❌ Not owner!", alert=True)
        return
    await event.answer()
    if not FORCED_CHANNELS:
        ch_text = "⚠️ *No forced channels added!*"
    else:
        ch_list = "\n".join([f"{i+1}. `{ch}`" for i, ch in enumerate(FORCED_CHANNELS)])
        ch_text = f"📋 *Total ({len(FORCED_CHANNELS)}):*\n\n{ch_list}"
    await event.edit(ch_text, buttons=[[Button.inline("🔙 BACK", data="main_menu")]])


@bot.on(events.CallbackQuery(data=b'list_members'))
async def list_members_callback(event):
    if event.sender_id != ADMIN_CHAT_ID:
        await event.answer("❌ Owner only!", alert=True)
        return
    await event.answer()
    if not HOSTED_USERS:
        member_text = "⚠️ *No users hosted yet!*"
    else:
        lines = []
        for i, uid in enumerate(HOSTED_USERS, 1):
            s = HOSTED_USER_ENTITIES.get(uid)
            if s and s.username:
                lines.append(f"{i}. @{s.username} (ID: `{uid}`)")
            else:
                lines.append(f"{i}. No Username (ID: `{uid}`)")
        member_text = f"👥 *Hosted ({len(HOSTED_USERS)}):*\n\n" + "\n".join(lines)
    await event.edit(member_text, buttons=[[Button.inline("🔙 BACK", data="main_menu")]])


@bot.on(events.CallbackQuery(data=b'broadcast'))
async def broadcast_callback(event):
    if event.sender_id != ADMIN_CHAT_ID:
        await event.answer("❌ Not owner!", alert=True)
        return
    await event.answer()
    USER_REG_DATA[event.sender_id] = {'step': 'broadcast_msg'}
    await event.edit(
        "📢 *Type broadcast message:*",
        buttons=[[Button.inline("🔙 BACK", data="main_menu")]]
    )


@bot.on(events.CallbackQuery(data=b'logout'))
async def logout_callback(event):
    await event.answer()
    uid = event.sender_id
    if uid in USER_REG_DATA:
        del USER_REG_DATA[uid]
    await event.edit(
        "💙 *Logged out. Send `/start` to return.* ✨",
        buttons=[[Button.inline("🔙 BACK", data="main_menu")]]
    )


@bot.on(events.ChatAction)
async def monitor_channel_leaves(event):
    if not event.user_left and not event.user_kicked:
        return
    user_id = event.user_id
    if user_id in HOSTED_USERS and user_id != ADMIN_CHAT_ID:
        for ch in FORCED_CHANNELS:
            try:
                clean_ch = ch.strip()
                if "t.me/" in clean_ch:
                    clean_ch = "@" + clean_ch.split("/")[-1]
                elif not clean_ch.startswith("@") and not clean_ch.startswith("-"):
                    clean_ch = "@" + clean_ch
                entity = await bot.get_entity(clean_ch)
                if event.chat_id == entity.id:
                    HOSTED_USERS.discard(user_id)
                    HOSTED_USER_ENTITIES.pop(user_id, None)
                    try:
                        await bot.send_message(
                            user_id,
                            "*You left a mandatory channel! Session stopped. Rejoin and /start.* 🛑"
                        )
                    except Exception:
                        pass
                    break
            except Exception:
                continue


@bot.on(events.NewMessage())
async def bot_wizard(event):
    if not event.is_private:
        return
    uid = event.sender_id
    text = event.raw_text.strip()
    if text.startswith('/start'):
        if uid in USER_REG_DATA:
            del USER_REG_DATA[uid]
        return
    if uid not in USER_REG_DATA:
        return
    state = USER_REG_DATA[uid]['step']

    if state == 'add_channel_input':
        if uid != ADMIN_CHAT_ID:
            del USER_REG_DATA[uid]
            return
        ch_input = text.strip()
        if "t.me/" in ch_input:
            ch = "@" + ch_input.split("/")[-1]
        else:
            ch = ch_input if ch_input.startswith('@') else f"@{ch_input}"
        if ch not in FORCED_CHANNELS:
            FORCED_CHANNELS.append(ch)
        await event.reply(f"✅ `{ch}` added!", buttons=[[Button.inline("🔙 BACK", data="main_menu")]])
        del USER_REG_DATA[uid]
        return

    if state == 'remove_channel_input':
        if uid != ADMIN_CHAT_ID:
            del USER_REG_DATA[uid]
            return
        ch_input = text.strip()
        if "t.me/" in ch_input:
            ch = "@" + ch_input.split("/")[-1]
        else:
            ch = ch_input if ch_input.startswith('@') else f"@{ch_input}"
        if ch in FORCED_CHANNELS:
            FORCED_CHANNELS.remove(ch)
            await event.reply(f"✅ `{ch}` removed!")
        else:
            await event.reply(f"❌ `{ch}` not found!")
        del USER_REG_DATA[uid]
        return

    if state == 'broadcast_msg':
        if uid != ADMIN_CHAT_ID:
            del USER_REG_DATA[uid]
            return
        await event.reply("⏳ Broadcasting...")
        count = 0
        for target_id in list(HOSTED_USERS):
            if target_id == ADMIN_CHAT_ID:
                continue
            try:
                await bot.send_message(target_id, f"📢\n\n{text}")
                count += 1
            except Exception:
                pass
        await event.reply(f"✅ Sent to {count} users.", buttons=[[Button.inline("🔙 BACK", data="main_menu")]])
        del USER_REG_DATA[uid]
        return

    if state == 'support_msg':
        sender = await event.get_sender()
        username_str = f"@{sender.username}" if (sender and sender.username) else f"No Username (ID: `{uid}`)"
        try:
            await bot.send_message(
                ADMIN_CHAT_ID,
                f"🚨 Support from {username_str}:\n💬 {text}"
            )
            await event.reply("✅ Message sent to owner.", buttons=[[Button.inline("🔙 BACK", data="main_menu")]])
        except Exception as e:
            await event.reply(f"❌ Error: {e}")
        del USER_REG_DATA[uid]
        return

    if state == 'api_id':
        try:
            USER_REG_DATA[uid]['api_id'] = int(text)
            USER_REG_DATA[uid]['step'] = 'api_hash'
            msg_text, entities = get_wizard_text_and_entities(f"✅ API ID saved!\n\n👉 Send your API HASH:")
            await event.reply(msg_text, formatting_entities=entities, buttons=[[Button.inline("❌ CANCEL / BACK 💎", data="main_menu")]])
        except ValueError:
            await event.reply("❌ API ID must be a number!")

    elif state == 'api_hash':
        USER_REG_DATA[uid]['api_hash'] = text
        USER_REG_DATA[uid]['step'] = 'phone'
        msg_text, entities = get_wizard_text_and_entities(f"✅ API HASH saved!\n\n👉 Send phone with country code (+91...):")
        await event.reply(msg_text, formatting_entities=entities, buttons=[[Button.inline("❌ CANCEL / BACK 💎", data="main_menu")]])

    elif state == 'phone':
        USER_REG_DATA[uid]['phone'] = text
        msg = await event.reply("⏳ Connecting...")
        try:
            c = TelegramClient(StringSession(), USER_REG_DATA[uid]['api_id'], USER_REG_DATA[uid]['api_hash'])
            await c.connect()
            sent = await c.send_code_request(text)
            USER_REG_DATA[uid]['client'] = c
            USER_REG_DATA[uid]['hash'] = sent.phone_code_hash
            USER_REG_DATA[uid]['step'] = 'otp'
            msg_text, entities = get_wizard_text_and_entities(f"✅ OTP sent!\n\n👉 Send OTP:")
            await msg.edit(msg_text, formatting_entities=entities, buttons=[[Button.inline("❌ CANCEL / BACK 💎", data="main_menu")]])
        except Exception as e:
            await msg.edit(f"❌ Error: {e}",
                           buttons=[[Button.inline("❌ CANCEL / BACK 💎", data="main_menu")]])
            if uid in USER_REG_DATA:
                del USER_REG_DATA[uid]

    elif state == 'otp':
        otp_text = text.replace(" ", "")
        msg = await event.reply("⏳ Verifying OTP...")
        data = USER_REG_DATA[uid]
        c = data['client']
        try:
            await c.sign_in(data['phone'], otp_text, phone_code_hash=data['hash'])
            register_userbot_handlers(c)
            await c.start()
            ACTIVE_USERBOTS[uid] = c
            HOSTED_USERS.add(uid)
            sender_entity = await event.get_sender()
            HOSTED_USER_ENTITIES[uid] = sender_entity
            msg_text, entities = get_wizard_text_and_entities(f"🎉 Successfully hosted!\n\n👑 Userbot active.\n💬 Send `xshiva` to see menu.")
            await msg.edit(msg_text, formatting_entities=entities, buttons=[[Button.inline("🔙 BACK", data="main_menu")]])
            del USER_REG_DATA[uid]
        except SessionPasswordNeededError:
            USER_REG_DATA[uid]['step'] = '2fa'
            msg_text, entities = get_wizard_text_and_entities(f"🔐 2FA enabled!\n\n👉 Send password:")
            await msg.edit(msg_text, formatting_entities=entities, buttons=[[Button.inline("❌ CANCEL / BACK 💎", data="main_menu")]])
        except Exception as e:
            await msg.edit(f"❌ OTP failed: {e}",
                           buttons=[[Button.inline("❌ CANCEL / BACK 💎", data="main_menu")]])
            if uid in USER_REG_DATA:
                del USER_REG_DATA[uid]

    elif state == '2fa':
        msg = await event.reply("⏳ Verifying 2FA...")
        data = USER_REG_DATA[uid]
        c = data['client']
        try:
            await c.sign_in(password=text)
            register_userbot_handlers(c)
            await c.start()
            ACTIVE_USERBOTS[uid] = c
            HOSTED_USERS.add(uid)
            sender_entity = await event.get_sender()
            HOSTED_USER_ENTITIES[uid] = sender_entity
            msg_text, entities = get_wizard_text_and_entities(f"🎉 2FA verified & hosted!\n\n👑 Active.\n💬 `xshiva` for menu.")
            await msg.edit(msg_text, formatting_entities=entities, buttons=[[Button.inline("🔙 BACK", data="main_menu")]])
            del USER_REG_DATA[uid]
        except Exception as e:
            await msg.edit(f"❌ 2FA failed: {e}",
                           buttons=[[Button.inline("❌ CANCEL / BACK 💎", data="main_menu")]])
            if uid in USER_REG_DATA:
                del USER_REG_DATA[uid]


# ==================== FIXED main() ====================
async def main():
    print("🤖 Manager Bot starting...")
    try:
        await bot.connect()
    except Exception as e:
        print(f"⚠️ Initial connect failed: {e}")
        print("🗑️ Removing stale session and retrying...")
        try:
            await bot.disconnect()
        except Exception:
            pass
        for fname in ("manager_bot_session.session", "manager_bot_session.session-journal"):
            try:
                if os.path.exists(fname):
                    os.remove(fname)
            except Exception:
                pass
        await bot.connect()

    if not await bot.is_user_authorized():
        try:
            await bot.sign_in(bot_token=BOT_TOKEN)
        except Exception as e:
            print(f"❌ Bot sign-in failed: {e}")
            print("   → Check that BOT_TOKEN is still valid with @BotFather")
            raise

    print("✅ Manager Bot online!")
    await bot.run_until_disconnected()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Stopped.")
