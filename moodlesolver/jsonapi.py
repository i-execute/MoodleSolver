import asyncio
import hashlib
import json
import re
from .errors import err

def _qh(st):
    st = st or {}
    q = st.get('question') or ''
    sig = [q]
    for v in (st.get('checked') or []):
        sig.append('1' if v else '0')
    sig.extend([str(v) for v in (st.get('text_inputs') or [])])
    for s in (st.get('selects') or st.get('match_selects') or []):
        sig.append(str(s.get('value') or ''))
    sig.extend([str(x) for x in (st.get('dragdrop') or [])])
    blob = '|'.join(sig)
    return hashlib.md5(blob.encode()).hexdigest()[:10] if q else None

def _qtype(st):
    st = st or {}
    kinds = st.get('option_kinds') or []
    has_texts = st.get('text_count') is not None and st.get('text_count') > 0
    if has_texts and (not kinds):
        return 'text'
    if st.get('selects') and (not kinds):
        return 'select'
    if kinds and all((k == 'radio' for k in kinds)):
        return 'radio'
    if kinds and all((k == 'checkbox' for k in kinds)):
        return 'checkbox'
    if kinds:
        return 'radio'
    if st.get('selects'):
        return 'select'
    if has_texts:
        return 'text'
    if st.get('match_selects'):
        return 'select'
    if st.get('dragdrop'):
        return 'match'
    return 'unknown'

class JsonAPI:
    def __init__(self, quiz_api, on_ocr=None, image_mode='b64',
                 image_base_url=None, ai_mode='off',
                 ai_model=None, ai_api_key=None, ai_base_url=None,
                 vision_capable=False):
        self.q = quiz_api
        if on_ocr is None:
            from .vision import make_on_ocr
            on_ocr = make_on_ocr()
        self.on_ocr = on_ocr
        self.image_mode = image_mode
        self.image_base_url = image_base_url
        self.vision_capable = vision_capable
        self.ai_mode = ai_mode
        self.ai_model = ai_model
        self.ai_api_key = ai_api_key
        self.ai_base_url = ai_base_url

    async def handle(self, command):
        c = json.loads(command) if isinstance(command, str) else command
        act = c.get('action')
        if act == 'select':
            return await self._select(c)
        if act == 'text':
            return await self._text(c)
        if act == 'selects':
            return await self._selects(c)
        if act == 'ocr':
            return await self._ocr(c)
        if act == 'confirm':
            return await self._confirm()
        if act == 'state':
            return await self._state(c.get('quiz_id'))
        if act == 'resume':
            from .ctx import CtxAPI
            r = await CtxAPI(self.q.m).resume_attempt(c.get('quiz_id'))
            r.pop('tab', None)
            return r
        if act == 'submit':
            return await self._submit()
        return err('E_UNKNOWN_ACTION', action=act)

    def _masked_url(self, fname):
        import hashlib
        import secrets
        raw = fname.rsplit('.', 1)[0]
        h = hashlib.sha256(raw.encode()).hexdigest()[:20]
        a = secrets.token_hex(16)
        b = secrets.token_hex(16)
        ext = fname.rsplit('.', 1)[-1] if '.' in fname else 'png'
        if ext == 'b64':
            ext = 'png'
        return (f'{self.image_base_url.rstrip("/")}/{a}/{b}/'
                f'img_{h}_moodlesolver.{ext}')

    async def _view(self, st):
        st = st or {}
        qimg = st.get('question_image_file')
        optimgs = st.get('option_image_files') or []
        if self.image_mode == 'url' and self.image_base_url:
            qimg = self._masked_url(qimg) if qimg else None
            optimgs = [self._masked_url(f) for f in optimgs]
            qb64 = ob64 = None
        elif self.vision_capable and self.image_base_url:
            qimg = self._masked_url(qimg) if qimg else None
            optimgs = [self._masked_url(f) for f in optimgs]
            qb64 = ob64 = None
        else:
            qb64 = st.get('question_image_b64')
            ob64 = st.get('option_images_b64')
        v = {'page': self._page(st), 'question': st.get('question'), 'question_hash': _qh(st), 'qtype': _qtype(st), 'options': st.get('option_labels'), 'option_kinds': st.get('option_kinds'), 'checked': st.get('checked'), 'selects': st.get('selects'), 'text_inputs': st.get('text_inputs'), 'match_selects': st.get('match_selects'), 'dragdrop': st.get('dragdrop'), 'images': optimgs, 'question_image': qimg, 'question_image_b64': qb64, 'option_images_b64': ob64, 'timeleft': st.get('timeleft'), 'buttons': st.get('buttons'), 'url': st.get('url')}
        if self.vision_capable:
            v['ocr'] = None
        else:
            v['ocr'] = await self._ocr_preview(st)
        return v

    async def _ocr_preview(self, st):
        import asyncio as aio
        files = []
        if st.get('question_image_file'):
            files.append(('question', st['question_image_file']))
        for f in st.get('option_image_files') or []:
            files.append((f, f))
        if not files:
            return None
        from .vision import _resolve

        async def one(name, f):
            ru, en, formula = await aio.gather(aio.to_thread(self.on_ocr, f, 'текст русский'), aio.to_thread(self.on_ocr, f, 'text english'), aio.to_thread(self.on_ocr, f, 'формула'), return_exceptions=True)
            entry = {}
            for k, v in zip(('ru', 'en', 'formula'), (ru, en, formula)):
                entry[k] = None if isinstance(v, Exception) else v
            return (name, entry)
        pairs = await aio.gather(*(one(n, f) for n, f in files), return_exceptions=True)
        out = {}
        for p in pairs:
            if isinstance(p, Exception):
                out.setdefault('errors', []).append(str(p)[:60])
            else:
                out[p[0]] = p[1]
        if self.ai_mode != 'off':
            try:
                from .aipreview import ai_preview, AI_MODE_OFF
                paths = [_resolve(f) for _, f in files]
                text = await aio.to_thread(
                    ai_preview, paths,
                    mode=self.ai_mode, model=self.ai_model,
                    api_key=self.ai_api_key,
                    base_url=self.ai_base_url)
                if text:
                    out['ai'] = text[:2000]
            except Exception:
                pass
        return out or None

    async def _select(self, c):
        answers = c.get('answers') or [{}]
        out = []
        for a in answers:
            r = await self.q.set_answer(index=a.get('index'), label=a.get('label'), labels=a.get('labels'))
            clicked = (r or {}).get('clicked', 0)
            if not clicked and a.get('label'):
                return err('E_NO_MATCH', label=a.get('label'), hint='try index; nbsp/zero-width in labels')
            out.append({'index': a.get('index'), 'label': a.get('label'), 'ok': clicked > 0})
        st = await self.q.get_state() or {}
        v = await self._view(st)
        v['selected'] = out
        return v

    async def _text(self, c):
        vals = c.get('values') or [c.get('value')]
        out = []
        for i, t in enumerate(vals):
            r = await self.q.set_text(t, i)
            if r != 'set':
                return err('E_NO_INPUT', index=i)
            out.append(r)
        st = await self.q.get_state() or {}
        v = await self._view(st)
        v['set'] = out
        return v

    async def _selects(self, c):
        pairs = c.get('pairs') or []
        out = []
        for p in pairs:
            r = await self.q.set_select(p.get('value'), p.get('index', 0))
            if r != 'set':
                return err('E_NO_OPTION', want=p.get('value'), options=(await self._view(await self.q.get_state() or {}) or {}).get('selects'))
            out.append(r)
        st = await self.q.get_state() or {}
        v = await self._view(st)
        v['set'] = out
        return v

    async def _ocr(self, c):
        if not self.on_ocr:
            return err('E_OCR', hint='no on_ocr callback configured')
        files = c.get('files') or []
        out = []
        for f in files:
            try:
                out.append(self.on_ocr(f, c.get('prompt')))
            except Exception as e:
                return err('E_OCR', file=f, detail=str(e)[:80])
        st = await self.q.get_state() or {}
        return {'ocr': out, **await self._view(st)}

    async def _confirm(self):
        st = await self.q.get_state() or {}
        checked = [c for c in (st.get('checked') or []) if c]
        texts = [t for t in (st.get('text_inputs') or []) if t]
        sels = [s for s in (st.get('selects') or []) if s.get('value')]
        if not (checked or texts or sels):
            return err('E_NOTHING_CHECKED', page=self._page(st),
                       question=st.get('question'),
                       selects_state=st.get('selects'),
                       text_state=st.get('text_inputs'))
        if st.get('selects') and len(sels) < len(st.get('selects') or []):
            empty = [i for i, s in enumerate(st.get('selects') or [])
                     if not s.get('value')]
            return err('E_SELECTS_INCOMPLETE', page=self._page(st),
                       empty_comboboxes=empty,
                       hint='fill all non-empty comboboxes; a combobox '
                            'left blank on purpose requires checked/text')
        t = await self.q._tab()
        u0 = st.get('url') or ''
        fin = await t.evaluate("(async () => { const n = document.querySelector('input[name=finishattempt], input[name=next]'); if (n) { n.click(); return n.name; } return 'nobtn'; })()")
        for _ in range(15):
            await asyncio.sleep(1)
            u = await t.url()
            if u != u0 and '[object' not in u and 'summary.php' in (u or ''):
                break
            if u != u0 and '[object' not in u:
                break
        u = await t.url()
        if u == u0 or '[object' in (u or ''):
            from .ctx import CtxAPI
            mm = re.search(r'cmid=(\d+)', u0)
            if mm:
                r = await CtxAPI(self.q.m).resume_attempt(int(mm.group(1)))
                if r.get('ok'):
                    t = self.q.m.tab
        st2 = await self.q.get_state() or {}
        if not st2.get('question'):
            st2 = await self._resume() or {}
        v = await self._view(st2)
        v['confirm'] = fin
        return v

    async def _resume(self):
        from .ctx import CtxAPI
        u = (await self.q.get_state() or {}).get('url') or ''
        mm = re.search(r'cmid=(\d+)', u)
        if not mm:
            return {}
        r = await CtxAPI(self.q.m).resume_attempt(int(mm.group(1)))
        if r.get('ok'):
            return await self.q.get_state() or {}
        return {}

    async def _state(self, quiz_id=None):
        st = await self.q.get_state() or {}
        if not st.get('question'):
            if quiz_id:
                from .ctx import CtxAPI
                r = await CtxAPI(self.q.m).resume_attempt(quiz_id)
                r.pop('tab', None)
                if r.get('ok'):
                    st = await self.q.get_state() or {}
            else:
                st = await self._resume() or st
        return await self._view(st)

    async def _submit(self):
        r = await self.q.submit_all()
        if isinstance(r, dict) and r.get('error'):
            return r
        st = await self.q.get_state() or {}
        mm = re.search(r'cmid=(\d+)', st.get('url') or '')
        if not mm:
            mm = re.search(r'cmid=(\d+)', (self.q.m.tab and await self.q.m.tab.url() or '') or '')
        if mm:
            t = await self.q.m.new_tab(f'{self.q.m.site}/mod/quiz/view.php?id={mm.group(1)}')
            r2 = await t.evaluate("(document.querySelector('[role=main]') ? document.querySelector('[role=main]').innerText : '').match(/оценка за этот тест:\\s*([\\d,]+)\\/(\\d+)/)")
            score = r2.get('result') if isinstance(r2.get('result'), str) else None
            from .ctx import CtxAPI
            await CtxAPI(self.q.m)._tab_for('my/')
            return {'submitted': r, 'score': score or await self.q.get_result(int(mm.group(1)))}
        return {'submitted': r}

    def _page(self, st):
        u = (st or {}).get('url') or ''
        if 'page=' in u:
            try:
                return int(u.split('page=')[1].split('&')[0])
            except ValueError:
                pass
        return None
