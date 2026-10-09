# TalabaBot

Telegram orqali referat, prezentatsiya, jadval va test tayyorlash, hujjatlarni aylantirish hamda vaqtli quiz o'tkazish uchun aiogram 3 boti. Interfeys: o'zbek, rus va ingliz tillari.

## Imkoniyatlar va chegaralar

- Referat → DOCX; prezentatsiya → PPTX; jadval → XLSX; test savollari → DOCX.
- Word ↔ PDF, rasmlar → PDF, 2–10 ta PDF birlashtirish, PDFni 20 tagacha bo'lakka ajratish.
- Word → PPTX, matnni qayta yozish va tarjima qilish. AI javobining tuzilishi, hajmi va paragraf soni tekshiriladi; yaroqsiz javob foydalanuvchiga tayyor ish sifatida berilmaydi.
- Word savolbankidan 100 tagacha savolli quiz. Javob muddati va sessiya tekshiriladi; restartdan keyin saqlangan quiz taymeri tiklanadi.
- “Mening ishlarim”: foydalanuvchiga tegishli fayllar, standart 30 kunlik saqlash. Tozalash har soatda ishlaydi.
- Pro: `PRO_WORD` ta matn/jadval/tarjima ishi, `PRO_SLIDE` ta **prezentatsiya**, `PRO_QUIZ` ta test yaratish yoki interaktiv quiz. **Kreditlar muddatsiz**, miqdori cheklangan; qayta xarid qoldiqqa qo'shiladi. Kredit tugaganda kunlik bepul limitdan foydalanish mumkin.
- Qo'lda to'lov: karta → chek → admin qarori. Bitta chekni takror tasdiqlash qayta kredit bermaydi. Bank to'lovi avtomatik tekshirilmaydi.

Referat sahifalari matn hajmi orqali taxmin qilinadi; Word shriftlari va joylashuviga qarab yakuniy sahifa soni farq qilishi mumkin. AI matnidagi fakt va manbalarni foydalanuvchi tekshirishi kerak. PDF → Word aniq maketni kafolatlamaydi, skanerlangan sahifalar uchun OCR yo'q. Slayd rasmlari tashqi servislar mavjudligiga bog'liq; rasm olinmasa matnli prezentatsiya yuboriladi va bu haqida xabar beriladi.

Standart cheklovlar: yuklanadigan fayl 20 MB, yuboriladigan natija 49 MB, PDF 300 sahifa, matn 120 000 belgi, rasm 12 megapiksel. Har bir albomda ko'pi bilan 10 rasm.

## Lokal o'rnatish

Sinov muhiti: Python 3.11, PostgreSQL 16, LibreOffice (`soffice` PATH ichida). Serverda Python 3.11 yoki yangiroq versiya talab qilinadi; CI Python 3.11 bilan ishlaydi.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

`.env` ichida `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `DATABASE_URL` va `ADMIN_IDS` ni to'ldiring. Alohida PostgreSQL bazasi va unga egalik qiladigan foydalanuvchi yarating. Jadval va qo'shimcha ustunlar startup paytida avtomatik yaratiladi; eski `users.json`/`admin.json` migratsiyasi bir marta bajariladi.

```bash
./run.sh
```

Paket versiyalari `requirements.txt` va tranzitiv bog'liqliklar `requirements.lock` orqali mahkamlangan. Ularni yangilaganda testlarni qayta ishlating.

## Muhim sozlamalar

| O'zgaruvchi | Standart | Vazifasi |
| --- | --- | --- |
| `DAILY_LIMIT` | `3` | Kunlik bepul AI ishlar |
| `APP_TIMEZONE` | `Asia/Tashkent` | Kunlik limit yangilanadigan vaqt zonasi |
| `GEMINI_MAX_CONCURRENT` | `2` | Parallel AI vazifalar |
| `GEMINI_QUEUE_LIMIT` | `20` | Kutayotgan AI vazifalar chegarasi |
| `GEMINI_TIMEOUT` | `60` | Har bir AI tarmoq so'rovi timeouti, sekund |
| `CONVERT_MAX_CONCURRENT` | `2` | Parallel konvertatsiyalar |
| `FILE_RETENTION_DAYS` | `30` | Fayllarni saqlash muddati |
| `MAX_PDF_PAGES` | `300` | PDF sahifalari chegarasi |
| `MAX_TEXT_CHARS` | `120000` | Word matni chegarasi |
| `PREMIUM_DAYS` | `30` | Eski sozlamaga moslik; yangi paket muddatsiz |
| `PRO_WORD` / `PRO_SLIDE` / `PRO_QUIZ` | `5` / `5` / `5` | Word-jadval / prezentatsiya / test-quiz kvotalari |
| `PAYMENT_AMOUNT` | `15000` | Foydalanuvchiga ko'rsatiladigan summa |
| `FILES_DIR` / `LOG_DIR` | `files/` / `logs/` | Absolyut yo'l berish mumkin |

To'lovni yoqish uchun `PAYMENT_CARD`, `PAYMENT_HOLDER` va kamida bitta admin kerak. `POLLINATIONS_TOKEN` ixtiyoriy rasm provayderi tokeni. Maxfiy qiymatlar `.env` ichida saqlanadi va gitga kiritilmaydi.

## Ishonchlilik

Kvota AI ishidan oldin PostgreSQL tranzaksiyasida band qilinadi. Xatolikda qaytariladi; muvaffaqiyatli fayl va hisob bir tranzaksiyada qayd etiladi. Telegramga yuborish muvaffaqiyatsiz bo'lsa, tayyor fayl “Mening ishlarim”da qoladi. Jarayon kutilmaganda to'xtasa, startup tugallanmagan ishlarni bekor qilib, Pro kreditini yoki o'sha kundagi bepul kvotani qaytaradi va foydalanuvchiga xabar yuborishga urinadi. Uzilgan AI ishi avtomatik qayta yaratilmaydi.

Quiz boshlanishida quiz kvotasi band qilinadi, yakunda bir marta hisoblanadi. Menyu orqali bekor qilingan quiz kvotasi qaytariladi. Restart faol quizni va band qilingan kvotasini saqlaydi.

Hujjat tili mavzu yoki Word matni asosida avtomatik aniqlanadi. Startup tarmoq xatosida Telegram ulanishini kutib qayta urinadi; noto'g'ri token kabi doimiy xatolar yashirilmaydi.

Har foydalanuvchining yangilanishlari ketma-ket bajariladi. Bot **bitta polling jarayoni** uchun mo'ljallangan: PostgreSQL advisory lock ikkinchi nusxani ishga tushirmaydi. AI navbati cheklangan, og'ir hujjat ishlari event loop tashqarisida, PDF fallback esa timeoutli alohida jarayonda bajariladi. Fayllar foydalanuvchi papkasida UUID nom bilan saqlanadi.

## Admin

- `/stats` — foydalanuvchilar va hujjatlar statistikasi.
- `/grant <user_id>` — muddatsiz Pro kvotalarini berish; eski uchinchi kun argumenti qabul qilinadi, muddat o'rnatmaydi.
- `/payments` — dastlabki 20 ta kutilayotgan chekni chiqarish; ular ko'rib chiqilgach keyingilari chiqadi.
- `/broadcast <matn>` — barcha foydalanuvchilarga xabar yuborish.
- Murojaatga “Javob yozish” tugmasi yoki Reply orqali javob berish; `/reply <user_id> <matn>` va `/javob` ham mavjud. Tugma rejimini `/cancel` bekor qiladi.
- `/admin <kod>` — ixtiyoriy `ADMIN_SECRET` bilan bir martalik admin qo'shish. Bir foydalanuvchi uchun 15 daqiqada 5 urinish. Yangi admin qo'shish uchun kodni almashtirish yoki `ADMIN_IDS`dan foydalanish kerak.

## Server: systemd

Ubuntu serverida PostgreSQL, Python virtualenv vositalari va LibreOffice Writer/Impress/Calc o'rnatilgan bo'lishi kerak. Kod `/opt/talaba_bot`, virtualenv `/opt/talaba_bot/.venv` ichida bo'ladi.

```bash
sudo useradd --system --user-group --home-dir /var/lib/talaba_bot --shell /usr/sbin/nologin talaba
sudo chown root:talaba /opt/talaba_bot/.env
sudo chmod 640 /opt/talaba_bot/.env
sudo cp deploy/talaba_bot.service /etc/systemd/system/talaba_bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now talaba_bot
sudo systemctl status talaba_bot
sudo journalctl -u talaba_bot -f
```

Mavjud `talaba` foydalanuvchisi bo'lsa `useradd`ni takrorlamang. Kod va `.venv` unga o'qiladigan bo'lishi kerak. Unit faylida fayllar `/var/lib/talaba_bot`, loglar `/var/log/talaba_bot` ichida; systemd ularni tegishli egasi va ruxsatlari bilan yaratadi. Unit boshqa kataloglarga yozishni cheklaydi. Maxsus yo'llar tanlansa unitning `ReadWritePaths` va katalog sozlamalarini ham moslang.

### Eski versiyadan o'tish

Avval botni to'xtating va baza/fayllar backupini oling. Eski `/opt/talaba_bot/files` katalogini va bazadagi yo'llarni saqlang, `talaba` foydalanuvchisiga ularni o'qish huquqini bering. Eski arxivdagi faqat bitta foydalanuvchiga tegishli fayl yuklab olish vaqtida yangi saqlash katalogiga ko'chiriladi. Bir xil yo'li bir necha foydalanuvchiga yozilgan eski fayllar maxfiylik uchun berilmaydi; oldin ustiga yozilgan faylni bu yangilanish tiklay olmaydi. `/tmp`dagi avval yo'qolgan fayllar ham qayta yaratilmaydi.

Yangilanishdan oldingi to'lov tugmalari chek ID saqlamagani uchun ishlamaydi: hali tasdiqlanmagan chekni qayta yuborish kerak. Eski “Qayta yaratish/PDF” tugmalari yangi ish yaratilganda yangilanadi. Eski kodga qaytish uchun yangilanishdan oldingi baza va fayl nusxasidan foydalaning.

### Backup va tiklash

`deploy/backup.py` uchun server versiyasiga mos `pg_dump` o'rnating. Baza va fayllarning bir vaqtdagi nusxasi uchun botni to'xtatib ishlating; bu script servisni o'zi to'xtatmaydi:

```bash
sudo systemctl stop talaba_bot
sudo /opt/talaba_bot/.venv/bin/python deploy/backup.py --output /var/backups/talaba_bot
sudo systemctl start talaba_bot
```

Script `.env`dagi `DATABASE_URL`dan foydalanadi, parolni buyruq argumentlariga yozmaydi. Nusxada `database.dump` va vaqtinchalik konvertatsiyasiz `files.tar.gz` bo'ladi. Eski `files/` katalogi hali arxivda ishlatilsa, uni ham alohida backup qiling. Nusxalarni boshqa disk/serverga olib boring va saqlash muddatini alohida belgilang.

Tiklashni avval alohida bo'sh bazada sinang: `pg_restore --no-owner --no-acl --dbname=<yangi_baza> database.dump`. `files.tar.gz` ichidagi `files/` tarkibini avvalgi `FILES_DIR`ga tiklang, egasini `talaba` qilib belgilang. Bazadagi fayl yo'llari absolyut bo'lgani uchun katalog yo'li bir xil bo'lishi kerak. Production bazaga tekshirmasdan restore qilmang.

## Tekshirish

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pip check
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/talaba_test \
  .venv/bin/python -m pytest tests/ --cov=bot --cov=db --cov=services --cov-report=term
```

Faqat test uchun ajratilgan baza ishlating. Testlar `DATABASE_URL`ni meros olmaydi, `TEST_DATABASE_URL` orqali alohida vaqtinchalik schema yaratib tozalaydi. Bu qiymat berilmasa DB testlari o'tkazib yuboriladi; berilgan bazaga ulanish ishlamasa test xato beradi. Konvertatsiya testlari uchun LibreOffice zarur. Testlar haqiqiy Telegram/Gemini kalitlarini ishlatmaydi.

Sinovlar atomar to'lov/kvota, xatolikdagi refund, parallel so'rovlar, restart tiklanishi, fayl egaligi, quiz muddati, AI validatsiyasi va Telegram dispatcher orqali asosiy foydalanuvchi yo'lini qamrab oladi. GitHub Actions shu tekshiruvlarni PostgreSQL va LibreOffice bilan bajaradi; minimal test qamrovi 60%.

Jonli foydalanishga chiqarishdan oldin haqiqiy Telegram/Gemini kalitlari bilan sinov akkauntida hujjat yaratish, to'lov tasdiqlash va restartni tekshiring. Mahalliy test natijasi tashqi API mavjudligi yoki real trafikdagi tezlik kafolati emas.
