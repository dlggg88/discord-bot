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

# Безопасное получение токена из переменных окружения
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
        
        log_channel = discord.utils.get(member.guild.text_channels, name="логи")
        if log_channel:
            embed = discord.Embed(
                title="🔨 Автоматический бан",
                color=0xff0000,
                timestamp=datetime.now()
            )
            embed.add_field(name="Пользователь", value=member.display_name, inline=False)
            embed.add_field(name="Причина", value="Выход с сервера", inline=False)
            await log_channel.send(embed=embed)
            
    except discord.Forbidden:
        print(f"❌ Нет прав для бана: {member.display_name}")

@bot.command()
async def пинг(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 Понг! {latency}мс")

@bot.command()
async def инфо(ctx):
    uptime = datetime.now() - bot_start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    embed = discord.Embed(
        title="ℹ️ Информация о боте",
        description="Автоматическая модерация сервера",
        color=0x3498db,
        timestamp=datetime.now()
    )
    embed.add_field(name="Функции", value="• Авто-бан при выходе\n• Логирование действий\n• 24/7 работа", inline=False)
    embed.add_field(name="📊 Статистика", value=f"Обработано выходов: {len(left_users)}", inline=True)
    embed.add_field(name="⏰ Аптайм", value=f"{hours}ч {minutes}м {seconds}с", inline=True)
    embed.add_field(name="🛡️ Хостинг", value="Railway", inline=True)
    embed.set_footer(text=f"Запущен: {bot_start_time.strftime('%d.%m.%Y %H:%M')}")
    
    await ctx.send(embed=embed)

@bot.command()
async def помощь(ctx):
    embed = discord.Embed(
        title="📋 Доступные команды",
        color=0x00ff00
    )
    embed.add_field(name="!пинг", value="Проверить пинг бота", inline=False)
    embed.add_field(name="!инфо", value="Информация о боте", inline=False)
    embed.add_field(name="!помощь", value="Это сообщение", inline=False)
    embed.add_field(name="⚙️ Авто-функции", value="• Бан при выходе с сервера\n• Логи в #логи", inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def очистить(ctx, количество: int = 10):
    await ctx.channel.purge(limit=количество + 1)
    msg = await ctx.send(f"🗑️ Удалено {количество} сообщений!")
    await asyncio.sleep(3)
    await msg.delete()

# Обработка ошибок чтобы бот не крашился
@bot.event
async def on_error(event, *args, **kwargs):
    print(f'❌ Ошибка в {event}: {args} {kwargs}')

@bot.event
async def on_command_error(ctx, error):
    print(f'❌ Ошибка команды: {error}')

keep_alive()

try:
    print("🚀 Запускаю бота...")
    bot.run(TOKEN)
except Exception as e:
    print(f'❌ Критическая ошибка: {e}')
