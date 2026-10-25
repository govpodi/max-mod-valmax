# Подключение одноразового MData helper к установщику

Пути `analysis/native-migration-v1/...` ниже обозначают локальные отчёты, не включённые в публикуемые исходники; ссылки на код ведут к файлам этого репозитория.

## Реализованный opt-in, 16.10.2026

Ниже сохранён исходный обзор точек интеграции; его номера строк относятся к
прежнему срезу. Теперь `modkit/package_mod.py` принимает `--native-transfer-proof`
со сводным отчётом `analysis/native-migration-v1/native-transfer-helper-materials-copy-proof.json`.
По умолчанию упаковка остаётся прежней. Opt-in сначала проверяет цепочку
отчётов и полный состав native helper, затем копирует его в `runtime/transfer`
до общего inventory и передаёт Inno `NativeTransfer=1`. Исходный список MAX
из 1696 файлов не расширяется файлами помощника.

Согласованный набор уже содержит Qt/VC runtime и материалы их лицензий/исходников.
Новое окно стартует без аргументов. Только ярлык MData переключён на него;
основной launcher и `recover` остаются прежними. WMI preflight учитывает
`valmax-transfer.exe`. Пока разрешена первая установка helper: существующий
`runtime/transfer` или reparse-предок останавливает opt-in без удаления файлов.
Обновление helper по предыдущему inventory ещё требуется.

Это подготовка native helper внутри существующего установщика, а не завершение
полного переноса клиента. Установленная ValMAX и пользовательские профили не
заменялись. Сборочные тесты и compile-only Inno не подтверждают реальную установку.

## Исходный обзор

Срез исходников от 16.10.2026. Это точки будущей интеграции, а не готовый native installer. Установщик, рабочая установка, профили и credentials при подготовке документа не менялись. Сборка отдельного helper bundle выполняется другим этапом; её итоговый manifest станет входом установщика.

## Точные точки изменения

| Место | Существующий контракт | Действие при готовности helper |
|---|---|---|
| [max-mod-setup.iss:9](../modkit/max-mod-setup.iss) | AppId `{419706B9-6FC7-46F8-B9FB-D7212FC91BAC}`; каталог `{localappdata}/Programs/ValMAX`, установка без elevation | Сохранить AppId, каталог и обычный пользовательский контекст. Импорт должен обращаться к credentials того же пользователя. |
| [max-mod-setup.iss:46](../modkit/max-mod-setup.iss) | Весь подготовленный payload копируется рекурсивно | Добавить уже проверенный helper bundle в payload до расчёта общего inventory. Inno не собирает зависимости самостоятельно. |
| [max-mod-setup.iss:50](../modkit/max-mod-setup.iss) | «Войти с MData» запускает `{app}/ValMAX.exe mdata`, WorkingDir `{app}` | Перенаправить только этот ярлык на native helper после проверки его production entry point. |
| [max-mod-setup.iss:51](../modkit/max-mod-setup.iss) | Recovery отдельно запускает `ValMAX.exe recover` | Не перенаправлять recovery на compact-import helper: полного восстановления он не реализует. |
| [max-mod-setup.iss:49](../modkit/max-mod-setup.iss), [строка 56](../modkit/max-mod-setup.iss) | Главный ярлык и postinstall запускают `ValMAX.exe` | Не подменять запуск клиента helper-ом. Изменение MData не делает основной launcher нативным. |
| [max-mod-setup.iss:91](../modkit/max-mod-setup.iss) | WMI проверяет `max.exe`, `max-service.exe`, `ValMAX.exe`, `MAX Mod.exe` перед установкой/удалением | Добавить точное имя `valmax-transfer.exe`. Сейчас открытый helper не попадает в эту проверку и может удерживать заменяемые файлы. Сохранить fail-closed при недоступном пути и границу `{app}/`. |

Installer preflight касается процессов конкретной установки. Он не заменяет проверку всех возможных участников MXMOD перед импортом: эту задачу выполняют [closed_clients.cpp:209](../native/transfer/closed_clients.cpp) и [host_callbacks.h:19](../native/transfer/host_callbacks.h).

## Layout и dispatch

Предлагаемое место — **`{app}/runtime/transfer/`**. Внутренний self-contained layout согласован с владельцем упаковщика:

```text
runtime/transfer/
  valmax-transfer.exe
  valmax_core.dll
  Qt6*.dll / необходимые app-local VC DLL
  qt.conf
  plugins/...
  qml/...
  licenses/...
  manifest + notices
```

UI находится в ресурсах helper. `qt.conf` использует только относительные `Prefix=.`, `Plugins=plugins`, `QmlImports=qml`. WorkingDir ярлыка — этот же каталог; нахождение зависимостей и core должно опираться на каталог EXE, а не на произвольный current directory. Отдельный процесс не должен искать Qt в установленном MAX, системном PATH или SDK разработчика. Точный список DLL/QML задаст проверенное замыкание собранного helper, а не копирование всего SDK.

Предпочтительный entry point ярлыка — helper без аргументов, открывающий выбор архива и ввод пароля. Это предложение для GUI этапа: production argv parser на момент данного среза ещё не зафиксирован. Не добавлять пароль, credential, JSON задания или fingerprint в ярлык/argv/environment. Не добавлять файловую ассоциацию `.mdata` без отдельно реализованного и проверенного контракта.

Существующая совместимость требует учесть не только новый ярлык:

- [bundle_entry.py:21](../modkit/bundle_entry.py) и [helper_command.py:46](../modkit/helper_command.py) принимают fixed role `mdata` и совместимый `--import-mdata`; [launch_mod.py:805](../modkit/launch_mod.py) сейчас открывает Python GUI. Новый ярлык сам по себе этот путь не переносит. Будущий native launcher должен направлять старые формы в тот же helper без секретных аргументов.
- [package_mod.py:154](../modkit/package_mod.py) создаёт совместимую копию `MAX Mod.exe`. При замене launcher сохранить обработку старых ярлыков, иначе они запустят оставшийся старый код.
- После compact import helper может завершиться предложением открыть ValMAX. Не запускать напрямую `runtime/mod/max.exe` в обход launcher. Автоперезапуск требует отдельной готовой native интеграции, не просто успешного импорта.

Имя продукта не меняет пространство данных. [launcher_config.rs:205](../native/core/src/launcher_config.rs) строит только `<explicit Local>/MXMOD/config.json`, а [mdata_import.rs:97](../native/core/src/mdata_import.rs) принимает явный Local. Не передавать туда `{app}`, каталог выбранного архива или папку ONEME. Installed helper должен получить обычный Local текущего пользователя своим проверенным host-путём. Имена токенов остаются `access_token_<ID>@MXMOD`; бренд ValMAX не является новым credential namespace.

## Inventory, Qt и notices

[package_mod.py:73](../modkit/package_mod.py) строит strict MAX inventory из 1696 baseline-файлов и `mod-manifest.json`; [строка 91](../modkit/package_mod.py) отвергает дополнительные файлы. Поэтому отдельный helper bundle добавляется рядом с `runtime/mod`, после его проверки. Не ослаблять baseline allowlist ради native DLL. [payload_inventory:64](../modkit/package_mod.py) затем включает всё дерево в `package-files.json` с `path/bytes/sha256` (сам inventory в собственный список не входит).

Новый manifest helper должен фиксировать точные EXE/core/Qt/plugins/QML/VC/notices, обычные и delay PE imports, source/build revision. Этот manifest — доказательство состава сборки, не самостоятельный доверенный runtime guard: [preflight.h:29](../native/launcher/preflight.h) требует встроенные immutable pins, а не подменяемый JSON. Статически импортированная DLL загружается раньше кода verifier; постфактум hash не доказывает безопасность первоначальной загрузки.

Текущий проверенный import core: `dc64035b71fcbfb863ce7a7a23cacce65c61e4198692da449ea3a672d064d5ba`, 3 118 592 байта, из mdata-import-native-proof.json (`analysis/native-migration-v1/mdata-import-native-proof.json`). Пакет должен брать принятую immutable пару DLL/import library; `.lib` нужна при сборке, не конечному пользователю. Не выбирать случайный последний `target/release` DLL.

Для notices есть конкретные пробелы старого упаковщика:

- [package_mod.py:33](../modkit/package_mod.py) собирает Python/Frida/Tk notices. Это не набор лицензий native helper. Существующий вызов PyInstaller на [строке 140](../modkit/package_mod.py) также остаётся: добавление одного EXE не даёт пакет без Python.
- [bootstrap_qt.py:53](../native/tooling/bootstrap_qt.py) получает binary SDK **Qt 6.11.2 MSVC2022 x64**, receipt — qt-sdk.json (`analysis/native-migration-v1/qt-sdk.json`). Он не собирает полный пакет лицензий/соответствующих исходников QtBase/QtDeclarative и их встроенных сторонних компонентов. Для выбранного LGPL варианта нужны материалы именно поставляемых модулей и возможность воспроизвести состав; одной MIT-лицензии ValMAX или общей ссылки Qt недостаточно как release inventory. Итоговые условия определяются текстами этих компонентов.
- [collect_rust_notices.py:99](../native/tooling/collect_rust_notices.py) формирует `rust-crates.json`, `ThirdParty-Rust.txt`, копии текстов и Cargo.lock SHA. Он включает также build-only зависимости и явно не является runtime DLL inventory или полным заключением о лицензиях.
- [package_webview_sources.py:44](../native/tooling/package_webview_sources.py) уже создаёт исходники изменённого Qt WebView backend и notices. Это часть общего ValMAX business payload, **не** замена исходникам QtBase/Declarative для standalone helper. WebView2 не надо включать в helper без фактической зависимости его UI. App-local VC DLL берутся из разрешённого redistribution набора с его условиями, не из произвольного System32.

## Upgrade и возврат прежнего launcher

Сохранить предыдущий проверенный installer/payload и его полный inventory до интеграции. Возврат означает согласованный старый launcher, helper и `runtime/mod`, а не замену одного EXE поверх новых библиотек. Сегодня `.iss` не содержит отдельного transactional rollback program tree; наличие Inno uninstall не доказывает такой откат.

Финальный native upgrade должен убрать только известные старые program-файлы по прежнему inventory: текущие `ignoreversion recursesubdirs` их автоматически не удаляют. Нельзя удалять профиль MXMOD, recovery marker, архивы пользователя или credentials. Требование уже закреплено в [native-migration.md:190](native-migration.md). До готовности полного native replacement этот документ не предлагает удалять работающий Python launcher/recovery.

Перед переключением ярлыка нужны отдельные результаты: helper запускается из установленного каталога с чужим WorkingDir и без SDK/Python в PATH; импорт/отмена/закрытие проходят в собственных синтетических профилях; открытый helper блокирует update/uninstall; старые ярлыки остаются рабочими; final file inventory и notices соответствуют реально включённым байтам. Настоящая установка и эти проверки в данном read-only этапе не выполнялись.
