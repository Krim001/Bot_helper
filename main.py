import json
import math
import os
from typing import Optional
import pyglet
import telebot
import api
import db
from dotenv import load_dotenv

load_dotenv()

DB = db.SQL()
API = api.API_YANDEX(api.get_token() if os.environ.get("PRODUCTION") == "true" else os.environ.get("IAM_TOKEN"),
                     os.environ.get("FOLDER_ID"), os.environ.get("GPT"))
bot = telebot.TeleBot(os.environ.get('TOKEN'))

def bot_message(message: telebot.types.Message, text: str, keyboard: Optional[telebot.REPLY_MARKUP_TYPES] = None):
    return bot.send_message(chat_id=message.chat.id, text=text, reply_markup=keyboard)

def bot_edit(message: telebot.types.Message, text: str, keyboard: Optional[telebot.REPLY_MARKUP_TYPES] = None):
    return bot.edit_message_text(chat_id=message.chat.id, message_id=message.message_id, text=text, reply_markup=keyboard)

@bot.message_handler(commands=['start'])
def send_welcome(message: telebot.types.Message) -> None:
    if DB.get_user(message.from_user.id) is not None:
        bot_message(message, "Привет я бот-помощник, я сделаю всё что хочешь только спроси или запиши голосове сообщение, и я тебе помогу!\nТакже ты "
                             "можешь прописать /tts и отправь текст, и /stt и отправь голосовое сообщение")
    else:
        if len(DB.get_count_all_users()) < 2:
            DB.create_user(message.from_user.id)
            bot_message(message,
                        "Привет я бот-помощник, я сделаю всё что хочешь только спроси или запиши голосове сообщение, и я тебе помогу!\nТакже ты "
                        "можешь прописать /tts и отправь текст, и /stt и отправь голосовое сообщение")
        else:
            bot_message(message,"Ошибка, сервер слишком плохой поэтому выдерживает всего двух человек")

@bot.message_handler(commands=['tts'])
def send_tts(message: telebot.types.Message) -> None:
    if DB.get_user(message.from_user.id) is None:
        bot_message(message, "Вы не авторизовались! Пропиши /start для авторизации")
        return
    bot_message(message, "Отправьте текст")
    bot.register_next_step_handler(message, tts_handle)


@bot.message_handler(commands=['stt'])
def send_stt(message: telebot.types.Message) -> None:
    if DB.get_user(message.from_user.id) is None:
        bot_message(message, "Вы не авторизовались! Пропиши /start для авторизации")
        return
    bot_message(message, "Отправьте голосовое сообщение")
    bot.register_next_step_handler(message, stt_handle)


@bot.message_handler(commands=["profile"])
def send_profile(message: telebot.types.Message) -> None:
    if DB.get_user(message.from_user.id) is None:
        bot_message(message, "Вы не авторизовались! Пропиши /start для авторизации")
        return
    user_id, symbols, blocks, tokens, token, messages = DB.get_user(message.from_user.id)
    bot_message(message, f"{message.from_user.first_name},\nСимволов: {symbols}\nБлоков: {blocks}\nТокенов: {tokens}")


@bot.message_handler(content_types=['text'])
def gpt_text(message: telebot.types.Message) -> None:
    if DB.get_user(message.from_user.id) is None:
        bot_message(message, "Вы не авторизовались! Пропиши /start для авторизации")
        return
    messanger = api.Messanger()
    if len(messanger.get_messages()) == 0:
        messanger.add_message("system", "Ты добрый бот помощник, ты отзываешься всем на помощь")
        messanger.add_message("system", "Отвечай кратко, без воды")
    messanger.add_message("user", message.text)
    if API.count_tokens(messanger.get_messages_str()) > DB.get_tokens(message.from_user.id):
        bot_message(message, "Ошибка у вас кончились токены")
        return

    mess = bot_message(message, "ботик думает...")
    answer, data = handle_gpt(message, messanger)
    bot_edit(mess, data)


@bot.message_handler(content_types=['voice'])
def gpt_voice(message: telebot.types.Message) -> None:
    if DB.get_user(message.from_user.id) is None:
        bot_message(message, "Вы не авторизовались! Пропиши /start для авторизации")
        return
    messanger = api.Messanger()
    if len(messanger.get_messages()) == 0:
        messanger.add_message("system", "Ты добрый бот помощник, ты отзываешься всем на помощь")
        messanger.add_message("system", "Отвечай кратко, без воды")

    file_info = bot.get_file(message.voice.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    with open(f"voices/{message.from_user.id}.ogg", "wb") as file:
        file.write(downloaded_file)
    duration = pyglet.media.load(f"voices/{message.from_user.id}.ogg").duration

    if duration > 60:
        bot_message(message, "Ошибка! Вы отправили cообщение больше 1 минуты")
        bot.register_next_step_handler(message, tts_handle)
        return

    if math.ceil(duration / 15) > DB.get_blocks(message.from_user.id):
        bot_message(message, "Ошибка! У вас закончились блоки")
        return

    answer, data = API.speech_to_text(downloaded_file)
    if not answer:
        bot_message(message, data)
        return

    messanger.add_message("user", data)
    if API.count_tokens(messanger.get_messages_str()) > DB.get_tokens(message.from_user.id):
        bot_message(message, "Ошибка у вас кончились токены")
        return

    bot_message(message, "ботик думает...")
    answer, text = handle_gpt(message, messanger)

    if not answer:
        bot_message(message, text)

    answer, data = API.text_to_speech(text)
    if answer:
        bot.send_voice(message.from_user.id, data)
        DB.take_away_symbols(message.from_user.id, len(text))
    else:
        bot_message(message, data)


def handle_gpt(message: telebot.types.Message, messanger: api.Messanger) -> (bool,str):
    if DB.get_user(message.from_user.id) is None:
        bot_message(message, "Вы не авторизовались! Пропиши /start для авторизации")
        return
    answer, data = API.gpt_ask(messanger)
    if not answer:
        return answer, data

    text, tokens = data
    messanger.add_message("assistant", text)
    DB.take_away_tokens(message.from_user.id, int(tokens))
    DB.update_chat(message.from_user.id, messanger.get_messages_str())
    return answer, text


@bot.message_handler(func=lambda message: False)
def stt_handle(message: telebot.types.Message) -> None:
    if message.content_type != "voice":
        bot_message(message, "Ошибка! Вы отправили не голосовое сообщение.")
        bot.register_next_step_handler(message, tts_handle)
        return

    file_info = bot.get_file(message.voice.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    with open(f"voices/{message.from_user.id}.ogg", "wb") as file:
        file.write(downloaded_file)
    duration = pyglet.media.load(f"voices/{message.from_user.id}.ogg").duration

    if duration > 60:
        bot_message(message, "Ошибка! Вы отправили cообщение больше 1 минуты")
        bot.register_next_step_handler(message, tts_handle)
        return

    if math.ceil(duration / 15) > DB.get_blocks(message.from_user.id):
        bot_message(message, "Ошибка! У вас закончились блоки")
        return

    answer, data = API.speech_to_text(downloaded_file)
    bot_message(message, data)
    if answer:
        DB.take_away_blocks(message.from_user.id, math.ceil(duration / 15))



@bot.message_handler(func=lambda message: False)
def tts_handle(message: telebot.types.Message) -> None:
    if message.content_type != "text":
        bot_message(message, "Ошибка! Вы отправили не текст.")
        bot.register_next_step_handler(message, tts_handle)
        return

    if len(message.text) > DB.get_symbols(message.from_user.id):
        bot_message(message, "Ошибка! У вас закончились токены")
        return

    if len(message.text) > 200:
        bot_message(message, "Ошибка! Вы отправили текст больше 200 символов!")
        bot.register_next_step_handler(message, tts_handle)
        return

    answer, data = API.text_to_speech(message.text)

    if answer:
        bot.send_voice(message.from_user.id, data)
        DB.take_away_symbols(message.from_user.id, len(message.text))
    else:
        bot_message(message, data)




bot.polling()
