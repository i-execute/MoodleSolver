import json
import urllib.request
import urllib.error

PROBE_PNG_B64 = ('iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAA'
                 'FUlEQVR42mNk+M+ACzDhWmYDIwMDAwMDAHmQAQAAAAAAAAAAA'
                 'AAAAAAAAAAAAMCJGk0AAAAAgAAxP3AAAAyWfI/eAAAAElFTkSuQmCC')
PROBE_PROMPT = 'Reply with exactly: OK'


def _chat(base_url, key, model, messages, max_tokens=20, timeout=30,
          vision=False):
    content = messages if isinstance(messages, list) else [
        {'type': 'text', 'text': str(messages)}]
    body = json.dumps({'model': model, 'max_tokens': max_tokens,
                       'temperature': 0,
                       'messages': [{'role': 'user', 'content': content}]}
                      ).encode()
    req = urllib.request.Request(
        base_url.rstrip('/') + '/chat/completions', data=body,
        headers={'Authorization': 'Bearer ' + key,
                 'Content-Type': 'application/json',
                 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as _r:
        raw = _r.read()
    r = json.loads(raw)
    return r['choices'][0]['message']['content']


def supports_vision(base_url, key, model, timeout=30):
    try:
        msgs = [
            {'type': 'text', 'text': PROBE_PROMPT},
            {'type': 'image_url', 'image_url': {
                'url': 'data:image/png;base64,' + PROBE_PNG_B64}}]
        r = _chat(base_url, key, model, msgs, timeout=timeout)
        return bool(r and r.strip())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return None
        return False
    except Exception:
        return False


class VisionRouter:
    def __init__(self, runner=None, ocr_fn=None):
        self.runner = runner
        self.ocr_fn = ocr_fn
        self.cache = {}

    def model_accepts_images(self, base_url, key, model):
        k = (base_url, model)
        if k not in self.cache:
            self.cache[k] = supports_vision(base_url, key, model)
        v = self.cache[k]
        if v is None:
            return True
        return v

    def route(self, base_url, key, model, images, prompt, ocr=None):
        if images and self.model_accepts_images(base_url, key, model):
            content = [{'type': 'text', 'text': prompt}]
            for b in images:
                content.append({'type': 'image_url', 'image_url': {
                    'url': 'data:image/png;base64,' + b
                    if not str(b).startswith('http')
                    else str(b)}})
            return {'mode': 'model', 'content': content}
        if images:
            ocr = ocr or self.ocr_fn
            texts = []
            for b in images:
                try:
                    texts.append(ocr(b) if ocr else '')
                except Exception:
                    texts.append('')
            return {'mode': 'ocr', 'content': prompt + '\nOCR: ' +
                    json.dumps(texts, ensure_ascii=False)}
        return {'mode': 'text', 'content': prompt}
