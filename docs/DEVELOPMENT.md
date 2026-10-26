# Разработка ValMAX 8.9.1

В `main` находятся исходники ValMAX 8.9.1. Готовый Windows x64 клиент распространяется через [GitHub Releases](https://github.com/govpodi/max-mod-valmax/releases/tag/v8.9.1): основной клиент выпущен как stable, а экспорт, удержание удалений и другие отдельно отмеченные возможности остаются BETA. Владелец проверил исправление повторного открытия на установленной preview.31 и принял 8.9.1; независимый полный прогон финального Setup 8.9.1 не повторялся. Установленная программа работает на C++/Qt и Rust без отдельного Python и локального HTTP-сервера. Python остаётся в генераторах, упаковке и синтетических тестах; это инструменты разработки, а не часть установленного runtime.

## Где находится текущий код

| Каталог | Назначение |
| --- | --- |
| [`native/core/`](../native/core/) | Rust: состояние клиента, локальные хранилища, API и экспорт. |
| [`native/qt/`](../native/qt/) и [`native/hooks/`](../native/hooks/) | C++/Qt: интерфейс, интеграция с клиентом и ограниченные обработчики событий. |
| [`native/launcher/`](../native/launcher/) и [`native/transfer/`](../native/transfer/) | Нативный запуск и перенос собственных сессий. |
| [`native/installer/`](../native/installer/) | Inno Setup и контракт управляемого обновления. |
| [`native/tooling/`](../native/tooling/) и [`native/tests/`](../native/tests/) | Подготовка сборки и проверки; здесь допустим Python. |
| [`modkit/`](../modkit/) | Исходные QML/трансформаторы, синтетические тесты и исторический Python-путь. `modkit/` не является entrypoint установленной 8.9.1. |

Диаграмма языков GitHub выделяет нативный клиент: Python-файлы прежней реализации, сборки и тестов исключены из неё через [`.gitattributes`](../.gitattributes), но остаются в репозитории и доступны для проверки. Некоторые инструменты в `modkit/` нужны нативной сборке.

Пользовательская страница находится в [`site/`](../site/) и публикуется из `main` через [workflow GitHub Pages](../.github/workflows/pages.yml). При новом выпуске обновите номер версии и ссылки на Setup в странице, метаданных и README; проверьте опубликованный адрес и [`sitemap.xml`](../site/sitemap.xml). Страница не загружает внешние скрипты и не собирает аналитику.

Текущий публичный пакет собирается в режиме `public-stable-core`; отдельные BETA-возможности и пределы проверки перечислены в [заметках 8.9.1](releases/v8.9.1.md). Режим полного `user-release` имеет более широкий набор требований. Сборка точного Setup из чистого checkout без закреплённого baseline MAX, Qt/MSVC/Rust, Inno Setup, материалов компонентов и квитанции принятия не воспроизводится одной командой.

## Проверки из чистого checkout

На Windows с Python 3.13 следующие проверки не требуют сторонних Python-пакетов:

```powershell
python -B -X utf8 .github/scripts/check_repository.py
python -B -X utf8 -m unittest discover -s modkit -p test_client_activation.py -v
python -B -X utf8 -m unittest discover -s modkit -p test_support_diagnostics.py -v
```

Для Rust-части CI использует закреплённый toolchain из [`native/rust-toolchain.toml`](../native/rust-toolchain.toml) и запускает из `native/` синтетические тесты:

```powershell
cd native
cargo test --locked -p valmax-core --lib retention_store::tests -- --test-threads=1
cargo test --locked -p valmax-core --test retention_runtime_selected -- --test-threads=1
cargo test --locked -p valmax-core --test quick_reply_store_contract --test support_diagnostics_native -- --test-threads=1
```

Тесты используют отдельные фикстуры и не открывают рабочий профиль. Полный состав заданий и изоляция временных файлов находятся в [workflow](../.github/workflows/source-check.yml). Source CI не собирает установщик и не подтверждает серверную историю, весь UI или работу на чистой машине.

Отдельное задание GitHub Actions запускает синтетические тесты MData после установки версий из `modkit/requirements.txt`. Оно направляет временные файлы и тестовый профиль в каталог runner. Локально эти тесты можно запустить так же, указав отдельный `VALMAX_TEST_TEMP_ROOT` и не используя рабочий MXMOD.

## Сборка нативного клиента

В подготовленной Windows x64 лаборатории настройте пути MSVC, Qt 6.11.2 и Rust, затем загрузите [`native/tooling/native_env.ps1`](../native/tooling/native_env.ps1) перед CMake/Cargo. Этот файл содержит пути конкретной лаборатории и `CARGO_TARGET_DIR` на E:; на другой машине пути нужно адаптировать. Нативный упаковщик [`package_native_client.py`](../native/tooling/package_native_client.py) принимает подготовленные runtime/QML/launcher proofs, материалы и проверку установленного клиента. [Контракт установщика](../native/installer/README.md) описывает обновление и ограничения; успешная компиляция одного компонента не заменяет проверку точного пакета.

### Синтетический Qt bootstrap для разработки

[Проверка порядка загрузки](../native/tests/bootstrap/proof.py) запускает только собственный `valmax_bootstrap_harness.exe` и `valmaxbootstrapplugin.dll`, без MAX, учётных записей и рабочего профиля. Переменные `VALMAX_BOOTSTRAP_BUILD_ROOT`, `VALMAX_BOOTSTRAP_QT_ROOT`, `VALMAX_BOOTSTRAP_FIXTURE_ROOT` и `VALMAX_BOOTSTRAP_REPORT` позволяют направить входы, временные файлы и отчёт на отдельный диск. `VALMAX_BOOTSTRAP_CLEAN_FIXTURE=1` удаляет созданную тестовую фикстуру после записи отчёта. Для `Qt6Core.dll` и `Qt6Gui.dll` проверка требует закреплённые SHA-256.

Этот host помогает быстро проверять порядок вызовов Qt. Он не устанавливает защитные фильтры в MAX; его отрицательные сценарии показывают, что отсутствующий или отклонённый плагин позволяет синтетическому приложению продолжить запуск без подтверждения готовности. Раннюю защиту реального клиента нужно доказывать отдельно.

<details>
<summary>Архив: прежняя сборка Python/PyInstaller</summary>

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

</details>

## English

ValMAX 8.9.1 ships a stable native C++/Qt and Rust core; advanced features are marked BETA where their limits remain. The owner tested the installed preview.31 and accepted the 8.9.1 fix; an independent full regression of the final 8.9.1 Setup was not repeated. Python is used for generators, packaging and synthetic tests, not the installed runtime. Source CI uses Python 3.13 and the pinned Rust toolchain on Windows; a separate job runs isolated MData tests. The collapsed `modkit/` commands above document the historical PyInstaller route. Rebuilding the exact native release requires a pinned MAX baseline, prepared toolchains, installed-client evidence and distribution materials; a clean checkout alone is not a release environment.
