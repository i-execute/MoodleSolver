import asyncio
import json
import urllib.request

class AIRunner:

    def __init__(self, api_key=None, base_url=None, model=None):
        self.key = api_key
        self.base = base_url or 'https://integrate.api.nvidia.com/v1'
        self.model = model or 'meta/llama-3.2-90b-vision-instruct'
        self.qapi = None

    def bind(self, quizapi):
        self.qapi = quizapi
        return self

    def ocr(self, file, prompt=None):
        from .vision import ocr_file
        d = getattr(self.qapi.m, 'dumps_dir', None) if self.qapi else None
        return ocr_file(file, prompt, d)

    def send_ai_request(self, messages, images=None, max_tokens=400):
        content = [{'type': 'text', 'text': json.dumps(messages, ensure_ascii=False)}] if isinstance(messages, (dict, list)) else [{'type': 'text', 'text': messages}]
        for b64 in images or []:
            content.append({'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}})
        body = json.dumps({'model': self.model, 'max_tokens': max_tokens, 'temperature': 0, 'messages': [{'role': 'user', 'content': content}]}).encode()
        req = urllib.request.Request(f'{self.base}/chat/completions', data=body, headers={'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json'})
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
            prompt = 'You are solving a Moodle quiz question. Question: ' + json.dumps({'page': st.get('page'), 'question_hash': st.get('question_hash'), 'timeleft': st.get('timeleft'), 'q': st.get('question'), 'options': st.get('options'), 'checked': st.get('checked'), 'images': st.get('images')}, ensure_ascii=False) + ' Reply ONLY JSON: {"action": "answer|text|select|next|submit", "label": "...", "labels": [...], "index": 0, "value": "...", "values": [...]}'
            if st.get('images'):
                try:
                    from .vision import ocr_file
                    ocrs = await asyncio.to_thread(lambda: [ocr_file(f) for f in st['images']])
                    prompt += '\nOCR of option images: ' + json.dumps(ocrs, ensure_ascii=False)
                except Exception:
                    pass
            txt = await asyncio.to_thread(self.send_ai_request, prompt, st.get('option_images_b64') or ([st['question_image_b64']] if st.get('question_image_b64') else None))
            resp = self.receive_ai_response(txt)
            act = await self.execute_ai_response(resp)
            if act in ('submit', None):
                await j.handle({'action': 'confirm'})
        return await self.qapi.get_result(quiz_id)
