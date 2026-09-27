# Trivia Bot
Bot link : https://t.me/Terival_bot
A Telegram bot for running live trivia games in group chats. Play random questions from the Open Trivia Database, or build your own quiz in chat and share it with a code.

## Features

- **Instant trivia**: `/trivia 10` starts a 10-question round from [OpenTDB](https://opentdb.com), posted as Telegram quiz polls with a timer.
- **Custom quizzes**: admins build a quiz step by step in chat (question, correct answer, two wrong answers) and publish it under a code like `MATH101`.
- **Scoring**: +1 for a correct answer, −1 for a wrong one. Only your first answer counts.
- **Leaderboards**: per-group standings and a global board across every group, with medals for the top 3 and your own rank if you're outside the top 5.
- **Render-ready**: includes a small health-check web server so it runs on Render's free tier.

## Commands

| Command | What it does |
| --- | --- |
| `/start` | Show help |
| `/trivia [n]` | Play `n` OpenTDB questions (default 5, max 15) |
| `/custom <code>` | Play a published custom quiz |
| `/createquiz` | Build a custom quiz (needs the admin code) |
| `/done` | Publish the quiz you're building |
| `/cancel` | Cancel quiz creation |
| `/leaderboard` | This group's standings |
| `/globalboard` | Standings across all groups |
| `/stopgame` | End the current round |


## Built with

Python, python-telegram-bot, httpx, aiohttp, Open Trivia Database API.

## Limitations

Scores and custom quizzes are stored in memory and reset when the bot restarts.
