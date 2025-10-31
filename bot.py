from flask import Flask
from threading import Thread
import discord
from discord.ext import commands
import os
import asyncio
from datetime import datetime

app = Flask('')

@app.route('/')
def home():
    return "🟢 Discord Bot Online"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# Используем os.getenv для Railway
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

left_users = {}
bot_start_time = datetime.now()

@bot.event
async def on_ready():
    print(f'🎉 Бот {bot.user} запущен на Railway!')
    await bot.change_presence(activity=discord.Game(name="Смотрящий за сервером 👁️"))

@bot.event
async def on_member_remove(member):
    left_users[member.id] = {'name': str(member), 'left_at': datetime.now()}
    try:
        await member.ban(reason="Автобан: выход с сервера")
        print(f"🔨 Забанен: {member.display_name}")
    except discord.Forbidden:
        print(f"❌ Нет прав: {member.display_name}")

@bot.command()
async def пинг(ctx):
    await ctx.send("🏓 Понг!")

@bot.command()
async def инфо(ctx):
    embed = discord.Embed(title="ℹ️ Бот на Railway", color=0x3498db)
    embed.add_field(name="Хостинг", value="Railway.app", inline=True)
    embed.add_field(name="Статус", value="🟢 24/7", inline=True)
    await ctx.send(embed=embed)

# Обработка ошибок чтобы бот не крашился
@bot.event
async def on_error(event, *args, **kwargs):
    print(f'❌ Ошибка в {event}: {args} {kwargs}')

@bot.event
async def on_command_error(ctx, error):
    print(f'❌ Ошибка команды: {error}')

keep_alive()

try:
    bot.run(TOKEN)
except Exception as e:
    print(f'❌ Критическая ошибка: {e}')
