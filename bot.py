from flask import Flask, render_template, request, jsonify
from threading import Thread
import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Select, Modal, TextInput
import os
import asyncio
from datetime import datetime, timedelta
import json
import sqlite3
import aiohttp
import secrets
from typing import Dict, List, Optional

# Конфигурация Flask для Railway
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
port = int(os.environ.get("PORT", 8080))

@app.route('/')
def home():
    return "🟢 Multi Bot System Online"

def run_flask():
    app.run(host='0.0.0.0', port=port, debug=False)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('bot_data.db', check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS role_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                role_id INTEGER,
                link_code TEXT UNIQUE,
                role_name TEXT,
                uses_limit INTEGER DEFAULT 0,
                uses_count INTEGER DEFAULT 0,
                expires_at DATETIME,
                created_by INTEGER,
                created_by_name TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        self.conn.commit()

db = Database()

# ========== СИСТЕМА ССЫЛОК РОЛЕЙ ==========
class RoleLinkSystem:
    def __init__(self):
        self.base_url = os.environ.get('RAILWAY_STATIC_URL', f'http://localhost:{port}')
    
    def create_role_link(self, server_id: int, role_id: int, role_name: str, created_by: int, created_by_name: str,
                        uses_limit: int = 0, expires_hours: int = 0) -> str:
        link_code = secrets.token_urlsafe(8)
        
        expires_at = None
        if expires_hours > 0:
            expires_at = datetime.now() + timedelta(hours=expires_hours)
        
        cursor = db.conn.cursor()
        cursor.execute('''
            INSERT INTO role_links 
            (server_id, role_id, role_name, link_code, uses_limit, expires_at, created_by, created_by_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (server_id, role_id, role_name, link_code, uses_limit, expires_at, created_by, created_by_name))
        db.conn.commit()
        
        return link_code
    
    def use_role_link(self, link_code: str, server_id: int) -> Dict:
        cursor = db.conn.cursor()
        cursor.execute('''
            SELECT * FROM role_links 
            WHERE link_code = ? AND server_id = ? AND is_active = TRUE
        ''', (link_code, server_id))
        link = cursor.fetchone()
        
        if not link:
            return {"success": False, "error": "Ссылка не найдена"}
        
        if link[5] > 0 and link[6] >= link[5]:
            return {"success": False, "error": "Лимит использований исчерпан"}
        
        if link[7] and datetime.now() > datetime.fromisoformat(link[7]):
            return {"success": False, "error": "Срок действия ссылки истек"}
        
        cursor.execute('''
            UPDATE role_links SET uses_count = uses_count + 1 
            WHERE id = ?
        ''', (link[0],))
        db.conn.commit()
        
        return {
            "success": True, 
            "role_id": link[2],
            "role_name": link[3],
            "uses_count": link[6] + 1,
            "uses_limit": link[5]
        }
    
    def get_active_links(self, server_id: int) -> List:
        cursor = db.conn.cursor()
        cursor.execute('''
            SELECT link_code, role_name, uses_limit, uses_count, expires_at, created_by_name, created_at
            FROM role_links 
            WHERE server_id = ? AND is_active = TRUE
            ORDER BY created_at DESC
        ''', (server_id,))
        return cursor.fetchall()

role_link_system = RoleLinkSystem()

# ========== КОМПОНЕНТЫ ИНТЕРФЕЙСА ==========

class CopyLinkModal(Modal):
    def __init__(self, link_url):
        super().__init__(title="📋 Копирование команды")
        self.link_url = link_url
        
        self.link_field = TextInput(
            label="Команда для копирования",
            default=link_url,
            style=discord.TextStyle.paragraph,
            placeholder="Скопируйте команду ниже"
        )
        self.add_item(self.link_field)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Команда скопирована! Теперь вы можете вставить её в чат.", ephemeral=True)

class CustomLinkModal(Modal):
    def __init__(self, role):
        super().__init__(title="🎛️ Кастомные настройки")
        self.role = role
        
        self.uses = TextInput(
            label="Лимит использований",
            placeholder="0 = без ограничений",
            default="0",
            max_length=4,
            required=True
        )
        
        self.hours = TextInput(
            label="Срок действия (часы)",
            placeholder="0 = бессрочно", 
            default="24",
            max_length=4,
            required=True
        )
        
        self.add_item(self.uses)
        self.add_item(self.hours)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            uses = int(self.uses.value)
            hours = int(self.hours.value)
            
            if uses < 0 or hours < 0:
                await interaction.response.send_message("❌ Числа должны быть положительными", ephemeral=True)
                return
            
            link_code = role_link_system.create_role_link(
                server_id=interaction.guild.id,
                role_id=self.role.id,
                role_name=self.role.name,
                created_by=interaction.user.id,
                created_by_name=interaction.user.name,
                uses_limit=uses,
                expires_hours=hours
            )
            
            embed = discord.Embed(
                title="🔗 Команда создана!",
                description=f"Роль: {self.role.mention}",
                color=0x00ff00
            )
            
            limits = []
            if uses > 0:
                limits.append(f"🔄 {uses} использований")
            if hours > 0:
                limits.append(f"⏰ {hours} часов")
            if not limits:
                limits.append("✅ Без ограничений")
            
            embed.add_field(name="Ограничения", value=" | ".join(limits), inline=True)
            embed.add_field(name="Команда", value=f"```!роль {link_code}```", inline=False)
            embed.add_field(name="Инструкция", value="Отправьте команду в чат чтобы получить роль", inline=False)
            
            view = LinkActionsView(link_code, self.role.name)
            await interaction.response.edit_message(embed=embed, view=view)
            
        except ValueError:
            await interaction.response.send_message("❌ Введите корректные числа", ephemeral=True)

class LinkActionsView(View):
    def __init__(self, link_code, role_name):
        super().__init__(timeout=300)
        self.link_code = link_code
        self.role_name = role_name
    
    @discord.ui.button(label="📋 СКОПИРОВАТЬ", style=discord.ButtonStyle.success, emoji="📋", row=0)
    async def copy_command(self, interaction: discord.Interaction, button: Button):
        modal = CopyLinkModal(f"!роль {self.link_code}")
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🔙 НАЗАД", style=discord.ButtonStyle.secondary, emoji="🔙", row=0)
    async def back_button(self, interaction: discord.Interaction, button: Button):
        await self.show_main_menu(interaction)
    
    async def show_main_menu(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎮 Управление ролями",
            description="Выберите действие:",
            color=0x5865F2
        )
        
        view = MainRoleView()
        await interaction.response.edit_message(embed=embed, view=view)

class RoleSelectView(View):
    def __init__(self, roles):
        super().__init__(timeout=180)
        self.roles = roles
        
        self.select = Select(
            placeholder="🎯 Выберите роль...",
            options=[
                discord.SelectOption(
                    label=role.name[:25],
                    value=str(role.id),
                    description=f"ID: {role.id}"[:50]
                ) for role in roles[:25]
            ]
        )
        self.select.callback = self.role_selected
        self.add_item(self.select)
    
    @discord.ui.button(label="🔙 НАЗАД", style=discord.ButtonStyle.secondary, emoji="🔙", row=1)
    async def back_button(self, interaction: discord.Interaction, button: Button):
        await self.show_main_menu(interaction)
    
    async def role_selected(self, interaction: discord.Interaction):
        role_id = int(self.select.values[0])
        role = interaction.guild.get_role(role_id)
        
        embed = discord.Embed(
            title="⚙️ Настройки команды",
            description=f"Роль: {role.mention}",
            color=0x3498db
        )
        
        view = LinkSettingsView(role, interaction.user.id, interaction.user.name)
        await interaction.response.edit_message(embed=embed, view=view)
    
    async def show_main_menu(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎮 Управление ролями",
            description="Выберите действие:",
            color=0x5865F2
        )
        
        view = MainRoleView()
        await interaction.response.edit_message(embed=embed, view=view)

class LinkSettingsView(View):
    def __init__(self, role, creator_id, creator_name):
        super().__init__(timeout=180)
        self.role = role
        self.creator_id = creator_id
        self.creator_name = creator_name
    
    @discord.ui.button(label="🚀 БЕЗ ОГРАНИЧЕНИЙ", style=discord.ButtonStyle.success, emoji="🚀", row=0)
    async def unlimited_button(self, interaction: discord.Interaction, button: Button):
        await self.create_link(interaction, 0, 0)
    
    @discord.ui.button(label="🎯 10 ИСПОЛЬЗОВАНИЙ", style=discord.ButtonStyle.primary, emoji="🎯", row=0)
    async def ten_uses_button(self, interaction: discord.Interaction, button: Button):
        await self.create_link(interaction, 10, 24)
    
    @discord.ui.button(label="⏰ 24 ЧАСА", style=discord.ButtonStyle.primary, emoji="⏰", row=1)
    async def one_day_button(self, interaction: discord.Interaction, button: Button):
        await self.create_link(interaction, 0, 24)
    
    @discord.ui.button(label="⚙️ КАСТОМНЫЕ НАСТРОЙКИ", style=discord.ButtonStyle.secondary, emoji="⚙️", row=1)
    async def custom_button(self, interaction: discord.Interaction, button: Button):
        modal = CustomLinkModal(self.role)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🔙 НАЗАД", style=discord.ButtonStyle.secondary, emoji="🔙", row=2)
    async def back_button(self, interaction: discord.Interaction, button: Button):
        roles = [role for role in interaction.guild.roles if role.name != "@everyone" and not role.managed]
        
        embed = discord.Embed(
            title="🎯 Выберите роль для команды",
            description="Выберите роль из списка ниже:",
            color=0x3498db
        )
        
        view = RoleSelectView(roles)
        await interaction.response.edit_message(embed=embed, view=view)
    
    async def create_link(self, interaction: discord.Interaction, uses: int, hours: int):
        link_code = role_link_system.create_role_link(
            server_id=interaction.guild.id,
            role_id=self.role.id,
            role_name=self.role.name,
            created_by=self.creator_id,
            created_by_name=self.creator_name,
            uses_limit=uses,
            expires_hours=hours
        )
        
        embed = discord.Embed(
            title="🔗 Команда создана!",
            description=f"Роль: {self.role.mention}",
            color=0x00ff00
        )
        
        limits = []
        if uses > 0:
            limits.append(f"🔄 {uses} использований")
        if hours > 0:
            limits.append(f"⏰ {hours} часов")
        if not limits:
            limits.append("✅ Без ограничений")
        
        embed.add_field(name="Ограничения", value=" | ".join(limits), inline=True)
        embed.add_field(name="Команда", value=f"```!роль {link_code}```", inline=False)
        embed.add_field(name="Инструкция", value="Отправьте команду в чат чтобы получить роль", inline=False)
        
        view = LinkActionsView(link_code, self.role.name)
        await interaction.response.edit_message(embed=embed, view=view)

class ActiveLinksView(View):
    def __init__(self, links, page=0):
        super().__init__(timeout=180)
        self.links = links
        self.page = page
        self.links_per_page = 5
        
    @discord.ui.button(label="🔙 НАЗАД", style=discord.ButtonStyle.primary, custom_id="back_btn")
    async def back_button(self, interaction: discord.Interaction, button: Button):
        await self.show_main_menu(interaction)
    
    async def show_main_menu(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎮 Управление ролями",
            description="Выберите действие:",
            color=0x5865F2
        )
        
        view = MainRoleView()
        await interaction.response.edit_message(embed=embed, view=view)

class QuickRoleView(View):
    def __init__(self, roles, user_id, user_name):
        super().__init__(timeout=180)
        self.roles = roles
        self.user_id = user_id
        self.user_name = user_name
        
        for i, role in enumerate(roles[:5]):
            button = Button(
                label=role.name[:15],
                style=discord.ButtonStyle.primary,
                emoji="🎯",
                row=i // 3
            )
            button.callback = self.create_quick_link_callback(role)
            self.add_item(button)
        
        # Кнопка "Назад"
        back_button = Button(
            label="🔙 НАЗАД",
            style=discord.ButtonStyle.secondary,
            emoji="🔙",
            row=2
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)
    
    def create_quick_link_callback(self, role):
        async def callback(interaction: discord.Interaction):
            link_code = role_link_system.create_role_link(
                server_id=interaction.guild.id,
                role_id=role.id,
                role_name=role.name,
                created_by=self.user_id,
                created_by_name=self.user_name,
                uses_limit=0,
                expires_hours=24
            )
            
            embed = discord.Embed(
                title="⚡ Команда создана!",
                description=f"Роль: {role.mention}",
                color=0x00ff00
            )
            embed.add_field(name="Команда", value=f"```!роль {link_code}```", inline=False)
            embed.add_field(name="Действует", value="24 часа", inline=True)
            embed.add_field(name="Лимит", value="Без ограничений", inline=True)
            
            view = LinkActionsView(link_code, role.name)
            await interaction.response.edit_message(embed=embed, view=view)
        
        return callback
    
    async def back_to_main(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎮 Управление ролями",
            description="Выберите действие:",
            color=0x5865F2
        )
        
        view = MainRoleView()
        await interaction.response.edit_message(embed=embed, view=view)

# ========== ГЛАВНОЕ МЕНЮ ==========

class MainRoleView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🎮 СОЗДАТЬ КОМАНДУ", style=discord.ButtonStyle.primary, emoji="🎮", custom_id="create_link_btn", row=0)
    async def create_link_button(self, interaction: discord.Interaction, button: Button):
        roles = [role for role in interaction.guild.roles if role.name != "@everyone" and not role.managed]
        
        if not roles:
            await interaction.response.send_message("❌ На сервере нет доступных ролей", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🎯 Выберите роль для команды",
            description="Выберите роль из списка ниже:",
            color=0x3498db
        )
        
        view = RoleSelectView(roles)
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="📊 АКТИВНЫЕ КОМАНДЫ", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="active_links_btn", row=0)
    async def active_links_button(self, interaction: discord.Interaction, button: Button):
        links = role_link_system.get_active_links(interaction.guild.id)
        
        if not links:
            embed = discord.Embed(
                title="🔗 Активные команды",
                description="❌ Нет активных команд",
                color=0x3498db
            )
            view = MainRoleView()
            await interaction.response.edit_message(embed=embed, view=view)
            return
        
        embed = discord.Embed(
            title="🔗 Активные команды",
            description=f"Всего активных команд: {len(links)}",
            color=0x3498db
        )
        
        for link_code, role_name, uses_limit, uses_count, expires_at, created_by, created_at in links[:5]:
            status = "✅ Активна"
            if uses_limit > 0:
                status = f"🔄 {uses_count}/{uses_limit}"
            if expires_at and datetime.now() > datetime.fromisoformat(expires_at):
                status = "❌ Истекла"
            
            expires_text = "Бессрочно"
            if expires_at:
                expires_dt = datetime.fromisoformat(expires_at)
                expires_text = expires_dt.strftime("%d.%m %H:%M")
            
            created_dt = datetime.fromisoformat(created_at)
            created_text = created_dt.strftime("%d.%m %H:%M")
            
            embed.add_field(
                name=f"🎯 {role_name}",
                value=(
                    f"**Код:** `{link_code}`\n"
                    f"**Статус:** {status}\n"
                    f"**Создал:** **{created_by}**\n"
                    f"**Создано:** {created_text}\n"
                    f"**Истекает:** {expires_text}"
                ),
                inline=False
            )
        
        if len(links) > 5:
            embed.set_footer(text=f"И еще {len(links) - 5} команд... Используйте кнопки для навигации")
        
        view = ActiveLinksView(links)
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="⚡ БЫСТРАЯ КОМАНДА", style=discord.ButtonStyle.success, emoji="⚡", custom_id="quick_link_btn", row=1)
    async def quick_link_button(self, interaction: discord.Interaction, button: Button):
        roles = [role for role in interaction.guild.roles if role.name != "@everyone" and not role.managed]
        
        if not roles:
            await interaction.response.send_message("❌ На сервере нет доступных ролей", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="⚡ Быстрая выдача ролей",
            description="Выберите роль для создания команды:",
            color=0x00ff00
        )
        
        popular_roles = roles[:5]
        
        view = QuickRoleView(popular_roles, interaction.user.id, interaction.user.name)
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="❓ ПОМОЩЬ", style=discord.ButtonStyle.danger, emoji="❓", custom_id="help_btn", row=1)
    async def help_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="📋 Помощь по командам",
            description="Как использовать систему ролей:",
            color=0x5865F2
        )
        
        embed.add_field(
            name="🎮 Создать команду",
            value="Создает команду для выдачи роли с настройками",
            inline=False
        )
        
        embed.add_field(
            name="📊 Активные команды", 
            value="Показывает все активные команды и их статус",
            inline=False
        )
        
        embed.add_field(
            name="⚡ Быстрая команда",
            value="Создает команду на 24 часа без ограничений",
            inline=False
        )
        
        embed.add_field(
            name="🎯 Использование",
            value="Отправьте `!роль КОД` в чат чтобы получить роль",
            inline=False
        )
        
        view = MainRoleView()
        await interaction.response.edit_message(embed=embed, view=view)

# ========== DISCORD BOT ==========
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN не установлен!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'🎉 Бот {bot.user} запущен!')
    print(f'📊 Подключен к {len(bot.guilds)} серверам')
    
    # Регистрируем постоянные кнопки
    bot.add_view(MainRoleView())
    
    # Устанавливаем статус
    try:
        activity = discord.Activity(type=discord.ActivityType.watching, name="за сервером 👁️")
        await bot.change_presence(activity=activity, status=discord.Status.online)
        print("✅ Статус бота установлен: 'Смотрящий за сервером 👁️'")
    except Exception as e:
        print(f"⚠️ Не удалось установить статус: {e}")

# ========== КОМАНДА ДЛЯ СОЗДАНИЯ ПАНЕЛИ ==========

@bot.command()
@commands.has_permissions(administrator=True)
async def панель(ctx):
    """Создать главную панель управления"""
    embed = discord.Embed(
        title="🎮 Управление ролями",
        description="Используйте кнопки ниже для управления ролями\n*Все действия происходят в этой панели*",
        color=0x5865F2
    )
    
    embed.add_field(
        name="🎮 СОЗДАТЬ КОМАНДУ", 
        value="Создать команду для выдачи роли с настройками", 
        inline=True
    )
    embed.add_field(
        name="📊 АКТИВНЫЕ КОМАНДЫ", 
        value="Просмотр всех активных команд", 
        inline=True
    )
    embed.add_field(
        name="⚡ БЫСТРАЯ КОМАНДА", 
        value="Создать команду без ограничений", 
        inline=True
    )
    embed.add_field(
        name="❓ ПОМОЩЬ", 
        value="Инструкция по использованию", 
        inline=True
    )
    
    view = MainRoleView()
    message = await ctx.send(embed=embed, view=view)
    
    try:
        await message.pin()
        await ctx.send("✅ Панель создана и закреплена!", delete_after=5)
    except:
        await ctx.send("✅ Панель создана! (Не удалось закрепить)", delete_after=5)
    
    await ctx.message.delete()

# ========== КОМАНДА ДЛЯ ПОЛУЧЕНИЯ РОЛИ ==========

@bot.command()
async def роль(ctx, код: str):
    """Получить роль по коду команды"""
    result = role_link_system.use_role_link(код, ctx.guild.id)
    
    if result["success"]:
        role_id = result["role_id"]
        role = ctx.guild.get_role(role_id)
        
        if role:
            try:
                if role in ctx.author.roles:
                    await ctx.author.remove_roles(role)
                    message = await ctx.send(f"✅ Роль {role.mention} убрана!")
                else:
                    await ctx.author.add_roles(role)
                    message = await ctx.send(f"✅ Роль {role.mention} выдана!")
                
                await asyncio.sleep(10)
                await ctx.message.delete()
                await message.delete()
                
            except discord.Forbidden:
                message = await ctx.send("❌ У бота нет прав для выдачи ролей")
                await asyncio.sleep(10)
                await ctx.message.delete()
                await message.delete()
        else:
            message = await ctx.send("❌ Роль не найдена на сервере")
            await asyncio.sleep(10)
            await ctx.message.delete()
            await message.delete()
    else:
        message = await ctx.send(f"❌ {result['error']}")
        await asyncio.sleep(10)
        await ctx.message.delete()
        await message.delete()

# ========== ЗАПУСК ПРИЛОЖЕНИЯ ==========

if __name__ == '__main__':
    keep_alive()
    print(f"🚀 Запускаю Multi Bot на порту {port}")
    
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")
