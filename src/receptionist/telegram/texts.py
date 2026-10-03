"""Fixed bot texts in the three supported languages."""

from typing import Literal

Language = Literal["ru", "uz", "en"]

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


def language_from_code(code: str | None) -> Language:
    """Pick a supported language from a Telegram `language_code` such as "ru" or "en-US"."""
    base = (code or "").split("-")[0].lower()
    if base in ("ru", "uz"):
        return base
    return "en"
