# Разработка и сборка

Этот репозиторий содержит исходники модификации и проверок ValMAX. Для готовой установки используйте GitHub Releases. Текущая публичная BETA собирается из `native/`: установленный клиент работает на C++/Qt и Rust без Python и локального HTTP-сервера. Python используется в инструментах сборки и синтетических тестах. Полная пересборка установщика из одного чистого checkout пока требует отдельно подготовленной лабораторной среды.

## Быстрая проверка исходников

Windows, Python 3.13; сторонние Python-пакеты для этих двух команд не нужны:

```powershell
python -B -X utf8 .github/scripts/check_repository.py
python -B -X utf8 -m unittest discover -s modkit -p test_client_activation.py -v
```

Тесты используют подставной Windows API и не открывают реальные профили. Проверка source CI не подтверждает native поведение, работу сервера, UI или пригодность нового установщика.

Отдельное задание GitHub Actions запускает синтетические тесты MData после установки версий из `modkit/requirements.txt`. Оно направляет временные файлы и тестовый профиль в каталог runner. Локально эти тесты можно запустить так же, указав отдельный `VALMAX_TEST_TEMP_ROOT` и не используя рабочий MXMOD.

## Нативная разработка

В подготовленной Windows x64 лаборатории загрузите `native/tooling/native_env.ps1` перед MSVC/CMake или Cargo. Скрипт задаёт текущие пути инструментов и `CARGO_TARGET_DIR` на E:; на другой машине сначала настройте эти пути под свою среду. Исходники и границы публичной упаковки описаны в [документации нативного установщика](../native/installer/README.md). Его режим `user-release` проверяет точный пакет, установленные сценарии и материалы; успешная компиляция отдельного компонента не заменяет эти проверки.

### Синтетический Qt bootstrap для разработки

[Проверка порядка загрузки](../native/tests/bootstrap/proof.py) запускает только собственный `valmax_bootstrap_harness.exe` и `valmaxbootstrapplugin.dll`, без MAX, учётных записей и рабочего профиля. Переменные `VALMAX_BOOTSTRAP_BUILD_ROOT`, `VALMAX_BOOTSTRAP_QT_ROOT`, `VALMAX_BOOTSTRAP_FIXTURE_ROOT` и `VALMAX_BOOTSTRAP_REPORT` позволяют направить входы, временные файлы и отчёт на отдельный диск. `VALMAX_BOOTSTRAP_CLEAN_FIXTURE=1` удаляет созданную тестовую фикстуру после записи отчёта. Для `Qt6Core.dll` и `Qt6Gui.dll` проверка требует закреплённые SHA-256.

Этот host помогает быстро проверять порядок вызовов Qt. Он не устанавливает защитные фильтры в MAX; его отрицательные сценарии показывают, что отсутствующий или отклонённый плагин позволяет синтетическому приложению продолжить запуск без подтверждения готовности. Раннюю защиту реального клиента нужно доказывать отдельно.

## Прежние Python-инструменты

Команды ниже документируют старый путь `modkit/` с PyInstaller и не собирают текущий нативный установщик.

## Точный baseline

Native patches рассчитаны на **MAX 26.35.1.80662**. Неизменённый `max.exe` должен иметь SHA-256:

```text
d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182
```

Baseline содержит только program files и должен быть получен отдельно. Он, MSI и извлечённые MAX/Qt ресурсы в Git не включены. Никогда не используйте папку с пользовательским профилем как baseline и не меняйте исходную базу: builder создаёт отдельный новый каталог.

## Подготовка среды и UI

Для native сборки используйте Windows x64 и отдельный virtualenv. Зафиксированные Python-зависимости находятся в `modkit/requirements.txt` и `modkit/requirements-build.txt`:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r modkit/requirements-build.txt
```

Генерации UI дополнительно нужны PySide6/Qt и официальный `rcc.exe`; они не перечислены в requirements. Ресурсы проекта проверялись с Qt 6.11.2. `modkit/icon_resources.py` сейчас использует фиксированный `DEFAULT_RCC`:

```text
D:/CodexLabs/max-mod-26.35.1/venv/Lib/site-packages/PySide6/rcc.exe
```

У `ui_prepare_features.py` пока нет CLI-параметра для этого пути. Перед регенерацией обеспечьте соответствующую среду либо настройте `DEFAULT_RCC` в своей рабочей копии. Следующая команда показывает CLI генератора; замените пути baseline/output на свои и используйте новый пустой каталог output:

```powershell
.venv\Scripts\python.exe -B -X utf8 modkit/ui_prepare_features.py --baseline D:/ValMAXBuild/baseline/max.exe --output D:/ValMAXBuild/ui-new --emit-spec modkit/ui_privacy.py
```

Генератор читает upstream QML/иконки из точного baseline, применяет трансформаторы и заново создаёт `ui_privacy.py`. Не правьте сгенерированные hex-байты вручную. Происхождение ресурсов и область MIT описаны в [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Отдельная копия runtime

После подготовки среды и UI:

```powershell
.venv\Scripts\python.exe -B -X utf8 modkit/build_mod.py build --baseline D:/ValMAXBuild/baseline --output D:/ValMAXBuild/mod-new --components accounts privacy isolation ui_privacy
.venv\Scripts\python.exe -B -X utf8 modkit/build_mod.py verify D:/ValMAXBuild/mod-new/mod-manifest.json
.venv\Scripts\python.exe -B -X utf8 modkit/launch_mod.py --manifest D:/ValMAXBuild/mod-new/mod-manifest.json --check-only
```

`--output` должен указывать на новый каталог, отдельный от baseline. Проверяются полный hash, ожидаемые байты и обратимость. `--check-only` проверяет manifest/конфигурацию без запуска клиента; он может читать существующий локальный MXMOD config. Для изолированной проверки выполняйте его в отдельной тестовой учётной записи Windows.

Source default manifest в `helper_command.py` привязан к `D:/CodexLabs/max-mod-26.35.1/runtime/valmax-live-v8/mod-manifest.json`; передавайте `--manifest` явно. Frozen helper использует относительный `runtime/mod/mod-manifest.json` внутри установки.

## Установщик

`package_mod.py` принимает явные `--manifest`, `--layout`, `--iscc`, `--output`. Нужны Inno Setup и отдельно подготовленный inventory исходного MSI с 1696 program files. Его SHA-256 закреплён в `package_mod.LAYOUT_SHA256`; сам inventory отсутствует в Git. Default layout также привязан к лабораторному пути на D.

```powershell
.venv\Scripts\python.exe -B -X utf8 modkit/package_mod.py --manifest D:/ValMAXBuild/mod-new/mod-manifest.json --layout D:/ValMAXBuild/restore_layout_report.json --iscc "C:/Program Files (x86)/Inno Setup 6/ISCC.exe" --output D:/ValMAXBuild/package-new
```

Это описание существующего CLI, а не обещание автономной сборки: до команды нужно подготовить pinned inventory и подходящие инструменты. Builder проверяет состав runtime, собирает helper через PyInstaller и создаёт notices и hashes. Пользовательские профили, MData и credentials в payload не входят.

Многие `test_*` и `*_qa.py` требуют локальные извлечённые QML, baseline и фиксированные пути `D:/CodexLabs`/`D:/ValMAX/analysis`. Их нельзя запускать как универсальный полный набор в чистом GitHub runner без подготовки fixtures и параметризации путей. Native, синтетические и серверные проверки следует описывать отдельно.

## English

The installed public beta is a native C++/Qt and Rust client. Python is used for build tools and synthetic tests. Source CI uses Python 3.13 on Windows; a separate job runs isolated MData tests. The older `modkit/` commands above document the historical PyInstaller route, not the current native installer. Rebuilding the native release requires a separately prepared, pinned MAX baseline and toolchain, exact installed-client evidence, and complete distribution materials; a clean checkout alone is not a release environment.
