import asyncio
import json


class HealthWatcher:
    def __init__(self, jsonapi, quiz_id, poll=5.0, max_dead=6):
        self.j = jsonapi
        self.quiz_id = quiz_id
        self.poll = poll
        self.max_dead = max_dead
        self.dead = 0
        self.last_state = None
        self.last_answer = None
        self.reconnects = 0
        self._task = None
        self._stop = False

    def remember(self, state, answer=None):
        self.last_state = state
        self.last_answer = answer

    async def check(self):
        st = await self.j.handle({"action": "state",
                                  "quiz_id": self.quiz_id})
        if st.get("error") or not st.get("url"):
            self.dead += 1
        else:
            self.dead = 0
            self.last_state = st
        return st

    async def reconnect(self):
        self.reconnects += 1
        m = self.j.q.m
        rl = {}
        try:
            rl = await m.ensure_login()
        except Exception as e:
            rl = {'error': str(e)[:80]}
        from .ctx import CtxAPI
        try:
            await CtxAPI(m).resume_attempt(self.quiz_id)
        except Exception as e:
            try:
                st = await self.j.handle(
                    {'action': 'state', 'quiz_id': self.quiz_id})
                if not st.get('error'):
                    return st
            except Exception:
                pass
            st = {'error': 'reconnect failed: ' + str(e)[:100],
                  'relogin': rl}
            self.last_notice = (
                'Сайт был недоступен. Последние изменения могли не '
                'сохраниться. Релогин: ' + json.dumps(
                    rl, ensure_ascii=False) + '. Проверь состояние '
                'checked и отвечай текущий вопрос.')
            return st
        try:
            st = await self.j.handle({'action': 'state',
                                      'quiz_id': self.quiz_id})
        except Exception as e:
            st = {'error': 'state after reconnect: ' + str(e)[:100],
                  'relogin': rl}
            return st
        self.last_notice = (
            'Сайт был недоступен. Последние изменения могли не '
            'сохраниться. Релогин: ' + json.dumps(
                rl, ensure_ascii=False) + '. Проверь состояние '
            'checked и отвечай текущий вопрос.')
        return st

    def notice(self):
        if self.last_state is None:
            return None
        return json.dumps({
            "event": "RECONNECTED",
            "warning": "site died, last changes may not be saved",
            "last_seen_page": self.last_state.get("page"),
            "last_seen_question": (self.last_state.get("question")
                                   or "")[:200],
            "last_answer": self.last_answer,
            "current": True,
        }, ensure_ascii=False)

    async def run(self):
        while not self._stop:
            await asyncio.sleep(self.poll)
            try:
                await self.check()
            except Exception:
                self.dead += 1
            if self.dead >= self.max_dead:
                try:
                    await self.reconnect()
                except Exception:
                    pass

    def start(self):
        self._task = asyncio.get_event_loop().create_task(self.run())

    def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()
