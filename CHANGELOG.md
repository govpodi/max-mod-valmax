# Изменения / Changelog

## 8.9.0 — черновик выпуска / release draft

- Подготовлена компактная поставка для Windows x64: ValMAX использует общий WebView2 Evergreen в системе вместо отдельной Fixed-копии. Установщик проверяет наличие Runtime до обновления. Из пакета убраны неиспользуемые файлы QtWebEngine, а соответствующие исходники Qt подготовлены отдельным архивом.
- Экспорт чата по-прежнему сохраняет только доступную сервером историю. Фото, видео и голосовые скачиваются при доступности; неполная история и ошибки вложений отмечаются в архиве. Это ограничение функции сохраняется независимо от статуса выпуска приложения.
- Дата, размеры, SHA-256 и результаты проверки установленного клиента будут добавлены после сборки и испытаний точного релизного пакета. Эта запись не является подтверждением выпуска.

The Windows x64 package is being prepared to use the shared WebView2 Evergreen Runtime instead of bundling a Fixed copy. Setup checks for the Runtime before upgrading. Unused QtWebEngine files are omitted, and corresponding Qt sources are prepared as a separate archive. Chat export remains limited to history and media available from the server; incomplete history and failed attachments are reported. Release date, sizes, checksums and installed-client results will be added only after the exact release package passes verification.

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
