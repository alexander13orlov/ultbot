import json
import logging
from datetime import datetime, date
from pathlib import Path
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import requests

# Token and settings
TOKEN = "1923438912:AAGlLG82e4IakOxot4a15MksOZS0A6lLr7E"
SETTINGS_FILE = Path(__file__).parent / "bot_settings.json"

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load/save settings with robust UTF-8 handling
def load_settings():
    if SETTINGS_FILE.exists():
        try:
            text = SETTINGS_FILE.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            # fallback: replace undecodable bytes
            raw = SETTINGS_FILE.read_bytes()
            text = raw.decode('utf-8', errors='replace')
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}; starting with empty settings.")
            return {"chats": {}}
    else:
        return {"chats": {}}

def save_settings(data):
    # ensure UTF-8 encoding
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding='utf-8')

settings = load_settings()

# Constants
WEEKDAYS = {"пн":0,"вт":1,"ср":2,"чт":3,"пт":4,"сб":5,"вс":6}
ICON_MAP = {
    '01d': '☀️', '01n': '🌙', '02d': '⛅️', '02n': '⛅️',
    '03d': '☁️', '03n': '☁️', '04d': '☁️', '04n': '☁️',
    '09d': '🌧️', '09n': '🌧️', '10d': '🌦️', '10n': '🌦️',
    '11d': '⛈️', '11n': '⛈️', '13d': '❄️', '13n': '❄️',
    '50d': '🌫️', '50n': '🌫️'
}
WEATHERAPI_CODE_MAP = {
    1000: '☀️', 1003: '⛅️', 1006: '☁️', 1009: '☁️', 1030: '🌫️',
    1063: '🌦️', 1066: '❄️', 1069: '❄️', 1072: '🌫️', 1087: '⛈️',
    1114: '❄️', 1117: '❄️', 1135: '🌫️', 1147: '🌫️', 1150: '🌦️',
    1153: '🌦️', 1168: '🌦️', 1171: '⛈️', 1180: '🌧️', 1183: '🌧️',
    1186: '🌧️', 1189: '🌧️', 1192: '🌧️', 1195: '🌧️', 1198: '🌧️',
    1201: '🌧️', 1204: '🌨️', 1207: '🌨️', 1210: '🌨️', 1213: '🌨️',
    1216: '🌨️', 1219: '🌨️', 1222: '❄️', 1225: '❄️', 1237: '🌨️',
    1240: '🌦️', 1243: '🌧️', 1246: '🌧️', 1249: '🌨️', 1252: '🌨️',
    1255: '🌨️', 1258: '🌨️', 1261: '🌨️', 1264: '🌨️', 1273: '⛈️',
    1276: '⛈️', 1279: '🌨️', 1282: '🌨️'
}
OWM_API = 'aca7e62658558133eae4c3f77f5d20ff'
WEATHERAPI_KEY = '50584232288b426091292309251405'
LAT, LON = 55.759931, 37.643032

# Pending states
pending_create = {}
pending_schedule = {}
pending_link = {}
pending_delete = {}

# Prompt template
CREATE_PROMPT = (
    "Задайте шаблон опроса по формату:\n"
    "ID;\nВопрос;\nВариант1;\nВариант2;\n...;"
)

# Helper for scoped settings
def get_scope(chat_id, topic_id):
    chats = settings.setdefault('chats', {})
    chat = chats.setdefault(str(chat_id), {})
    topics = chat.setdefault('topics', {})
    return topics.setdefault(str(topic_id or 'root'), {})

# === Command Handlers ===

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    text = (
        "/createpoll — создать шаблон опроса;\n"
        "/viewpoll_<id> — показать шаблон опроса;\n"
        "/showpolls — список опросов;\n"
        "/<id> — запустить опрос или вывести ссылку;\n"
        "/setschedule — задать расписание;\n"
        "/schedule — показать расписание;\n"
        "/showlinks — список ссылок;\n"
        "/autopoll — вкл/выкл автоопросы;\n"
        "/antidoublepoll — вкл/выкл антидублирование;\n"
        "/setlink — задать ссылку;\n"
        "/opros — был ли сегодня опрос;\n"
        "/forecast [owm|wa] — почасовой прогноз;\n"
        "/del — удалить идентификатор;\n"
        "/getsettings — получить файл настроек"
    )
    await context.bot.send_message(chat_id=chat_id, text=text, message_thread_id=thread_id)

async def createpoll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    pending_create[(chat_id, thread_id)] = True
    await context.bot.send_message(chat_id=chat_id, text=CREATE_PROMPT, message_thread_id=thread_id)

async def viewpoll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    parts = update.message.text.split('_', 1)
    if len(parts) != 2 or not parts[1].isalnum():
        return await context.bot.send_message(chat_id, 'Используйте /viewpoll_<ID>', message_thread_id=thread_id)
    tpl = get_scope(chat_id, thread_id).get('templates', {}).get(parts[1])
    if not tpl:
        return await context.bot.send_message(chat_id, 'Опрос не найден', message_thread_id=thread_id)
    lines = [f"{parts[1]};", f"{tpl['question']};"] + [f"{opt};" for opt in tpl['options']]
    await context.bot.send_message(chat_id, '\n'.join(lines), message_thread_id=thread_id)

async def showpolls_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    keys = list(get_scope(chat_id, thread_id).get('templates', {}).keys())
    await context.bot.send_message(chat_id, ', '.join(keys) if keys else 'Опросов нет', message_thread_id=thread_id)

async def entity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    ident = update.message.text.lstrip('/')
    scope = get_scope(chat_id, thread_id)
    if ident in scope.get('templates', {}):
        tpl = scope['templates'][ident]
        await context.bot.send_poll(
            chat_id=chat_id,
            question=tpl['question'],
            options=tpl['options'],
            is_anonymous=False,
            allows_multiple_answers=False,
            message_thread_id=thread_id
        )
        scope['last_poll'] = date.today().isoformat()
        save_settings(settings)
    else:
        await context.bot.send_message(
            chat_id,
            scope.get('links', {}).get(ident, 'ID не найден'),
            message_thread_id=thread_id
        )

async def setschedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    pending_schedule[(chat_id, thread_id)] = True
    await context.bot.send_message(chat_id=chat_id, text='Шаблон: ID;день;HH:MM;', message_thread_id=thread_id)

async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    entries = get_scope(chat_id, thread_id).get('schedule', [])
    seen, uniq = set(), []
    for e in entries:
        k = (e['name'], e['day'], e['time'])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    msg = '\n'.join(f"{e['name']};{e['day']};{e['time']};" for e in uniq) or 'Расписание не задано'
    await context.bot.send_message(chat_id, msg, message_thread_id=thread_id)

async def showlinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    keys = list(get_scope(chat_id, thread_id).get('links', {}).keys())
    await context.bot.send_message(chat_id, ', '.join(keys) if keys else 'Ссылок нет', message_thread_id=thread_id)

async def autopoll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    scope = get_scope(chat_id, thread_id)
    scope['autopoll'] = not scope.get('autopoll', False)
    save_settings(settings)
    state = 'вкл' if scope['autopoll'] else 'выкл'
    await context.bot.send_message(chat_id, f"Автоопросы {state}", message_thread_id=thread_id)

async def antidoublepoll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    scope = get_scope(chat_id, thread_id)
    scope['antidouble'] = not scope.get('antidouble', False)
    save_settings(settings)
    state = 'вкл' if scope['antidouble'] else 'выкл'
    await context.bot.send_message(chat_id, f"Антидублирование {state}", message_thread_id=thread_id)

async def setlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    pending_link[(chat_id, thread_id)] = True
    await context.bot.send_message(chat_id=chat_id, text='Отправьте: ID URL', message_thread_id=thread_id)

async def opros_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    last = get_scope(chat_id, thread_id).get('last_poll')
    msg = 'Опрос был сегодня' if last == date.today().isoformat() else 'Опрос не был сегодня'
    await context.bot.send_message(chat_id, msg, message_thread_id=thread_id)

async def del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    pending_delete[(chat_id, thread_id)] = True
    await context.bot.send_message(chat_id=chat_id, text='Введите ID для удаления', message_thread_id=thread_id)

async def getsettings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    if SETTINGS_FILE.exists():
        await context.bot.send_document(chat_id=chat_id, document=str(SETTINGS_FILE), message_thread_id=thread_id)
    else:
        await context.bot.send_message(chat_id, 'Файл не найден', message_thread_id=thread_id)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    text = update.message.text or ""
    key = (chat_id, thread_id)
    scope = get_scope(chat_id, thread_id)

    # Удаление
    if pending_delete.pop(key, False):
        if text in scope.get('templates', {}):
            del scope['templates'][text]
        elif text in scope.get('links', {}):
            del scope['links'][text]
        else:
            return await context.bot.send_message(chat_id, 'ID не найден', message_thread_id=thread_id)
        save_settings(settings)
        return await context.bot.send_message(chat_id, 'Удалено ✅', message_thread_id=thread_id)

    # Создание опроса
    if pending_create.pop(key, False):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 4 or not all(ln.endswith(';') for ln in lines):
            return await context.bot.send_message(chat_id, 'Неверный формат, строки должны заканчиваться ";"', message_thread_id=thread_id)
        ident = lines[0][:-1]
        if not ident.isalnum():
            return await context.bot.send_message(chat_id, 'ID — только латиница и цифры', message_thread_id=thread_id)
        question = lines[1][:-1]
        options = list(dict.fromkeys(ln[:-1] for ln in lines[2:]))
        scope.setdefault('templates', {})[ident] = {'question': question, 'options': options}
        save_settings(settings)
        return await context.bot.send_message(chat_id, 'Опрос сохранён ✅', message_thread_id=thread_id)

    # Установка ссылки
    if pending_link.pop(key, False):
        parts = text.split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isalnum():
            return await context.bot.send_message(chat_id, 'Неверный формат, используйте: ID URL', message_thread_id=thread_id)
        lid, url = parts
        scope.setdefault('links', {})[lid] = url
        save_settings(settings)
        return await context.bot.send_message(chat_id, 'Ссылка сохранена ✅', message_thread_id=thread_id)

    # Установка расписания
    if pending_schedule.pop(key, False):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        entries, seen = [], set()
        for ln in lines:
            parts = [x.strip() for x in ln.split(';') if x.strip()]
            if len(parts) != 3 or parts[1] not in WEEKDAYS:
                continue
            k = tuple(parts)
            if k in seen:
                continue
            seen.add(k)
            entries.append({'name': parts[0], 'day': parts[1], 'time': parts[2]})
        scope['schedule'] = entries
        save_settings(settings)
        return await context.bot.send_message(chat_id, 'Расписание сохранено ✅', message_thread_id=thread_id)

async def fetch_owm():
    curr_url = f"https://api.openweathermap.org/data/2.5/weather?lat={LAT}&lon={LON}&appid={OWM_API}&units=metric&lang=ru"
    curr = requests.get(curr_url).json()
    print(curr_url)
    f_url = f"https://api.openweathermap.org/data/2.5/forecast?lat={LAT}&lon={LON}&appid={OWM_API}&units=metric&lang=ru"
    fdata = requests.get(f_url).json().get('list', [])
    lines = []
    w = curr['weather'][0]
    ic = ICON_MAP.get(w['icon'], w['icon'])
    lines.append(f"сейчас {w['description']} {ic} {curr['main']['feels_like']}°C давление {int(curr['main']['pressure']*0.750062)}мм.рт.ст. ветер {curr['wind']['speed']}м/с влажность {curr['main']['humidity']}%")
    cnt = 0
    now_ts = curr['dt']
    for entry in fdata:
        if cnt >= 3:
            break
        if entry['dt'] <= now_ts:
            continue
        t = entry['dt_txt'].split(' ')[1][:5]
        w = entry['weather'][0]
        ic = ICON_MAP.get(w['icon'], w['icon'])
        lines.append(f"{t} {w['description']} {ic} {entry['main']['feels_like']}°C осадков {int(entry.get('pop',0)*100)}% ветер {entry['wind']['speed']}м/с влажность {entry['main']['humidity']}%")
        cnt += 1
    return "\n".join(lines)

async def fetch_wa():
    url = f"http://api.weatherapi.com/v1/forecast.json?key={WEATHERAPI_KEY}&q={LAT},{LON}&hours=10&lang=ru"
    data = requests.get(url).json()
    lines = []
    c = data['current']
    cond = c['condition']
    icon = WEATHERAPI_CODE_MAP.get(cond['code'], '')
    lines.append(f"сейчас {cond['text']} {icon} {c['feelslike_c']}°C кол.осадков {c.get('precip_mm',0)}мм ветер {c['wind_kph']}км/ч влажность {c['humidity']}%")
    now_h = datetime.now().hour
    cnt = 0
    for h in data['forecast']['forecastday'][0]['hour']:
        hr = int(h['time'].split(' ')[1][:2])
        if hr <= now_h:
            continue
        icon_h = WEATHERAPI_CODE_MAP.get(h['condition']['code'], '')
        lines.append(f"{hr:02d}:00 {h['condition']['text']} {icon_h} {h['feelslike_c']}°C вер.дождя {h.get('chance_of_rain',0)}% ветер {h['wind_kph']}км/ч влажность {h['humidity']}%")
        cnt += 1
        if cnt >= 9:
            break
    return "\n".join(lines)

async def forecast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id
    args = [a.lower() for a in context.args]
    if 'owm' in args:
        try:
            msg = await fetch_owm()
            return await context.bot.send_message(chat_id, msg, message_thread_id=thread_id)
        except Exception as e:
            logger.warning(f"OWM err: {e}")
            return await context.bot.send_message(chat_id, 'Ошибка с OWM', message_thread_id=thread_id)
    if 'wa' in args:
        try:
            msg = await fetch_wa()
            return await context.bot.send_message(chat_id, msg, message_thread_id=thread_id)
        except Exception as e:
            logger.warning(f"WA err: {e}")
            return await context.bot.send_message(chat_id, 'Ошибка с WeatherAPI', message_thread_id=thread_id)
    for fn in (fetch_owm, fetch_wa):
        try:
            msg = await fn()
            return await context.bot.send_message(chat_id, msg, message_thread_id=thread_id)
        except Exception as e:
            logger.warning(f"fallback err: {e}")
    await context.bot.send_message(chat_id, 'Прогноз недоступен', message_thread_id=thread_id)


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    now_d = date.today().isoformat()
    now = datetime.now()
    for cid, chat_data in settings.get('chats', {}).items():
        for tid, scope in chat_data.get('topics', {}).items():
            if not scope.get('autopoll'):
                continue
            if scope.get('antidouble') and scope.get('last_poll') == now_d:
                continue
            for e in scope.get('schedule', []):
                if WEEKDAYS.get(e['day']) == now.weekday() and e['time'] == now.strftime('%H:%M'):
                    tpl = scope.get('templates', {}).get(e['name'])
                    if tpl:
                        await context.bot.send_poll(
                            chat_id=int(cid),
                            question=tpl['question'],
                            options=tpl['options'],
                            is_anonymous=False,
                            allows_multiple_answers=False,
                            message_thread_id=int(tid)
                        )
                        scope['last_poll'] = now_d
                        save_settings(settings)

# Entry point
if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()

    # Explicit command handlers
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('createpoll', createpoll_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/viewpoll_[A-Za-z0-9]+$'), viewpoll_command))
    app.add_handler(CommandHandler('showpolls', showpolls_command))
    app.add_handler(CommandHandler('setschedule', setschedule_command))
    app.add_handler(CommandHandler('schedule', schedule_command))
    app.add_handler(CommandHandler('showlinks', showlinks_command))
    app.add_handler(CommandHandler('autopoll', autopoll_command))
    app.add_handler(CommandHandler('antidoublepoll', antidoublepoll_command))
    app.add_handler(CommandHandler('setlink', setlink_command))
    app.add_handler(CommandHandler('opros', opros_command))
    app.add_handler(CommandHandler('getsettings', getsettings_command))
    app.add_handler(CommandHandler('forecast', forecast_command))
    app.add_handler(CommandHandler('del', del_command))

    # Dynamic /<id> handler for polls and links
    app.add_handler(
        MessageHandler(
            filters.Regex(r'^/[A-Za-z0-9]+$'),
            entity_command
        )
    )

    # Plain text handler for pending inputs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Daily job for auto polls
    app.job_queue.run_repeating(daily_job, interval=60, first=10)
    app.run_polling()
