// Сцены дизайн-студии: какие экраны снимать и как до них дойти. Роли — по
// дев-стенду server/pult_dev.py: 1001 выездник, 222 руководитель, 333
// безопасник, 444 директор («full»), 555 владелец (стенд с ADMIN_ID=555).
// Переходы — через функции самой оболочки, а не координаты: сцена не должна
// ломаться от того, что кнопка сдвинулась. Окно статистики — «7 дней»:
// синтетика стенда ночью даёт пустое «сегодня».
const AGENT = 1001, LEAD = 222, SEC = 333, DIR = 444, OWNER = 555;
const week = async (d) => { await d.js("dgPreset('7d')"); await d.settle(); };

module.exports = [
  { name: "agent-list", run: async (d) => { await d.open(AGENT); await d.shot("agent-list"); } },
  { name: "agent-card", run: async (d) => {
    await d.open(AGENT);
    await d.js("document.querySelector('.pcard[onclick]')?.click()"); await d.settle(900);
    await d.shot("agent-card");
    await d.js("[...document.querySelectorAll('.tabs button')].pop()?.click()"); await d.settle();
    await d.shot("agent-form");
  } },
  { name: "agent-marks", run: async (d) => { await d.open(AGENT); await d.js("renderMarks()"); await d.settle(); await d.shot("agent-marks", { long: false }); } },
  // Руководитель: при запуске — его режим, «Сводка».
  { name: "lead-digest", run: async (d) => {
    await d.open(LEAD); await d.shot("lead-digest", { long: false });
    await week(d); await d.shot("lead-digest-7d");
    await d.js("document.querySelector('details.fold')?.setAttribute('open','')"); await d.settle();
    await d.shot("lead-funnel", { long: false });
  } },
  { name: "lead-team", run: async (d) => {
    await d.open(LEAD); await d.js("renderTeam()"); await d.settle(); await week(d);
    await d.shot("lead-team");
    await d.js("document.querySelector('.stwrap').scrollLeft=400"); await d.settle(200);
    await d.shot("lead-team-scrolled", { long: false });
    await d.js("dgFilterPick()"); await d.settle(200);
    await d.shot("lead-filter", { long: false });
    await d.js("closePick(); dgPickEmp(0)"); await d.settle();
    await d.shot("lead-emp");
  } },
  { name: "lead-field", run: async (d) => {
    await d.open(LEAD); await d.js("setMode('field')"); await d.settle();
    await d.shot("lead-field", { long: false });
    await d.js("openPortfolioPick()"); await d.settle(200);
    await d.shot("lead-portfolio-pick", { long: false });
  } },
  { name: "lead-screens", run: async (d) => {
    await d.open(LEAD); await week(d);
    for (const [v, js] of [["workload", "dgGo('workload',{wemp:null})"], ["plan", "dgGo('plan')"],
      ["binds", "dgGo('binds')"], ["links", "dgGo('team_links')"], ["promises", "dgOpenPromises()"],
      ["cases", "dgCases('visits')"], ["photos", "dgGo('photos')"]]) {
      await d.js(js); await d.settle(); await d.shot("lead-" + v, { long: false });
    }
  } },
  { name: "sec-digest", run: async (d) => { await d.open(SEC); await week(d); await d.shot("sec-digest", { long: false }); } },
  { name: "dir-digest", run: async (d) => { await d.open(DIR); await week(d); await d.shot("dir-digest", { long: false }); } },
  { name: "owner", run: async (d) => {
    await d.open(OWNER); await week(d); await d.shot("owner-digest", { long: false });
    await d.js("setMode('field')"); await d.settle(); await d.shot("owner-field", { long: false });
  } },
];
