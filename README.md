# TubeSave

Приложение для скачивания видео и Shorts с YouTube в **MP4** — максимальное доступное качество, звук AAC, со встроенным превью.

## Скачать

Готовые сборки (Windows):

| Файл | Описание |
|------|----------|
| [TubeSave-Windows.zip](https://github.com/mine1510/TubeSave/releases/latest/download/TubeSave-Windows.zip) | ZIP-архив |
| [TubeSave-Windows.rar](https://github.com/mine1510/TubeSave/releases/latest/download/TubeSave-Windows.rar) | RAR-архив |

Страница релизов: [Releases](https://github.com/mine1510/TubeSave/releases)

Также архивы лежат в папке [`download/`](./download/) репозитория.

## Как пользоваться

1. Распакуйте архив.
2. Запустите `TubeSave.exe`.
3. Вставьте ссылку на видео или Shorts.
4. Выберите папку сохранения (запоминается).
5. Нажмите **Скачать**.

Поддерживаемые ссылки:

- `https://www.youtube.com/watch?v=...`
- `https://www.youtube.com/shorts/...`
- `https://youtu.be/...`

## Возможности

- Обычные видео и YouTube Shorts
- MP4 без лишней перекодировки (H.264 + AAC, когда доступно)
- Превью ролика вшивается в файл
- Статусы прогресса, скорость, ETA, таймер
- Современный минималистичный интерфейс
- Запуск без командной строки

## Сборка из исходников

Нужен Python 3.11+.

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pythonw.exe app.py
```

Сборка `.exe`:

```bat
build.bat
```

Готовый файл появится в `dist\TubeSave.exe`.

## Структура проекта

| Файл | Назначение |
|------|------------|
| `app.py` | Графический интерфейс |
| `downloader.py` | Логика скачивания (yt-dlp) |
| `build.bat` | Сборка exe через PyInstaller |
| `TubeSave.lnk` / `TubeSave.bat` | Ярлыки запуска |

## Лицензия

Для личного использования. Соблюдайте [условия YouTube](https://www.youtube.com/t/terms) при скачивании контента.
