const roles: Record<string,string> = {
  employee:'Сотрудник', manager:'Руководитель', analyst:'Аналитик',
  courier:'Курьер', logistic:'Логист', observer:'Наблюдатель', owner:'Владелец',
};
const territories: Record<string,string> = {
  self:'Личная область', employees:'Назначенные сотрудники', team:'Команда', all:'Вся область',
};

export function grantLabel(grant: {role:string; territory:string}) {
  return `${roles[grant.role] || grant.role} · ${territories[grant.territory] || grant.territory}`;
}
