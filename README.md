# Taksi boti (Telegram)

Yo'lovchilar xaritadan taksi chaqiradi, haydovchilar buyurtma qabul qiladi, admin arizalarni tasdiqlaydi.

## Imkoniyatlar

**Yo'lovchi**
- Xaritada o'z joyini avtomatik ko'rish va borish nuqtasini belgilash (Telegram Mini App)
- Masofa va vaqtga qarab narx, to'lov naqd pul
- Buyurtmani tasdiqlash yoki bekor qilish
- Haydovchi ma'lumotlari va jonli kuzatuv
- 1–5 yulduz bilan baholash, buyurtmalar tarixi

**Haydovchi**
- `/driver` orqali ariza (ism, mashina, telefon, guvohnoma va texpasport rasmi)
- Admin tasdiqlagandan keyin 🟢 Online / ⚫ Offline
- Buyurtmani qabul qilish, jonli lokatsiya ulashish, yakunlash
- Statistika: reyting, bajarilgan buyurtmalar, daromad

**Admin** (`ADMIN_IDS` dagilar)
- `/admin` panel: arizalarni tasdiqlash/rad etish, haydovchilarni bloklash
- Hisobot: bugun, 7 kun, 30 kun
- Narxlarni `/setprice` bilan o'zgartirish (botni qayta ishga tushirmasdan)

## Tuzilishi

```
taxi-bot/
├── main.py         # ishga tushirish
├── config.py       # sozlamalar (.env dan)
├── db.py           # SQLite ma'lumotlar bazasi
├── pricing.py      # masofa va narx hisoblash
├── keyboards.py    # tugmalar menyusi
├── handlers.py     # barcha buyruq va tugmalar
├── webapp/
│   └── index.html  # xarita (Telegram Mini App)
├── requirements.txt
└── .env.example
```

## O'rnatish

1. **Token oling.** Telegram'da @BotFather ga `/newbot` yuboring va tokenni oling.
2. **Admin ID sini bilib oling.** Telegram'da @userinfobot ga yozing, u sizning ID ni aytadi.
3. **Xaritani joylang.** `webapp/index.html` faylini GitHub Pages'ga yuklang (repo Settings → Pages). Manzil HTTPS bo'ladi.
4. **Muhit sozlang:**
   ```bash
   cp .env.example .env
   # .env ichida BOT_TOKEN, WEBAPP_URL va ADMIN_IDS ni to'ldiring
   ```
5. **O'rnating va ishga tushiring:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   python main.py
   ```

## Sinov

1. Admin hisobidan `/driver` yuboring va arizani to'ldiring. Admin panelda ariza chiqadi, ✅ tugmasini bosing.
2. Haydovchi hisobidan 🟢 Online ni bosing.
3. Boshqa hisobdan (yo'lovchi) 🚕 tugmasini bosing, xaritada joy belgilang va tasdiqlang.
4. Haydovchi "Qabul qilish" ni bosadi, so'ng jonli lokatsiya yuboradi.

## Ma'lumki, hali to'liq emas

- **Masofa** to'g'ri chiziq bo'yicha hisoblanadi (×1.3). Aniq yo'l uchun OSRM yoki Google Directions API ulang (`pricing.py`).
- **Shikoyatlar** va **to'lov provayderi** (Click, Payme) yo'q.
- **Haydovchi hujjatlari** Telegram serverida saqlanadi. Real xizmat uchun shaxsiy ma'lumot qoidalariga rioya qiling.
- Bot polling rejimida ishlaydi. Ko'p foydalanuvchi uchun webhook va PostgreSQL tavsiya etiladi.

## Serverda doimiy ishlatish

Bot kompyuter o'chib yonganda ham ishlashi uchun VPS ga joylang va `systemd` servisi qiling, yoki `docker` ishlating.
