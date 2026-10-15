import asyncio
import json
import shutil
import urllib.request

OC_DEFAULT_MODEL = 'mimo-v2.6-flash-free'
MODE_OPENCODE = 'opencode'
MODE_OPENAI = 'openai'

AGENT_PROMPT = (
    'You are solving a Moodle quiz on mathematical statistics. '
    'For each question: read the question text, options and OCR data. '
    'Choose the correct answer. Reply ONLY JSON: '
    '{"action": "answer|text|select|next|submit", '
    '"label": "...", "labels": [...], "index": 0, '
    '"value": "...", "values": [...]}')


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
                 model=None):
        self.mode = mode
        self.key = api_key
        self.base = base_url
        self.model = model
        self.qapi = None

    def bind(self, quizapi):
        self.qapi = quizapi
        return self

    def ocr(self, file, prompt=None):
        from .vision import ocr_file
        d = getattr(self.qapi.m, 'dumps_dir', None) if self.qapi else None
        return ocr_file(file, prompt, d)

    def send_ai_request(self, messages, images=None, max_tokens=400):
        if self.mode == MODE_OPENCODE:
            return self._send_opencode(messages, images, max_tokens)
        return self._send_openai(messages, images, max_tokens)

    def _send_opencode(self, messages, images=None, max_tokens=400):
        if not shutil.which('opencode'):
            raise RuntimeError('opencode binary not found')
        mdl = self.model or OC_DEFAULT_MODEL
        if '/' not in mdl:
            mdl = 'opencode/' + mdl
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
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=240).stdout.strip()
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
        r = json.load(urllib.request.urlopen(req, timeout=120))
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

    async def loop(self, quiz_id, max_steps=60, answer_bank=None):
        info = await self.qapi.get_quiz_info(quiz_id)
        if info.get('can_start'):
            await self.qapi.start_attempt(quiz_id)
        from .jsonapi import JsonAPI
        j = JsonAPI(self.qapi, on_ocr=self.ocr)
        for _ in range(max_steps):
            st = await j.handle({'action': 'state', 'quiz_id': quiz_id})
            try:
                check_session_error(st)
            except SessionError as e:
                return {'error': e.code, 'message': e.message}
            if st.get('error') and 'summary' not in st.get('url', ''):
                return st
            if 'summary' in st.get('url', ''):
                return await j.handle({'action': 'submit'})
            bank = None
            if answer_bank:
                bank = answer_bank(st.get('question') or '', st.get('options') or [])
            if bank is not None:
                cmd = {'action': 'select', 'answers': [{'index': bank}]}
                if st.get('qtype') == 'text':
                    cmd = {'action': 'text', 'values': [bank]}
                await j.handle(cmd)
                await j.handle({'action': 'confirm'})
                continue
            prompt = AGENT_PROMPT + ' Question: ' + json.dumps(
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
            txt = await asyncio.to_thread(
                self.send_ai_request, prompt,
                st.get('option_images_b64') or
                ([st['question_image_b64']]
                 if st.get('question_image_b64') else None))
            resp = self.receive_ai_response(txt)
            act = await self.execute_ai_response(resp)
            if act == 'selects_incomplete':
                prompt2 = (AGENT_PROMPT + ' Some comboboxes are empty. '
                           'Fill them or confirm blank ones. State: ' +
                           json.dumps(st.get('selects'),
                                      ensure_ascii=False))
                txt2 = await asyncio.to_thread(self.send_ai_request, prompt2)
                resp2 = self.receive_ai_response(txt2)
                act = await self.execute_ai_response(resp2)
            if act in ('submit', 'next', None):
                await j.handle({'action': 'confirm'})
        return await self.qapi.get_result(quiz_id)
