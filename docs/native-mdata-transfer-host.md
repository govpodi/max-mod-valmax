# Одноразовый native host переноса MData

Read-only срез исходников. Это контракт следующего компонента, не готовый importer или разрешение запускать клиент. В рамках работы процессы MAX, профили и WinCred не открывались. Ранее остановленные early-bootstrap gate и retention сюда не входят.

## Кратчайший путь

Отдельный packaged `valmax-transfer.exe`: небольшой C++/Qt диалог выбора файла и пароля, один рабочий поток, Rust backend компактного импорта. Qt и core берутся из собственного проверяемого пакета. Процесс живёт независимо от MAX, поэтому закрытие основного клиента не уничтожает импорт. HTTP, Python и постоянно работающий наблюдатель не нужны.

Первый законченный путь — явный запуск окна «Войти с MData», пользователь закрывает ValMAX через трей, helper ждёт проверенное завершение. Пароль вводится в helper; его нет в argv, environment, файле задания или диагностике. In-app handoff можно подключить позже, не задерживая этот путь: команда запуска не содержит пароль; helper сам показывает выбранный архив/preview и получает подтверждение. Автоматическое закрытие MAX допустимо только после отдельного подтверждения готовности helper; простое успешное CreateProcess этого не доказывает.

**Первый отсутствующий production-компонент:** полный closed-client scanner. `native/launcher/activation.cpp` перечисляет окна конкретного проверенного executable и не заменяет такой scanner. Другого native полного scanner в прочитанном дереве нет; владелец launcher-модулей это подтвердил.

## Точный действующий контракт закрытых клиентов

| Исходник | Что реально делает |
| --- | --- |
| `modkit/max_client_policy.py`: `NAMES`, `OriginalImagePolicy`, `scan_clients` | Рассматривает `max.exe` **и** `max-service.exe` во всех каталогах. Неизвестный/неабсолютный путь или исключение делают scan неполным. Известный путь с непроверенными байтами считается потенциальным MXMOD-клиентом. |
| `modkit/privacy_server.py`: `_main_processes`, `process_path` | Toolhelp-перечисление с проверкой `ERROR_NO_MORE_FILES`, QueryFullProcessImageName через ограниченный process handle. `_main_processes` выбирает только main EXE и пригоден лишь для узкого решения о возврате UI. |
| `modkit/mdata_service.py`: `owned_client_activity`, `_wait_closed`, `_run` | Сейчас production full enumeration приходит от Frida. Под общим transfer mutex: непрерывно 2 секунды `(count=0, complete=true)`, дедлайн 120 секунд, повтор через 50 мс. Любой кандидат или неполнота сбрасывает интервал. На истечении — `client_still_running`. |
| `modkit/session_transfer.py`: `_import_after_exit`, `_restore` | Та же политика 120/2 секунды, опрос 100 мс; повторная проверка непосредственно перед записью. |
| `modkit/mdata_import.py`: `ImportJob._closed/_work` | Standalone путь сейчас сразу отклоняет незакрытый клиент или неполный scan (`process_scan_failed`), затем передаёт строгий callback сервису. Это отличается от ожидающего in-app пути. |
| `modkit/mdata_recovery.py`, `modkit/mdata_service.py`: `recover_backup` | Проверка запускается только после явного действия; открытие Recovery не сканирует процессы/credentials. Backend повторяет closed-check под mutex до применения. |

Исключение «это официальный MAX/ONEME» определяется **парой точных бинарников**, а не названием каталога, подписью manifest, процессной командной строкой или чтением ONEME-профиля. `ORIGINAL_IMAGES` сейчас содержит:

| Процесс | Размер EXE / SHA-256 | Размер соседнего `config.dll` / SHA-256 |
| --- | --- | --- |
| `max.exe` | 79444816 / `d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182` | 875856 / `8e7d9d53f77d0f2599ed2bb5b117b3c8a4256ee361434ebd3d977b01d4685035` |
| `max-service.exe` | 123454288 / `bd82b3fa0a6d24ee87bb9e0ec32ee311df7c40d8d233a71537685af00d50e0eb` | 875856 / `950531233749920b60fdcb05a0465eb84f8f2acf3fbb76fc4d1279cb67f714f3` |

Оба файла удерживаются одновременно с read access и share-read-only; каталог/reparse/hardlink, размер, EOF, изменение identity/write/change metadata или несовпадение хэша запрещают исключение. Кэш Python — LRU до 64 записей по удержанной identity/размеру/временам и ожидаемому digest; hit тоже требует нового безопасного открытия. Обновившийся официальный MAX с другой парой **не** считается проверенным и должен быть закрыт. Эти pins фиксируют конкретную версию, не все будущие официальные сборки.

## Минимальный native scanner

Новый `native/launcher/closed_clients.{h,cpp}` без attach, чтения памяти/командной строки, оконных действий и terminate:

```cpp
struct ClientScan { uint32_t candidates; bool complete; };
ClientScan scan_closed_clients();
// Весь вызов на рабочем потоке, с injected API/clock для owned tests.
WaitResult wait_closed(ScanApi&, Cancellation&, Deadline); // 120 s, 2 s settle
```

- Toolhelp `TH32CS_SNAPPROCESS` по всем доступным записям; обе MAX basename без учёта регистра. Не ограничивать install directory, parent PID, текущий runtime или наличие окна.
- Обход считается полным только при штатном `ERROR_NO_MORE_FILES`. Ошибка первой/следующей записи, повреждённая запись, достижение явно заданного предела перечисления — `complete=false`. Предлагаемый защитный предел 65536 записей — новое ограничение, не существующая гарантия legacy.
- Для кандидата: держать process handle с QUERY_LIMITED_INFORMATION/SYNCHRONIZE, запросить bounded absolute image path, проверить basename и локальный путь; неполученный/неоднозначный путь не превращается в ноль клиентов. Исчезновение PID во время scan разрешается новой полной попыткой, а не предположением по PID.
- Исключать только одновременно проверенную exact EXE/config пару выше. Для пары переносится узкая логика `OriginalImagePolicy`, не полный обход официального каталога. Путь/имя сами по себе никогда не означают ONEME.
- Возвращать только count/complete/конечный код; PIDs/пути и низкоуровневые ошибки не нужны UI или журналу. Во время ожидания incomplete всегда сбрасывает settle; по истечении 120 секунд не применять данные. Standalone UX может уточнить `process_scan_failed`, если последняя причина — неполный scan.
- Абсолютный monotonic deadline проверяется до/после каждой попытки и между порциями хэширования; ожидание 50 мс ограничено остатком. 120 секунд — политика ожидания, **не гарантия прерывания зависшего Windows filesystem call**. Нельзя обещать жёсткую задержку закрытия при блокирующем I/O.

Два удачных scan через 2 секунды не доказывают непрерывное отсутствие процесса между снимками. Общий `Local\MAXModSessionTransferV1` предотвращает гонку только с cooperating launcher/transfer путями. Произвольный запуск EXE напрямую, переименованный неизвестный процесс и вредоносная загрузка DLL этой политикой не покрыты. Это честный предел legacy-контракта; новый ранний gate здесь не добавляется и не обходится.

## Граница host → Rust и жизненный цикл

Предлагаемая внутренняя host-only операция `import_compact` получает явный trusted `localRoot`, выбранный `file:///` URL, пароль, подтверждённый SHA-256 fingerprint, operation ID. Root получается из host-конфигурации, не из QML-поля. Для первого executable достаточно собственного выбора файла и authenticated preview; никакой сохранённой «команды импорта» на диск.

Новый компактный C ABI ещё требуется: синхронный worker-only вызов, borrowed bounded UTF-8 request, finite result `{state,result,profiles,error,committed}`, cancellation и callback свежего closed-check. Callback вызывается на том же OS-потоке, на котором host держит transfer mutex; его context живёт до возврата. Rust не должен принимать пользовательское JSON `closed:true` как разрешение записи. Использовать привычные ABI version/size, panic boundary и ownership buffers из `native/core/valmax_core.h`; имён готовых import exports сейчас нет.

1. **Открытие диалога:** без чтения credentials/истории/авторизаций. Проверяемый package context должен жить до завершения worker. Explicit root — только `<existing local>/MXMOD`, без ONEME, UNC/ADS/reparse/hardlinks и создания произвольных предков.
2. **Явное подтверждение:** валидировать выбранный файл/пароль/fingerprint; файл внутри целевого MXMOD отклонить, как `MDataService.request` и `ImportJob._work`. Захватить `TransferGuard` на рабочем OS-потоке и удерживать через ожидание, проверку/создание config, decode, применение и rollback. Импорт второй операции не запускается.
3. **Closed wait:** полные scan в течение 2 секунд, максимум 120 секунд. Затем проверить Recovery-маркер. Любой существующий повреждённый/частичный/чужой marker блокирует обычный import. Mutex abandonment не очищает marker.
4. **Config:** `prepare_profile(explicit_local, false, outerGuard, trusted_prepare)` / `launcher_profile::prepare` может создать лишь отсутствующий собственный config с 74 pinned полями и 8 critical overrides. Existing config валидируется без переписывания. `check_only` намеренно пропускает recovery guard и **не** разрешает мутацию или запуск. Runtime-open не использовать для подготовки: оно может затронуть другие настройки.
5. **Decode/apply:** только компактный v2 в этом первом host. Полная аутентификация terminal/EOF и fingerprint тех же входных байтов; fresh closed-check непосредственно перед mutating transaction. Transaction держит parent/root/auth/user pins; максимальные 3 live tokens; existing safe auth/user остаются. Credentials только exact `access_token_<ID>@MXMOD`. Журнал/rollback — отдельный реализуемый backend, не обязанность UI.
6. **Закрытие окна/отмена:** до первой мутации можно завершить без применения. После начала транзакции UI лишь запрашивает отмену; worker доводит commit/rollback и сохраняет конечный outcome. Не уничтожать process/DLL/context под работающим worker, не делать kill-on-main-exit для helper. Ошибка статусной записи не запускает импорт повторно.
7. **Конец:** committed/already_present — одноразовый terminal. Неоднозначность/rollback failure не выдаётся за отмену без изменений; сохранить требуемое recovery состояние. Конечный статус не содержит архивный путь, пароль, IDs или tokens. Restart_failed после commit не разрешает повторное применение.

Автоперезапуск — отдельный последний шаг через уже разрешённый проверенный launcher, с повторной проверкой recovery/отсутствия конфликтов. В текущем native дереве полного production launcher ещё нет. Первый helper может честно закончить «Данные добавлены; открой ValMAX», не запускать MAX напрямую и не возвращать пользователя в Python/HTTP. Это временно неполная legacy-автоматизация, а не готовность всего переноса.

## Что переиспользовать и чего не хватает

| Есть | Применение / предел |
| --- | --- |
| `native/launcher/transfer_mutex.*`, `native/core/src/transfer_lock.rs` | Один совместимый thread-affine mutex; внешнее удержание обязательно. |
| `native/launcher/preflight.*` | Owned payload pins и lifetime handles. Текущий target — preflight, не готовый launcher и не безопасность первоначального DLL loading. Для helper нужен конкретный package inventory/load strategy; импортированная DLL может загрузиться до вызова verifier. |
| `native/launcher/profile_client.*`, `native/core/src/launcher_{config,profile}.rs` | Config guard и host-only ABI `valmax_launcher_prepare`; не closed-client detector. |
| `native/core/src/{mdata_archive,session_archive,mdata_login,mdata_preview_file,selected_path}.rs` | Форматы, строгое чтение, authenticated preview/metadata. Не применение аккаунтов. |
| `native/core/src/{login_credentials,mdata_recovery_state,settings_io}.rs` | Exact token adapter, durable marker, pinned file operations. Не полноценный legacy full-profile restore. |
| `native/launcher/startup_client.*`, `native/core/src/startup_{status,ffi}.rs` | Необязательный operation-specific receipt; не guard, не MData transaction result и не разрешение запуска. |
| `native/launcher/environment.*` | Будущая сборка child environment. Она не подтверждает closed clients, payload loading или startup readiness. |

Оставшиеся реальные границы: production scanner/waiter; one-shot executable и его загрузка проверенного core; compact transaction + host ABI (в параллельной разработке, не заявлены готовыми этим документом); dialog/preview/confirmation и final result; затем отдельная разрешённая интеграция перезапуска. Legacy `.maxsession`, full `.mdata`/Recovery требуют своих транзакций и credential/file policies — не объявлять их поддержкой compact host.

Проверенная исходная опора: `analysis/native-migration-v1/mdata-export-native-proof.json` (264 Rust, 420 Qt plugin, 201 QML stub; import/recovery false), `launcher-transfer-mutex-proof.json` (53 checks, только переименованный synthetic mutex), `launcher-native-suite-proof.json` (6 standalone suites), `launcher-config-proof.json` (1120 synthetic differential cases), `launcher-profile-proof.json`. Эти отчёты **не** доказывают отсутствующий production scanner или готовность one-shot import. Новый аудит не запускал их повторно.
