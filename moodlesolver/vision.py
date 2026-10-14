import os
import shutil
import subprocess
import threading
DEFAULT_PROMPT = 'Транскрибируй формулу. Только формула.'

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
        for root in ('/tmp',):
            c = os.path.join(root, path)
            if os.path.exists(c):
                return c
    return p

def ocr_file(path, prompt=None, dumps_dir=None):
    p = _resolve(path, dumps_dir)
    pr = (prompt or '').lower()
    if 'русск' in pr:
        return ocr_local(p, lang='rus')
    if 'english' in pr or 'англ' in pr:
        return ocr_local(p, lang='eng')
    if 'формул' in pr or not prompt:
        f = ocr_formula(p)
        if f:
            return f
    return ocr_local(p)

def make_on_ocr(dumps_dir=None):

    def cb(file, prompt=None):
        return ocr_file(file, prompt, dumps_dir)
    return cb
