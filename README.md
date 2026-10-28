# Luna taksi boti (Telegram)

Yo'lovchilar xaritadan taksi chaqiradi, haydovchilar navbat bilan buyurtma oladi, admin arizalarni tasdiqlaydi va komissiyani nazorat qiladi. To'lov faqat naqd.

## Imkoniyatlar

**Yo'lovchi**
- Xaritada joy belgilash (Telegram Mini App, GitHub Pages)
- Masofa va vaqtga qarab narx (yo'l bo'yicha OSRM, xizmat ishlamasa taxminiy hisob)
- Haydovchi qabul qilgach: telefon va bot ichida xabar almashish
- Haydovchi yo'lda, yetib keldi xabarlari va jonli lokatsiya
- Bekor qilish (qabul qilingandan keyin jarima), shikoyat, 1–5 yulduz baho
- Bir vaqtda faqat bitta faol buyurtma

**Haydovchi**
- `/driver` orqali ariza (ism, mashina, telefon, guvohnoma, texpasport)
- Admin tasdiqlagach 🟢 Online / ⚫ Offline (smena 12 soatdan keyin avtomatik tugaydi)
- Navbat: eng yaqin va eng uzoq kutgan haydovchi birinchi, javob uchun 40 soniya
- Buyurtma qabul qilish, 🚗 Yetib keldim, 🏁 Yakunlash, bekor qilish (jarima qarzga yoziladi)
- Kutish haqi: bepul daqiqadan keyin har daqiqa uchun
- Komissiya (standart 10%) qarzga yoziladi; qarz chegaradan oshsa buyurtma ololmaydi
- Statistika: reyting, safarlar, sof daromad, qarz

**Admin** (`ADMIN_IDS` dagilar) — `/admin`
- Arizalar, haydovchilar (bloklash / blokdan chiqarish)
- Faol buyurtmalarni ko'rish va bekor qilish
- Shikoyatlar ro'yxati va yopish
- Qarzlar ro'yxati, `/debt <haydovchi_id> <summa>` bilan to'lovni qayd etish
- Hisobot: bugun, 7 kun, 30 kun (Asia/Tashkent vaqti bo'yicha)
- `/setprice <kalit> <qiymat>` bilan narx va komissiyani o'zgartirish

## Tuzilishi

```
luna-bot/
├── main.py          # ishga tushirish, bitta nusxa qulfi, log
├── config.py        # sozlamalar (.env dan) va vaqt chegaralari
├── db.py            # SQLite: buyurtma, haydovchi, qarz, shikoyat, zaxira
├── dispatch.py      # navbat, taymerlar, avtomatik offline, kunlik zaxira
├── handlers.py      # barcha buyruq va tugmalar
├── storage.py       # FSM holatini bazada saqlash
├── pricing.py       # masofa (OSRM) va narx
├── keyboards.py     # asosiy menyu
├── webapp/index.html  # xarita (Mini App)
├── tests/           # avtomatik testlar
├── requirements.txt
└── .env.example
```

## O'rnatish

1. @BotFather dan token oling. @userinfobot dan o'z ID ingizni bilib oling.
2. `webapp/index.html` ni GitHub Pages ga joylang. Manzil `https://...` bo'ladi.
3. `.env` yarating (`.env.example` dan nusxa oling): `BOT_TOKEN`, `WEBAPP_URL`, `ADMIN_IDS`.
4. Ishga tushiring (Python 3.12):
   ```bash
   py -3.12 -m pip install -r requirements.txt
   py -3.12 main.py
   ```

Bir vaqtda faqat bitta nusxa ishlaydi: ikkinchisi "allaqachon ishlayapti" deb to'xtaydi.

## Ma'lumotlar

- `taxi.db` — asosiy baza (`DB_PATH` bilan o'zgartiriladi).
- `backups/` — har kuni bir marta zaxira nusxa, oxirgi 14 kun saqlanadi.
- `logs/bot.log` — xatolar va voqealar (5 MB dan keyin aylanadi).

## Testlar

```bash
py -3.12 -m unittest discover -s tests -v
```

## Ma'lum cheklovlar

- Telegram Mini App va OSRM ommaviy serveri cheklangan. Yuklama ko'p bo'lsa o'z OSRM serveringizni `OSRM_BASE` ga ulang.
- Polling rejimi: bitta jarayon. Ko'p serverga o'tish uchun webhook va PostgreSQL kerak bo'ladi.
- Haydovchi hujjatlari Telegram serverida saqlanadi. Real xizmatda shaxsiy ma'lumotlar qoidalariga rioya qiling.
- Yo'lovchining bekor qilish jarimasi hozircha faqat yozib qo'yiladi (`orders.cancel_fee`), uni naqd yig'ish admin zimmasida.
- Komissiya to'lovi qo'lda `/debt` bilan qayd etiladi.

## Serverda doimiy ishlatish

VPS ga joylang va `systemd` servisi qiling, yoki `docker` ishlating. Kompyuter o'chib yonganda bot to'xtaydi.
