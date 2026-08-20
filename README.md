# TubeSave

Приложение для скачивания видео в **MP4** (или только аудио) — максимальное доступное качество, AAC, со встроенным превью.

## Скачать

Готовые сборки (Windows):

| Файл | Описание |
|------|----------|
| [TubeSave-Windows.zip](https://github.com/mine1510/TubeSave/releases/latest/download/TubeSave-Windows.zip) | ZIP-архив |
| [TubeSave-Windows.rar](https://github.com/mine1510/TubeSave/releases/latest/download/TubeSave-Windows.rar) | RAR-архив |

Страница релизов: [Releases](https://github.com/mine1510/TubeSave/releases)

Также архивы лежат в папке [`download/`](./download/) репозитория.

## Кнопка в браузере (без копирования ссылки)

1. Запустите TubeSave.
2. В приложении нажмите **Браузер** — откроется папка расширения и инструкция.
3. Chrome / Edge / **Яндекс.Браузер**:
   - Chrome: `chrome://extensions`
   - Edge: `edge://extensions`
   - Яндекс: `browser://extensions`  
   → режим разработчика → «Загрузить распакованное» → папка `browser-extension`.
4. На YouTube / Shorts / VK-клипах / Яндекс.Музыке появятся кнопки TubeSave.
Плагин и приложение сами проверяют обновления при запуске. Крестик сворачивает TubeSave в трей.

Расширение шлёт ссылку на локальный мост (`127.0.0.1:17834`). Если приложение закрыто, используется протокол `tubesave://`.

## Как пользоваться

1. Распакуйте архив.
2. Запустите `TubeSave.exe`.
3. Вставьте ссылку на видео.
4. Выберите папку сохранения (запоминается).
5. Нажмите **Скачать**.

Поддерживаемые сайты:

- YouTube — `youtube.com`, `youtu.be`, Shorts
- VK Video — `vkvideo.ru`, `vk.com` (видео и клипы)
- Яндекс.Музыка — `music.yandex.ru` (аудио)
- Rule34 — `rule34.xxx`, `rule34video.com`
- Iwara — `iwara.tv`
- PornHub — `pornhub.com` / `pornhub.org`

## Возможности

- Видео с нескольких сайтов (через yt-dlp)
- MP4 без лишней перекодировки (H.264 + AAC, когда доступно)
- Только аудио (M4A)
- Выбор качества
- Превью ролика вшивается в файл
- Тёмная тема, статусы прогресса, скорость, ETA
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
| `bridge.py` | Локальный мост для расширения / протокол `tubesave://` |
| `updater.py` / `version.py` / `update.json` | Проверка и установка обновлений |
| `browser-extension/` | Расширение Chrome / Edge / Яндекс.Браузер |
| `build.bat` | Сборка exe через PyInstaller |
| `TubeSave.lnk` / `TubeSave.bat` | Ярлыки запуска |

## Лицензия

Для личного использования. Соблюдайте [условия YouTube](https://www.youtube.com/t/terms) при скачивании контента.
