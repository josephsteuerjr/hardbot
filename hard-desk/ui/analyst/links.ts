// Personal profile management replaces link issuance.
import { go } from '../kit/shell';
import { toast } from '../kit/dom';
export { showIssued } from '../kit/issued';
export async function issueLink(_person:unknown):Promise<void>{
  await go('accounts');toast('Сотрудник создаёт профиль в программе на своём ПК. Здесь доступны профили и устройства.');
}
