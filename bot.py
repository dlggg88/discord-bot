import discord
from discord.ext import commands
import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# 🔑 Токен бота
TOKEN = "MTQzMzU3OTUzMjYzNTQ3MTk2Mw.G2KYb2.wkdhqxOyFsBn7cKOHU_i5ZuKw39OIFZIqOWGc0"

# Настройка бота
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Хранилища
left_users = {}

# ==================== АВТОМАТИЧЕСКИЙ БАН ====================

@bot.event
async def on_member_remove(member):
    """Автоматический бан при выходе с сервера"""
    left_users[member.id] = {
        'name': str(member),
        'left_at': datetime.now()
    }
    
    try:
        await member.ban(reason="Автобан: выход с сервера")
        print(f"🔨 Забанен при выходе: {member.display_name}")
        
        log_channel = discord.utils.get(member.guild.text_channels, name="логи")
        if log_channel:
            embed = discord.Embed(
                title="🔨 Автоматический бан",
                color=0xff0000,
                timestamp=datetime.now()
            )
            embed.add_field(name="Пользователь", value=f"{member.display_name}", inline=False)
            embed.add_field(name="Причина", value="Выход с сервера", inline=False)
            await log_channel.send(embed=embed)
            
    except discord.Forbidden:
        print(f"❌ Нет прав для бана: {member.display_name}")

# ==================== БАЗОВЫЕ КОМАНДЫ ====================

@bot.command()
async def пинг(ctx):
    """Проверить работоспособность бота"""
    await ctx.send("🏓 Понг!")

@bot.command()
async def инфо(ctx):
    """Информация о боте"""
    embed = discord.Embed(
        title="ℹ️ Информация о боте",
        description="Бот для автоматической модерации сервера",
        color=0x3498db
    )
    embed.add_field(name="Функции", value="• Авто-бан при выходе\n• Логирование действий", inline=False)
    embed.add_field(name="Префикс", value="!", inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def статус(ctx):
    """Проверить статус бота"""
    embed = discord.Embed(
        title="📊 Статус бота",
        color=0x00ff00,
        timestamp=datetime.now()
    )
    embed.add_field(name="🟢 Статус", value="Онлайн", inline=True)
    embed.add_field(name="📈 Обработано выходов", value=len(left_users), inline=True)
    embed.add_field(name="⚡ Пинг", value=f"{round(bot.latency * 1000)}мс", inline=True)
    await ctx.send(embed=embed)

# ==================== ЗАПУСК БОТА ====================

@bot.event
async def on_ready():
    print(f'🎉 Бот {bot.user} запущен!')
    print(f'🛡️ Авто-бан системы активны')
    await bot.change_presence(activity=discord.Game(name="Смотрящий за сервером 👁️"))

if __name__ == "__main__":
    print("🚀 Запускаю бота...")
    bot.run(TOKEN)
