"""Territory checks around legacy manager/admin operations."""
from .contract import CRMError


class AdminScope:
    def __init__(self,session):
        context=session.get('context') or {}
        self.actor_uid=context.get('actor_uid')
        staff=list(context.get('staff') or [])
        self.names={str(person.get('name','')).strip().casefold() for person in staff}
        self.uids={int(actor) for person in staff for actor in person.get('actor_uids',())}
        if not self.names and context.get('employee_uuid'):
            self.uids.add(int(self.actor_uid))

    def employee(self,name):
        return str(name or '').strip().casefold() in self.names

    def account(self,account):
        if not isinstance(account,dict):return False
        actor=account.get('actor_uid',account.get('telegram_id'))
        if actor is not None and self.uids:
            try:return int(actor) in self.uids
            except (TypeError,ValueError):return False
        return self.employee(account.get('employee'))

    def filter_team(self,result):
        if not isinstance(result,dict):return result
        result=dict(result)
        result['employees']=[dict(row) for row in result.get('employees',())
                             if self.employee(row.get('employee')) and (row.get('account') is None or self.account(row['account']))]
        return result

    def filter_plan(self,result):
        if not isinstance(result,dict):return result
        result=dict(result)
        result['employees']=[dict(row) for row in result.get('employees',()) if self.employee(row.get('employee'))]
        return result

    def require_employee(self,name):
        if not self.employee(name):
            raise CRMError('Сотрудник вне доступной территории.','forbidden',403)

    def require_username(self,team,username):
        team=self.filter_team(team)
        normalized=str(username or '').strip().casefold()
        if not any(str((row.get('account') or {}).get('username','')).casefold()==normalized
                   for row in team.get('employees',())):
            raise CRMError('Аккаунт вне доступной территории.','forbidden',403)

    def require_device(self,team,device_id):
        team=self.filter_team(team)
        if type(device_id) is not int:
            raise CRMError('Некорректное устройство.','forbidden',403)
        if not any(device.get('id')==device_id for row in team.get('employees',())
                   for device in (row.get('account') or {}).get('devices',())):
            raise CRMError('Устройство вне доступной территории.','forbidden',403)


def scoped(session):
    context=session.get('context') or {}
    if context.get('access_model',session.get('access_model',1))!=2 or context.get('owner'):
        return None
    return AdminScope(session)


def require_safe_admin(session,path,body):
    if scoped(session) is None:return
    if path in {'/state','/busy'}:return
    if path=='/admin' and body.get('method')=='GET' and body.get('path')=='/admin/accounts.json':return
    raise CRMError('Для этой полномочия доступны только чтение состояния и список аккаунтов в его территории.','forbidden',403)


def filter_safe_admin_result(session,path,body,result):
    if path!='/admin' or body.get('method')!='GET' or body.get('path')!='/admin/accounts.json':
        return result
    scope=scoped(session)
    if scope is None or not isinstance(result,dict) or not result.get('ok'):
        return result
    data=result.get('data')
    if not isinstance(data,dict) or not isinstance(data.get('accounts'),list):
        return dict(ok=False,error='Служба входа вернула неполный список аккаунтов.')
    accounts=[account for account in data['accounts'] if isinstance(account,dict)
              and type(account.get('actor_uid')) is int and account['actor_uid'] in scope.uids]
    return dict(result,data=dict(data,accounts=accounts,approver='',password_review_owner=False))
