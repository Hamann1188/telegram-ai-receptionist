"""Fixed bot texts in the three supported languages, and language detection."""

import re
from typing import Literal

Language = Literal["ru", "uz", "en"]
TELEGRAM_MESSAGE_LIMIT = 4096

GREETING: dict[Language, str] = {
    "ru": (
        "Здравствуйте! Я виртуальный администратор Registan Smile Clinic "
        "(вымышленная клиника для демонстрации).\n\n"
        "Отвечу на вопросы о клинике, подберу свободное время и запишу на приём "
        "или передам разговор сотруднику. Чем могу помочь?"
    ),
    "uz": (
        "Assalomu alaykum! Men Registan Smile Clinic virtual administratoriman "
        "(namoyish uchun o'ylab topilgan klinika).\n\n"
        "Klinika haqidagi savollarga javob beraman, bo'sh vaqtni topib qabulga yozib "
        "qo'yaman yoki sizni xodim bilan bog'layman. Qanday yordam bera olaman?"
    ),
    "en": (
        "Hello! I'm the virtual receptionist of Registan Smile Clinic "
        "(a fictional clinic for this demo).\n\n"
        "I can answer questions about the clinic, find a free time and book an "
        "appointment, or connect you with a staff member. How can I help?"
    ),
}

RATE_LIMITED: dict[Language, str] = {
    "ru": "Слишком много сообщений подряд. Пожалуйста, подождите несколько минут.",
    "uz": "Juda ko'p xabar yubordingiz. Iltimos, bir necha daqiqa kuting.",
    "en": "That's a lot of messages in a row. Please wait a few minutes.",
}

DAILY_LIMIT: dict[Language, str] = {
    "ru": "На сегодня лимит сообщений исчерпан. Пожалуйста, позвоните в клинику: {phone}.",
    "uz": "Bugungi xabarlar limiti tugadi. Iltimos, klinikaga qo'ng'iroq qiling: {phone}.",
    "en": "You've reached today's message limit. Please call the clinic on {phone}.",
}

TOO_LONG: dict[Language, str] = {
    "ru": "Сообщение слишком длинное. Пожалуйста, сократите его до {limit} символов.",
    "uz": "Xabar juda uzun. Iltimos, uni {limit} belgigacha qisqartiring.",
    "en": "That message is too long. Please keep it under {limit} characters.",
}

UNSUPPORTED: dict[Language, str] = {
    "ru": "Пока я понимаю только текстовые сообщения. Напишите, пожалуйста, текстом.",
    "uz": "Hozircha faqat matnli xabarlarni tushunaman. Iltimos, matn bilan yozing.",
    "en": "For now I can only read text messages. Please type your question.",
}

OPERATOR_MODE: dict[Language, str] = {
    "ru": "Ваш разговор передан сотруднику клиники, он ответит здесь. Чтобы снова "
    "говорить с ботом, отправьте /start.",
    "uz": "Suhbatingiz klinika xodimiga topshirildi, u shu yerda javob beradi. Bot bilan "
    "qayta gaplashish uchun /start yuboring.",
    "en": "Your conversation is with a clinic staff member, who will reply here. To talk "
    "to the bot again, send /start.",
}

FORGET_CONFIRM: dict[Language, str] = {
    "ru": "Удалить историю переписки и ваши данные (имя и телефон)?",
    "uz": "Yozishmalar tarixi va ma'lumotlaringiz (ism va telefon) o'chirilsinmi?",
    "en": "Delete our conversation history and your details (name and phone)?",
}

FORGET_CANCELS_BOOKINGS: dict[Language, str] = {
    "ru": " Ваши предстоящие записи будут отменены: {count}.",
    "uz": " Kelgusi yozuvlaringiz bekor qilinadi: {count}.",
    "en": " Your upcoming appointments will be cancelled: {count}.",
}

FORGET_BUTTONS: dict[Language, tuple[str, str]] = {
    "ru": ("Удалить", "Отмена"),
    "uz": ("O'chirish", "Bekor qilish"),
    "en": ("Delete", "Cancel"),
}

FORGET_DONE: dict[Language, str] = {
    "ru": "Готово: ваши данные удалены.",
    "uz": "Tayyor: ma'lumotlaringiz o'chirildi.",
    "en": "Done: your data has been deleted.",
}

FORGET_KEPT: dict[Language, str] = {
    "ru": "Хорошо, ничего не удалено.",
    "uz": "Yaxshi, hech narsa o'chirilmadi.",
    "en": "OK, nothing was deleted.",
}

NOTHING_STORED: dict[Language, str] = {
    "ru": "У нас нет сохранённых данных о вас.",
    "uz": "Siz haqingizda hech qanday ma'lumot saqlanmagan.",
    "en": "We don't have any data stored about you.",
}

COMMANDS: dict[Language, dict[str, str]] = {
    "ru": {"start": "Начать разговор заново", "forget": "Удалить мои данные"},
    "uz": {"start": "Suhbatni qaytadan boshlash", "forget": "Ma'lumotlarimni o'chirish"},
    "en": {"start": "Start a new conversation", "forget": "Delete my data"},
}

_UZ_CYRILLIC = set("ўқғҳЎҚҒҲ")
# Uzbek Latin: o' / g' (with any apostrophe) or common Uzbek words.
_UZ_LATIN = re.compile(
    r"[ogOG]['ʻʼ‘’`]|\b(?:salom|assalomu|rahmat|qanday|qancha|qachon|qayerda|kerak|bormi|"
    r"yo'q|iltimos|uchun|bilan|men|menga|sizda|tish|tishim|yozil\w*|narx\w*|ertaga|bugun|"
    r"soat|kuni|qabul\w*)\b",
    re.IGNORECASE,
)


def language_from_code(code: str | None) -> Language:
    """Pick a supported language from a Telegram `language_code` such as "ru" or "en-US"."""
    base = (code or "").split("-")[0].lower()
    if base in ("ru", "uz"):
        return base
    return "en"


def detect_language(text: str, fallback: Language) -> Language:
    """The language of a message, for fixed texts and as a hint to the model.

    Cyrillic is Russian unless it has Uzbek-only letters; Latin is Uzbek when it has
    Uzbek markers, otherwise English. Messages without letters keep the fallback.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return fallback
    cyrillic = sum("Ѐ" <= c <= "ӿ" for c in letters)
    if cyrillic * 2 > len(letters):
        return "uz" if any(c in _UZ_CYRILLIC for c in text) else "ru"
    return "uz" if _UZ_LATIN.search(text) else "en"


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split a reply into Telegram-sized parts, preferring paragraph and line breaks."""
    text = text.strip()
    parts: list[str] = []
    while len(text) > limit:
        cut = max(
            text.rfind("\n\n", 0, limit), text.rfind("\n", 0, limit), text.rfind(" ", 0, limit)
        )
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts
