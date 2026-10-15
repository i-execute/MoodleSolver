import json
import time
import urllib.request
import urllib.error

PROVIDER_PRESETS = {
    'odirouter': {
        'base_url': 'https://api.odirouter.ai/v1',
        'models': ['gemini-3.5-flash', 'free-gemini-3.1-flash-lite',
                   'free-gemini-2.5-pro', 'free-claude-haiku-4.5',
                   'free-qwen3.5-flash'],
    },
    'openrouter': {
        'base_url': 'https://openrouter.ai/api/v1',
        'models': [],
    },
    'nvidia': {
        'base_url': 'https://integrate.api.nvidia.com/v1',
        'models': [],
    },
    'openai': {
        'base_url': 'https://api.openai.com/v1',
        'models': ['gpt-4o-mini', 'gpt-4o'],
    },
    'anthropic': {
        'base_url': 'https://api.anthropic.com/v1',
        'models': ['claude-haiku-4.5'],
    },
    'groq': {
        'base_url': 'https://api.groq.com/openai/v1',
        'models': [],
    },
    'deepseek': {
        'base_url': 'https://api.deepseek.com/v1',
        'models': ['deepseek-chat'],
    },
    'together': {
        'base_url': 'https://api.together.xyz/v1',
        'models': [],
    },
}


def resolve_preset(name):
    p = PROVIDER_PRESETS.get((name or '').lower())
    if not p:
        return None
    return {'base_url': p['base_url'],
            'models': list(p['models'])}


def fetch_models(base_url, api_key, timeout=30):
    req = urllib.request.Request(
        base_url.rstrip('/') + '/models',
        headers={'Authorization': f'Bearer {api_key}',
                 'User-Agent': 'Mozilla/5.0'})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as e:
        return {'ok': False, 'error': str(e)[:120], 'models': []}
    ids = sorted({m.get('id') or m.get('name') or ''
                  for m in r.get('data', []) if m.get('id') or m.get('name')})
    return {'ok': True, 'models': ids}


class Provider:
    def __init__(self, base_url, model, keys):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.keys = []
        for k in keys:
            if isinstance(k, dict):
                self.keys.append(k.get('value') or '')
            elif k:
                self.keys.append(str(k))
        self.keys = [k for k in self.keys if k]
        self.banned = {k: 0.0 for k in self.keys}
        self.cursor = 0

    def available(self):
        now = time.time()
        return [k for k in self.keys
                if self.banned.get(k, 0) < now] or self.keys

    def ban(self, key, seconds=300):
        self.banned[key] = time.time() + seconds

    def next_key(self):
        avail = self.available()
        if not avail:
            return None
        k = avail[self.cursor % len(avail)]
        self.cursor += 1
        return k


class KeyPool:
    def __init__(self, providers=None):
        self.providers = []
        for p in providers or []:
            self.providers.append(
                Provider(p.get('base_url'), p.get('model'),
                         p.get('keys') or []))
        self.cursor = 0

    def add(self, base_url, model, keys):
        self.providers.append(Provider(base_url, model, keys))

    def load(self, path, default_model=None):
        data = json.load(open(path))
        if isinstance(data, dict):
            data = (data.get('providers') or data.get('keys') or [])
        groups = {}
        for e in data:
            if not isinstance(e, dict):
                continue
            url = e.get('base_url') or e.get('url')
            mdl = e.get('model') or default_model
            preset = e.get('preset') or e.get('provider')
            if not url and preset and resolve_preset(preset):
                url = resolve_preset(preset)['base_url']
            if not mdl and preset and resolve_preset(preset):
                models = resolve_preset(preset)['models']
                mdl = models[0] if models else None
            if e.get('keys') is not None and url:
                keys = []
                for k in e.get('keys', []):
                    if isinstance(k, dict):
                        if k.get('status') in (None, '', 'valid') and k.get('value'):
                            keys.append(k['value'])
                    elif k:
                        keys.append(str(k))
                if keys:
                    self.add(url, mdl, keys)
                continue
            k = (e.get('value') or '').strip()
            if not k or e.get('status') not in (None, '', 'valid'):
                continue
            groups.setdefault((url, mdl), []).append(k)
        for (url, model), keys in groups.items():
            if url:
                self.add(url, model, keys)
        return self

    def pick(self):
        alive = [p for p in self.providers if p.keys]
        if not alive:
            return None
        p = alive[self.cursor % len(alive)]
        self.cursor += 1
        k = p.next_key()
        if not k:
            for q in alive:
                k = q.next_key()
                if k:
                    return (q, k)
            return None
        return (p, k)

    def report(self, provider, key, status_code):
        if status_code == 429 or status_code >= 500 or status_code in (0, 408):
            provider.ban(key, 300)
            return 'rate-limited'
        if 400 <= status_code < 500:
            provider.ban(key, 3600)
            return 'invalid'
        return 'ok'


def pool_request(pool, messages, max_tokens=800, timeout=120,
                 retries=None):
    tries = retries if retries is not None else max(
        1, sum(len(p.keys) for p in pool.providers))
    last = None
    for _ in range(tries):
        pk = pool.pick()
        if not pk:
            raise RuntimeError('no available keys in pool')
        p, k = pk
        content = [{'type': 'text',
                    'text': json.dumps(messages, ensure_ascii=False)
                    if isinstance(messages, (dict, list)) else str(messages)}]
        for m in messages if isinstance(messages, list) else []:
            if isinstance(m, dict):
                for b64 in m.get('images') or []:
                    content.append({'type': 'image_url',
                                    'image_url': {'url':
                                    f'data:image/png;base64,{b64}'}})
        body = json.dumps({'model': p.model, 'max_tokens': max_tokens,
                           'temperature': 0,
                           'messages': [{'role': 'user',
                                         'content': content}]}).encode()
        req = urllib.request.Request(
            p.base_url + '/chat/completions', data=body,
            headers={'Authorization': f'Bearer {k}',
                     'Content-Type': 'application/json',
                     'User-Agent': 'Mozilla/5.0'})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=timeout))
            return r['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            pool.report(p, k, e.code)
            last = e
        except Exception as e:
            pool.report(p, k, 0)
            last = e
    raise RuntimeError(f'all keys failed: {last}'[:200])
