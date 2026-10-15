import os
import shutil
import subprocess
import threading
DEFAULT_PROMPT = 'Транскрибируй формулу. Только формула.'
PROMPT_RU = 'Перепиши весь русский текст с картинки точно как есть.'
PROMPT_EN = 'Write all English text from the image exactly as is.'
PROMPT_DESC = ('Опиши подробно что на картинке: объекты, текст, '
               'таблицы, графики, диаграммы, условия задач.')
PROMPT_FORMULA = 'Транскрибируй формулу в LaTeX. Только формула.'
PROMPT_TABLE = ('Перепиши таблицу с картинки в markdown, '
                'все числа точно.')

def ocr_local(path, lang='rus+eng', psm=None, timeout=20):
    if not shutil.which('tesseract'):
        return None
    cmd = ['tesseract', path, 'stdout', '-l', lang]
    if psm:
        cmd += ['--psm', str(psm)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return None
    return out or None
_FORMULA_MODEL = None
_FORMULA_LOCK = threading.Lock()

def _formula_model():
    global _FORMULA_MODEL
    with _FORMULA_LOCK:
        if _FORMULA_MODEL is None:
            from pix2tex.cli import LatexOCR
            _FORMULA_MODEL = LatexOCR()
    return _FORMULA_MODEL

def ocr_formula(path, timeout=40):
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image

    def _run():
        return _formula_model()(Image.open(path))
    try:
        with ThreadPoolExecutor(1) as ex:
            return ex.submit(_run).result(timeout=timeout) or None
    except Exception:
        return None

def _resolve(path, dumps_dir=None):
    p = path
    if not os.path.exists(p) and dumps_dir:
        p = os.path.join(dumps_dir, path)
    if not os.path.exists(p):
        c = os.path.join('/tmp', path)
        if os.path.exists(c):
            p = c
    if os.path.exists(p) and p.endswith('.b64'):
        import base64
        try:
            raw = open(p, 'rb').read()
            data = base64.b64decode(raw)
            if data[:4] == b'\x89PNG' or data[:2] == b'\xff\xd8':
                png = '/tmp/ocr_' + os.path.basename(p)[:-4] + '.png'
                with open(png, 'wb') as f:
                    f.write(data)
                return png
        except Exception:
            pass
    return p

def ocr_file(path, prompt=None, dumps_dir=None):
    p = _resolve(path, dumps_dir)
    pr = (prompt or '').lower()
    if 'русск' in pr:
        return ocr_local(p, lang='rus')
    if 'english' in pr or 'англ' in pr:
        return ocr_local(p, lang='eng')
    if 'таблиц' in pr or 'table' in pr:
        t = ocr_local(p)
        if t:
            return t
    if 'формул' in pr or 'latex' in pr or not prompt:
        f = ocr_formula(p)
        if f:
            return f
    if 'описан' in pr or 'опис' in pr or 'describe' in pr:
        return ocr_local(p)
    return ocr_local(p)

def ocr_full(path, dumps_dir=None):
    p = _resolve(path, dumps_dir)
    ru = ocr_local(p, lang='rus')
    en = ocr_local(p, lang='eng')
    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(1) as ex:
            formula = ex.submit(lambda: ocr_formula(p)).result(timeout=40)
    except Exception:
        formula = None
    return {'ru': ru, 'en': en, 'formula': formula}

def make_on_ocr(dumps_dir=None):

    def cb(file, prompt=None):
        return ocr_file(file, prompt, dumps_dir)
    return cb
