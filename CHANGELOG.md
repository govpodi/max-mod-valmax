# Изменения / Changelog

## 8.9.0 — Windows x64

- Для компактной поставки Windows x64 подготовлен `ValMAX-8.9.0-Setup.exe`: системный WebView2 Evergreen используется вместо отдельной Fixed-копии, неиспользуемые файлы QtWebEngine исключены. Контрольные суммы Setup и отдельных архивов исходных материалов Qt/FFmpeg находятся в `SHA256SUMS.txt`.
- Кандидат preview.29 после первоначальных успешных проверок дал поздний сбой маршрутизации и перестал сохранять новые удаления; его ранние результаты не подтверждают устойчивость выпуска.
- В установленной preview.30 пройдены 14 из 14 основных сценариев; два новых тестовых удаления между собственными аккаунтами создали пузыри с корзиной прямо в чатах получателей. Оба пузыря сохранились после штатного выхода и повторного запуска. Кандидат прошёл 10 минут простоя и 30 минут после новых сообщений в одном процессе без ошибок маршрута и пропусков. Финальный установленный Setup проверяется отдельно.
- Экспорт выбранного чата остаётся **BETA**: история может быть неполной. В preview.30 тестовый экспорт сохранил 20 сообщений и все 6 запрошенных медиафайлов (4 фото, 1 видео, 1 голосовое). Ещё 2 записи получили статус `unsupported`; это не сбой загрузки запрошенных медиа. Проверены сигнатуры и размеры файлов; отдельная SHA-256-проверка этих скачанных файлов в p30 не заявляется.
- В preview.30 проверены воспроизведение собственного тестового фото, видео и голосового и открытие встроенной бизнес-вкладки. Проверка запуска мини-приложений и раннего применения защитных фильтров остаётся отдельной задачей BETA.

| Данные финального установленного 8.9.0 | Значение |
| --- | --- |
| Итог проверки основного клиента | 14/14 основных сценариев; два аккаунта, чаты и медиа, пузырь удаления после перезапуска, встроенный «Бизнес». BETA-экспорт: 21 сообщение и 6 медиа; полнота не подтверждена. Не менее 10 минут покоя и 30 минут после контрольных сообщений в одном процессе: 0 ошибок маршрута, новых ограниченных пропусков и удалений без записи нет; исходный пропуск 1 и 4 удалений без локальной текстовой записи с неустановленными причинами, поэтому удержание BETA |
| SHA-256 квитанции финальной установленной проверки | b786a413057a7f1dae1a26ebd20631abfec3aef247b1847de7605a56c6813d6a |
| Размер `ValMAX-8.9.0-Setup.exe`, байт | 241 731 977 |
| SHA-256 `ValMAX-8.9.0-Setup.exe` | `389874446a9be4393a5c60dee33e68fcceda3554809be074e450f18c2a6d3260` |

The compact Windows x64 package uses the system WebView2 Evergreen Runtime and omits unused QtWebEngine files. Candidate preview.29 developed a late retention routing failure after its initial successful checks, so those early results do not establish release health. In installed preview.30, all 14 core scenarios passed; two new deletions between user-owned accounts produced in-chat trash bubbles and both survived a graceful exit and relaunch. The candidate then passed 10 minutes idle and 30 minutes after new test traffic in the same process with zero routing failures and skips. A BETA export saved 20 messages and all six requested media files (four photos, one video, one voice); two other entries were `unsupported`, not failed downloads of the requested media. File signatures and sizes were checked, but separate SHA-256 verification of the p30 downloads is not claimed. Own test media playback and the embedded Business tab were checked. Mini-app launch and early protection remain separate BETA checks. The exact 8.9.0 Setup is installed, its runtime matched the package, and the profile passed path-and-SHA-256 verification. Final installed functional QA: 14/14 core scenarios; two accounts, chats and media, an in-chat deletion bubble after restart, and embedded Business. BETA export: 21 messages and six media files; completeness unconfirmed. At least 10 minutes idle and 30 minutes after test traffic in one process: zero routing errors, no new bounded skips or deletions without retained text; one earlier bounded skip and 4 earlier deletion IDs without retained text of unknown cause mean retention stays BETA.

## 8.9.0-native.preview.22 Beta — 2026-10-26

- Исправлена пагинация старых сохранённых удалений без ключа порядка. Курсоры разделены по аккаунту и чату.
- В установленной preview.22 проверены два аккаунта, отправка и удаление тестового сообщения, пузырь с корзинкой в самом чате и сохранность профилей после штатного перезапуска.
- Экспорт собственного тестового диалога сохранил 15 сообщений, 4 фото, 1 видео и 1 голосовое. Все шесть медиафайлов прошли проверку формата и SHA-256; два других вложения отмечены как пропущенные. Полнота истории сервером не подтверждена, поэтому функция остаётся БЕТА.

Preview.22 fixes paging of older retained deletions without an order key and keeps account/chat cursors separate. The installed client was checked with two own accounts, a fresh in-chat deleted-message bubble and a graceful restart. A test export saved 15 messages and six verified photo, video and voice files; two other attachments were recorded as omitted. Complete history is not established, so export remains BETA.

## 8.9.0-native.preview.16 Beta — 2026-10-25

- Обработка уведомлений об удалении в нативном хосте разбивает большие пачки на части. Пачка сверх заданных пределов пропускается без отключения дальнейшего сохранения; некорректные данные аккаунта и маршрута по-прежнему закрывают обработку. Проверено синтетическими нативными тестами и новым удалением между двумя собственными аккаунтами в установленной preview.16.
- Сохранённые удаления внутри чата показываются окнами по 200 записей с переходом к более ранним и поздним. Автоматические QML-проверки покрывают границу 199/200, смену аккаунта и обновление списка. Доступность старых записей зависит от того, были ли они ранее сохранены клиентом.
- Управляемый установщик распознаёт зарегистрированное имя предыдущей версии `ValMAX BETA` при обновлении, сохраняя строгую проверку пути и метки установки. Сценарии обновления и отката прошли синтетические проверки.
- Экспорт остаётся БЕТА: сервер не подтвердил полноту старой истории; отдельные вложения могут быть недоступны. В нативном пакете по-прежнему нет Qt Lottie. [Сторонние материалы](THIRD_PARTY_NOTICES.md) не подтверждают точное соответствие исходников распространяемым FFmpeg DLL.

Preview.16 adds bounded deletion-callback batches, 200-entry navigation for retained-deletion bubbles in the chat, and managed upgrade support for the previous `ValMAX BETA` registration. Synthetic native, QML and installer checks passed. An installed preview.16 captured a new deletion between the two own test accounts and displayed the retained bubble after restart. The final public installer is verified separately. Chat export remains BETA: complete old history and individual media availability are not guaranteed.

## 8.9.0-native.preview.15 Beta — 2026-10-25

- Нативный клиент Windows использует C++/Qt и Rust; установленной версии не нужны Python и локальный HTTP-помощник.
- Сохранённые после включения функции удаления отображаются пузырями с корзиной внутри чата. Для записей с подтверждённым ключом порядка используется исходное время; положение старых записей без него может быть приблизительным.
- Бета-экспорт выбранного чата показывает предупреждение о возможной неполноте истории, ход загрузки и вложения. Доступные фото, видео и голосовые скачиваются последовательно с ограничением темпа и пределом 4 ГиБ на файл; недоступные файлы отмечаются в архиве. Полнота серверной истории не подтверждена.
- Два индикатора записи переведены на Qt Quick; семь бинарных файлов Qt Lottie и его `qmldir` исключены из нативного пакета. Исходные и лицензионные материалы других компонентов привязываются к проверяемым версиям и хэшам.
- Для Beta подготовлен отдельный комплект с официальными исходными архивами FFmpeg 7.1.3 и 7.1.5 как справочным материалом. Их точное соответствие десяти распространяемым DLL, включая возможные изменения и параметры сборки, не доказано. Дополнительные ограничения описаны в [сторонних материалах](THIRD_PARTY_NOTICES.md).

Preview.15 Beta adds in-chat retained-deletion bubbles, a paced selected-chat export with available photo, video and voice attachments, and Qt Quick recording indicators in place of Qt Lottie. Complete old history and exact FFmpeg DLL source/build correspondence are not established; unavailable attachments are recorded in the export.

## 8.9.0-native.preview.14 — исходники локальной тестовой версии, 2026-10-25

- Бета-экспорт выбранного чата теперь записывает доступные фото, видео и голосовые потоком с локальным пределом 4 ГиБ на файл. Передача может продолжаться при сетевом прогрессе; простой и общая длительность ограничены. Для видео предусмотрена одна попытка другого проверенного разрешения после допустимой ошибки.
- Окно экспорта явно сообщает о пределе, показывает ход работы и поддерживает отмену. Полнота серверной истории не утверждается; архив остаётся БЕТА.
- В план сборки добавлены проверенные материалы QtImageFormats и уведомления libtiff/libwebp. Это не подтверждает материалы всего клиента или право распространять компоненты MAX.
- Критерий релиза для Бета-архива требует установленную проверку медиа, предупреждения, прогресса, задержки, отмены и ошибок. Ранняя защита, полный цикл установленного клиента и права на распространение остаются открытыми; `releaseEligible=false`.

Preview.14 source adds bounded streaming media, one lower video variant retry, explicit BETA scope and pinned QtImageFormats materials. A successful build, installation and public stable release require separate verification.

## 8.9.0-native.preview.12 — локальная проверка, 2026-10-25

- Пользовательский runtime перенесён на C++/Qt и Rust; Python и локальный HTTP-помощник не входят в установленный пакет.
- Удалённое «для всех» уже полученное сообщение может сохраняться отдельным пузырём с корзинкой внутри чата. Проверены два тестовых аккаунта, положение пузыря и сохранение после перезапуска.
- Экспорт выбранного чата через штатный api2 помечен как БЕТА: история может быть неполной, вложения скачиваются при доступности, интерфейс показывает ход работы.
- Известны ошибка подписи календарной даты нового пузыря и незавершённые проверки полной истории, ранней защиты, установленных функций и материалов для распространения. Публичный релиз не разрешён (`releaseEligible=false`).

Native preview.12 moves the installed runtime to C++/Qt and Rust, adds an in-chat retained-deletion bubble and a BETA selected-chat api2 export. It remains a local test build with incomplete release and distribution evidence. See [preview status](docs/NATIVE_PREVIEW_12.md).

## 8.8.4 Beta — 2026-10-16

- Исправлена работа `@pic`/`@vid` с ограничениями Wikimedia: корректный идентификатор клиента, общая очередь загрузок и соблюдение времени повторной попытки.
- Миниатюры кешируются между поисками, одинаковые одновременные загрузки объединяются. Ошибка загрузки сохраняет результаты и черновик; интерфейс показывает время ожидания.

## 8.8.3 Beta — 2026-10-16

- Исправлен отказ запуска с включённым режимом призрака (`ghost_ack_failed`). Проверка виртуальных методов теперь учитывает штатные переходы MAX к функциям.
- Добавлены проверки реальной таблицы методов PE и подключение полного обработчика к собственному тестовому процессу без запуска MAX и чтения профиля.

## 8.8.2 Beta — 2026-10-15

- Исправлена геометрия результатов `@pic`/`@vid`: выбор фото и прокрутка списка доступны в маленьком окне.
- Добавлен локальный учёт просмотренных сообщений для счётчика режима призрака. Поправка действует в текущем запуске; при неоднозначной синхронизации возвращается серверный счётчик. Полная повторная синхронизация ещё требует проверки.
- Исправлен фильтр ручной отметки прочтения: действие «Сделать непрочитанным» больше не принимается за отправку прочтения.
- Добавлена страница «Помощь и диагностика»: просмотр и сохранение разрешённых технических сведений в новый JSON-файл, без автоматической отправки.
- Упрощены инструкция участия и форма ошибки; обновлены русский и английский README, расшифровка ВАЛ, контакты Telegram и скриншот со скрытыми личными данными.

Fixes media-result layout and selection, adds a session-local ghost unread-counter
adjustment and corrects the manual read filter. Adds opt-in local diagnostic
export and clearer bug reporting; updates bilingual project documentation.
The counter adjustment remains beta, with conservative server-count fallback;
full resynchronization still needs validation.

## 8.8.1 Beta — 2026-10-15

Первый публичный выпуск.

- До трёх аккаунтов: названия, цвета и расписания уведомлений.
- Быстрые ответы с вложениями, отдельная вкладка бизнес-кабинета.
- Компактный перенос MData и экспорт переписки в HTML.
- Локальный PIN, режим призрака и сохранение уже полученных удалённых сообщений.
- Настройки оптимизации прокрутки, кэша, фотографий и фоновых вкладок.
- Повторный запуск через ярлык открывает существующее окно.
- Запуск ждёт завершения предыдущего помощника; ошибки показывают конкретную причину вместо общего сообщения MData.

Initial public beta: account tools, quick replies, business dashboard, MData,
HTML export, local chat PINs, ghost controls, received-message retention and
rendering settings. Fixes repeat-shortcut activation and startup diagnostics.
