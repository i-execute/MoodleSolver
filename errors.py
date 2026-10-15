ERRORS = {'E_LOGIN': 'login failed / session killed', 'E_NO_TAB': 'no usable browser tab (all dead or [object Object])', 'E_DEAD_TAB': 'tab went to /mod/quiz/[object Object] 404 — use resume', 'E_NO_ATTEMPT': 'quiz has no live attempt; start_attempt first', 'E_NO_SUMMARY': 'submit_all could not reach summary page', 'E_NOTHING_CHECKED': 'confirm called but nothing selected', 'E_NO_MATCH': 'label did not match any option (nbsp/zero-width?)', 'E_NO_INPUT': 'text input index out of range', 'E_NO_OPTION': 'select value not in dropdown options', 'E_NO_IMG': 'image grab failed (canvas tainted or incomplete)', 'E_OCR': 'vision LLM failed / timeout on image', 'E_QUIZ_CLOSED': 'no attempts left', 'E_UNKNOWN_ACTION': 'protocol action not recognized'}

def err(code, **kw):
    d = {'error': ERRORS.get(code, code), 'code': code}
    d.update(kw)
    return d
