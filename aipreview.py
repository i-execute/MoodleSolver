import base64
import json
import shutil
import subprocess
import urllib.request

AI_MODE_OFF = 'off'
AI_MODE_OPENCODE = 'opencode'
AI_MODE_OPENAI = 'openai'
AI_MODES = (AI_MODE_OFF, AI_MODE_OPENCODE, AI_MODE_OPENAI)

OC_DEFAULT_MODEL = 'mimo-v2.6-flash-free'


def opencode_available():
    return shutil.which('opencode') is not None


def openai_vision(paths, prompt, api_key, base_url, model, timeout=120):
    content = [{'type': 'text', 'text': prompt}]
    for p in paths:
        with open(p, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        content.append({'type': 'image_url',
                        'image_url': {'url': f'data:image/png;base64,{b64}'}})
    body = json.dumps({'model': model, 'max_tokens': 800, 'temperature': 0,
                       'messages': [{'role': 'user', 'content': content}]}).encode()
    req = urllib.request.Request(base_url.rstrip('/') + '/chat/completions', data=body,
                                 headers={'Authorization': f'Bearer {api_key}',
                                          'Content-Type': 'application/json',
                                          'User-Agent': 'Mozilla/5.0'})
    r = json.load(urllib.request.urlopen(req, timeout=timeout))
    return r['choices'][0]['message']['content']


def ai_preview(paths, prompt='Опиши что на картинке, весь текст перепиши',
               mode=AI_MODE_OFF, model=None, timeout=120,
               api_key=None, base_url=None):
    if not paths or mode == AI_MODE_OFF:
        return None
    plist = list(paths) if isinstance(paths, (list, tuple)) else [paths]
    if mode == AI_MODE_OPENAI:
        if not (api_key and base_url):
            return None
        try:
            return openai_vision(plist, prompt, api_key,
                                 base_url, model, timeout)
        except Exception:
            return None
    if mode == AI_MODE_OPENCODE:
        if not opencode_available():
            return None
        mdl = model or OC_DEFAULT_MODEL
        if '/' not in mdl:
            mdl = 'opencode/' + mdl
        cmd = ['opencode', 'run', prompt, '--model', mdl]
        for p in plist:
            cmd += ['-f', p]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=timeout).stdout.strip()
        except Exception:
            return None
        if not out:
            return None
        lines = [ln for ln in out.splitlines()
                 if ln and not ln.startswith('>')]
        return '\n'.join(lines).strip() or None
    return None
