import asyncio
import base64
import hashlib
import json
import os
import re
import aiohttp

class CamofoxError(Exception):
    pass

class AsyncTab:

    def __init__(self, session, user, tab_id):
        self.s = session
        self.user = user
        self.id = tab_id

    async def _post(self, path, body):
        last = None
        for attempt in range(3):
            try:
                async with self.s.post(path, json=body, timeout=aiohttp.ClientTimeout(total=90)) as r:
                    d = await r.json()
                    if r.status >= 400:
                        raise CamofoxError(f'HTTP {r.status}: {str(d)[:200]}')
                    return d
            except (CamofoxError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = e
                await asyncio.sleep(1 + attempt)
        raise last

    async def _get(self, path):
        async with self.s.get(path, timeout=aiohttp.ClientTimeout(total=90)) as r:
            return await r.json()

    async def snapshot_raw(self):
        return await self._get(f'/tabs/{self.id}/snapshot?userId={self.user}')

    async def snapshot(self, tries=5, delay=3):
        for _ in range(tries):
            d = await self.snapshot_raw()
            s = d.get('snapshot') or ''
            if s:
                return s
            await asyncio.sleep(delay)
        raise CamofoxError(f'empty snapshot {self.id}')

    async def main_content(self):
        s = await self.snapshot()
        a, b = (s.find('Содержимое'), s.find('Блоки'))
        if a >= 0 < b:
            return s[a + 9:b]
        a, b = (s.find('Main content'), s.find('Blocks'))
        if a >= 0 < b:
            return s[a + 13:b]
        return s

    async def click(self, ref):
        return await self._post(f'/tabs/{self.id}/click', {'userId': self.user, 'ref': ref})

    async def type(self, ref, text):
        return await self._post(f'/tabs/{self.id}/type', {'userId': self.user, 'ref': ref, 'text': text})

    async def select(self, ref, option):
        return await self._post(f'/tabs/{self.id}/select', {'userId': self.user, 'ref': ref, 'option': option})

    async def navigate(self, url, settle=2):
        r = await self._post(f'/tabs/{self.id}/navigate', {'userId': self.user, 'url': url})
        await asyncio.sleep(settle)
        return r

    async def evaluate(self, expression):
        return await self._post(f'/tabs/{self.id}/evaluate', {'userId': self.user, 'expression': expression})

    async def url(self):
        d = await self.snapshot_raw()
        return d.get('url', '')

    async def close(self):
        async with self.s.delete(f'/tabs/{self.id}', params={'userId': self.user}) as r:
            return await r.json()

    async def js_click_label(self, labels):
        labs = json.dumps(labels if isinstance(labels, list) else [labels], ensure_ascii=False)
        r = await self.evaluate(f"(() => {{\n            const want = {labs};\n            let n = 0;\n            for (const w of want) {{\n                const el = [...document.querySelectorAll('input[type=checkbox],input[type=radio]')]\n                    .find(x => (x.parentElement.textContent || '').trim().toLowerCase()\n                        .includes(w.toLowerCase()));\n                if (el) {{ el.click(); n++; }}\n            }}\n            return String(n);\n        }})()")
        return r.get('result')

    async def js_click_next(self):
        await self.evaluate("document.querySelector('input[name=next]').click(); 'ok'")

    async def click_answer(self, idx):
        for _ in range(3):
            try:
                r = await self.evaluate(f"(() => {{ const ins = document.querySelectorAll('.answer input[type=radio], .answer input[type=checkbox]'); if (!ins[{idx}]) return 'nobtn'; if (ins[{idx}].type === 'radio' && ins[{idx}].checked) return 'ok'; ins[{idx}].click(); return ins[{idx}].checked ? 'ok' : 'fail'; }})()")
                if r.get('result') in ('ok', 'nobtn'):
                    return r.get('result')
            except CamofoxError:
                pass
        return 'fail'

    async def next_page(self):
        u0 = await self.url()
        h0 = await self.evaluate("(() => { const q = document.querySelector('.qtext'); return q ? q.innerText.trim().slice(0, 80) : ''; })()")
        h0 = (h0.get('result') or '')
        await self.evaluate("(() => { const n = document.querySelector('input[name=next]') || document.querySelector('input[name=finishattempt]'); if (n) n.click(); return n ? 'ok' : 'nonext'; })()")
        for _ in range(12):
            await asyncio.sleep(1)
            u = await self.url()
            h = await self.evaluate("(() => { const q = document.querySelector('.qtext'); return q ? q.innerText.trim().slice(0, 80) : ''; })()")
            h = (h.get('result') or '')
            if u != u0 and '[object' not in u:
                return u
            if h and h != h0 and u == u0 and '[object' not in u:
                return u
            if 'attempt.php' in u and 'page=' in u and not h:
                await self.evaluate("(() => { const s = document.querySelector('form.quizcontinue input[type=submit], form button[type=submit], form input[type=submit]:not([name=finishattempt])'); if (s) { s.click(); return 'resub'; } return 'noresub'; })()")
        return u0

    async def qtext(self):
        r = await self.evaluate("document.querySelector('.qtext') ? document.querySelector('.qtext').innerText.trim() : ''")
        return r.get('result') or ''

    async def opt_labels(self):
        r = await self.evaluate("[...document.querySelectorAll('.answer input[type=radio]')].map(e => e.parentElement.textContent.trim())")
        return r.get('result') or []

    async def opts_b64(self, sel='.answer img'):
        js = f"(async () => {{\n            const out = [];\n            for (const im of document.querySelectorAll('{sel}')) {{\n                await new Promise(r => im.complete ? r() : im.onload = r);\n                const c = document.createElement('canvas');\n                c.width = im.naturalWidth; c.height = im.naturalHeight;\n                c.getContext('2d').drawImage(im, 0, 0);\n                out.push(c.toDataURL('image/png'));\n            }}\n            return JSON.stringify(out);\n        }})()"
        r = await self.evaluate(js)
        d = r.get('result') or '[]'
        try:
            arr = json.loads(d) if isinstance(d, str) else d
            return [x.split(',', 1)[1] for x in arr if ',' in x]
        except Exception:
            return []

    async def imgs_count(self):
        r = await self.evaluate("document.querySelectorAll('.answer img').length")
        return r.get('result')

class AsyncMoodle:

    def __init__(self, site, username=None, password=None, user=None, api_url='http://localhost:9377', dumps_dir='moodlesolver_dumps'):
        self.username = username
        self.password = password
        self.site = site.rstrip('/')
        self.api_url = api_url.rstrip('/')
        self.user = user or 'moodle_' + hashlib.sha1((username or 'anon').encode()).hexdigest()[:8]
        self.dumps_dir = dumps_dir
        self.tab = None
        self.s = None
        self._connected = False

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    @property
    def is_connected(self):
        return self._connected

    async def connect(self):
        if not self._connected:
            self.s = aiohttp.ClientSession(base_url=self.api_url)
            self._connected = True
        return self

    async def start(self):
        if not self._connected:
            await self.connect()
        if self.username and self.password:
            return await self.login(self.username, self.password)
        return await self.whoami()

    async def disconnect(self):
        if self.s:
            for t in await self.tabs():
                await AsyncTab(self.s, self.user, t['tabId']).close()
            await self.s.close()
        self.s = None
        self._connected = False

    async def login(self, username, password):
        t = await self.pick('login/index.php') or await self.new_tab(f'{self.site}/login/index.php')
        s = await t.main_content()
        if 'Вы уже вошли' in s or 'Вы зашли' in s or 'Logged in' in s:
            return await self.whoami()
        m_u = re.search('(?:textbox|field) "([^"]*(?:Username|Логин|Пользователь)[^"]*)"\\s*\\[e(\\d+)\\]', s)
        m_p = re.search('(?:textbox|field) "([^"]*(?:Password|Пароль)[^"]*)"\\s*\\[e(\\d+)\\]', s)
        m_b = re.search('button "([^"]*(?:Log in|Вход|Войти)[^"]*)"\\s*\\[e(\\d+)\\]', s)
        if not (m_u and m_p and m_b):
            raise CamofoxError(f'login form not parsed: {s[:300]}')
        await t.type(f'e{m_u.group(2)}', username)
        await t.type(f'e{m_p.group(2)}', password)
        await t.click(f'e{m_b.group(2)}')
        await asyncio.sleep(4)
        return await self.whoami()

    async def whoami(self):
        t = await self.pick('my/') or await self.new_tab(f'{self.site}/my/')
        r = await t.evaluate("JSON.stringify(document.querySelector('.usertext')?.textContent || null)")
        u = r.get('result')
        if u and u != 'null':
            return u.strip('"')
        s = await t.main_content()
        m = re.search('Вы зашли под именем\\s*\\n?\\s*- link "([^"]+)"', s)
        if m:
            return m.group(1)
        m = re.search('You are logged in as\\s*\\n?\\s*- link "([^"]+)"', s)
        return m.group(1) if m else None

    async def start_attempt(self, cmid):
        u = f'{self.site}/mod/quiz/view.php?id={cmid}'
        t = self.tab or await self.new_tab(u)
        await asyncio.sleep(3)
        r = await t.evaluate("document.querySelector('input[name=sesskey]') ? document.querySelector('input[name=sesskey]').value : ''")
        sk = r.get('result') or ''
        if not sk:
            return (None, 'nosesskey')
        r = await t.evaluate(f"(async () => {{\n            const fd = new FormData();\n            fd.append('cmid', '{cmid}');\n            fd.append('sesskey', '{sk}');\n            const resp = await fetch('{self.site}/mod/quiz/startattempt.php',\n                {{method: 'POST', body: fd, credentials: 'include',\n                  redirect: 'follow'}});\n            return 'status:' + resp.status + ' final:' + resp.url;\n        }})()")
        await asyncio.sleep(3)
        await t.navigate(u)
        await asyncio.sleep(3)
        r = await t.evaluate("document.body.innerText.includes('Продолжить последнюю попытку') || document.querySelector('.que') ? 'attempt-live' : 'check'")
        st = r.get('result')
        attempt_url = f'{self.site}/mod/quiz/attempt.php'
        for tb in await self.tabs():
            if 'attempt.php' in tb.get('url', ''):
                attempt_url = tb['url']
                break
        return (attempt_url, st)

    async def tabs(self):
        async with self.s.get('/tabs', params={'userId': self.user}) as r:
            return (await r.json()).get('tabs', [])

    async def new_tab(self, url, settle=3, retries=3):
        last = None
        for _ in range(retries):
            try:
                async with self.s.post('/tabs', json={
                        'userId': self.user, 'sessionKey': 's',
                        'url': url}) as r:
                    d = await r.json()
                t = AsyncTab(self.s, self.user, d['tabId'])
            except (KeyError, CamofoxError) as e:
                last = e
                try:
                    await self.close_stale()
                except Exception:
                    pass
                await asyncio.sleep(5)
                continue
            await asyncio.sleep(settle)
        self.tab = t
        return t

    async def pick(self, url_substr, latest=True):
        ts = [t for t in await self.tabs() if url_substr in t.get('url', '')]
        if not ts:
            return None
        t = AsyncTab(self.s, self.user, ts[-1]['tabId'] if latest else ts[0]['tabId'])
        self.tab = t
        return t

    async def close_stale(self, keep=''):
        for t in await self.tabs():
            if keep and keep in t.get('url', ''):
                continue
            try:
                await AsyncTab(self.s, self.user,
                               t['tabId']).close()
            except Exception:
                pass

    async def current_page(self, tab=None):
        from .parse import parse_page
        return parse_page(await (tab or self.tab).main_content())

    async def download_image(self, path='/tmp/moodle_img.jpg', tab=None):
        t = tab or self.tab
        d = (await t.evaluate("(async () => {\n            const img = document.querySelector('.qtext img, .content img');\n            if (!img) return 'noimg';\n            await new Promise(r => img.complete ? r() : img.onload = r);\n            const c = document.createElement('canvas');\n            c.width = img.naturalWidth; c.height = img.naturalHeight;\n            c.getContext('2d').drawImage(img, 0, 0);\n            return c.toDataURL('image/jpeg', 0.9);\n        })()")).get('result')
        if not isinstance(d, str) or ',' not in d:
            return None
        open(path, 'wb').write(base64.b64decode(d.split(',')[1]))
        return path

    async def download_all_images(self, prefix='/tmp/moodle_img', tab=None):
        t = tab or self.tab
        d = (await t.evaluate("(async () => {\n            const imgs = [...document.querySelectorAll('.qtext img, .content img')];\n            const out = [];\n            for (const img of imgs) {\n                await new Promise(r => img.complete ? r() : img.onload = r);\n                const c = document.createElement('canvas');\n                c.width = img.naturalWidth; c.height = img.naturalHeight;\n                c.getContext('2d').drawImage(img, 0, 0);\n                out.push(c.toDataURL('image/jpeg', 0.9));\n            }\n            return JSON.stringify(out);\n        })()")).get('result')
        try:
            arr = json.loads(d or '[]')
        except Exception:
            return []
        paths = []
        for i, data in enumerate(arr):
            if isinstance(data, str) and ',' in data:
                p = f'{prefix}_{i}.jpg'
                open(p, 'wb').write(base64.b64decode(data.split(',')[1]))
                paths.append(p)
        return paths

    async def answer_text(self, text, tab=None):
        t = tab or self.tab
        r = await t.evaluate(f"(() => {{\n            const el = [...document.querySelectorAll('.qtext ~ * input[type=text], input[type=text]')]\n                .filter(x => !x.name.includes('sesskey'))[0];\n            if (!el) return 'notext';\n            el.focus();\n            el.value = {json.dumps(text, ensure_ascii=False)};\n            el.dispatchEvent(new Event('input', {{bubbles: true}}));\n            el.dispatchEvent(new Event('change', {{bubbles: true}}));\n            return el.value;\n        }})()")
        return r.get('result')

    async def answer_match(self, pairs, tab=None):
        t = tab or self.tab
        r = await t.evaluate(f"(() => {{\n            const pairs = {json.dumps(pairs, ensure_ascii=False)};\n            let n = 0;\n            for (const p of pairs) {{\n                const row = [...document.querySelectorAll('tr, .answer li')]\n                    .find(x => (x.textContent || '').includes(p[0]));\n                if (!row) continue;\n                const sel = row.querySelector('select');\n                if (!sel) continue;\n                const opt = [...sel.options].find(o => o.textContent.trim() === p[1]);\n                if (opt) {{ sel.value = opt.value;\n                    sel.dispatchEvent(new Event('change', {{bubbles: true}})); n++; }}\n            }}\n            return String(n);\n        }})()")
        return r.get('result')

    def dump(self, name, content):
        os.makedirs(self.dumps_dir, exist_ok=True)
        p = f'{self.dumps_dir}/{name}'
        open(p, 'w').write(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=1))
        return p

    async def dump_question(self, q, quiz_id):
        rec = {'n': q['number'], 'text': q['text'], 'options': [o['label'] for o in q['options']], 'multi': q['multi'], 'timer': q['timer']}
        p = await self.download_image('%s/q%s_n%s.jpg' % (self.dumps_dir, quiz_id, q['number']))
        if p:
            rec['image'] = p
        self.dump("q%s_n%s.json" % (quiz_id, q['number']), rec)
        return rec

    async def answer_step(self, labels, tab=None):
        t = tab or self.tab
        r = await t.js_click_label(labels)
        await asyncio.sleep(0.7)
        await t.js_click_next()
        await asyncio.sleep(2.4)
        return (r, await self.current_page())

    async def step_quiz(self, quiz_id, bank, noans=None, on_question=None):
        noans = noans if noans is not None else []
        prev = None
        while True:
            q = await self.current_page()
            if q.get('number') == prev:
                await asyncio.sleep(2)
                continue
            prev = q.get('number')
            if not q['text'] and (not q['options']) and (not q.get('selects')):
                return noans
            rec = await self.dump_question(q, quiz_id)
            ans = None
            for k, v in bank.items():
                if k.lower() in (q['text'] or '').lower():
                    ans = v
                    break
            if on_question:
                ans = await on_question(q, ans, rec)
            if ans:
                labs = ans if isinstance(ans, list) else [ans]
                r, nxt = await self.answer_step(labs)
                if not nxt['next_ref'] and nxt['text'] is None:
                    return noans
            else:
                noans.append(rec)
                if not q['next_ref']:
                    return noans
                await self.tab.js_click_next()
                await asyncio.sleep(2.4)

    async def resume_attempt(self, quiz_id):
        t = await self.pick(f'view.php?id={quiz_id}') or await self.new_tab(f'{self.site}/mod/quiz/view.php?id={quiz_id}')
        self.tab = t
        r = await t.evaluate("(async () => { const b = [...document.querySelectorAll('button, input[type=submit]')].find(x => (x.textContent || x.value || '').includes('Продолжить')); if (b) { b.closest('form').submit(); return 'sub'; } return 'nobtn'; })()")
        if r.get('result') == 'sub':
            await asyncio.sleep(6)
        at = await self.pick('attempt.php')
        if at:
            self.tab = at
            return at.id
        return None

    async def finish_attempt(self):
        q = await self.current_page()
        if q.get('next_label') and ('Закончить' in q['next_label'] or 'Finish' in q['next_label']):
            await self.tab.js_click_next()
            await asyncio.sleep(3)
        t = await self.pick('summary.php')
        if t:
            self.tab = t
        s = await self.tab.main_content()
        m = re.search('button "Отправить всё и завершить[^"]*"[^\\[]*\\[e(\\d+)\\]', s)
        if m:
            await self.tab.click(f'e{m.group(1)}')
            await asyncio.sleep(3)
            s = await self.tab.main_content()
            m = re.search('button "Отправить всё и завершить тест"[^\\[]*\\[e(\\d+)\\]', s)
            if m:
                await self.tab.click(f'e{m.group(1)}')
                await asyncio.sleep(6)

    async def quiz_result(self, quiz_id):
        v = await self.pick(f'view.php?id={quiz_id}') or await self.new_tab(f'{self.site}/mod/quiz/view.php?id={quiz_id}')
        await asyncio.sleep(2)
        s = await v.main_content()
        self.dump(f'result_{quiz_id}.txt', s)
        sc = re.search('итоговая оценка за этот тест:\\s*([\\d,]+)/([\\d,]+)', s)
        if sc:
            return sc.group(1) + '/' + sc.group(2)
        return re.findall('- row "([^"]+Отправлено[^"]*)"', s)

    async def review(self, attempt_id, cmid, showall=True):
        u = f'{self.site}/mod/quiz/review.php?attempt={attempt_id}&cmid={cmid}'
        if showall:
            u += '&showall=1'
        t = self.tab or await self.new_tab(u)
        await t.navigate(u)
        await asyncio.sleep(3)
        s = await t.snapshot(tries=2)
        self.dump(f'review_{attempt_id}.txt', s)
        a = s.find('Содержимое')
        return s[a:s.find('Блоки')] if a >= 0 else s

    async def ensure_login(self, username=None, password=None):
        u = username or self.username
        p = password or self.password
        try:
            t = await self.pick('my/')
            if t:
                url = await t.url()
                if 'login' not in str(url) and '[object' not in str(url):
                    return {'ok': True, 'relogin': False}
        except Exception:
            pass
        if not (u and p):
            return {'error': 'no credentials for relogin'}
        try:
            await self.login(u, p)
            return {'ok': True, 'relogin': True}
        except Exception as e:
            return {'error': f'relogin failed: {str(e)[:120]}'}

    async def open_session(self, username, password, quiz_id=None):
        self.username = username
        self.password = password
        await self.start()
        await self.close_stale()
        await self.login(username, password)
        if quiz_id:
            t = await self.new_tab(f'{self.site}/mod/quiz/view.php?id={quiz_id}')
            self.tab = t
        return self

    async def events(self):
        at = await self.pick('attempt.php') or await self.pick('summary.php')
        if not at:
            return {'test': None, 'question': None, 'answer': None}
        u = await at.url()
        m = re.search('attempt=(\\d+)', u)
        ev = {'test': m.group(1) if m else None, 'question': None, 'answer': None}
        r = await at.evaluate("(() => { const q = document.querySelector('.qtext'); const c = document.querySelector('.answer input:checked'); return JSON.stringify({q: q ? q.innerText.trim().slice(0, 200) : null, a: c ? c.parentElement.textContent.trim() : null}); })()")
        d = json.loads(r.get('result') or '{}')
        ev['question'] = d.get('q')
        ev['answer'] = d.get('a')
        return ev
