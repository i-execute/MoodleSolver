# moodlesolver

Асинхронная Python-библиотека и MCP-сервер для автоматизации прохождения тестов на платформе Moodle через браузерный API (camofox).

## Возможности

- **Авторизация и сессии** — вход по логину/паролю, реанимация попытки после обрыва (`resume_attempt`), коды ошибок сессии (`SESSION_NOT_AUTHORIZED`, `SESSION_EMPTY`, `ATTEMPT_NOT_FOUND`)
- **Навигация по курсам** — список курсов, секций, ресурсов, тестов; полная выгрузка курса в JSON; скачивание файлов и картинок; поиск методички по номеру теста (`get_guide`)
- **Прохождение тестов** — все типы вопросов: одиночный выбор, множественный (чекбоксы), выпадающие списки (в т.ч. составление предложений из N комбобоксов с контролем заполненности), ввод текста, вопросы с картинками и формулами
- **JSON-протокол** — `JsonAPI` и MCP-тул `quiz_command` принимают команды JSON и возвращают состояние вопроса: текст, варианты, что реально отмечено, остаток таймера
- **OCR без внешних API** — локально: tesseract (текст ru/en) и pix2tex (формулы LaTeX); параллельно, результаты в том же JSON; промпт-роутинг (формулы/русский/английский/таблицы/описания)
- **AI-режимы (опционально)** — `off` (по умолчанию, только локальный OCR), `opencode` (локальный opencode CLI, дефолт mimo), `openai` (любой OpenAI-совместимый endpoint: свой ключ, base_url, модель)
- **Картинки в ответе** — base64 или HTTP-ссылки (встроенный файл-сервер `serve_dumps`)
- **Коды ошибок** — структурированные `E_*` вместо тихих падений; `E_SELECTS_INCOMPLETE` — не все комбобоксы заполнены
- **MCP-сервер** — 28 инструментов для подключения любой LLM-моделью, включая `solve_test` — полный автопрогон теста одной командой

## Установка

```bash
pip install moodlesolver
```

Зависимости для локального OCR (опционально, рекомендуются):

```bash
apt-get install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng
pip install moodlesolver[ocr]
```

Требуется запущенный camofox-browser (API по умолчанию `http://localhost:9377`).

## Быстрый старт

```python
import asyncio
from moodlesolver import AsyncMoodle, QuizAPI, JsonAPI

async def main():
    m = AsyncMoodle(site="https://moodle.example.com")
    await m.open_session("login", "password")

    from moodlesolver import CtxAPI
    ctx = CtxAPI(m)
    quizzes = await ctx.get_quizzes(42)

    q = QuizAPI(m)
    api = JsonAPI(q)

    st = await api.handle({"action": "state"})
    print(st["question"], st["options"], st["timeleft"])

    await api.handle({"action": "select", "answers": [{"index": 2}]})
    st = await api.handle({"action": "confirm"})
    print(st["question"])

    await api.handle({"action": "submit"})
    print(await api.handle({"action": "result"}))

asyncio.run(main())
```

## JSON-протокол

Каждый ответ содержит: `page`, `question`, `question_hash`, `qtype`, `options`, `checked` (что реально отмечено на сервере), `selects`, `text_inputs`, `timeleft`, `buttons`, а также:

- `question_image_b64` / `option_images_b64` — картинки в base64 (или ссылки при `image_mode="url"`)
- `ocr` — локальное превью каждой картинки: `{"formula": "…", "ru": "…", "en": "…"}`, а при включённом AI-режиме ещё `ai` — описание картинок vision-моделью

```python
api = JsonAPI(q, ai_mode="openai", ai_model="...",
              ai_api_key="...", ai_base_url="https://.../v1")
api = JsonAPI(q, ai_mode="opencode")
api = JsonAPI(q, ai_mode="off")
```

Режимы `ai_mode`: `off` — только локальный tesseract+pix2tex; `opencode` — локальный opencode CLI (модель mimo по умолчанию); `openai` — любой OpenAI-совместимый endpoint (нужны `ai_api_key` и `ai_base_url`).

Подробная схема: `moodlesolver/schema/quiz_protocol.json`.

## MCP-сервер

```json
{
  "mcpServers": {
    "moodlesolver": {
      "command": "python",
      "args": ["-m", "moodlesolver.mcp_server"]
    }
  }
}
```

Инструменты: connect (с опциональными api_key/base_url/model), get_courses, get_quizzes, get_course, get_course_sections, get_resources, download_resource, dump_course, download_images, search_courses, get_quiz, start_attempt, resume_attempt, get_state, get_options, see_image, set_answer, set_text, set_select, next_page, submit_all, get_result, quiz_command, send_ai_request, receive_ai_response, execute_ai_response, solve_test и другие.

`solve_test(quiz_id, mode, api_key, base_url, model)` — полный автопрогон теста: AIRunner читает вопросы, решает через AI, проставляет ответы, проверяет заполненность комбобоксов, сабмитит и возвращает балл.

## AIRunner

Полный автопрогон теста одним процессом с решением через внешнюю LLM:

```python
from moodlesolver import AIRunner

r = AIRunner(mode="openai", api_key="...",
             base_url="https://api.example.com/v1",
             model="model-name").bind(q)
await r.loop(quiz_id)
```

Или через локальный opencode:

```python
r = AIRunner(mode="opencode").bind(q)
await r.loop(quiz_id)
```

## Лицензия

MIT
