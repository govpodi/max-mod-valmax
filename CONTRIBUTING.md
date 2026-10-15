# Участие в разработке

Предлагайте небольшие изменения с понятным описанием проблемы, результата и проверки. Сохраняйте ожидаемые байты, SHA-256 guards и обратимость native patches. Сгенерированный `modkit/ui_privacy.py` обновляйте через генератор, а не ручной правкой байтов.

Для тестов используйте временные профили и вымышленные сообщения. Не добавляйте настоящие MXMOD/ONEME, WinCred, токены, архивы переписки, baseline или готовые бинарные файлы. Проблемы безопасности описаны в [SECURITY.md](SECURITY.md).

Из корня репозитория на Windows с Python 3.13:

```powershell
python -B -X utf8 .github/scripts/check_repository.py
python -B -X utf8 -m unittest discover -s modkit -p test_client_activation.py -v
```

Первая команда проверяет Python syntax, локальные ссылки в Markdown и публичную иллюстрацию. Вторая запускает 22 синтетических теста активации окна с подставным Windows API; настоящий клиент не запускается. Именно этот ограниченный набор выполняет GitHub Actions. Полный native/QML QA требует внешней лабораторной среды и не выполняется этим CI.

Порядок сборки и текущие ограничения: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). В pull request укажите выполненные команды и ограничения проверки. Если менялись upstream материалы, сохраните их notices и отметьте происхождение; лицензия оригинальных изменений — [MIT](LICENSE).

## English

Keep pull requests focused and describe the problem, resulting behavior, and validation. Preserve version/hash/byte guards and reversible native patches. Use synthetic data only. Run the two commands above; CI checks repository metadata and 22 mocked activation tests, not the complete native/QML suite. See [development notes](docs/DEVELOPMENT.md) and [security reporting](SECURITY.md).
