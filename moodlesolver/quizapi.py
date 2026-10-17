import asyncio
import json
import re

class QuizAPI:

    def __init__(self, moodle):
        self.m = moodle

    async def _tab(self):
        t = await self.m.pick('attempt.php')
        if not t:
            t = await self.m.pick('summary.php')
        if not t:
            t = await self.m.pick('view.php?id=')
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
        t = await self._tab()
        if not t:
            return {'error': 'no tab'}
        tl = await t.evaluate("(() => { const e = document.querySelector('#quiz-timer, .quiztimer, [class*=timeleft]'); if (e) return e.textContent.trim(); const m = document.body.innerText.match(/Осталось[:\\s]*(\\d+)[^\\d]{0,3}(\\d*)/); return m ? m[0] : null; })()")
        timeleft = tl.get('result')
        r = await t.evaluate("(async () => {\n            const q = document.querySelector('.qtext');\n            const inputs = [...document.querySelectorAll('.answer input')];\n            const kinds = inputs.map(e => e.type);\n            const labels = inputs.map(e =>\n                e.parentElement.textContent.trim());\n            const checked = inputs.map(e => e.checked);\n            const btns = [...document.querySelectorAll(\n                'form button[type=submit], form input[type=submit]')];\n            const texts = [...document.querySelectorAll(\n                '.answer input[type=text], .answer textarea')];\n            const imgs = [...document.querySelectorAll('.answer img')];\n            const qi = document.querySelector('.qtext img');\n            const grab = async im => { await new Promise(rr =>\n                im.complete ? rr() : im.onload = rr);\n                const c = document.createElement('canvas');\n                c.width = im.naturalWidth; c.height = im.naturalHeight;\n                c.getContext('2d').drawImage(im, 0, 0);\n                return c.toDataURL('image/png').split(',')[1]; };\n            let qimg = null;\n            if (qi) qimg = await grab(qi);\n            const optimgs = [];\n            for (const im of imgs) optimgs.push(await grab(im));\n            return JSON.stringify({\n                question: q ? q.innerText.trim() : null,\n                question_image: qimg ? 1 : null,\n                option_kinds: kinds, option_labels: labels,\n                checked: checked,\n                selects: [...document.querySelectorAll('select')].map(s => ({\n                    options: [...s.options].map(o => o.textContent.trim()),\n                    value: s.options[s.selectedIndex].textContent.trim()})),\n                text_inputs: texts.map(e => e.value),\n                buttons: btns.map(b => (b.name || '') + '=' + b.value),\n                url: location.href});\n        })()")
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
        t = await self._tab()
        r = await t.evaluate(f"(() => {{ const ts = document.querySelectorAll('.answer input[type=text], .answer textarea'); if (!ts[{index}]) return 'noinput'; ts[{index}].value = {json.dumps(text)}; ts[{index}].dispatchEvent(new Event('input', {{bubbles: true}})); return 'set'; }})()")
        return r.get('result')

    async def set_select(self, value, index=0):
        t = await self._tab()
        r = await t.evaluate("(async () => { const ss = document.querySelectorAll('select'); if (!ss[" + str(index) + "]) return 'nosel'; const want = " + json.dumps(value) + '; const o = [...ss[' + str(index) + "].options].find(x => x.textContent.trim().toLowerCase() === want.toLowerCase()); if (!o) return 'noopt'; ss[" + str(index) + '].value = o.value; ss[' + str(index) + "].dispatchEvent(new Event('change', {bubbles: true})); return 'set'; })()")
        return r.get('result')

    async def next_page(self):
        t = await self._tab()
        return await t.next_page()

    async def get_options(self):
        st = await self.get_state()
        return {'labels': st.get('option_labels'), 'kinds': st.get('option_kinds'), 'selects': st.get('selects'), 'text_inputs': st.get('text_inputs')}

    async def submit_all(self):
        from .dbglog import dbg
        t = await self.m.pick('summary.php')
        if not t:
            at = await self.m.pick('attempt.php')
            if at:
                dbg('submit: attempt page -> finishattempt click')
                r = await at.evaluate("(() => { const n = document.querySelector('input[name=next], input[name=finishattempt]'); if (n) { n.click(); return 'ok'; } return 'x'; })()")
                dbg('submit: finish click %r' % r)
                for _ in range(10):
                    await asyncio.sleep(1)
                    t = await self.m.pick('summary.php')
                    if t:
                        break
                if not t:
                    t = await self.m.pick('processattempt')
        if not t:
            dbg('submit: no summary tab found')
            return {'error': 'no summary'}
        dbg('submit: summary page -> Отправить всё')
        r = await t.evaluate("(() => { const b = [...document.querySelectorAll('form button[type=submit], form input[type=submit]')].find(x => (x.textContent || x.value || '').includes('Отправить всё')); if (b) { b.click(); return 'sub'; } return 'nobtn'; })()")
        await asyncio.sleep(4)
        r2 = await t.evaluate("(() => { const f = document.querySelector('form'); if (!f) { return 'noform'; } const b = [...document.querySelectorAll('form button[type=submit], form input[type=submit]')].find(x => (x.textContent || x.value || '').includes('Отправить всё и завершить')); if (b) { b.click(); } f.submit(); return 'done'; })()")
        await asyncio.sleep(4)
        dbg('submit: final %r' % r2)
        return r2.get('result')

    async def get_result(self, quiz_id):
        return await self.m.quiz_result(quiz_id)
