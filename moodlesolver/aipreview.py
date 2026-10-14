import shutil
import subprocess

OC_DEFAULT_MODEL = "opencode/mimo-v2.6-flash-free"


def opencode_available():
    return shutil.which("opencode") is not None


def ai_preview(paths, prompt="Опиши что на картинке, весь текст перепиши",
               model=OC_DEFAULT_MODEL, timeout=120):
    if not opencode_available() or not paths:
        return None
    cmd = ["opencode", "run", prompt, "--model", model]
    for p in paths:
        cmd += ["-f", p]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout).stdout.strip()
    except Exception:
        return None
    if not out or "Error" in out.splitlines()[0]:
        return None
    lines = [ln for ln in out.splitlines() if not ln.startswith(">")]
    return "\n".join(lines).strip() or None
