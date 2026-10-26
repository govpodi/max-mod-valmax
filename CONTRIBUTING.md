# Как помочь ValMAX

Заметили ошибку или придумали улучшение — об этом можно сообщить без навыков программирования.

## Сообщить об ошибке

1. Полностью закройте ValMAX: значок в трее → «Выход». Откройте приложение снова и проверьте, повторяется ли проблема.
2. Посмотрите [уже открытые ошибки](https://github.com/govpodi/max-mod-valmax/issues). Если нашли такую же, дополните её своими шагами.
3. [Создайте сообщение об ошибке](https://github.com/govpodi/max-mod-valmax/issues/new?template=bug_report.yml). Напишите простыми словами:
   - что хотели сделать;
   - куда нажали и что произошло;
   - что ожидали увидеть;
   - повторяется ли ошибка каждый раз;
   - версию ValMAX и Windows. Версию Windows показывает команда `winver` из меню «Пуск».

Например: «В настройках включил режим призрака. Закрыл приложение через трей и открыл снова. Ожидал, что переключатель останется включённым, но он выключен». Для примеров используйте вымышленные имена и сообщения.

Если ValMAX запускается, откройте **Настройки → Помощь и диагностика**. Там можно посмотреть состав отчёта и нажать **«Сохранить отчёт…»**, затем выбрать новый файл `.json`. Приложение соберёт версии, известный код состояния запуска, состояния переключателей и общие счётчики. Недоступные сведения отмечаются как `null`. Счётчики относятся к текущему наблюдению, а сохранённые настройки могут требовать перезапуска.

Отчёт остаётся в выбранном вами файле: приложение не отправляет его автоматически. Просмотрите его перед тем, как вручную прикрепить к ошибке. Если приложение не запускается или отчёт недоступен, просто опишите проблему и перепишите видимый код ошибки — отчёт необязателен.

## Что можно приложить

- Короткие шаги воспроизведения и точный текст видимой ошибки.
- Отчёт `.json`, созданный через «Помощь и диагностика».
- Обрезанный скриншот, если на нём не видно аккаунтов, переписки и личных данных.

**Не отправляйте** номера телефонов, имена и ID аккаунтов или чатов, пароли, коды входа, токены, защищённые ссылки на медиа, базы профиля, папки MXMOD/ONEME, архивы MData/MAXSession и экспорты переписки. Не прикладывайте сырые логи и дампы: они могут содержать личные данные. Локальный путь с именем пользователя тоже следует убрать из текста и снимка экрана.

Для предложений используйте [новый issue](https://github.com/govpodi/max-mod-valmax/issues/new) и опишите, какую задачу решит улучшение. Уязвимости сообщайте через [закрытый канал из SECURITY.md](SECURITY.md); их детали не публикуйте в обычном issue.

## Для разработчиков

Основной код ValMAX 8.9.0 находится в [`native/`](native/): Rust в `core/`, C++/Qt в `qt/`, `hooks/`, `launcher/` и `transfer/`. Установленный клиент не запускает Python; он остаётся в генераторах, упаковке и синтетических тестах. Старый Python/PyInstaller-путь `modkit/` не собирает текущий Setup. Подробная [карта исходников и требования сборки](docs/DEVELOPMENT.md) находятся в документации.

Предлагайте небольшие изменения с понятным описанием проблемы, результата и проверки. Сохраняйте ожидаемые байты, SHA-256 guards и обратимость native patches. Сгенерированный `modkit/ui_privacy.py` обновляйте через генератор, а не ручной правкой байтов.

Для тестов используйте временные профили и вымышленные сообщения. Не добавляйте настоящие MXMOD/ONEME, WinCred, токены, архивы переписки, baseline или готовые бинарные файлы.

Проверки исходников из корня репозитория на Windows с Python 3.13:

```powershell
python -B -X utf8 .github/scripts/check_repository.py
python -B -X utf8 -m unittest discover -s modkit -p test_client_activation.py -v
python -B -X utf8 -m unittest discover -s modkit -p test_support_diagnostics.py -v
```

Эти три команды выполняются в задании `source` GitHub Actions: синтаксис и ссылки, 22 синтетических теста активации окна с подставным Windows API, состав диагностического отчёта и сохранение на вымышленных данных. Отдельные задания проверяют MData в изолированном профиле и Rust-логику удержания, быстрых ответов и диагностики. Для Rust используется закреплённый [toolchain](native/rust-toolchain.toml); команды есть в [workflow](.github/workflows/source-check.yml). CI не запускает настоящий клиент и не собирает релизный Setup; полный native/QML QA требует подготовленной Windows лаборатории.

Порядок сборки и текущие ограничения: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). В pull request укажите выполненные команды и ограничения проверки. Если менялись upstream материалы, сохраните их notices и отметьте происхождение; лицензия оригинальных изменений — [MIT](LICENSE).

## English

To report a bug, [open the bug form](https://github.com/govpodi/max-mod-valmax/issues/new?template=bug_report.yml), describe your steps, expected result, actual result, repeatability, and ValMAX/Windows versions. A report from **Settings → Help and diagnostics → Save report…** is optional; it stays in the file you choose and is never sent automatically. Review it before attaching it manually. If the app cannot start, describe the problem and copy the visible error code instead.

Never attach personal data, account/chat IDs, credentials, profile folders, message exports, MData/session archives, raw logs, or dumps. Use synthetic examples and crop screenshots. Report vulnerabilities through [the private security channel](SECURITY.md).

Keep pull requests focused, preserve native version/hash/byte guards and reversibility, and describe validation. Production code lives in `native/` (C++/Qt and Rust); Python is retained for generation, packaging and tests. GitHub Actions runs the three source checks above, isolated MData tests and selected Rust tests, but does not build the release Setup or exercise the installed client. See [development notes](docs/DEVELOPMENT.md) for the native build requirements.
