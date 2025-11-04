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

# ========== DISCORD BOT ==========
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN не установлен!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

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

# ========== БАЗА ДАННЫХ ДЛЯ СКЛАДА ==========
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('bot_data.db', check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        # Таблица для учета склада
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS warehouse (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                item_name TEXT NOT NULL,
                category TEXT,
                quantity INTEGER DEFAULT 0,
                unit TEXT DEFAULT 'шт.',
                min_stock INTEGER DEFAULT 0,
                location TEXT,
                notes TEXT,
                created_by INTEGER,
                created_by_name TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица истории движений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                item_id INTEGER,
                item_name TEXT,
                change_type TEXT, -- 'incoming', 'outgoing', 'adjustment'
                quantity_change INTEGER,
                previous_quantity INTEGER,
                new_quantity INTEGER,
                reason TEXT,
                created_by INTEGER,
                created_by_name TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()

db = Database()

# ========== СИСТЕМА УЧЕТА СКЛАДА ==========
class WarehouseSystem:
    def __init__(self):
        pass
    
    def add_item(self, server_id: int, item_name: str, category: str, quantity: int, 
                 unit: str, min_stock: int, location: str, notes: str, 
                 created_by: int, created_by_name: str) -> int:
        cursor = db.conn.cursor()
        cursor.execute('''
            INSERT INTO warehouse 
            (server_id, item_name, category, quantity, unit, min_stock, location, notes, created_by, created_by_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (server_id, item_name, category, quantity, unit, min_stock, location, notes, created_by, created_by_name))
        
        item_id = cursor.lastrowid
        
        # Записываем в историю
        cursor.execute('''
            INSERT INTO stock_movements 
            (server_id, item_id, item_name, change_type, quantity_change, previous_quantity, new_quantity, reason, created_by, created_by_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (server_id, item_id, item_name, 'incoming', quantity, 0, quantity, 'Первое поступление', created_by, created_by_name))
        
        db.conn.commit()
        return item_id
    
    def update_quantity(self, server_id: int, item_id: int, new_quantity: int, 
                       change_type: str, reason: str, created_by: int, created_by_name: str) -> bool:
        cursor = db.conn.cursor()
        
        # Получаем текущее количество
        cursor.execute('SELECT quantity FROM warehouse WHERE id = ? AND server_id = ?', (item_id, server_id))
        result = cursor.fetchone()
        
        if not result:
            return False
        
        previous_quantity = result[0]
        quantity_change = new_quantity - previous_quantity
        
        # Обновляем количество
        cursor.execute('''
            UPDATE warehouse SET quantity = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ? AND server_id = ?
        ''', (new_quantity, item_id, server_id))
        
        # Получаем название товара
        cursor.execute('SELECT item_name FROM warehouse WHERE id = ?', (item_id,))
        item_name = cursor.fetchone()[0]
        
        # Записываем в историю
        cursor.execute('''
            INSERT INTO stock_movements 
            (server_id, item_id, item_name, change_type, quantity_change, previous_quantity, new_quantity, reason, created_by, created_by_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (server_id, item_id, item_name, change_type, quantity_change, previous_quantity, new_quantity, reason, created_by, created_by_name))
        
        db.conn.commit()
        return True
    
    def delete_item(self, server_id: int, item_id: int) -> bool:
        cursor = db.conn.cursor()
        cursor.execute('DELETE FROM warehouse WHERE id = ? AND server_id = ?', (item_id, server_id))
        db.conn.commit()
        return cursor.rowcount > 0
    
    def get_warehouse_items(self, server_id: int) -> List:
        cursor = db.conn.cursor()
        cursor.execute('''
            SELECT id, item_name, category, quantity, unit, min_stock, location, notes, created_by_name, updated_at
            FROM warehouse 
            WHERE server_id = ?
            ORDER BY item_name
        ''', (server_id,))
        return cursor.fetchall()
    
    def get_stock_movements(self, server_id: int, days: int = 7) -> List:
        cursor = db.conn.cursor()
        since_date = datetime.now() - timedelta(days=days)
        cursor.execute('''
            SELECT item_name, change_type, quantity_change, new_quantity, reason, created_by_name, created_at
            FROM stock_movements 
            WHERE server_id = ? AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT 50
        ''', (server_id, since_date))
        return cursor.fetchall()

warehouse_system = WarehouseSystem()

# ========== МОДАЛЬНЫЕ ОКНА ДЛЯ СКЛАДА ==========

class AddItemModal(Modal):
    def __init__(self):
        super().__init__(title="📦 Добавить предмет")
        
        self.item_name = TextInput(
            label="Название предмета",
            placeholder="Например: Клавиатура Logitech",
            required=True,
            max_length=100
        )
        
        self.category = TextInput(
            label="Категория",
            placeholder="Например: Компьютерная техника",
            required=True,
            max_length=50
        )
        
        self.quantity = TextInput(
            label="Количество",
            placeholder="Например: 10",
            required=True,
            max_length=10
        )
        
        self.unit = TextInput(
            label="Единица измерения",
            placeholder="Например: шт., упак., кг",
            default="шт.",
            required=True,
            max_length=10
        )
        
        self.min_stock = TextInput(
            label="Минимальный запас",
            placeholder="0 - без контроля",
            default="0",
            required=True,
            max_length=10
        )
        
        self.location = TextInput(
            label="Место хранения",
            placeholder="Например: Стеллаж A-1",
            required=False,
            max_length=50
        )
        
        self.notes = TextInput(
            label="Примечания",
            placeholder="Дополнительная информация",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500
        )
        
        self.add_item(self.item_name)
        self.add_item(self.category)
        self.add_item(self.quantity)
        self.add_item(self.unit)
        self.add_item(self.min_stock)
        self.add_item(self.location)
        self.add_item(self.notes)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity = int(self.quantity.value)
            min_stock = int(self.min_stock.value)
            
            if quantity < 0 or min_stock < 0:
                await interaction.response.send_message("❌ Количество не может быть отрицательным", ephemeral=True)
                return
            
            item_id = warehouse_system.add_item(
                server_id=interaction.guild.id,
                item_name=self.item_name.value,
                category=self.category.value,
                quantity=quantity,
                unit=self.unit.value,
                min_stock=min_stock,
                location=self.location.value,
                notes=self.notes.value,
                created_by=interaction.user.id,
                created_by_name=interaction.user.name
            )
            
            await interaction.response.send_message(
                f"✅ Предмет '{self.item_name.value}' добавлен на склад!", 
                ephemeral=True
            )
            
            # Обновляем панель склада
            await WarehousePanel.show_warehouse(interaction)
            
        except ValueError:
            await interaction.response.send_message("❌ Введите корректные числа для количества", ephemeral=True)

class UpdateQuantityModal(Modal):
    def __init__(self, item_id, item_name, current_quantity):
        super().__init__(title="📊 Изменить количество")
        self.item_id = item_id
        self.item_name = item_name
        self.current_quantity = current_quantity
        
        self.new_quantity = TextInput(
            label=f"Текущее количество: {current_quantity}",
            placeholder="Введите новое количество",
            required=True,
            max_length=10
        )
        
        self.reason = TextInput(
            label="Причина изменения",
            placeholder="Например: Поступление, Списание, Инвентаризация",
            required=True,
            max_length=100
        )
        
        self.add_item(self.new_quantity)
        self.add_item(self.reason)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_quantity = int(self.new_quantity.value)
            
            success = warehouse_system.update_quantity(
                server_id=interaction.guild.id,
                item_id=self.item_id,
                new_quantity=new_quantity,
                change_type='adjustment',
                reason=self.reason.value,
                created_by=interaction.user.id,
                created_by_name=interaction.user.name
            )
            
            if success:
                await interaction.response.send_message(
                    f"✅ Количество '{self.item_name}' изменено: {self.current_quantity} → {new_quantity}", 
                    ephemeral=True
                )
                await WarehousePanel.show_warehouse(interaction)
            else:
                await interaction.response.send_message("❌ Предмет не найден", ephemeral=True)
                
        except ValueError:
            await interaction.response.send_message("❌ Введите корректное число", ephemeral=True)

class IncomingModal(Modal):
    def __init__(self, item_id, item_name, current_quantity):
        super().__init__(title="📥 Приход предмета")
        self.item_id = item_id
        self.item_name = item_name
        self.current_quantity = current_quantity
        
        self.quantity_to_add = TextInput(
            label=f"Текущее количество: {current_quantity}",
            placeholder="Сколько единиц добавить?",
            required=True,
            max_length=10
        )
        
        self.reason = TextInput(
            label="Причина поступления",
            placeholder="Например: Закупка, Возврат",
            required=True,
            max_length=100
        )
        
        self.add_item(self.quantity_to_add)
        self.add_item(self.reason)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity_to_add = int(self.quantity_to_add.value)
            new_quantity = self.current_quantity + quantity_to_add
            
            if quantity_to_add <= 0:
                await interaction.response.send_message("❌ Количество должно быть положительным", ephemeral=True)
                return
            
            success = warehouse_system.update_quantity(
                server_id=interaction.guild.id,
                item_id=self.item_id,
                new_quantity=new_quantity,
                change_type='incoming',
                reason=self.reason.value,
                created_by=interaction.user.id,
                created_by_name=interaction.user.name
            )
            
            if success:
                await interaction.response.send_message(
                    f"✅ Приход '{self.item_name}': +{quantity_to_add} (всего: {new_quantity})", 
                    ephemeral=True
                )
                await WarehousePanel.show_warehouse(interaction)
            else:
                await interaction.response.send_message("❌ Предмет не найден", ephemeral=True)
                
        except ValueError:
            await interaction.response.send_message("❌ Введите корректное число", ephemeral=True)

class OutgoingModal(Modal):
    def __init__(self, item_id, item_name, current_quantity):
        super().__init__(title="📤 Расход предмета")
        self.item_id = item_id
        self.item_name = item_name
        self.current_quantity = current_quantity
        
        self.quantity_to_remove = TextInput(
            label=f"Текущее количество: {current_quantity}",
            placeholder="Сколько единиц списать?",
            required=True,
            max_length=10
        )
        
        self.reason = TextInput(
            label="Причина списания",
            placeholder="Например: Продажа, Использование, Брак",
            required=True,
            max_length=100
        )
        
        self.add_item(self.quantity_to_remove)
        self.add_item(self.reason)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity_to_remove = int(self.quantity_to_remove.value)
            new_quantity = self.current_quantity - quantity_to_remove
            
            if quantity_to_remove <= 0:
                await interaction.response.send_message("❌ Количество должно быть положительным", ephemeral=True)
                return
            
            if new_quantity < 0:
                await interaction.response.send_message("❌ Недостаточно предметов на складе", ephemeral=True)
                return
            
            success = warehouse_system.update_quantity(
                server_id=interaction.guild.id,
                item_id=self.item_id,
                new_quantity=new_quantity,
                change_type='outgoing',
                reason=self.reason.value,
                created_by=interaction.user.id,
                created_by_name=interaction.user.name
            )
            
            if success:
                await interaction.response.send_message(
                    f"✅ Списание '{self.item_name}': -{quantity_to_remove} (осталось: {new_quantity})", 
                    ephemeral=True
                )
                await WarehousePanel.show_warehouse(interaction)
            else:
                await interaction.response.send_message("❌ Предмет не найден", ephemeral=True)
                
        except ValueError:
            await interaction.response.send_message("❌ Введите корректное число", ephemeral=True)

# ========== ОТДЕЛЬНАЯ ПАНЕЛЬ СКЛАДА ==========

class WarehousePanel(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="ДОБАВИТЬ", style=discord.ButtonStyle.success, emoji="📦", row=0)
    async def add_item(self, interaction: discord.Interaction, button: Button):
        modal = AddItemModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="ПРЕДМЕТЫ", style=discord.ButtonStyle.primary, emoji="📋", row=0)
    async def show_items(self, interaction: discord.Interaction, button: Button):
        await self.show_warehouse(interaction)
    
    @discord.ui.button(label="ИСТОРИЯ", style=discord.ButtonStyle.secondary, emoji="📊", row=0)
    async def history(self, interaction: discord.Interaction, button: Button):
        await self.show_history(interaction)
    
    @discord.ui.button(label="ГЛАВНАЯ", style=discord.ButtonStyle.secondary, emoji="🏠", row=1)
    async def main_menu(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🏠 Главное меню",
            description="Выберите панель для управления:",
            color=0x5865F2
        )
        
        view = MainMenuView()
        await interaction.response.edit_message(embed=embed, view=view)
    
    @classmethod
    async def show_warehouse(cls, interaction: discord.Interaction):
        items = warehouse_system.get_warehouse_items(interaction.guild.id)
        
        embed = discord.Embed(
            title="📦 Учет склада",
            description=f"Всего предметов: {len(items)}",
            color=0x3498db
        )
        
        if not items:
            embed.description = "📭 Склад пуст"
            view = WarehousePanel()
            await interaction.response.edit_message(embed=embed, view=view)
            return
        
        # Показываем все предметы в виде таблицы
        items_text = ""
        for item in items[:20]:  # Ограничиваем количество предметов
            item_id, name, category, quantity, unit, min_stock, location, notes, created_by, updated = item
            
            status = "🟢"
            if min_stock > 0 and quantity <= min_stock:
                status = "🟡" if quantity > 0 else "🔴"
            
            item_line = f"{status} **{name}** - {quantity} {unit}"
            if location:
                item_line += f" | 🗂️ {location}"
            
            items_text += f"{item_line}\n"
        
        embed.add_field(
            name="📋 Список предметов",
            value=items_text,
            inline=False
        )
        
        if len(items) > 20:
            embed.set_footer(text=f"Показано 20 из {len(items)} предметов")
        
        view = WarehousePanel()
        await interaction.response.edit_message(embed=embed, view=view)
    
    @classmethod
    async def show_history(cls, interaction: discord.Interaction):
        movements = warehouse_system.get_stock_movements(interaction.guild.id, 7)
        
        embed = discord.Embed(
            title="📊 История движений (7 дней)",
            color=0x9b59b6
        )
        
        if not movements:
            embed.description = "📭 Нет движений за последние 7 дней"
            view = WarehousePanel()
            await interaction.response.edit_message(embed=embed, view=view)
            return
        
        for movement in movements[:10]:  # Ограничиваем количество записей
            item_name, change_type, quantity_change, new_quantity, reason, created_by, created_at = movement
            
            if change_type == 'incoming':
                emoji = "📥"
                change_text = f"+{quantity_change}"
            elif change_type == 'outgoing':
                emoji = "📤"
                change_text = f"-{quantity_change}"
            else:
                emoji = "📊"
                change_text = f"→ {new_quantity}"
            
            created_dt = datetime.fromisoformat(created_at)
            time_text = created_dt.strftime("%d.%m %H:%M")
            
            embed.add_field(
                name=f"{emoji} {item_name}",
                value=f"**Изменение:** {change_text}\n**Причина:** {reason}\n**Кто:** {created_by}\n**Когда:** {time_text}",
                inline=False
            )
        
        view = WarehousePanel()
        await interaction.response.edit_message(embed=embed, view=view)

# ========== ОТДЕЛЬНАЯ ПАНЕЛЬ РОЛЕЙ ==========

class RolesPanel(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="СОЗДАТЬ КОМАНДУ", style=discord.ButtonStyle.primary, emoji="🎮", row=0)
    async def create_command(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🎮 Создание команды",
            description="Функция в разработке...",
            color=0x5865F2
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="АКТИВНЫЕ КОМАНДЫ", style=discord.ButtonStyle.secondary, emoji="📊", row=0)
    async def active_commands(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="📊 Активные команды",
            description="Функция в разработке...",
            color=0x3498db
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="БЫСТРАЯ КОМАНДА", style=discord.ButtonStyle.success, emoji="⚡", row=1)
    async def quick_command(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="⚡ Быстрая команда",
            description="Функция в разработке...",
            color=0x00ff00
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="ГЛАВНАЯ", style=discord.ButtonStyle.secondary, emoji="🏠", row=1)
    async def main_menu(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🏠 Главное меню",
            description="Выберите панель для управления:",
            color=0x5865F2
        )
        
        view = MainMenuView()
        await interaction.response.edit_message(embed=embed, view=view)

# ========== ГЛАВНОЕ МЕНЮ ==========

class MainMenuView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="УПРАВЛЕНИЕ РОЛЯМИ", style=discord.ButtonStyle.primary, emoji="🎮", row=0)
    async def roles_panel(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🎮 Управление ролями",
            description="Выберите действие:",
            color=0x5865F2
        )
        
        view = RolesPanel()
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="УЧЕТ СКЛАДА", style=discord.ButtonStyle.success, emoji="📦", row=0)
    async def warehouse_panel(self, interaction: discord.Interaction, button: Button):
        await WarehousePanel.show_warehouse(interaction)
    
    @discord.ui.button(label="НАСТРОЙКИ", style=discord.ButtonStyle.secondary, emoji="⚙️", row=1)
    async def settings_panel(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="⚙️ Настройки",
            description="Раздел в разработке...",
            color=0x95a5a6
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="ПОМОЩЬ", style=discord.ButtonStyle.danger, emoji="❓", row=1)
    async def help_panel(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="❓ Помощь",
            description="**Доступные команды:**\n\n`!панель` - Создать главную панель\n`!склад` - Создать панель склада\n`!роли` - Создать панель ролей",
            color=0xe74c3c
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ========== КОМАНДЫ БОТА ==========

@bot.event
async def on_ready():
    print(f'🎉 Бот {bot.user} запущен!')
    print(f'📊 Подключен к {len(bot.guilds)} серверам')
    
    # Регистрируем постоянные кнопки
    bot.add_view(MainMenuView())
    bot.add_view(RolesPanel())
    bot.add_view(WarehousePanel())
    
    # Устанавливаем статус
    try:
        activity = discord.Activity(type=discord.ActivityType.watching, name="за сервером 👁️")
        await bot.change_presence(activity=activity, status=discord.Status.online)
        print("✅ Статус бота установлен: 'Смотрящий за сервером 👁️'")
    except Exception as e:
        print(f"⚠️ Не удалось установить статус: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def панель(ctx):
    """Создать главную панель управления"""
    embed = discord.Embed(
        title="🏠 Главное меню",
        description="Выберите панель для управления:",
        color=0x5865F2
    )
    
    embed.add_field(
        name="🎮 УПРАВЛЕНИЕ РОЛЯМИ", 
        value="Создание команд для выдачи ролей", 
        inline=True
    )
    embed.add_field(
        name="📦 УЧЕТ СКЛАДА", 
        value="Управление предметами и запасами", 
        inline=True
    )
    embed.add_field(
        name="⚙️ НАСТРОЙКИ", 
        value="Настройки системы", 
        inline=True
    )
    embed.add_field(
        name="❓ ПОМОЩЬ", 
        value="Инструкция по использованию", 
        inline=True
    )
    
    view = MainMenuView()
    message = await ctx.send(embed=embed, view=view)
    
    try:
        await message.pin()
        await ctx.send("✅ Главная панель создана и закреплена!", delete_after=5)
    except:
        await ctx.send("✅ Главная панель создана! (Не удалось закрепить)", delete_after=5)
    
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def склад(ctx):
    """Создать панель учета склада"""
    embed = discord.Embed(
        title="📦 Учет склада",
        description="Управление предметами и запасами:",
        color=0x3498db
    )
    
    embed.add_field(
        name="📦 ДОБАВИТЬ", 
        value="Добавить новый предмет", 
        inline=True
    )
    embed.add_field(
        name="📋 ПРЕДМЕТЫ", 
        value="Просмотр всех предметов", 
        inline=True
    )
    embed.add_field(
        name="📊 ИСТОРИЯ", 
        value="История движений", 
        inline=True
    )
    embed.add_field(
        name="🏠 ГЛАВНАЯ", 
        value="Вернуться в главное меню", 
        inline=True
    )
    
    view = WarehousePanel()
    message = await ctx.send(embed=embed, view=view)
    
    try:
        await message.pin()
        await ctx.send("✅ Панель склада создана и закреплена!", delete_after=5)
    except:
        await ctx.send("✅ Панель склада создана! (Не удалось закрепить)", delete_after=5)
    
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def роли(ctx):
    """Создать панель управления ролями"""
    embed = discord.Embed(
        title="🎮 Управление ролями",
        description="Создание команд для выдачи ролей:",
        color=0x5865F2
    )
    
    embed.add_field(
        name="🎮 СОЗДАТЬ КОМАНДУ", 
        value="Создать команду для роли", 
        inline=True
    )
    embed.add_field(
        name="📊 АКТИВНЫЕ КОМАНДЫ", 
        value="Просмотр активных команд", 
        inline=True
    )
    embed.add_field(
        name="⚡ БЫСТРАЯ КОМАНДА", 
        value="Быстрое создание команды", 
        inline=True
    )
    embed.add_field(
        name="🏠 ГЛАВНАЯ", 
        value="Вернуться в главное меню", 
        inline=True
    )
    
    view = RolesPanel()
    message = await ctx.send(embed=embed, view=view)
    
    try:
        await message.pin()
        await ctx.send("✅ Панель ролей создана и закреплена!", delete_after=5)
    except:
        await ctx.send("✅ Панель ролей создана! (Не удалось закрепить)", delete_after=5)
    
    await ctx.message.delete()

# ========== ЗАПУСК ПРИЛОЖЕНИЯ ==========

if __name__ == '__main__':
    keep_alive()
    print(f"🚀 Запускаю Multi Bot на порту {port}")
    
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")
