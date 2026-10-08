# 🎓 TalabaBot — Telegram bot: mavzu va hajm berib, tayyor hujjat olasiz

## Xizmatlar
- 📄 **Referat (DOCX)** — mavzu + sahifalar soni → Word fayl (muqova, boʻlimlar, adabiyotlar, sahifa raqamlari)
- 🎤 **Prezentatsiya (PPTX)** — mavzu + slaydlar soni → turli joylashuvdagi PowerPoint slaydlari, har slaydga bepul mavzuga mos rasm (Commons/Pollinations) va speaker notes
- 📊 **Jadval (XLSX)** — mavzu + qatorlar soni → Excel (formatlangan jadval + avtomatik grafik)
- 📋 **Test savollari (DOCX)** — mavzu + savollar soni → Word (javoblari bilan)
- 🧩 **Quiz** — Word savolbank yuklaysiz, bot interaktiv test oʻtkazadi (vaqt bilan)
- 📁 **Mening ishlarim** — har bir yaratilgan hujjat saqlanadi (30 kun), qayta yuklab olasiz
- 🔧 **Asboblar**: Word↔PDF, PDF birlashtirish, PDF boʻlish, Word→PPTX, ✍️ qayta yozish, 🌐 tarjima
- 👥 **Biz haqimizda** — murojaat adminga boradi, admin Reply bilan javob qaytaradi
- ⭐ **Premium** — `premium_until` bilan cheksiz imkoniyatlar
- 💳 **Qoʻlda toʻlov (Pro paket)** — “Sotib olish” → karta chiqadi → foydalanuvchi pul tashlab chek (skreshot) yuboradi → admin tekshirib “Tasdiqlash” tugmasini bosadi → Pro paket beriladi
- ⏳ **Kunlik limit** — bepul foydalanuvchi uchun kuniga `DAILY_LIMIT` (standart 2) ta AI hujjat; ertaga qaytadi
- ⏱ **Global navbat** — Gemini soʻrovlari `GEMINI_MAX_CONCURRENT` (standart 2) talikda, "Navbatingiz: X" xabari
- 🌐 Tillar: oʻzbekcha / русский / English
- 🔁 Qayta yaratish va PDFga aylantirish tugmalari
- 🔔 **Admin xabarlari** — kim kirdi, kim hujjat soʻradi, limit tugadi, xatoliklar
- 📊 `/stats` — foydalanuvchilar, kunlik faol, hujjatlar, Premium, turlar boʻyicha

## Admin buyruqlari (faqat ADMIN_IDS / ADMIN_SECRET)
- `/stats` — statistika + hujjat turlari boʻyicha
- `/grant <user_id> <kun>` — foydalanuvchiga Premium berish (masalan: `/grant 123456789 30`)
- `/broadcast <matn>` — barcha foydalanuvchilarga xabar tarqatish
- Murojaatga Reply berib javob yozish — foydalanuvchiga yetadi
- `/admin` — ADMIN_IDS roʻyxatiga kirmagan boʻlsa, ADMIN_SECRET kodi bilan admin boʻlish

## Sistemaga qoʻyish
Tavsiya etilgan: Ubuntu server, `systemd` orqali avtomatik ishga tushadi va qulab tushsa qayta ishga turadi.

### 1. Talablar
```bash
sudo apt update
sudo apt install -y python3 python3-venv libreoffice
# Ubuntu 22.04: python3.10 boʻlsa ham ishlaydi; Project talab qilgan minimal: Python 3.10+
```

### 2. Kodni olish
```bash
mkdir -p /opt/talaba_bot
# kod fayllarini shu papkaga joylashtiring (bot.py, services/, i18n.py, ...)
cd /opt/talaba_bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. PostgreSQL (oʻz parolingizni ishlating)
```sql
CREATE ROLE talaba_bot WITH LOGIN PASSWORD 'KuchliParol123';
CREATE DATABASE talaba_bot OWNER talaba_bot;
```
Jadvallar bot 1-ishi boshlanganda avtomatik yaratiladi; eski `users.json`/`admin.json` bir marta migratsiya qilinadi.

### 4. `.env` faylini toʻldiring
Kodga parol/token yozmaymiz — hammasi `.env`da:
```
TELEGRAM_BOT_TOKEN=123456789:...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
ADMIN_IDS=123456789,987654321        # admin Telegram ID lari (vergul bilan)
ADMIN_SECRET=                         # ixtiyoriy bir martalik kod
DATABASE_URL=postgresql://talaba_bot:KuchliParol123@localhost:5432/talaba_bot
DAILY_LIMIT=2                         # bepul rejimda kunlik AI hujjatlar
GEMINI_MAX_CONCURRENT=2               # bir vaqtda Gemini soʻrovlari soni
```
`.env` hech qachon git/qonga tushmasligi kerak (`.gitignore`da).
Slaydlarga ramlar qoʻshimcha kalitsiz — Wikimedia Commons va Pollinations orqali bepul yuklanadi.

### 5. systemd xizmati
`deploy/talaba_bot.service` ni moslang (User, papka yoʻli) va:
```bash
sudo cp deploy/talaba_bot.service /etc/systemd/system/talaba_bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now talaba_bot
sudo systemctl status talaba_bot        # holati
sudo journalctl -u talaba_bot -f        # jonli loglar
```
`deploy/talaba_bot.service` ichida muhim:
- `Restart=always` — qulab tushsa qayta ishga tushiradi
- `WorkingDirectory=/opt/talaba_bot`
- `ExecStart=/opt/talaba_bot/.venv/bin/python /opt/talaba_bot/bot.py`
- `User=talaba` — alohida foydalanuvchi bilan ishlatish tavsiya etiladi

### 6. Lokal ishga tushirish (oddiy)
```bash
./run.sh
```

## Testlar
```bash
.venv/bin/python -m pytest tests/ -q
```
Nima tekshiriladi:
- `tests/test_documents.py` — DOCX/PPTX/XLSX quruvchilar (bulletlar, sliderlar, grafik)
- `tests/test_generator.py` — Gemini prompt formatlash, JSON validatsiya qayta urinishlari
- `tests/test_pdf.py` — PDF birlashtirish/boʻlish/konvertatsiya
- `tests/test_quiz.py` — savolbank parsring
- `tests/test_db.py` — PostgreSQL (mavjud boʻlmasa auto-skip)
- `tests/test_i18n.py` — 3 til kalitlari tengligi, placeholder mosligi

## Loglar
- `logs/bot.log` — barcha hodisalar (avtomatik rotorli, maks. 5 MB x 3) — `config.py` orqali
- systemd sharoidida: `journalctl -u talaba_bot -f`

## Ishlash sxemasi
Foydalanuvchi → til tanlaydi → xizmat turi → mavzu → hajm (sahifa/slayd/qator) →
`quota_ok()` (Pro paket kvotasi yoki kunlik bepul limit) → `call_ai()` (global Gemini navbat) →
Gemini JSON tuzadi → `python-docx`/`python-pptx`/`openpyxl` bilan fayl yigʻiladi →
`consume_quota()` hisoblanadi, fayl `files/`ga saqlanadi va yuboriladi.

## Papkalar
- `bot.py` — aiogram 3, FSM holatlar, tugmalar, `/grant`, `/broadcast`, `/stats`
- `config.py` — `.env` sozlamalari + RotatingFileHandler log (logger `talababot`)
- `db.py` — PostgreSQL (users, admins, files) + avtomatik migratsiya
- `services/fsm_pg.py` — FSM holatlar PostgreSQL (restartda yoʻqolmaydi)
- `i18n.py` — 3 tildagi barcha matnlar
- `services/generator.py` — Gemini kontent yaratish (retry + JSON validatsiya)
- `services/documents.py` — DOCX / PPTX / XLSX quruvchilar
- `services/convert.py` — LibreOffice va pdf2docx (semaphore bilan, noyob profil)
- `services/payments.py` — qoʻlda toʻlov `card_info()`/`is_ready()` (Payme/Click keyin ulanadi)
- `services/quiz.py`, `services/pdf.py` — quiz savolbanki, PDF amallar
- `files/<user_id>/` — yaratilgan fayllar, 30 kundan keyin avtomatik oʻchadi
- `deploy/talaba_bot.service` — systemd misol

## Pro toʻlovni sozlash (qoʻlda, karta orqali)
1. `.env`ga kartangizni yozing (hech qachon git/github ga tushmaydi):
   ```
   PAYMENT_CARD=0000000000000000
   PAYMENT_HOLDER=Davlatov Asilbek
   PAYMENT_AMOUNT=50000
   PREMIUM_DAYS=30
   PRO_WORD=5
   PRO_SLIDE=3
   ```
   `PAYMENT_CARD` boʻsh boʻlsa bot “Toʻlov hali sozlanmagan” deydi (xatoliksiz).
2. Botni qayta ishga tushiring. ⭐ Premium → “Sotib olish” → karta raqami + summa chiqadi.
3. Foydalanuvchi pul oʻtkazib **chek (skreshot)** yuboradi → bot uni barcha adminlarga
   “✅ Tasdiqlash / ❌ Rad etish” tugmalari bilan joʻnatadi.
4. Admin **✅ Tasdiqlash**ni bossa → `grant_package()` → Pro paket (PREMIUM_DAYS kun) beriladi,
   foydalanuvchiga xabar va adminga bildirish boradi.
5. ⏳ Paket tugash bildirishnomasi avtomatik: tugashidan 24 soat qolganda bir marta va tugagach — `premium_notifier()` orqali.

## Pro paket nima beradi
- 📄 `PRO_WORD` (5) ta Word hujjat (referat, test, jadval, qayta yozish, tarjima)
- 🎤 `PRO_SLIDE` (3) ta slayd (prezentatsiya, Word→PPT)
- 📊 **Quiz tahlili** — test tugagach har bir savol boʻyicha batafsil tahlil (Pro uchun)
- Paket tugasa yoki davri o'tsa — bepul rejimga qaytadi (kunlik `DAILY_LIMIT`)

## Kelajak (roadmap)
- Payme / Click toʻlovni `services/payments.py` orqali qoʻshish
