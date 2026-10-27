import asyncio
import json
import re

class QuizAPI:

    def __init__(self, moodle):
        self.m = moodle

    async def _tab(self):
        cur = self.m.tab
        if cur is not None:
            try:
                u = await cur.url()
                if 'summary.php' in str(u):
                    self.m.tab = cur
                    return cur
            except Exception:
                pass
        t = await self.m.pick('summary.php')
        if t:
            self.m.tab = t
            return t
        try:
            from .client import AsyncTab
            for tab in await self.m.tabs():
                try:
                    tt = AsyncTab(self.m.s, self.m.user, tab['tabId'])
                    u = str(await tt.url())
                    if 'summary.php' in u:
                        self.m.tab = tt
                        return tt
                except Exception:
                    continue
        except Exception:
            pass
        if cur is not None:
            try:
                u = await cur.url()
                if 'attempt.php' in str(u):
                    self.m.tab = cur
                    return cur
            except Exception:
                pass
        t = await self.m.pick('attempt.php')
        if not t:
            t = await self.m.pick('view.php?id=')
        if t:
            self.m.tab = t
        return t or self.m.tab

    async def get_quiz_info(self, quiz_id):
        t = await self.m.pick(f'view.php?id={quiz_id}') or await self.m.new_tab(f'{self.m.site}/mod/quiz/view.php?id={quiz_id}')
        self.m.tab = t
        s = await t.main_content()
        info = {'quiz_id': quiz_id, 'attempts_left': None, 'timer': None, 'attempt_id': None}
        m = re.search('Разрешено попыток:\\s*(\\d+)', s)
        if m:
            info['attempts_left'] = m.group(1)
        m = re.search('Ограничение по времени:\\s*([^\\n]+)', s)
        if m:
            info['timer'] = m.group(1).strip()
        m = re.search('attempt=(\\d+)', s)
        if m:
            info['attempt_id'] = m.group(1)
        info['can_start'] = 'Начать тестирование' in s
        info['can_continue'] = 'Продолжить текущую попытку' in s
        info['available'] = 'недоступен' not in s.lower()
        return info

    async def start_attempt(self, quiz_id):
        from .dbglog import dbg
        t = await self.m.pick(f'view.php?id={quiz_id}') or \
            await self.m.new_tab(f'{self.m.site}/mod/quiz/view.php?id={quiz_id}')
        self.m.tab = t
        s = await t.main_content()
        if 'Продолжить текущую попытку' in s:
            dbg('start %d: resume own attempt' % quiz_id)
            at = await self.m.resume_attempt(quiz_id)
            if at:
                return at
        dbg('start %d: click startattempt' % quiz_id)
        r = await t.evaluate("var fs = Array.from(document.querySelectorAll('form')).filter(f => f.action.includes('startattempt'));fs.length ? (fs[fs.length-1].submit(), 'sub') : 'nof'")
        if r.get('result') == 'sub':
            await asyncio.sleep(5)
            at = await self.m.pick('attempt.php')
            if at:
                self.m.tab = at
                return (await self.m.events()).get('test')
        return None

    async def get_state(self):
        t = await self._find_tab('attempt.php') or await self._tab()
        if not t:
            return {'error': 'no tab'}
        tl = await t.evaluate("(() => { const e = document.querySelector('#quiz-timer, .quiztimer, [class*=timeleft]'); if (e) return e.textContent.trim(); const m = document.body.innerText.match(/Осталось[:\\s]*(\\d+)[^\\d]{0,3}(\\d*)/); return m ? m[0] : null; })()")
        timeleft = tl.get('result')
        r = await t.evaluate("(async () => {\n    const q = document.querySelector('.qtext');\n    const inputs = [...document.querySelectorAll('.answer input')];\n    const kinds = inputs.map(e => e.type);\n    const labels = inputs.map(e => e.parentElement.textContent.trim());\n    const checked = inputs.map(e => e.checked);\n    const btns = [...document.querySelectorAll('form button[type=submit], form input[type=submit]')];\n    const texts = [...document.querySelectorAll('.answer input[type=text], .answer textarea, .qtype_numerical input[type=text], .que input[type=text], .answer input:not([type=radio]):not([type=checkbox]):not([type=submit]):not([type=hidden])')];\n    const imgs = [...document.querySelectorAll('.answer img')];\n    const qi = document.querySelector('.qtext img');\n    const grab = async im => { await new Promise(rr => im.complete ? rr() : im.onload = rr); const c = document.createElement('canvas'); c.width = im.naturalWidth; c.height = im.naturalHeight; c.getContext('2d').drawImage(im, 0, 0); return c.toDataURL('image/png').split(',')[1]; };\n    let qimg = null;\n    if (qi) qimg = await grab(qi);\n    const optimgs = [];\n    for (const im of imgs) optimgs.push(await grab(im));\n    const msel = [...document.querySelectorAll('.que select, .answer select')].filter(s => !s.closest('.othernav, #quiznav, .jumps-menu, nav')).map(s => ({options: [...s.options].map(o => o.textContent.trim()), value: s.options[s.selectedIndex] ? s.options[s.selectedIndex].textContent.trim() : ''}));\n    const dd = [...document.querySelectorAll('.que .draghome, .que .drag, table.match .option, .que .droppreview')].map(e => e.textContent.trim()).filter(Boolean);\n    return JSON.stringify({\n        question: q ? q.innerText.trim() : null,\n        question_image: qimg ? 1 : null,\n        option_kinds: kinds, option_labels: labels,\n        checked: checked,\n        selects: [...document.querySelectorAll('select')].filter(s => !s.closest('.othernav, #quiznav, .jumps-menu, nav')).map(s => ({options: [...s.options].map(o => o.textContent.trim()), value: s.options[s.selectedIndex].textContent.trim()})),\n        text_inputs: texts.map(e => e.value),\n        text_count: texts.length,\n        match_selects: msel.length ? msel : null,\n        dragdrop: dd.length ? dd : null,\n        buttons: btns.map(b => (b.name || '') + '=' + b.value),\n        url: location.href});\n})()")
        st = json.loads(r.get('result') or '{}')
        if timeleft:
            st['timeleft'] = timeleft
        if st.get('question_image'):
            arr = await t.opts_b64('.qtext img')
            if arr:
                self.m.dump('q_image.b64', arr[0])
                st['question_image_file'] = 'q_image.b64'
                st['question_image_b64'] = arr[0]
        arr2 = await t.opts_b64()
        for i, b in enumerate(arr2):
            self.m.dump(f'opt_image_{i}.b64', b)
            st.setdefault('option_image_files', []).append(f'opt_image_{i}.b64')
        if arr2:
            st['option_images_b64'] = arr2
        return st

    async def set_answer(self, label=None, index=None, labels=None, checked=None):
        from .dbglog import dbg
        t = await self._tab()
        wants = labels if labels else [label] if label else []
        dbg('answer: wants=%r index=%r' % (wants[:3], index))
        if wants:
            set_mark = 'true' if checked is not False else 'false'
            r = await t.evaluate("(async () => { const ins = [...document.querySelectorAll('.answer input[type=radio], .answer input[type=checkbox]')]; const wants = " + json.dumps([w.lower() for w in wants]) + "; const setTo = " + set_mark + "; let n = 0; const norm = s => (s || '').replace(/[\\s\\xa0\\u200b\\u200c]+/g, ' ') .trim().toLowerCase(); for (const w of wants) {  const wn = norm(w);  let e = ins.find(x => norm(x.parentElement.textContent) === wn);  if (!e) e = ins.find(x => norm(x.parentElement.textContent)   .startsWith(wn.slice(0, 25)));  if (e) { if (e.type === 'checkbox' && e.checked !== setTo) { e.click(); } else if (e.type === 'radio' && !e.checked) { e.click(); } if (e.type === 'radio' || e.checked === setTo) n++; } } return JSON.stringify({clicked: n}); })()")
            return json.loads(r.get('result') or '{}')
        if index is not None:
            set_mark = 'true' if checked is not False else 'false'
            r2 = await t.evaluate(f"(() => {{ const ins = document.querySelectorAll('.answer input[type=radio], .answer input[type=checkbox]'); if (!ins[{index}]) return 'nobtn'; const e = ins[{index}]; if (e.type === 'checkbox') {{ const want = {set_mark}; if (e.checked !== want) e.click(); return e.checked === want ? 'ok' : 'fail'; }} if (e.checked) return 'ok'; e.click(); return e.checked ? 'ok' : 'fail'; }})()")
            return {'clicked': 1 if (r2.get('result') if isinstance(r2, dict) else r2) == 'ok' else 0}
        return {'clicked': 0}

    async def set_text(self, text, index=0):
        t = await self._find_tab('attempt.php') or await self._tab()
        js = "(() => { const ts = document.querySelectorAll('.answer input[type=text], .answer textarea, .qtype_numerical input[type=text], .que input[type=text], .answer input:not([type=radio]):not([type=checkbox]):not([type=submit]):not([type=hidden])'); if (!ts[IDX]) return 'noinput'; const e = ts[IDX]; const v = VAL; e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); e.dispatchEvent(new Event('blur', {bubbles: true})); return e.value === v ? 'set' : 'mismatch'; })()"
        js = js.replace('IDX', str(index)).replace('VAL', json.dumps(text))
        r = await t.evaluate(js)
        return r.get('result')

    async def set_select(self, value, index=0):
        t = await self._find_tab('attempt.php') or await self._tab()
        r = await t.evaluate("(async () => { const ss = document.querySelectorAll('select'); if (!ss[" + str(index) + "]) return 'nosel'; const want = " + json.dumps(value) + '; const o = [...ss[' + str(index) + "].options].find(x => x.textContent.trim().toLowerCase() === want.toLowerCase()); if (!o) return 'noopt'; ss[" + str(index) + '].value = o.value; ss[' + str(index) + "].dispatchEvent(new Event('change', {bubbles: true})); return 'set'; })()")
        return r.get('result')

    async def next_page(self):
        t = await self._tab()
        old_id = t.id
        u = await t.next_page()
        if 'summary.php' in str(u):
            from .client import AsyncTab
            nu = None
            try:
                nu = await self.m.pick('summary.php')
            except Exception:
                nu = None
            if nu is None or nu.id == old_id:
                try:
                    for tab in await self.m.tabs():
                        try:
                            tt = AsyncTab(self.m.s, self.m.user, tab['tabId'])
                            uu = str(await tt.url())
                            if 'summary.php' in uu and tt.id != old_id:
                                nu = tt
                                break
                            if 'summary.php' in uu and tt.id == old_id:
                                nu = tt
                        except Exception:
                            continue
                except Exception:
                    pass
            if nu and nu.id != old_id:
                try:
                    await AsyncTab(self.m.s, self.m.user, old_id).close()
                except Exception:
                    pass
            self.m.tab = nu or self.m.tab
        return u

    async def get_options(self):
        st = await self.get_state()
        return {'labels': st.get('option_labels'), 'kinds': st.get('option_kinds'), 'selects': st.get('selects'), 'text_inputs': st.get('text_inputs')}

    async def _find_tab(self, substr):
        from .client import AsyncTab
        cur = self.m.tab
        if cur is not None:
            try:
                if substr in str(await cur.url()):
                    return cur
            except Exception:
                pass
        try:
            ts = [t for t in await self.m.tabs() if substr in t.get('url', '')]
            if ts:
                return AsyncTab(self.m.s, self.m.user, ts[-1]['tabId'])
        except Exception:
            pass
        try:
            for tab in await self.m.tabs():
                try:
                    tt = AsyncTab(self.m.s, self.m.user, tab['tabId'])
                    if substr in str(await tt.url()):
                        return tt
                except Exception:
                    continue
        except Exception:
            pass
        return None

    async def submit_all(self):
        from .dbglog import dbg
        from .client import AsyncTab
        for step in range(8):
            t = await self._find_tab('summary.php')
            if t is None:
                t = await self._find_tab('processattempt')
            if t is None:
                t = await self._find_tab('attempt.php')
            if t is None:
                dbg('submit: no live tab, step %d' % step)
                await asyncio.sleep(3)
                continue
            self.m.tab = t
            try:
                u = str(await t.url())
            except Exception:
                u = ''
            dbg('submit: step %d url=%s' % (step, u[:70]))
            if 'processattempt' in u:
                r = await t.evaluate("(() => { const f = document.querySelector('form[action*=processattempt]') || document.querySelector('form'); if (!f) return 'noform'; const b = f.querySelector('input[type=submit], button[type=submit]'); if (b) b.click(); else f.submit(); return 'sub'; })()")
                dbg('submit: confirm %r' % r.get('result'))
                await asyncio.sleep(5)
                continue
            if 'summary.php' in u:
                r = await t.evaluate("(() => { const b = [...document.querySelectorAll('form button[type=submit], form input[type=submit]')].find(x => (x.textContent || x.value || '').includes('Отправить всё')); if (b) { b.click(); return 'sub'; } return 'nobtn'; })()")
                dbg('submit: summary submit %r' % r.get('result'))
                await asyncio.sleep(5)
                nu = ''
                try:
                    nu = str(await t.url())
                except Exception:
                    pass
                if 'summary.php' not in nu:
                    await asyncio.sleep(4)
                return {'ok': True, 'step': step}
            for _ in range(12):
                r = await t.evaluate("(() => { const fa = document.querySelector('input[name=finishattempt]'); const n = document.querySelector('input[name=next]'); let b = fa || n; if (!b) { const bs = [...document.querySelectorAll('form button[type=submit], form input[type=submit]')].filter(x => (x.name || '') === 'next' || (x.value || x.textContent || '').includes('Закончить')); b = bs[0] || null; } if (!b) return 'x'; const isFinish = !!fa || (b.value || b.textContent || '').includes('Закончить'); const f = b.closest('form'); if (f) { f.submit(); } else { b.click(); } return isFinish ? 'finishattempt' : 'next'; })()")
                rv = r.get('result')
                dbg('submit: page-walk %r' % rv)
                await asyncio.sleep(4)
                if rv == 'finishattempt':
                    break
                nu = ''
                try:
                    nu = str(await t.url())
                except Exception:
                    pass
                if 'summary.php' in nu or 'processattempt' in nu:
                    break
                try:
                    t = await self._find_tab('attempt.php') or t
                except Exception:
                    pass
        return {'error': 'no summary'}

    async def get_result(self, quiz_id):
        return await self.m.quiz_result(quiz_id)
