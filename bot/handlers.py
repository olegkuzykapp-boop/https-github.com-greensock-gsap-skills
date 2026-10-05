import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from . import db
from .config import ADMIN_IDS, CHANNEL_URL, COURSES, Course

router = Router()
log = logging.getLogger(__name__)


# ---------- клавіатури ----------

def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📚 Каталог курсів", callback_data="catalog")],
        [InlineKeyboardButton(text="🎓 Мої курси", callback_data="my")],
    ]
    if CHANNEL_URL:
        rows.append([InlineKeyboardButton(text="📢 Наш канал", url=CHANNEL_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{c.title} — {c.price_stars} ⭐", callback_data=f"course:{c.id}")]
        for c in COURSES.values()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def course_kb(course: Course, owned: bool) -> InlineKeyboardMarkup:
    action = (
        InlineKeyboardButton(text="🔗 Отримати доступ", callback_data=f"access:{course.id}")
        if owned
        else InlineKeyboardButton(text=f"💳 Купити за {course.price_stars} ⭐", callback_data=f"buy:{course.id}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[[action], [InlineKeyboardButton(text="⬅️ До каталогу", callback_data="catalog")]]
    )


def course_text(course: Course) -> str:
    return (
        f"<b>{html.escape(course.title)}</b>\n\n"
        f"{html.escape(course.description)}\n\n"
        f"Ціна: <b>{course.price_stars} ⭐</b>"
    )


# ---------- доступ до курсу ----------

async def send_access(bot: Bot, user_id: int, course: Course) -> None:
    """Створює одноразове запрошення в закритий канал курсу."""
    try:
        link = await bot.create_chat_invite_link(
            chat_id=course.chat_id, name=f"{course.id}:{user_id}"[:32], member_limit=1
        )
    except TelegramAPIError:
        log.exception("Не вдалося створити посилання для курсу %s", course.id)
        await bot.send_message(
            user_id, "⚠️ Не вдалося створити посилання. Адміністратор уже отримав повідомлення і допоможе вам."
        )
        for admin in ADMIN_IDS:
            await bot.send_message(
                admin, f"❗ Помилка видачі доступу до «{course.title}» користувачу {user_id}. "
                       "Перевірте, що бот є адміном каналу курсу з правом запрошувати."
            )
        return
    await bot.send_message(
        user_id,
        f"🎉 Ваш доступ до <b>{html.escape(course.title)}</b>:\n{link.invite_link}\n\n"
        "Посилання одноразове — не передавайте його іншим. "
        "Якщо загубите, натисніть «Мої курси» — бот видасть нове.",
    )


# ---------- користувацькі команди ----------

@router.message(CommandStart())
async def start(message: Message, command: CommandObject) -> None:
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.full_name)

    # Глибоке посилання з каналу: t.me/your_bot?start=course_pro
    if command.args and command.args.startswith("course_"):
        course = COURSES.get(command.args.removeprefix("course_"))
        if course:
            owned = await db.has_course(user.id, course.id)
            await message.answer(course_text(course), reply_markup=course_kb(course, owned))
            return

    await message.answer(
        f"Привіт, {html.escape(user.first_name)}! 👋\n\n"
        "Тут можна придбати наші курси. Оплата — через Telegram Stars ⭐, "
        "доступ видається автоматично одразу після оплати.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery) -> None:
    await call.message.edit_text("Головне меню:", reply_markup=main_menu())
    await call.answer()


@router.callback_query(F.data == "catalog")
async def cb_catalog(call: CallbackQuery) -> None:
    await call.message.edit_text("📚 Оберіть курс:", reply_markup=catalog_kb())
    await call.answer()


@router.callback_query(F.data.startswith("course:"))
async def cb_course(call: CallbackQuery) -> None:
    course = COURSES.get(call.data.split(":", 1)[1])
    if not course:
        await call.answer("Курс не знайдено", show_alert=True)
        return
    owned = await db.has_course(call.from_user.id, course.id)
    await call.message.edit_text(course_text(course), reply_markup=course_kb(course, owned))
    await call.answer()


@router.callback_query(F.data == "my")
async def cb_my(call: CallbackQuery) -> None:
    owned = [COURSES[cid] for cid in await db.user_courses(call.from_user.id) if cid in COURSES]
    if not owned:
        await call.message.edit_text(
            "У вас поки немає курсів.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📚 Каталог курсів", callback_data="catalog")],
            ])
        )
    else:
        rows = [[InlineKeyboardButton(text=f"🔗 {c.title}", callback_data=f"access:{c.id}")] for c in owned]
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")])
        await call.message.edit_text(
            "🎓 Ваші курси (натисніть, щоб отримати посилання):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    await call.answer()


@router.callback_query(F.data.startswith("access:"))
async def cb_access(call: CallbackQuery, bot: Bot) -> None:
    course = COURSES.get(call.data.split(":", 1)[1])
    if not course or not await db.has_course(call.from_user.id, course.id):
        await call.answer("У вас немає доступу до цього курсу", show_alert=True)
        return
    await call.answer()
    await send_access(bot, call.from_user.id, course)


# ---------- оплата ----------

@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery) -> None:
    course = COURSES.get(call.data.split(":", 1)[1])
    if not course:
        await call.answer("Курс не знайдено", show_alert=True)
        return
    if await db.has_course(call.from_user.id, course.id):
        await call.answer("Ви вже придбали цей курс 🙂", show_alert=True)
        return
    await call.message.answer_invoice(
        title=course.title[:32],
        description=course.description[:255],
        payload=course.id,
        currency="XTR",  # Telegram Stars
        prices=[LabeledPrice(label=course.title[:32], amount=course.price_stars)],
    )
    await call.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    course = COURSES.get(query.invoice_payload)
    if not course or query.total_amount != course.price_stars:
        await query.answer(ok=False, error_message="Курс недоступний або ціна змінилась. Спробуйте ще раз.")
        return
    if await db.has_course(query.from_user.id, course.id):
        await query.answer(ok=False, error_message="Ви вже придбали цей курс.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot) -> None:
    payment = message.successful_payment
    course = COURSES.get(payment.invoice_payload)
    await db.add_purchase(
        message.from_user.id,
        payment.invoice_payload,
        payment.total_amount,
        payment.currency,
        payment.telegram_payment_charge_id,
    )
    if not course:  # курс видалили з каталогу між рахунком і оплатою
        await message.answer("Оплату отримано, але курс не знайдено. Зверніться до адміністратора.")
        return
    await send_access(bot, message.from_user.id, course)

    who = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
    for admin in ADMIN_IDS:
        await bot.send_message(
            admin,
            f"💰 Нова покупка: <b>{html.escape(course.title)}</b>\n"
            f"Покупець: {html.escape(who)} (<code>{message.from_user.id}</code>)\n"
            f"Сума: {payment.total_amount} ⭐\n"
            f"ID платежу: <code>{payment.telegram_payment_charge_id}</code>",
        )


# ---------- адмін ----------

admin = Router()
admin.message.filter(F.from_user.id.in_(ADMIN_IDS))


@admin.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    s = await db.stats()
    lines = [f"👥 Користувачів: {s['users']}", ""]
    total = 0
    for course_id, count, amount in s["per_course"]:
        title = COURSES[course_id].title if course_id in COURSES else course_id
        lines.append(f"• {html.escape(title)}: {count} продажів, {amount} ⭐")
        total += amount or 0
    lines.append(f"\n💰 Разом: {total} ⭐")
    await message.answer("\n".join(lines))


@admin.message(Command("refund"))
async def cmd_refund(message: Message, command: CommandObject, bot: Bot) -> None:
    if not command.args:
        await message.answer("Використання: /refund <ID платежу>")
        return
    charge_id = command.args.strip()
    purchase = await db.get_purchase(charge_id)
    if not purchase:
        await message.answer("Платіж не знайдено.")
        return
    user_id, course_id, refunded = purchase
    if refunded:
        await message.answer("Цей платіж уже повернено.")
        return
    try:
        await bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
    except TelegramAPIError as e:
        await message.answer(f"Помилка повернення: {html.escape(str(e))}")
        return
    await db.mark_refunded(charge_id)
    course = COURSES.get(course_id)
    if course:  # прибираємо доступ до закритого каналу
        try:
            await bot.ban_chat_member(course.chat_id, user_id)
            await bot.unban_chat_member(course.chat_id, user_id)
        except TelegramAPIError:
            log.warning("Не вдалося видалити %s з каналу курсу %s", user_id, course_id)
    await message.answer("✅ Кошти повернено, доступ закрито.")


@admin.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject, bot: Bot) -> None:
    """/broadcast <текст> — або відповісти командою /broadcast на будь-яке повідомлення."""
    source = message.reply_to_message
    if not source and not command.args:
        await message.answer("Використання: /broadcast <текст> або відповідь на повідомлення командою /broadcast")
        return
    sent = failed = 0
    for uid in await db.all_user_ids():
        try:
            if source:
                await source.copy_to(uid)
            else:
                await bot.send_message(uid, command.args)
            sent += 1
        except TelegramAPIError:
            failed += 1
        await asyncio.sleep(0.05)  # ліміт Telegram ~30 повідомлень/сек
    await message.answer(f"📨 Розсилку завершено. Надіслано: {sent}, помилок: {failed}")


@admin.message(Command("links"))
async def cmd_links(message: Message, bot: Bot) -> None:
    """Готові посилання на курси для постів у ТГК."""
    me = await bot.get_me()
    lines = [
        f"{html.escape(c.title)}:\nhttps://t.me/{me.username}?start=course_{c.id}"
        for c in COURSES.values()
    ]
    await message.answer("🔗 Посилання для постів у каналі:\n\n" + "\n\n".join(lines))
