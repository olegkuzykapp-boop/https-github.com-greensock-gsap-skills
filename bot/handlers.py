import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from . import db
from .config import ADMIN_IDS, CHANNEL_URL, COURSES, CURRENCY, PAYMENT_DETAILS, PAYMENT_METHODS, Course

router = Router()
log = logging.getLogger(__name__)


class ManualPay(StatesGroup):
    receipt = State()  # чекаємо скрін/квитанцію про оплату


def price_text(course: Course) -> str:
    parts = []
    if "stars" in PAYMENT_METHODS:
        parts.append(f"{course.price_stars} ⭐")
    if "manual" in PAYMENT_METHODS:
        parts.append(f"{course.price} {CURRENCY}")
    return " / ".join(parts)


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
        [InlineKeyboardButton(text=f"{c.title} — {price_text(c)}", callback_data=f"course:{c.id}")]
        for c in COURSES.values()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def course_kb(course: Course, owned: bool) -> InlineKeyboardMarkup:
    action = (
        InlineKeyboardButton(text="🔗 Отримати доступ", callback_data=f"access:{course.id}")
        if owned
        else InlineKeyboardButton(text=f"💳 Купити — {price_text(course)}", callback_data=f"buy:{course.id}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[[action], [InlineKeyboardButton(text="⬅️ До каталогу", callback_data="catalog")]]
    )


def course_text(course: Course) -> str:
    return (
        f"<b>{html.escape(course.title)}</b>\n\n"
        f"{html.escape(course.description)}\n\n"
        f"Ціна: <b>{html.escape(price_text(course))}</b>"
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
        "Тут можна придбати наші курси. Доступ видається одразу після оплати.",
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

async def get_buyable(call: CallbackQuery) -> Course | None:
    course = COURSES.get(call.data.split(":", 1)[1])
    if not course:
        await call.answer("Курс не знайдено", show_alert=True)
        return None
    if await db.has_course(call.from_user.id, course.id):
        await call.answer("Ви вже придбали цей курс 🙂", show_alert=True)
        return None
    return course


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery, state: FSMContext) -> None:
    course = await get_buyable(call)
    if not course:
        return
    if PAYMENT_METHODS == {"stars"}:
        await send_stars_invoice(call, course)
    elif PAYMENT_METHODS == {"manual"}:
        await start_manual(call, course, state)
    else:
        await call.message.edit_text(
            f"Оберіть спосіб оплати курсу <b>{html.escape(course.title)}</b>:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"⭐ Telegram Stars — {course.price_stars} ⭐",
                                      callback_data=f"pay_stars:{course.id}")],
                [InlineKeyboardButton(text=f"💳 Переказ на картку — {course.price} {CURRENCY}",
                                      callback_data=f"pay_manual:{course.id}")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"course:{course.id}")],
            ]),
        )
        await call.answer()


@router.callback_query(F.data.startswith("pay_stars:"))
async def cb_pay_stars(call: CallbackQuery) -> None:
    if course := await get_buyable(call):
        await send_stars_invoice(call, course)


@router.callback_query(F.data.startswith("pay_manual:"))
async def cb_pay_manual(call: CallbackQuery, state: FSMContext) -> None:
    if course := await get_buyable(call):
        await start_manual(call, course, state)


# --- Telegram Stars ---

async def send_stars_invoice(call: CallbackQuery, course: Course) -> None:
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


# --- ручна оплата на картку ---

async def start_manual(call: CallbackQuery, course: Course, state: FSMContext) -> None:
    await state.set_state(ManualPay.receipt)
    await state.update_data(course_id=course.id)
    await call.message.answer(
        f"💳 Оплата курсу <b>{html.escape(course.title)}</b>\n"
        f"Сума: <b>{course.price} {html.escape(CURRENCY)}</b>\n\n"
        f"{html.escape(PAYMENT_DETAILS)}\n\n"
        "Після оплати надішліть сюди <b>скріншот або квитанцію</b> (фото чи файл). "
        "Адміністратор перевірить оплату і бот видасть доступ.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_pay")],
        ]),
    )
    await call.answer()


@router.callback_query(F.data == "cancel_pay")
async def cb_cancel_pay(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Оплату скасовано.", reply_markup=main_menu())
    await call.answer()


@router.message(StateFilter(ManualPay.receipt), F.photo | F.document)
async def got_receipt(message: Message, state: FSMContext, bot: Bot) -> None:
    course = COURSES.get((await state.get_data()).get("course_id"))
    await state.clear()
    if not course:
        await message.answer("Курс не знайдено. Почніть спочатку: /start")
        return
    order_id = await db.create_order(message.from_user.id, course.id, course.price)
    who = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Підтвердити", callback_data=f"approve:{order_id}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"reject:{order_id}"),
    ]])
    caption = (
        f"🧾 Заявка №{order_id} на ручну оплату\n"
        f"Курс: <b>{html.escape(course.title)}</b>\n"
        f"Сума: {course.price} {html.escape(CURRENCY)}\n"
        f"Покупець: {html.escape(who)} (<code>{message.from_user.id}</code>)"
    )
    for admin_id in ADMIN_IDS:
        try:
            await message.copy_to(admin_id, caption=caption, reply_markup=kb)
        except TelegramAPIError:
            log.exception("Не вдалося надіслати заявку адміну %s", admin_id)
    await message.answer("✅ Квитанцію отримано! Доступ прийде сюди одразу після перевірки оплати.")


@router.message(StateFilter(ManualPay.receipt))
async def receipt_wrong_type(message: Message) -> None:
    await message.answer("Надішліть, будь ласка, скріншот або файл квитанції (або натисніть «Скасувати»).")


# ---------- адмін ----------

admin = Router()
admin.message.filter(F.from_user.id.in_(ADMIN_IDS))
admin.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))


@admin.callback_query(F.data.startswith(("approve:", "reject:")))
async def cb_review_order(call: CallbackQuery, bot: Bot) -> None:
    action, order_id = call.data.split(":", 1)
    approved = action == "approve"
    order = await db.close_order(int(order_id), "approved" if approved else "rejected")
    if not order:
        await call.answer("Цю заявку вже оброблено", show_alert=True)
        return
    user_id, course_id, amount = order
    course = COURSES.get(course_id)
    if approved:
        await db.add_purchase(user_id, course_id, amount, CURRENCY, f"manual-{order_id}")
        if course:
            await send_access(bot, user_id, course)
        else:
            await bot.send_message(user_id, "Оплату підтверджено, але курс не знайдено. Зверніться до адміністратора.")
    else:
        await bot.send_message(
            user_id, "❌ На жаль, оплату не підтверджено. Якщо це помилка — напишіть адміністратору."
        )
    status = "✅ Підтверджено" if approved else "❌ Відхилено"
    await call.message.edit_caption(
        caption=f"{call.message.html_text}\n\n{status} ({html.escape(call.from_user.full_name)})\n"
                + (f"ID платежу: <code>manual-{order_id}</code>" if approved else ""),
        reply_markup=None,
    )
    await call.answer(status)


@admin.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    s = await db.stats()
    lines = [f"👥 Користувачів: {s['users']}", ""]
    totals: dict[str, int] = {}
    for course_id, currency, count, amount in s["per_course"]:
        title = COURSES[course_id].title if course_id in COURSES else course_id
        cur = "⭐" if currency == "XTR" else currency
        lines.append(f"• {html.escape(title)}: {count} продажів, {amount} {html.escape(cur)}")
        totals[cur] = totals.get(cur, 0) + (amount or 0)
    total = " + ".join(f"{v} {html.escape(k)}" for k, v in totals.items()) or "0"
    lines.append(f"\n💰 Разом: {total}")
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
    manual = charge_id.startswith("manual-")
    if not manual:
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
    await message.answer(
        "✅ Доступ закрито. Поверніть кошти покупцю на картку вручну." if manual
        else "✅ Кошти повернено, доступ закрито."
    )


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
