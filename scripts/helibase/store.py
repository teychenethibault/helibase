"""Accès à la base Supabase par son API REST, avec la clé secrète (jamais publiée)."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class SupabaseError(RuntimeError):
    pass


class Store:
    def __init__(self, url=None, key=None):
        self.url = (url or os.environ['SUPABASE_URL']).rstrip('/') + '/rest/v1/'
        key = key or os.environ['SUPABASE_SECRET_KEY']
        self.headers = {'apikey': key, 'Content-Type': 'application/json'}
        if key.startswith('eyJ'):      # ancienne clé « service_role » au format JWT
            self.headers['Authorization'] = 'Bearer ' + key

    def _call(self, method, path, params=None, body=None, prefer=None, extra_headers=None):
        url = self.url + path
        if params:
            url += '?' + urllib.parse.urlencode(params, safe=',.()*:')
        headers = dict(self.headers)
        if prefer:
            headers['Prefer'] = prefer
        if extra_headers:
            headers.update(extra_headers)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(4):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors='replace')
                if e.code >= 500 and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise SupabaseError(f'{method} {path} → {e.code} : {detail}') from None
            except urllib.error.URLError:
                if attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise

    def select(self, table, columns='*', **filters):
        """Lit toute une table (par pages de 1000 lignes)."""
        rows, offset = [], 0
        while True:
            params = {'select': columns, **filters}
            page = self._call('GET', table, params, extra_headers={'Range': f'{offset}-{offset + 999}'})
            rows.extend(page or [])
            if not page or len(page) < 1000:
                return rows
            offset += 1000

    def insert(self, table, rows, on_conflict=None):
        """Insère (ou met à jour si on_conflict) et renvoie les lignes écrites."""
        out = []
        for i in range(0, len(rows), 500):
            chunk = rows[i:i + 500]
            params = {'on_conflict': on_conflict} if on_conflict else None
            prefer = 'return=representation' + (',resolution=merge-duplicates' if on_conflict else '')
            out.extend(self._call('POST', table, params, chunk, prefer=prefer) or [])
        return out

    def update(self, table, values, **filters):
        return self._call('PATCH', table, filters, values, prefer='return=representation')
