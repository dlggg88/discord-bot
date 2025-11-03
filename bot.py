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
        
        # Таблица для ссылок ролей
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
        
        # Проверяем лимит использований
        if link[5] > 0 and link[6] >= link[5]:
            return {"success": False, "error": "Лимит использований исчерпан"}
        
        # Проверяем срок действия
        if link[7] and datetime.now() > datetime.fromisoformat(link[7]):
            return {"success": False, "error": "Срок действия ссылки истек"}
        
        # Обновляем счетчик использований
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
        super().__init__(title="📋 Копирование ссылки")
        self.link_url = link_url
        
        self.link_field = TextInput(
            label="Ссылка для копирования",
            default=link_url,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.link_field)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Ссылка готова для копирования!", ephemeral=True)

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
            message = await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except ValueError:
            await interaction.response.send_message("❌ Введите корректные числа", ephemeral=True)

class LinkActionsView(View):
    def __init__(self, link_code, role_name):
        super().__init__(timeout=300)
        self.link_code = link_code
        self.role_name = role_name
    
    @discord.ui.button(label="📋 Копировать команду", style=discord.ButtonStyle.primary, emoji="📋")
    async def copy_command(self, interaction: discord.Interaction, button: Button):
        modal = CopyLinkModal(f"!роль {self.link_code}")
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="📤 Поделиться в чате", style=discord.ButtonStyle.success, emoji="📤")
    async def share_link(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title=f"🔗 Получить роль: {self.role_name}",
            description="Используйте команду ниже чтобы получить роль:",
            color=0x5865F2
        )
        embed.add_field(name="Команда", value=f"```!роль {self.link_code}```", inline=False)
        embed.set_footer(text="Сообщение автоматически удалится через 1 минуту")
        
        # Отправляем в общий чат и удаляем через минуту
        message = await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ Сообщение отправлено в чат!", ephemeral=True)
        
        # Удаляем через 1 минуту
        await asyncio.sleep(60)
        try:
            await message.delete()
        except:
            pass

class ActiveLinksView(View):
    def __init__(self, links, page=0):
        super().__init__(timeout=180)
        self.links = links
        self.page = page
        self.links_per_page = 5
        
    @discord.ui.button(label="⬅️ Назад", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: Button):
        if self.page > 0:
            await self.show_page(interaction, self.page - 1)
    
    @discord.ui.button(label="➡️ Вперед", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: Button):
        if (self.page + 1) * self.links_per_page < len(self.links):
            await self.show_page(interaction, self.page + 1)
    
    @discord.ui.button(label="🔄 Обновить", style=discord.ButtonStyle.primary)
    async def refresh(self, interaction: discord.Interaction, button: Button):
        await self.show_page(interaction, self.page)
    
    async def show_page(self, interaction: discord.Interaction, page: int):
        start_idx = page * self.links_per_page
        end_idx = start_idx + self.links_per_page
        page_links = self.links[start_idx:end_idx]
        
        embed = discord.Embed(
            title=f"🔗 Активные команды (Страница {page + 1})",
            description=f"Всего активных команд: {len(self.links)}",
            color=0x3498db
        )
        
        for link_code, role_name, uses_limit, uses_count, expires_at, created_by, created_at in page_links:
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
        
        if not page_links:
            embed.description = "❌ На этой странице нет команд"
        
        view = ActiveLinksView(self.links, page)
        await interaction.response.edit_message(embed=embed, view=view)

class RoleSelectView(View):
    def __init__(self, roles, action_type):
        super().__init__(timeout=180)
        self.roles = roles
        self.action_type = action_type
        
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
    
    async def role_selected(self, interaction: discord.Interaction):
        role_id = int(self.select.values[0])
        role = interaction.guild.get_role(role_id)
        
        if self.action_type == "quick":
            link_code = role_link_system.create_role_link(
                server_id=interaction.guild.id,
                role_id=role.id,
                role_name=role.name,
                created_by=interaction.user.id,
                created_by_name=interaction.user.name,
                uses_limit=0,
                expires_hours=24
            )
            
            embed = discord.Embed(
                title="⚡ Команда создана",
                description=f"Роль: {role.mention}",
                color=0x00ff00
            )
            embed.add_field(name="Команда", value=f"```!роль {link_code}```", inline=False)
            embed.add_field(name="Статус", value="✅ 24 часа без ограничений", inline=True)
            
            view = LinkActionsView(link_code, role.name)
            await interaction.response.edit_message(embed=embed, view=view)
            
        else:
            embed = discord.Embed(
                title="⚙️ Настройки команды",
                description=f"Роль: {role.mention}",
                color=0x3498db
            )
            
            view = LinkSettingsView(role, interaction.user.id, interaction.user.name)
            await interaction.response.edit_message(embed=embed, view=view)

class LinkSettingsView(View):
    def __init__(self, role, creator_id, creator_name):
        super().__init__(timeout=180)
        self.role = role
        self.creator_id = creator_id
        self.creator_name = creator_name
    
    @discord.ui.button(label="1️⃣ Без ограничений", style=discord.ButtonStyle.secondary)
    async def unlimited_button(self, interaction: discord.Interaction, button: Button):
        await self.create_link(interaction, 0, 0)
    
    @discord.ui.button(label="2️⃣ 10 использований", style=discord.ButtonStyle.secondary)
    async def ten_uses_button(self, interaction: discord.Interaction, button: Button):
        await self.create_link(interaction, 10, 24)
    
    @discord.ui.button(label="3️⃣ 1 день", style=discord.ButtonStyle.secondary)
    async def one_day_button(self, interaction: discord.Interaction, button: Button):
        await self.create_link(interaction, 0, 24)
    
    @discord.ui.button(label="🎛️ Кастомные настройки", style=discord.ButtonStyle.primary)
    async def custom_button(self, interaction: discord.Interaction, button: Button):
        modal = CustomLinkModal(self.role)
        await interaction.response.send_modal(modal)
    
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

class QuickRoleView(View):
    def __init__(self, roles, user_id, user_name):
        super().__init__(timeout=180)
        self.roles = roles
        self.user_id = user_id
        self.user_name = user_name
        
        for i, role in enumerate(roles):
            button = Button(
                label=role.name[:15],
                style=discord.ButtonStyle.primary
            )
            button.callback = self.create_quick_link_callback(role)
            self.add_item(button)
    
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

class PermanentRoleView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🔗 Создать команду", style=discord.ButtonStyle.primary, emoji="🔗", custom_id="create_link_btn")
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
        
        view = RoleSelectView(roles, "create")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="📊 Активные команды", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="active_links_btn")
    async def active_links_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        links = role_link_system.get_active_links(interaction.guild.id)
        
        if not links:
            await interaction.followup.send("❌ Нет активных команд", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🔗 Активные команды",
            description=f"Всего активных команд: {len(links)}",
            color=0x3498db
        )
        
        # Показываем первые 5 команд с информацией о создателе
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
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="⚡ Быстрая команда", style=discord.ButtonStyle.success, emoji="⚡", custom_id="quick_link_btn")
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
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class MainPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🎮 Роли", style=discord.ButtonStyle.primary, emoji="🎮", custom_id="main_roles")
    async def roles_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🎮 Управление ролями",
            description="Выберите действие:",
            color=0x5865F2
        )
        
        view = PermanentRoleView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="👥 Участники", style=discord.ButtonStyle.secondary, emoji="👥", custom_id="main_members")
    async def members_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔄 Раздел участников в разработке...", ephemeral=True)
    
    @discord.ui.button(label="⚙️ Настройки", style=discord.ButtonStyle.success, emoji="⚙️", custom_id="main_settings")
    async def settings_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔄 Раздел настроек в разработке...", ephemeral=True)

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
    bot.add_view(PermanentRoleView())
    bot.add_view(MainPanelView())
    
    # Устанавливаем статус "Смотрящий за сервером"
    activity = discord.Activity(type=discord.ActivityType.watching, name="Смотрящий за сервером 👁️")
    await bot.change_presence(activity=activity)

# ========== КОМАНДЫ ДЛЯ СОЗДАНИЯ ПАНЕЛЕЙ ==========

@bot.command()
@commands.has_role('Admin')
async def создать_панель(ctx):
    """Создать закрепленную панель управления"""
    embed = discord.Embed(
        title="🎮 Управление ролями",
        description="Используйте кнопки ниже для управления ролями",
        color=0x5865F2
    )
    embed.add_field(
        name="🔗 Создать команду", 
        value="Создать команду для выдачи роли", 
        inline=True
    )
    embed.add_field(
        name="📊 Активные команды", 
        value="Просмотр всех активных команд", 
        inline=True
    )
    embed.add_field(
        name="⚡ Быстрая команда", 
        value="Создать команду без ограничений", 
        inline=True
    )
    
    view = PermanentRoleView()
    message = await ctx.send(embed=embed, view=view)
    
    try:
        await message.pin()
        await ctx.send("✅ Панель создана и закреплена!", delete_after=5)
    except:
        await ctx.send("✅ Панель создана! (Не удалось закрепить)", delete_after=5)
    
    await ctx.message.delete()

@bot.command()
@commands.has_role('Admin')
async def главная_панель(ctx):
    """Создать главную панель управления"""
    embed = discord.Embed(
        title="⚙️ Панель управления сервером",
        description="Выберите раздел для управления:",
        color=0x5865F2
    )
    
    embed.add_field(
        name="🎮 Управление ролями", 
        value="Создание и управление командами для ролей", 
        inline=False
    )
    embed.add_field(
        name="👥 Участники", 
        value="Управление участниками сервера", 
        inline=False
    )
    embed.add_field(
        name="⚙️ Настройки", 
        value="Настройки бота и сервера", 
        inline=False
    )
    
    view = MainPanelView()
    message = await ctx.send(embed=embed, view=view)
    
    try:
        await message.pin()
        await ctx.send("✅ Главная панель создана!", delete_after=5)
    except:
        pass
    
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
                # Проверяем, есть ли уже роль
                if role in ctx.author.roles:
                    await ctx.author.remove_roles(role)
                    message = await ctx.send(f"✅ Роль {role.mention} убрана!")
                else:
                    await ctx.author.add_roles(role)
                    message = await ctx.send(f"✅ Роль {role.mention} выдана!")
                
                # Удаляем сообщения через 10 секунд
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

# ========== КОМАНДА ДЛЯ РУЧНОГО СОЗДАНИЯ КОМАНД ==========

@bot.command()
@commands.has_role('Admin')
async def создать_команду(ctx, роль: discord.Role, использование: int = 0, часы: int = 24):
    """Создать команду для выдачи роли"""
    if использование > 1000:
        await ctx.send("❌ Максимальный лимит: 1000 использований", delete_after=5)
        return
    
    if часы > 8760:  # 1 год
        await ctx.send("❌ Максимальный срок: 8760 часов (1 год)", delete_after=5)
        return
    
    link_code = role_link_system.create_role_link(
        server_id=ctx.guild.id,
        role_id=роль.id,
        role_name=роль.name,
        created_by=ctx.author.id,
        created_by_name=ctx.author.name,
        uses_limit=использование,
        expires_hours=часы
    )
    
    embed = discord.Embed(
        title="🔗 Команда создана",
        description=f"Роль: {роль.mention}",
        color=0x00ff00
    )
    embed.add_field(name="Команда", value=f"`!роль {link_code}`", inline=True)
    embed.add_field(name="Лимит", value=f"{использование if использование > 0 else '∞'}", inline=True)
    embed.add_field(name="Срок", value=f"{часы if часы > 0 else '∞'} часов", inline=True)
    embed.add_field(name="Использование", value="Отправьте команду в чат чтобы получить роль", inline=False)
    
    await ctx.author.send(embed=embed)
    message = await ctx.send("✅ Команда создана! Проверьте личные сообщения.", delete_after=5)
    await ctx.message.delete()
    
    # Удаляем сообщение бота через 5 секунд
    await asyncio.sleep(5)
    await message.delete()

# ========== ОСНОВНЫЕ КОМАНДЫ ==========

@bot.command()
async def помощь(ctx):
    """Показать все команды"""
    embed = discord.Embed(
        title="📋 Команды Multi Bot",
        description="Доступные команды для управления",
        color=0x00ff00
    )
    
    embed.add_field(
        name="🎮 Панели управления",
        value="`!главная_панель` - главная панель\n`!создать_панель` - панель ролей",
        inline=False
    )
    
    embed.add_field(
        name="🔗 Управление ролями", 
        value="`!создать_команду @роль [лимит] [часы]` - создать команду\n`!роль код` - получить роль",
        inline=False
    )
    
    embed.add_field(
        name="⚙️ Система",
        value="`!очистить N` - удалить сообщения (админы)",
        inline=False
    )
    
    message = await ctx.send(embed=embed)
    
    # Удаляем сообщение помощи через 2 минуты
    await asyncio.sleep(120)
    try:
        await message.delete()
    except:
        pass

@bot.command()
@commands.has_permissions(administrator=True)
async def очистить(ctx, количество: int = 10):
    """Удалить сообщения (только для админов)"""
    await ctx.channel.purge(limit=количество + 1)
    msg = await ctx.send(f"🗑️ Удалено {количество} сообщений!")
    await asyncio.sleep(3)
    await msg.delete()

# ========== ВЕБ-МАРШРУТЫ ==========

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "multi-bot",
        "version": "1.0.0"
    })

# ========== ЗАПУСК ПРИЛОЖЕНИЯ ==========

if __name__ == '__main__':
    keep_alive()
    print(f"🚀 Запускаю Multi Bot на порту {port}")
    print(f"🔑 Токен: {'установлен' if TOKEN else 'НЕ УСТАНОВЛЕН!'}")
    
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")
