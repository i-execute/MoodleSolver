import argparse
import asyncio
import json
from mcp.server.fastmcp import FastMCP
from .client import AsyncMoodle
from .quizapi import QuizAPI
from .airunner import AIRunner
mcp = FastMCP('moodlesolver')
_state = {}

def _qapi():
    if 'qapi' not in _state:
        _state['qapi'] = QuizAPI(_state['m'])
    return _state['qapi']

@mcp.tool()
async def connect(site: str, username: str, password: str,
                  api_key: str = '', base_url: str = '',
                  model: str = '') -> str:
    _state['m'] = AsyncMoodle(site=site, user=username.split('@')[0])
    await _state['m'].open_session(username, password)
    if api_key:
        _state['key'] = api_key
    if base_url:
        _state['base_url'] = base_url
    if model:
        _state['model'] = model
    return 'connected'

@mcp.tool()
async def get_quiz(quiz_id: int) -> str:
    return json.dumps(await _qapi().get_quiz_info(quiz_id), ensure_ascii=False)

@mcp.tool()
async def start_attempt(quiz_id: int) -> str:
    return json.dumps({'attempt_id': await _qapi().start_attempt(quiz_id)})

@mcp.tool()
async def get_state() -> str:
    return json.dumps(await _qapi().get_state(), ensure_ascii=False)

@mcp.tool()
async def see_image(file: str, prompt: str='') -> str:
    from .vision import ocr_file
    dumps_dir = getattr(_state.get('m'), 'dumps_dir', None)
    return await asyncio.to_thread(ocr_file, file, prompt or None, dumps_dir)

@mcp.tool()
async def get_courses() -> str:
    from .ctx import CtxAPI
    return json.dumps(await CtxAPI(_state['m']).get_courses(), ensure_ascii=False)

@mcp.tool()
async def get_quizzes(course_id: int) -> str:
    from .ctx import CtxAPI
    return json.dumps(await CtxAPI(_state['m']).get_quizzes(course_id), ensure_ascii=False)

@mcp.tool()
async def get_course_sections(course_id: int) -> str:
    from .ctx import CtxAPI
    return json.dumps(await CtxAPI(_state['m']).get_course_sections(course_id), ensure_ascii=False)

@mcp.tool()
async def get_resources(course_id: int) -> str:
    from .ctx import CtxAPI
    return json.dumps(await CtxAPI(_state['m']).get_resources(course_id), ensure_ascii=False)

@mcp.tool()
async def download_resource(cmid: int, path: str='') -> str:
    from .ctx import CtxAPI
    return json.dumps(await CtxAPI(_state['m']).download_resource(cmid, path or None))

@mcp.tool()
async def search_courses(query: str) -> str:
    from .ctx import CtxAPI
    return json.dumps(await CtxAPI(_state['m']).search_courses(query), ensure_ascii=False)

@mcp.tool()
async def get_course(course_id: int) -> str:
    from .ctx import CtxAPI
    return json.dumps(await CtxAPI(_state['m']).get_course(course_id), ensure_ascii=False)

@mcp.tool()
async def dump_course(course_id: int, with_resources: bool=False) -> str:
    from .ctx import CtxAPI
    return json.dumps(await CtxAPI(_state['m']).dump_course(course_id, with_resources=with_resources))

@mcp.tool()
async def download_images(course_id: int, out_dir: str='/tmp/moodle_imgs') -> str:
    from .ctx import CtxAPI
    return json.dumps(await CtxAPI(_state['m']).download_images(course_id, out_dir))

@mcp.tool()
async def quiz_command(command: str) -> str:
    from .jsonapi import JsonAPI
    j = JsonAPI(_qapi())
    return json.dumps(await j.handle(command), ensure_ascii=False)

@mcp.tool()
async def resume_attempt(quiz_id: int) -> str:
    from .ctx import CtxAPI
    r = await CtxAPI(_state['m']).resume_attempt(quiz_id)
    r.pop('tab', None)
    return json.dumps(r, ensure_ascii=False)

@mcp.tool()
async def get_options() -> str:
    return json.dumps(await _qapi().get_options(), ensure_ascii=False)

@mcp.tool()
async def set_answer(label: str='', index: int=-1) -> str:
    return json.dumps(await _qapi().set_answer(label=label or None, index=None if index < 0 else index))

@mcp.tool()
async def set_text(text: str, index: int=0) -> str:
    return json.dumps({'ok': await _qapi().set_text(text, index)})

@mcp.tool()
async def set_select(value: str, index: int=0) -> str:
    return json.dumps({'ok': await _qapi().set_select(value, index)})

@mcp.tool()
async def next_page() -> str:
    return await _qapi().next_page()

@mcp.tool()
async def submit_all() -> str:
    return await _qapi().submit_all()

@mcp.tool()
async def get_result(quiz_id: int) -> str:
    return await _qapi().get_result(quiz_id)

@mcp.tool()
async def send_ai_request(prompt: str, api_key: str='', base_url: str='', model: str='', max_tokens: int=400) -> str:
    r = AIRunner(api_key or _state.get('key'), base_url or None, model or None)
    return r.send_ai_request(prompt, max_tokens=max_tokens)

@mcp.tool()
async def receive_ai_response(text: str) -> str:
    return json.dumps(AIRunner().receive_ai_response(text))

@mcp.tool()
async def execute_ai_response(response: str) -> str:
    return json.dumps(await AIRunner().bind(_qapi()).execute_ai_response(json.loads(response)))

@mcp.tool()
async def solve_test(quiz_id: int, mode: str = 'openai',
                     api_key: str = '', base_url: str = '',
                     model: str = '') -> str:
    """Решает тест полностью через AI. Требуется connect перед вызовом.
    mode: 'openai' (OpenAI-совместимый endpoint, нужны api_key+base_url)
    или 'opencode' (локальный opencode CLI, модель mimo по умолчанию).
    Возможные ошибки: SESSION_NOT_AUTHORIZED (перелогинься через connect),
    SESSION_EMPTY, ATTEMPT_NOT_FOUND."""
    key = api_key or _state.get('key')
    url = base_url or _state.get('base_url')
    mdl = model or _state.get('model')
    r = AIRunner(mode=mode, api_key=key, base_url=url,
                 model=mdl).bind(_qapi())
    try:
        res = await r.loop(quiz_id)
    except Exception as e:
        res = {'error': 'AI_ERROR', 'message': str(e)[:200]}
    return json.dumps(res, ensure_ascii=False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stdio', action='store_true', default=True)
    mcp.run()
if __name__ == '__main__':
    main()
