# Сборка тестового нативного runtime

`assemble_native_runtime.py` — инструмент сборки. Он не запускает MAX,
MData-помощник, браузер или установщик и не обращается к профилям. Python нужен
только на машине сборки; в результат он не попадает.

Сборщик проверяет полный состав immutable baseline, существующие точные патчи
accounts/privacy/isolation и сгенерированный native QML. Все патчи проверяются
обратным применением. Общий plugin/core, самостоятельный transfer helper и
фиксированный WebView2 берутся из отдельных проверенных receipts. Помощник
сохраняет собственный core и собственные Qt/VC/Rust материалы. Копии файлов
независимы, hardlink/reparse/private/development файлы отвергаются.

Пример из PowerShell в `D:/ValMAX`:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = 'D:/ValMAXBuild/tmp'
$env:TMP = 'D:/ValMAXBuild/tmp'
& D:/CodexLabs/max-mod-26.35.1/venv/Scripts/python.exe `
  native/tooling/assemble_native_runtime.py `
  --output D:/ValMAXBuild/build/native-developer-staging-NEW `
  --report D:/ValMAX/analysis/native-migration-v1/native-developer-staging-NEW.json `
  --qt-completion-proof D:/ValMAX/analysis/native-migration-v1/qt-runtime-completion-inputs-20261021.json `
  --qt-completion-materials D:/ValMAX/analysis/native-migration-v1/qt-runtime-completion-materials-20261021.json `
  --qt-sdk-supplement-proof D:/ValMAX/analysis/native-migration-v1/qt-runtime-sdk-supplement-20261021.json
```

Все входные receipts можно задать явно; `--help` перечисляет параметры.
Выход и report должны быть новыми. `--resume-output` разрешён только для
незавершённой собственной сборки без финального manifest: весь уже скопированный
runtime обязан точно совпадать с ожидаемыми байтами. Несовпадающие файлы не
перезаписываются и не удаляются. Готовый staging не обновляется на месте.

Проверки сборщика:

```powershell
& D:/CodexLabs/max-mod-26.35.1/venv/Scripts/python.exe `
  native/tests/test_assemble_native_runtime.py
```

Тесты используют собственные синтетические файлы в `D:/ValMAXBuild/build`.
Проверка PE импортов статическая: отдельно учитываются direct/delay imports,
архитектуры и каталоги `mod`, `mod/max-service`, `transfer`, `webview2`. PATH
и SDK не используются как замена отсутствующим packaged DLL. Наличие системной
DLL относится к ОС машины сборки; выполнение на чистой машине и произвольные
динамические загрузки этим не доказываются.

Перед большой копией проверяется полный будущий PE graph из frozen source
paths и фактических подготовленных patched bytes. При неполной closure режима
Qt completion новая большая копия даже не начинается. После копии проверка
повторяется уже на созданном runtime.

Состояние сборки 21.10.2026:

- Финальный runtime:
  `D:/ValMAXBuild/build/native-developer-staging-final-20261021/runtime`.
- 4149 файлов, 2606871947 байт; 435 PE, пропущенных direct/delay зависимостей нет.
- Унаследованные пропуски QtSerialPort/QtWebEngine закрыты точными официальными
  Qt 6.11.2 Windows x64 MSVC2022 архивами. SHA-256 сверены с официальными
  Metalink; исходные WebEngine plugins побайтно совпали с baseline. Все новые
  release DLL/process проверены как x64/6.11.2.0, без debug flag.
- QtWebEngineProcess, release resources и все locales включены. QtQuickWidgets
  и PrintSupport извлечены повторно из уже проверенных QtBase/QtDeclarative
  архивов и побайтно сверены с SDK. Путь процесса/resources задан через
  `LibraryExecutables=.` и `Data=.` в собственном `mod/qt.conf` согласно
  [инструкции Qt](https://doc.qt.io/qt-6.11/qtwebengine-deploying.html).
- Полные соответствующие source archives QtSerialPort/QtWebEngine, оригинальные
  license texts и SBOM включены. Отдельный статический Qt symbol audit проверил
  4681 импортируемый символ в 41 ребре, пропусков нет.
- Предыдущие промежуточные staging/proofs сохранены неизменными; они не являются
  финальным runtime.
- Два retention QML ресурса, требующие прежнего local helper, сохранены и
  перечислены в отчёте. Сам local helper/server/observer не включён.
- В baseline нет MAX/third-party notices. Qt corresponding sources помощника
  не подменяют материалы для всех Qt модулей MAX; применимость всей совокупности
  лицензий и отдельные Microsoft fixed runtime terms остаются незакрытыми.
- Последующий ограниченный аудит `native-distribution-materials-audit-20261021.json`
  сохранил текущие официальные Fixed Version terms, tagged Gum COPYING и малые
  существующие legal texts отдельно в `D:/ValMAXBuild/materials/native-distribution-audit-20261021`.
  Финальный runtime не менялся. MAX MSI содержит только placeholder в поле
  лицензии; matching provenance для части Qt/сторонних библиотек и историческая
  редакция WebView2 terms не установлены. Main MAX CRT 14.51 не привязывается к
  helper archive 14.44. Этот inventory не оценивает готовность распространения.
- Список всех cross-platform Cargo.lock entries шире Windows target notices.
  `libc`/`wasi` в сравнительном поле отчёта не означают, что эти crates входят
  в Windows runtime. SHA Cargo.lock совпадает с собранными Windows notices.

`releaseEligible` всегда `false`. Этот результат — developer staging, без
пользовательского установщика. Он не закрывает перенос retention, полную историю
экспорта, раннюю защиту или проверку жизненного цикла установленного клиента.
Упаковщик пользовательского выпуска должен продолжать блокировать эти пробелы.

Дополнительный loader smoke проверяет реальную загрузку 7 добавленных release
Qt DLL и 3 исходных Qt plugins. Это отдельный Win32 `/MT` probe без Qt headers,
QML engines, plugin instances и вызовов exports. Пути и SHA встроены при сборке;
`LoadLibraryExW` разрешает только каталог целевого DLL, `FINAL/mod` и Windows
System32. PATH очищен, каждый загруженный модуль сверяется с FINAL inventory или
учитывается как системный. Probe работает в bounded job с лимитом один процесс.

```powershell
. D:/ValMAX/native/tooling/msvc_env.ps1
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = 'D:/ValMAXBuild/tmp'
$env:TMP = 'D:/ValMAXBuild/tmp'
& D:/CodexLabs/max-mod-26.35.1/venv/Scripts/python.exe `
  native/tests/qt/run_loader_smoke.py `
  --staging-proof D:/ValMAX/analysis/native-migration-v1/native-developer-staging-final-20261021.json `
  --build D:/ValMAXBuild/build/qt-loader-final-smoke-NEW `
  --report D:/ValMAX/analysis/native-migration-v1/qt-loader-final-smoke-NEW.json
```

Зафиксированный результат — `qt-loader-final-smoke-20261021-v2.json`: 10/10
targets, 109 загруженных modules, exit 0 за 8.248 s, без timeout и оставшихся
процессов. FINAL inventory совпал до/после. MAX, QtWebEngineProcess, браузер,
transfer helper и hooks не запускались. Это loader proof, не тест Qt instances
или полного клиентского сценария. Поле `checks` считает также блоки хеширования
и проверки строк и не является числом функциональных тестов.
