import asyncio
import json
import re

class CtxAPI:

    def __init__(self, moodle):
        self.m = moodle

    async def _tab_for(self, url):
        await self.m.close_stale(keep=url)
        t = await self.m.pick(url) or await self.m.new_tab(f'{self.m.site}/{url}')
        await asyncio.sleep(3)
        return t

    async def get_courses(self):
        t = await self._tab_for('my/')
        r = await t.evaluate("JSON.stringify([...document.querySelectorAll('a[href*=course]')].map(a => ({cid: ((a.href.match(/id=(\\d+)/) || [])[1]) || '',name: a.textContent.trim()})).filter(x => x.name))")
        rows = json.loads(r.get('result') or '[]')
        seen, out = (set(), [])
        for x in rows:
            if x['cid'] and x['cid'] not in seen:
                seen.add(x['cid'])
                out.append({'id': int(x['cid']), 'name': x['name'][:80]})
        return out

    async def get_course_sections(self, course_id):
        t = await self._tab_for(f'course/view.php?id={course_id}')
        r = await t.evaluate("JSON.stringify([...document.querySelectorAll('.course-section, li.section')].map(s => ({name: (s.querySelector('.sectionname, h3') || {textContent: ''}).textContent.trim(),items: [...s.querySelectorAll('a[href*=mod], a[href*=resource]')].map(a => ({href: a.href,cmid: ((a.href.match(/id=(\\d+)/) || [])[1]) || '',name: (a.textContent || '').trim()}))})))")
        return json.loads(r.get('result') or '[]')

    async def get_quizzes(self, course_id):
        t = await self._tab_for(f'course/view.php?id={course_id}')
        r = await t.evaluate('JSON.stringify([...document.querySelectorAll(\'a[href*="mod/quiz"]\')].map(a => ({cmid: ((a.href.match(/id=(\\d+)/) || [])[1]) || \'\',name: (a.textContent || \'\').trim()})).filter(x => x.cmid))')
        rows = json.loads(r.get('result') or '[]')
        seen, out = (set(), [])
        for x in rows:
            if x['cmid'] and x['cmid'] not in seen:
                seen.add(x['cmid'])
                out.append({'cmid': int(x['cmid']), 'name': x['name'][:80]})
        return out

    async def get_resources(self, course_id):
        t = await self._tab_for(f'course/view.php?id={course_id}')
        r = await t.evaluate('JSON.stringify([...document.querySelectorAll(\'a[href*="mod/resource"]\')].map(a => ({cmid: ((a.href.match(/id=(\\d+)/) || [])[1]) || \'\',name: (a.textContent || \'\').trim(),href: a.href})).filter(x => x.cmid))')
        rows = json.loads(r.get('result') or '[]')
        seen, out = (set(), [])
        for x in rows:
            if x['cmid'] and x['cmid'] not in seen:
                seen.add(x['cmid'])
                out.append({'cmid': int(x['cmid']), 'name': x['name'][:80]})
        return out

    async def resume_attempt(self, quiz_id):
        await self.m.close_stale(keep=f'view.php?id={quiz_id}')
        t = await self.m.pick(f'view.php?id={quiz_id}') or await self.m.new_tab(f'{self.m.site}/mod/quiz/view.php?id={quiz_id}')
        await asyncio.sleep(2)
        r = await t.evaluate("(async () => { const b = [...document.querySelectorAll('button, input[type=submit]')].find(x => (x.textContent || x.value || '').includes('Продолжить')); if (b) { b.closest('form').submit(); return 'submitted'; } return document.querySelector('[role=main]') ? document.querySelector('[role=main]').innerText .slice(0, 200) : 'nobtn'; })()")
        res = r.get('result') or ''
        if res != 'submitted':
            return {'error': 'no live attempt', 'page': res[:200]}
        for _ in range(12):
            await asyncio.sleep(1)
            u = await t.url()
            if 'attempt.php' in u and '[object' not in u:
                return {'ok': True, 'url': u, 'tab': t.id}
        return {'error': 'reanimate failed', 'url': await t.url()}

    async def download_resource(self, cmid, path=None):
        t = await self._tab_for(f'mod/resource/view.php?id={cmid}')
        await asyncio.sleep(2)
        r = await t.evaluate("(async () => { let a = document.querySelector('.resourceworkaround a[href*=pluginfile]') || document.querySelector('a[href*=pluginfile]') || document.querySelector('object[data*=pluginfile], embed[src*=pluginfile]'); const u = a ? (a.href || a.data || a.src) : null; if (!u) return JSON.stringify({error: 'no file url'}); const resp = await fetch(u); const b = await resp.blob(); const b64 = await new Promise(rr => { const fr = new FileReader(); fr.onload = () => rr(fr.result.split(',')[1]); fr.readAsDataURL(b); }); return JSON.stringify({b64}); })()")
        d = json.loads(r.get('result') or '{}')
        if d.get('error'):
            return d
        import base64
        p = path or f'resource_{cmid}.bin'
        open(p, 'wb').write(base64.b64decode(d['b64']))
        return {'file': p, 'bytes': len(d['b64']) * 3 // 4}

    async def get_course(self, course_id):
        secs = await self.get_course_sections(course_id)
        quizzes = await self.get_quizzes(course_id)
        resources = await self.get_resources(course_id)
        t = await self.m.pick(f'course/view.php?id={course_id}')
        name = ''
        if t:
            r = await t.evaluate("document.querySelector('h1, .course-title') ? document.querySelector('h1').textContent.trim() : ''")
            name = r.get('result') or ''
        return {'id': course_id, 'name': name, 'sections': secs, 'quizzes': quizzes, 'resources': resources, 'quiz_ids': [q['cmid'] for q in quizzes], 'resource_ids': [x['cmid'] for x in resources]}

    async def dump_course(self, course_id, path=None, with_resources=False):
        import os
        d = await self.get_course(course_id)
        d['files'] = []
        if with_resources:
            for r in d['resources']:
                p = f"res_{r['cmid']}.bin"
                try:
                    got = await self.download_resource(r['cmid'], p)
                    if got.get('file'):
                        d['files'].append(got['file'])
                except Exception as e:
                    d['files'].append("error:%s:%s" % (r['cmid'], str(e)[:40]))
        p = path or f'course_{course_id}.json'
        with open(p, 'w') as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        return {'file': p, 'quizzes': len(d['quizzes']), 'resources': len(d['resources']), 'downloaded': len(d['files'])}

    async def download_images(self, course_id, out_dir='/tmp/moodle_imgs'):
        import base64
        import os
        t = await self._tab_for(f'course/view.php?id={course_id}')
        r = await t.evaluate("(async () => { const ims = [...document.querySelectorAll('.course-content img')]; const out = []; for (const im of ims) { await new Promise(rr => im.complete ? rr() : im.onload = rr); const c = document.createElement('canvas'); c.width = im.naturalWidth; c.height = im.naturalHeight; c.getContext('2d').drawImage(im, 0, 0); out.push(c.toDataURL('image/png').split(',')[1]); } return JSON.stringify(out); })()")
        arr = json.loads(r.get('result') or '[]')
        os.makedirs(out_dir, exist_ok=True)
        files = []
        for i, b in enumerate(arr):
            p = f'{out_dir}/img_{i}.png'
            with open(p, 'wb') as f:
                f.write(base64.b64decode(b))
            files.append(p)
        return {'files': files, 'count': len(files)}

    async def search_courses(self, query):
        return [c for c in await self.get_courses() if query.lower() in c['name'].lower()]

    async def get_guide(self, quiz_cmid, path=None):
        return await self._get_guide(quiz_cmid, path)

    async def _get_guide(self, quiz_cmid, path=None):
        for course in await self.get_courses():
            secs = await self.get_course_sections(course['id'])
            for s in secs:
                names = [i for i in s.get('items', [])]
                quiz = next((i for i in names if i.get('cmid') == str(quiz_cmid)), None)
                if not quiz:
                    continue
                import re as _re
                m = _re.search(r'(\d+)', quiz.get('name') or '')
                num = m.group(1) if m else ''
                guide = next((i for i in names
                              if 'етодичка' in (i.get('name') or '')
                              and num and num in (i.get('name') or '')), None)
                if guide:
                    gm = _re.search(r'id=(\d+)', guide.get('href') or '')
                    if gm:
                        p = path or f'guide_{num}.pdf'
                        r = await self.download_resource(int(gm.group(1)), p)
                        return {'ok': True, 'quiz': quiz.get('name'),
                                'guide': guide.get('name'),
                                'cmid': int(gm.group(1)), 'file': p,
                                'bytes': r.get('bytes')}
        return {'error': 'guide not found'}
