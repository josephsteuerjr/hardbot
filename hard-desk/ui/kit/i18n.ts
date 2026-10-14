type Lang = "ru" | "uz" | "tg";
const messages = {
  ru: { reviewQueue: "Разобрать запись", exportSource: "Экспортировать исходник", fixCard: "Исправить в карточке",
    draftError: "Черновик не сохранился на компьютере", reviewSaved: "Проверка сохранена",
    offlinePolicy: "Офлайн-копию можно читать 8 часов. Затем подключитесь к сети. Очередь и черновики остаются.",
    sourceNotice: "Источник указан проверяющим. Автоматического подтверждения телефонии нет.",
    unknownJob: "Результат неизвестен. Сверьте журнал операций перед повтором.",
    retained: "Неотправленные записи и черновики сохраняются для исходного сотрудника." },
  uz: { reviewQueue: "Yozuvni tekshirish", exportSource: "Asl yozuvni eksport qilish", fixCard: "Kartada tuzatish",
    draftError: "Qoralama kompyuterda saqlanmadi", reviewSaved: "Tekshiruv saqlandi",
    offlinePolicy: "Oflayn nusxani 8 soat o‘qish mumkin. Keyin tarmoqqa ulaning. Navbat va qoralamalar qoladi.",
    sourceNotice: "Manbani tekshiruvchi ko‘rsatgan. Telefoniya avtomatik tasdiqlanmaydi.",
    unknownJob: "Natija noma’lum. Takrorlashdan oldin amaliyotlar jurnalini tekshiring.",
    retained: "Yuborilmagan yozuvlar va qoralamalar asl xodim uchun saqlanadi." },
  tg: { reviewQueue: "Баррасии сабт", exportSource: "Содироти нусхаи асл", fixCard: "Ислоҳ дар корт",
    draftError: "Пешнавис дар компютер захира нашуд", reviewSaved: "Санҷиш захира шуд",
    offlinePolicy: "Нусхаи офлайнро 8 соат хондан мумкин аст. Сипас ба шабака пайваст шавед. Навбат ва пешнависҳо мемонанд.",
    sourceNotice: "Манбаъро санҷишкунанда нишон додааст. Телефония худкор тасдиқ намешавад.",
    unknownJob: "Натиҷа номаълум аст. Пеш аз такрор дафтари амалҳоро санҷед.",
    retained: "Сабтҳои фиристоданашуда ва пешнависҳо барои корманди аслӣ нигоҳ дошта мешаванд." },
};
let language: Lang = "ru";
try { const saved = localStorage.getItem("hard.lang"); if (saved && saved in messages) language = saved as Lang; } catch { /* optional preference */ }
export const tr = (key: keyof typeof messages.ru): string => messages[language][key];
export function setLanguage(lang: string): void {
  language = lang in messages ? lang as Lang : "ru";
  document.documentElement.lang = language;
  try { localStorage.setItem("hard.lang", language); } catch { /* optional preference */ }
}
export const currentLanguage = (): Lang => language;
