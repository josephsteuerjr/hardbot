"""
Локализация: русский / узбекский / таджикский.

Выездники СНГ часто плохо знают русский — интерфейс и варианты ответов
переводятся. ВАЖНО: значения статусов в БД хранятся каноничными (русскими) —
переводится только то, что показывается; экспорт в реестр остаётся точным.

Переводы — первый проход, легко править прямо здесь (Егор/носители уточнят).
"""

LANGUAGES = {"ru": "Русский", "uz": "O‘zbekcha", "tg": "Тоҷикӣ"}
DEFAULT_LANG = "ru"


def t(key: str, lang: str = DEFAULT_LANG, **kw) -> str:
    d = TEXT.get(key, {})
    s = d.get(lang) or d.get("ru") or key
    return s.format(**kw) if kw else s


def tv(value: str | None, lang: str = DEFAULT_LANG) -> str:
    """Перевести значение статуса для показа (в БД остаётся русское)."""
    if not value or lang == "ru":
        return value or "—"
    return VALUES.get(value, {}).get(lang, value)


def tf(field_key: str, lang: str = DEFAULT_LANG) -> str:
    """Локализованный заголовок поля."""
    return FIELD_TITLES.get(field_key, {}).get(lang) \
        or FIELD_TITLES.get(field_key, {}).get("ru", field_key)


def variants(key: str) -> set[str]:
    """Все языковые варианты строки — для матчинга нажатой кнопки меню."""
    return set(TEXT.get(key, {}).values())


TEXT: dict[str, dict[str, str]] = {
    "access_denied": {
        "ru": "⛔ Доступа нет. Передайте аналитику ваш ID: <code>{id}</code>",
        "uz": "⛔ Ruxsat yo‘q. Analitikka ID’ingizni yuboring: <code>{id}</code>",
        "tg": "⛔ Дастрасӣ нест. Ба таҳлилгар ID-и худро фиристед: <code>{id}</code>",
    },
    "choose_language": {
        "ru": "Выберите язык:", "uz": "Tilni tanlang:", "tg": "Забонро интихоб кунед:"},
    # старт: только вход в мини-апп (рабочий процесс — там)
    "start_app": {
        "ru": "Здравствуйте! Нажмите кнопку ниже, чтобы открыть приложение.",
        "uz": "Assalomu alaykum! Ilovani ochish uchun quyidagi tugmani bosing.",
        "tg": "Салом! Барои кушодани барнома тугмаи зеринро пахш кунед."},
    "start_no_app": {
        "ru": "Здравствуйте! Приложение появится здесь, как только администратор его подключит.",
        "uz": "Assalomu alaykum! Administrator ulagach, ilova shu yerda paydo bo‘ladi.",
        "tg": "Салом! Пас аз пайвасти маъмур барнома ин ҷо пайдо мешавад."},
    "btn_open_app": {
        "ru": "🚀 Открыть приложение", "uz": "🚀 Ilovani ochish", "tg": "🚀 Кушодани барнома"},
    # автономное приложение (PWA вне Телеги, работает без интернета)
    "btn_app_link": {
        "ru": "📲 Ссылка для входа в приложение",
        "uz": "📲 Ilovaga kirish havolasi",
        "tg": "📲 Пайванди воридшавӣ ба барнома"},
    "btn_manual_android": {
        "ru": "📖 Установить на Android", "uz": "📖 Android’ga o‘rnatish",
        "tg": "📖 Насб дар Android"},
    "btn_manual_ios": {
        "ru": "📖 Установить на iPhone", "uz": "📖 iPhone’ga o‘rnatish",
        "tg": "📖 Насб дар iPhone"},
    "manual_android": {
        "ru": "📖 Установка на Android — по шагам:\n\n"
              "1) Нажмите на ссылку в сообщении выше — откроется страница.\n"
              "2) Если страница открылась ВНУТРИ Телеграма — нажмите ⋮ (три точки "
              "в правом верхнем углу) → «Открыть в…» / «Открыть в Chrome».\n"
              "3) В Chrome подождите, пока появится список клиентов.\n"
              "4) Нажмите ⋮ (три точки в правом верхнем углу Chrome).\n"
              "5) Выберите «Установить приложение» (или «Добавить на гл. экран»).\n"
              "6) Нажмите «Установить». На рабочем столе появится иконка «Маршрут».\n"
              "7) Готово. Теперь ВСЕГДА открывайте с этой иконки, как обычное "
              "приложение — заново входить не нужно.\n\n"
              "Работает без интернета: заполняйте визиты, при связи всё уйдёт само.",
        "uz": "📖 Android’ga o‘rnatish — bosqichma-bosqich:\n\n"
              "1) Yuqoridagi xabardagi havolani bosing — sahifa ochiladi.\n"
              "2) Sahifa Telegram ICHIDA ochilsa — ⋮ (yuqori o‘ng burchakdagi uch "
              "nuqta) → «…da ochish» / «Chrome’da ochish».\n"
              "3) Chrome’da mijozlar ro‘yxati chiqquncha kuting.\n"
              "4) Chrome’ning yuqori o‘ng burchagidagi ⋮ ni bosing.\n"
              "5) «Ilovani o‘rnatish» (yoki «Bosh ekranga qo‘shish»)ni tanlang.\n"
              "6) «O‘rnatish»ni bosing. Ekranda «Marshrut» ikonkasi paydo bo‘ladi.\n"
              "7) Tayyor. Endi DOIM shu ikonkadan oching — qayta kirish kerak emas.\n\n"
              "Internetsiz ishlaydi: chiqishlarni to‘ldiring, aloqa paydo bo‘lganda o‘zi yuboriladi.",
        "tg": "📖 Насб дар Android — қадам ба қадам:\n\n"
              "1) Пайванди паёми болоро пахш кунед — саҳифа мекушояд.\n"
              "2) Агар саҳифа ДАР ДОХИЛИ Телеграм кушода шавад — ⋮ (се нуқта дар "
              "кунҷи болои рост) → «Кушодан дар…» / «Кушодан дар Chrome».\n"
              "3) Дар Chrome то пайдо шудани рӯйхати мизоҷон интизор шавед.\n"
              "4) Дар кунҷи болои рости Chrome ⋮ -ро пахш кунед.\n"
              "5) «Насби барнома» (ё «Ба экрани асосӣ илова»)-ро интихоб кунед.\n"
              "6) «Насб»-ро пахш кунед. Дар экран нишонаи «Маршрут» пайдо мешавад.\n"
              "7) Тайёр. Акнун ҲАМЕША аз ҳамин нишона кушоед — аз нав даромадан "
              "лозим нест.\n\n"
              "Бе интернет кор мекунад: сафарҳоро пур кунед, ҳангоми алоқа худаш фиристода мешавад."},
    "manual_ios": {
        "ru": "🍎 Установка на iPhone — по шагам:\n\n"
              "1) Нажмите на ссылку в сообщении выше — откроется страница.\n"
              "2) Страница откроется ВНУТРИ Телеграма. Нажмите «···» в углу → "
              "«Открыть в Safari». (Если сразу открылся Safari — ещё лучше.)\n"
              "3) В Safari подождите, пока появится список клиентов.\n"
              "4) Нажмите кнопку «Поделиться» — квадратик со стрелкой вверх ⬆️ "
              "внизу экрана по центру.\n"
              "5) Прокрутите список вниз и выберите «На экран “Домой”».\n"
              "6) Нажмите «Добавить». На рабочем столе появится иконка «Маршрут».\n"
              "7) Готово. Теперь ВСЕГДА открывайте с этой иконки — заново входить "
              "не нужно.\n\n"
              "Работает без интернета: заполняйте визиты, при связи всё уйдёт само.",
        "uz": "🍎 iPhone’ga o‘rnatish — bosqichma-bosqich:\n\n"
              "1) Yuqoridagi xabardagi havolani bosing — sahifa ochiladi.\n"
              "2) Sahifa Telegram ICHIDA ochiladi. Burchakdagi «···» → «Safari’da "
              "ochish». (Darhol Safari ochilsa — yanada yaxshi.)\n"
              "3) Safari’da mijozlar ro‘yxati chiqquncha kuting.\n"
              "4) «Ulashish» tugmasini bosing — ekran pastidagi ⬆️ strelkali kvadrat.\n"
              "5) Ro‘yxatni pastga aylantiring va «Bosh ekranga»ni tanlang.\n"
              "6) «Qo‘shish»ni bosing. Ekranda «Marshrut» ikonkasi paydo bo‘ladi.\n"
              "7) Tayyor. Endi DOIM shu ikonkadan oching — qayta kirish kerak emas.\n\n"
              "Internetsiz ishlaydi: chiqishlarni to‘ldiring, aloqa paydo bo‘lganda o‘zi yuboriladi.",
        "tg": "🍎 Насб дар iPhone — қадам ба қадам:\n\n"
              "1) Пайванди паёми болоро пахш кунед — саҳифа мекушояд.\n"
              "2) Саҳифа ДАР ДОХИЛИ Телеграм мекушояд. «···»-и кунҷро пахш кунед → "
              "«Дар Safari кушодан». (Агар якбора Safari кушода шавад — боз беҳтар.)\n"
              "3) Дар Safari то пайдо шудани рӯйхати мизоҷон интизор шавед.\n"
              "4) Тугмаи «Мубодила»-ро пахш кунед — чоркунҷа бо тири ⬆️ дар поёни "
              "экран, дар марказ.\n"
              "5) Рӯйхатро ба поён гардонед ва «Ба экрани асосӣ»-ро интихоб кунед.\n"
              "6) «Илова»-ро пахш кунед. Дар экран нишонаи «Маршрут» пайдо мешавад.\n"
              "7) Тайёр. Акнун ҲАМЕША аз ҳамин нишона кушоед — аз нав даромадан "
              "лозим нест.\n\n"
              "Бе интернет кор мекунад: сафарҳоро пур кунед, ҳангоми алоқа худаш фиристода мешавад."},
    # Ссылка многоразовая в окне активации (db.APP_LINK_MAX_USES): её можно открыть
    # и в браузере Телеги, и потом в Safari/Chrome для установки — ничего не сгорает,
    # никаких кодов руками. Вход в установленный ярлык переезжает сам (персональный
    # манифест со start_url-токеном — см. web._manifest).
    # Отказы на слово «ссылка». Молчание здесь дороже любой формулировки: человек
    # пишет «ссылка», получает обычную кнопку и не понимает, кто сломан — он или бот.
    "applink_not_bound": {
        "ru": "🔒 Ссылка входа выдаётся выездникам и руководителям.\n"
              "Вас пока не закрепили: попросите администратора привязать вас "
              "к сотруднику реестра или назначить руководителем.",
        "uz": "🔒 Kirish havolasi faqat xodimlar va rahbarlar uchun.\n"
              "Siz hali biriktirilmagansiz: administratordan reyestrdagi xodimga "
              "biriktirishni yoki rahbar qilishni so‘rang.",
        "tg": "🔒 Пайванди воридшавӣ танҳо ба кормандон ва роҳбарон дода мешавад.\n"
              "Шумо ҳанӯз вобаста нашудаед: аз маъмур хоҳиш кунед, ки шуморо "
              "ба корманди феҳрист вобаста кунад ё роҳбар таъин кунад.",
    },
    "applink_no_url": {
        "ru": "⚙️ Автономное приложение сейчас не настроено (нет адреса). "
              "Сообщите администратору — это правится на сервере.",
        "uz": "⚙️ Mustaqil ilova hozircha sozlanmagan (manzil yo‘q). "
              "Administratorga xabar bering.",
        "tg": "⚙️ Барномаи мустақил ҳанӯз танзим нашудааст (суроға нест). "
              "Ба маъмур хабар диҳед.",
    },
    "applink_msg": {
        "ru": "📲 Ваша ссылка для входа (только для вас, {days} дн.):\n{url}\n\n"
              "Установка (1 раз):\n"
              "• Android: откройте ссылку в Chrome → меню ⋮ → «Установить приложение».\n"
              "• iPhone: откройте ссылку в Safari → «Поделиться» → «На экран Домой».\n"
              "Вход перенесётся сам. Дальше открывайте с иконки — работает без интернета.",
        "uz": "📲 Kirish havolangiz (faqat siz uchun, {days} kun):\n{url}\n\n"
              "O‘rnatish (1 marta):\n"
              "• Android: havolani Chrome’da oching → menyu ⋮ → «Ilovani o‘rnatish».\n"
              "• iPhone: havolani Safari’da oching → «Ulashish» → «Bosh ekranga».\n"
              "Kirish o‘zi ko‘chib o‘tadi. Keyin ikonkadan oching — internetsiz ishlaydi.",
        "tg": "📲 Пайванди воридшавии шумо (танҳо барои шумо, {days} рӯз):\n{url}\n\n"
              "Насб (1 маротиба):\n"
              "• Android: пайвандро дар Chrome кушоед → меню ⋮ → «Насби барнома».\n"
              "• iPhone: пайвандро дар Safari кушоед → «Мубодила» → «Ба экрани асосӣ».\n"
              "Воридшавӣ худаш мегузарад. Баъд аз нишона кушоед — бе интернет кор мекунад."},
    "language_saved": {
        "ru": "Язык сохранён ✅", "uz": "Til saqlandi ✅", "tg": "Забон захира шуд ✅"},
    "welcome": {
        "ru": "Здравствуйте! Меню — снизу. Откройте «Мои клиенты».",
        "uz": "Assalomu alaykum! Menyu — pastda. «Mijozlarim»ni oching.",
        "tg": "Салом! Меню — дар поён. «Мизоҷони ман»-ро кушоед."},
    "btn_my_clients": {
        "ru": "📋 Мои клиенты", "uz": "📋 Mijozlarim", "tg": "📋 Мизоҷони ман"},
    "btn_search": {"ru": "🔍 Поиск", "uz": "🔍 Qidirish", "tg": "🔍 Ҷустуҷӯ"},
    "btn_my_plans": {"ru": "📅 Мои выезды", "uz": "📅 Chiqishlarim", "tg": "📅 Сафарҳои ман"},
    "btn_language": {"ru": "🌐 Язык", "uz": "🌐 Til", "tg": "🌐 Забон"},
    "no_clients": {
        "ru": "За вами пока никто не закреплён.",
        "uz": "Sizga hali mijoz biriktirilmagan.",
        "tg": "Ба шумо ҳоло мизоҷе вобаста нашудааст."},
    "clients_count": {
        "ru": "Ваши клиенты: {n}", "uz": "Mijozlaringiz: {n}", "tg": "Мизоҷони шумо: {n}"},
    "search_prompt": {
        "ru": "Введите ФИО, договор или телефон:",
        "uz": "F.I.Sh., shartnoma yoki telefon kiriting:",
        "tg": "Ному насаб, шартнома ё телефон ворид кунед:"},
    "nothing_found": {"ru": "Ничего не найдено.", "uz": "Hech narsa topilmadi.", "tg": "Чизе ёфт нашуд."},
    "found_count": {"ru": "Найдено: {n}", "uz": "Topildi: {n}", "tg": "Ёфт шуд: {n}"},
    "no_plans": {"ru": "Повторных выездов нет.", "uz": "Takroriy chiqishlar yo‘q.", "tg": "Сафари такрорӣ нест."},
    "plans_header": {"ru": "Ваши выезды:", "uz": "Chiqishlaringiz:", "tg": "Сафарҳои шумо:"},
    # карточка
    "card_visited": {"ru": "✅ Выезд зафиксирован", "uz": "✅ Chiqish qayd etildi", "tg": "✅ Сафар сабт шуд"},
    "card_not_visited": {"ru": "🆕 Выезда ещё не было", "uz": "🆕 Hali chiqilmagan", "tg": "🆕 Ҳоло сафар нашудааст"},
    "card_photos": {"ru": "Фото: {n}", "uz": "Rasm: {n}", "tg": "Расм: {n}"},
    "card_partner": {"ru": "🏢 Партнёр", "uz": "🏢 Hamkor", "tg": "🏢 Шарик"},
    "card_contract": {"ru": "📄 Договор", "uz": "📄 Shartnoma", "tg": "📄 Шартнома"},
    "card_debt": {"ru": "💰 Долг", "uz": "💰 Qarz", "tg": "💰 Қарз"},
    "card_address": {"ru": "📍 Адрес", "uz": "📍 Manzil", "tg": "📍 Суроға"},
    "card_phone": {"ru": "📞 Телефон", "uz": "📞 Telefon", "tg": "📞 Телефон"},
    "card_phone2": {"ru": "📞 Доп. телефон", "uz": "📞 Qo‘shimcha tel.", "tg": "📞 Телефони иловагӣ"},
    "card_assigned": {"ru": "📅 В работе с", "uz": "📅 Ish boshlangan", "tg": "📅 Дар кор аз"},
    "card_status": {"ru": "📝 Статус", "uz": "📝 Holat", "tg": "📝 Ҳолат"},
    "card_last_visit": {"ru": "📋 Последний выезд", "uz": "📋 Oxirgi chiqish", "tg": "📋 Сафари охирин"},
    "card_replan": {"ru": "🔔 Повторный выезд", "uz": "🔔 Takroriy chiqish", "tg": "🔔 Сафари такрорӣ"},
    # кнопки карточки
    "btn_fix_visit": {"ru": "📝 Зафиксировать выезд", "uz": "📝 Chiqishni qayd etish", "tg": "📝 Сафарро сабт кардан"},
    "btn_fix_pay": {"ru": "💳 Оплата / обещание", "uz": "💳 To‘lov / va’da", "tg": "💳 Пардохт / ваъда"},
    "btn_replan": {"ru": "📅 Повторный выезд", "uz": "📅 Takroriy chiqish", "tg": "📅 Сафари такрорӣ"},
    "btn_geo": {"ru": "📍 Геолокация", "uz": "📍 Geolokatsiya", "tg": "📍 Геолокатсия"},
    "btn_photos": {"ru": "📷 Фото", "uz": "📷 Rasm", "tg": "📷 Расм"},
    "btn_history": {"ru": "📜 История", "uz": "📜 Tarix", "tg": "📜 Таърих"},
    "btn_back_list": {"ru": "⬅️ К списку", "uz": "⬅️ Ro‘yxatga", "tg": "⬅️ Ба рӯйхат"},
    # визит
    "step_header": {"ru": "Выезд · шаг {i}/{n}", "uz": "Chiqish · qadam {i}/{n}", "tg": "Сафар · қадам {i}/{n}"},
    "comment_prompt": {
        "ru": "💬 Комментарий (или пропустите):",
        "uz": "💬 Izoh (yoki o‘tkazib yuboring):",
        "tg": "💬 Шарҳ (ё гузаред):"},
    "btn_skip": {"ru": "➡️ Пропустить", "uz": "➡️ O‘tkazish", "tg": "➡️ Гузарондан"},
    "btn_back": {"ru": "⬅️ Назад", "uz": "⬅️ Orqaga", "tg": "⬅️ Қафо"},
    "btn_cancel": {"ru": "❌ Отмена", "uz": "❌ Bekor", "tg": "❌ Бекор"},
    "visit_saved": {"ru": "✅ Выезд сохранён.", "uz": "✅ Chiqish saqlandi.", "tg": "✅ Сафар захира шуд."},
    "pay_prompt": {
        "ru": "💳 Сумма оплаты/обещания (числом):",
        "uz": "💳 To‘lov/va’da summasi (raqamda):",
        "tg": "💳 Маблағи пардохт/ваъда (бо рақам):"},
    "pay_saved": {"ru": "✅ Оплата сохранена.", "uz": "✅ To‘lov saqlandi.", "tg": "✅ Пардохт захира шуд."},
    "replan_prompt": {
        "ru": "📅 Дата повторного выезда (ДД.ММ.ГГГГ):",
        "uz": "📅 Takroriy chiqish sanasi (KK.OO.YYYY):",
        "tg": "📅 Санаи сафари такрорӣ (РР.ММ.СССС):"},
    "replan_bad": {
        "ru": "Не понял дату. Пример: 15.06.2026",
        "uz": "Sana noto‘g‘ri. Masalan: 15.06.2026",
        "tg": "Сана нодуруст. Мисол: 15.06.2026"},
    "replan_saved": {
        "ru": "✅ Повторный выезд: {d}. Утром придёт напоминание.",
        "uz": "✅ Takroriy chiqish: {d}. Ertalab eslatma keladi.",
        "tg": "✅ Сафари такрорӣ: {d}. Пагоҳӣ ёдрасӣ меояд."},
    "geo_prompt": {
        "ru": "📍 Пришлите геолокацию (скрепка → Геопозиция).",
        "uz": "📍 Geolokatsiya yuboring (qisqich → Joylashuv).",
        "tg": "📍 Геолокатсия фиристед (сванак → Ҷойгиршавӣ)."},
    "geo_saved": {"ru": "✅ Геолокация сохранена.", "uz": "✅ Geolokatsiya saqlandi.", "tg": "✅ Геолокатсия захира шуд."},
    "photo_prompt": {
        "ru": "📷 Пришлите фото. Когда закончите — нажмите «Готово».",
        "uz": "📷 Rasm yuboring. Tugagach — «Tayyor» bosing.",
        "tg": "📷 Расм фиристед. Баъди анҷом — «Тайёр»-ро пахш кунед."},
    "photo_received": {"ru": "📷 Принято. Всего: {n}", "uz": "📷 Qabul qilindi. Jami: {n}", "tg": "📷 Қабул шуд. Ҳамагӣ: {n}"},
    "btn_done": {"ru": "✅ Готово", "uz": "✅ Tayyor", "tg": "✅ Тайёр"},
    "photos_saved": {"ru": "✅ Фото сохранены.", "uz": "✅ Rasmlar saqlandi.", "tg": "✅ Расмҳо захира шуданд."},
    "history_header": {"ru": "📜 История:", "uz": "📜 Tarix:", "tg": "📜 Таърих:"},
    "no_history": {"ru": "Истории пока нет.", "uz": "Hali tarix yo‘q.", "tg": "Ҳоло таърих нест."},
    "cancelled": {"ru": "Отменено.", "uz": "Bekor qilindi.", "tg": "Бекор карда шуд."},
    "reminder_push": {
        "ru": "🔔 Сегодня повторный выезд:\n👤 {client} ({cid})\n📍 {addr}",
        "uz": "🔔 Bugun takroriy chiqish:\n👤 {client} ({cid})\n📍 {addr}",
        "tg": "🔔 Имрӯз сафари такрорӣ:\n👤 {client} ({cid})\n📍 {addr}"},
    # самовыбор «кто вы»
    "who_are_you": {
        "ru": "👋 Здравствуйте! Выберите себя из списка (один раз):",
        "uz": "👋 Assalomu alaykum! Ro‘yxatdan o‘zingizni tanlang (bir marta):",
        "tg": "👋 Салом! Худро аз рӯйхат интихоб кунед (як маротиба):"},
    "no_employees": {
        "ru": "Реестр ещё не загружен. Обратитесь к аналитику.",
        "uz": "Reyestr hali yuklanmagan. Analitikka murojaat qiling.",
        "tg": "Реестр ҳоло бор нашудааст. Ба таҳлилгар муроҷиат кунед."},
    "bound_ok": {
        "ru": "✅ Готово. Вы вошли как: {emp}",
        "uz": "✅ Tayyor. Siz kirdingiz: {emp}",
        "tg": "✅ Тайёр. Шумо ворид шудед: {emp}"},
    "already_bound": {
        "ru": "Вы уже выбрали себя: {emp}",
        "uz": "Siz allaqachon tanlagansiz: {emp}",
        "tg": "Шумо аллакай интихоб кардаед: {emp}"},
    # аппрув привязки (выездник ждёт, владелец одобряет)
    "bind_pending": {
        "ru": "🕓 Заявка отправлена. Доступ откроется после подтверждения аналитиком.",
        "uz": "🕓 Ariza yuborildi. Analitik tasdiqlagach ruxsat ochiladi.",
        "tg": "🕓 Дархост фиристода шуд. Пас аз тасдиқи таҳлилгар дастрасӣ кушода мешавад."},
    "bind_request_admin": {
        "ru": "🔐 Запрос на доступ:\nID <code>{id}</code> хочет работать как «{emp}».\nОдобрить?",
        "uz": "🔐 Ruxsat so‘rovi:\nID <code>{id}</code> «{emp}» bo‘lib ishlamoqchi.\nTasdiqlansinmi?",
        "tg": "🔐 Дархости дастрасӣ:\nID <code>{id}</code> мехоҳад ҳамчун «{emp}» кор кунад.\nТасдиқ мешавад?"},
    "btn_approve": {"ru": "✅ Одобрить", "uz": "✅ Tasdiqlash", "tg": "✅ Тасдиқ"},
    "btn_reject": {"ru": "❌ Отклонить", "uz": "❌ Rad etish", "tg": "❌ Рад"},
    "bind_approved_user": {
        "ru": "✅ Доступ открыт. Вы вошли как: {emp}",
        "uz": "✅ Ruxsat ochildi. Siz kirdingiz: {emp}",
        "tg": "✅ Дастрасӣ кушода шуд. Шумо ворид шудед: {emp}"},
    "bind_rejected_user": {
        "ru": "⛔ Заявка отклонена. Уточните у аналитика.",
        "uz": "⛔ Ariza rad etildi. Analitik bilan bog‘laning.",
        "tg": "⛔ Дархост рад шуд. Бо таҳлилгар тамос гиред."},
    "bind_admin_done": {"ru": "Готово: {emp} — {status}", "uz": "Tayyor: {emp} — {status}",
                        "tg": "Тайёр: {emp} — {status}"},
    # карточка — расширенная инфа
    "card_contracts": {"ru": "Договоры", "uz": "Shartnomalar", "tg": "Шартномаҳо"},
    "card_total": {"ru": "💰 Долг всего", "uz": "💰 Jami qarz", "tg": "💰 Қарзи умумӣ"},
    "card_promise": {"ru": "🤝 Обещание", "uz": "🤝 Va’da", "tg": "🤝 Ваъда"},
    "card_last_pay": {"ru": "💵 Посл. платёж", "uz": "💵 Oxirgi to‘lov", "tg": "💵 Пардохти охирин"},
    "card_birth": {"ru": "🎂 Дата рожд.", "uz": "🎂 Tug‘. sana", "tg": "🎂 Санаи таваллуд"},
    # беседа (владельцу в личку)
    "group_accept_q": {
        "ru": "📨 Бот добавлен в беседу:\n«{title}»\nid: <code>{id}</code>\nСделать её рабочей?",
        "uz": "📨 Bot guruhga qo‘shildi:\n«{title}»\nid: <code>{id}</code>\nIshchi qilamizmi?",
        "tg": "📨 Бот ба гурӯҳ илова шуд:\n«{title}»\nid: <code>{id}</code>\nКорӣ кунем?"},
    "btn_accept": {"ru": "✅ Принять", "uz": "✅ Qabul", "tg": "✅ Қабул"},
    "btn_decline": {"ru": "Нет", "uz": "Yo‘q", "tg": "Не"},
    "group_accepted": {"ru": "✅ Беседа подключена.", "uz": "✅ Guruh ulandi.", "tg": "✅ Гурӯҳ пайваст шуд."},
}

FIELD_TITLES: dict[str, dict[str, str]] = {
    # --- статьи разбивки по долгу (см. debt.py; порядок — как в реестре) ---
    # ЗК и прочие кредитные контрагенты
    "debt_overdue": {"ru": "Просроченный долг", "uz": "Muddati o‘tgan qarz",
                     "tg": "Қарзи мӯҳлаташ гузашта"},
    "interest_overdue": {"ru": "Просроченные проценты", "uz": "Muddati o‘tgan foizlar",
                         "tg": "Фоизҳои мӯҳлаташ гузашта"},
    "penalty": {"ru": "Неустойка и штрафы", "uz": "Penya va jarimalar", "tg": "Ноустуворӣ ва ҷаримаҳо"},
    "duty": {"ru": "Госпошлина", "uz": "Davlat boji", "tg": "Боҷи давлатӣ"},
    # Делимобиль (каршеринг)
    "dtp": {"ru": "Возмещение убытков ДТП", "uz": "YTH zararini qoplash", "tg": "Ҷуброни зарари СНҲ"},
    "rent": {"ru": "Аренда", "uz": "Ijara", "tg": "Иҷора"},
    "camera_fine": {"ru": "Штрафы с камер", "uz": "Kameradan jarimalar", "tg": "Ҷаримаҳо аз камера"},
    "dirt": {"ru": "Грязь в салоне", "uz": "Salon ifloslanishi", "tg": "Ифлосии дохили мошин"},
    "trash": {"ru": "Мусор", "uz": "Axlat", "tg": "Партов"},
    "new_damage": {"ru": "Новые повреждения", "uz": "Yangi shikastlar", "tg": "Осебҳои нав"},
    "evacuation": {"ru": "Эвакуация", "uz": "Evakuatsiya", "tg": "Эвакуатсия"},
    "peni": {"ru": "Пени", "uz": "Peniya", "tg": "Пеня"},
    "other": {"ru": "Прочее", "uz": "Boshqa", "tg": "Дигар"},
    # итоги (не статьи — показываются отдельно)
    "debt_total": {"ru": "Долг всего", "uz": "Jami qarz", "tg": "Қарзи умумӣ"},
    "debt_today": {"ru": "Долг на сегодня", "uz": "Bugungi qarz", "tg": "Қарз имрӯз"},
    # --- поля формы выезда ---
    "call_status": {"ru": "Статус звонка", "uz": "Qo‘ng‘iroq holati", "tg": "Ҳолати занг"},
    "contact_with": {"ru": "С кем был контакт?", "uz": "Kim bilan aloqa bo‘ldi?", "tg": "Бо кӣ тамос буд?"},
    "who_at_address": {"ru": "Кто был на адресе?", "uz": "Manzilda kim bor edi?", "tg": "Дар суроға кӣ буд?"},
    "client_location": {"ru": "Где находится клиент?", "uz": "Mijoz qayerda?", "tg": "Мизоҷ дар куҷост?"},
    "purpose_rf": {"ru": "Цель приезда в РФ", "uz": "RFga kelish maqsadi", "tg": "Мақсади омадан ба РФ"},
    "agreed_payment": {"ru": "Договорились об оплате?", "uz": "To‘lov bo‘yicha kelishildimi?", "tg": "Дар бораи пардохт мувофиқа шуд?"},
}

# Перевод значений статусов (ключ = каноничное русское значение из fields.DROPDOWNS)
VALUES: dict[str, dict[str, str]] = {
    # статус звонка / общий результат
    "Доп. Обогащение": {"uz": "Qo‘shimcha boyitish", "tg": "Ғанигардонии иловагӣ"},
    "Рассрочка": {"uz": "Bo‘lib to‘lash", "tg": "Пардохти қисман"},
    "Отказ от оплаты": {"uz": "To‘lashdan bosh tortish", "tg": "Рад аз пардохт"},
    "Не обработан": {"uz": "Ishlanmagan", "tg": "Коркард нашуда"},
    "В работе": {"uz": "Ish jarayonida", "tg": "Дар кор"},
    "Нет контакта": {"uz": "Aloqa yo‘q", "tg": "Тамос нест"},
    "Обещает оплатить": {"uz": "To‘lashga va’da berdi", "tg": "Ваъдаи пардохт дод"},
    "Оплачен": {"uz": "To‘langan", "tg": "Пардохт шуд"},
    "Передана инф 3 лицу": {"uz": "Ma’lumot 3-shaxsga berildi", "tg": "Маълумот ба шахси сеюм дода шуд"},
    "Тюрьма": {"uz": "Qamoqda", "tg": "Дар зиндон"},
    "Смерть": {"uz": "Vafot etgan", "tg": "Фавтида"},
    "Кредит не брал": {"uz": "Kredit olmagan", "tg": "Қарз нагирифтааст"},
    # с кем контакт
    "Должник": {"uz": "Qarzdor", "tg": "Қарздор"},
    "3 лицо": {"uz": "3-shaxs", "tg": "Шахси сеюм"},
    "Должник и 3 лицо": {"uz": "Qarzdor va 3-shaxs", "tg": "Қарздор ва шахси сеюм"},
    "Контакт не установлен/ должника не знают":
        {"uz": "Aloqa yo‘q / qarzdorni bilishmaydi", "tg": "Тамос нашуд / қарздорро намешиносанд"},
    # договорились об оплате
    "Обещает оплатить клиент": {"uz": "Mijoz to‘lashga va’da berdi", "tg": "Мизоҷ ваъдаи пардохт дод"},
    "Обещает оплатить 3-е лицо": {"uz": "3-shaxs to‘lashga va’da berdi", "tg": "Шахси сеюм ваъдаи пардохт дод"},
    "Обещает оплатить клиент и 3-е лицо":
        {"uz": "Mijoz va 3-shaxs va’da berishdi", "tg": "Мизоҷ ва шахси сеюм ваъда доданд"},
    "На оплату договориться не удалось":
        {"uz": "To‘lov bo‘yicha kelishilmadi", "tg": "Дар бораи пардохт мувофиқа нашуд"},
    "3-е лицо передаст информацию клиенту":
        {"uz": "3-shaxs mijozga xabar beradi", "tg": "Шахси сеюм ба мизоҷ хабар медиҳад"},
    # кто на адресе
    "По адресу проживает клиент": {"uz": "Manzilda mijoz yashaydi", "tg": "Дар суроға мизоҷ зиндагӣ мекунад"},
    "По адресу проживают родственники клиента":
        {"uz": "Manzilda mijozning qarindoshlari yashaydi", "tg": "Дар суроға хешовандон зиндагӣ мекунанд"},
    "По адресу проживают знакомые клиента":
        {"uz": "Manzilda mijozning tanishlari yashaydi", "tg": "Дар суроға шиносон зиндагӣ мекунанд"},
    "По адресу клиента не знают/ не общаются":
        {"uz": "Manzilda mijozni bilishmaydi", "tg": "Дар суроға мизоҷро намешиносанд"},
    "Не удалось установить кто проживает по адресу":
        {"uz": "Kim yashashini aniqlab bo‘lmadi", "tg": "Муайян нашуд, ки кӣ зиндагӣ мекунад"},
    # местоположение
    "Клиент в РФ": {"uz": "Mijoz RFda", "tg": "Мизоҷ дар РФ"},
    "Клиент на родине": {"uz": "Mijoz vatanida", "tg": "Мизоҷ дар ватан"},
    "Клиент в другой стране": {"uz": "Mijoz boshqa davlatda", "tg": "Мизоҷ дар кишвари дигар"},
    "Местоположение не установлено": {"uz": "Joylashuvi aniqlanmadi", "tg": "Ҷойгиршавӣ муайян нашуд"},
    # цель приезда
    "Уехал на работу": {"uz": "Ishga ketgan", "tg": "Ба кор рафтааст"},
    "Переехал жить": {"uz": "Ko‘chib ketgan", "tg": "Кӯчида рафтааст"},
    "Находится на СВО": {"uz": "SVOda", "tg": "Дар СВО"},
    "Уехал на лечение": {"uz": "Davolanishga ketgan", "tg": "Ба табобат рафтааст"},
    "Не дали информацию": {"uz": "Ma’lumot berishmadi", "tg": "Маълумот надоданд"},
}
