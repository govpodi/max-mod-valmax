# Передача @pic/@vid в черновик MAX

Проверенный минимальный контракт — вызов штатного
`uploadPhotoOrVideo(QList<QUrl>)` в GUI-потоке и сохранение выбранного файла в
локальном staging-кэше. Возвращаемого идентификатора вложения или отдельного
подтверждения принятия файла у этого метода нет. Отсутствие такого подтверждения
не блокирует перенос существующей функции: старая реализация тоже сохраняет
файл по TTL и передаёт его штатному полю сообщения без ожидания ACK.

## Что проверено

База: MAX `26.35.1.80662`, `max.exe` SHA-256
`d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182`.
Изолированная тестовая копия: `max.exe`
`584449c11629b1f3a7d04198f549b61f443d60c6c8458f74eb92bb515a51ab1b`.
Пять исследованных участков машинного кода совпадают между базой и этой копией.

- `analysis/native-migration-v1/media-draft-static-proof.json`: **260 проверок**
  трёх MOC-таблиц, ограниченных участков кода и извлечённого QML. Базовые файлы не
  менялись; полный поиск по бинарникам не выполнялся.
- `analysis/native-migration-v1/media-draft-max-proof.json`: **один успешный
  запуск** собственного анонимного MAX с прежними защитными перехватчиками до
  возобновления процесса. Настоящий `QQmlEngine` создал
  `MessageSenderViewContext` с пустым chatId и null context factory. Методы
  `setPresenterFactory`, `onCreate`, отправка и подключение аккаунта не вызывались.
- В созданном объекте проверены реальные классы, `roleNames()`, сигнатура и
  возвращаемый тип. Затем `QMetaMethod::invoke` вызвал метод на проверенном
  **null presenter** с URL единственного собственного синтетического PNG.
  Результат вызова — `true`, число строк модели **0 → 0**, файл сохранился.
  Это прямой пример: успешная диспетчеризация не означает принятие вложения.
- Тестовые процессы **33912** и **23280** завершены через удерживаемые process
  handles; ожидание завершения прошло. `max.exe`, `core.dll` и Qt DLL не менялись.
  Пользовательская установка и профили не использовались. Временные Frida guards
  — только инструмент проверки, не часть будущего runtime и не ОС-песочница.

Helper DLL: SHA-256
`98d4a8476752af893048ff4ad2ea8fac3e502fef774b5ee6320146c9632a45f4`.
Исходники и runner: `native/tests/media/draft/`; статический reader:
`native/tests/media/draft_contract_probe.py`. Тестовый приватный offset `0x18`
использован лишь для запрета вызова при ненулевом presenter в этой точной версии.
Он не является интерфейсом будущего адаптера.

## Реальные точки подключения

Извлечённый `OneMeUi/BaseMessageSendPanel.qml` задаёт:

```qml
readonly property alias messageSenderViewContext: viewContext
attachmentUploadModel: viewContext.uploadView.uploadModel
onPhotoOrVideoUploadSelected: fileList => viewContext.uploadView.uploadPhotoOrVideo(fileList)
onFileUploadSelected: fileList => viewContext.uploadView.uploadFiles(fileList)
onRemoveAttachClicked: index => viewContext.uploadView.removeRow(index)
```

Рабочий sender получает `chatId` из `chatPropertiesContext.get().chatId`,
`isDelayedMode` из поля сообщения и фабрику из
`commonDependency.messageContextFactory`. Штатный QML инициализирует presenter
через `messageSenderDependency.presenterFactory` и `onCreate()`.
Адаптер должен использовать **уже созданный штатный sender активного поля**;
самостоятельная фабрика sender не требуется и не проверена как рабочий путь.

В GUI-потоке доступна цепочка:
`BaseMessageSendPanel.messageSenderViewContext → uploadView → uploadModel`.
Факт наличия QObject и метода недостаточен для идентификации текущего аккаунта.
Привязка page/account должна исходить из доверенного host context; содержимое
запроса QML не может выбирать account ID, путь staging или другой sender.

| Объект | Подтверждённый интерфейс | Ограничение |
| --- | --- | --- |
| `Ui::History::AttachmentUpload::View` | `void uploadPhotoOrVideo(QList<QUrl>)` | `invoke=true` подтверждает только вызов |
| Тот же View | `mediaAttachLimitOverflow()`, `tooLargeUploadFile()`, `cleared()` | Ошибки/общая очистка без ID выбранного файла |
| `UploadModel` | `attachmentsCount: qsizetype`, `hasFile: bool` | Bindable properties; собственных signals/methods нет |
| `UploadModel` | Общие сигналы `QAbstractItemModel` | Row insertion не связывает строку с нашей операцией |
| Role `0x105` | `localFilePreview`, значение `QString` | Равенство исходному staging-пути не доказано |
| Roles `0x104`, `0x106..0x10a` | `networkPreviewUrl`, `chatId`, `messageId`, `postId`, `attachmentId`, `isDelayed` | Значения новых строк в рабочем аккаунте не исследовались |

`SendAttachmentView.qml` передаёт `localFilePreview` как `localFilePath` в
photo/video/file delegate. Машинный код `data()` копирует QString из записи
модели. Кто заполняет это поле, представляет ли оно исходный файл или отдельное
превью, не установлено. Поэтому сравнение этого поля с URL, изменение count или
появление любой строки **не используются как ACK**.

## Порядок передачи без гонки с отменой

1. Пользователь явно выбирает opaque result ID. Сервис загружает выбранный
   оригинал; ControllerStamp, engine lease и native cancellation token остаются
   привязаны к аккаунту, полю и поколению поиска. Лимит выбранной операции занят.
2. На I/O worker создаётся `PreparedFile`. URL копируется только в native DTO.
   Перед передачей ещё раз проверяются stamp/lease и отмена.
3. **До публикации DTO в GUI** вызывается `retain_for_handoff()`. Успех переводит
   файл в terminal TTL-кэш и освобождает удерживаемый файловый handle. Отмена до
   этого перехода отменяет подготовленный файл; после перехода release/cancel
   уже не удаляет его. При ошибке retain в GUI ничего не публикуется.
4. GUI получает DTO, повторно проверяет engine/page/account/search generation,
   существование штатного sender, его точную сигнатуру и лимит вложений.
   Передача выполняется прямым вызовом в GUI-потоке. Для локального URL не нужен
   HTTP, и он не передаётся в JSON, журнал или публичную модель результатов.
5. После вызова возвращается конечный результат **dispatched**, **stale** или
   **unavailable**. Он освобождает общий лимит выбранной операции. Ни один из
   этих результатов не называется «вложение принято».
6. Если поле исчезло, поколение сменилось, метод недоступен или результат
   неоднозначен, уже retained-файл остаётся в TTL-кэше. Это ограниченный расход
   квоты, предотвращающий удаление файла, который MAX уже мог начать читать.
   Нет попытки откатить чужой черновик или удалить произвольную строку модели.

**Нельзя** сначала вызвать MAX, затем делать retain: конкурентная отмена между
ними могла бы удалить уже переданный файл. Дополнительная ACK-транзакция для
этого контракта не нужна.

Уведомления error от штатного View остаются частью обычного интерфейса MAX.
Подпись источника задаётся в том же активном поле и поколении, как в legacy,
без вызова `sendPressed`, `sendOnlyFiles` или других методов отправки. QML/C++
вызов может синхронно вызвать сигналы: sender/composer проверяются через GUI-only
`QPointer` после каждой потенциально реентрантной операции, до следующего доступа.

## Владение файлами и пределы проверки

Legacy `modkit/inline_media.py::_stage_entry` сохраняет файл до ответа,
`inline_media_composer.qml::modInlineSelect` задаёт подпись и вызывает
`modQuickReplyAttachmentsRequested([], [url])`; отдельного ACK нет.
Очистка старого staging запускается при следующем выборе медиа.

Native сохраняет пределы: **25 MiB** на файл, **128 MiB** на staging-кэш,
**24 часа** до возможности удаления. TTL не гарантирует удаление точно в срок:
очистка выполняется при следующей операции; занятый читателем MAX файл нельзя
насильно удалять. Время считается от staging-файла, а не от отправки сообщения.
Длительно оставленный черновик может пережить TTL — это известное ограничение
старого поведения, а не доказанное долговременное владение файлами со стороны MAX.

Не доказаны копирование оригинала MAX, момент завершения чтения, соответствие
`localFilePreview` исходному пути и сохранность черновика после перезапуска.
Один тест с null presenter не является успешным добавлением в рабочий черновик.
Он достаточен для точной сигнатуры/диспетчеризации и для отказа от ложного ACK.

Следующие шаги реализации: process-wide media service, engine frontend,
host-привязка активного composer/account и его lifecycle, staged native DTO,
GUI handoff с указанными проверками. Для них нужен отдельный синтетический
end-to-end тест и затем обычная пользовательская проверка черновика. Этот отчёт
не включает ранний bootstrap gate, сохранение удалённых сообщений, установщик
или перенос действующей пользовательской установки.
