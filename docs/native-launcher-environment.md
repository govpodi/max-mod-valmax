# Окружение дочернего native-клиента

`native/launcher/environment.{h,cpp}` — отдельный C++ builder. Он получает
переданные вызывающим кодом строки и возвращает новый UTF-16 блок окружения.
Он не читает окружение процесса, профили, реестр или файловую систему,
не создаёт каталоги и не запускает приложения. Python нужен только для
сборочного проверочного скрипта, не для библиотеки.

## Контракт

`build_child_environment` принимает либо `span<const EnvironmentEntry>`, либо
ровно один Windows-блок с двумя конечными NUL. Оба overload возвращают
`ChildEnvironment`: конечный код ошибки и пустой блок при ошибке; готовый
отсортированный блок при успехе. Входные буферы не изменяются. Выход может
содержать унаследованные секретные значения, поэтому его нельзя журналировать.

`LexicalEnvironmentPaths` содержит два явных пути: fixed WebView2 runtime и
подготовленный корень `MXMOD`. Название типа намеренно не обещает проверки
файлов. Будущий host обязан проверить полный комплект runtime, подготовить
`MXMOD/business-webview2` и держать соответствующие guards в ходе запуска.
Builder проверяет только написание drive-absolute пути: без UNC/device paths,
ADS, dot segments, пустых компонентов, управляющих символов, Windows device
names, конечных точек/пробелов и компонента `ONEME`. `/` преобразуется в `\`.
Runtime и изменяемый корень MXMOD не должны совпадать или быть вложены друг
в друга. Эти правила не обнаруживают junction, 8.3 alias или подмену каталога.

В новом блоке всегда ровно по одному значению:

| Переменная | Значение |
| --- | --- |
| `WEBVIEW2_BROWSER_EXECUTABLE_FOLDER` | Явный fixed runtime |
| `WEBVIEW2_USER_DATA_FOLDER` | Явный MXMOD + `business-webview2` |
| `QT_WEBVIEW_PLUGIN` | `webview2` |
| `QML_XHR_ALLOW_FILE_READ` | `1`, сохранённый контракт локального JSON оптимизации |

Перед вставкой удаляются все варианты регистра `WEBVIEW2_*`, а также точный
список переменных выбора плагинов/импортов и их директорий:

`QT_PLUGIN_PATH`, `QT_QPA_PLATFORM_PLUGIN_PATH`, `QML_IMPORT_PATH`,
`QML2_IMPORT_PATH`, `QT_QPA_PLATFORM`, `QT_QPA_PLATFORMTHEME`,
`QT_QPA_GENERIC_PLUGINS`, `QT_IM_MODULE`, `QT_IM_MODULES`, `QT_STYLE_OVERRIDE`,
`QT_QUICK_CONTROLS_STYLE`, `QT_QUICK_CONTROLS_FALLBACK_STYLE`,
`QT_QUICK_CONTROLS_CONF`, `QML_DISK_CACHE_PATH`, `QT_QPA_FONTDIR`,
`QT_WEBVIEW_PLUGIN`, `QML_XHR_ALLOW_FILE_READ`.

Также удаляются `VALMAX_NATIVE_RUNTIME_CONFIG` и `MAX_MOD_STARTUP_OPERATION`
в любом регистре. Bridge воспринимает первый как конфигурацию доверенного
host, второй связывает receipt с конкретным запуском. Будущий host обязан
вставить собственную проверенную конфигурацию и ID текущей операции после
этого шага. Builder не создаёт JSON из пути WebView2 и не объявляет клиент
готовым к запуску: это ещё не полная сборка окружения host.

Обычные переменные сохраняются буквально, включая `PATH`, `TEMP`,
`LOCALAPPDATA`, `QT_SCALE_FACTOR`, `QSG_RHI_BACKEND`, `QT_QUICK_BACKEND` и
`QML_DISABLE_DISK_CACHE`. `LOCALAPPDATA` не используется для выбора пути.
Служебные Windows-имена вида `=C:` поддерживаются, их непустое значение
сохраняется как непрозрачная строка и не участвует в разрешении путей.
Известные служебные записи оболочки `=::`, `=ExitCode` и `=ExitCodeAscii`
проходят проверки размера и UTF-16, затем удаляются. Они не нужны помощнику,
который получает абсолютные пути. Неизвестная недопустимая запись с начальным
`=` по-прежнему останавливает запуск с `invalid_hidden_environment_entry`;
запись без разделителя — с `environment_entry_without_separator`.
Запускатор показывает только такой конечный код, без имён и значений среды.

Сортировка и поиск дубликатов используют `CompareStringOrdinal(..., TRUE)`.
Одинаковые сохраняемые имена отклоняются даже при одинаковом значении.
Дубликаты удаляемых overrides не создают неоднозначности. Сравнение следует
таблице Windows, а не Unicode casefold Python. Некорректные UTF-16 surrogate
pairs и встроенный NUL в name/value API отклоняются. В block API NUL служит
разделителем записей; ранний конец, хвост или отсутствие double NUL — ошибка.

Проектные лимиты: 1 048 576 UTF-16 units на входной/выходной блок, 4096
записей, 32767 units на `name=value`. Они не выдаются за общие пределы Windows.
Канонические вставки учитываются в лимитах выхода. Нет частичного результата
при ошибке. Ошибки выделения памяти отображаются отдельным конечным кодом.

## Сопоставление со старым запускатором

В `modkit/business_environment.py` уже была собственная папка
`MXMOD/business-webview2`, но очищалась только `WEBVIEW2_USER_DATA_FOLDER`.
Полная очистка `WEBVIEW2_*`, закрепление fixed runtime и удаление Qt plugin
overrides — новые явные требования native-запуска. `optimization_store.py`
добавлял `QML_XHR_ALLOW_FILE_READ=1`; это поведение сохранено.

`external_environment.py` очищал `PATH` от PyInstaller bundle и временно
менял DLL search directory родителя. Новый launcher не является PyInstaller
bundle. Builder не делает такую очистку и не вызывает `SetDllDirectoryW`.
Сохранённый `PATH` **не доказывает** безопасный DLL search или полноту
проверенного комплекта. Это отдельный контракт host/preflight/qt.conf.

## Проверка и оставшиеся границы

`native/tests/launcher/environment_test.cpp` проверяет synthetic inputs,
Unicode, все overrides, Windows drive entries, дубликаты, сохранение обычных
переменных, точные границы и 200 перестановок/повторных преобразований.
Также проверяются удаление служебных записей оболочки, их Unicode/размеры
и вложенное преобразование среды клиент → запускатор → помощник.
`native/tooling/test_launcher_environment.py` собирает отдельную библиотеку,
запускает только её тест и сохраняет hashes и журнал. Дополнительно проверяет
импорты object-файла: единственный Win32 import — `CompareStringOrdinal`.

Не проверены реальный запуск MAX/WebView2, DLL closure, registry policies,
права на каталог, отсутствие reparse points или удержание файлов runtime.
Очистка environment не отменяет WebView2 registry policies. Не имитируется
отказ allocator; обработчик `bad_alloc` проверен кодом. Будущий host должен
передавать блок только дочернему `CreateProcessW` с
`CREATE_UNICODE_ENVIRONMENT`, не менять parent/global environment и не
обходить preflight при ошибке builder.

Основание формата: [Microsoft environment blocks](https://learn.microsoft.com/en-us/windows/win32/procthread/changing-environment-variables)
и [CreateProcessW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw).
Выбор runtime/data: [WebView2 environment options](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/webview2-idl).
Поиск Qt плагинов: [Deploying Plugins](https://doc.qt.io/qt-6/deployment-plugins.html),
импорты QML: [Deploying QML Applications](https://doc.qt.io/qt-6/qtquick-deployment.html).
