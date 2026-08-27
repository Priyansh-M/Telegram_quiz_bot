import asyncio
import html
import logging
import os
import random
import time
from dataclasses import dataclass, field

from aiohttp import web
import httpx
from telegram import Poll, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE").strip()
ACCESS_CODE = os.environ.get("QUIZ_ACCESS_CODE", "TRIVIA123").strip()

# Conversation States for Admin Creation Wizard
(
    WAIT_CODE,
    WAIT_QUIZ_ID,
    WAIT_TIMER,
    WAIT_QUESTION,
    WAIT_CORRECT,
    WAIT_WRONG_1,
    WAIT_WRONG_2,
) = range(7)


@dataclass
class GameState:
    chat_id: int
    total_questions: int
    question_time_limit: int = 15
    scores: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    answered_users: set = field(default_factory=set)
    current_poll_id: str | None = None
    current_correct_index: int | None = None
    question_number: int = 0
    active: bool = True


games: dict[int, GameState] = {}
global_scores: dict[int, int] = {}    # user_id -> lifetime points
user_names: dict[int, str] = {}       # user_id -> display name
custom_quizzes: dict[str, dict] = {}  # quiz_code -> quiz data dictionary


def truncate(text: str, limit: int) -> str:
    return text[: limit - 3] + "..." if len(text) > limit else text


async def fetch_opentdb_questions(amount: int = 5) -> list[dict]:
    url = "https://opentdb.com/api.php"
    params = {"amount": amount, "type": "multiple"}
    async with httpx.AsyncClient(timeout=10) as client:
        for _ in range(3):
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("response_code") == 5:
                await asyncio.sleep(5)
                continue
            if data.get("response_code") == 0 and data.get("results"):
                break
        else:
            raise RuntimeError("API rate-limited")

    questions = []
    for item in data["results"]:
        q = truncate(html.unescape(item["question"]), 300)
        c = truncate(html.unescape(item["correct_answer"]), 100)
        inc = [truncate(html.unescape(a), 100) for a in item["incorrect_answers"]]
        opts = inc + [c]
        random.shuffle(opts)
        questions.append({
            "question": q,
            "options": opts,
            "correct_index": opts.index(c),
            "category": html.unescape(item.get("category", ""))
        })
    return questions


# --- ADMIN QUIZ CREATION WIZARD ---

async def start_create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🔐 **Admin Verification**\nPlease enter the admin access code to create a custom quiz:")
    context.user_data["new_quiz"] = {"questions": []}
    return WAIT_CODE


async def check_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.strip() != ACCESS_CODE:
        await update.message.reply_text("❌ Incorrect access code. Action cancelled.")
        return ConversationHandler.END
    await update.message.reply_text("✅ Access granted!\nEnter a **Unique Quiz Access Code** for this quiz (e.g., `MATH101`):")
    return WAIT_QUIZ_ID


async def set_quiz_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    quiz_id = update.message.text.strip().upper()
    if quiz_id in custom_quizzes:
        await update.message.reply_text("❌ This quiz code already exists! Enter a different unique code:")
        return WAIT_QUIZ_ID
    
    context.user_data["new_quiz"]["id"] = quiz_id
    await update.message.reply_text(f"🔑 Quiz Code set to **{quiz_id}**!\nEnter question timer in seconds (e.g., 15):")
    return WAIT_TIMER


async def set_timer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        timer = max(5, min(60, int(update.message.text.strip())))
        context.user_data["new_quiz"]["timer"] = timer
        await update.message.reply_text("Timer set!\nNow enter Question #1:")
        return WAIT_QUESTION
    except ValueError:
        await update.message.reply_text("Please enter a valid number between 5 and 60:")
        return WAIT_TIMER


async def add_question_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["current_q"] = {"question": update.message.text.strip()}
    await update.message.reply_text("Enter the **CORRECT** answer option:")
    return WAIT_CORRECT


async def add_correct_option(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["current_q"]["correct"] = update.message.text.strip()
    await update.message.reply_text("Enter Wrong Option 1:")
    return WAIT_WRONG_1


async def add_wrong_1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["current_q"]["wrong1"] = update.message.text.strip()
    await update.message.reply_text("Enter Wrong Option 2:")
    return WAIT_WRONG_2


async def add_wrong_2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cq = context.user_data["current_q"]
    wrong2 = update.message.text.strip()
    
    opts = [cq["correct"], cq["wrong1"], wrong2]
    random.shuffle(opts)
    
    context.user_data["new_quiz"]["questions"].append({
        "question": cq["question"],
        "options": opts,
        "correct_index": opts.index(cq["correct"]),
        "category": "Custom Admin Quiz"
    })
    
    await update.message.reply_text(
        "✅ Question added!\nReply with your **Next Question**, or type `/done` to publish the quiz."
    )
    return WAIT_QUESTION


async def finish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    quiz_data = context.user_data.get("new_quiz")
    if not quiz_data or not quiz_data["questions"]:
        await update.message.reply_text("No questions created.")
        return ConversationHandler.END
    
    quiz_id = quiz_data["id"]
    custom_quizzes[quiz_id] = quiz_data
    await update.message.reply_text(
        f"🎉 **Quiz Published!**\n"
        f"🔑 **Unique Code:** `{quiz_id}`\n"
        f"📊 Questions: {len(quiz_data['questions'])}\n\n"
        f"Users in any group can start this quiz using `/custom {quiz_id}`."
    )
    return ConversationHandler.END


async def cancel_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Quiz creation cancelled.")
    return ConversationHandler.END


# --- GAME LOGIC & TRIVIA COMMANDS ---

async def trivia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id in games and games[chat_id].active:
        await update.message.reply_text("A game is already running! Use /stopgame first.")
        return

    n = 5
    if context.args:
        try:
            n = max(1, min(15, int(context.args[0])))
        except ValueError:
            pass

    await update.message.reply_text(f"🎯 Starting a {n}-question OpenTDB round!")
    try:
        questions = await fetch_opentdb_questions(n)
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("Could not load questions. Try again soon.")
        return

    game = GameState(chat_id=chat_id, total_questions=len(questions), question_time_limit=15)
    games[chat_id] = game
    await run_game_loop(context, game, questions)


async def custom_trivia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    if not context.args:
        available = ", ".join([f"`{code}`" for code in custom_quizzes.keys()]) or "None"
        await update.message.reply_text(
            f"❌ Please specify a quiz code!\n"
            f"**Usage:** `/custom <quiz_code>`\n"
            f"**Available Quizzes:** {available}"
        )
        return

    quiz_code = context.args[0].strip().upper()
    if quiz_code not in custom_quizzes:
        await update.message.reply_text(f"❌ Quiz code `{quiz_code}` not found.")
        return

    if chat_id in games and games[chat_id].active:
        await update.message.reply_text("A game is already running in this chat! Use /stopgame first.")
        return

    selected_quiz = custom_quizzes[quiz_code]
    questions = selected_quiz["questions"]
    timer = selected_quiz["timer"]

    await update.message.reply_text(f"⭐ Starting Custom Quiz **{quiz_code}** ({len(questions)} questions)!")
    game = GameState(chat_id=chat_id, total_questions=len(questions), question_time_limit=timer)
    games[chat_id] = game
    await run_game_loop(context, game, questions)


async def run_game_loop(context: ContextTypes.DEFAULT_TYPE, game: GameState, questions: list[dict]) -> None:
    for i, q in enumerate(questions):
        if games.get(game.chat_id) is not game or not game.active:
            break
        await ask_question(context, game, q, i)
        await asyncio.sleep(game.question_time_limit + 2)

    if games.get(game.chat_id) is game and game.active:
        await send_leaderboard(game.chat_id, context, caller_id=0, final=True)
        games.pop(game.chat_id, None)


async def ask_question(context: ContextTypes.DEFAULT_TYPE, game: GameState, q: dict, index: int) -> None:
    game.question_number = index + 1
    game.answered_users.clear()
    
    poll_text = truncate(f"Q{game.question_number}/{game.total_questions} · {q['question']}", 300)
    msg = await context.bot.send_poll(
        chat_id=game.chat_id,
        question=poll_text,
        options=q["options"],
        type=Poll.QUIZ,
        correct_option_id=q["correct_index"],
        is_anonymous=False,
        open_period=game.question_time_limit,
    )
    game.current_poll_id = msg.poll.id
    game.current_correct_index = q["correct_index"]


async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    answer = update.poll_answer
    user = answer.user

    game = next((g for g in games.values() if g.current_poll_id == answer.poll_id and g.active), None)
    if game is None or not answer.option_ids:
        return

    if user.id in game.answered_users:
        return

    game.answered_users.add(user.id)
    user_names[user.id] = user.full_name
    game.names[user.id] = user.full_name
    
    if user.id not in global_scores:
        global_scores[user.id] = 0

    chosen = answer.option_ids[0]
    if chosen == game.current_correct_index:
        game.scores[user.id] = game.scores.get(user.id, 0) + 1
        global_scores[user.id] += 1
    else:
        game.scores[user.id] = game.scores.get(user.id, 0) - 1
        global_scores[user.id] -= 1


# --- LEADERBOARD DISPLAY ---

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_leaderboard(update.effective_chat.id, context, caller_id=update.effective_user.id, final=False)


async def send_leaderboard(chat_id: int, context: ContextTypes.DEFAULT_TYPE, caller_id: int, final: bool) -> None:
    if not global_scores:
        await context.bot.send_message(chat_id, "No registered player scores yet!")
        return

    sorted_players = sorted(global_scores.items(), key=lambda item: item[1], reverse=True)
    
    ranked_list = []
    current_rank = 1
    for idx, (uid, score) in enumerate(sorted_players):
        if idx > 0 and score < sorted_players[idx - 1][1]:
            current_rank = idx + 1
        ranked_list.append((current_rank, uid, score))

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 **Overall Standings**\n" if final else "📊 **Current Leaderboard**\n"]
    
    caller_in_top5 = False
    caller_rank_line = None

    for rank, uid, score in ranked_list:
        name = user_names.get(uid, "Unknown")
        rank_str = medals.get(rank, f"{rank}.")
        
        if rank <= 5:
            lines.append(f"{rank_str} {name} — {score} pts")
            if uid == caller_id:
                caller_in_top5 = True
        
        if uid == caller_id:
            caller_rank_line = f"{rank_str} {name} — {score} pts"

    if caller_id and not caller_in_top5 and caller_rank_line:
        lines.append("\n--- Your Rank ---")
        lines.append(caller_rank_line)

    await context.bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")


async def stopgame(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    game = games.get(chat_id)
    if game is None or not game.active:
        await update.message.reply_text("No active game to stop.")
        return
    game.active = False
    await send_leaderboard(chat_id, context, caller_id=update.effective_user.id, final=True)
    games.pop(chat_id, None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 **Welcome to Trivia Bot!**\n\n"
        "🎮 **Commands:**\n"
        "/trivia [n] — Play OpenTDB quiz (n questions)\n"
        "/custom <quiz_code> — Play custom admin quiz\n"
        "/createquiz — Build a custom quiz (Admins)\n"
        "/leaderboard — View global rankings\n"
        "/stopgame — Stop current round"
    )


# --- RENDER HEALTH-CHECK WEB SERVER ---

async def handle_ping(request: web.Request) -> web.Response:
    return web.Response(text="Bot is awake!")


async def start_web_server() -> None:
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def post_init(application: Application) -> None:
    asyncio.create_task(start_web_server())


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise SystemExit("Missing valid TELEGRAM_BOT_TOKEN.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Quiz Creation Conversation Handler
    quiz_builder = ConversationHandler(
        entry_points=[CommandHandler("createquiz", start_create_quiz)],
        states={
            WAIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_code)],
            WAIT_QUIZ_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_quiz_id)],
            WAIT_TIMER: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_timer)],
            WAIT_QUESTION: [
                CommandHandler("done", finish_quiz),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_question_text),
            ],
            WAIT_CORRECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_correct_option)],
            WAIT_WRONG_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wrong_1)],
            WAIT_WRONG_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wrong_2)],
        },
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
    )

    app.add_handler(quiz_builder)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("trivia", trivia))
    app.add_handler(CommandHandler("custom", custom_trivia))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("stopgame", stopgame))
    app.add_handler(PollAnswerHandler(receive_poll_answer))

    logger.info("Bot started successfully...")
    app.run_polling()


if __name__ == "__main__":
    main()