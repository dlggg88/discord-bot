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

@app.errorhandler(500)
def internal_error(error):
    return "❌ Internal Server Error", 500

@app.errorhandler(404)
def not_found(error):
    return "🔍 Page Not Found", 404

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
        
        # Таблица для склада
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS storage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                resource_name TEXT,
                resource_amount INTEGER DEFAULT 0,
                resource_description TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_by INTEGER,
                updated_by_name TEXT
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

# ========== СИСТЕМА СКЛАДА ==========
class StorageSystem:
    def add_resource(self, server_id: int, resource_name: str, amount: int, description: str, user_id: int, user_name: str):
        """Добавить или обновить ресурс на складе"""
        cursor = db.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO storage 
            (server_id, resource_name, resource_amount, resource_description, updated_by, updated_by_name)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (server_id, resource_name, amount, description, user_id, user_name))
        db.conn.commit()
    
    def get_resources(self, server_id: int) -> List:
        """Получить все ресурсы склада"""
        cursor = db.conn.cursor()
        cursor.execute('''
            SELECT resource_name, resource_amount, resource_description, updated_by_name, last_updated
            FROM storage 
            WHERE server_id = ?
            ORDER BY resource_name
        ''', (server_id,))
        return cursor.fetchall()
    
    def update_resource_amount(self, server_id: int, resource_name: str, new_amount: int, user_id: int, user_name: str):
        """Обновить количество ресурса"""
        cursor = db.conn.cursor()
        cursor.execute('''
            UPDATE storage 
            SET resource_amount = ?, updated_by = ?, updated_by_name = ?, last_updated = CURRENT_TIMESTAMP
            WHERE server_id = ? AND resource_name = ?
        ''', (new_amount, user_id, user_name, server_id, resource_name))
        db.conn.commit()
    
    def delete_resource(self, server_id: int, resource_name: str):
        """Удалить ресурс со склада"""
        cursor = db.conn.cursor()
        cursor.execute('''
            DELETE FROM storage 
            WHERE server_id = ? AND resource_name = ?
        ''', (server_id, resource_name))
        db.conn.commit()

storage_system = StorageSystem()

# ========== КОМПОНЕНТЫ ИНТЕРФЕЙСА ==========

class CopyLinkModal(Modal):
    def __init__(self, link_url):
        super().__init__(title="Копирование команды")
        self.link_url = link_url
        
        self.link_field = TextInput(
            label="Команда для копирования",
            default=link_url,
            style=discord.TextStyle.paragraph,
            placeholder="Скопируйте команду ниже"
        )
        self.add_item(self.link_field)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("Команда скопирована! Теперь вы можете вставить её в чат.", ephemeral=True)

class CustomLinkModal(Modal):
    def __init__(self, role):
        super().__init__(title="Кастомные настройки")
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
                created_by_name=str(interaction.user),
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
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except ValueError:
            await interaction.response.send_message("❌ Введите корректные числа", ephemeral=True)

class LinkActionsView(View):
    def __init__(self, link_code, role_name):
        super().__init__(timeout=300)
        self.link_code = link_code
        self.role_name = role_name
    
    @discord.ui.button(label="Копировать", style=discord.ButtonStyle.success, row=0)
    async def copy_command(self, interaction: discord.Interaction, button: Button):
        modal = CopyLinkModal(f"!роль {self.link_code}")
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Поделиться", style=discord.ButtonStyle.primary, row=0)
    async def share_link(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title=f"🔗 Получить роль: {self.role_name}",
            description="Используйте команду ниже чтобы получить роль:",
            color=0x5865F2
        )
        embed.add_field(name="Команда", value=f"```!роль {self.link_code}```", inline=False)
        embed.set_footer(text="Сообщение автоматически удалится через 1 минуту")
        
        message = await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ Сообщение отправлено в чат!", ephemeral=True)
        
        await asyncio.sleep(60)
        try:
            await message.delete()
        except:
            pass
    
    @discord.ui.button(label="Отправить", style=discord.ButtonStyle.secondary, row=1)
    async def quick_send(self, interaction: discord.Interaction, button: Button):
        message = await interaction.channel.send(f"**Получить роль '{self.role_name}':**\n```!роль {self.link_code}```")
        await interaction.response.send_message("✅ Команда отправлена в чат!", ephemeral=True)
        
        await asyncio.sleep(30)
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
        
    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: Button):
        if self.page > 0:
            await self.show_page(interaction, self.page - 1)
    
    @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: Button):
        if (self.page + 1) * self.links_per_page < len(self.links):
            await self.show_page(interaction, self.page + 1)
    
    @discord.ui.button(label="🔄", style=discord.ButtonStyle.primary)
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
            placeholder="Выберите роль...",
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
                created_by_name=str(interaction.user),
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
            
            view = LinkSettingsView(role, interaction.user.id, str(interaction.user))
            await interaction.response.edit_message(embed=embed, view=view)

class LinkSettingsView(View):
    def __init__(self, role, creator_id, creator_name):
        super().__init__(timeout=180)
        self.role = role
        self.creator_id = creator_id
        self.creator_name = creator_name
    
    @discord.ui.button(label="Без ограничений", style=discord.ButtonStyle.success, row=0)
    async def unlimited_button(self, interaction: discord.Interaction, button: Button):
        await self.create_link(interaction, 0, 0)
    
    @discord.ui.button(label="10 использований", style=discord.ButtonStyle.primary, row=0)
    async def ten_uses_button(self, interaction: discord.Interaction, button: Button):
        await self.create_link(interaction, 10, 24)
    
    @discord.ui.button(label="24 часа", style=discord.ButtonStyle.primary, row=1)
    async def one_day_button(self, interaction: discord.Interaction, button: Button):
        await self.create_link(interaction, 0, 24)
    
    @discord.ui.button(label="Кастомные", style=discord.ButtonStyle.secondary, row=1)
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
        
        for role in roles:
            button = Button(
                label=role.name[:15],
                style=discord.ButtonStyle.primary,
                custom_id=f"quick_role_{role.id}"
            )
            button.callback = self.create_quick_link_callback(role)
            self.add_item(button)
    
    def create_quick_link_callback(self, role):
        async def callback(interaction: discord.Interaction):
            try:
                await interaction.response.defer(ephemeral=True)
                
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
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                
            except Exception as e:
                print(f"Ошибка в quick role callback: {e}")
                await interaction.followup.send("❌ Ошибка при создании команды", ephemeral=True)
        
        return callback

# ========== СИСТЕМА СКЛАДА - МОДАЛЬНЫЕ ОКНА ==========

class AddResourceModal(Modal):
    def __init__(self):
        super().__init__(title="Добавить ресурс")
        
        self.resource_name = TextInput(
            label="Название ресурса",
            placeholder="Например: Дерево, Железо, Золото...",
            max_length=50,
            required=True
        )
        
        self.amount = TextInput(
            label="Количество",
            placeholder="Введите число",
            default="0",
            max_length=10,
            required=True
        )
        
        self.description = TextInput(
            label="Описание",
            placeholder="Описание ресурса...",
            style=discord.TextStyle.paragraph,
            required=False
        )
        
        self.add_item(self.resource_name)
        self.add_item(self.amount)
        self.add_item(self.description)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount.value)
            if amount < 0:
                await interaction.response.send_message("❌ Количество не может быть отрицательным", ephemeral=True)
                return
            
            storage_system.add_resource(
                server_id=interaction.guild.id,
                resource_name=self.resource_name.value,
                amount=amount,
                description=self.description.value,
                user_id=interaction.user.id,
                user_name=str(interaction.user)
            )
            
            await interaction.response.send_message(f"✅ Ресурс **{self.resource_name.value}** добавлен на склад в количестве `{amount}`", ephemeral=True)
            
        except ValueError:
            await interaction.response.send_message("❌ Введите корректное число для количества", ephemeral=True)

class UpdateResourceModal(Modal):
    def __init__(self, resource_name, current_amount):
        super().__init__(title="Обновить ресурс")
        self.resource_name = resource_name
        
        self.new_amount = TextInput(
            label=f"Новое количество для {resource_name}",
            placeholder=f"Текущее: {current_amount}",
            default=str(current_amount),
            max_length=10,
            required=True
        )
        
        self.add_item(self.new_amount)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_amount = int(self.new_amount.value)
            if new_amount < 0:
                await interaction.response.send_message("❌ Количество не может быть отрицательным", ephemeral=True)
                return
            
            storage_system.update_resource_amount(
                server_id=interaction.guild.id,
                resource_name=self.resource_name,
                new_amount=new_amount,
                user_id=interaction.user.id,
                user_name=str(interaction.user)
            )
            
            await interaction.response.send_message(f"✅ Ресурс **{self.resource_name}** обновлен. Новое количество: `{new_amount}`", ephemeral=True)
            
        except ValueError:
            await interaction.response.send_message("❌ Введите корректное число для количества", ephemeral=True)

# ========== ПАНЕЛЬ СКЛАДА В 1 ОКНЕ ==========

class StorageMainView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Обновить", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="storage_refresh", row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: Button):
        await self.show_storage(interaction)
    
    @discord.ui.button(label="Добавить", style=discord.ButtonStyle.success, emoji="📥", custom_id="storage_add", row=0)
    async def add_button(self, interaction: discord.Interaction, button: Button):
        modal = AddResourceModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Статистика", style=discord.ButtonStyle.primary, emoji="📈", custom_id="storage_stats", row=1)
    async def stats_button(self, interaction: discord.Interaction, button: Button):
        await self.show_statistics(interaction)
    
    @discord.ui.button(label="Управление", style=discord.ButtonStyle.secondary, emoji="⚙️", custom_id="storage_manage", row=1)
    async def manage_button(self, interaction: discord.Interaction, button: Button):
        await self.show_management(interaction)
    
    async def show_storage(self, interaction: discord.Interaction = None, is_response: bool = True):
        """Показать склад в одном окне"""
        try:
            resources = storage_system.get_resources(interaction.guild.id)
            
            embed = discord.Embed(
                title="📦 СКЛАД СЕРВЕРА",
                color=0x9567FE,
                timestamp=datetime.now()
            )
            
            if not resources:
                embed.description = "📭 Склад пуст. Добавьте ресурсы с помощью кнопки 'Добавить'"
                if is_response:
                    await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
                else:
                    await interaction.edit_original_response(embed=embed, view=self)
                return
            
            # Общая статистика
            total_resources = len(resources)
            total_amount = sum(amount for _, amount, _, _, _ in resources)
            
            embed.add_field(
                name="📊 ОБЩАЯ СТАТИСТИКА",
                value=f"**Ресурсов:** {total_resources}\n**Всего единиц:** {total_amount}",
                inline=False
            )
            
            # Таблица ресурсов в виде кода для лучшего отображения
            table_header = "┌─────────────────┬─────────────┬─────────────────┐\n"
            table_header += "│     РЕСУРС      │ КОЛИЧЕСТВО  │    ОБНОВЛЕНО    │\n"
            table_header += "├─────────────────┼─────────────┼─────────────────┤\n"
            
            table_rows = []
            for resource_name, amount, description, updated_by, last_updated in resources:
                # Обрезаем длинные названия
                name_display = resource_name[:14] + "..." if len(resource_name) > 14 else resource_name.ljust(14)
                amount_display = str(amount).ljust(10)
                
                # Форматируем время
                last_updated_dt = datetime.fromisoformat(last_updated)
                time_display = last_updated_dt.strftime("%d.%m %H:%M")
                
                table_rows.append(f"│ {name_display} │ {amount_display} │ {time_display} │")
            
            table_footer = "└─────────────────┴─────────────┴─────────────────┘"
            
            table_content = table_header + "\n".join(table_rows) + "\n" + table_footer
            
            embed.add_field(
                name="📋 ТАБЛИЦА РЕСУРСОВ",
                value=f"```{table_content}```",
                inline=False
            )
            
            # Информация о последнем обновлении
            if resources:
                last_resource = resources[0]
                embed.set_footer(text=f"Последнее обновление: {last_resource[3]}")
            
            if is_response:
                await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
            else:
                await interaction.edit_original_response(embed=embed, view=self)
            
        except Exception as e:
            print(f"Ошибка в show_storage: {e}")
            if is_response:
                await interaction.response.send_message("❌ Ошибка при загрузке склада", ephemeral=True)
            else:
                await interaction.followup.send("❌ Ошибка при загрузке склада", ephemeral=True)
    
    async def show_statistics(self, interaction: discord.Interaction):
        """Показать статистику склада"""
        try:
            resources = storage_system.get_resources(interaction.guild.id)
            
            if not resources:
                await interaction.response.send_message("📭 Склад пуст", ephemeral=True)
                return
            
            total_resources = len(resources)
            total_amount = sum(amount for _, amount, _, _, _ in resources)
            avg_amount = total_amount // total_resources if total_resources > 0 else 0
            
            # Самые популярные ресурсы
            top_resources = sorted(resources, key=lambda x: x[1], reverse=True)[:3]
            least_resources = sorted(resources, key=lambda x: x[1])[:3]
            
            embed = discord.Embed(
                title="📈 СТАТИСТИКА СКЛАДА",
                color=0x9567FE,
                timestamp=datetime.now()
            )
            
            # Основная статистика
            embed.add_field(
                name="📊 ОСНОВНЫЕ ПОКАЗАТЕЛИ",
                value=(
                    f"**Всего ресурсов:** {total_resources}\n"
                    f"**Общее количество:** {total_amount}\n"
                    f"**Среднее количество:** {avg_amount}"
                ),
                inline=False
            )
            
            # Топ ресурсов
            top_text = "\n".join([f"• **{name}** - `{amount}`" for name, amount, _, _, _ in top_resources])
            embed.add_field(
                name="🏆 ТОП-3 РЕСУРСА",
                value=top_text,
                inline=True
            )
            
            # Наименьшие ресурсы
            least_text = "\n".join([f"• **{name}** - `{amount}`" for name, amount, _, _, _ in least_resources])
            embed.add_field(
                name="📉 МИНИМАЛЬНЫЕ",
                value=least_text,
                inline=True
            )
            
            # Распределение
            if total_amount > 0:
                distribution = []
                for name, amount, _, _, _ in top_resources:
                    percentage = (amount / total_amount) * 100
                    distribution.append(f"• **{name}** - {percentage:.1f}%")
                
                embed.add_field(
                    name="📐 РАСПРЕДЕЛЕНИЕ",
                    value="\n".join(distribution),
                    inline=False
                )
            
            view = StorageMainView()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            print(f"Ошибка в show_statistics: {e}")
            await interaction.response.send_message("❌ Ошибка при загрузке статистики", ephemeral=True)
    
    async def show_management(self, interaction: discord.Interaction):
        """Показать управление ресурсами"""
        try:
            resources = storage_system.get_resources(interaction.guild.id)
            
            if not resources:
                await interaction.response.send_message("📭 Склад пуст. Сначала добавьте ресурсы", ephemeral=True)
                return
            
            embed = discord.Embed(
                title="⚙️ УПРАВЛЕНИЕ РЕСУРСАМИ",
                description="Выберите ресурс для управления:",
                color=0x9567FE
            )
            
            # Создаем кнопки для каждого ресурса
            view = ResourceManagementView(resources)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            print(f"Ошибка в show_management: {e}")
            await interaction.response.send_message("❌ Ошибка при загрузке управления", ephemeral=True)

class ResourceManagementView(View):
    def __init__(self, resources):
        super().__init__(timeout=180)
        self.resources = resources
        
        # Создаем выпадающий список для выбора ресурса
        self.select = Select(
            placeholder="Выберите ресурс...",
            options=[
                discord.SelectOption(
                    label=f"{name} ({amount})",
                    value=name,
                    description=description[:50] if description else "Без описания"
                ) for name, amount, description, _, _ in resources[:25]
            ]
        )
        self.select.callback = self.resource_selected
        self.add_item(self.select)
    
    async def resource_selected(self, interaction: discord.Interaction):
        resource_name = self.select.values[0]
        current_amount = next((amount for name, amount, _, _, _ in self.resources if name == resource_name), 0)
        
        embed = discord.Embed(
            title=f"⚙️ Управление: {resource_name}",
            description=f"Текущее количество: `{current_amount}`",
            color=0x9567FE
        )
        
        view = ResourceActionsView(resource_name, current_amount)
        await interaction.response.edit_message(embed=embed, view=view)

class ResourceActionsView(View):
    def __init__(self, resource_name, current_amount):
        super().__init__(timeout=180)
        self.resource_name = resource_name
        self.current_amount = current_amount
    
    @discord.ui.button(label="Изменить количество", style=discord.ButtonStyle.primary, row=0)
    async def edit_amount(self, interaction: discord.Interaction, button: Button):
        modal = UpdateResourceModal(self.resource_name, self.current_amount)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Удалить ресурс", style=discord.ButtonStyle.danger, row=0)
    async def delete_resource(self, interaction: discord.Interaction, button: Button):
        storage_system.delete_resource(interaction.guild.id, self.resource_name)
        await interaction.response.send_message(f"✅ Ресурс **{self.resource_name}** удален со склада", ephemeral=True)
    
    @discord.ui.button(label="Назад к складу", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: Button):
        view = StorageMainView()
        await view.show_storage(interaction, is_response=False)

# ========== ОСНОВНЫЕ ПАНЕЛИ ==========

class PermanentRoleView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Создать команду", style=discord.ButtonStyle.primary, emoji="🎮", custom_id="perm_create_link", row=0)
    async def create_link_button(self, interaction: discord.Interaction, button: Button):
        try:
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
            
        except Exception as e:
            print(f"Ошибка в create_link_button: {e}")
            await interaction.response.send_message("❌ Произошла ошибка при создании команды", ephemeral=True)
    
    @discord.ui.button(label="Активные команды", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="perm_active_links", row=0)
    async def active_links_button(self, interaction: discord.Interaction, button: Button):
        try:
            links = role_link_system.get_active_links(interaction.guild.id)
            
            if not links:
                await interaction.response.send_message("❌ Нет активных команд", ephemeral=True)
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
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            print(f"Ошибка в active_links_button: {e}")
            await interaction.response.send_message("❌ Произошла ошибка при загрузке команд", ephemeral=True)
    
    @discord.ui.button(label="Быстрая команда", style=discord.ButtonStyle.success, emoji="⚡", custom_id="perm_quick_link", row=1)
    async def quick_link_button(self, interaction: discord.Interaction, button: Button):
        try:
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
            view = QuickRoleView(popular_roles, interaction.user.id, str(interaction.user))
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            print(f"Ошибка в quick_link_button: {e}")
            await interaction.response.send_message("❌ Произошла ошибка при создании быстрой команды", ephemeral=True)
    
    @discord.ui.button(label="Помощь", style=discord.ButtonStyle.danger, emoji="❓", custom_id="perm_help", row=1)
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
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class MainPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Управление ролями", style=discord.ButtonStyle.primary, emoji="🎮", custom_id="main_roles", row=0)
    async def roles_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🎮 Управление ролями",
            description="Выберите действие:",
            color=0x5865F2
        )
        
        embed.add_field(
            name="🎮 СОЗДАТЬ КОМАНДУ",
            value="Создать команду для выдачи роли с настройками",
            inline=False
        )
        
        embed.add_field(
            name="📊 АКТИВНЫЕ КОМАНДЫ", 
            value="Просмотр всех активных команд и их статуса",
            inline=False
        )
        
        embed.add_field(
            name="⚡ БЫСТРАЯ КОМАНДА",
            value="Создать команду на 24 часа без ограничений",
            inline=False
        )
        
        view = PermanentRoleView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="Управление участниками", style=discord.ButtonStyle.secondary, emoji="👥", custom_id="main_members", row=0)
    async def members_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="👥 Управление участниками",
            description="Функции управления участниками:",
            color=0x3498db
        )
        
        embed.add_field(
            name="📊 Статистика",
            value="Просмотр статистики сервера",
            inline=True
        )
        
        embed.add_field(
            name="🎭 Массовая выдача ролей",
            value="Выдача ролей нескольким участникам",
            inline=True
        )
        
        embed.add_field(
            name="🔄 В разработке",
            value="Дополнительные функции скоро будут добавлены",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="Склад", style=discord.ButtonStyle.success, emoji="📦", custom_id="main_storage", row=1)
    async def storage_button(self, interaction: discord.Interaction, button: Button):
        view = StorageMainView()
        await view.show_storage(interaction)
    
    @discord.ui.button(label="О системе", style=discord.ButtonStyle.danger, emoji="ℹ️", custom_id="main_about", row=1)
    async def about_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="ℹ️ О системе Multi Bot",
            description="Многофункциональный бот для управления сервером",
            color=0x5865F2
        )
        
        embed.add_field(
            name="🚀 Возможности",
            value="• Управление ролями\n• Система команд\n• Управление складом\n• Модерация",
            inline=True
        )
        
        embed.add_field(
            name="📊 Статистика",
            value=f"• Серверов: {len(bot.guilds)}\n• Задержка: {round(bot.latency * 1000)}мс",
            inline=True
        )
        
        embed.add_field(
            name="🔧 Технологии",
            value="• Python 3.11\n• Discord.py\n• SQLite3\n• Flask",
            inline=False
        )
        
        embed.set_footer(text="Multi Bot System v2.0 | Разработано с ❤️")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ========== DISCORD BOT ==========
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN не установлен!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# ========== ОБРАБОТЧИКИ СОБЫТИЙ ==========

@bot.event
async def on_ready():
    print(f'🎉 Бот {bot.user} запущен!')
    print(f'📊 Подключен к {len(bot.guilds)} серверам')
    
    # Регистрируем постоянные кнопки
    bot.add_view(PermanentRoleView())
    bot.add_view(MainPanelView())
    bot.add_view(StorageMainView())
    
    # Устанавливаем статус
    activity = discord.Activity(type=discord.ActivityType.watching, name="за сервером")
    await bot.change_presence(activity=activity)

@bot.event
async def on_member_remove(member):
    """Автоматический бан при выходе пользователя"""
    try:
        # Баним всех, кто покидает сервер
        try:
            await member.ban(reason="Автоматический бан при выходе")
            print(f"🔨 Пользователь {member} забанен при выходе")
            
            # Логируем в канал
            log_channel = discord.utils.get(member.guild.text_channels, name="логи")
            if log_channel:
                embed = discord.Embed(
                    title="🔨 Автоматический бан",
                    description=f"Пользователь **{member}** забанен при выходе",
                    color=0xff0000,
                    timestamp=datetime.now()
                )
                embed.add_field(name="ID", value=member.id, inline=True)
                embed.add_field(name="Причина", value="Автоматический бан при выходе", inline=True)
                await log_channel.send(embed=embed)
                
        except discord.Forbidden:
            print(f"❌ Нет прав для бана пользователя {member}")
        except discord.HTTPException as e:
            print(f"❌ Ошибка при бане пользователя {member}: {e}")
                
    except Exception as e:
        print(f"Ошибка в автобане: {e}")

# ========== КОМАНДЫ ДЛЯ СОЗДАНИЯ ПАНЕЛЕЙ ==========

@bot.command()
@commands.has_permissions(administrator=True)
async def создать_панель(ctx):
    """Создать закрепленную панель управления"""
    embed = discord.Embed(
        title="🎮 Управление ролями",
        description="Используйте кнопки ниже для управления ролями",
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
    
    view = PermanentRoleView()
    message = await ctx.send(embed=embed, view=view)
    
    try:
        await message.pin()
    except:
        pass
    
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def главная_панель(ctx):
    """Создать главную панель управления"""
    embed = discord.Embed(
        title="⚙️ Панель управления сервером",
        description="Выберите раздел для управления:",
        color=0x5865F2
    )
    
    embed.add_field(
        name="🎮 УПРАВЛЕНИЕ РОЛЯМИ", 
        value="Создание и управление командами для ролей", 
        inline=False
    )
    embed.add_field(
        name="👥 УПРАВЛЕНИЕ УЧАСТНИКАМИ", 
        value="Управление участниками сервера", 
        inline=False
    )
    embed.add_field(
        name="📦 СКЛАД", 
        value="Управление ресурсами сервера", 
        inline=False
    )
    embed.add_field(
        name="ℹ️ О СИСТЕМЕ", 
        value="Информация о боте и его возможностях", 
        inline=False
    )
    
    view = MainPanelView()
    message = await ctx.send(embed=embed, view=view)
    
    try:
        await message.pin()
    except:
        pass
    
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def склад(ctx):
    """Создать отдельную панель склада"""
    view = StorageMainView()
    await view.show_storage(ctx)

# ========== КОМАНДА ДЛЯ ПОЛУЧЕНИЯ РОЛИ (СЕКРЕТНАЯ) ==========

@bot.command()
async def роль(ctx, код: str = None):
    """Получить роль по коду команды (секретно)"""
    if not код:
        # Секретное сообщение, которое удалится сразу
        message = await ctx.send("❌ Укажите код команды: `!роль КОД`")
        await asyncio.sleep(5)
        await ctx.message.delete()
        await message.delete()
        return
    
    # Сразу удаляем команду пользователя
    await ctx.message.delete()
    
    result = role_link_system.use_role_link(код, ctx.guild.id)
    
    if result["success"]:
        role_id = result["role_id"]
        role = ctx.guild.get_role(role_id)
        
        if role:
            try:
                if role in ctx.author.roles:
                    await ctx.author.remove_roles(role)
                    # Отправляем секретное сообщение в ЛС
                    try:
                        await ctx.author.send(f"✅ Роль **{role.name}** убрана!")
                    except:
                        # Если ЛС закрыты, отправляем временное сообщение в чат
                        message = await ctx.send(f"✅ {ctx.author.mention}, роль **{role.name}** убрана!", delete_after=5)
                else:
                    await ctx.author.add_roles(role)
                    # Отправляем секретное сообщение в ЛС
                    try:
                        await ctx.author.send(f"✅ Роль **{role.name}** выдана!")
                    except:
                        # Если ЛС закрыты, отправляем временное сообщение в чат
                        message = await ctx.send(f"✅ {ctx.author.mention}, роль **{role.name}** выдана!", delete_after=5)
                
            except discord.Forbidden:
                try:
                    await ctx.author.send("❌ У бота нет прав для выдачи ролей")
                except:
                    message = await ctx.send("❌ У бота нет прав для выдачи ролей", delete_after=5)
        else:
            try:
                await ctx.author.send("❌ Роль не найдена на сервере")
            except:
                message = await ctx.send("❌ Роль не найдена на сервере", delete_after=5)
    else:
        try:
            await ctx.author.send(f"❌ {result['error']}")
        except:
            message = await ctx.send(f"❌ {result['error']}", delete_after=5)

# ========== ЗАПУСК ПРИЛОЖЕНИЯ ==========
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
