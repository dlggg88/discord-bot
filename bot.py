import os
import discord
from discord.ext import commands
from flask import Flask
from threading import Thread

print("=" * 50)
print("🔄 ЗАПУСК ДИАГНОСТИКИ BOT...")
print("=" * 50)

# Конфигурация
app = Flask(__name__)
port = int(os.environ.get("PORT", 8080))
TOKEN = os.getenv('DISCORD_TOKEN')

# Диагностика переменных окружения
print("🔍 ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ:")
print(f"   PORT: {port}")
print(f"   TOKEN: {'✅ УСТАНОВЛЕН' if TOKEN else '❌ ОТСУТСТВУЕТ'}")

if TOKEN:
    print(f"   Длина токена: {len(TOKEN)} символов")
    print(f"   Префикс токена: {TOKEN[:10]}...")

@app.route('/')
def home():
    return "🟢 Bot Status: Online"

@app.route('/health')
def health():
    return {"status": "healthy", "service": "discord-bot"}

def run_flask():
    print("🌐 Запускаю Flask сервер...")
    app.run(host='0.0.0.0', port=port, debug=False)

# Настройка бота
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print("=" * 50)
    print("🎉 БОТ УСПЕШНО ЗАПУЩЕН!")
    print(f"🤖 Имя: {bot.user}")
    print(f"🆔 ID: {bot.user.id}")
    print(f"📊 Серверов: {len(bot.guilds)}")
    print(f"📡 Задержка: {round(bot.latency * 1000)}ms")
    print("=" * 50)

@bot.event
async def on_connect():
    print("🔗 Подключение к Discord установлено")

@bot.event
async def on_disconnect():
    print("🔌 Отключение от Discord")

@bot.command()
async def ping(ctx):
    await ctx.send(f"🏓 Понг! {round(bot.latency * 1000)}мс")

# Запуск
if __name__ == '__main__':
    print("🚀 ЗАПУСК ПРИЛОЖЕНИЯ...")
    
    if not TOKEN:
        print("❌ КРИТИЧЕСКАЯ ОШИБКА: DISCORD_TOKEN не найден!")
        print("💡 Решение: Установите переменную DISCORD_TOKEN в настройках Railway")
        exit(1)
    
    # Запускаем Flask в фоне
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("🤖 ЗАПУСКАЮ DISCORD BOT...")
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ ОШИБКА: Неверный токен бота")
        print("💡 Проверьте правильность DISCORD_TOKEN в настройках Railway")
    except discord.PrivilegedIntentsRequired:
        print("❌ ОШИБКА: Не включены привилегированные интенты")
        print("💡 Решение: Включите в Discord Developer Portal:")
        print("   - PRESENCE INTENT")
        print("   - SERVER MEMBERS INTENT")
        print("   - MESSAGE CONTENT INTENT")
    except Exception as e:
        print(f"❌ НЕИЗВЕСТНАЯ ОШИБКА: {type(e).__name__}: {e}")
