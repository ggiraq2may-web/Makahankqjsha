"""Kro GeoIP: player lookup and official Midasbuy Turkey shop navigation.

A shop URL is NOT an account/region mutation, purchase authorization, or verification.
No payment or geographic restrictions are bypassed.
"""
import asyncio
import logging
import math
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

LOG = logging.getLogger('kro_geoip')
PLAYER_ID_RE = re.compile(r'^[0-9]{5,20}$')
DB_PATH = os.getenv('DB_PATH', '/data/kro_geoip.db' if Path('/data').is_dir() else 'kro_geoip.db')
TARGET_REGION = 'Turkey'
# Public official Midasbuy listing (not an API or a region-changing endpoint).
TURKEY_SHOP_URL = 'https://www.midasbuy.com/midasbuy/tr/buy/pubgm'
OFFICIAL_FAQ_URL = 'https://cdn.midasbuy.com/oversea_web/faq/faq.html'


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def connect():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute('PRAGMA busy_timeout=10000')
        enable_wal(conn)
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            ensure_schema(conn)
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def enable_wal(conn):
    deadline = time.monotonic() + 10
    while True:
        try:
            conn.execute('PRAGMA journal_mode=WAL').fetchone()
            return
        except sqlite3.OperationalError as exc:
            # Concurrent first opens can report BUSY before the busy handler runs.
            error_name = getattr(exc, 'sqlite_errorname', None)
            busy = error_name == 'SQLITE_BUSY' or str(exc) == 'database is locked'
            if not busy or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS lookups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL, player_id TEXT NOT NULL,
        nickname TEXT, player_region TEXT, shop_region TEXT,
        status TEXT NOT NULL, created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS operations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL, player_id TEXT NOT NULL,
        target_region TEXT NOT NULL, operation_status TEXT NOT NULL,
        verification_status TEXT NOT NULL, evidence_source TEXT,
        created_at TEXT NOT NULL)''')
    # Upgrade existing installations without deleting their audit history.
    cols = {r[1] for r in conn.execute('PRAGMA table_info(operations)')}
    if 'lookup_id' not in cols:
        conn.execute('ALTER TABLE operations ADD COLUMN lookup_id INTEGER')
    conn.execute('CREATE INDEX IF NOT EXISTS lookups_user_idx ON lookups(user_id, id)')
    conn.execute('CREATE INDEX IF NOT EXISTS operations_user_idx ON operations(user_id, id)')
    conn.execute('CREATE INDEX IF NOT EXISTS operations_lookup_idx ON operations(lookup_id)')


def record(user_id, player_id, result):
    with closing(connect()) as conn, conn:
        lookup_cursor = conn.execute('''INSERT INTO lookups
            (user_id, player_id, nickname, player_region, shop_region, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (user_id, player_id, result.get('nickname'), result.get('player_region'),
             None, result['status'], utcnow()))
        # Showing an official storefront is not an account or Midasbuy state mutation.
        conn.execute('''INSERT INTO operations
            (user_id, player_id, target_region, operation_status,
             verification_status, evidence_source, created_at, lookup_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, player_id, TARGET_REGION, 'NOT_PERFORMED',
             'UNVERIFIED', None, utcnow(), lookup_cursor.lastrowid))


def provider_config():
    """Never automatically trust a URL returned from player/provider input."""
    template = os.getenv('PLAYER_LOOKUP_URL', '').strip()
    if not template:
        return None
    if template.count('{player_id}') != 1:
        raise ValueError('PLAYER_LOOKUP_URL must contain {player_id} exactly once')
    parsed = urlparse(template)
    if (parsed.scheme != 'https' or not parsed.hostname or '@' in parsed.netloc
            or '{player_id}' in parsed.netloc or '#' in template):
        raise ValueError('PLAYER_LOOKUP_URL must be a valid HTTPS URL without URL credentials')
    try:
        httpx.URL(template.replace('{player_id}', '123456'))
        if parsed.port == 0:
            raise ValueError('invalid port')
    except (httpx.InvalidURL, ValueError) as exc:
        raise ValueError('PLAYER_LOOKUP_URL has an invalid host or port') from exc
    return template


def request_timeout():
    timeout = float(os.getenv('REQUEST_TIMEOUT', '15'))
    if not math.isfinite(timeout):
        raise ValueError('REQUEST_TIMEOUT must be finite')
    return min(max(timeout, 1), 60)


def lookup_cooldown():
    return max(int(os.getenv('LOOKUP_COOLDOWN_SECONDS', '15')), 0)


async def lookup_player(player_id, client=None):
    if not isinstance(player_id, str) or not PLAYER_ID_RE.fullmatch(player_id):
        return {'status': 'INVALID_PLAYER_ID'}
    template = provider_config()
    if template is None:
        return {'status': 'PROVIDER_NOT_CONFIGURED'}
    url = template.replace('{player_id}', quote(player_id, safe=''))
    headers = {'Accept': 'application/json'}
    token = os.getenv('PLAYER_LOOKUP_TOKEN', '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    timeout = request_timeout()
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        try:
            response = await client.get(url, headers=headers, timeout=timeout, follow_redirects=False)
            if response.status_code == 429:
                return {'status': 'RATE_LIMITED'}
            if response.status_code in (401, 403):
                return {'status': 'PROVIDER_UNAUTHORIZED'}
            if response.status_code == 404:
                return {'status': 'PLAYER_NOT_FOUND'}
            if response.status_code in (301, 302, 303, 307, 308):
                return {'status': 'PROVIDER_REDIRECT_REJECTED'}
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            return {'status': 'TIMEOUT'}
        except (httpx.HTTPError, ValueError):
            return {'status': 'PROVIDER_FAILED'}
        if not isinstance(data, dict):
            return {'status': 'INVALID_RESPONSE'}
        # A provider MUST document which field means player_region; a numeric
        # zone_id alone cannot be interpreted as a geographic country.
        info = data.get('info', data)
        if not isinstance(info, dict):
            return {'status': 'INVALID_RESPONSE'}
        if data.get('success') is False:
            return {'status': 'PLAYER_NOT_FOUND'}
        name = next((info[k] for k in ('nickname', 'charac_name', 'player_name', 'name')
                     if isinstance(info.get(k), str) and info[k].strip()), None)
        region = next((info[k] for k in ('player_region', 'user_region')
                       if isinstance(info.get(k), str) and info[k].strip()), None)
        if not name:
            return {'status': 'UNRECOGNIZED_RESPONSE'}
        return {'status': 'LOOKUP_SUCCESS', 'nickname': name[:100],
                'player_region': region[:100] if region else None}
    finally:
        if own_client:
            await client.aclose()


def keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('GeoIP | استعلام', callback_data='lookup')],
        [InlineKeyboardButton('متجر تركيا الرسمي', url=TURKEY_SHOP_URL)],
        [InlineKeyboardButton('شروط المنطقة والدفع', callback_data='eligibility')],
        [InlineKeyboardButton('حول الخدمة', callback_data='about')],
    ])


def purchase_note():
    return (
        'متجر تركيا الرسمي متاح للفتح، لكن عرض الصفحة لا يعني أن حسابك مؤهل '
        'للشراء منها. قد يقيّد Midasbuy طرق الدفع أو الشراء وفق منطقة اللاعب '
        'والحساب. تحقق من الدولة والعملة والمنتج ورسالة الأهلية في الموقع '
        'الرسمي قبل الدفع. البوت لا يغيّر منطقة الحساب أو المتجر المحفوظة '
        'ولا يعالج المدفوعات أو يتجاوز القيود.'
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('awaiting', None)
    await update.message.reply_text('Kro GeoIP\n\nاختر استعلام اللاعب أو افتح متجر تركيا الرسمي.\nلا توجد خدمة تغيير منطقة آلية مثبتة.', reply_markup=keyboard())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('awaiting', None)
    await update.message.reply_text('تم إلغاء الإدخال.', reply_markup=keyboard())


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'lookup':
        context.user_data['awaiting'] = True
        await query.edit_message_text('أرسل Player ID (أرقام فقط):')
    elif query.data == 'about':
        await query.edit_message_text(
            'Kro GeoIP\nاستعلام عن اللاعب والوصول إلى متجر تركيا الرسمي.\n'
            'لا يتم تغيير مناطق اللاعبين أو تجاوز قيود الدفع.', reply_markup=keyboard())
    elif query.data == 'eligibility':
        await query.edit_message_text(purchase_note(), reply_markup=keyboard())


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting'):
        await update.message.reply_text('ابدأ بالأمر /start ثم اختر GeoIP.')
        return
    player_id = (update.message.text or '').strip()
    if not PLAYER_ID_RE.fullmatch(player_id):
        await update.message.reply_text('Player ID غير صالح. أدخل 5–20 رقماً أو /cancel.')
        return
    now = time.monotonic()
    previous = context.user_data.get('last_lookup_time', -1e12)
    cooldown = lookup_cooldown()
    if now - previous < cooldown:
        await update.message.reply_text('طلب متكرر؛ حاول لاحقاً.')
        return
    context.user_data['last_lookup_time'] = now
    context.user_data.pop('awaiting', None)
    message = await update.message.reply_text('جاري الاستعلام عن اللاعب...')
    try:
        result = await lookup_player(player_id)
    except (ValueError, OSError) as exc:
        LOG.error('Provider configuration error: %s', type(exc).__name__)
        result = {'status': 'CONFIGURATION_ERROR'}
    try:
        await asyncio.to_thread(record, update.effective_user.id, player_id, result)
    except (sqlite3.Error, OSError) as exc:
        LOG.error('Unable to record lookup: %s', type(exc).__name__)
        await message.edit_text('تعذّر حفظ نتيجة الاستعلام. حاول لاحقاً.', reply_markup=keyboard())
        return
    name = result.get('nickname') or 'Not verified'
    region = result.get('player_region') or 'Not verified'
    await message.edit_text(
        f'Kro GeoIP\n\nPlayer ID: {player_id}\n'
        f'اسم اللاعب: {name}\nمنطقة اللاعب: {region}\n'
        'منطقة متجر Midasbuy الخاصة بك: غير متحقق منها\n'
        f'المتجر المطلوب: {TARGET_REGION}\n'
        f'حالة الاستعلام: {result["status"]}\n'
        'تغيير المنطقة: لم يُنفّذ\nالتحقق من أهلية الشراء: غير متاح\n\n'
        'يمكنك فتح صفحة تركيا الرسمية من الزر أدناه. فتحها ليس تغييراً للحساب.',
        reply_markup=keyboard())


def history_entries(user_id):
    with closing(connect()) as conn:
        return conn.execute('''SELECT l.player_id, l.status,
            COALESCE(o.operation_status,'NOT_PERFORMED'),
            COALESCE(o.verification_status,'UNVERIFIED'), l.created_at
            FROM lookups l LEFT JOIN operations o ON o.lookup_id = l.id
            WHERE l.user_id=? ORDER BY l.id DESC LIMIT 10''',
            (user_id,)).fetchall()


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        entries = await asyncio.to_thread(history_entries, update.effective_user.id)
    except (sqlite3.Error, OSError) as exc:
        LOG.error('Unable to read history: %s', type(exc).__name__)
        await update.message.reply_text('تعذّر قراءة السجل. حاول لاحقاً.')
        return
    if not entries:
        await update.message.reply_text('لا يوجد سجل بعد.')
        return
    lines = ['آخر الاستعلامات:']
    for pid, status, operation, verify, created in entries:
        lines.append(f'{pid} | {status} | {operation}/{verify} | {created[:19]}')
    await update.message.reply_text('\n'.join(lines)[:3900])


async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'صفحة متجر PUBG Mobile التركي الرسمية:\n'
        f'{TURKEY_SHOP_URL}\n\n{purchase_note()}',
        reply_markup=keyboard(),
    )


def main():
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(),
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    # Telegram puts the bot token in the request URL. Provider URLs can contain player IDs.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    token = os.getenv('BOT_TOKEN', '').strip()
    if not token:
        raise RuntimeError('BOT_TOKEN is required')
    provider_config()
    request_timeout()
    lookup_cooldown()
    with closing(connect()):
        pass
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('cancel', cancel))
    app.add_handler(CommandHandler('history', history))
    app.add_handler(CommandHandler('shop', shop))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive))
    LOG.info('Starting Kro GeoIP polling worker')
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
