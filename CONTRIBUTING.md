# Contributing / Участие в разработке

Thanks for your interest in Onyx Panel! / Спасибо за интерес к Onyx Panel!

## Report a bug / Сообщить о проблеме

[Open an issue](https://github.com/xCodeOn/Onyx-Panel/issues/new/choose) with the panel version (`onyx-panel-update` or the badge on the dashboard), the OS, steps to reproduce and logs from `journalctl -u onyx-panel -n 100`. Remove secrets, access keys and subscription links before pasting.

[Откройте issue](https://github.com/xCodeOn/Onyx-Panel/issues/new/choose) с версией панели, ОС, шагами воспроизведения и логами `journalctl -u onyx-panel -n 100`. Перед вставкой уберите секреты, ключи доступа и ссылки подписок.

## Development setup / Настройка окружения

The panel is plain Python 3 with zero dependencies. The web UI lives in `onyx_ui.py`; the HTTP server and routes are assembled into `panel.py` from the heredoc inside `install-panel.sh`; feature modules are the `onyx_*.py` files.

Панель — чистый Python 3 без зависимостей. Интерфейс — в `onyx_ui.py`; HTTP-сервер и маршруты собираются в `panel.py` из heredoc внутри `install-panel.sh`; модули функций — файлы `onyx_*.py`.

```bash
git clone https://github.com/xCodeOn/Onyx-Panel.git
cd Onyx-Panel
python3 tests/check_heredocs.py   # every installer heredoc must compile
python3 tests/test_modules.py     # module logic
python3 tests/test_server.py      # server smoke tests (routes, settings page, …)
```

Run the tests before opening a PR — CI runs the same suite on every push. / Прогоняйте тесты перед PR — CI запускает тот же набор на каждый пуш.

## Releases / Релизы

The version lives in one place: the repo-root `VERSION` file. On every push to `main` the release workflow rewrites the version literals in `update.sh`, `install-core.sh`, `install-final.sh` and `install-panel.sh` from that file, so nothing else needs editing. To release a new version: change `VERSION`, push — the tag and GitHub Release appear by themselves (a `v<version>` tag is created only once). The node API reports the installed version at runtime from `/etc/onyx-panel/version`, so it never goes stale either.

Версия живёт в одном месте — файле `VERSION` в корне репозитория. При каждом пуше в `main` релизный workflow перезаписывает литералы версии в `update.sh`, `install-core.sh`, `install-final.sh` и `install-panel.sh` из этого файла, больше ничего править не нужно. Чтобы выпустить новую версию: поменяйте `VERSION` и запушьте — тег и GitHub Release появятся сами (тег `v<версия>` создаётся один раз). Node-API отдаёт версию установленной панели в рантайме из `/etc/onyx-panel/version`, так что и он не устаревает.

Releases are published from `main` (historically also via `./release.sh vX.Y.Z -m "what changed"`). The panel updates itself from the latest `v*` tag, and servers deploy via `onyx-panel-update`.

Релизы публикуются из `main` (исторически — также скриптом `./release.sh vX.Y.Z -m "что изменилось"`). Панель обновляется сама по последнему тегу `v*`, серверы ставят её через `onyx-panel-update`.
