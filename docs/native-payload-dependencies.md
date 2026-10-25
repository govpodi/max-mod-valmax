# Зависимости будущего нативного пакета

Это статический аудит исходных файлов, а не разрешение выпускать установщик или удалять перечисленные компоненты. Готовый пакет ещё должен пройти проверку в собственной изолированной копии. В ходе аудита MAX, DLL, браузер и пользовательские профили не запускались и не изменялись.

Машинный итог: `analysis/native-migration-v1/payload-dependency-audit.json`. Подробные PE-ребра, QML-метаданные и списки файлов с SHA-256 находятся в `C:/ValMAXBuild/fixtures/payload-dependencies-v1/`. Повторение: `native/tooling/audit_payload_dependencies.py`, затем `native/tooling/test_payload_dependency_audit.py`.

## Обязательные файлы и реальные пробелы

Исходный пакет — `release/valmax-v8.8.4-20261016/build/dist/ValMAX/runtime/mod`. Проверены 312 его PE-файлов, 11 нативных DLL/вариантов моста, ещё 5 файлов объединённого моста и 39 PE-файлов фиксированного WebView2. Проверяются обычные и отложенные импорты, архитектура, именованные/ordinal-экспорты и перенаправленные экспорты. Для больших таблиц Qt снят ошибочный для них предел парсера в 8192 имени; полнота таблицы отдельно проверяется.

У `max.exe` есть 50 файлов в обычном app-local PE-замыкании, включая сам EXE. У `max-service/max-service.exe` — 39. Полные списки с версиями и SHA-256 находятся в `package-requirements.json → normalAppLocalClosures`. Это нижняя граница состава: динамические плагины и QML нужно добавлять отдельно. Каталог сервиса — отдельный корень поиска; одинаковое имя DLL в двух каталогах не разрешает удалять одну из копий.

`tracer_crash_reporter.dll`, `tracernative.dll`, `updater_oneme.dll` и `WinSparkle.dll` входят в обычное замыкание MAX. Блокировка вызовов внутри них не удаляет импорт из PE-таблицы. С текущими таблицами эти DLL сохраняются.

| Импорт | Кто его требует | Фактический статус и действие |
|---|---|---|
| `Qt6SerialPort.dll` | Только `plugins/position/qtposition_nmea.dll` | DLL отсутствует. NMEA-плагин — кандидат на исключение; до этого нужна проверка отсутствия нативного NMEA/GPS-сценария и тест геолокации. Альтернатива — поставлять совместимую Qt SerialPort и проверить её лицензию/ABI. |
| `Qt6WebEngineQuick.dll` | Только `qml/QtWebEngine/qtwebenginequickplugin.dll` | DLL отсутствует. Дерево QtWebEngine — кандидат на исключение, но не доказанно лишний компонент всего MAX. Условия ниже. |
| `Qt6WebEngineQuickDelegatesQml.dll` | Только `qml/QtWebEngine/ControlsDelegates/qtwebenginequickdelegatesplugin.dll` | Та же условная ветка WebEngine. Подкладывать случайную DLL другой версии нельзя. |
| `icuuc.dll` | Обе `Qt6Core.dll` — основная и сервиса | Найдена в System32, нужные экспорты присутствуют. Это зависимость ОС; её не нужно копировать из System32 в пакет. |

Microsoft включает `icuuc.dll` и `icuin.dll` начиная с Windows 10 1703; объединённая `icu.dll` появилась с 1903. Это не устанавливает минимальную Windows для всего ValMAX: ограничения Qt, WebView2 и других импортов нужно проверять отдельно. [Microsoft ICU](https://learn.microsoft.com/en-us/windows/win32/intl/international-components-for-unicode--icu-).

## Qt WebView и Qt WebEngine — разные ветки

Из 107 распознанных RCC-таблиц MAX декодированы 1061 QML/JS/qmldir-ресурс без ошибок. `QtWebView` найден в `LeadingSettingsBar.qml`, `SettingsFaq.qml`, `WebMediaWindow.qml`; текущий `native/qt/BusinessPanel.qml` также создаёт компонент с `QtQuick` и `QtWebView`. В проверенных ресурсах нет ссылок на `QtWebEngine`, `QtPositioning`, `PositionSource` или `nmea.source`. Это наблюдение об исходниках, а не доказательство отсутствия динамических вызовов C++.

В `qml/QtWebView/qmldir` присутствует `depends QtWebEngine auto`. Такая директива сообщает сборщику о скрытой зависимости для развёртывания; она отличается от `import`, импортирующего типы модуля. Поэтому слепое следование deployment-метаданным затягивает неполную ветку WebEngine даже для backend WebView2. `optional plugin` тоже не означает, что весь модуль можно выбросить: он позволяет пропустить загрузку плагина, если нужные типы уже зарегистрированы. [Qt: qmldir](https://doc.qt.io/qt-6.8/qtqml-modules-qmldir.html).

Для текущего пути оставляются:

- штатная `Qt6WebView.dll` 6.11.2.0, SHA-256 `9891d8e88236660bf63213f0a538318c8afe8d67e980a0ff86d8eb509e2df7bc`;
- `Qt6WebViewQuick.dll`, `qml/QtWebView/`, зависимые Qt Quick/QML/Core/Gui;
- `WebView2Loader.dll`, потому что он также непосредственно импортирован самим `max.exe`;
- замена только `plugins/webview/qtwebview_webview2.dll` на проверенный нативный backend, SHA-256 `b2a593cc77b40233120f90eacf59bcee24ea41c12bbac470ac3b78979d1e10a6`;
- весь фиксированный Microsoft WebView2 155.0.4283.60 x64 с 256 файлами и его notices, по отдельному manifest/proof. Внутри vendor-дистрибутива есть и x86-компоненты; их наличие не является основанием для удаления.

Существующий proof подтверждает `about:blank` в собственном процессе Qt 6.11.2 и контроллер бизнес-панели на синтетическом backend. Он не доказывает загрузку бизнес-сайта, отсутствие фонового трафика или корректность любого нового installer layout. Части fixed runtime нельзя исключать только потому, что они не достижимы по PE-импортам: там есть динамические процессы, DLL и ресурсы.

Практический следующий шаг для WebEngine: в отдельном staging-каталоге задать явный WebView2 backend и точный состав, отдельно обработать deployment-директиву `depends`, затем проверить бизнес, FAQ и веб-медиа на настоящем движке MAX. До такой проверки статус ветки — `conditional_prune_candidate`, а не `unused`.

## Плагины, которые пока сохраняются

`qwindows`, SVG/icon/imageformats, multimedia, TLS/networkinformation, QML Controls/Dialogs/Effects/Shapes/VectorImage и используемые лабораторные модули сохраняются. Часть выбирается по формату файла, системе, теме или сценарию вызова; отсутствие обычного PE-импорта не доказывает ненужность. Наблюдаемые импорты QML, их точные qmldir-директивы и перечень DLL-плагинов со всеми хэшами записаны в `package-requirements.json`.

Qt NMEA обслуживает последовательный порт, сокет или файл; Windows positioning и positionpoll — отдельные плагины. Пока не проверено, использует ли MAX эти функции из C++, нельзя переносить вывод об NMEA на весь QtPositioning. [Qt NMEA](https://doc.qt.io/qt-6/position-plugin-nmea.html), [Qt positioning plugins](https://doc.qt.io/qt-6/qtpositioning-plugins.html).

## Нативные модули и ограничения проверки

Core, privacy, ghost и notifications проверены по точным SHA их proof. Текущий core `9329d8b01b35493ec4667e9b7594d35c2e6b5d6a6f80342f1dc077dd22b38f36` связан с `startup-client-proof.json`: это новый receipt ABI, проверенный адресно, включая C++→Rust и совместимость с Python. Дополнительно получен `core-startup-regression-proof.json`: полный прогон 142 тестов, 0 ошибок и 0 пропусков, относится точно к этому `9329d8b0…`. Исторические 137 тестов относятся к `f29f113f…`, который сохранён отдельно для объединённого моста.

Пять прежних мостов `valmaxnativeplugin.dll` собраны с разным набором функций. Дополнительно проверен новый объединённый кандидат: SHA-256 `cb7c22aff5e216c846507ea7a99b696858ad56f71f36489f037be77529ab26e1`, с отдельными frozen core `f29f113f…` и hook DLL из `qt-all-features-build.json`. Для него используется отдельная карта зависимостей: новый core `9329d8b0…` не подменяет ранее проверенный DLL. Standalone lifecycle proof содержит 150 host checks, 69 проверок трёх циклов QQmlEngine и 12 отрицательных проверок конфигурации; он не подтверждает положительный сценарий реального MAX или ранний bootstrap. `valmax_retention_queue.dll` разобрана отдельно, но исключена из предлагаемой shipping-карты: пока есть только синтетический queue proof, этап хранения/интеграции остановлен. Ранний bootstrap до MAX login также не доказан этим аудитом.

Windows API sets — имена контрактов, а не обязательно файлы DLL. Скрипт читает системную схему и отмечает отдельно точное имя и предположительный host по hash-prefix версии; для кандидата проверяются экспорты, но это не доказательство принятия версии загрузчиком. Неразрешённые глубоко системные ссылки и шесть symbol-ребер к `comctl32` оставлены явными: эта модель не воспроизводит SxS/activation context, KnownDLL, PATH или состояние уже загруженных модулей. Нельзя превращать такие результаты в требование копировать системные DLL. [Microsoft API sets](https://learn.microsoft.com/en-us/windows/win32/apiindex/windows-apisets).

## Условия завершения bundling

1. Зафиксировать одну версию native MAX, all-feature bridge, core, hook DLL, WebView backend, notices и manifest; повторить аудит по этим байтам.
2. Разрешить три условные отсутствующие Qt-зависимости: подтвердить исключение соответствующих плагинов проверками staging либо включить полный совместимый модуль. Не скрывать ошибку общего сканера глобальным allowlist.
3. Проверить реальный loader search, QML imports, plugin enumeration, обычные и delay-loaded функции в этом layout на каждой поддерживаемой версии Windows. Отдельно проверить отсутствие зависимости от PATH/Qt/Python установленного на машине разработчика.
4. Проверить старт, список чатов, медиа/звонки, фото/голосовые/видео, native панели, бизнес/FAQ/web media и завершение процессов в собственной тестовой копии. Только после этого сокращать необязательные форматы, TLS/backend/style-плагины или дубликаты сервиса.
5. Поставлять complete fixed WebView2 offline и сохранить все обязательные сторонние notices. Этот аудит ничего не скачивает и не меняет текущую установку.
