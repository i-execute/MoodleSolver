# moodlesolver

Асинхронная Python-библиотека и MCP-сервер для автоматизации прохождения тестов на платформе Moodle через браузерный API (camofox).

## Возможности

- **Авторизация и сессии** — вход по логину/паролю, реанимация попытки после обрыва (`resume_attempt`)
- **Навигация по курсам** — список курсов, секций, ресурсов, тестов; полная выгрузка курса в JSON; скачивание файлов и картинок
- **Прохождение тестов** — все типы вопросов: одиночный выбор, множественный (чекбоксы), выпадающие списки, ввод текста, вопросы с картинками и формулами
- **JSON-протокол** — `JsonAPI` и MCP-тул `quiz_command` принимают команды JSON и возвращают состояние вопроса: текст, варианты, что реально отмечено, остаток таймера
- **OCR без внешних API** — локально: tesseract (текст ru/en) и pix2tex (формулы LaTeX); параллельно, результаты в том же JSON
- **Картинки в ответе** — base64 или HTTP-ссылки (встроенный файл-сервер `serve_dumps`)
- **Коды ошибок** — структурированные `E_*` вместо тихих падений
- **MCP-сервер** — 27 инструментов для подключения любой LLM-моделью

## Установка

```bash
pip install moodlesolver
```

Зависимости для OCR (опционально, но рекомендуются):

```bash
apt-get install tesseract-ocr tesseract-ocr-rus
pip install pix2tex
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
- `ocr` — локальное превью каждой картинки: `{"formula": "…", "ru": "…", "en": "…"}`, а при `ai_preview=True` ещё `ai_preview` — описание картинок vision-моделью OpenCode (mimo-v2.6-flash-free по умолчанию)

```python
api = JsonAPI(q, ai_preview=True)          # включить AI-описание картинок
api = JsonAPI(q, ai_preview=True, ai_model="opencode/longcat-2.5-preview-free")
```

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

Инструменты: connect, get_courses, get_quizzes, get_course, dump_course, download_images, download_resource, search_courses, resume_attempt, start_attempt, get_state, get_options, get_images, set_answer, set_text, set_select, next_page, submit_all, get_result, quiz_command, see_image и другие.

## AIRunner

Полный автопрогон теста одним процессом с решением через внешнюю LLM:

```python
from moodlesolver import AIRunner

r = AIRunner(api_key="...", base_url="https://api.example.com/v1",
             model="model-name").bind(q)
await r.loop(quiz_id)
```

## Лицензия

MIT
