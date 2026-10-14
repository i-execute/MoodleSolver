import re
ANSWER_TYPES = ('radio', 'checkbox', 'combobox', 'textbox')

def parse_page(snapshot):
    q = {'type': 'unknown'}
    m = re.search('heading "Вопрос (\\d+)"', snapshot)
    if not m:
        m = re.search('heading "Question (\\d+)"', snapshot)
    q['number'] = int(m.group(1)) if m else None
    m = re.search('Балл:\\s*([\\d,]+)', snapshot)
    if not m:
        m = re.search('Mark[s]?\\s*([\\d,]+)', snapshot)
    q['points'] = m.group(1) if m else None
    m = re.search('heading "Текст вопроса".*?\\n.*?paragraph: (.+)', snapshot)
    if not m:
        m = re.search('heading "Question text".*?\\n.*?paragraph: (.+)', snapshot)
    q['text'] = m.group(1).strip() if m else None
    opts = []
    for m in re.finditer('(radio|checkbox) "([^"]+)" \\[e(\\d+)\\]([^\\n]*)', snapshot):
        opts.append({'ref': 'e' + m.group(3), 'label': m.group(2), 'type': m.group(1), 'checked': 'checked' in m.group(4)})
    q['options'] = opts
    q['selects'] = [{'ref': 'e' + r, 'current': l} for l, r in re.findall('combobox[^"\\n]*"([^"]+)" \\[e(\\d+)\\]', snapshot)]
    q['textboxes'] = [{'ref': 'e' + r, 'label': l} for l, r in re.findall('textbox "([^"]+)" \\[e(\\d+)\\]', snapshot)]
    m = re.search('button "(Следующая страница|Завершить попытку[^"]*|Next page|Finish attempt[^"]*)"[^\\[]*\\[e(\\d+)\\]', snapshot)
    q['next_ref'] = 'e' + m.group(2) if m else None
    q['next_label'] = m.group(1) if m else None
    m = re.search('Оставшееся время (\\d+):(\\d+):(\\d+)', snapshot)
    if not m:
        m = re.search('Time remaining (\\d+):(\\d+):(\\d+)', snapshot)
    q['timer'] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None
    q['raw'] = snapshot
    q['multi'] = any((o['type'] == 'checkbox' for o in opts))
    q['has_image'] = bool(q['text'] and ('picture' in q['text'].lower() or 'картинк' in q['text'].lower()))
    return q
