# Перенос runtime ValMAX на C++ и Rust

Упомянутые пути `analysis/native-migration-v1/...` обозначают локальные отчёты проверки; эти файлы не входят в публикуемые исходники.

### Локальная тестовая установка, 24.10.2026

По запросу пользователя текущие перенесённые компоненты установлены вместо
8.8.4 как `8.9.0-native.preview.2`. Аккаунты используют прежний MXMOD; старая
программа и проверенная копия данных сохранены для отката. Нативная копия
открыла существующий аккаунт и страницу оптимизации, установщик завершился0.
Отчёт установки (`analysis/native-migration-v1/native-preview-installed-20261024.json`)
отделяет этот реальный ограниченный smoke от полного feature testing.
Это явный `local-preview` с сохранёнными blockers, не завершённая миграция
или публичный релиз. Переписывание остаётся приостановленным пользователем.

### Нативная проверка окружения до доступа к аккаунтам, 21.10.2026

Общий `native/common/startup_context` подключён к двум production entrypoints.
Launcher проверяет собственный package/AppContainer context до KnownFolder,
driver и профиля; transfer helper — до `QGuiApplication` и `Controller`.
Неопределённый или неподдерживаемый context завершает запуск с понятным
сообщением. Оба KnownFolder-вызова используют `NO_PACKAGE_REDIRECTION`.
Старые exit codes и Rust ABI сохранены. Guard не выполняет reentry и не
доказывает отсутствие унаследованной VFS; полный desktop-reentry parity открыт.

Итог component integration (`analysis/native-migration-v1/native-startup-context-integration-20261021.json`):
53 context checks, 70 launcher policy, 191 fake-core / 231 actual frozen-core,
329 helper checks PASS; production helper открыл idle QML window и штатно
закрылся с пакетными Qt/Rust/CRT без Python/SDK в PATH. Это собственные
синтетические проверки, без реального импорта, MAX, профилей или WinCred.
125 source bindings / 63 unique sources сверены с текущими файлами.

Новый launcher скомпилирован против прежнего immutable полного runtime.
Новый helper собран и проверен отдельно, но в это runtime дерево ещё не
включён; готового общего установщика данный этап не создаёт. Старые receipts
не переписаны, установленная ValMAX не изменена.

Отдельный system-only loader proof (`analysis/native-migration-v1/native-system-contract-loader-proof-20261021.json`)
подтвердил 4 API-set symbol resolutions текущей Windows. Функции не вызывались,
payload DLL не загружались. Это не clean-Windows или installed-client proof.

### Уточнение публичных Qt-контрактов, 21.10.2026

Статическая регистрация `QQmlContext::setContextProperty("application", QObject*)`
теперь связана с точным классом `Ui::Application::Application`, а не только с
именем из QML. Binding proof (`analysis/native-migration-v1/public-application-binding-20261021/binding-proof.json`)
связывает literal/xref, минимальные constructor slices, RTTI и Qt schema revision
13: 3 метода / 8 свойств. Типизированный `accountModel` ведёт к
`GuiUtils::AccountModel`; его revision 13 schema содержит смену текущего
аккаунта, `currentAccountId`, `activeAccountDependency` и методы выбора.
`ready` — read-only bindable property, flags `0x02015001`, не команда запуска.
В этих двух direct schemas нет объявленного потока received/update/deleted
или pause-before-start; унаследованные интерфейсы и порядок фоновых событий
остаются неизвестными.

Bounded EOF findings также не дают сертификата полноты истории:
`hasMoreForward=false` бывает начальным состоянием, а `historyStateChanged`
уведомляет `isUnreadHistory` (`analysis/native-migration-v1/public-history-properties-contract-20261021.md`).
Factory/session/link branch (`analysis/native-migration-v1/public-history-parent-completion-surface-20261021.json`)
не установила успешный ACK/ошибку, связанные с конкретными history view/request.
Messages-search schema (`analysis/native-migration-v1/public-history-search-moc-20261021/scan-manifest.json`)
описывает результаты запроса поиска; её `searchInProgress`, `nothingFound` и
`totalCount` не доказывают EOF всей переписки. Пустая очередь и таймер также
не заменяют этот контракт.

Это read-only static evidence по закреплённым копиям и stock QML, без запуска
MAX, профилей, сообщений, сети или новой реализации private hooks. Четыре
продуктовых release gates остаются открытыми; текущие сборочные результаты
и startup guard этим уточнением не пересматриваются.

Дата инвентаризации: 16 октября 2026. Исходная точка — реальные исходники
`D:/ValMAX/modkit`, установочный payload 8.8.4. Этот документ фиксирует объём
переноса и критерии приёмки; он не подтверждает, что перенос уже выполнен.
Машиночитаемая карта исходников, SHA-256, публичных операций и проверок:
`D:/ValMAX/analysis/native-migration-v1/inventory.json`.

Текущий итог на 21 октября 2026: рабочая установленная версия остаётся 8.8.4
на прежнем runtime. Обычный C++ запускатор собран и проверен на собственном
тестовом клиенте. Отдельный помощник MData собран, проверен и дополнен
материалами распространения. Вход с MData из настроек и обоих экранов входа
переведён на нативный запуск помощника с подтверждённым завершением клиента.
Общий C++/Qt plugin собран с новой Release Rust DLL и проверен в отдельной
копии Qt без SDK/Python в PATH. Полный нативный клиент и его установщик ещё
не проверены в сборе. Гибридный
установщик с Python не является промежуточным пользовательским релизом.

### Последнее изменение: проверка процессов до подготовки профиля

Последующий lifecycle review нашёл пропущенную интеграцию старой политики:
нативный launcher узнавал только собственный точный executable path и вызывал
изменяющую подготовку MXMOD до поиска клиента. Работающая ValMAX из другой
папки могла быть пропущена. В Python уже был `max_client_policy`; его готовый
C++ эквивалент `native/transfer/closed_clients.cpp` использовался помощником
MData, но не launcher. Теперь launcher использует этот же неизменённый модуль:
исключается только точная оригинальная пара EXE + соседний config.dll.
Проверка не зависит от папки установки и учитывает portable/developer-копии.

Сканирование теперь идёт до подготовки профиля/runtime. Свой работающий main
получает только read-only recovery/config guard и активацию окна. Другая
потенциальная MXMOD-копия, отдельный собственный service или неизвестный scan
останавливают запуск до записи. Только после sampled `Absent` допускается
подготовка, затем read-only guard и scan повторяются перед spawn.
Новый код ошибки `profile_in_use` добавлен без изменения старых exit codes.
Нет нового пути переопределения профиля из CLI/окружения.

Read-only guard проверяет только наличие технического recovery marker и
использует прежний Rust check-only API. Он не создаёт исчезнувший config при
активации. Бюджет process scan проверяется в каждой итерации и после enumeration;
Windows I/O не объявлен прерываемым. Неподконтрольный прямой запуск между
наблюдениями остаётся ограничением, как и у прежней sampled проверки.

`launcher-lifecycle-real-core-20261021.json`: 70 policy, 191 fake-core и 231
actual frozen Rust-core checks PASS. Проверены service-only/foreign-main/
foreign-service/unknown отказы без создания config/runtime, сохранность
синтетического состояния аккаунта/config при активации, отсутствие создания
исчезнувшего config, recovery marker regular/empty/directory/junction/broken.
Отдельно 21 input boundary checks PASS. Production fixture скомпилировала
настоящий GUI entrypoint и проверила byte-identical четыре stock icon frames,
но не исполняла его. Все процессы/данные fixture собственные; настоящие MAX,
профили и WinCred не использовались. Test PID seam существует только в тестовой
компиляции, production всегда использует Toolhelp.

Новый matching launcher собран по прежнему неизменённому native runtime;
`launcher-profile-guard-build-20261021.json` фиксирует точные bytes и shared
source hashes. Launcher SHA:
`89bbaa75ec1de82ef9fe3109a743bbaed9a2fced32f756a48c9105d2dec59ff7`.
`native-profile-guard-integration-final-20261021.json` связывает новые receipts:
372 текущие source rows / 256 уникальных файлов согласованы, старые 306 rows
не выдаются за current после изменения launcher. Preflight снова подтвердил
неизменённые 4 150 runtime files, отсутствие missing dependencies/legacy QML
references и static guard identity 10/8. `artifactValidation=true`,
`releaseEligible=false`; installer не собирался, production GUI/реальный MAX
не запускались. Полные gates retention/server EOF/early protection/installed
client остаются незакрытыми.

Для следующего настоящего own-profile smoke не подтверждена отдельная Windows
guest: `WindowsSandbox.exe` отсутствует, просмотр Hyper-V VM inventory получил
access denied. Это не доказывает отсутствие VM. Обычный production launcher
использует KnownFolder текущего SID; смена environment `LOCALAPPDATA` сама по
себе не изолирует профиль или WinCred. Рабочая установленная копия не менялась.

### Предыдущий этап: нативные настройки retention и согласованные DLL

21 октября настройки и статус сохранения удалённых сообщений переведены на
`RetentionTransport` → C++ `Bridge` → Rust `retention_preferences`. Файл
`message-retention-settings.json` сохраняет прежний формат и ревизию. Чтение
не переписывает старые байты; выключение использует атомарную запись и CAS.
Включение возвращает `retention_unavailable`, пока нет архива и источника
событий. Сохранённое ранее `enabled:true` отображается как пожелание, а не
работающая защита. Счётчики неизвестного архива остаются `null`.

Контроллер не опрашивает отсутствующий архив. В новых сгенерированных QML/JS
нет обращений к прежнему localhost helper; Python нужен только инструментам
сборки и проверок, в новый runtime он не входит. Это завершение переноса
настроек и транспорта, а не реализации самой функции retention.

Проверки текущих байтов:

- `retention-preferences-unit-proof-20261021.json`: 13 Rust проверок, включая
  прямой Runtime C ABI, строгий формат, отсутствие записи при отказе и CAS.
- `retention-config-native-ui-final-v2-20261021.json` и повтор: два прохода
  по 76 проверок фактического QML transport и функций панели/контроллера.
  Проверены отмена, очереди, исключения и повторный вход во время `invoke`.
- `settings-transport-reentrant-regression-20261021.json`: 52 проверки
  фактического общего `SettingsTransport` с синтетическим Bridge. Отмена во
  время `invoke`, исключения, неверные/повторные ID и поздние ответы не
  оставляют pending-запросов и не подменяют другой запрос.
- `qt-retention-config-runtime-20261021.json`: 437 проверок пересобранного
  plugin с новой Release Rust DLL в отдельном Qt runtime.
- `retention-config-real-runtime-20261021.json`: 36 проверок через настоящие
  plugin/core в трёх отдельных процессах. Прочитаны старые байты без записи,
  проверены отказ включения, конфликт ревизии, выключение и чтение после
  перезапуска. Использован собственный синтетический профиль.

Статическая сверка обнаружила ошибку прежнего комплекта: 7 из 10 записей
встроенных проверок DLL защиты относились к другой тестовой копии MAX.
Те же три DLL пересобраны с точными SHA/размерами текущих восьми target PE;
логика обработчиков не менялась. `hooks-retention-config-build-20261021.json`
и `guard-targets-current-assembler-revalidation-20261021.json` подтверждают
10 согласованных записей. Это необходимая согласованность файлов, а не
проверка фактического включения защиты при запуске.

Assembler и packager теперь требуют эту сверку по фактическим байтам,
неизменным sidecar/header/DLL и полному inventory. Проверки verifier — 16 PASS;
упаковщика — 45 PASS. Общий runtime теперь собран отдельно в
`C:/ValMAXBuild/build/native-retention-config-staging-20261021/runtime`:
4 150 файлов / 2 606 923 981 байт, inventory SHA
`a36e66d97b61157c0679df0cb007fcbfc4a385661dfe0dda80a7cc24cfd1b79a`.
`native-retention-config-staging-20261021.json` фиксирует 435 PE, отсутствие
неразрешённых QML references и direct/delay зависимостей, согласованность
10 guard records. Matching launcher собран в
`D:/ValMAXBuild/build/launcher-retention-config-20261021/ValMAX.exe`, SHA
`62e30e37079f0036f6472de8cd009f435718294a20d1695cc36acee698324bf1`.
Итог: `native-retention-config-integration-final-20261021.json` связывает
точные receipts; 306 source hash rows согласованы. Preflight сверил 4 361
dependency edges и нативные UI resources: `artifactValidation.verified=true`,
`releaseEligible=false`. Launcher и MAX на этом этапе не запускались,
пользовательский установщик не собирался. Статическая проверка не охватывает
imported symbols, dynamic loads и запуск на чистой машине.
Четыре продуктовых release gates сохранены: архив retention,
достоверный конец серверной истории, ранняя готовность dynamic protection
и полный установленный клиент. Рабочая 8.8.4 и реальные аккаунты не менялись.

Дополнительная обычная разведка не закрыла два контракта:
`public-history-parent-completion-surface-20261021.md` не установил ACK/EOF
в history dependencies, URL loading signals или QObject superclass;
`ordinary-startup-readiness-gates-20261021.md` отделяет готовность Rust Bridge
от готовности dynamic protection. Задержка UI до `Bridge.ready` сама по себе
не гарантирует защиту фоновых событий аккаунта. Новые private hooks и ранний
bootstrap не добавлялись.

### Предыдущий этап: нативная очередь и сбор страниц истории

21 октября добавлен `HistoryModelCursor`, обычный C++ объект над
`QAbstractItemModel`. Он забирает ограниченные партии роли `messageContext`
и следит за изменениями модели. В экспортёре больше нет повторного прохода
от нулевой строки и пустого `DelegateModel` для всей истории. Необработанный
остаток партии возвращается в очередь; reset, смена аккаунта/модели и отмена
не могут отправить старую партию в новый экспорт. Объекты MAX читаются на их
Qt-потоке, без фонового доступа к QObject. Между сообщениями сбор уступает
GUI после бюджета 2 мс; один вызов SDK прервать этим механизмом нельзя.

Проверки привязаны к реальным новым исходникам и собранной DLL:

- `history-model-cursor-proof-v2-20261021.json`: 80 C++ и 10 QML проверок,
  включая индексы при insert/remove/reset, lease, смену потока, уничтожение
  модели и QObject/value-wrapper конверсию. На собственных 10 000 строках
  cursor прочитал роль 10 000 раз; контроль с повторным проходом — 255 000.
- `history-harvest-integration-final-20261021.json` и
  `history-harvest-integration-clock-repeat-20261021.json`: два прохода по
  109 проверок фактически сгенерированной функции сбора с настоящим cursor
  и plugin. Проверены границы байтов/времени, неготовый wrapper, отмена,
  исключения и синхронная смена состояния во время commit. DTO и ответы
  архивного writer здесь синтетические; это не проверка серверной истории.
- Поздний review обнаружил ошибку в вызывающем `historyStep`: он мог перейти
  к paging, пока неготовая локальная строка ещё pending. Генератор теперь
  проверяет pending-очередь до paging/history proof, после существующего
  30s timeout. Исправление проверено в `history-harvest-caller-final-20261021.json`
  и `history-harvest-caller-repeat-20261021.json`: два раза по 131 PASS actual
  generated `harvestPage` + `historyStep`. Ожидание после 500 мс не допускает
  fetch/proof; readiness без `dataChanged` подхватывается, timeout сохраняет
  честный partial. Воспроизведение прежнего дефекта сохранено отдельно.
- `native-list-work-queue-proof-20261021.json`: очередь загрузки строк больше
  не уведомляет QML, если её публичные счётчики не изменились. 52 проверки
  прошли. В пяти одинаковых синтетических прогонах устранены 40 000 лишних
  уведомлений и пересчётов binding; медиана этой работы в GUI-потоке
  сократилась со 126,3168 до 17,3627 мс. Это результат узкого теста очереди,
  а не ускорение всего MAX или измерение FPS.
- `qt-generator-model-cursor-final-20261021.json`: legacy-генерация и три
  скомпилированных ресурса переписки сохранены. Новый экспорт использует
  прямой native transport и cursor, таймер активного экспорта — 16 мс.
  Поздний caller guard отдельно регенерирован и проверен в
  `qt-generator-model-cursor-caller-20261021.json`; legacy и compiled
  preservation проверки также прошли.
- `qt-model-cursor-build-20261021.json` и
  `qt-model-cursor-runtime-20261021.json`: общий plugin пересобран с этими
  изменениями, проверена настоящая загрузка в отдельной копии Qt — 437 PASS.
  SHA-256 plugin: `0338a9ca33567c637514cf9ef69bd79ed26a4ef9368f10a5a6fbf7ca3d78d3bf`.
  Rust core не менялся; low-level EOF host в сборку не включён.
- `native-model-cursor-staging-20261021.json`: новый независимый runtime
  содержит 4149 файлов / 2606904203 байт, 435 PE; direct/delay зависимостей
  не пропущено. Python, observer и локальный сервер не включены. Два
  оставшихся legacy retention-ресурса явно перечислены как незавершённые.
  Старый `native-developer-staging-final-20261021.json` остаётся историческим
  неизменяемым артефактом и не обозначает текущий plugin.
- После позднего caller guard собрана отдельная актуальная копия:
  `native-model-cursor-caller-integration-final-20261021.json` объединяет
  assembly, новый matching launcher и read-only preflight. В runtime те же
  4149 файлов / 2606904203 байт; по сравнению с предыдущей копией изменён
  только `mod/max.exe` с QML resource. Launcher SHA-256:
  `73259ce7128bf2318c78007f2dfbb262faa688af596abdca3aa3e3ca2185efe5`.
  39 ресурсов и 4361 dependency edges согласованы, пропусков зависимостей нет.
  `artifactValidation.verified=true`, `releaseEligible=false`: это сборка
  для дальнейшей интеграции, пользовательский установщик не выпускался.
  Предыдущие immutable копии не изменялись.

`public-history-properties-contract-20261021.md` фиксирует ограничение
публичного API: `hasMoreForward=false` устанавливается уже в конструкторе,
до серверного ответа, а `historyStateChanged` относится к `isUnreadHistory`.
Ни один из них сам по себе не доказывает успешное получение последней страницы.
Пустая локальная очередь, пауза или отсутствие новых строк также не EOF.
Полнота всей истории остаётся незакрытым критерием, а не скрытым допущением.

`public-account-message-events-surface-20261021.md` подтверждает обычную
цепочку dependency текущего аккаунта и сигналы контекста отдельного сообщения.
Публичный поток received/update/deleted для всех чатов аккаунта пока не
подтверждён. `rowsRemoved` и открытие уведомления не подменяют такой поток.
В `public-account-message-events-moc-addendum-20261021.md` из static MOC
таблиц извлечены ещё три schema: Providers/ModelsDependency не декларируют
signals/methods, SharedMessage factory требует уже известные ChatId/MessageId.
Эта ветка не установила фоновый event stream; наличие иных публичных
источников остаётся неизвестным.

Новая установленная версия и сохранность входа в реальные аккаунты этими
тестами не проверялись. Рабочая ValMAX 8.8.4 и пользовательские профили не
изменялись; пользовательский установщик по-прежнему не выпускается.
Карта реализаций и границ проверок всех 16 групп функций:
`analysis/native-migration-v1/native-feature-inventory-16groups-20261021.md`.
Наличие component receipt отделено от проверки окончательного установленного
клиента; процент готовности из количества групп не вычислялся.

### Фото: проверка с включённым кэшем

`photo-cache-true-final-proof-20261021.json` содержит A/B и повтор каждого
варианта: baseline прошёл 19 проверок, candidate — 20, в обоих проходах.
Собственный Qt 6.11.2/D3D11 fixture использует
`cache:true`, фиксированный исходный decode-размер 704×180, буфер делегатов
500px, async Loader, текущую декларацию 120ms debounce и одинаковую маску
ShaderEffect. Изображения синтетические; MAX и реальные provider не запускались.

При ограничении полного Image видимой областью число запросов собственного
provider за семь фаз снизилось 56 → 42, пик активных полных Image — 10 → 4.
После settle у baseline было 3–7 offscreen Image, у candidate — 0 (краткий пик
1 при переходе). На resize burst добавилось 6 → 3 provider calls; при тёплом
возврате 3 → 0. Повторные счётчики совпали. Кэш использовался в обоих вариантах;
создание Image не тождественно новому decode. Все full requests выполнялись
в worker, а суммарная измеренная работа provider сократилась приблизительно
с 25–26 до 20–21 мс за весь сценарий. Это не GUI time/FPS и само по себе
не объясняет сильные лаги реального клиента.

Candidate остаётся тестовым. HistoryPreviewCell не получает прямую ссылку
на viewport; корректное наблюдение clipping/reparent/window и его стоимость
в настоящем MAX не установлены. Production UI по этому узкому тесту не менялся.
Старый результат 101 → 2 decode из fixture с `cache:false` не используется
как доказательство поведения рабочего кэша или ускорения клиента.

### Ранее проверенные результаты 21 октября

- `launcher-production-stock-icon-final-proof-20261021.json`: 53 проверки
  порядка/окружения, 115 проверок настоящего Win32 запуска собственного
  тестового клиента, 4 проверки описи и компиляция GUI entrypoint с иконкой.
  Stock PE используется только как compile-time источник иконки, не выполняется.
  Собранный пример привязан к синтетическому payload; production `ValMAX.exe`
  отдельно собран по окончательным файлам runtime, как указано ниже.
- `launcher-real-core-icon-regression-20261021.json` дополняет предыдущую
  проверку: 152 проверки C++ driver с настоящей зафиксированной Rust DLL и
  собственным тестовым клиентом. Проверены создание/сохранение config,
  отказ до запуска при плохих данных, bundled VC runtime, активация окна,
  передача управления MData и сохранность непрозрачного тестового состояния.
  Реальный вход в аккаунт и импорт MData этим сценарием не проверялись.
  Путь службы исправлен на штатный `mod/max-service/max-service.exe`, рядом
  с её собственным `config.dll`; дерево службы не переносится в корень.
  Проверка повторена после добавления icon RC в production CMake; 53 policy
  и 115 проверок с fake core также прошли. Старый roundtrip receipt сохранён.
- `native-transfer-recovery-materials-runtime-20261021.json`: 329 проверок
  preview/import/recovery controller и реального отображения Qt UI.
  Production helper штатно запускается без SDK/Python в PATH. Его новый
  пакет включает повторно проверенные материалы Qt/VC; это готовность
  самостоятельного помощника, а не всего ValMAX. Настоящий импорт и
  восстановление пользовательских аккаунтов не выполнялись.
- `mdata-handoff-proof-20261021-v2.json`: 131 проверка передачи управления,
  включая уничтожение исходной страницы. Ненулевой exit/тайм-аут не закрывает
  клиент; успешный запуск помощника не обозначается как успешный импорт.
- `mdata-transport-handoff-regression-20261021.json`: 201 проверка нативного
  экспорта/предпросмотра MData, отмены и жизненного цикла запросов.
- `export-native-media-rust-proof.json`: 47 проверок экспорта, файловых lease
  и авторизованного потокового writer. C++ сетевой worker отдельно прошёл
  51 проверку на временном loopback-сервере с тестовым Rust sink. В клиенте
  этот worker является HTTP-клиентом и не запускает локальный сервер.
- `export-download-roundtrip-proof.json`: 180 сквозных проверок уже с настоящей
  Rust DLL. Синтетическое вложение проходит C++ HTTP worker, Rust writer,
  подтверждение и импорт в архив с побайтовой проверкой. HTTP 404 и отмена
  освобождают временную загрузку; отменённый архив не публикуется, прежний
  архив не меняется. Полнота истории в этом тесте явно не заявляется.
- `qt-ordinary-export-mdata-build-20261021.json` и
  `qt-ordinary-export-mdata-runtime-20261021.json`: общий plugin собран с
  зафиксированной парой Rust DLL/import library; 437 проверок прошли.
  Проверены настоящая загрузка plugin, три цикла QML-движка, регистрации
  media/MData/network, обычные host seams и ошибочная конфигурация.
  TLS использует bundled Schannel из тестового пакета при чистом PATH,
  внешние запросы не отправлялись. Сборочная опция `ALL_FEATURES` относится
  только к четырём проверяемым библиотекам, а не ко всем функциям ValMAX.
- `qt-generator-final-20261021.json` подтвердил неизменность legacy-генерации
  и сохранение трёх скомпилированных ресурсов переписки. Финальный guard
  `native-packaging-blockers-final-20261021.json` обнаруживает ровно два
  оставшихся localhost-ресурса: настройки и controller сохранения удалённых.
  Экраны входа и настройки MData больше не относятся к этим блокерам.
- `native/installer/valmax-native.iss` проверен компилятором Inno без выпуска
  EXE (`/O-`). `package_native_client.py` проверяет согласованность ресурсов,
  описи launcher и зависимостей. Выбор папки ONEME/MXMOD запрещён.
- Managed upgrade: `native-installer-upgrade-webengine-proof-20261021.json` содержит
  64 проверки настоящей silent установки/обновления/удаления с отдельным AppId
  на собственных каталогах. Старое дерево сохраняется целиком, при сбое
  возвращаются файлы и точная ветка регистрации. Проверены восстановление
  после прерывания процесса, занятый mutex, оставшиеся браузерные процессы,
  reparse/неизвестные каталоги и повреждённые журналы. Внешний тестовый профиль
  сохраняется. Это не проверка устойчивости к потере питания и не ручной
  мастер возврата к успешно заменённой версии.
- `native-upgrade-compatibility-final-20261021.json`: 121 проверка совместимости
  прежних форматов настроек с настоящей Rust DLL. Чтение не переписывает файлы,
  нативные изменения читаются старым форматом и сохраняются при перезапуске;
  config и непрозрачная синтетическая БД аккаунтов остаются побайтно прежними.
- `native-package-release-eligibility-v3-materials-final-20261021.json`: 32 проверки
  допуска к выпуску и сопроводительных файлов. Обычная упаковка требует
  подтверждений всех функций на тех же байтах; developer staging не может
  компилироваться как пользовательский установщик. В пакет добавляются
  инструкция на двух языках, собственная MIT license и сторонние notices.
  Диагностический preflight проверяет реальные байты и зависимости даже при
  известных функциональных блокерах, не ослабляя запрет пользовательского выпуска.
  Повтор выполнен после уточнения RU/EN third-party notices: прежняя таблица
  Python 8.8.1 явно отделена от нового нативного runtime; native payload не менялся.
- `native-developer-staging-final-20261021.json`: собран полный независимый
  каталог runtime, 4149 файлов / 2606871947 байт. Для 435 PE проверены direct/delay
  зависимости, пропусков на машине сборки нет. Добавлены точные release-компоненты
  Qt 6.11.2, WebEngine process/resources/locales и соответствующие материалы
  исходников. Python, локальный сервер и observer в этот каталог не включены;
  два оставшихся retention QML ресурса сохранены как явный блокер функций.
- `qt-runtime-completion-final-symbols-20261021.json`: проверен 4681 символ
  в 41 ребре зависимостей добавленных Qt-компонентов и исходных plugins.
  `qt-loader-final-smoke-20261021-v2.json`: фактическая загрузка 10/10 DLL/plugins
  при очищенном PATH завершилась с exit 0; все 109 загруженных модулей сверены
  с runtime или отнесены к Windows. MAX, браузер, helper и hooks не запускались,
  Qt engines/plugin instances не создавались. Финальный runtime не изменился.
- `launcher-native-final-icon-20261021.json`: production C++ запускатор
  пересобран по описи именно этого финального runtime. Компиляция и сверка
  исходников/файлов прошли; группа и четыре кадра иконки совпали побайтно.
  EXE не запускался, `distributionReady=false`. Это заменяет необходимость
  будущей пересборки синтетического примера, но не проверку всего клиента.
- `native-final-icon-release-preflight-20261021.json`: реальные байты runtime,
  запускатора и 39 ресурсов согласованы; проверено 4361 ребро direct/delay
  зависимостей. Отсутствующие подтверждения функций отделены от целостности
  файлов; два legacy QML ресурса по-прежнему блокируют пользовательский выпуск.
  На этом этапе версия релиза не назначалась, payload/ISCC не выпускались.
- `native-distribution-materials-audit-20261021.json`: отдельно собраны небольшие
  имеющиеся/официальные тексты с происхождением и SHA; большие архивы не дублируются,
  финальный runtime не менялся. Материалы helper не подменяют main MAX CRT и другие
  компоненты. Для MAX, части Qt и сторонних библиотек полного matching provenance
  пока нет; историческая применимость текущих WebView2 terms не установлена.
  Это технический inventory материалов, не подтверждение права распространения.

Незавершёнными остаются устранение функциональных блокеров в собранном runtime
и проверка функций в установленном клиенте, серверная проверка вложений/полноты
истории, готовность защиты с самого
старта и подключение нативного архива retention и источника событий. Зависимость
его интерфейса от прежнего сервера устранена на последующем этапе выше. Механизм
управляемого обновления уже проверен отдельно; его проверка со всем конечным
клиентом ещё предстоит. Независимая карта функций и ограничений находится в
`analysis/native-migration-v1/native-surface-audit-20261021.md`.
Работа над низкоуровневым EOF hook остановлена после
отказа автоматической проверки с причиной «possible cybersecurity risk».
Его подготовленные исходники и синтетические результаты не являются
разрешением включить этот обработчик в production-сборку. Ранее отклонённые
retention/early-bootstrap работы также не возобновляются через другой путь.

## Что означает готовый перенос

Пользователь устанавливает один EXE и получает существующие функции ValMAX.
В установленной программе нет Python-интерпретатора, PyInstaller/PYZ, PySide,
Tkinter, запуска Python из Rust/C++, скачивания компиляторов, библиотек или
дополнительного runtime при первом запуске. Python допустим в инструментах
разработчика, генераторах ресурсов и тестах совместимости.

QML остаётся штатным интерфейсом Qt. Его JS-выражения не являются Python-runtime
и не требуют механической переписи каждой кнопки. Сервисные вызовы QML переходят
на собственный Qt C++ bridge; тяжёлые операции исполняются вне GUI-потока в Rust.
Локального HTTP/TCP-сервера и постоянного диагностического наблюдателя нет.
Разовая утилита импорта/восстановления может быть отдельным нативным EXE.
Именованный канал Windows допустим для активации существующего окна и таких
операций; штатные клиентские IPC MAX не нужно ошибочно запрещать вместе с HTTP.

Rust-прокси перед `privacy_server.py`, встроенный CPython, работающие только
настройки в отдельном демонстрационном окне или исчезнувшие функции не выполняют
эту цель. Каждая функция ниже должна работать в установленной копии MAX.

## Что работает сейчас и требует замены

`bundle_entry.py` выбирает четыре роли: `launcher`, `observe`, `recover`, `mdata`.
`launch_mod.py` проверяет точные файлы/патчи, готовит полный config, изолированное
окружение и запуск в пользовательском desktop, восстанавливает окно из трея.
`privacy_server.py` запускает поток HTTP на 127.0.0.1:18973, инструментирует
проверенные процессы через Frida и обслуживает практически все настройки и
длительные операции. Его имя не описывает фактический объём ответственности.

До возобновления нового клиента подтверждаются включённые ghost/retention hooks
и политика PIN-уведомлений. Ошибка этой стадии не должна превращаться в успешный
запуск с визуально включённой, но неработающей защитой. Сохранение настройки и
подтверждение её применения — разные состояния.

Аналитика уже отключается постоянными патчами из `privacy.py`: session creation,
event queue, используемые Tracer entry points и MyTracker initializer службы
звонков. `isolation.py` сохраняет MXMOD namespace, отдельный instance/pipe,
отключение автообновлений и опасного глобального taskkill. `accounts.py`
сохраняет локальную поддержку нескольких аккаунтов. Эти build-time патчи
должны остаться без зависимости от диагностического наблюдения. Статическая
проверка байтов сама по себе не доказывает отсутствие всех сетевых утечек.

## Целевые компоненты

| Компонент | Ответственность | Граница |
| --- | --- | --- |
| Нативный запускатор | Проверка manifest/payload, startup receipt, MXMOD config, desktop launch, активация окна, recovery guard, загрузка модуля до защищаемых действий | C++/Win32 либо Rust/Win32; одноразовый процесс |
| Qt bridge | QObject API настроек, сигналы результатов/прогресса, QML image provider, выбор файлов, жизненный цикл страниц/аккаунтов | C++ с ABI именно Qt клиента |
| Native hooks | Ghost + local unread bookkeeping, retention, notification redaction, доказательство конца истории | C++ callbacks с точными ABI/байтовыми проверками; in-process, без Python/JS observer |
| Runtime library | Форматы/валидация, атомарные stores, crypto, MData, PIN, SQLite индексы, export HTML/media, inline-media transport, support report | Rust; ограниченные worker queues |
| Импорт/восстановление | Оффлайн импорт, backup/rollback, recovery UI и повторный запуск клиента | Нативный Qt UI + тот же Rust library, без Tkinter |
| Build tools | Генерация QML/resources, постоянные патчи, сборка, inventory/лицензии | Python допустим только на машине сборки |
| Необязательная диагностика | Числа кадров/очередей и конечные коды ошибок | Явное включение, без токенов/переписки/сырых пакетов |

В payload 8.8.4 `runtime/mod/Qt6Core.dll` имеет FileVersion **6.11.2.0**.
Кроме версии надо проверить архитектуру, compiler ABI, debug/release и реальный
набор Qt modules. Две разные Qt внутри одного процесса недопустимы. Загрузка
bridge в реальном MAX и выдача QObject в его QQmlEngine — отдельная обязательная
проверка; успешная сборка standalone Qt-примера её не заменяет.

Предлагаемая FFI: versioned C ABI, явные длины и владение буферами, opaque handles,
request ID + generation, cancellation, ограничение размеров и глубины значений,
конечные коды ошибок. Rust panic/С++ exceptions не переходят ABI. Qt QObject и
модели используются только на владеющем GUI-потоке. Нельзя переносить QObjects
в Rust worker либо отдавать им указатели на временную память модели. Завершение
задачи после закрытия страницы/смены аккаунта отбрасывается по generation.

## Полная карта функций

| Функция | Исходный контракт | Сохранить при переносе |
| --- | --- | --- |
| Запуск/трей | `bundle_entry`, `launch_mod`, `desktop_process`, `client_activation`, `startup_status` | Один экземпляр, повторный ярлык разворачивает существующее окно, запуск без оригинального MAX, конкретная причина ошибки |
| Приватность и постоянные патчи | `privacy`, `isolation`, `accounts`, `build_mod`, `verify_payloads` | Полные SHA-256, ожидаемые байты/обратимость; пропуск аналитики не блокирует вход/звонки; диагностический счётчик необязателен |
| Аккаунты | `account_preferences`, `account_preferences_panel` | Название, цвет, расписание, стабильные ID, не перемешивать аккаунты; все сохранённые поля round-trip |
| Быстрые ответы | `quick_reply_store`, composer/panel | Глобальные и account-specific команды, приоритет account-specific, до 10 частей/вложений, локальные файлы, автоприкрепление и закрытие подсказки; отправка только пользователем |
| Оптимизация | `optimization_store`, `optimization_gates`, `ValMAXWheelAdmission`, `ValMAXListWorkQueue`, `ValMAXAsyncListRow` и transforms | Все 11 флагов, desired/active/restartRequired; ограничение колеса по представленным кадрам, 5 строк после остановки, видимые строки раньше prefetch, stale generation, reuse/caches, профиль/контакты/звонки/переписка |
| MData | `mdata_*`, `session_*` | Компактный login v2, выбранные аккаунты, inspect → fingerprint → confirm, импорт поверх существующих аккаунтов, legacy profile v1 и maxsession чтение, rollback и restart |
| Вход/Recovery без основного окна | `mdata_import`, `mdata_recovery`, `mdata_recovery_state` | Ярлыки `mdata`/`recover`, выбор файла/пароля, проверка завершения owned clients, durable recovery guard, восстановление после обрыва |
| Режим призрака | `ghost_store`, `ghost_runtime`, `ghost_native`, `ghost_unread_native`, `ghost_monitor.js` | Hide typing/read/online/stories, runtime ACK, локальные непрочитанные без серверного read; сброс bookkeeping при потере соответствия/аккаунта/полной синхронизации |
| Удалённые сообщения | `message_retention_*` | Уже полученные сообщения остаются в штатной БД, входящий event + sync-row paths; раздельные account/chat IDs, received IDs, persisted trash marker после рестарта; без выдумывания неполученной истории |
| PIN | `chat_lock_service`, `chat_lock_controller`, `chat_lock_ui` | Set/unlock/remove/lock/lock-all/status, account+chat scope, Избранное не получает чужой PIN, timeout grants, rate limit; Qt UI и отсутствие мерцания защищённой переписки |
| PIN-уведомления | `notification_policy`, `notification_native`, `notification_image`, monitor/updater JS | «Новое сообщение» без имени/аватара/текста лишь для защищённых чатов; обычные уведомления без изменений; catalog/policy ACK до включения PIN |
| Экспорт чата | `chat_export_*` | Страницы/медиа/chunks/cancel/status, native history-end provenance, complete/partial различимы; HTML в стиле MAX, текст/фото/видео/voice/audio/files, SQLite spool, защищённые URL/авторизация вне архива |
| @pic / @vid | `inline_media`, composer | Результаты, кликабельные preview, оригинал в черновик, attribution, shared max-2 concurrency, Retry-After, URL cache/singleflight, отмена stale result; без автоматической отправки |
| Бизнес | `business_panel`, `business_environment`, `business_ui` | Настоящая внутренняя WebView, навигация/OAuth в ней, отдельный MXMOD business-webview2 user-data folder; не внешний браузер |
| Профиль/ссылки | `profile_details_panel`, `profile_details_ui` | Уже разрешённые native contact contexts; server link или приглашение собственного account ID; не генерировать opaque URL из ID |
| Оформление | `valmax_icons`, `icon_resources`, `menu_icons_ui`, `popup_lifetime_ui`, panel transforms | Иконки без пропадания, BETA, русские формулировки, native UI tokens, прокрутка всех страниц в маленьком окне |
| Поддержка | `support_diagnostics`, `support_panel`, `native_crash_diagnostics` | Только allowlist версий/счётчиков/конечных ошибок, явное сохранение в выбранный файл, никакой отправки или чтения профилей для отчёта |
| Установщик | `package_mod`, `max-mod-setup.iss` | Один оффлайн EXE, обновление прежнего AppId/ярлыков, bundled dependencies, rollback program files, данные аккаунтов сохраняются |

Account settings хранят `ghost.hideRead/hideStoryRead/hideTyping/hideOnline/autoOffline`,
но текущий `GhostRuntime.status()` сообщает scope `all_accounts`. Эти сохранённые
поля не являются доказательством работающей индивидуальной ghost-политики.
Перенос должен сохранять данные и существующее поведение, не заявляя ещё не
реализованное. Server 2FA/session expiry остаются требованиями сервера.

## Старый HTTP-контракт: 42 операции

Ниже не план нового HTTP API. Это список обязательных обработчиков для прямых
вызовов bridge и асинхронных сигналов. Схемы тела/исходные методы находятся также
в `inventory.json`. Равенство множеств ключей, schema=1, ограничения и конечные
ошибки должны сохраниться, даже когда HTTP status больше не используется.

| Старый путь | Операции |
| --- | --- |
| `/status`, `/identity` | GET каждой |
| `/support-diagnostics` | GET preview, POST export |
| `/quick-replies`, `/session-transfer`, `/account-settings`, `/optimization-settings`, `/ghost-settings` | GET + POST каждой |
| `/mdata` | GET status + POST export/import |
| `/mdata/inspect`, `/mdata/export-selected`, `/mdata/import-confirmed` | POST каждой |
| `/message-retention/status` | GET |
| `/message-retention/lookup`, `/message-retention/settings` | POST каждой |
| `/chat-locks` | GET catalog |
| `/chat-locks/{status,set,unlock,remove,lock,lock-all}` | POST каждой |
| `/notification-privacy` | GET |
| `/inline-media/preview?id=…` | GET bytes+MIME, заменить QQuickAsyncImageProvider |
| `/inline-media/{search,select}` | POST каждой |
| `/chat-export/{start,page,media,media-start,media-chunk,finish,status,history,cancel}` | POST каждой |

Не ограничиваться этими путями: `OptimizationConfig.qml` синхронно читает локальный
bool snapshot, launcher использует `/identity`, recovery ждёт освобождения порта,
а hooks присылают события через Frida. Все эти скрытые зависимости тоже удаляются.
HTTP/XHR к настоящим удалённым сервисам и бизнес-страницам не являются локальным
наблюдателем; экспорт должен сохранить штатную авторизованную загрузку медиа.

## Сохраняемые данные и совместимость

- Namespace `%LOCALAPPDATA%/MXMOD`, credentials `@MXMOD`, instance `mxmod_instance`
  и штатный pipe `mxmod_local_pipe` сохраняются. Не импортировать ONEME автоматически.
- `config.json`: полный version-pinned документ, сейчас 74 поля, обязательные
  overrides из `launch_mod.CONFIG_DEFAULTS`, не урезанный JSON с тремя параметрами.
- `quick-replies.json`, `account-settings.json`, `ghost-settings.json`,
  `optimization-settings.json`: schema=1; optimistic revision — SHA-256 точных
  canonical UTF-8 bytes. Account IDs — строки, bool не целое число, duplicate keys
  и NaN запрещены. Unicode escaping/canonical order проверить golden fixtures.
- `optimization-active.json`: snapshot запуска рядом с runtime; его нельзя
  включать в поставляемый payload или менять за спиной уже работающего клиента.
- `message-retention-settings.json`, `retained-messages.sqlite3`,
  `received-message-ids.sqlite3`: точные SQLite schemas/application_id,
  account/chat/message primary key, namespace и journal safety. Индекс хранит ID
  и метки, а сохраняемое содержимое остаётся в штатном message storage.
- `chat-locks.json`: до 256 scopes `accountId:chatId`; PBKDF2-HMAC-SHA256,
  210000 итераций, соль 16 байт, verifier 32 байта под Windows DPAPI с entropy
  `ValMAX.ChatOpeningPIN.v1`; постоянные failures/blockedUntil и временные grants.
  Не заменять DPAPI plaintext/самодельным шифрованием ради совместимости.
- `.mdata` profile magic `MXMODMD\x01`, login `MXMODMD\x02`, `.maxsession`
  `MXMODSA\x01`; AES-256-GCM + scrypt N=32768/r=8/p=1, salt16, AAD и границы
  chunked stream точно по действующему формату. MData chunk 1 MiB, отдельная
  финальная запись; tamper/truncation/reorder должны отвергаться.
- Компактный login переносит выбранные `auth_<ID>` и точные `access_token_<ID>@MXMOD`.
  Текущий лимит компактного импорта — 3 аккаунта. Legacy full-profile и recovery
  используют более широкий явный whitelist; не смешивать их с обычным export.
- `MXMOD-MData-Recovery.json`, `MXMOD-MData-Backups`, `mdata-result.json`,
  `session-transfer-result.json` и transfer mutex сохраняют согласованность
  между старой установкой и новым recovery. Не удалять marker при неполном rollback.
- Inline-media `.mxmod-helper-temp/inline-media` содержит только созданный этой
  функцией cache/staging; bounded cleanup не пересекает пользовательские файлы.
- Экспорт: отдельная новая выбранная папка, transient `index.sqlite`, media assets
  и опубликованный HTML. Не считать импортированный чужой путь разрешением перезаписи.

Во всех файловых компонентах сохраняются ограничения размера, deny reparse/links,
проверка identities открытых файлов, atomic rename/fsync, обработка заполненного
диска и отсутствие токенов/паролей в exception/error/status. Во время портирования
используются только синтетические профили и in-memory credentials. Двойная запись
старым и новым backend в один store запрещена.

## Зависимости установщика

Нативный пакет содержит launcher, совместимый Qt bridge, Rust library/worker,
нужные Qt DLL/plugins/QML/resources, SQLite/crypto/TLS и C/C++ runtime согласно
реальному PE dependency graph. Rust toolchain пользователю не нужен. Выбор
static MSVC CRT либо разрешённых app-local DLL фиксируется сборкой, а не надеждой
на Visual Studio/редистрибутив на машине пользователя.

Отдельный обнаруженный пробел: `business_panel.qml` сейчас предлагает установить
WebView2, если он отсутствует. Для цели «всё в установщике» нужно включить
подходящий оффлайн/Fixed Version WebView2 runtime, настроить его путь и подтвердить
внутреннюю бизнес-вкладку на чистой Windows без системного WebView2. Внешние
business/auth страницы требуют интернет, но их runtime не должен докачиваться.
Версию/условия распространения и supported Windows проверить перед упаковкой.

Сохранить AppId `{419706B9-6FC7-46F8-B9FB-D7212FC91BAC}`, ярлыки ValMAX/MData/recover,
проверку закрытия только owned процессов. Обновление должно удалить известные
устаревшие Python/PYZ/Frida-Python файлы из program tree по старому inventory,
не касаясь MXMOD/credentials/пользовательских данных. Иначе чистая установка будет
нативной, а upgrade оставит старый runtime. Удаление приложения не удаляет аккаунты.
Лицензии MAX/Qt/третьих компонентов отдельно от MIT собственного кода; требуемые
notices входят в установщик. Перенос языка не обещает исчезновения AV-срабатываний.

## Первые переносимые чистые модули Rust

1. Canonical JSON/revisions/validation настроек: `ghost_store`, `optimization_store`,
   `account_preferences`, `quick_reply_store`. Сначала pure encode/decode + vectors,
   затем общий Windows atomic store adapter. Это сразу обслуживает реальные UI.
2. `session_archive` и `mdata_archive`: versioned потоковый codec, policy validators,
   AEAD и chunk validation. Golden compatibility в обе стороны с Python; Windows
   credentials и живой импорт подключаются позже через явные интерфейсы.
3. `notification_policy`, `ghost_runtime`, retention record validation: чистые
   shapes, revisions, lease/ack state machines со fake clock.
4. `chat_export_render` + validators: HTML escaping/render и bounded record model;
   затем SQLite spool и безопасный filesystem publisher.
5. `inline_media` pure URL/MIME/Retry-After/parsers/cache key; затем один bounded
   native HTTP transport с доказанным отсутствием локального listener.
6. `support_diagnostics` allowlist serializer; версии python заменить новым
   версионированным полем runtime, сохранив читаемость старых отчётов.

Перед переносом горячего скролла измерять текущие QML admission/presentation
и реальную native incubation отдельно. Python не рисует кадры. C++ queue должна
сохранить frame acknowledgement, bounded backlog и приоритет видимых строк;
одна лишь замена языка не подтверждает улучшение плавности.

## Проверяемые критерии завершения

Все критерии первоначально **не доказаны**. Машиночитаемые G01–G18 в inventory
задают доказательство, а не отметку о выполнении. Миграция закончена только после
проверки всего списка на итоговом установленном EXE.

1. **G01 Scope:** каждая функция таблицы и все 42 операции имеют нативного
   обработчика и end-to-end маршрут из реального QML; неиспользуемый Rust API не считается.
2. **G02 Packaging:** инвентаризация unpacked installer, PE imports+delay-load,
   runtime process/module trace не содержат CPython/PYZ/PySide/Tk/Frida-Python;
   нет launch/PATH fallback или скачивания зависимостей.
3. **G03 Transport:** анализ generated QML + native code и runtime bind/listen trace
   показывают отсутствие локального HTTP/TCP сервера и обращений к :18973.
   Старый recovery/launcher больше не зависит от освобождения порта.
4. **G04 Loading:** bridge загружается в настоящий MAX, его QObject доступен
   каждому нужному QQmlEngine; negative fixtures wrong SHA/ABI/slot прекращают
   активацию, unload/shutdown не оставляют callbacks на освобождённую память.
5. **G05 Startup:** синтетические cold/warm/tray/double-launch/desktop/recovery
   scenarios, enabled protection ACK до первого защищаемого действия; никаких
   зависимостей от установленного официального MAX.
6. **G06 Stores:** bidirectional golden vectors старого/нового codec, conflicts,
   duplicate keys, Unicode, races, disk-full/reparse/identity swap; исходные
   MXMOD данные остаются совместимы, protected verifier не ослаблен.
7. **G07 MData:** v1/v2/maxsession чтение, v2 создание, inspect/confirm TOCTOU,
   1–3 аккаунта, merge/collision, backup/rollback/restart, truncated/AEAD failures,
   abrupt-stop recovery; отсутствие секретов в артефактах и аргументах процесса.
8. **G08 Ghost:** реальные native fixtures проверяют четыре фильтра и локальные
   unread credits, destructor/thunk guards, account switch, stale ACK, sync reset.
   Серверную видимость проверять отдельно; fixture не заменяет серверное доказательство.
9. **G09 Retention:** event delete и sync row delete, same-client account switch,
   только known received IDs; persist/restart/trash marker, disabled и disk failure.
10. **G10 PIN/Notifications:** synthetic native notification display/edit,
    protected/unprotected/self-chat/account collisions, hidden name/photo/text,
    expired policy/lease, set/remove transaction order; Qt PIN UI reopen.
11. **G11 Export:** выбранный синтетический чат в native harness; страницы,
    duplicates, all media classes, chunk retry/cancel, partial vs proven end,
    archive opened оффлайн и playable media; неисполненный HTML/script escaping.
12. **G12 Media:** fixture provider + контролируемая публичная загрузка установленным
    native backend: search/preview/select original, draft/cancel/stale page,
    cooldown/singleflight/max2, invalid URL/MIME/oversize; сообщений не отправлять.
13. **G13 UI:** настройки, quick reply attachments, BETA/icons/profile links,
    маленькое окно/колесо/тёмная тема/языки и account switch; screenshots и QTest.
14. **G14 Performance:** одна synthetic dataset/размер окна/частота монитора и input
    trace до/после; frame p50/p95/p99, max stall, GUI work, memory, ready-row delay,
    repeat-open/minimize cache. Chats/contacts/calls/history-photo/profile/resize
    измеряются отдельно. На 180 Гц бюджет кадра 5.56 ms; не выдавать среднее за p99.
15. **G15 Business:** загрузка native WebView без системного WebView2/докачек,
    внутривкладочная навигация и раздельный user-data folder на чистой Windows.
16. **G16 Diagnostics:** opt-in/off, конечные allowlist fields, отсутствие сообщений,
    токенов, PIN, URL с auth и raw packets в логах/support/crash metadata.
17. **G17 Installer lifecycle:** offline clean install, upgrade 8.8.4→native,
    rollback, uninstall/reinstall, old shortcuts, stale-runtime cleanup, no
    profileIncluded/credentialsIncluded; запуск на Windows без dev toolchain.
18. **G18 Release:** installer SHA+полный file manifest+лицензии+source revision,
    CI/checks относятся к тем же bytes; готовый EXE действительно скачивается.

Ни один старый зелёный Python test run, mock-only smoke или наличие файлов
Rust/C++ не доказывает эти end-to-end критерии. До их выполнения цель остаётся
активной; рабочая 8.8.4 нужна как откат и источник compatibility fixtures.

## Готовый чистый потоковый кодек MData

`native/core/src/mdata_archive.rs` переносит формат MXMODMD v1/profile и v2/login:
AES-256-GCM, scrypt 32768/8/1, кадры по 1 MiB, манифест до 8 MiB и суммарные
файлы до 4 GiB. Политика имён credentials и путей приходит отдельно от архива;
файловая система, WinCred, реальные профили и сеть модулю недоступны.

`encrypt_stream` принимает фабрику `Read` и `Write` результата. `decrypt_stream`
принимает `Read`, фабрику приватных staging-sinks и необязательный count-only
preflight. Фабрика **не должна создавать окончательные пользовательские файлы**.
Даже после записи всех байтов остаётся проверка терминального кадра и EOF:
при любой ошибке весь staging отбрасывается; commit разрешён лишь после `Ok`.
Кодек возвращает чувствительный манифест только после этой проверки. Транзакция
импорта, backup/rollback/recovery и QML-подтверждение остаются отдельной работой.

`mdata_casefold.rs` содержит сгенерированные правила Python Unicode 16.0.0.
Генератор записывает свою SHA-256; сравнение путей использует полный casefold,
не Unicode lower и не нормализацию. Дифференциальная проверка сравнила все
1 112 064 допустимых Unicode scalar и Windows-ограничения путей.

В `session_archive.rs` RAII охватывает частично разобранные JSON-массивы,
объекты и ключи, ошибку после конца JSON и частично сформированные канонические
байты. Это best-effort очистка принадлежащих модулю буферов, а не обещание
гарантированного стирания всех копий в памяти.

Зафиксированное доказательство: `analysis/native-migration-v1/mdata-stream-proof.json`.
35 native contract tests и 45 Python↔Rust interop/differential checks прошли.
Архивы и временные артефакты содержат только искусственные данные и находятся
в `C:/ValMAXBuild/fixtures/mdata-stream-v1`. Проверены повреждения, обрывы,
перестановка/повтор кадров, terminal/EOF, частичные I/O, Unicode, точные схемы
и лимиты. Граница 4 GiB проверена метаданными, без выделения 4 GiB памяти.
Это доказательство формата, а не выполнения G07 или готовности установщика.

## Состояние прямого нативного runtime, 16 октября

Qt-плагин с Rust runtime загружен в настоящий QQmlEngine MAX в отдельной
офлайн-копии. Предварительные guards закрывают сеть, credentials и реальные
каталоги профилей; установленная пользовательская 8.8.4 не заменялась.
Ошибка тестового platform plugin устранена проверкой qwindows до создания
QGuiApplication. Неверный plugin path заканчивается конечной ошибкой без окна.

Четыре семейства настроек — оптимизация, режим призрака, аккаунты и быстрые
ответы — получили прямой transport QML → C++ → Rust. Генератор меняет этот
маршрут только в `--native-runtime`; обычная генерация ресурсов отдельно
сравнивается на идентичность. У быстрых ответов сохранены области аккаунтов,
части и метаданные вложений; добавление идёт в черновик, отправка вручную.
37 проверок настоящих QML-контроллеров и 428 Python↔Rust проверок store прошли.
Полный визуальный редактор и реальное добавление файлов в MAX ещё не доказаны.

C++ ghost port устанавливает пять replacements и семь listeners, включая
локальный unread ledger. ABI/pinned-module проверки и офлайн-запуск в MAX
подтвердили установку и получение Rust ACK. Этот запуск использовал выключенную
политику; видимость на сервере не проверялась. Настройка Save сама по себе
никогда не выставляет `applied`. Общий HookSet дополнительно исправлен:
переустановка и очистка trampoline запрещены, пока callback не завершился.
Результат запуска привязан к SHA DLL в конкретном proof; последующий relink
не переносит это доказательство автоматически на новые байты.

PIN service перенесён с PBKDF2-SHA256 (210000), Windows DPAPI, прежним форматом
`chat-locks.json`, долговременными счётчиками неудачных попыток и временными
grant. 75 совместимых проверок в обе стороны и нативная проверка отказа commit
прошли. Grant выдаётся после durable write и связан с конкретным verifier,
поэтому удаление/повторное создание PIN другим процессом не наследует доступ.

Host-only notification callbacks не доступны из QML. `prepare` получает только
нормализованные scope IDs и revision; его lease удерживается через commit/grant
и освобождается тем же OS thread. `refresh` идёт через Rust с тем же порядком
блокировок, чтобы старый снимок каталога не отменил новую защиту. Без реального
host ACK новый PIN не сохраняется. C++ фильтр уведомлений и его UI-интеграция
остаются отдельным незавершённым этапом.

Общая регрессия Rust после подключения этих маршрутов: **78 тестов прошли**.
Доказательства: `core-regression-proof.json`, `chat-lock-interop-proof.json`,
`notification-bridge-review.json`, `quick-reply-store-proof.json`,
`quick-reply-qt-proof.json`, `qt-ghost-max-proof.json` и
`qt-ghost-max-lifecycle-proof.json` в `analysis/native-migration-v1`.
Обязательные итоговые G01–G18 по-прежнему не закрыты: launcher, оставшиеся
native services/transports, полный MData import/recovery, export publisher,
offline WebView2, инсталлятор и end-to-end/performance проверки ещё нужны.

## Следующий проверенный этап: уведомления, privacy verifier и экспорт

В изолированной копии MAX настоящий `NotificationHost` установил шесть
replacements и два identity-listener. Через прямой C++ → Rust lease прошли
искусственные PIN set/lock/unlock/remove, возврат пустой политики и отзыв
подтверждения после shutdown. Проверены 60 host-сценариев и 15 QML-контроллеров.
Реальная доставка уведомлений ОС и запуск с пользовательскими аккаунтами этим
не доказаны. Подробности и точные SHA — `qt-notification-review.json` и
`qt-notification-max-lifecycle-proof.json`.

Отключение Tracer/MyTracker уже находится в статических патчах клиентских
бинарников и конфигурации. Новый `valmax_privacy.dll` проверяет шесть файлов,
31 патч и соответствие загруженных байтов. Он не наблюдает сеть, не читает
пакеты, не ставит callbacks и не восстанавливает телеметрию при shutdown.
`privacy-native-proof.json` содержит 34 native и 14 ABI проверки. Отдельный
`qt-privacy-max-proof.json` подтверждает MAIN-процесс; service-only правила
показываются как проверенные в сборке, без утверждения, что служба проверена
в работе. Это не гарантия отсутствия любых сетевых запросов MAX.

Потоковый `export_render.rs` совпал с прежним Python HTML побайтно на
дифференциальном корпусе; JSONL/manifest сравниваются структурно. Проверены
9 нативных сценариев и 549 случаев совместимости, включая фактический предел
миллиона сообщений и вложений. `export_storage.rs` пишет в принадлежащий
экспорту временный каталог: относительные операции от удерживаемых handles,
128-битные file IDs, отказ от reparse/hardlink/remote device, ограниченный
буфер текста, проверка SHA и заголовка вложения из одного потока. Готовая
папка публикуется без замены существующей только после успешного рендера.
Отмена удаляет только собственные объекты; ошибка очистки остаётся ошибкой
при повторной проверке. 13 storage integration и две unit-проверки прошли.
Модуль не обещает неизменность файлов против их владельца после закрытия
handles и публикации. Полный exporter с native history/coordinator/download
ещё не перенесён; chunk-адаптер пока ограничен 64 MiB.

Общая регрессия на этом снимке — **102 нативных теста прошли**, без пропусков.
`core-export-regression-proof.json` фиксирует SHA проверенной DLL, исходников
и Cargo.lock; `export-storage-proof.json` и `export-storage-review.json`
разделяют проверенные сценарии и оставшиеся ограничения. Rust-зависимость
SQLite собирается внутрь модуля (`rusqlite 0.40.2`, bundled SQLite), без
отдельной установки на компьютере пользователя. Сборщик лицензий
`native/tooling/collect_rust_notices.py` подготовил 114 файлов для 58 resolved
crates в `C:/ValMAXBuild/package-notices/rust-20261016-b`; этот список включает
build-time зависимости и не заменяет лицензии MAX/Qt/Gum/VC runtime.

Для retention создана только синтетическая очередь request/ACK с ограниченными
scope IDs. `retention-queue-proof.json` фиксирует 98 native и 21 ABI проверку;
реальных hooks в этой DLL нет, `ready/applied` всегда false. Этап переноса
SQLite-хранилища был остановлен автоматической проверкой с указанием возможного
киберриска. Его код не был записан, интеграция очереди и native gates не
продолжается. Это незавершённая часть общей цели, а не работающая замена старой
функции. Независимые этапы launcher/config/activation и WebView продолжаются.

Установленная ValMAX 8.8.4 и её профили по-прежнему не изменялись. Полностью
нативного установщика пока нет; G01–G18 в целом не закрыты.

## Нативный запуск и локальная диагностика: проверенный промежуточный результат

`launcher_config.rs` переносит полный 74-полевой шаблон и восемь обязательных
значений из прежнего launcher. Проверены 10 нативных тестов и 1120 сравнений
с Python; 17 случаев намеренно отклоняются строже (границы чисел/глубины JSON,
Unicode и безопасная запись пути). Исправлена совместимость целого `-0`.
Это пока чистые функции: создание профиля, блокировка переноса и восстановление
импорта этим модулем не выполняются. `launcher-config-proof.json` фиксирует
границы и SHA исходника.

Для открытия уже работающего окна есть C++ `activation` с проверками точного
пути EXE, PID, класса окна, владельца и состояния процесса перед действием.
72 проверки прошли: 64 на fake API, восемь на metadata собственного скрытого
Win32-окна. Реальный вызов show/foreground на пользовательской ValMAX здесь
не проверялся. Предел 2500 ms относится к бюджету повторов; это не обещание
жёсткого времени ответа всех Win32 вызовов. Подробности —
`launcher-activation-proof.json`; весь launcher ещё не заменён.

`support_diagnostics.rs` и маршруты `support.preview`/`support.export` работают
через нативное ядро. Отчёт schema 2 содержит явный native runtime вместо версии
Python; чтение прежней schema 1 сохранено. Копируются только фиксированные
числовые версии, булевы настройки, счётчики и конечные коды. Неизвестные поля
не сериализуются; недоступные наблюдения остаются null. Предпросмотр не создаёт
файлы. Сохранение использует выбранный локальный URL и публикацию нового файла
через открытый handle с `ReplaceIfExists=false`, без автоматической отправки.

Девять нативных проверок диагностики охватывают лишние поля, типы, URL,
одновременную публикацию, существующий файл/каталог/hardlink и junction родителя.
Четыре FFI-теста включают реальный preview/export и одинаковый список capabilities
при открытии и запросе версии. Ошибка после успешного rename может оставить
готовый новый файл; транзакционный откат такого случая не заявлен. Инъекции
disk-full/fsync пока нет. QML-интеграция фиксируется отдельным proof.

Общая регрессия этого снимка: **122 теста прошли, без пропусков**. Полный лог,
SHA DLL из `target/release/deps`, Cargo.lock и исходников записаны в
`core-support-regression-proof.json`; узкая область — `support-native-proof.json`.
Эти результаты не подтверждают готовность полного нативного установщика.

## Подготовка запуска и ошибка тестового WebView

Нативный preflight сверяет фиксированный набор из 1701 файла, размеры и SHA,
удерживая handles проверенных файлов и каталогов. Standalone EXE выполняет
только проверку; он не запускает клиент. Проверки: 71 native + 10 production
check-only. Handles защищают известные файлы от записи/замены, но не запрещают
создать новый дочерний файл в каталоге после инвентаризации. Полная проверка
зависимостей DLL и будущей политики их загрузки остаётся отдельным этапом.
Отчёты: `launcher-preflight-proof.json`, `launcher-preflight-review.json`.

`launcher_profile.rs` проверяет полный MXMOD config, сохраняет существующие
байты и неизвестные поля, создаёт отсутствующий config без замены чужого файла.
Любой recovery marker, включая пустой, повреждённый, каталог, hardlink или
занятый файл, запрещает изменяющую подготовку. Проверочный режим ничего не
создаёт и не даёт разрешение на запуск. Внешний C++ TransferGuard должен жить
до конца старта на том же OS thread; внутренний Rust mutex защищает только
вызов подготовки. Результат с путём не является удерживаемым доказательством
неизменности файла. Перед spawn нужна повторная проверка recovery/readiness.

Типизированная граница `valmax_launcher_prepare` возвращает 16-байтный результат
с конечными кодами без возвращаемых буферов. Семь profile и три boundary-теста
прошли на собственных каталогах и переименованных mutex. Положительные вызовы
границы пока проверены через unit callee; отдельный C++ binding проверен с fake
ABI. В работающую установку этот маршрут ещё не подключён. Точные данные —
`launcher-profile-proof.json` и `profile-client-proof.json`.

`startup_status.rs` переносит файл конечного результата запуска: явное владение
operation ID, отказ запоздавшему процессу и сохранение первой точной ошибки.
Пять тестов включают конкурентные попытки и занятый файл. Ошибка записи отчёта
не должна заменять основную ошибку запуска. UI-сообщения, process-local fallback
и передача operation ID в новый процесс этим store ещё не реализованы.

После этих изменений полная Rust-регрессия: **137 тестов прошли**, без пропусков.
`core-launcher-regression-proof.json` фиксирует лог, новые bytes DLL и исходники.
Старые проверки Qt/настоящего MAX остаются привязанными к их собственным SHA,
а не автоматически подтверждают новую DLL после перелинковки.

В комплект зависимостей подготовлен Fixed WebView2. Его обычный запуск проверен
на `about:blank` в отдельном профиле с включённой песочницей. Это не проверка
настоящего бизнес-сайта, входа или отсутствия фоновых запросов Chromium.
Публичные Qt DLL MAX не заменялись; отдельно собирается его WebView2 backend.

Отрицательный тест с файлом вместо user-data каталога показал пользователю
собственный диалог Edge. Исторический отчёт явно помечен
`userObservedDialog: true`; `SetErrorMode` не подавляет такой диалог. Теперь
проверка существования, типа и доступности каталога выполняется до вызова
CreateEnvironment. Ошибки COM, timeout и поздние callbacks проверяются
синтетическим загрузчиком без запуска браузера. Проверка каталога не обещает
отсутствие внутренних диалогов Edge при последующем сбое диска, ACL или runtime.
Рабочая установка и пользовательские профили не изменялись.

## Прямой вызов статуса запуска из C++

`valmax_startup_status` добавляет отдельный host-only C ABI над `startup_status.rs`.
Запрос и ответ имеют фиксированный размер 64 байта; строковые буферы обратно
не передаются. Чтение требует точный operation ID. Запись проверяет роль,
этап, состояние и конечный код до файловой операции. Этой функции нет в QML
и в диспетчере runtime; каталог задаёт только нативный запускатор.

`startup_client.cpp` проверяет UTF-16/UTF-8, размеры ABI, диапазоны полей и ID
ответа. Отказ записи или чтения остаётся диагностической ошибкой: он не может
заменить исходную причину отказа запуска или разрешить запуск клиента.
Старые названия этапов и роль `observe` сохранены ради совместимости файла;
это не создаёт наблюдатель или сервер.

Проверен реальный вызов C++ → Rust DLL и чтение/запись одного синтетического
файла старым Python-кодом и новым backend в обе стороны. Проверки сохраняют
первую ошибку и отклоняют запоздавшего писателя. Конкурентное удержание mutex
между процессами Python и Rust отдельно пока не проверялось. Точные SHA,
импортируемый символ и область проверки: `startup-client-proof.json`.

Полная регрессия после этого изменения: **142 теста прошли, без пропусков**.
`core-startup-regression-proof.json` относится к DLL `9329d8b0…`.
Пользовательский запускатор, его окно ошибок и готовый установщик этим этапом
ещё не заменены. Прежние proof-файлы относятся к указанным в них версиям DLL.

## Нативная основа поиска фото и видео

`native/media` переносит `@pic`/`@vid` в C++ без локального HTTP-сервера.
На этом этапе готовы библиотеки, проверка публичного запроса Wikimedia и
синтетическая сквозная проверка. Рабочий composer и установщик ещё используют
прежний маршрут. Готовность библиотек не означает готовность всего переноса.

`protocol` разбирает запрос поиска, ответ провайдера, лицензии и подписи,
проверяет адреса и форматы файлов. Проверки выполняются до нормализации URL:
неполный UTF-8, скрытые сегменты пути и Unicode-подмена разрешённого имени
хоста отклоняются. `Retry-After` сохраняет полный срок ожидания провайдера,
включая срок больше суток. Таблица HTML-сущностей взята из CPython; рядом
сохранены происхождение и лицензия PSF. Это данные для C++-библиотеки,
интерпретатор Python в неё не входит.

`scheduler` разделяет два места для запросов между поиском, миниатюрами
и выбранными оригиналами. В очереди максимум 64 задания; поиск и выбор
оригинала приоритетнее миниатюр. После отмены активное место освобождается
только когда загрузчик подтвердил остановку. Смена поколения страницы
отменяет даже результат, который уже получен, но ещё не передан потребителю.

`result_cache` хранит не больше 48 результатов на 20 минут с общим учётом
размера строк. Пакет до шести результатов публикуется целиком. В интерфейс
попадают публичные подписи и непрозрачные идентификаторы; адреса оригиналов
и миниатюр остаются внутри сервиса. Область привязана к аккаунту и странице.
Закрытие области запрещает запоздалую вставку. Копию, уже полученную рабочим
потоком, отозвать невозможно, поэтому перед каждой последующей публикацией
сервис обязан повторно проверить актуальность области.

`thumbnail` обрабатывает только байты в памяти и один кадр изображения.
До декодирования проверяются размер файла до 2 МиБ, стороны до 4096 пикселей
и площадь до 4 миллионов пикселей. Результат ограничен 512×512, сохраняет
прозрачность, учитывает ориентацию и переводит известный цветовой профиль
в sRGB. Исходные текстовые метаданные и произвольный профиль не сохраняются.
Крайние пропорции не превращают короткую сторону в ноль пикселей.
Глобальные настройки декодеров MAX не меняются. Эти ограничения не являются
песочницей кодека или гарантией размера всех его внутренних выделений памяти.

Проверки используют синтетические данные и реальные Qt 6.11.2 DLL из комплекта
MAX. Разбор протокола сопоставлен с прежним Python-кодом; очередь проверена
при отмене, смене страницы и удалении владельца из callback. Кэш проверен при
конкурентном закрытии областей, а декодер — на JPEG, PNG, GIF, WebP и двух
рабочих потоках. Точные исходники, SHA и результаты находятся в
`media-protocol-proof.json`, `media-scheduler-proof.json`,
`media-result-cache-proof.json` и `media-thumbnail-proof.json`.

`transport` выполняет настоящие QtNetwork GET в отдельном service QThread.
Для API, миниатюр и оригиналов действует общая очередь и ограничение времени
загрузки. Перенаправления обрабатываются явно с повторной проверкой адреса.
Cookies, сохранение HTTP-кэша и повторное использование авторизации отключены.
Учитывается выбранный системный proxy; ошибка маршрута не включает обходной
прямой запрос. Синхронный поиск proxy/PAC в Windows может задержать service
thread; таймер Qt не может прервать этот системный вызов. Ограничения заголовков
проверяются после HTTP-парсера Qt и не ограничивают все его внутренние выделения.

`controller` связывает страницу, приватные результаты и загрузки. Новый поиск
меняет поколение страницы. Каждый успешный асинхронный результат содержит
`ControllerStamp`; адаптер должен проверить его ещё раз после декодирования
или файловой операции. Проверки только существования страницы недостаточно.
Собственный gate контроллера охватывает сетевую загрузку выбранного оригинала;
адаптер должен удерживать запрет повторного выбора до конечного результата
передачи в GUI, включая staging и ожидание этой передачи.

`decode_queue` запускает максимум два декодера и держит до восьми ожидающих
заданий. Готовый результат занимает активное место до доставки на owner thread,
поэтому занятый интерфейс не создаёт неограниченную очередь изображений.
Очередь владеет компактными копиями входных байтов и MIME. Отмена не прерывает
кодек внутри сторонней библиотеки. При уничтожении очередь дожидается рабочих
потоков и удаляет ожидающие вызовы до освобождения своего состояния; обычное
закрытие host должно сначала дождаться асинхронного завершения работы.

`preview_provider` отдаёт QML только готовые пиксели из памяти. На один engine
допускаются 16 изображений и 8 МиБ пикселей; идентификаторы не переиспользуются.
Публикация требует актуальное поколение хранилища. В QML нужен `cache: false`
и явная очистка `source` при смене страницы: уже переданную Qt текстуру отозвать
из нативного кэша невозможно. Провайдер не открывает сеть или файлы.

`media-flow-proof.json` проверяет общую цепочку на реальных DLL MAX:
отдельный service QThread → подставленный HTTP-ответ → настоящее декодирование
PNG → `QQmlEngine Image.Ready`. Проверены смена поиска во время декодирования,
получение оригинала нативным callback и закрытие очередей. Это не проверка
настоящего MAX composer, GPU-отрисовки окна или пользовательского аккаунта.
Остальные отчёты: `media-transport-proof.json`, `media-controller-proof.json`,
`media-decode-queue-proof.json` и `media-preview-provider-proof.json`.

Отдельный `live_probe.py --live-public-wikimedia` выполняет явную проверку
публичного поиска, одной миниатюры и одного оригинала. Он не входит в обычные
тесты, не создаёт локальный сервер, не использует MAX-профили и не сохраняет
медиа. Результат в `media-live-probe.json` описывает фактически пройденные шаги,
включая ошибку провайдера или обработчика; выполненный запрос сам по себе
не считается успешной проверкой. В журнал попадают конечные коды и размеры,
без содержимого ответов, значений cookies, адресов медиа и поискового текста.

Первый настоящий запрос обнаружил несовместимость с Qt: `rawHeaderPairs()`
объединяет несколько `Set-Cookie` через LF. Общая проверка считала это ошибкой
всего ответа. Теперь транспорт проверяет отдельные записи `QHttpHeaders`;
лимиты и запрет повторных критичных заголовков сохранены. Cookies по-прежнему
не принимаются в хранилище и не отправляются следующим запросом. После
исправления live probe получил пять результатов, декодировал миниатюру
6434 байта и загрузил оригинал 7779 байт; все три ответа имели HTTP 200.
Поля `invalidHeader`/`invalidHeaders` в диагностике этого probe относятся к
прежнему объединённому представлению `rawHeaderPairs()`, а не к результату
новой проверки транспорта. Значения заголовков не сохранены.

Rust-модуль `media_staging` уже сохраняет выбранный оригинал в отдельный
каталог с лимитами размера, отменой и очисткой. Первоначальный прогон включал
168 тестов, из них 26 относились к staging; дополнительно сверены 690 случаев
проверки сигнатур с прежней реализацией. Контракт и границы проверки описаны
в [native-media-staging.md](native-media-staging.md), результат —
`media-staging-proof.json`.

После добавления C ABI полный прогон Rust содержит 178 успешных тестов.
Обёртка C++ проверена отдельно: 226 проверок с подставленным ABI и 438 с
настоящей DLL. Зафиксированная DLL `6d51da3b…` содержит 18 экспортов;
`core-media-staging-regression-proof.json` связывает исходники, результаты
прогонов и неизменяемую пару DLL/import library для сборки Qt-плагина.
Непрозрачные handles не переиспользуются, отмена из другого потока не выполняет
файловую очистку, а файловые операции остаются задачей I/O worker.

У штатного `uploadPhotoOrVideo(QList<QUrl>)` нет подтверждённого ответа о
принятии конкретного файла: метод возвращает `void`. Поэтому файл переводится
в ограниченный TTL-кэш **до** передачи в GUI. После повторной проверки страницы
адаптер вызывает метод и сообщает только факт диспетчеризации. Это предотвращает
удаление уже переданного файла при конкурентной отмене. Проверка сигнатуры и
вызова с пустым presenter не заменяет проверку рабочего черновика; детали —
[native-media-draft-contract.md](native-media-draft-contract.md).

`MediaEngineContext` уже подключён через `plugin.initializeEngine`. Его время
жизни совпадает с QQmlEngine, а не с singleton настроек: `clearSingletons()`
не сбрасывает провайдер миниатюр. Смена страницы отзывает прежние разрешения
на публикацию. Общая сборка с новой Rust DLL прошла 312 проверок, включая
44 проверки жизненного цикла контекста (`qt-all-features-media-engine-proof.json`).
Самостоятельный тест контекста содержит 401 проверку.

`MediaServiceHost` связывает эти библиотеки: один поток обслуживает сеть и
контроллер, отдельный поток сохраняет файлы, два потока декодируют миниатюры.
Передача готовых изображений в GUI ограничена двумя результатами одновременно.
Выбор оригинала занимает общее место до конечного результата в GUI, включая
файловую запись. Закрытие страницы немедленно отзывает её поколение; остановка
сервиса дожидается рабочих задач асинхронно. Проверка использует подставленные
HTTP-ответы, настоящие Qt-кодеки и зафиксированную Rust DLL; установленный MAX
и пользовательские профили не участвуют (`media-service-proof.json`).

`draft_dispatcher` вызывает только точную публичную сигнатуру добавления
вложения, проверяет лимит десяти файлов и повторно проверяет страницу после
чтения свойств штатной модели. `composer_binding` получает account/chat ID через
нативные типы и штатные форматтеры QmlUtils, без преобразования через JS Number.
Проверки этих адаптеров используют модели-заглушки и фактические Qt DLL.
Сквозная проверка `MediaComposerFrontend` прошла 1005 проверок: подставленные
HTTP-ответы → шесть настоящих декодированных миниатюр → `Image.Ready` → запись
выбранного файла через Rust → вызов тестового метода добавления в черновик.
Проверены отмена, ошибки, смена аккаунта и повторные вызовы Qt во время чтения
модели; старый результат не сбрасывает новый поиск. Отчёт
`media-composer-proof.json` фиксирует также фактически загруженные DLL.
Диспетчер повторно сверяет реальный чат после `attachmentsCount`, даже если
поколение ещё не изменилось (153 отдельные проверки). Это подтверждает передачу
тестовому presenter, но не приём вложения рабочим MAX.
`MediaComposer` создаётся из QML, но получает account/chat и штатные объекты
добавления файла через нативную модель. В QML доступны только поиск, выбор
непрозрачного ID, отмена и публичные результаты. Путь сохранённого оригинала
через QML не проходит. Адаптер прошёл 125 проверок с настоящими Qt и Rust;
изменение чата без уведомляющего сигнала также отменяет старую передачу.
Нативный вариант генератора теперь заменяет локальный HTTP-транспорт поиска
этим адаптером, а миниатюры читает через `image://valmaxmedia` с `cache: false`.
Проверка генератора подтверждает сохранность байтов обычного варианта сборки.

Для сервиса добавлен отдельный владелец на время жизни приложения. Подготовка
каталога и проверка уже загруженной Rust DLL выполняются в рабочем потоке.
Каталог — фиксированный `inline-media-native-v1` внутри явно заданного профиля;
другие настройки и данные профиля не читаются. Нормальный выход откладывается
до завершения задач; прямой `exit()` и уничтожение приложения имеют завершающий
проход обработки событий Qt. Библиотека и каталоги остаются закреплены до
освобождения всех рабочих потоков. Это не проверка безопасности первоначальной
загрузки DLL и не гарантия времени выхода при блокирующем вызове ОС.

Общий плагин с медиасервисом собран в `qt-all-features-native-media` и прошёл
363 проверки (`qt-all-features-native-media-proof.json`). Из них 51 проверяет
зарегистрированный `MediaComposer` и фактический фрагмент сгенерированного QML.
Проверены импорты зависимостей DLL, отсутствие Python среди них и завершение
тестовых процессов. Подготовка сервиса отдельно прошла 89 проверок, его запуск
и выход — 88, включая закрытие до завершения подготовки и уничтожение
приложения без `exec()`. Эти проверки запуска и выхода используют свободный
от загрузок сервис; отмена во время передачи проверяется отдельно на ServiceHost.

Установленная ValMAX и опубликованный установщик остаются на прежней реализации.
Приём вложения настоящим MAX ещё не подтверждён этим набором тестов. Координатор
экспорта, проверка компактного MData, предпросмотр и маркер восстановления
перенесены в отдельные Rust-модули (область проверки ниже). Подключение этих
модулей к Qt и сама транзакция импорта пока не завершены.

Кнопка выбора не должна отправлять файл сама: отправка остаётся действием
пользователя. Рабочая установка этими проверками не меняется.

## Координатор экспорта и компактный MData

`export_job.rs` принимает страницы только выбранного чата: до 200 сообщений
и 2 МиБ на запрос. SQLite отсекает повторные ID и хранит порядок сообщений;
повтор последней страницы или блока вложения не увеличивает счётчики.
HTML собирается через последовательный курсор, без загрузки всей переписки
в память. Запись, хеширование и рендер должны выполняться на рабочем потоке
хоста. Один объект задания не используется одновременно несколькими потоками;
флаг отмены можно выставлять из другого потока.

Временная SQLite-база находится рядом с будущим архивом в выбранной папке.
Её каталог и файл удерживаются до закрытия соединения; SQLite включён в Rust
библиотеку. Journal и временные SQL-таблицы остаются в памяти, чтобы содержимое
не уходило в системный TEMP. Это не шифрование экспорта и не защита от другого
процесса с правами того же пользователя. Очистка завершает работу с базой
до публикации архива. Существующий каталог назначения не заменяется.

Незавершённые вложения запрещают публикацию. Отметка полной истории требует
наблюдения обоих краёв от привязанной нативной модели; одного утверждения
интерфейса недостаточно. Без подтверждения результат остаётся частичным.
Пока реализован перенос вложений блоками до 48 КиБ с пределом файла 64 МиБ.
Координатор подключён к C ABI; штатная загрузка больших видео, получение истории
из MAX и Qt-панель ещё требуют подключения. Этот этап не объявляет экспорт доступным в новой
сборке клиентского интерфейса.

`mdata_login.rs` проверяет выбранные ID, формат токенов и соответствие manifest
для максимум трёх аккаунтов. `mdata_preview.rs` принимает ограниченный буфер
архива до 2 МиБ и возвращает только ID, отображаемые имена и отпечаток файла.
Список аккаунтов появляется лишь после проверки всего зашифрованного потока,
завершающей записи и конца файла. Неверный пароль, повреждение и лишние байты
не дают частичного предпросмотра. Некорректные Unicode surrogate escapes
в JSON метаданных дают пустые имена; это строже старого Python-предпросмотра.
Модули не читают Windows Credential Manager и не устанавливают профили.

`mdata_recovery_state.rs` создаёт новый маркер восстановления без перезаписи
существующего. Повреждённый или занятый маркер блокирует продолжение; удаление
требует полного совпадения ожидаемой операции и выполняется через тот же
проверенный файловый handle. Сам маркер не заменяет резервное копирование,
импорт и откат — эти операции ещё предстоит перенести.

Общий прогон Rust завершён: **216 тестов прошли, ошибок и пропусков нет**,
из них 38 новых проверок индекса, координатора экспорта, MData и восстановления.
Проверки используют только синтетические данные. Результаты и версии исходников
зафиксированы в `export-mdata-native-proof.json`. Установленная ValMAX, её аккаунты,
ранее собранный Qt-плагин и опубликованный установщик этим этапом не заменяются.

## Закрытие нативного интерфейса во время работы

`NativeBridge` проверяет, жив ли объект после синхронных QML-сигналов, прежде
чем продолжать работу. Обработчик сигнала может закрыть engine или удалить
singleton, в том числе во время доставки строк и результата операции.
Уничтожение bridge останавливает таймеры, дожидается рабочих задач и удаляет
ожидающие MetaCall до освобождения членов объекта. Это закрывает также случай,
когда обработчик `destroyed` запускает вложенный цикл событий.

`qt-bridge-lifetime-proof.json` содержит 61 отдельную проверку этих путей.
Пересобранный общий plugin проверен с прежними зафиксированными core и hook DLL:
268 проверок в `qt-all-features-media-lifetime-proof.json`. Это новая сборка
библиотеки в тестовом каталоге; предыдущие отчёты сохраняют свои исходные SHA.
Работающий MAX, ранний bootstrap и установщик этим изменением не заменяются.

## Прямой вызов MData и экспорта из нативного runtime

В `Runtime` добавлены `mdata.inspect` и команды `export.*`. Предпросмотр читает
только явно выбранный локальный `.mdata`, с ограничением размера и проверкой
пути перед чтением. Qt передаёт запрос рабочему потоку C++, тот вызывает Rust
через C ABI. Локальный HTTP-сервер в этой цепочке не используется.
`MDataTransport.qml` ограничивает очередь восемью запросами и удаляет свои ссылки
на пароль после передачи. Отмена запрещает доставку позднего ответа; уже начатая
проверка архива может завершиться. Это не гарантия зануления строк JavaScript.

Один runtime ведёт одно активное задание экспорта. Команды требуют точного ID
задания и случайного токена; подтверждение полноты истории нельзя передать через
JSON. Завершение интерфейса сначала закрывает приём новых заданий и выставляет
флаг отмены, затем дожидается рабочих потоков. Это учитывает также закрытие во
время открытия runtime и создание экспорта одновременно с закрытием. Очистка
файлов выполняется на рабочем потоке; сам сигнал отмены её завершения не обещает.

Проверки текущего этапа:

- **237 Rust-тестов**, 20 наборов, без ошибок и пропусков; в том числе C ABI,
  выбранные файлы MData, задания экспорта и отмена.
- **405 проверок общего Qt-плагина** с настоящей Rust DLL: предпросмотр архива,
  неверный пароль, повреждение, уничтожение bridge во время работы, три цикла
  создания engine и ранее подключённые модули. Источник архива — синтетический
  Python-кодек, дочерний тестовый процесс работает на Qt/C++/Rust.
- **90 проверок QML-транспорта** с подставным bridge; генератор отдельно подтвердил
  неизменность прежней сборки и сохранение скомпилированных ресурсов MAX.

Доказательства: `runtime-transfer-native-proof.json`, `qt-runtime-transfer-proof.json`
и `mdata-transport-proof.json` в `analysis/native-migration-v1`. Новая пара DLL и
импортной библиотеки закреплена отдельно от предыдущих сборок. Пользовательская
установка и аккаунты не менялись. Полный импорт/восстановление MData и получение
истории/вложений для экспорта из настоящего MAX ещё не подключены; этот этап
не является готовым нативным установщиком.

## Создание компактной MData в Rust

`mdata_export.rs` подключён к `Runtime` через `mdata.export` и
`mdata.export-selected`. Экспорт выполняется на рабочем потоке, без HTTP,
закрытия MAX или чтения истории. Каталог профиля задаёт нативный владелец;
назначение выбирает пользователь. Путь должен вести к новому локальному
`.mdata` вне профиля. Существующий файл не заменяется.

`mdata_login_capture.rs` читает только небольшие `auth_<ID>` и точные токены
соответствующих аккаунтов. Файлы кэша и переписки не открываются. Имена файлов
в корне перечисляются по удерживаемому каталогу, без рекурсии; перечисление
ограничено 10 000 записями, кандидаты входа — 63 файлами по 256 КиБ,
активные аккаунты — тремя. Нет общего перебора секретов Windows.
Windows-адаптер `login_credentials.rs` отделён от тестового хранилища;
прочитанные секретные буферы стираются при освобождении по возможности.

До чтения токенов проверяются пароль, выбор аккаунтов, путь, существующий
файл назначения, маркер восстановления и запас места. Экспорт сравнивает
снимки до и после шифрования, затем повторно проверяет токены. Изменение
источника допускает максимум три попытки. Это ограниченная проверка живого
профиля, а не атомарный снимок вместе с работающим MAX и Windows Credential
Manager. Общий mutex согласует только участвующие операции переноса.

В памяти формируется ограниченный зашифрованный архив. Временный файл содержит
только шифротекст; публикация идёт атомарным переименованием без замены.
Сигнал закрытия runtime прекращает приём новых экспортов и ставит отмену
активному. Отмена проверяется между этапами: она не прерывает scrypt или
системный файловый вызов и может проиграть уже начавшейся публикации.
Ошибка после переименования также не доказывает отсутствие готового файла.

У интерфейса отдельные признаки доступности создания и импорта. Создание
передаётся непосредственно нативному модулю; импорт не включается преждевременно.
После потери подтверждения интерфейс не должен показывать старый успех:
пользователю предлагается проверить выбранный файл перед повтором.

Полный Rust-прогон этого этапа: 264 теста в 21 наборе, без ошибок и пропусков.
Положительный экспорт проверяется с настоящими временными файлами и подставными
токенами, без обращения к реальному Credential Manager. Проверка Qt отдельно
подтверждает маршрутизацию и отказы до чтения токенов; это не проверка переноса
реального аккаунта между компьютерами.

Итоговая связка прошла **420 проверок Qt/Rust**; **201 проверка QML-транспорта**
выполнена с подставным bridge. Генератор подтвердил сохранение прежних ресурсов
и раздельное включение кнопок создания/импорта. Проверены также потеря ответа,
отмена ожидания, закрытие после отключения runtime и запоздалый ответ предыдущей
операции: они не возвращают старый успех и не оставляют бесконечный статус работы.
Отчёт этапа — `analysis/native-migration-v1/mdata-export-native-proof.json`.
Rust DLL закреплена SHA-256
`34632b2abd540f8f5b87ab77d3b3bade1b1a8f3ea27eeeca759a25fdac80406e`.

На момент проверки экспорта компактный импорт и полное восстановление ещё
не были перенесены. Их различия,
обработка оставшихся файлов аккаунта и порядок отката описаны в
`native-mdata-import-contract.md`. Для импорта требуется одноразовый нативный
процесс, переживающий штатное закрытие MAX; постоянный локальный сервер для
него не нужен. Установленная 8.8.4 и данные пользователя не заменялись.

## Компактный импорт: нативная транзакция и интерфейс помощника

`mdata_import_archive.rs` полностью проверяет выбранный архив v2, пароль,
конечный блок и окончание файла до передачи данных импорту. SHA-256 должен
совпадать с подтверждённым предпросмотром; хэш и расшифровка используют один
ограниченный снимок файла. Расшифрованные данные не записываются во временный
каталог. Полный старый архив профиля этим путём не импортируется.

`mdata_login_import.rs` и `mdata_login_storage.rs` добавляют только недостающие
аккаунты. Непустой собственный токен определяет активный аккаунт; оставшиеся
`user<ID>` и безопасный `auth_<ID>` без токена сохраняются. Существующие токены
не заменяются. Все новые аккаунты проходят проверку до первой записи;
небольшой auth-файл создаётся раньше токена и удерживается до конца операции.

Журнал учитывает и случай, когда системный вызов записал токен, но вернул
ошибку. Откат сначала восстанавливает отсутствовавшие или пустые токены,
затем удаляет только созданные этой операцией auth-файлы. Если откат токена
не удался, файлы сохраняются: действующий частично добавленный аккаунт не
должен остаться без своих данных. Повторный явно запущенный импорт может
добавить оставшиеся аккаунты. Это не атомарная транзакция между файловой
системой и Windows Credential Manager.

`mdata_import.rs` удерживает общий mutex переноса, ждёт закрытия клиента,
проверяет маркер восстановления, архив и запас места, подготавливает config
и заново проверяет процессы перед применением. До начала транзакции работает
отмена; после начала запись или откат доводятся до результата. Подготовленный
корректный config может остаться после последующей ошибки. Автоматического
перезапуска при ошибке нет.

`transfer_ffi.rs` предоставляет C++-помощнику отдельные функции предпросмотра
и импорта. Пароль передаётся только в памяти; аргументы процесса, переменные
окружения и файл задания его не содержат. Это внутренний интерфейс владельца
операции: QML/Runtime не получают произвольный импорт или поле `closed: true`.
Нативный scanner `native/transfer/closed_clients.cpp` перечисляет оба процесса
MAX во всех каталогах и признаёт обход полным только после штатного окончания.
Неизвестный путь или неполный результат не разрешают запись. Исключение для
официального клиента требует совпадения точной пары EXE/config с закреплёнными
хэшами. Новая неизвестная официальная версия также должна быть закрыта.

Проверки используют собственные зашифрованные архивы, настоящие файловые
операции Windows и подставные токены. Проверены сохранение старых данных,
добавление двух аккаунтов, повтор, сбой второго токена и неудачный откат.
Настоящий C++ клиент загрузил собранную Rust DLL и проверил границу ABI и
отказы до WinCred. Отдельная проверка процессов была только чтением, без
управления приложениями. Итоговые числа и хэши фиксируются в
`analysis/native-migration-v1/mdata-import-native-proof.json` и
`closed-clients-native-proof.json`.

Итог этого этапа: **305 Rust-тестов в 21 наборе**, **24 проверки C++ → Rust DLL**
и **53 проверки scanner**. Ошибок и пропусков в Rust-прогоне нет. Сборка DLL:
SHA-256 `dc64035b71fcbfb863ce7a7a23cacce65c61e4198692da449ea3a672d064d5ba`.
Предыдущая связка Qt/экспорта сохранена отдельно; QML-интерфейс импорта пока
остаётся выключенным. Новый core ещё не выдан за полностью собранный клиент.

`native/transfer/host_callbacks.*` соединяет scanner с C ABI помощника.
Объект создаётся на рабочем потоке; из другого потока разрешён только сигнал
отмены. Ожидание закрытия выполняется один раз, свежая проверка перед записью
не повторяет весь интервал ожидания. Ошибка проверки отменяет предыдущий
успех. Проверки этого адаптера используют подставные часы и результаты scan;
они не запускают MAX и не обращаются к учётным данным.
Все **61 проверка адаптера** прошли; отчёт —
`analysis/native-migration-v1/host-callbacks-native-proof.json`.

На момент проверки этой транзакции ещё оставались окно одноразового
Qt-помощника и его включение в установщик, полное восстановление старых архивов,
интеграция всего нативного клиента и проверка установки. Рабочая 8.8.4 и реальные
аккаунты на этом этапе не менялись. Успешный вход на сервер с перенесённой
сессией пока не проверен.

## Отдельный нативный помощник MData

`native/transfer/app` собирается в обычный `valmax-transfer.exe` на C++/Qt.
Окно выбирает локальный архив, принимает пароль, показывает проверенный список
аккаунтов и требует отдельного подтверждения импорта. Криптография и запись
выполняются через C ABI Rust на одном рабочем потоке. Постоянного HTTP-сервера
и Python в этом помощнике нет. Python используется только инструментами сборки
и тестов; это ещё не означает, что весь установленный клиент перенесён.

Каталог LocalAppData получает C++ через Windows Known Folders. QML не выбирает
каталог профиля, подставной backend или состояние закрытых процессов. Тестовая
инъекция доступна только в отдельном тестовом EXE. Пароль не передаётся через
аргументы, окружение или файл задания; очищение собственных буферов выполняется
по возможности, без обещания стереть все внутренние копии Qt/Windows.

Изменение файла или пароля отменяет прежний предпросмотр. Начавшийся импорт
замораживает выбранные данные, чтобы позднее событие интерфейса не скрывало
фактический результат записи. Закрытие окна во время операции просит отмену
и ждёт завершения worker. Поздняя отмена не подменяет успешную запись.
`rollback_failed` оставляет ошибку на экране и блокирует повтор в текущем окне;
это не новый постоянный маркер восстановления. Автозапуска ValMAX после
импорта нет — пользователю предлагается открыть клиент после успеха.

Проверены **172 условия**: настоящий Qt/C++ → Rust предпросмотр искусственного
архива с двумя аккаунтами, неверный пароль и повреждение файла, очереди
завершений, отмена, закрытие и разрушение контроллера, сохранение ошибки отката,
прокрутка окна 390×450 до кнопки импорта и тёмная тема. Сценарии записи окна
используют подставной backend; успешный импорт с настоящим WinCred не запускался.
Синтетические события колеса имеют монотонные временные метки: начальный
вариант теста с одинаковой меткой останавливал продвижение после первого
события и был исправлен без изменения штатного ScrollView.

Отдельно запущен конечный EXE из собственного пакета, с чужим WorkingDir и без
SDK/Python в PATH. Проверены создание его окна, загрузка Qt/core/qwindows из
пакета и штатное закрытие. Все тестовые процессы завершились; эта проверка
не выбирала архив и не запрашивала импорт. Это проверка на машине сборки,
а не проверка чистой Windows или реального переноса между компьютерами.

Первый пакет содержал 1518 файлов без manifest, 79 940 253 байта. Проверены 748 обычных
и отложенных зависимостей PE; Qt и VC runtime лежат рядом с помощником,
`qt.conf` использует относительные пути. Windows DLL/API sets остаются границей
операционной системы. Исходники инструментов: `build_transfer_helper.py`,
`package_transfer_helper.py`, `run_transfer_helper.py` и
`run_transfer_helper_startup.py`. Итоговые хэши и связанные отчёты —
`analysis/native-migration-v1/native-transfer-helper-proof.json`.

Этот первый промежуточный пакет ещё не содержал полного комплекта Qt
notices/материалов исходников. Следующий этап описан ниже. Установленная 8.8.4,
настоящие аккаунты и GitHub Release не менялись.

## Материалы зависимостей и подготовка установщика MData

Новый пакет `45d8161c3563da037ce8dd7b4b0ad9115d559ee88871589ef2be1d3c9a882650`
сохраняет прежние EXE, Rust core и все runtime DLL/QML побайтно. В нём 1729 файлов
без manifest, 181 100 390 байт. Добавлены 211 файлов материалов: полные официальные
архивы исходников QtBase/QtDeclarative 6.11.2, лицензии и атрибуции, исходный
документ условий Visual C++ и список распространяемых файлов Microsoft.
Пользователю не нужны Python, Rust, C++ compiler или Qt SDK для запуска помощника.

`collect_qt_notices.py` проверяет официальные SHA256 архивов, соответствие
исходников commit/tree из SDK и фактические runtime байты. Все 64 SHA1 из Qt SPDX
отличаются от фактических DLL; ожидаемые и фактические значения сохранены,
причина не выдумывается. Идентичность DLL определяется отдельными SHA256 SDK
и пакета. Это не воспроизводимая побитовая сборка Qt. `collect_vc_notices.py`
сверяет пять поставляемых VC DLL с исходным официальным VSIX. Сбор материалов
не является юридическим заключением о полном релизе.

`enrich_transfer_package.py` создаёт новый каталог, не меняя старый пакет.
Связанные receipts фиксируют исходный manifest, материалы и конечный manifest.
Новый пакет повторно прошёл 172 проверки и обычный запуск без принудительного
программного рендеринга. Все пять VC DLL загружены из пакета. Создание и закрытие
окна подтверждены; первый GPU-кадр и работа на чистой Windows этой проверкой
не подтверждаются. Итог: `analysis/native-migration-v1/native-transfer-helper-materials-copy-proof.json`.

Проверка установщика обнаружила, что прежний тестовый harness оставлял hardlink
из fixture в пакет. Содержимое совпадало, но пакет получал второй путь записи.
Ссылки собственного fixture заменены независимыми копиями с проверкой всех
1730 хэшей; исходные байты не менялись. Harness теперь сразу копирует файлы,
и 172 проверки повторены на независимом fixture.

`package_mod.py --native-transfer-proof <summary>` включает помощник явно.
Проверяются связанные build/package/runtime/startup/distribution receipts,
полный состав файлов, материалы и их хэши до запуска PyInstaller. Помощник
попадает в `runtime/transfer` до общего inventory; строгий список 1696 файлов
MAX не ослабляется. Ярлык MData при этом открывает `valmax-transfer.exe` без
аргументов. Основной запуск и восстановление старых архивов пока используют
прежние entry points.

Inno проверяет закрытие `valmax-transfer.exe` вместе с процессами ValMAX.
Первое включение помощника допускается только при отсутствии целевого
`runtime/transfer` и reparse-предков. Существующий helper не удаляется и не
смешивается с новой версией: его обновление по управляемому inventory пока
остаётся отдельной задачей. Без opt-in прежняя схема упаковки сохраняется.

Проверки упаковки: 21 тест, compile-only Inno для обычного и opt-in вариантов,
отказ при некорректном флаге или отсутствующем helper. На настоящем новом пакете
выполнены проверка цепочки receipts, независимое копирование всех 1730 файлов
(с manifest: 181 695 952 байта) и проверка общего inventory. Отчёт —
`analysis/native-migration-v1/native-transfer-installer-integration-v2-proof.json`.
Последняя проверка также запрещает вывод сборки внутрь исходного пакета helper
до создания каталога и запуска PyInstaller. Прежние compile/copy свидетельства
переиспользованы со ссылкой на их неизменённый отчёт; соответствующий код не менялся.
Тестовые установщики с искусственным payload не запускались; установка в
пользовательский каталог этим этапом не проверена.

Остаются фактическая установка/обновление и совместимые команды нативного
launcher, полное восстановление архивов и интеграция остального нативного
клиента. `distributionReady` здесь относится только к материалам и проверкам
отдельного помощника, а не к готовности всего ValMAX без Python.

### Полный архив: политика и приватная распаковка

`mdata_profile.rs` переносит правила полного профиля из Python: допустимые
пути, точные имена собственных ключей, владельца web-записей и соответствие
файлов истории ключам хранения. Пустая резервная копия допустима; ограничение
компактного импорта в три аккаунта к восстановлению не применяется. Правила
сверены с 505 синтетическими случаями, полученными из выбранных чистых
Python-функций без импорта адаптеров файловой системы или WinCred.

`mdata_profile_stage.rs` распаковывает только полный поток profile-v1 в новый
соседний временный каталог под явно переданным существующим родителем.
Родитель с компонентами MXMOD/ONEME запрещён; существующий профиль модуль не
открывает. Проверяются объём, число файлов, запас места, завершение
аутентифицированного потока, отсутствие хвоста и связь файлов БД с ключами.
Запись выполняется через удерживаемые дескрипторы; проверка размера и flush
завершаются до выдачи результата. Полученные из архива ключи остаются в памяти
с очисткой при освобождении; модуль не обращается к Windows Credentials.

Каталоги ищутся бинарным поиском среди детей с Windows ordinal comparison.
Пути `A/x` и `a/y` используют один каталог, а `ß` и `ss` не склеиваются этим
сравнением. Полные повторяющиеся пути дополнительно проверяются согласно
Python casefold формата архива. Тест на 256 каталогах проверяет повторное
использование разных регистров имён и число сравнений без привязки к таймеру.

Очистка использует только собственные удерживаемые дескрипторы, без
рекурсивного удаления по пути. Writer может пережить оболочку stage: владение
сохраняется до его закрытия. Ошибка очистки остаётся ошибкой при повторном
`discard()`. На этой Windows создание hardlink оказалось возможным даже при
share=0; проверка числа ссылок отклоняет такой файл. Неуверенные или посторонние
объекты не удаляются. Тесты используют только собственные каталоги внутри
`C:/ValMAXBuild/fixtures/mdata-profile-stage`; после прогона fixture-каталог пуст.

Отчёт `analysis/native-migration-v1/mdata-profile-stage-proof.json` содержит
44 успешных теста: 21 проверка политики/распаковки и 23 регрессионные проверки
контейнера и маркера восстановления. Сохранены команды, логи, хэши источников
и запущенного тестового файла. Остались прежние предупреждения компилятора
об именах типов Win32 extern declarations; ABI этим этапом не менялся.

Это ещё не полное восстановление. `StagedProfile` не умеет публиковать дерево,
менять ключи, очищать маркер или перезапускать клиент. `seal()` проверяет
известные файлы, но не отсутствие добавленных извне объектов: перед будущей
публикацией требуется проверка точного состава. Также остаются защищённое
открытие выбранного backup, поддержка старого `.maxsession`, запись актуального
config, адаптер полного набора ключей и транзакция замены корня с откатом.
Пины потомков нужно закрыть перед переименованием всего корня; удержание
родителя и передача владения должны быть отдельной проверенной операцией.
Установленная 8.8.4, готовый пакет помощника и его неизменяемая DLL этим
этапом не менялись; новый код собирался в отдельный target на C.

### Подготовка Recovery: источник, точный состав и config

Следующий этап закрывает три ограничения предыдущего staging. Новый
`mdata_recovery_archive.rs` открывает только файл, указанный в точном текущем
recovery marker. Родительские каталоги и файл удерживаются открытыми; запись,
переименование, reparse, hardlink, неподходящий тип и изменённые метаданные
не принимаются. Чтение потоковое, максимум 1 MiB за одну операцию, без выделения
буфера по размеру файла. Верхняя граница покрывает даже короткие data frames,
допускаемые полным кодеком; это не лимит компактного MData. Проверка большой
границы использует sparse fixture с AllocationSize не больше 4096 байт,
а не физическое создание многогигабайтного архива.

Перед завершением чтения и после него повторно проверяется маркер. Успех
`verify_complete()` означает полный EOF неизменённого входа; аутентификацию
контейнера отдельно выполняет `mdata_archive`. Отсутствующий после прерванного
восстановления MXMOD допустим: подготовка не создаёт и не открывает его.

`win_inventory.rs` перечисляет прямых детей каждого временного каталога через
его удерживаемый дескриптор. Проверяются структура Win32-ответа, UTF-16, типы,
reparse и дубликаты Windows ordinal; фактические имена и 128-битные ID
сопоставляются с журналом собственных объектов. Неизвестный объект блокирует
готовность и не удаляется очисткой. Это проверка на конкретный момент, не
атомарная блокировка добавления файлов другим процессом; будущая публикация
должна повторить её непосредственно перед применением.

`StagedProfile::prepare_recovery()` потребляет исходную распаковку и выдаёт
отдельный `PreparedRecovery`. Только корневой config заменяется актуальным
74-полевым `make_config()`: `CONFIG.JSON` распознаётся как тот же файл,
вложенный `user42/config.json` остаётся без изменений. Запись идёт через
удерживаемый файл с возвратом курсора в начало, точным усечением длины и flush;
старый хвост не остаётся. При отсутствии config он создаётся эксклюзивно.
Дополнительный служебный файл допускается и при 10 000 файлах исходного
архива. Исходный manifest остаётся описанием архива, а не изменённого дерева.
Любая ошибка после начала записи оставляет состояние неготовым; повторная
попытка не снимает эту ошибку.

`mdata_recovery_prepare.rs` связывает открытие входа, декодирование, проверку
EOF и подготовку config. `PreparedBackup` удерживает вход и временное дерево,
проверяет оба перед дальнейшим использованием и не изменяет recovery marker.
`discard()` очищает временное дерево; входной файл остаётся открытым до
освобождения `PreparedBackup`.

Проверка `analysis/native-migration-v1/mdata-recovery-prepare-proof.json`:
78 тестов прошли, пропущенных нет — 33 для политики/staging/inventory,
12 для reader и связанной подготовки, 16 для контейнера, 7 для маркера и
10 для конфигурации launcher. В них входят 505 дифференциальных случаев
Python-политики. Проверены partial-write failure, неизвестные объекты,
подмена маркера, изменение timestamp через заранее открытый metadata handle,
максимальное число файлов и отсутствие изменений исходного профиля.
После тестов три новых fixture-каталога пусты. Прежний отчёт staging относится
к предыдущей ревизии; новый отчёт содержит актуальные хэши исходников и логов.

Полная Recovery-транзакция всё ещё не подключена: остаются полный адаптер
ключей MXMOD, замена корня с передачей владения и откатом, очистка точного
маркера после commit, старый формат `.maxsession`, отмена длительных операций
и UI/coordinator с transfer mutex и свежей проверкой закрытых клиентов.
Подготовка не является разрешением применить профиль без этих проверок.
Рабочая установка, замороженная DLL помощника и готовый пакет не изменялись.

### Полные ключи Recovery и явный откат

`mdata_credentials.rs` добавляет отдельный нативный адаптер полного набора
собственных ключей MXMOD. Адаптер компактного входа не расширялся. Перечисление
WinCred читает только метаданные имён; содержимое чужих записей не копируется.
Значения запрашиваются отдельными вызовами по точному проверенному имени.
Неверный регистр имени, неизвестный вид ключа, дубликаты, превышение 256 записей
или предельного размера значения отклоняются. Временные копии значений
очищаются при освобождении. Конструирование адаптера не вызывает WinCred.

`mdata_credential_transaction.rs` делает снимок текущих ключей и формирует
план замены. Перед первой записью снимок проверяется повторно. Отсутствующая
запись отличается от существующей пустой; пустое значение можно восстановить.
Каждый снимок ограничен 256 записями, но два непересекающихся снимка дают до
512 изменений. Лимит компактного импорта в три аккаунта здесь не применяется.
Имена, отличающиеся только регистром между снимками, отклоняются до изменения.

Попытка записывается в журнал до вызова WinAPI. Гарантированный конфликт до
записи исключается из отката; неоднозначный системный сбой проверяется чтением.
Откат идёт в обратном порядке, не заменяет неожиданное значение и продолжает
обрабатывать остальные записи после ошибки. Повторный явный откат касается
только незавершённых записей: успешно восстановленный ключ больше не считается
собственной незавершённой операцией. `Drop` ничего не записывает в WinCred.
После успешного применения владелец обязан явно вызвать `commit()` либо
`rollback()`; commit ключей сам по себе не очищает recovery marker.

`PreparedBackup::credentials()` выдаёт ограниченный объект с очищаемыми
значениями из уже аутентифицированного архива. Перед выдачей повторно
проверяются источник, маркер и временное дерево. Интеграционный тест проходит
путь зашифрованный backup → подготовленный config → ключи → применение и откат
в памяти; исходный временный профиль и marker остаются прежними.

Отчёт `analysis/native-migration-v1/mdata-recovery-credentials-proof.json`:
105 тестов прошли, пропущенных нет. Из них 26 проверяют новый адаптер через
fake WinAPI и транзакцию через память, 13 — reader/подготовку/связку с ключами,
33 — политику/staging/inventory, 33 — регрессии контейнера, marker и config.
Отчёт фиксирует хэши исходников, исполняемого тестового файла и всех логов;
505 случаев дифференциальной проверки политики также прошли. Реальное
хранилище Windows Credentials и пользовательские профили не использовались.

Проверка ожидаемого значения WinCred не является атомарным CAS: будущий
координатор должен удерживать transfer mutex и проверять закрытие клиентов.
До полного восстановления остаются передача владения staging, замена корня
через удерживаемые дескрипторы с откатом, очистка точного marker после общего
commit, поддержка старого `.maxsession`, отмена и подключение UI. После начала
передачи обычный Drop staging должен только закрывать handles, сохраняя дерево;
текущая автоматическая очистка stage ещё не подходит для опубликованного корня.
Рабочая 8.8.4, пакет помощника и замороженная DLL не обновлялись.

### Публикация Recovery и общий координатор

`mdata_recovery_apply.rs` теперь выполняет полную цепочку восстановления:
transfer mutex, свежая проверка закрытых клиентов, аутентификация backup,
подготовка config и ключей, замена корня, commit и очистка точного marker.
`RootSwap` передаёт владение временным деревом без удаления на Drop. Старый
MXMOD сохраняется отдельным соседним каталогом; переименования выполняются
через удерживаемый родительский дескриптор без замены существующего объекта.
При ошибке независимо предпринимаются оба отката — ключей и корня. Неожиданный
файл или каталог сохраняется и приводит к явной ошибке, а не перезаписи.

На Windows проверена фактическая смена имён каталогов. Первоначальный вызов
`SetFileInformationByHandle` с относительным RootDirectory возвращал ошибку 87;
использован уже применяемый в проекте `NtSetInformationFile`. Тест отдельно
воспроизводит отказ второго переименования после перемещения старого MXMOD
и проверяет восстановление старого корня даже при ошибке отката ключей.

Отчёт `analysis/native-migration-v1/mdata-recovery-apply-proof.json` подтверждает
122 теста без пропусков и 505 случаев дифференциальной проверки политики.
Хэши исходников после прогона совпали; замороженная DLL помощника не изменена.
Проверка использовала собственные временные каталоги и ключи в памяти,
без пользовательских профилей и настоящего Windows Credentials.

Это не атомарная транзакция на случай отключения питания: marker и старые
деревья сохраняют возможность явного восстановления. После начала применения
отмена откладывается до окончания commit или rollback. UI/C ABI ещё подключаются;
полный нативный установщик не готов. Старый `.maxsession` этим этапом не добавлен.

Уточнённый критерий результата: установленный клиент целиком использует
C++/Rust, без Python, локального сервера или возврата к Python при ошибке.
Исторический Python-код остаётся описанием поведения и инструментом сборки.
Рабочую 8.8.4 не выдаём за нативную сборку и сохраняем данные аккаунтов.

### Настройки аккаунтов без локального сервера

В native-генерации `AccountsStackView`, `SettingsAccountSelectorDelegate`,
`ProfileInfoCell` и присутствующий в baseline `AccountButton` получают
названия, цвета и рабочие часы через `SettingsTransport` → `accounts.get`.
Ни один из этих четырёх читателей больше не обращается к localhost. Логика
расписания и строковые ID аккаунтов сохранены. Если новый читатель останется
на старом endpoint, native-генерация завершается ошибкой.

`account-readers-native-proof.json`: 5 проверок преобразований, 43 проверки
исполнения на Qt 6.11.2 и полный прогон генерации ресурсов прошли. Проверены
смена аккаунта, большие ID, кэш ревизий, расписание, ручное снятие mute,
запоздалый ответ и уничтожение страницы. Qt-проверка использовала синтетический
Bridge; работа в установленном клиенте этим отчётом не подтверждается.
