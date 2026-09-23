'use strict';
/* Пульт руководителя — оживление пяти экранов.
 *
 * Один модуль на голом JS. Ни фреймворка, ни сборщика, ни CDN — и не из
 * аскетизма: страницу открывают с телефона через тоннель cloudflared, где
 * каждый лишний запрос стоит секунды ожидания, а сам пульт обязан подниматься
 * на ноутбуке без интернета. Всё, что умеет страница, лежит в этих трёх
 * файлах рядом.
 *
 * Разметка (index.html) — контракт: экраны ищутся по data-screen, переходы по
 * data-go, места под данные по id. Здесь не создаётся ни одного «нового»
 * экрана и не трогается element.style: вид задаёт ui.css, а этот файл лишь
 * расставляет его классы.
 *
 * Данные берутся ТОЛЬКО у локального сервера пульта (pult_ruk/*.py). Токен
 * «Харда» живёт на компьютере руководителя, браузер его не видит и видеть не
 * должен — поэтому здесь нет ни одного адреса, кроме /api/….
 *
 * Правило экрана: у каждого числа есть подпись словами, у каждого списка —
 * три состояния (грузим / пусто / сломалось), и ни одной надписи без
 * объяснения. Связь потеряна — данные остаются на экране, а сверху появляется
 * строка, во сколько и что случилось.
 */

// ── Мелкая утварь ───────────────────────────────────────────────────────────

const id = (name) => document.getElementById(name);

/** Узел с классом и текстом.
 *  Верстка собирается узлами, а не склейкой HTML-строк: в данные попадают ФИО
 *  должников и адреса, и любая склейка однажды станет дырой. textContent
 *  разметку не исполняет. */
function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null && text !== '') node.textContent = String(text);
    return node;
}

const NUM = new Intl.NumberFormat('ru-RU');
const TIME = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });
const DAY = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' });

/** Число разрядами: 1 508 299. Не число — null, и зовущий сам решает, что
 *  написать вместо него: «0» и «не пришло» — разные новости, и подменять одно
 *  другим на экране руководителя нельзя. */
function num(value) {
    const n = Number(value);
    return Number.isFinite(n) ? NUM.format(n) : null;
}

/** Число со словом в правильном падеже: 1 должник, 2 должника, 5 должников. */
function plural(n, one, few, many) {
    const a = Math.abs(Number(n) || 0) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    if (b === 1) return one;
    return many;
}

function counted(value, one, few, many) {
    const n = num(value);
    return (n === null ? '?' : n) + ' ' + plural(value, one, few, many);
}

/** Дата из ISO без часовых поясов.
 *  new Date('2026-09-21') — это полночь UTC, и у руководителя западнее Гринвича
 *  она показалась бы двадцатым числом. Собираем дату по частям, в местном дне. */
function isoDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || '').trim());
    if (!m) return null;
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

function dateWords(iso) {
    const d = isoDate(iso);
    return d ? DAY.format(d) : '';
}

function timeWords(when) {
    return when ? TIME.format(when) : '';
}

/** Соседний день: сдвиг в днях от ISO-даты. Нужен, чтобы отличить «вчера» от
 *  просто прошедшего дня в заголовке. */
function isoShift(iso, days) {
    const d = isoDate(iso);
    if (!d) return '';
    d.setDate(d.getDate() + days);
    const p = (x) => String(x).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

function rangeWords(from, to) {
    if (!from || !to) return '';
    if (from === to) return dateWords(from);
    return 'с ' + dateWords(from) + ' по ' + dateWords(to);
}

/** Две буквы для кружка в строке списка. Пустое имя кружок не рисует. */
function initials(name) {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '';
    return parts.slice(0, 2).map((p) => p[0].toUpperCase()).join('');
}

/** Телефон ссылкой только там, где по ней действительно звонят.
 *  На ПК tel: открывает случайную программу или ничего — там номер оставляем
 *  текстом, чтобы его можно было выделить и скопировать. */
/** Узкий экран: телефон. Тот же порог, что у раскладки в ui.css (900px) —
 *  два источника правды о ширине разошлись бы в первый же день. */
function isNarrow() {
    try {
        return !!(window.matchMedia && window.matchMedia('(max-width: 900px)').matches);
    } catch (e) {
        return false;
    }
}

function isTouch() {
    try {
        return !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
    } catch (e) {
        return false;
    }
}

// ── Разговор с локальным сервером пульта ────────────────────────────────────

/** Ошибка с человеческим текстом. needPin — особый случай: телефон в тоннеле
 *  потерял сессию, и вместо экрана надо показать ворота с кодом-пропуском. */
function ApiError(message, needPin) {
    const e = new Error(message || 'пульт не ответил');
    e.needPin = !!needPin;
    return e;
}

async function api(path, init) {
    let resp;
    try {
        resp = await fetch(path, Object.assign({
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
            cache: 'no-store',
        }, init || {}));
    } catch (e) {
        // Сети до СВОЕГО компьютера нет: пульт закрыли, машина уснула или
        // оборвался тоннель. Это не ошибка «Харда», и текст должен отличаться.
        throw ApiError('пульт на компьютере не отвечает');
    }
    let data = null;
    try {
        data = await resp.json();
    } catch (e) {
        data = null;
    }
    if (resp.status === 401 || (data && data.need_pin)) {
        throw ApiError((data && data.error) || 'нужен код-пропуск', true);
    }
    if (!resp.ok) {
        throw ApiError((data && data.error) || ('сервер пульта ответил ошибкой ' + resp.status));
    }
    if (data && data.error) throw ApiError(data.error);
    if (!data) throw ApiError('пульт прислал ответ, который не удалось прочитать');
    return data;
}

function post(path, body) {
    return api(path, {
        method: 'POST',
        headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
}

// ── Состояние ───────────────────────────────────────────────────────────────

function box() {
    return { data: null, at: null, loading: false, error: '', forDate: '' };
}

const S = {
    screen: 'today',
    state: null,          // ответ /api/state
    gate: 'link',         // 'link' — вход по ссылке, 'pin' — код-пропуск с телефона
    fail: '',             // строка «связь потеряна в 14:32…» для шапки
    date: '',             // выбранный день; пусто — «сегодня по серверу»
    qr: '',               // картинка кода: приходит только в ответе /api/phone
    pushes: null,         // свои рубильники рассылок (ответ /api/pushes)
    summary: box(),       // /api/today — кормит и «Сегодня», и «Люди»
    promises: Object.assign(box(), { only: '' }),
    report: Object.assign(box(), { preset: 'week', from: '', to: '' }),
    entry: Object.assign(box(), { issued: null }),   // /api/team_links + последняя выданная
    people: { open: '', cards: {} },   // раскрытая строка и карточки /api/employee
};

/** Дата сводки.
 *  Контракт локального API: date:{iso, today, prev, next}. «Хард» отдаёт её
 *  плоско (date, today, prev_date, next_date), и если сводку однажды прокинут
 *  сквозь как есть — пилюли дней не должны молча умереть. Читаем обе формы,
 *  контрактная главнее. */
function dateOf(data) {
    const d = data && data.date;
    if (d && typeof d === 'object') {
        return {
            iso: d.iso || '', today: d.today || '',
            prev: d.prev || '', next: d.next || '',
        };
    }
    return {
        iso: (typeof d === 'string' ? d : '') || '',
        today: (data && data.today) || '',
        prev: (data && data.prev_date) || '',
        next: (data && data.next_date) || '',
    };
}

/** Сегодняшний день считает сервер (контейнер живёт по UTC, телефон — по +5).
 *  Часы браузера берём, только пока сводка ни разу не пришла. */
function serverToday() {
    const d = dateOf(S.summary.data || {});
    if (d.today) return d.today;
    const now = new Date();
    const p = (x) => String(x).padStart(2, '0');
    return now.getFullYear() + '-' + p(now.getMonth() + 1) + '-' + p(now.getDate());
}

// ── Общие куски экрана ──────────────────────────────────────────────────────

/** Строка связи в шапке. Появляется поверх любого экрана и НЕ стирает данные:
 *  руководителю важнее увидеть вчерашнее число с отметкой «связи нет», чем
 *  пустой экран. */
function renderFail() {
    const node = id('conn-fail');
    if (!node) return;
    node.textContent = S.fail;
    node.hidden = !S.fail;
}

function setFail(message, hasData) {
    S.fail = 'Связь потеряна в ' + timeWords(new Date()) + ': ' + message + '. ' +
        (hasData ? 'На экране — последнее, что успело прийти.'
                 : 'Данных пока нет — откройте раздел ещё раз.');
    renderFail();
}

function clearFail() {
    S.fail = '';
    renderFail();
}

/** Что писать в уголке шапки: грузим / данные на 14:32 / не загрузилось. */
function stampText(rec, prefix) {
    const head = prefix ? prefix + ' · ' : '';
    if (rec.loading && !rec.data) return 'грузим…';
    if (rec.loading) return head + 'обновляем…';
    if (rec.data && rec.at) return head + 'данные на ' + timeWords(rec.at);
    if (rec.error) return head + 'не загрузилось';
    return head;
}

function emptyNote(text) {
    return el('div', 'empty', text);
}

function failNote(text) {
    return el('div', 'fail', text);
}

/** Строка-заготовка, пока данные едут: та же высота, что у настоящей. */
function ghostRow() {
    const row = el('div', 'row row--ghost');
    const body = el('div', 'row__body');
    body.appendChild(el('div', 'skeleton'));
    row.appendChild(body);
    return row;
}

function ghostRows(n) {
    const out = [];
    for (let i = 0; i < n; i += 1) out.push(ghostRow());
    return out;
}

/** Баббл с одним числом и подписью словами.
 *  value === null значит «число не пришло»: на его месте заготовка, а подпись
 *  честно это называет — ноль вместо неизвестного числа врал бы. */
/** «1 243 500 сомони» → число отдельно, единица в подписи.
 *  Сервер отдаёт сумму одной строкой, и в узкой карточке она переносится по
 *  слову: получаются две строки жирного крупного текста, которые спорят с
 *  соседями. Разряды не трогаем — режем по ПОСЛЕДНЕМУ пробелу и только если
 *  хвост не цифра. */
function splitMoney(text) {
    const raw = String(text || '').trim();
    const cut = raw.lastIndexOf(' ');
    if (cut > 0) {
        const tail = raw.slice(cut + 1);
        if (tail && !/[\d]/.test(tail)) {
            return { value: raw.slice(0, cut), label: tail + ' — долг обработанных' };
        }
    }
    return { value: raw, label: 'долг обработанных' };
}

function statBubble(opts) {
    const node = el(opts.go ? 'button' : 'div',
        'bubble' + (opts.flat ? ' bubble--flat' : ''));
    if (opts.go) node.dataset.go = opts.go;   // переход ловит общий обработчик
    // Цвет несёт САМО ЧИСЛО, а не карточка: красная плашка целиком кричит даже
    // тогда, когда просрочено одно обещание, и экран перестаёт различать
    // «плохо» и «очень плохо». Деньги пишутся словами («1,2 млн ₽») и длиннее
    // цифры, поэтому им свой, меньший кегль.
    const big = el('div', 'num' + (opts.money ? ' num--money' : '')
        + (opts.tone ? ' num--' + opts.tone : ''));
    if (opts.value === null || opts.value === undefined || opts.value === '') {
        big.appendChild(el('div', 'skeleton skeleton--num'));
        node.appendChild(big);
        node.appendChild(el('div', 'label', opts.label + ' — число ещё не пришло'));
        return node;
    }
    big.textContent = opts.value;
    node.appendChild(big);
    node.appendChild(el('div', 'label', opts.label));
    // Пояснение отдельной строкой, а не хвостом подписи: на телефоне хвост
    // разворачивал карточку в абзац, и числа тонули в тексте.
    if (opts.sub) node.appendChild(el('div', 'sub', opts.sub));
    return node;
}

function chip(text, tone) {
    return el('span', 'chip' + (tone ? ' chip--' + tone : ''), text);
}

function chipLine(items) {
    const wrap = el('div', 'chips');
    items.filter(Boolean).forEach((c) => wrap.appendChild(c));
    return wrap;
}

function pill(label, pressed, onClick) {
    const b = el('button', 'pill', label);
    b.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    b.addEventListener('click', onClick);
    return b;
}

// ── Переключение экранов ────────────────────────────────────────────────────

function tabButtons() {
    return Array.prototype.slice.call(
        document.querySelectorAll('.rail__item[data-go], .tabs__item[data-go]'));
}

function go(name) {
    S.screen = name;
    Array.prototype.slice.call(document.querySelectorAll('[data-screen]')).forEach((s) => {
        s.hidden = s.dataset.screen !== name;
    });
    // aria-current ставим только на рейку и нижние вкладки: баббл с числом
    // тоже несёт data-go, но «текущей страницей» он не является.
    tabButtons().forEach((b) => {
        if (b.dataset.go === name) b.setAttribute('aria-current', 'page');
        else b.removeAttribute('aria-current');
    });
    ensure(name, false);
    renderFail();
}

/** Данные грузятся по требованию: свод команды на «Харде» считается секундами,
 *  и дёргать его для экрана, который никто не открыл, нечестно по отношению к
 *  выездникам — они в это время ждут сохранения карточки. */
function ensure(name, force) {
    if (name === 'today' || name === 'people') {
        const stale = S.summary.forDate !== (S.date || S.summary.forDate);
        if (force || stale || (!S.summary.data && !S.summary.loading)) loadSummary(S.date);
    } else if (name === 'promises') {
        const stale = S.promises.forDate !== (S.date || S.promises.forDate);
        if (force || stale || (!S.promises.data && !S.promises.loading)) loadPromises();
    } else if (name === 'report') {
        if (force || (!S.report.data && !S.report.loading)) loadReport(S.report.preset);
    } else if (name === 'entry') {
        if (force || (!S.entry.data && !S.entry.loading)) loadEntry();
    } else if (name === 'me') {
        renderMe();
    }
}

// ── Загрузка ────────────────────────────────────────────────────────────────

function onError(rec, e) {
    if (e && e.needPin) {
        showGate('pin');
        return;
    }
    rec.error = e && e.message ? e.message : 'что-то пошло не так';
    setFail(rec.error, !!rec.data);
}

async function loadSummary(date) {
    const rec = S.summary;
    rec.loading = true;
    renderToday();
    renderPeople();
    try {
        const q = date ? ('?date=' + encodeURIComponent(date)) : '';
        const data = await api('/api/today' + q);
        rec.data = data;
        rec.at = new Date();
        rec.error = '';
        const d = dateOf(data);
        S.date = d.iso || S.date;
        rec.forDate = S.date;
        // День сменился — списки обещаний за прошлый день больше не про этот.
        if (S.promises.forDate && S.promises.forDate !== S.date) S.promises.data = null;
        S.people.cards = {};
        clearFail();
    } catch (e) {
        onError(rec, e);
    }
    rec.loading = false;
    renderToday();
    renderPeople();
    renderRailDot();
}

async function loadEmployee(employee) {
    const cards = S.people.cards;
    cards[employee] = cards[employee] || { data: null, error: '', loading: false };
    const card = cards[employee];
    if (card.loading || card.data) return;
    card.loading = true;
    renderPeople();
    try {
        const q = '?emp=' + encodeURIComponent(employee) +
            (S.date ? '&date=' + encodeURIComponent(S.date) : '');
        card.data = await api('/api/employee' + q);
        card.error = '';
        clearFail();
    } catch (e) {
        card.error = e && e.message ? e.message : 'карточка не открылась';
        if (e && e.needPin) { showGate('pin'); return; }
    }
    card.loading = false;
    renderPeople();
}

async function loadPromises() {
    const rec = S.promises;
    rec.loading = true;
    renderPromises();
    try {
        const q = S.date ? ('?date=' + encodeURIComponent(S.date)) : '';
        rec.data = await api('/api/promises' + q);
        rec.at = new Date();
        rec.error = '';
        rec.forDate = S.date;
        clearFail();
    } catch (e) {
        onError(rec, e);
    }
    rec.loading = false;
    renderPromises();
    renderRailDot();
}

/** Окно отчёта считается от серверного «сегодня»: часы ноутбука и часы
 *  контейнера расходятся, и «неделя» с разных машин должна быть одной и той же. */
function reportRange(preset) {
    const today = serverToday();
    if (preset === 'today') return { from: today, to: today };
    if (preset === 'month') return { from: isoShift(today, -29), to: today };
    return { from: isoShift(today, -6), to: today };
}

async function loadReport(preset) {
    const rec = S.report;
    rec.preset = preset;
    const range = reportRange(preset);
    rec.from = range.from;
    rec.to = range.to;
    rec.loading = true;
    renderReport();
    try {
        rec.data = await api('/api/stats?from=' + encodeURIComponent(range.from) +
            '&to=' + encodeURIComponent(range.to));
        rec.at = new Date();
        rec.error = '';
        clearFail();
    } catch (e) {
        onError(rec, e);
    }
    rec.loading = false;
    renderReport();
}

// ── Экран «Сегодня» ─────────────────────────────────────────────────────────

function dayTitle(d) {
    if (!d.iso) return 'Сегодня';
    if (d.iso === d.today) return 'Сегодня, ' + dateWords(d.iso);
    if (d.iso === isoShift(d.today, -1)) return 'Вчера, ' + dateWords(d.iso);
    return dateWords(d.iso);
}

function renderDayPills(d) {
    const box2 = id('today-days');
    if (!d || !d.iso) {
        box2.replaceChildren();
        return;
    }
    const items = [];
    if (d.prev) items.push({ iso: d.prev, label: labelForDay(d.prev, d.today) });
    if (d.today && d.today !== d.prev) items.push({ iso: d.today, label: labelForDay(d.today, d.today) });
    // Смотрим день, которого нет среди соседей (пришли по ссылке или уже
    // ушли вглубь недели) — показываем его отдельной пилюлей, а не теряем.
    if (!items.some((x) => x.iso === d.iso)) {
        items.unshift({ iso: d.iso, label: labelForDay(d.iso, d.today) });
    }
    box2.replaceChildren(...items.map((x) => pill(
        x.label, x.iso === d.iso, () => { if (x.iso !== d.iso) loadSummary(x.iso); })));
}

function labelForDay(iso, today) {
    const short = isNarrow();
    if (iso === today) return short ? 'Сегодня' : 'Сегодня, ' + dateWords(iso);
    if (iso === isoShift(today, -1)) return short ? 'Вчера' : 'Вчера, ' + dateWords(iso);
    return dateWords(iso);
}

function renderToday() {
    const rec = S.summary;
    const data = rec.data;
    const d = dateOf(data || {});
    id('today-title').textContent = dayTitle(d);
    id('today-stamp').textContent = stampText(rec);
    renderDayPills(data ? d : null);

    const totals = data && data.totals;
    const lead = id('today-persons');
    const leadLabel = id('today-persons-label');
    if (!totals) {
        lead.textContent = rec.loading ? '…' : '0';
        leadLabel.textContent = rec.loading
            ? 'считаем день — это занимает несколько секунд'
            : 'должников обработано; данные ещё не пришли';
    } else {
        lead.textContent = num(totals.persons) === null ? '0' : num(totals.persons);
        leadLabel.textContent = 'должников обработано — по ним сегодня был выезд';
    }

    const grid = id('today-grid');
    if (!totals) {
        grid.replaceChildren(
            statBubble({ value: null, label: 'договоров у этих людей' }),
            statBubble({ value: null, label: 'кейсов с фотографией с выезда' }),
            statBubble({ value: null, label: 'долг по обработанным', money: true }),
            statBubble({ value: null, label: 'просроченных обещаний' }));
    } else {
        const withPhoto = num(totals.with_photo);
        const persons = num(totals.persons);
        const overdue = Number(totals.overdue_promises) || 0;
        grid.replaceChildren(
            statBubble({
                go: 'people', value: num(totals.contracts),
                label: 'договоров', sub: 'у должника их бывает несколько',
            }),
            statBubble({
                value: withPhoto, label: 'с фотографией',
                sub: persons ? 'из ' + persons + ' обработанных' : 'кейсов с фото с выезда',
            }),
            statBubble(Object.assign({ money: true },
                splitMoney(totals.money_text || '0'),
                { sub: 'сумма долгов тех, к кому съездили' })),
            statBubble({
                go: 'promises', tone: overdue > 0 ? 'bad' : '',
                value: num(totals.overdue_promises),
                label: 'просроченных обещаний', sub: 'обещали заплатить и не заплатили',
            }));
    }

    renderLeadAside(totals, data);
    renderTodayPeople(data, rec, d);
}

/** «Из чего сложился день» — рядом с ведущим числом.
 *  Это не украшение: руководитель, увидев 34, первым делом спрашивает «сколько
 *  человек их сделали» и «все ли выезжали». Ответ стоит рядом, а не на другом
 *  экране. */
function renderLeadAside(totals, data) {
    const box2 = id('today-aside');
    if (!box2) return;
    if (!totals) { box2.replaceChildren(); return; }
    const team = data ? teamRows(data) : [];
    const worked = team.filter((r) => Number(r.persons) > 0).length;
    const chips = [];
    if (team.length) {
        chips.push(chip(worked + ' из ' + team.length + ' выезжали',
            worked === team.length ? 'ok' : (worked ? '' : 'warn')));
    }
    const photo = Number(totals.with_photo);
    const persons = Number(totals.persons);
    if (Number.isFinite(photo) && Number.isFinite(persons) && persons > 0) {
        const share = Math.round((photo / persons) * 100);
        chips.push(chip('с фото ' + share + '%', share >= 80 ? 'ok' : 'warn'));
    }
    box2.replaceChildren(...chips);
}

function renderTodayPeople(data, rec, d) {
    const rows = id('today-people');
    if (!data) {
        rows.replaceChildren(...(rec.loading
            ? ghostRows(3)
            : [emptyNote(rec.error
                ? 'Кто работал — пока не знаем: список не загрузился.'
                : 'Кто работал — покажем, как только придут данные.')]));
        return;
    }
    const all = teamRows(data);
    const worked = all.filter((r) => Number(r.persons) > 0);
    const out = [];
    const totals = data.totals || {};
    out.push(ghostSummaryRow(
        counted(totals.active !== undefined ? totals.active : worked.length,
            'сотрудник', 'сотрудника', 'сотрудников') + ' выезжали',
        'всего в вашем охвате ' + counted(totals.employees !== undefined ? totals.employees : all.length,
            'человек', 'человека', 'человек')));
    if (!worked.length) {
        out.push(emptyNote(d.iso && d.iso === d.today
            ? 'Сегодня выездов ещё не было.'
            : 'В этот день выездов не было.'));
    } else {
        worked.forEach((r) => out.push(employeeRow(r, false, () => {
            S.people.open = r.employee;
            loadEmployee(r.employee);
            go('people');
        })));
    }
    rows.replaceChildren(...out);
}

/** Плоский список сотрудников сводки. display[] — уже нужный порядок; groups[]
 *  остаётся запасным входом, если сервер отдал только его. */
function teamRows(data) {
    if (data && Array.isArray(data.display) && data.display.length) return data.display;
    const out = [];
    ((data && data.groups) || []).forEach((g) => {
        (g.employees || []).forEach((r) => out.push(r));
    });
    return out;
}

function ghostSummaryRow(name, note) {
    const row = el('div', 'row row--ghost');
    const body = el('div', 'row__body');
    body.appendChild(el('div', 'row__name', name));
    if (note) body.appendChild(el('div', 'row__note', note));
    row.appendChild(body);
    return row;
}

function employeeRow(r, opened, onClick) {
    const b = el('button', 'row');
    b.setAttribute('aria-expanded', opened ? 'true' : 'false');
    const face = initials(r.employee);
    if (face) b.appendChild(el('div', 'row__face', face));
    const body = el('div', 'row__body');
    body.appendChild(el('div', 'row__name', r.employee || 'Без имени'));
    body.appendChild(el('div', 'row__note', Number(r.persons) > 0
        ? counted(r.persons, 'должник', 'должника', 'должников') + ' · ' +
          counted(r.contracts, 'договор', 'договора', 'договоров') + ' · с фото ' + (num(r.with_photo) || '0')
        : 'выездов в этот день не было'));
    b.appendChild(body);
    const tail = el('div', 'row__tail');
    tail.appendChild(el('div', 'row__big', num(r.persons) === null ? '0' : num(r.persons)));
    tail.appendChild(el('div', 'row__note', plural(r.persons, 'должник', 'должника', 'должников')));
    b.appendChild(tail);
    b.addEventListener('click', onClick);
    return b;
}

// ── Экран «Люди» ────────────────────────────────────────────────────────────

function renderPeople() {
    const rec = S.summary;
    const data = rec.data;
    id('people-stamp').textContent = stampText(rec, S.date ? dateWords(S.date) : '');
    const filters = id('people-filters');
    const rows = id('people-rows');

    if (!data) {
        filters.replaceChildren();
        rows.replaceChildren(...(rec.loading
            ? ghostRows(4)
            : [emptyNote(rec.error
                ? 'Список сотрудников не загрузился. Откройте раздел ещё раз.'
                : 'Список сотрудников ещё не загружен.')]));
        return;
    }

    const all = teamRows(data);
    const groups = ((data.groups) || []).filter((g) => g.title);
    const picks = [
        { key: 'all', label: 'Все · ' + (num(all.length) || '0'), list: all },
        {
            key: 'active',
            label: 'Работали · ' + (num(all.filter((r) => Number(r.persons) > 0).length) || '0'),
            list: all.filter((r) => Number(r.persons) > 0),
        },
    ];
    groups.forEach((g) => picks.push({
        key: 'g:' + (g.code || g.title),
        label: (g.flag ? g.flag + ' ' : '') + g.title + ' · ' + (num((g.employees || []).length) || '0'),
        list: g.employees || [],
    }));
    if (!picks.some((p) => p.key === S.people.filter)) S.people.filter = 'all';
    filters.replaceChildren(...picks.map((p) => pill(p.label, p.key === S.people.filter, () => {
        S.people.filter = p.key;
        renderPeople();
    })));

    const shown = (picks.find((p) => p.key === S.people.filter) || picks[0]).list;
    if (!shown.length) {
        rows.replaceChildren(emptyNote(S.people.filter === 'active'
            ? 'В этот день никто не выезжал.'
            : 'В вашем охвате пока нет сотрудников — состав заводит аналитик.'));
        return;
    }
    const out = [];
    shown.forEach((r) => {
        const opened = S.people.open === r.employee;
        out.push(employeeRow(r, opened, () => {
            S.people.open = opened ? '' : r.employee;
            if (!opened) loadEmployee(r.employee);
            renderPeople();
        }));
        if (opened) out.push(employeeCard(r));
    });
    rows.replaceChildren(...out);
}

/** Подробности раскрываются ПОД строкой, а не новым экраном: руководитель не
 *  теряет место в списке и не жмёт «назад» на телефоне. */
function employeeCard(row) {
    const card = el('div', 'row__more');
    const state = S.people.cards[row.employee] || {};
    if (state.loading && !state.data) {
        card.appendChild(el('div', 'skeleton'));
        card.appendChild(el('div', 'note', 'Открываем карточку…'));
        return card;
    }
    if (state.error && !state.data) {
        card.appendChild(failNote('Карточка не открылась: ' + state.error));
        return card;
    }
    const d = state.data;
    if (!d) {
        card.appendChild(el('div', 'note', 'Карточка ещё не открыта.'));
        return card;
    }

    const outcomes = (d.outcomes || []).map((o) => chip(
        (o.emoji ? o.emoji + ' ' : '') + o.status + ' · ' + (num(o.n) || '0')));
    if (outcomes.length) card.appendChild(chipLine(outcomes));

    card.appendChild(el('div', 'note',
        counted(d.persons, 'должник', 'должника', 'должников') + ' за день · ' +
        counted(d.contracts, 'договор', 'договора', 'договоров') + ' · ' +
        'фотографий с выезда ' + (num(d.photos_day) || '0')));
    card.appendChild(el('div', 'note',
        'Долг по обработанным: ' + (d.money_text || 'сумм нет')));

    const act = d.activity;
    if (act) {
        const bits = [];
        if (act.last_activity) bits.push('последняя работа ' + dateWords(String(act.last_activity).slice(0, 10)));
        if (act.visits_7d !== undefined && act.visits_7d !== null) {
            bits.push('за неделю ' + counted(act.visits_7d, 'выезд', 'выезда', 'выездов'));
        }
        if (act.visits_30d !== undefined && act.visits_30d !== null) {
            bits.push('за месяц ' + (num(act.visits_30d) || '0'));
        }
        if (bits.length) card.appendChild(el('div', 'note', bits.join(' · ')));
        const idle = Number(act.idle_days);
        if (Number.isFinite(idle) && idle >= 3) {
            card.appendChild(chipLine([chip('молчит ' + counted(idle, 'день', 'дня', 'дней'), 'warn')]));
        }
    }

    const cases = d.cases || [];
    if (cases.length) {
        const list = el('div', 'rows');
        cases.slice(0, 12).forEach((c) => {
            const row2 = el('div', 'row row--ghost');
            const body = el('div', 'row__body');
            body.appendChild(el('div', 'row__name', c.client || 'Без имени'));
            body.appendChild(el('div', 'row__note',
                (c.emoji ? c.emoji + ' ' : '') + (c.outcome || 'исход не записан')));
            body.appendChild(el('div', 'row__note',
                'долг ' + (c.money_text || 'не указан') + ' · ' +
                (Number(c.photos) > 0 ? 'фото ' + (num(c.photos) || '0') : 'фото нет')));
            row2.appendChild(body);
            list.appendChild(row2);
        });
        card.appendChild(list);
        if (cases.length > 12) {
            card.appendChild(el('div', 'note',
                'Показаны первые 12 из ' + (num(cases.length) || '') + ' — остальные в отчёте.'));
        }
    } else {
        card.appendChild(el('div', 'note', 'В этот день выездов у него не было.'));
    }

    const btns = el('div', 'btns');
    const toPromises = el('button', 'btn', 'Его просроченные обещания');
    toPromises.addEventListener('click', () => {
        S.promises.only = row.employee;
        go('promises');
    });
    btns.appendChild(toPromises);
    card.appendChild(btns);
    return card;
}

// ── Экран «Обещания» ────────────────────────────────────────────────────────

/** Сумма обещания приходит числом без валюты: в базе это int. Валюту берём из
 *  долга того же человека — если долг в одной валюте, обещание в ней же. При
 *  смешанном долге валюту не выдумываем. */
function promisedMoney(item) {
    const n = Number(item.promised_sum);
    if (!Number.isFinite(n) || !n) return '';
    const units = Array.isArray(item.money) ? item.money : [];
    const unit = units.length === 1 ? String(units[0].unit || '') : '';
    return NUM.format(n) + (unit ? ' ' + unit : '');
}

function promiseRows(data) {
    const out = [];
    ((data && data.employees) || []).forEach((e) => {
        (e.items || []).forEach((item) => out.push(Object.assign({ employee: e.employee }, item)));
    });
    // Самые тухлые сверху: у руководителя первый вопрос — «кто висит дольше всех».
    out.sort((a, b) => (Number(b.days_overdue) || 0) - (Number(a.days_overdue) || 0));
    return out;
}

function renderPromises() {
    const rec = S.promises;
    const rows = id('promises-rows');
    id('promises-stamp').textContent = stampText(rec, S.date ? dateWords(S.date) : '');

    if (!rec.data) {
        rows.replaceChildren(...(rec.loading
            ? ghostRows(4)
            : [emptyNote(rec.error
                ? 'Список не загрузился. Откройте раздел ещё раз.'
                : 'Список ещё не загружен.')]));
        return;
    }

    const all = promiseRows(rec.data);
    const shown = S.promises.only ? all.filter((x) => x.employee === S.promises.only) : all;
    const totals = rec.data.totals || {};
    const out = [];

    out.push(ghostSummaryRow(
        counted(totals.persons !== undefined ? totals.persons : all.length,
            'просроченное обещание', 'просроченных обещания', 'просроченных обещаний'),
        promiseSummaryNote(totals)));

    if (S.promises.only) {
        const back = el('button', 'row');
        const body = el('div', 'row__body');
        body.appendChild(el('div', 'row__name', 'Показаны обещания: ' + S.promises.only));
        body.appendChild(el('div', 'row__note', 'нажмите, чтобы показать всю команду'));
        back.appendChild(body);
        back.addEventListener('click', () => {
            S.promises.only = '';
            renderPromises();
        });
        out.push(back);
    }

    if (!shown.length) {
        out.push(emptyNote(S.promises.only
            ? 'У этого сотрудника просроченных обещаний нет.'
            : 'Просроченных обещаний нет — все обещания пока в срок.'));
    } else {
        shown.forEach((item) => out.push(promiseRow(item)));
    }
    rows.replaceChildren(...out);
}

function promiseSummaryNote(totals) {
    const bits = [];
    if (totals.untouched !== undefined) {
        bits.push('к ' + (num(totals.untouched) || '0') + ' после обещания не приезжали');
    }
    if (totals.worked !== undefined) {
        bits.push('к ' + (num(totals.worked) || '0') + ' съездили');
    }
    if (totals.deferred) {
        bits.push(counted(totals.deferred, 'обещание', 'обещания', 'обещаний') + ' ещё впереди');
    }
    return bits.join(' · ');
}

function promiseRow(item) {
    const row = el('div', 'row row--ghost');
    const face = initials(item.client);
    if (face) row.appendChild(el('div', 'row__face', face));

    const body = el('div', 'row__body');
    body.appendChild(el('div', 'row__name', item.client || 'Имя не записано'));
    const sum = promisedMoney(item);
    body.appendChild(el('div', 'row__note', (sum
        ? 'обещал ' + sum
        : 'сумму обещания не записали') +
        (item.promise_date ? ' на ' + dateWords(item.promise_date) : '')));
    body.appendChild(el('div', 'row__note', 'выездник ' + (item.employee || 'не указан')));

    const marks = [];
    if (item.money_text) marks.push(chip('долг ' + item.money_text));
    marks.push(item.visited_after_promise
        ? chip('съездили после обещания')
        : chip('не приезжали', 'bad'));
    // Телефон — только если он пришёл: локальный API его сейчас не отдаёт
    // (см. ответ по задаче), но как только появится — строка оживёт сама.
    const phone = String(item.phone || '').trim();
    if (phone) {
        if (isTouch()) {
            const a = el('a', 'chip chip--accent', 'позвонить ' + phone);
            a.href = 'tel:' + phone.replace(/[^\d+]/g, '');
            marks.push(a);
        } else {
            marks.push(chip('телефон ' + phone));
        }
    }
    body.appendChild(chipLine(marks));
    row.appendChild(body);

    const tail = el('div', 'row__tail');
    tail.appendChild(el('div', 'row__big', num(item.days_overdue) === null ? '0' : num(item.days_overdue)));
    tail.appendChild(el('div', 'row__note', plural(item.days_overdue, 'день', 'дня', 'дней') + ' ждём'));
    row.appendChild(tail);
    return row;
}

// ── Экран «Отчёт» ───────────────────────────────────────────────────────────

const REPORT_PRESETS = [
    { key: 'today', label: 'Сегодня' },
    { key: 'week', label: 'Неделя' },
    { key: 'month', label: 'Месяц' },
];

function renderReport() {
    const rec = S.report;
    const pills = id('report-period');
    pills.replaceChildren(...REPORT_PRESETS.map((p) => pill(p.label, p.key === rec.preset, () => {
        if (p.key !== rec.preset || !rec.data) loadReport(p.key);
    })));

    const stamp = id('report-stamp');
    if (stamp) stamp.textContent = stampText(rec, rangeWords(rec.from, rec.to));

    const grid = id('report-totals');
    const totals = rec.data && rec.data.totals;
    if (!totals) {
        grid.replaceChildren(...(rec.loading
            ? [statBubble({ flat: true, value: null, label: 'выездов сделано' }),
               statBubble({ flat: true, value: null, label: 'должников объехали' }),
               statBubble({ flat: true, value: null, label: 'заплатили после выезда' })]
            : [emptyNote(rec.error
                ? 'Итоги не загрузились. Выберите период ещё раз.'
                : 'Выберите период — посчитаем итоги.')]));
        return;
    }
    if (!Number(totals.visits)) {
        grid.replaceChildren(emptyNote('За этот период выездов не было. ' +
            'Если так быть не должно — проверьте период или спросите аналитика.'));
        return;
    }

    const plan = Number(totals.plan) || 0;
    const visits = Number(totals.visits) || 0;
    grid.replaceChildren(
        statBubble({
            flat: true, value: num(totals.visits),
            label: 'выездов сделано' + (plan ? ' — план на период ' + (num(plan) || '') +
                ' (' + Math.round((visits / plan) * 100) + '%)' : ''),
        }),
        statBubble({ flat: true, value: num(totals.persons), label: 'должников объехали' }),
        statBubble({
            flat: true, value: num(totals.contracts),
            label: 'договоров в этих кейсах — у одного должника их бывает несколько',
        }),
        statBubble({
            flat: true, value: num(totals.paid), tone: 'ok',
            label: 'должников заплатили после выезда',
        }),
        statBubble({ flat: true, value: num(totals.promised), label: 'пообещали заплатить' }),
        statBubble({
            flat: true, value: num(totals.overdue),
            tone: Number(totals.overdue) > 0 ? 'bad' : '',
            label: 'сорвали обещание — срок прошёл, денег нет',
        }),
        statBubble({ flat: true, value: num(totals.with_photo), label: 'кейсов с фотографией с выезда' }),
        statBubble({
            flat: true, money: true, value: totals.money_paid_text || '0',
            label: 'долг тех, кто заплатил — сколько сняли с портфеля',
        }),
        statBubble({
            flat: true, money: true, value: totals.money_text || '0',
            label: 'долг всех, к кому съездили',
        }));
}

/** Книга качается через тот же локальный сервер: сначала смотрим, не прислал
 *  ли он вместо файла отказ по-русски, и только потом отдаём браузеру. */
async function downloadBook() {
    const btn = id('report-xlsx');
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Собираем книгу…';
    const rec = S.report;
    const range = (rec.from && rec.to) ? { from: rec.from, to: rec.to } : reportRange(rec.preset);
    try {
        const url = '/api/report.xlsx?from=' + encodeURIComponent(range.from) +
            '&to=' + encodeURIComponent(range.to);
        const resp = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
        const type = resp.headers && resp.headers.get ? (resp.headers.get('Content-Type') || '') : '';
        if (!resp.ok || type.indexOf('json') >= 0) {
            let message = 'книга не собралась';
            try {
                const bad = await resp.json();
                if (bad && bad.error) message = bad.error;
            } catch (e) { /* не JSON — оставляем общий текст */ }
            throw ApiError(message, resp.status === 401);
        }
        const blob = await resp.blob();
        const href = URL.createObjectURL(blob);
        const a = el('a');
        a.href = href;
        a.download = 'Хард — отчёт ' + range.from + '—' + range.to + '.xlsx';
        document.body.appendChild(a);
        a.click();
        a.remove();
        // Отпускаем память не сразу: Safari успевает начать скачивание позже.
        setTimeout(() => URL.revokeObjectURL(href), 30000);
        clearFail();
    } catch (e) {
        if (e && e.needPin) showGate('pin');
        else setFail(e && e.message ? e.message : 'книга не скачалась', !!rec.data);
    }
    btn.disabled = false;
    btn.textContent = label;
}

// ── Экран «Я и телефон» ─────────────────────────────────────────────────────

/** Рубильники рассылок. Ключи те же, что у сервера «Хард» (push_visits,
 *  push_digest, push_promises) — если локальный API однажды начнёт их отдавать
 *  и принимать, переключатели оживут без переписывания экрана. */
const PUSH_SWITCHES = [
    {
        key: 'push_visits', name: 'Каждый выезд',
        note: 'бот пишет в личку, как только выездник закрыл карточку',
    },
    {
        key: 'push_digest', name: 'Вечерняя сводка',
        note: 'один раз в конце дня — итог по всей команде',
    },
    {
        key: 'push_promises', name: 'Просроченные обещания',
        note: 'напоминание про тех, кто обещал заплатить и не заплатил',
    },
];

function renderMe() {
    const st = S.state || {};
    // Имя — текст, а не число: у .num свой кегль в 44px, и фамилия в нём
    // выглядит вывеской, а не подписью «под кем я смотрю».
    id('me-name').className = 'name-lead';
    const mgr = st.manager || {};
    id('me-name').textContent = mgr.name || 'Имя не задано';
    const scopeBits = [];
    scopeBits.push('охват: ' + (mgr.scope || 'не задан — его ставит аналитик'));
    const team = S.summary.data ? teamRows(S.summary.data).length : 0;
    if (team) scopeBits.push(counted(team, 'сотрудник', 'сотрудника', 'сотрудников') + ' в команде');
    id('me-scope').textContent = scopeBits.join(' · ');
    // Под кем и ГДЕ: с двумя серверами «куда я смотрю» решается взглядом
    // на этот экран, без звонка аналитику.
    id('me-server-now').textContent = st.hard_url
        ? 'сервер: ' + hostOf(st.hard_url) : '';

    renderServers(st.servers || []);

    // То же самое — внизу рейки: руководителю, открывшему чужой ноутбук, важно
    // с первого взгляда видеть, под кем он смотрит.
    const railName = id('rail-name');
    if (railName) {
        railName.textContent = mgr.name || 'Я и телефон';
        const face = id('rail-face');
        if (face) face.textContent = initials(mgr.name) || '—';
        const scope = id('rail-scope');
        if (scope) scope.textContent = mgr.scope || 'Я и телефон';
    }

    renderPhoneCard(st.phone || {});
    renderSwitches();
}

/** Rubilymail toggles: the manager turns them personally — right from here.
 *  The state arrives from /api/pushes; until it arrives, the toggles are locked and
 *  explain why, so it doesn't read as a malfunction. */
async function renderSwitches() {
    const wrap = id('me-switches');
    if (!wrap) return;
    if (!S.pushes) {
        try {
            S.pushes = (await api('/api/pushes')).pushes || null;
        } catch (e) {
            S.pushes = null;
        }
    }
    const pushes = S.pushes;
    const rows = PUSH_SWITCHES.map((spec) => {
        const known = pushes && Object.prototype.hasOwnProperty.call(pushes, spec.key);
        return toggleRow(spec, known ? !!pushes[spec.key] : false, known,
            known ? ((v) => switchPush(spec.key, v)) : null);
    });
    const why = el('div', 'note note--tiny', pushes
        ? 'You toggle these yourself — they take effect immediately. The analyst can change the same ones in their console.'
        : 'Mailing status didn\'t arrive — check the connection and reopen the page.');
    wrap.replaceChildren(why, ...rows);
}

async function switchPush(key, value) {
    try {
        const out = await post('/api/pushes', { key, value });
        if (out && out.pushes) S.pushes = out.pushes;
    } catch (e) {
        setFail((e && e.message) || 'the toggle didn\'t switch', false);
    }
    await renderSwitches();
}

function toggleRow(spec, value, known, onToggle) {
    const row = el('label', known ? 'toggle' : 'toggle toggle--locked');
    const body = el('div', 'toggle__body');
    body.appendChild(el('div', 'toggle__name', spec.name));
    body.appendChild(el('div', 'toggle__note',
        spec.note + (known ? '' : ' · state didn\'t arrive')));
    row.appendChild(body);
    const input = el('input');
    input.type = 'checkbox';
    input.checked = !!value;
    input.disabled = !known;
    if (known && onToggle) input.addEventListener('change', () => onToggle(input.checked));
    row.appendChild(input);
    row.appendChild(el('span', 'toggle__box'));
    return row;
}

// ── Вход команды ────────────────────────────────────────────────────────────

/** Ссылки входа выдаёт сам руководитель: строка на человека, состояние входа
 *  и две кнопки. Выдача сразу показывает ссылку с QR — переслать её в
 *  мессенджер можно одним движением. */
async function loadEntry() {
    const rec = S.entry;
    rec.loading = true;
    renderEntry();
    try {
        const data = await api('/api/team_links');
        rec.data = data;
        rec.error = '';
        rec.at = new Date();
    } catch (e) {
        onError(rec, e);
    } finally {
        rec.loading = false;
        renderEntry();
    }
}

function entryState(row) {
    if (row.entered) {
        return { text: 'вошёл', ok: true,
                 hint: (row.devices || 1) + ' устройств' };
    }
    if (row.alive || row.code) {
        const left = Math.max((row.max_uses || 2) - (row.uses || 0), 0);
        return { text: 'выдана — ждёт', ok: false,
                 hint: 'открытий осталось: ' + left };
    }
    return { text: 'не выдавали', ok: false, hint: '' };
}

function renderEntry() {
    const rec = S.entry;
    const rows = id('entry-rows');
    if (!rows) return;
    const stamp = id('entry-stamp');
    if (rec.loading && !rec.data) {
        rows.replaceChildren(el('div', 'note', 'Загружаем команду…'));
        if (stamp) stamp.textContent = '';
        return;
    }
    if (rec.error && !rec.data) {
        rows.replaceChildren(el('div', 'fail', rec.error));
        return;
    }
    const links = (rec.data && rec.data.links) || [];
    if (stamp) {
        stamp.textContent = rec.at ? 'обновлено ' + timeWords(rec.at) : '';
    }
    if (!links.length) {
        rows.replaceChildren(el('div', 'note',
            'В команде пока никто не закреплён за портфелями.'));
        return;
    }
    rows.replaceChildren(...links.map((row) => {
        const state = entryState(row);
        const line = el('div', 'row');
        const body = el('div', 'row__body');
        body.appendChild(el('div', 'row__name', row.employee));
        body.appendChild(el('div', 'row__note' + (state.ok ? ' row__note--ok' : ''),
            state.text + (state.hint ? ' · ' + state.hint : '')));
        line.appendChild(body);
        const btns = el('div', 'btns btns--row');
        const issue = el('button', 'btn btn--primary',
                         row.code ? 'Выдать заново' : 'Выдать вход');
        issue.type = 'button';
        issue.addEventListener('click', () => issueEntry(row.employee));
        btns.appendChild(issue);
        if (row.entered || row.code) {
            const revoke = el('button', 'btn btn--danger', 'Отозвать');
            revoke.type = 'button';
            revoke.addEventListener('click', () =>
                revokeEntry(row.employee, row.entered));
            btns.appendChild(revoke);
        }
        line.appendChild(btns);
        return line;
    }));
}

async function issueEntry(employee) {
    try {
        const out = await post('/api/team_link', { employee, action: 'issue' });
        if (out && out.url) {
            S.entry.issued = out;
            renderIssued();
            await loadEntry();
        } else {
            setFail((out && out.error) || 'ссылку собрать не удалось', false);
        }
    } catch (e) {
        setFail((e && e.message) || 'ссылку собрать не удалось', false);
    }
}

async function revokeEntry(employee, entered) {
    const question = entered
        ? 'Отозвать вход «' + employee + '»? Телефон выйдет из приложения сразу.'
        : 'Погасить выданную ссылку «' + employee + '»? Открыть её будет нельзя.';
    if (!window.confirm(question)) return;
    try {
        await post('/api/team_link', { employee, action: 'revoke' });
        if (S.entry.issued && S.entry.issued.employee === employee) {
            S.entry.issued = null;
            renderIssued();
        }
        await loadEntry();
    } catch (e) {
        setFail((e && e.message) || 'отзыв не прошёл', false);
    }
}

function renderIssued() {
    const bubble = id('entry-issued');
    if (!bubble) return;
    const out = S.entry.issued;
    bubble.hidden = !out;
    if (!out) return;
    id('entry-issued-name').textContent = out.employee || '';
    id('entry-issued-url').textContent = out.url || '';
    id('entry-issued-meta').textContent =
        'открытий: ' + (out.uses || 2) + ' · годна до ' + (out.expires_at || '—');
    const qr = id('entry-qr');
    if (out.qr) {
        qr.src = out.qr;
        qr.hidden = false;
    } else {
        qr.removeAttribute('src');
        qr.hidden = true;
    }
}

function renderServers(list) {
    const bubble = id('me-server');
    const rows = id('me-server-rows');
    if (!bubble || !rows) return;
    bubble.hidden = (list || []).length < 2;
    if (bubble.hidden) return;
    const now = (S.state && S.state.hard_url) || '';
    rows.replaceChildren(...list.map((srv) => {
        const btn = el('button', 'btn' + (srv.url === now ? ' btn--primary' : ''));
        btn.type = 'button';
        btn.textContent = srv.ready ? srv.host : (srv.host + ' — без токена');
        btn.disabled = srv.url === now;
        btn.addEventListener('click', () => switchServer(srv.url));
        return btn;
    }));
}

async function switchServer(url) {
    try {
        await post('/api/server', { url });
    } catch (e) {
        setFail((e && e.message) || 'не получилось переключиться', false);
        return;
    }
    // Данные прежнего сервера не должны пережить переключение: иначе на новом
    // сервере человек смотрит вчерашнюю команду старого.
    S.summary = box();
    S.promises = Object.assign(box(), { only: '' });
    S.report = Object.assign(box(), { preset: 'week', from: '', to: '' });
    S.entry = Object.assign(box(), { issued: null });
    S.pushes = null;
    S.people = { open: '', cards: {} };
    await boot();
}

function hostOf(url) {
    try { return new URL(url).host || url; } catch (e) { return url; }
}

function renderPhoneCard(phone) {
    const on = !!phone.on;
    const note = id('phone-note');
    if (!S.phoneError) {
        note.className = 'note';
        note.textContent = on
            ? 'Пульт открыт для телефона. Наведите камеру на код или наберите адрес — при первом входе спросят код-пропуск.'
            : 'Пока пульт открыт на этом компьютере, его можно открыть и с телефона — по этому коду.';
    } else {
        note.className = 'fail';
        note.textContent = S.phoneError;
    }
    id('phone-start').hidden = on;
    id('phone-stop').hidden = !on;

    const qr = id('phone-qr');
    if (on && S.qr) {
        qr.src = S.qr;
        qr.hidden = false;
    } else {
        qr.removeAttribute('src');
        qr.hidden = true;
    }

    const details = id('phone-details');
    details.hidden = !on;
    if (on) {
        const url = phone.url || '';
        id('phone-url').textContent = url
            ? 'Адрес: ' + url
            : 'Адрес ещё не появился — cloudflared его выдаёт за несколько секунд.';
        id('phone-pin').textContent = phone.pin || 'не задан';
        if (!S.qr) {
            // Картинку кода отдаёт только ответ /api/phone: после перезагрузки
            // страницы её взять неоткуда, и вместо пустого места — адрес словами.
            id('phone-url').textContent += ' · код-картинка рисуется при включении, наберите адрес руками';
        }
    }
}

async function switchPhone(on) {
    const btn = id(on ? 'phone-start' : 'phone-stop');
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = on ? 'Поднимаем тоннель…' : 'Гасим…';
    S.phoneError = '';
    try {
        const r = await post('/api/phone', { on: on });
        S.state = S.state || {};
        S.state.phone = { on: !!r.on, url: r.url || '', pin: r.pin || '' };
        S.qr = r.qr || '';
    } catch (e) {
        // Нет cloudflared — это не падение пульта: кнопка остаётся, а строка
        // говорит, что поставить.
        S.phoneError = (e && e.message ? e.message : 'тоннель не поднялся');
    }
    btn.disabled = false;
    btn.textContent = label;
    renderMe();
}

async function logout() {
    const btn = id('me-out');
    btn.disabled = true;
    try {
        await post('/api/out', {});
    } catch (e) {
        // Даже если сервер не ответил, держать человека внутри бессмысленно:
        // перечитаем состояние и покажем то, что есть на самом деле.
    }
    btn.disabled = false;
    S.summary = box();
    S.promises = Object.assign(box(), { only: '' });
    S.report = Object.assign(box(), { preset: 'week', from: '', to: '' });
    S.people = { open: '', cards: {} };
    S.qr = '';
    S.date = '';
    await boot();
}

// ── Ворота: вход по ссылке и код-пропуск с телефона ─────────────────────────

// Тексты входа по ссылке уже написаны в разметке — снимаем их при старте,
// чтобы после режима ПИНа вернуть ровно их, а не свою копию.
const GATE = {
    title: '', note: '', place: '',
};

function rememberGateTexts() {
    GATE.title = id('gate-title').textContent;
    GATE.note = id('gate-note').textContent;
    GATE.place = id('gate-input').placeholder || '';
}

function showGate(mode) {
    S.gate = mode;
    id('app').hidden = true;
    id('gate').hidden = false;
    const input = id('gate-input');
    id('gate-fail').hidden = true;
    if (mode === 'pin') {
        id('gate-title').textContent = 'Код-пропуск';
        id('gate-note').textContent = 'Пульт открыт с телефона. Введите четыре цифры, ' +
            'которые видны на экране компьютера. Спросим один раз.';
        input.placeholder = '····';
        input.inputMode = 'numeric';
        input.setAttribute('maxlength', '8');
        // Четыре цифры набирают на телефоне, часто одной рукой и на ходу:
        // поле становится крупным и по центру, чтобы попадать не глядя.
        input.classList.add('field--pin');
        id('gate-go').textContent = 'Войти по коду';
    } else {
        id('gate-title').textContent = GATE.title;
        id('gate-note').textContent = GATE.note;
        input.placeholder = GATE.place;
        input.inputMode = 'text';
        input.removeAttribute('maxlength');
        input.classList.remove('field--pin');
        id('gate-go').textContent = 'Войти';
    }
    input.value = '';
}

function gateFail(text) {
    const node = id('gate-fail');
    node.textContent = text;
    node.hidden = !text;
}

async function gateSubmit() {
    const input = id('gate-input');
    const value = String(input.value || '').trim();
    if (!value) {
        gateFail(S.gate === 'pin'
            ? 'Введите код-пропуск — четыре цифры с экрана компьютера.'
            : 'Вставьте ссылку входа — ту, что выдал аналитик.');
        return;
    }
    const btn = id('gate-go');
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Проверяем…';
    gateFail('');
    try {
        if (S.gate === 'pin') await post('/api/pin', { pin: value });
        else await post('/api/link', { link: value });
        input.value = '';
        btn.disabled = false;
        btn.textContent = label;
        await boot();
        return;
    } catch (e) {
        gateFail(e && e.message ? e.message : 'не получилось войти');
    }
    btn.disabled = false;
    btn.textContent = label;
}

// ── Счётчик на рейке ────────────────────────────────────────────────────────

function renderRailDot() {
    const totals = (S.summary.data && S.summary.data.totals) || null;
    const n = totals ? Number(totals.overdue_promises) : NaN;
    const has = Number.isFinite(n) && n > 0;
    const words = has ? counted(n, 'просроченное обещание', 'просроченных обещания',
        'просроченных обещаний') : '';
    // Два места, одно число: на рейке ПК оно читается цифрой, а на кнопке
    // шириной в четверть телефона цифру не разобрать — там ui.css превращает
    // ту же плашку в точку. Поэтому текст ставим в обе, а разницу решает стиль.
    [id('rail-promises'), id('tab-promises')].forEach((dot) => {
        if (!dot) return;
        dot.hidden = !has;
        dot.textContent = has ? num(n) : '';
        if (has) dot.setAttribute('aria-label', words);
        else dot.removeAttribute('aria-label');
    });
}

// ── Старт ───────────────────────────────────────────────────────────────────

async function boot() {
    let st;
    try {
        st = await api('/api/state');
    } catch (e) {
        if (e && e.needPin) {
            showGate('pin');
            return;
        }
        showGate(S.gate);
        gateFail((e && e.message ? e.message : 'пульт не ответил') +
            '. Проверьте, что пульт запущен на компьютере, и обновите страницу.');
        return;
    }
    S.state = st;
    if (st.need_pin) {
        showGate('pin');
        return;
    }
    if (!st.ready) {
        showGate('link');
        return;
    }
    id('gate').hidden = true;
    id('app').hidden = false;
    renderMe();
    go(S.screen || 'today');
}

/** Светлая / тёмная / как в системе.
 *
 *  Выбор пишется в data-theme на <html> — оттуда его читает весь ui.css, — и
 *  запоминается в этом браузере. Именно в этом: пульт открывают и с ноутбука в
 *  офисе, и с телефона в машине вечером, и навязывать им одно оформление
 *  неправильно. Первичная установка делается инлайном в <head>, до отрисовки,
 *  чтобы тёмный пульт не мигал белым; здесь только переключение. */
function applyTheme(choice) {
    const value = (choice === 'light' || choice === 'dark') ? choice : 'auto';
    document.documentElement.dataset.theme = value;
    try { localStorage.setItem('hard-ruk-theme', value); } catch (e) { /* приватный режим */ }
    const group = id('me-theme');
    if (!group) return;
    group.querySelectorAll('button[data-theme]').forEach((btn) => {
        btn.setAttribute('aria-checked', btn.dataset.theme === value ? 'true' : 'false');
    });
}

function wire() {
    // Переходы: один обработчик на всю страницу — бабблы и строки рождаются
    // после загрузки данных, и вешать на каждую свой слушатель незачем.
    document.addEventListener('click', (ev) => {
        const target = ev.target && ev.target.closest ? ev.target.closest('[data-go]') : null;
        if (target && target.dataset && target.dataset.go) go(target.dataset.go);
    });

    id('gate-go').addEventListener('click', gateSubmit);
    id('gate-input').addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') gateSubmit();
    });
    id('report-xlsx').addEventListener('click', downloadBook);
    id('phone-start').addEventListener('click', () => switchPhone(true));
    id('phone-stop').addEventListener('click', () => switchPhone(false));
    id('me-out').addEventListener('click', logout);
    id('entry-copy').addEventListener('click', copyIssued);
    id('entry-hide').addEventListener('click', () => {
        S.entry.issued = null;
        renderIssued();
    });

    const theme = id('me-theme');
    if (theme) {
        theme.addEventListener('click', (ev) => {
            const btn = ev.target && ev.target.closest ? ev.target.closest('button[data-theme]') : null;
            if (btn) applyTheme(btn.dataset.theme);
        });
        let saved = null;
        try { saved = localStorage.getItem('hard-ruk-theme'); } catch (e) { saved = null; }
        applyTheme(saved);
    }

    // Вернулись к вкладке после совещания — данные старше пяти минут
    // перечитываем: смотреть на позавчерашнюю цифру как на сегодняшнюю опаснее,
    // чем подождать секунду.
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) return;
        const rec = S.screen === 'promises' ? S.promises
            : (S.screen === 'report' ? S.report : S.summary);
        if (rec && rec.at && (Date.now() - rec.at.getTime() > 5 * 60 * 1000)) {
            ensure(S.screen, true);
        }
    });
}

function start() {
    rememberGateTexts();
    wire();
    boot();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
} else {
    start();
}
