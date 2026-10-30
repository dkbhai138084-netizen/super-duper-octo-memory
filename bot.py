import os
import io
import base64
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
# ============================================================
# 👇👇 NAYA TOKEN YAHAN PASTE KARO (quotes ke andar, bina space ke) 👇👇
BOT_TOKEN_MANUAL = ""
# ☝️☝️ BotFather se Revoke karke jo naya token mile wahi daalna ☝️☝️
# ============================================================

# Agar hosting me env variable BOT_TOKEN set hai to wo pehle use hoga, warna upar wala
BOT_TOKEN = (
    os.environ.get("BOT_TOKEN")
    or os.environ.get("TELEGRAM_TOKEN")
    or os.environ.get("TOKEN")
    or (BOT_TOKEN_MANUAL if BOT_TOKEN_MANUAL != "PASTE_NEW_TOKEN_HERE" else "")
).strip()
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", ""))
API_ID = int(os.environ.get("API_ID", ""))
API_HASH = os.environ.get("API_HASH", "")

USER_REG_DATA = {}
ACTIVE_USERBOTS = {}
HOSTED_USERS = set()
HOSTED_USER_ENTITIES = {}
FORCED_CHANNELS = []

# ---- CREDITS / PLAN SYSTEM ----
CREDITS_FILE = "user_credits.json"
UPI_ID = "Shiva-singhs@fam"
QR_CODE_B64 = "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"
PLANS_TEXT = (
    "💎 *Plans*\n\n"
    "🗓 Weekly — 1 credit — ₹40\n"
    "🗓 Monthly — 3 credit — ₹99\n\n"
    f"👉 Pay via UPI: `{UPI_ID}`\n"
    "✅ Payment ke baad screenshot support me bhejo, credit manually add kiya jayega."
)

def load_credits():
    try:
        with open(CREDITS_FILE, "r", encoding="utf-8") as f:
            return {int(uid): int(c) for uid, c in json.load(f).items()}
    except (OSError, ValueError, TypeError):
        return {}

CREDITS = load_credits()

def save_credits():
    with open(CREDITS_FILE, "w", encoding="utf-8") as f:
        json.dump(CREDITS, f)

def get_credits(uid):
    return CREDITS.get(uid, 0)

def add_credits(uid, amount):
    CREDITS[uid] = get_credits(uid) + amount
    save_credits()
    return CREDITS[uid]

def use_credit(uid):
    if get_credits(uid) <= 0:
        return False
    CREDITS[uid] -= 1
    save_credits()
    return True

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
    client._shiva_raw_send_message = original_send_message
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
            "┏━━━━━━━ 🎮 𝑩𝑨𝑲𝑨 (𝑮𝑨𝑴𝑬)\n┃\n"
            "┃  ❖ `xbaka` — sabhi users pe /kill /bal /rob (reply = sirf ek user)\n"
            "┃  ❖ `xstopbaka` — baka stop\n"
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

    # ============ BAKA (AUTO /kill /bal /rob) ============
    BAKA_TRACK = {}   # chat_id -> {'sent': set, 'replies': set, 'bal_id': int, 'future': Future}
    BAKA_LOCKS = {}   # chat_id -> asyncio.Lock (ek chat me ek time pe ek hi cycle)
    BAKA_DELAY = 5    # seconds between rounds
    BAKA_MSG_GAP = 5  # do messages ke beech kam se kam itne second (Telegram flood se bachne ke liye)

    async def _raw_send(chat_id, text, reply_to=None):
        sender = getattr(client, '_shiva_raw_send_message', client.send_message)
        return await sender(chat_id, text, reply_to=reply_to, parse_mode=None)

    def _baka_parse_amount(text):
        """/bal reply se Coins amount nikalta hai (e.g. 'Coins: $150' -> 150)."""
        if not text:
            return None
        clean = text.replace(",", "")
        m = re.search(r'\$\s*(\d+)', clean)
        if m:
            return int(m.group(1))
        m = re.search(r'\U0001F4B0\D{0,15}?(\d+)', clean)
        return int(m.group(1)) if m else None

    @client.on(events.NewMessage(incoming=True))
    async def baka_listener(event):
        track = BAKA_TRACK.get(event.chat_id)
        if not track or event.reply_to_msg_id not in track['sent']:
            return
        try:
            # game bot ka jo bhi reply hamare command pe aaya, delete list me
            track['replies'].add(event.id)
            fut = track.get('future')
            if fut and not fut.done() and event.reply_to_msg_id == track.get('bal_id'):
                amount = _baka_parse_amount(event.raw_text)
                if amount is not None:
                    fut.set_result(amount)
        except Exception:
            pass

    async def _baka_cycle(chat_id, reply_msg_id):
        """Ek user pe: /kill -> /bal -> /rob <amount>, phir apne + bot ke messages delete."""
        lock = BAKA_LOCKS.setdefault(chat_id, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            track = {'sent': set(), 'replies': set(), 'bal_id': None, 'future': None}
            BAKA_TRACK[chat_id] = track
            try:
                m = await _raw_send(chat_id, "/kill", reply_to=reply_msg_id)
                track['sent'].add(m.id)
                await asyncio.sleep(BAKA_MSG_GAP)

                m = await _raw_send(chat_id, "/bal", reply_to=reply_msg_id)
                track['sent'].add(m.id)
                track['bal_id'] = m.id
                track['future'] = loop.create_future()
                try:
                    amount = await asyncio.wait_for(track['future'], timeout=6)
                except asyncio.TimeoutError:
                    amount = None

                if amount and amount > 0:
                    m = await _raw_send(chat_id, f"/rob {amount}", reply_to=reply_msg_id)
                    track['sent'].add(m.id)

                await asyncio.sleep(1.5)  # bot ke replies aane do
            finally:
                BAKA_TRACK.pop(chat_id, None)
                # pehle apne messages, phir bot ke replies (bot ke liye admin delete right chahiye)
                if track['sent']:
                    try:
                        await client.delete_messages(chat_id, list(track['sent']))
                    except Exception:
                        pass
                if track['replies']:
                    try:
                        await client.delete_messages(chat_id, list(track['replies']))
                    except Exception:
                        pass

    async def _baka_worker(chat_id, reply_msg_id=None):
        """reply_msg_id diya ho to sirf us user pe, warna group ke sabhi users pe."""
        me = await client.get_me()
        while True:
            try:
                if reply_msg_id:
                    await _baka_cycle(chat_id, reply_msg_id)
                else:
                    users = [
                        u async for u in client.iter_participants(chat_id)
                        if not u.bot and not u.deleted and u.id != me.id
                    ]
                    for u in users:
                        target_msg = None
                        async for m in client.iter_messages(chat_id, from_user=u.id, limit=1):
                            target_msg = m
                        if not target_msg:
                            continue  # is user ka koi message nahi mila
                        await _baka_cycle(chat_id, target_msg.id)
                        await asyncio.sleep(BAKA_MSG_GAP)
            except asyncio.CancelledError:
                break
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except Exception:
                await asyncio.sleep(2)
            await asyncio.sleep(BAKA_DELAY)

    @client.on(events.NewMessage(pattern=r'^xbaka$'))
    async def xbaka_handler(event):
        if not await check_auth(event):
            return
        reply = await event.get_reply_message()
        if reply:
            key = f"baka_{event.chat_id}_{reply.sender_id}"
            reply_id = reply.id
        else:
            if not event.is_group:
                return await edit_or_reply(event, "❌ Group me use karo!")
            key = f"baka_{event.chat_id}_all"
            reply_id = None
        old_task = ACTIVE_LOOPS.get(key)
        if isinstance(old_task, asyncio.Task) and not old_task.done():
            return await edit_or_reply(event, "ℹ️ Baka already running.")
        ACTIVE_LOOPS[key] = asyncio.create_task(_baka_worker(event.chat_id, reply_id))
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.NewMessage(pattern=r'^xstopbaka$'))
    async def xstopbaka_handler(event):
        if not await check_auth(event):
            return
        chat_id = event.chat_id
        count = 0
        for k, t in list(ACTIVE_LOOPS.items()):
            if k.startswith("baka_") and f"_{chat_id}_" in k:
                if isinstance(t, asyncio.Task):
                    t.cancel()
                del ACTIVE_LOOPS[k]
                count += 1
        await edit_or_reply(event, f"🛑 Stopped {count} baka task(s).")
        await asyncio.sleep(2)
        try:
            await event.delete()
        except Exception:
            pass

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


@bot.on(events.NewMessage(pattern=r'^/addcredit(?:\s+(\d+)\s+(\d+))?$'))
async def add_credit_command(event):
    if not event.is_private or event.sender_id != ADMIN_CHAT_ID:
        return
    m = event.pattern_match
    if not m.group(1):
        return await event.reply("Usage: /addcredit <user_id> <amount>")
    target, amount = int(m.group(1)), int(m.group(2))
    total = add_credits(target, amount)
    await event.reply(f"✅ {amount} credit add kiya user `{target}` ko.\nAb total: {total}")
    try:
        await bot.send_message(target, f"🎉 Aapko {amount} credit mil gaya! Ab total: {total}\n`/start` bhejo hosting ke liye.")
    except Exception:
        pass


@bot.on(events.NewMessage(pattern=r'^/credits(?:\s+(\d+))?$'))
async def check_credits_command(event):
    if not event.is_private or event.sender_id != ADMIN_CHAT_ID:
        return
    m = event.pattern_match
    if m.group(1):
        target = int(m.group(1))
        return await event.reply(f"User `{target}`: {get_credits(target)} credit")
    if not CREDITS:
        return await event.reply("Koi credit record nahi hai.")
    lines = [f"`{uid}`: {c}" for uid, c in CREDITS.items()]
    await event.reply("💰 *Credits:*\n\n" + "\n".join(lines))


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
            [Button.inline("💎 ʙᴜʏ ᴘʟᴀɴ", data="buy_plan"), Button.inline("👛 ᴍʏ ᴡᴀʟʟᴇᴛ", data="my_credits")],
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
            [Button.inline("💎 ʙᴜʏ ᴘʟᴀɴ", data="buy_plan"), Button.inline("👛 ᴍʏ ᴡᴀʟʟᴇᴛ", data="my_credits")],
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
            [Button.inline("💎 ʙᴜʏ ᴘʟᴀɴ", data="buy_plan"), Button.inline("👛 ᴍʏ ᴡᴀʟʟᴇᴛ", data="my_credits")],
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
            [Button.inline("💎 ʙᴜʏ ᴘʟᴀɴ", data="buy_plan"), Button.inline("👛 ᴍʏ ᴡᴀʟʟᴇᴛ", data="my_credits")],
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
    if uid != ADMIN_CHAT_ID and get_credits(uid) <= 0:
        buttons = [[Button.inline("💎 ʙᴜʏ ᴘʟᴀɴ", data="buy_plan")],
                   [Button.inline("🔙 BACK", data="main_menu")]]
        await event.edit(
            "❌ Aapke paas koi credit nahi hai.\n\n👉 Pehle plan khareedo, phir hosting start hogi.",
            buttons=buttons
        )
        return
    USER_REG_DATA[uid] = {'step': 'api_id'}
    buttons = [[Button.inline("❌ CANCEL / BACK 💎", data="main_menu")]]
    await event.edit(
        "👋 Welcome to SHIVA USERBOT! 🚀\n\n👉 Send your API ID (number):",
        buttons=buttons
    )


PLAN_INFO = {
    "weekly": {"label": "Weekly", "price": 40, "credits": 1},
    "monthly": {"label": "Monthly", "price": 99, "credits": 3},
}

async def generate_upi_qr(amount, plan_label):
    """UPI deep link ke saath dynamic QR banata hai, amount pehle se bhara hota hai."""
    import urllib.parse as _up
    upi_link = (
        "upi://pay?pa=" + _up.quote(UPI_ID) +
        "&pn=" + _up.quote("Shiva Userbot") +
        "&am=" + str(amount) +
        "&cu=INR&tn=" + _up.quote(f"{plan_label} Plan")
    )

    # 1) local 'qrcode' library ho to sabse pehle wahi use karo (offline, fast)
    try:
        import qrcode
        img = qrcode.make(upi_link)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        buf.name = "qr_code.png"
        return buf
    except Exception:
        pass

    # 2) library na ho to online QR generator se banwa lo (internet to bot ke paas hai hi)
    try:
        api_url = "https://api.qrserver.com/v1/create-qr-code/?size=500x500&data=" + _up.quote(upi_link)
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    buf = io.BytesIO(data)
                    buf.name = "qr_code.png"
                    return buf
    except Exception:
        pass

    # 3) dono fail ho to purana static QR (fixed amount) fallback, taaki kam se kam kuch to dikhe
    try:
        qr_bytes = base64.b64decode(QR_CODE_B64)
        buf = io.BytesIO(qr_bytes)
        buf.name = "qr_code.jpg"
        return buf
    except Exception:
        return None


@bot.on(events.CallbackQuery(data=b'buy_plan'))
async def buy_plan_callback(event):
    await event.answer()
    uid = event.sender_id
    if uid in USER_REG_DATA:
        del USER_REG_DATA[uid]
    buttons = [
        [Button.inline("🗓 Weekly — ₹40 (1 credit)", data="vplan_weekly")],
        [Button.inline("🗓 Monthly — ₹99 (3 credit)", data="vplan_monthly")],
        [Button.inline("🔙 BACK", data="main_menu")],
    ]
    text = "💎 *Plans*\n\n👉 Kaunsa plan lena hai, select karo:"
    try:
        await event.edit(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)


@bot.on(events.CallbackQuery(pattern=rb'^vplan_(weekly|monthly)$'))
async def verify_plan_callback(event):
    await event.answer()
    uid = event.sender_id
    plan_key = event.pattern_match.group(1).decode()
    info = PLAN_INFO[plan_key]
    plan_name = f"{info['label']} — ₹{info['price']}"
    USER_REG_DATA[uid] = {'step': 'verify_utr', 'plan_name': plan_name, 'credits_amt': info['credits']}

    qr_file = await generate_upi_qr(info['price'], info['label'])
    caption = (
        f"💎 Plan: *{plan_name}*\n"
        f"💰 Amount: ₹{info['price']}\n\n"
        f"👉 Is QR ko scan karke UPI app se pay karo.\n"
        f"✅ Payment ke baad yahin reply me apna *UTR / Transaction ID* bhejo."
    )
    buttons = [[Button.inline("❌ CANCEL", data="main_menu")]]
    try:
        await event.delete()
    except Exception:
        pass
    if qr_file:
        await bot.send_file(event.chat_id, qr_file, caption=caption, buttons=buttons)
    else:
        await bot.send_message(event.chat_id, caption, buttons=buttons)


@bot.on(events.CallbackQuery(data=b'my_credits'))
async def my_credits_callback(event):
    await event.answer()
    uid = event.sender_id
    bal = get_credits(uid)
    wallet_text = (
        "👛 *My Wallet*\n\n"
        f"💰 Balance: *{bal} credit*\n"
        "📌 1 hosting = 1 credit\n\n"
        "Credit kam ho to Buy Plan se recharge karo."
    )
    buttons = [[Button.inline("💎 ʙᴜʏ ᴘʟᴀɴ", data="buy_plan")],
               [Button.inline("🔙 BACK", data="main_menu")]]
    await event.edit(wallet_text, buttons=buttons)


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
        "📖 *GUIDE*\n\nGet API ID and HASH from `  my.telegram.org ` ✨",
        buttons=[[Button.inline("🔙 BACK", data="main_menu")]]
    )


@bot.on(events.CallbackQuery(data=b'help'))
async def help_callback(event):
    await event.answer()
    uid = event.sender_id
    if not await check_forced_channels(event, uid):
        return
    await event.edit(
        "🤎 *HELP*\n\nContact support for hosting issues. @cxxwioo ✨",
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

    elif state == 'verify_utr':
        if not text:
            await event.reply("👉 UTR / Transaction ID text me bhejo:")
            return
        USER_REG_DATA[uid]['utr'] = text
        USER_REG_DATA[uid]['step'] = 'verify_screenshot'
        await event.reply("✅ UTR save ho gaya!\n\n📸 Ab payment ka *screenshot* bhejo (photo):")

    elif state == 'verify_screenshot':
        if not event.photo:
            await event.reply("📸 Sirf photo bhejo (payment screenshot).")
            return
        data = USER_REG_DATA[uid]
        sender = await event.get_sender()
        uname = f"@{sender.username}" if (sender and sender.username) else f"ID: `{uid}`"
        caption = (
            "🆕 *New Payment Verification*\n\n"
            f"👤 {uname}\n"
            f"🆔 `{uid}`\n"
            f"💎 Plan: {data.get('plan_name')}\n"
            f"🧾 UTR: `{data.get('utr')}`\n\n"
            "Approve karoge to credit auto add ho jayega."
        )
        approve_buttons = [[
            Button.inline("✅ Approve", data=f"approve_{uid}_{data.get('credits_amt')}"),
            Button.inline("❌ Reject", data=f"reject_{uid}"),
        ]]
        try:
            await bot.send_file(ADMIN_CHAT_ID, event.photo, caption=caption, buttons=approve_buttons)
            await event.reply("✅ Payment verification admin ko bhej di hai.\n⏳ Approve hote hi credit mil jayega.")
        except Exception as e:
            await event.reply(f"❌ Error bhejte waqt: {e}")
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
            if uid != ADMIN_CHAT_ID:
                use_credit(uid)
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
            if uid != ADMIN_CHAT_ID:
                use_credit(uid)
            msg_text, entities = get_wizard_text_and_entities(f"🎉 2FA verified & hosted!\n\n👑 Active.\n💬 `xshiva` for menu.")
            await msg.edit(msg_text, formatting_entities=entities, buttons=[[Button.inline("🔙 BACK", data="main_menu")]])
            del USER_REG_DATA[uid]
        except Exception as e:
            await msg.edit(f"❌ 2FA failed: {e}",
                           buttons=[[Button.inline("❌ CANCEL / BACK 💎", data="main_menu")]])
            if uid in USER_REG_DATA:
                del USER_REG_DATA[uid]

@bot.on(events.CallbackQuery(pattern=rb'^approve_(\d+)_(\d+)$'))
async def approve_payment_callback(event):
    if event.sender_id != ADMIN_CHAT_ID:
        return await event.answer("❌ Not allowed", alert=True)
    target_uid = int(event.pattern_match.group(1))
    credits_amt = int(event.pattern_match.group(2))
    total = add_credits(target_uid, credits_amt)
    await event.answer("✅ Approved!")
    try:
        await event.edit(buttons=None)
    except Exception:
        pass
    try:
        await event.respond(f"✅ Approved — {credits_amt} credit added to `{target_uid}` (total: {total})")
    except Exception:
        pass
    try:
        await bot.send_message(
            target_uid,
            f"🎉 Payment Approved!\n\n💰 {credits_amt} credit add ho gaya.\n👛 Total: {total} credit.\n👉 `/start` bhejo hosting ke liye."
        )
    except Exception:
        pass


@bot.on(events.CallbackQuery(pattern=rb'^reject_(\d+)$'))
async def reject_payment_callback(event):
    if event.sender_id != ADMIN_CHAT_ID:
        return await event.answer("❌ Not allowed", alert=True)
    target_uid = int(event.pattern_match.group(1))
    await event.answer("❌ Rejected")
    try:
        await event.edit(buttons=None)
    except Exception:
        pass
    try:
        await event.respond(f"❌ Rejected — `{target_uid}`")
    except Exception:
        pass
    try:
        await bot.send_message(
            target_uid,
            "❌ Aapka payment verify nahi ho saka.\n👉 Sahi UTR aur screenshot ke saath dubara try karo, ya support me contact karo."
        )
    except Exception:
        pass



async def main():
    print("🤖 Manager Bot starting...")
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing! BotFather se naya token lo aur env var BOT_TOKEN me daalo.")
        return
    await bot.start(bot_token=BOT_TOKEN)
    print("✅ Manager Bot online!")
    await bot.run_until_disconnected()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Stopped.")
