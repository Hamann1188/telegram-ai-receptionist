"""Texts for the clinic's admin group, in the language set by RECEPTIONIST_ADMIN_LANGUAGE."""

from receptionist.telegram.texts import Language

BOOKING_CREATED: dict[Language, str] = {
    "ru": "🗓 Новая запись #{id}\n{service}\n{weekday}, {date}, {start}–{end}\n"
    "{name}, {phone}\nЧат: {chat}",
    "uz": "🗓 Yangi yozuv #{id}\n{service}\n{weekday}, {date}, {start}–{end}\n"
    "{name}, {phone}\nChat: {chat}",
    "en": "🗓 New booking #{id}\n{service}\n{weekday}, {date}, {start}–{end}\n"
    "{name}, {phone}\nChat: {chat}",
}

BOOKING_CANCELLED: dict[Language, str] = {
    "ru": "❌ Запись #{id} отменена\n{service}\n{weekday}, {date}, {start}–{end}\n"
    "{name}, {phone}\nЧат: {chat}",
    "uz": "❌ Yozuv #{id} bekor qilindi\n{service}\n{weekday}, {date}, {start}–{end}\n"
    "{name}, {phone}\nChat: {chat}",
    "en": "❌ Booking #{id} cancelled\n{service}\n{weekday}, {date}, {start}–{end}\n"
    "{name}, {phone}\nChat: {chat}",
}

HANDOFF: dict[Language, str] = {
    "ru": "🙋 Пациенту нужен сотрудник ({reason})\nЧат: {chat}\n\n{summary}\n\n"
    "Ответьте на это сообщение (Reply), и ответ уйдёт пациенту.",
    "uz": "🙋 Bemorga xodim kerak ({reason})\nChat: {chat}\n\n{summary}\n\n"
    "Ushbu xabarga javob (Reply) bering, javob bemorga yuboriladi.",
    "en": "🙋 A patient needs a staff member ({reason})\nChat: {chat}\n\n{summary}\n\n"
    "Reply to this message and your answer goes to the patient.",
}

TAKEN_OVER: dict[Language, str] = {
    "ru": "👤 {admin} ведёт чат {chat}. Отвечайте на сообщения этого чата (Reply).",
    "uz": "👤 {admin} {chat} chatini olib boryapti. Ushbu chat xabarlariga javob (Reply) bering.",
    "en": "👤 {admin} took over chat {chat}. Reply to this chat's messages to answer.",
}

PATIENT_MESSAGE: dict[Language, str] = {
    "ru": "💬 {chat}:\n{text}",
    "uz": "💬 {chat}:\n{text}",
    "en": "💬 {chat}:\n{text}",
}

PATIENT_FILE: dict[Language, str] = {
    "ru": "💬 {chat} прислал(а) файл:",
    "uz": "💬 {chat} fayl yubordi:",
    "en": "💬 {chat} sent a file:",
}

RETURNED: dict[Language, str] = {
    "ru": "🤖 {admin} вернул(а) чат {chat} боту.",
    "uz": "🤖 {admin} {chat} chatini botga qaytardi.",
    "en": "🤖 {admin} returned chat {chat} to the bot.",
}

PATIENT_RESTARTED: dict[Language, str] = {
    "ru": "🤖 Пациент {chat} перезапустил бота (/start), разговор снова ведёт бот.",
    "uz": "🤖 Bemor {chat} botni qayta ishga tushirdi (/start), suhbatni yana bot olib boradi.",
    "en": "🤖 Patient {chat} restarted the bot (/start); the bot handles the chat again.",
}

NOT_OPERATOR_MODE: dict[Language, str] = {
    "ru": "Этот чат сейчас ведёт бот. Нажмите «Взять чат», чтобы ответить пациенту.",
    "uz": "Bu chatni hozir bot olib boryapti. Bemorga javob berish uchun «Chatni olish»ni bosing.",
    "en": "The bot is handling this chat. Press “Take over” to answer the patient.",
}

UNKNOWN_CHAT: dict[Language, str] = {
    "ru": "Не знаю, к какому пациенту относится это сообщение, или его данные удалены.",
    "uz": "Bu xabar qaysi bemorga tegishli ekanini bilmayman yoki uning ma'lumotlari o'chirilgan.",
    "en": "I don't know which patient this belongs to, or their data was deleted.",
}

DELIVERY_FAILED: dict[Language, str] = {
    "ru": "Не удалось доставить сообщение: возможно, пациент заблокировал бота.",
    "uz": "Xabarni yetkazib bo'lmadi: bemor botni bloklagan bo'lishi mumkin.",
    "en": "Couldn't deliver the message: the patient may have blocked the bot.",
}

ALREADY_OPERATOR: dict[Language, str] = {
    "ru": "Этот чат уже ведёт сотрудник.",
    "uz": "Bu chatni allaqachon xodim olib boryapti.",
    "en": "A staff member is already handling this chat.",
}

ALREADY_BOT: dict[Language, str] = {
    "ru": "Этот чат уже ведёт бот.",
    "uz": "Bu chatni allaqachon bot olib boryapti.",
    "en": "The bot is already handling this chat.",
}

DONE: dict[Language, str] = {"ru": "Готово", "uz": "Tayyor", "en": "Done"}

TAKE_OVER_BUTTON: dict[Language, str] = {
    "ru": "👤 Взять чат",
    "uz": "👤 Chatni olish",
    "en": "👤 Take over",
}

RETURN_BUTTON: dict[Language, str] = {
    "ru": "🤖 Вернуть боту",
    "uz": "🤖 Botga qaytarish",
    "en": "🤖 Return to bot",
}

REASONS: dict[Language, dict[str, str]] = {
    "ru": {
        "patient_request": "просит человека",
        "complaint": "жалоба",
        "medical_question": "медицинский вопрос",
        "emergency": "СРОЧНО",
        "not_understood": "бот не понял",
        "other": "другое",
    },
    "uz": {
        "patient_request": "odam so'rayapti",
        "complaint": "shikoyat",
        "medical_question": "tibbiy savol",
        "emergency": "SHOSHILINCH",
        "not_understood": "bot tushunmadi",
        "other": "boshqa",
    },
    "en": {
        "patient_request": "asked for a person",
        "complaint": "complaint",
        "medical_question": "medical question",
        "emergency": "URGENT",
        "not_understood": "bot didn't understand",
        "other": "other",
    },
}

WEEKDAYS: dict[Language, tuple[str, ...]] = {
    "ru": ("пн", "вт", "ср", "чт", "пт", "сб", "вс"),
    "uz": ("du", "se", "ch", "pa", "ju", "sh", "ya"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}

CHAT_ID: str = (
    "This chat's id is {id}.\n\n"
    "To make it the clinic's admin group, put this line in .env and restart the bot:\n"
    "RECEPTIONIST_ADMIN_CHAT_ID={id}"
)
