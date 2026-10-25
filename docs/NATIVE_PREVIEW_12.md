# ValMAX 8.9.0 Native Preview 12 — local testing (Windows x64)

**Status: local preview for controlled testing. `releaseEligible=false`. This is not a completed public release.** The installer itself carries the same warning. Publication as a full release must wait for the product and distribution-materials gates below.

## Русский

ValMAX 8.9.0-native.preview.12 переносит пользовательский runtime на C++/Qt и Rust. В установленном пакете нет Python runtime и локального HTTP-помощника. Сборка предназначена для проверки на Windows x64 с базой MAX 26.35.1.80662.

### Что изменилось

- **Сохранённые удаления · БЕТА.** Уже полученное сообщение после наблюдаемого удаления «для всех» показывается в чате отдельным локальным пузырём с корзинкой. В проверке установленного клиента с двумя собственными аккаунтами пузырь стоял между соседними сообщениями и остался после перезапуска. Старые записи без ключа порядка показываются в приблизительном положении.
- **Экспорт выбранного чата · БЕТА.** Штатный api2 используется для доступной истории, фото, видео и голосовых. Интерфейс предупреждает о возможной неполноте, показывает ход загрузки и ограничивает темп запросов. Недоступные вложения должны оставаться отмеченными как пропущенные. Проверка установленной предыдущей preview получила 11 тестовых сообщений и 6 вложений; 2 вложения были недоступны. Этот результат не подтверждает полноту истории и не является проверкой экспорта из установленной preview.12.
- **Обновление.** Установщик сохраняет предыдущую программу для отката и не удаляет данные аккаунтов MXMOD и Windows credentials. Перед запуском установщика полностью выйдите из ValMAX через меню значка в трее.

### Известные ограничения

- Над новым пузырём может появляться «Дата сообщения не подтверждена»: проверка календарного дня в этой preview требует исправления и проверки на реальной модели истории. Время «Удалено …» внутри пузыря означает время удаления, а не отправки.
- Локальное сохранение касается уже полученных сообщений после включения функции. Ранее удалённые или не полученные клиентом сообщения не восстанавливаются. Сложные случаи с неактивным аккаунтом, массовыми удалениями и сбоями записи ещё не прошли полный установленный тест.
- Полнота серверной истории для экспорта не доказана; готовый архив может содержать только доступную часть переписки. Фото, видео или голосовые могут отсутствовать, если их не удалось получить. Экспорт большого чата из установленной preview.12 и отмена во время длительной сетевой загрузки ещё требуют проверки.
- Готовность динамической защиты режима призрака и уведомлений до первого действия после запуска не подтверждена. Полный набор сценариев установленного клиента, включая MData с несколькими аккаунтами, также не завершён.
- Проверка материалов и условий распространения всего включённого клиента остаётся открытой: для MAX и части его сторонних компонентов, нескольких модулей Qt и WebView2 нет завершённого подтверждения применимых материалов. Наличие собранного установщика не означает готовность к публичному распространению.

**Проверенный файл:** `ValMAX-8.9.0-native.preview.12-Setup.exe`
**SHA-256:** `4372d8f3f44652c6ee13b9974deb31a66dd64084716f7eda90a6360865b95d38`

## English

ValMAX 8.9.0-native.preview.12 uses a C++/Qt and Rust runtime. The installed package contains no Python runtime or local HTTP helper. This Windows x64 build is based on MAX 26.35.1.80662 and is intended for controlled testing.

### Changes

- **Retained deletions · BETA.** A previously received message observed as deleted for everyone can appear inside the chat as a local bubble with a trash icon. In a two-account installed-client smoke test, the bubble appeared between surviving messages and persisted after restart. Older records without an original order key remain approximately positioned.
- **Selected-chat export · BETA.** Ordinary api2 is used for available history, photos, videos and voice messages. The UI warns about partial history, reports progress and paces requests. Unavailable attachments must be marked as omissions. An installed test of the previous preview obtained 11 synthetic messages and 6 media files; 2 files were unavailable. This does not prove complete history or installed preview.12 export behavior.
- **Upgrade.** Setup retains the previous program for rollback and preserves MXMOD account data and Windows credentials. Fully exit ValMAX from the tray menu before running Setup.

### Known limitations

- A new retained bubble may show “Message date not confirmed.” This calendar-day check needs a fix and an installed-client test. Its “Deleted …” timestamp is the deletion time, not the original send time.
- Retention applies only to messages the client already received after the feature was enabled. Earlier deletions or messages never received cannot be recovered. Inactive-account, high-volume and storage-failure paths need further installed testing.
- Server history completeness is unverified. An archive may contain only the available part of a conversation; media may be omitted when unavailable. Large-chat export and cancellation during prolonged network activity have not been tested in installed preview.12.
- Ghost and notification protection readiness before first use is unverified. The full installed-client regression, including multi-account MData transfer, is incomplete.
- Whole-client redistribution materials and applicable terms remain under review for MAX, bundled third-party components, several Qt modules and WebView2. A built installer does not establish public distribution readiness.

**Verified asset:** `ValMAX-8.9.0-native.preview.12-Setup.exe`
**SHA-256:** `4372d8f3f44652c6ee13b9974deb31a66dd64084716f7eda90a6360865b95d38`
