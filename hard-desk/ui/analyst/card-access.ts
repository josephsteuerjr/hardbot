import { api, hard } from '../kit/api';
import { setPersonTransport } from '../views/common';
import { adm } from './lib';

export function useAnalystCardTransport(): void {
  const read: typeof api = async (path, query, method = 'GET') => {
    if (method !== 'GET' || !['/api/manager/person', '/api/manager/photos'].includes(path))
      return {ok: false, status: 403, kind: 'forbidden', text: 'Недопустимый запрос карточки'};
    const qs = new URLSearchParams(Object.entries(query || {})
      .filter(([, value]) => value !== undefined && value !== null && value !== '')
      .map(([key, value]) => [key, String(value)])).toString();
    try {
      const data = await adm('GET', path.replace('/api/manager/', '/admin/control/') + (qs ? '?' + qs : ''));
      return {ok: true, status: 200, data};
    } catch (error) {
      return {ok: false, status: 0, kind: 'server', text: (error as Error).message};
    }
  };
  setPersonTransport(read, async (path, query) => {
    const kind = path === '/api/manager/photo' ? 'photo' : path === '/api/manager/receipt_photo' ? 'receipt_photo' : '';
    if (!kind || typeof query?.file !== 'string') return null;
    const result = await hard.local('/admin_image', {kind, file: query.file});
    return result.ok ? result.data?.src || null : null;
  });
}
