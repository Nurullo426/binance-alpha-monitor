#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Alpha monitor v1.4 — быстрый детектор "Успейте принять участие"
- Только уведомления (НЕ участвует автоматически)
- Формат уведомления: стиль, который ты указал
"""

import requests
import time
import random
import json
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup
from threading import Thread
from flask import Flask

# --- НАСТРОЙКИ БОТА ---
TELEGRAM_BOT_TOKEN = '7810292989:AAFKDPamp7LcDFaimnDHauP7g5SuQFReKLQ'
TELEGRAM_CHAT_IDS = [
    '2002273774',  # Ваш ID
    '334157830'  # ID друга @RAskarovic
]

CHECK_URL = 'https://www.binance.com/ru/feed/alpha'
CHECK_INTERVAL = 5  # Интервал проверки в секундах (быстро)
DETECTED_FILE = 'detected_airdrops.json'
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117 Safari/537.36",
]

KEY_PHRASES = [
    r"Успейте\s*принять\s*участие", r"Сумма\s*получения", r"Требуемые\s*баллы",
    r"Этапы\s*аирдропа", r"\b15\s*балл", r"\b190\s*балл", r"Завершен",
    r"Завершено"
]


# ---------- load / save detected ----------
def load_detected():
    if os.path.exists(DETECTED_FILE):
        try:
            with open(DETECTED_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_detected(s):
    try:
        with open(DETECTED_FILE, "w", encoding="utf-8") as f:
            json.dump(list(s), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения detected: {e}")


# --------- Telegram sender ----------
def send_telegram_message(message, url_button=None):
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        if url_button:
            payload["reply_markup"] = json.dumps(
                {
                    "inline_keyboard": [[{
                        "text": "Открыть Binance Alpha",
                        "url": url_button
                    }]]
                },
                ensure_ascii=False)
        try:
            r = requests.post(api, data=payload, timeout=8)
            if r.status_code == 200:
                print(f"✅ Уведомление отправлено {chat_id}")
            else:
                print(f"⚠️ Telegram {r.status_code} -> {r.text[:200]}")
        except Exception as e:
            print(f"❌ Ошибка отправки в {chat_id}: {e}")


# --------- Парсер/детектор ----------
def find_signals(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    text_all = soup.get_text(" ", strip=True)
    found_any = False
    for p in KEY_PHRASES:
        if re.search(p, text_all, flags=re.IGNORECASE):
            found_any = True
            break
    if not found_any:
        return []

    results = []
    candidate_nodes = []
    for tag in soup.find_all(text=True):
        t = tag.strip()
        if not t:
            continue
        for p in KEY_PHRASES:
            if re.search(p, t, flags=re.IGNORECASE):
                candidate_nodes.append(tag.parent)
                break

    uniq_blocks = []
    seen_hash = set()
    for node in candidate_nodes:
        container = node
        for _ in range(4):
            if container is None:
                break
            txt = container.get_text(" ", strip=True)
            # filter out very short/irrelevant
            if len(txt) < 20:
                container = container.parent
                continue
            h = hash(txt[:400])
            if h not in seen_hash:
                seen_hash.add(h)
                uniq_blocks.append(txt)
                break
            container = container.parent

    for block in uniq_blocks:
        b = re.sub(r"\s+", " ", block)
        m_proj = re.split(r"(Сумма\s*получения|Требуемые\s*балл)",
                          b,
                          flags=re.IGNORECASE)
        project = (m_proj[0].strip()[:120]
                   ) if m_proj and len(m_proj) > 0 else "Unknown Project"

        m_req = re.search(r"Требуемые\s*балл\w*\s*[:\s]*([0-9]{1,5})",
                          b,
                          flags=re.IGNORECASE)
        if not m_req:
            m_req = re.search(r"([0-9]{1,5})\s*балл", b, flags=re.IGNORECASE)
        required = m_req.group(1) if m_req else "—"

        m_reward = re.search(r"Сумма\s*получения\s*[:\s]*([^\n\r•—]{1,80})",
                             b,
                             flags=re.IGNORECASE)
        reward = m_reward.group(1).strip() if m_reward else "—"

        urgent = True if re.search(
            r"Успейте\s*принять\s*участие", b, flags=re.IGNORECASE) else False
        has_15 = True if re.search(r"\b15\s*балл", b,
                                   flags=re.IGNORECASE) else False
        has_190 = True if re.search(r"\b190\s*балл", b,
                                    flags=re.IGNORECASE) else False
        status = "Завершено" if re.search(r"Завершен", b,
                                          flags=re.IGNORECASE) else "Активен"

        airdrop_id = f"{project}_{required}_{reward}".replace(" ", "_")[:180]

        results.append({
            "id": airdrop_id,
            "project": project,
            "required_points": required,
            "reward": reward,
            "urgent": urgent,
            "has_15": has_15,
            "has_190": has_190,
            "status": status,
            "excerpt": b[:400]
        })

    return results


# --------- Проверка страницы ----------
def check_page():
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "ru-RU,ru;q=0.8"
    }
    try:
        r = requests.get(CHECK_URL, headers=headers, timeout=12)
        r.raise_for_status()
        html = r.text
        signals = find_signals(html)
        important = []
        for s in signals:
            if s["status"] == "Активен" and (s["urgent"] or s["has_15"]
                                             or s["has_190"]):
                important.append(s)
        return important
    except Exception as e:
        print(f"❌ Ошибка fetch/parsing: {e}")
        return []


# ---------------- Main ----------------
def main():
    print("🟢 Binance Alpha monitor v1.4 — быстрый режим (каждые ~5s)")
    detected = load_detected()
    print(f"📦 Загрузили {len(detected)} ранее обнаруженных airdrop'ов")
    
    # Отправка тестового сообщения при запуске
    startup_msg = (
        "🤖 <b>Бот запущен и работает!</b>\n"
        "Мониторинг Binance Alpha активирован.\n"
        f"Получатели: {len(TELEGRAM_CHAT_IDS)} пользователя"
    )
    send_telegram_message(startup_msg)
    print("✅ Тестовое сообщение отправлено")

    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n🔍 Проверяем страницу... {now}")
            found = check_page()
            if not found:
                print(
                    f"✅ Новых сигналов нет ({datetime.now().strftime('%H:%M:%S')})"
                )
            else:
                for a in found:
                    if a["id"] in detected:
                        print(
                            f"— уже оповещали: {a['project']} ({a['required_points']})"
                        )
                        continue

                    # Форматированное сообщение в твоём стиле
                    spend_text = "15 баллов" if a["has_15"] else (
                        "190 баллов" if a["has_190"] else "—")
                    msg = (
                        "🚨 <b>ВЫШЕЛ НОВЫЙ AIRDROP!</b> 🚨\n\n"
                        f"💰 <b>Монета:</b> {a['project']}\n"
                        f"🎯 <b>Условия для участия:</b> {a['required_points']} баллов\n"
                        f"💸 <b>Трата баллов:</b> {spend_text}\n"
                        f"⏰ <b>Обнаружен:</b> {datetime.now().strftime('%H:%M:%S')}\n\n"
                        "⚡ Скорее беги забирать!\n"
                        f"🔗 {CHECK_URL}\n\n"
                        "#Airdrop #BinanceAlpha")

                    send_telegram_message(msg, url_button=CHECK_URL)
                    detected.add(a["id"])
                    save_detected(detected)
                    time.sleep(0.6)

            time.sleep(max(1, CHECK_INTERVAL + random.uniform(-1, 1)))
        except KeyboardInterrupt:
            print("🛑 Остановлено пользователем")
            break
        except Exception as e:
            print(f"❌ Ошибка основного цикла: {e}")
            time.sleep(5)


# --- Flask сервер для UptimeRobot ---
app = Flask(__name__)

@app.route("/")
def home():
    return "Binance Alpha Monitor is alive ✅"

def run_server():
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)

# --- Запуск ---
if __name__ == "__main__":
    # Запуск Flask в отдельном потоке
    server_thread = Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    
    # Запуск основного бота
    main()
