import asyncio
import json
import shutil
import time
import urllib.request

OC_DEFAULT_MODEL = 'mimo-v2.6-flash-free'
MODE_OPENCODE = 'opencode'
MODE_OPENAI = 'openai'
MODE_POOL = 'pool'

AGENT_PROMPT = (
    'You are solving a Moodle quiz. '
    'For each question: read the question text, options and OCR data. '
    'Choose the correct answer. Reply ONLY JSON: '
    '{"action": "answer|text|select|next|submit", '
    '"label": "...", "labels": [...], "index": 0, '
    '"value": "...", "values": [...]} '
    'IMPORTANT: if this question is already answered (checked contains '
    'true for the option you chose), reply {"action":"next"} instead of '
    'answering again. After answering a NEW question on a page that '
    'shows no other unanswered questions, reply {"action":"next"}.')


class SessionError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def check_session_error(st):
    if not st:
        raise SessionError('SESSION_EMPTY', 'state is empty')
    e = st.get('error') or ''
    if 'login' in e.lower() or 'auth' in e.lower() or 'guest' in e.lower():
        raise SessionError('SESSION_NOT_AUTHORIZED',
                           'session not authorized, connect required')
    if 'attempt' in e.lower() and 'not found' in e.lower():
        raise SessionError('ATTEMPT_NOT_FOUND', 'attempt not found')
    return None


class AIRunner:

    def __init__(self, mode=MODE_OPENAI, api_key=None, base_url=None,
                 model=None, pool=None, keykeeper_file=None,
                 image_base_url=None, vision_model=None,
                 vision_base_url=None, vision_api_key=None,
                 fallback_opencode=True, fallback_ocr=True,
                 chain=None):
        self.fallback_opencode = fallback_opencode
        self.fallback_ocr = fallback_ocr
        self.chain = chain or ['primary', 'pool', 'opencode', 'ocr']
        self._last_error = None
        self.image_base_url = image_base_url
        self.vision_model = vision_model
        self.vision_base_url = vision_base_url
        self.vision_api_key = vision_api_key
        self.vision_router = None
        self.max_tokens = 400
        self.mode = mode
        self.key = api_key
        self.base = base_url
        self.model = model
        self.pool = pool
        if keykeeper_file and not pool:
            from .keypool import KeyPool
            self.pool = KeyPool().load(keykeeper_file,
                                       default_model=model)
            self.mode = MODE_POOL
        self.qapi = None

    def bind(self, quizapi):
        self.qapi = quizapi
        return self

    def ocr(self, file, prompt=None):
        from .vision import ocr_file
        d = getattr(self.qapi.m, 'dumps_dir', None) if self.qapi else None
        return ocr_file(file, prompt, d)

    def send_ai_request(self, messages, images=None, max_tokens=400):
        from .dbglog import dbg, err as _err
        t0 = time.time()
        dbg('AI request start (images=%d, chain=%s)' % (
            len(images or []), self.chain))
        errors = []
        for step in self.chain:
            try:
                if step == 'primary':
                    out = self._send_primary(messages, images, max_tokens)
                elif step == 'pool':
                    if not self.pool:
                        continue
                    from .keypool import pool_request
                    out = pool_request(self.pool, str(messages),
                                       max_tokens=max(
                                           1600, max_tokens))
                elif step == 'opencode':
                    if not (self.fallback_opencode
                            and shutil.which('opencode')):
                        continue
                    out = self._send_opencode(
                        self._flatten(messages, images, use_ocr=True),
                        None, max_tokens)
                elif step == 'ocr':
                    if not (self.fallback_ocr and images):
                        continue
                    out = self._ocr_only_answer(messages, images)
                else:
                    continue
            except Exception as e:
                errors.append('%s: %s' % (step, str(e)[:120]))
                self._last_error = str(e)[:120]
                dbg('AI step %s FAILED: %s' % (step, self._last_error))
                continue
            if out is not None:
                dbg('AI step %s OK in %.1fs: %r' % (
                    step, time.time() - t0, str(out)[:80]))
                return out
            errors.append('%s: empty' % step)
        msg = 'AI all backends failed: ' + ' | '.join(errors)
        _err(msg)
        raise RuntimeError(msg)

    def _send_primary(self, messages, images, max_tokens):
        if images:
            from .visionroute import VisionRouter
            if self.vision_router is None:
                self.vision_router = VisionRouter(ocr_fn=self._ocr_b64)
            if self.vision_model:
                try:
                    return self._route_via_vision_model(
                        messages, images, max_tokens)
                except Exception:
                    pass
            route = self.vision_router.route(
                self._active_base(), self._active_key(), self.model,
                images, str(messages), ocr=self._ocr_b64)
            if route['mode'] == 'ocr':
                return self._send_plain(route['content'], max_tokens)
            return self._send_content(route['content'], max_tokens)
        if self.mode == MODE_POOL:
            from .keypool import pool_request
            prompt = str(messages)
            return pool_request(self.pool, prompt,
                                max_tokens=max(1600, max_tokens))
        if self.mode == MODE_OPENCODE:
            return self._send_opencode(messages, None, max_tokens)
        return self._send_openai(messages, None, max_tokens)

    def _flatten(self, messages, images, use_ocr=False):
        prompt = str(messages)
        if images and use_ocr:
            try:
                prompt += '\nOCR: ' + json.dumps(
                    [self._ocr_b64(b) for b in images],
                    ensure_ascii=False)
            except Exception:
                pass
        return prompt

    def _ocr_only_answer(self, messages, images):
        texts = []
        for b in images:
            try:
                texts.append(self._ocr_b64(b))
            except Exception:
                texts.append('')
        return json.dumps({'action': 'answer', 'index': 0,
                           'ocr': texts, 'mode': 'local_ocr'},
                          ensure_ascii=False)

    def _active_base(self):
        if self.mode == MODE_POOL and self.pool.providers:
            return self.pool.providers[0].base_url
        return self.base or ''

    def _active_key(self):
        if self.mode == MODE_POOL and self.pool.providers:
            return self.pool.providers[0].keys[0] if \
                self.pool.providers[0].keys else ''
        return self.key or ''

    def _route_via_vision_model(self, prompt, images, max_tokens=400):
        from .visionroute import _chat
        content = [{'type': 'text', 'text': str(prompt)}]
        for b in images:
            u = b if str(b).startswith('http') else \
                'data:image/png;base64,' + str(b)
            content.append({'type': 'image_url', 'image_url': {'url': u}})
        return _chat(self.vision_base_url or self._active_base(),
                     self.vision_api_key or self._active_key(),
                     self.vision_model, content,
                     max_tokens=self.max_tokens or 400)

    def _send_content(self, content, max_tokens):
        from .visionroute import _chat
        return _chat(self._active_base(), self._active_key(),
                     self.model, content, max_tokens=max_tokens)

    def _send_plain(self, prompt, max_tokens):
        if self.mode == MODE_POOL:
            from .keypool import pool_request
            return pool_request(self.pool, prompt,
                                max_tokens=max_tokens)
        if self.mode == MODE_OPENCODE:
            return self._send_opencode(prompt, None, max_tokens)
        return self._send_openai(prompt, None, max_tokens)

    def _ocr_b64(self, b64):
        from .vision import ocr_file
        import base64
        raw = base64.b64decode(b64 if not str(b64).startswith('http')
                               else base64.b64encode(
                                   urllib.request.urlopen(
                                       b64, timeout=20).read()).decode())
        import tempfile
        import os
        p = tempfile.mktemp(suffix='.png')
        open(p, 'wb').write(raw)
        try:
            return ocr_file(p)
        finally:
            try:
                os.unlink(p)
            except Exception:
                pass

    def _send_opencode(self, messages, images=None, max_tokens=400,
                       force_default_model=False):
        if not shutil.which('opencode'):
            raise RuntimeError('opencode binary not found')
        mdl = OC_DEFAULT_MODEL if force_default_model else (
            self.model or OC_DEFAULT_MODEL)
        if '/' not in mdl:
            mdl = 'opencode/' + mdl
        if not mdl.startswith('opencode/'):
            mdl = 'opencode/' + mdl.split('/')[-1]
        if 'mimo' not in mdl:
            mdl = 'opencode/' + OC_DEFAULT_MODEL
        text = json.dumps(messages, ensure_ascii=False) if isinstance(
            messages, (dict, list)) else str(messages)
        cmd = ['opencode', 'run', text, '--model', mdl]
        import subprocess
        import tempfile
        for b64 in images or []:
            fn = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            import base64
            fn.write(base64.b64decode(b64))
            fn.close()
            cmd += ['-f', fn.name]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
            out, _err = proc.communicate(timeout=60)
            out = (out or '').strip()
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError('opencode timeout')
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            raise
        lines = [ln for ln in out.splitlines()
                 if ln and not ln.startswith('>')]
        return '\n'.join(lines).strip()

    def _send_openai(self, messages, images=None, max_tokens=400):
        if not (self.key and self.base):
            raise RuntimeError('api_key and base_url required for openai mode')
        content = [{'type': 'text',
                    'text': json.dumps(messages, ensure_ascii=False)
                    if isinstance(messages, (dict, list)) else str(messages)}]
        for b64 in images or []:
            content.append({'type': 'image_url',
                            'image_url': {'url':
                                          f'data:image/png;base64,{b64}'}})
        body = json.dumps({'model': self.model, 'max_tokens': max_tokens,
                           'temperature': 0,
                           'messages': [{'role': 'user',
                                         'content': content}]}).encode()
        req = urllib.request.Request(
            self.base.rstrip('/') + '/chat/completions', data=body,
            headers={'Authorization': f'Bearer {self.key}',
                     'Content-Type': 'application/json',
                     'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=120) as _r:
            raw = _r.read()
        r = json.loads(raw)
        return r['choices'][0]['message']['content']

    def receive_ai_response(self, text):
        try:
            return json.loads(text.strip().strip('`').removeprefix('json').strip())
        except Exception:
            return {'action': None, 'raw': text}

    async def execute_ai_response(self, resp):
        a = resp.get('action')
        if a == 'answer':
            for l in resp.get('labels') or ([resp['label']] if resp.get('label') else []):
                await self.qapi.set_answer(label=l)
            if resp.get('index') is not None:
                await self.qapi.set_answer(index=resp['index'])
        elif a == 'text':
            await self.qapi.set_text(resp['value'], resp.get('index', 0))
        elif a == 'select':
            for v, i in zip(resp.get('values', [resp.get('value')]), range(99)):
                await self.qapi.set_select(v, i)
            st = await self.qapi.get_state() or {}
            if st.get('selects') and any(not s.get('value')
                                         for s in st['selects']):
                return 'selects_incomplete'
            return 'next'
        elif a == 'next':
            return await self.qapi.next_page()
        elif a == 'submit':
            return await self.qapi.submit_all()
        return resp.get('action')

    async def loop(self, quiz_id, max_steps=200, answer_bank=None):
        info = await self.qapi.get_quiz_info(quiz_id)
        if info.get('can_start'):
            await self.qapi.start_attempt(quiz_id)
        else:
            from .ctx import CtxAPI
            try:
                await CtxAPI(self.qapi.m).resume_attempt(quiz_id)
            except Exception:
                pass
        from .jsonapi import JsonAPI
        from .watcher import HealthWatcher
        from .visionroute import supports_vision
        vc = False
        try:
            if self.vision_model:
                vc = bool(supports_vision(
                    self.vision_base_url or self._active_base(),
                    self.vision_api_key or self._active_key(),
                    self.vision_model))
            elif self._active_base() and self._active_key() and self.model:
                sv = supports_vision(self._active_base(),
                                     self._active_key(), self.model)
                vc = bool(sv) if sv is not None else False
        except Exception:
            vc = False
        j = JsonAPI(self.qapi, on_ocr=self.ocr,
                    image_mode='url' if self.image_base_url else 'b64',
                    image_base_url=self.image_base_url,
                    vision_capable=vc and bool(self.image_base_url))
        w = HealthWatcher(j, quiz_id, poll=4.0, max_dead=4)
        notice = None
        empty = 0
        for _ in range(max_steps):
            from .dbglog import dbg
            try:
                st = await j.handle({'action': 'state', 'quiz_id': quiz_id})
                dbg('state OK url=%s q=%r err=%r' % (
                    str(st.get('url'))[:60],
                    str(st.get('question'))[:60],
                    st.get('error')))
            except Exception as e:
                dbg('state EXC %s -> reconnect' % str(e)[:100])
                st = await w.reconnect()
                notice = w.notice()
            if not (st.get('question') or st.get('images') or
                    st.get('selects') or st.get('text_inputs') or
                    st.get('error')):
                empty += 1
                if empty > 3:
                    from .ctx import CtxAPI
                    ast = {}
                    try:
                        ast = await CtxAPI(self.qapi.m).attempt_status(
                            quiz_id)
                    except Exception:
                        pass
                    if not ast.get('live'):
                        dbg('attempt dead, exit loop: %r' % ast)
                        return {'done': 'no live attempt',
                                'status': ast}
                    empty = 0
                    st = await w.reconnect()
                    notice = w.notice()
                    continue
            elif 'view.php' in str(st.get('url', '')) and \
                    not st.get('question'):
                dbg('on view.php without question -> check attempt')
                from .ctx import CtxAPI
                ast = {}
                try:
                    ast = await CtxAPI(self.qapi.m).attempt_status(
                        quiz_id)
                except Exception:
                    pass
                if ast.get('finished') and not ast.get('live'):
                    dbg('attempt finished, exit: %r' % ast)
                    return {'done': 'attempt finished', 'status': ast}
            else:
                empty = 0
            if st.get('error') and 'summary' not in st.get('url', ''):
                if 'attempt' in str(st.get('error')).lower() or st.get('error') in ('no attempt', 'SESSION_EMPTY'):
                    st = await w.reconnect()
                    notice = w.notice()
                    if st.get('error'):
                        return st
                    continue
                st = await w.reconnect()
                if st.get('error'):
                    return st
                notice = notice or w.notice()
                continue
            if 'summary' in st.get('url', ''):
                try:
                    return await j.handle({'action': 'submit'})
                except Exception:
                    st = await w.reconnect()
                    continue
            bank = None
            if answer_bank:
                bank = answer_bank(st.get('question') or '', st.get('options') or [])
            if bank is not None:
                cmd = {'action': 'select', 'answers': [{'index': bank}]}
                if st.get('qtype') == 'text':
                    cmd = {'action': 'text', 'values': [bank]}
                await j.handle(cmd)
                try:
                    await j.handle({'action': 'confirm'})
                except Exception:
                    st = await w.reconnect()
                    notice = w.notice()
                continue
            prompt = AGENT_PROMPT
            if notice:
                prompt += '\nВНИМАНИЕ: сайт был недоступен, последние ' \
                          'изменения могли не сохраниться. Текущее ' \
                          'задание ниже — проверь состояние checked и ' \
                          'перейди к текущему вопросу.\n' + notice + '\n'
                notice = None
            prompt += ' Question: ' + json.dumps(
                {'page': st.get('page'),
                 'question_hash': st.get('question_hash'),
                 'timeleft': st.get('timeleft'),
                 'q': st.get('question'),
                 'options': st.get('options'),
                 'checked': st.get('checked'),
                 'images': st.get('images')}, ensure_ascii=False)
            if st.get('images'):
                try:
                    from .vision import ocr_file
                    ocrs = await asyncio.to_thread(
                        lambda: [ocr_file(f) for f in st['images']])
                    prompt += '\nOCR of option images: ' + json.dumps(
                        ocrs, ensure_ascii=False)
                except Exception:
                    pass
            try:
                txt = await asyncio.to_thread(
                    self.send_ai_request, prompt,
                    st.get('option_images_b64') or
                    ([st['question_image_b64']]
                     if st.get('question_image_b64') else None))
            except Exception:
                await asyncio.sleep(20)
                txt = 'NOANSWER'
            resp = self.receive_ai_response(txt)
            try:
                act = await self.execute_ai_response(resp)
            except Exception:
                act = 'error'
            if act == 'error':
                st = await w.reconnect()
                notice = w.notice()
                continue
            if act == 'selects_incomplete':
                prompt2 = (AGENT_PROMPT + ' Some comboboxes are empty. '
                           'Fill them or confirm blank ones. State: ' +
                           json.dumps(st.get('selects'),
                                      ensure_ascii=False))
                txt2 = await asyncio.to_thread(self.send_ai_request, prompt2)
                resp2 = self.receive_ai_response(txt2)
                act = await self.execute_ai_response(resp2)
            if act in ('submit', 'next', None):
                try:
                    await j.handle({'action': 'confirm'})
                except Exception:
                    st = await w.reconnect()
                    notice = w.notice()
        return await self._final_result(quiz_id, w)

    async def _final_result(self, quiz_id, w):
        for _ in range(3):
            try:
                return await self.qapi.get_result(quiz_id)
            except Exception:
                await w.reconnect()
        try:
            return await self.qapi.get_result(quiz_id)
        except Exception:
            return {'error': 'result unavailable'}
