# ValMAX 8.9.0 Native Preview 14 — local source candidate (Windows x64)

**Status: local testing candidate. `releaseEligible=false`. No public preview.14 installer or checksum is published by this source commit.** Building an installer does not establish installed-client behavior or distribution readiness.

## Русский

В локальном `main` находятся исходники 8.9.0-native.preview.14 для базы MAX 26.35.1.80662. Пользовательский runtime переносится на C++/Qt и Rust; Python runtime и локальный HTTP-помощник не входят в предусмотренный установленный пакет. Сведения о проверке предыдущей версии сохранены в [preview.12](NATIVE_PREVIEW_12.md) и не считаются проверкой preview.14.

### Изменения в исходниках

- **Экспорт выбранного чата · БЕТА.** Штатный api2 получает доступную историю. Фото, видео и голосовые записываются потоком с локальным пределом 4 ГиБ на файл. Интерфейс предупреждает о возможной неполноте, показывает ход работы и позволяет отменить экспорт.
- **Ограничение нагрузки.** Запросы дозируются; сетевой простой и общая длительность передачи ограничены. Для видео после допустимой ошибки предусмотрена одна попытка другого проверенного разрешения.
- **Материалы зависимостей.** В план сборки добавлены материалы QtImageFormats и уведомления libtiff/libwebp. Это не подтверждает материалы и права на распространение всего клиента.

### Что остаётся проверить

- Полнота серверной истории не доказана. Архив может содержать только доступную часть переписки; недоступные вложения должны быть отмечены в отчёте экспорта.
- Установленная preview.14 ещё требует проверки большого чата, фото, видео, голосовых, отчёта о пропусках, прогресса, отмены, ограничений темпа и ошибок сети.
- Сохранение удалённых сообщений касается только уже полученных клиентом сообщений после включения функции. Поведение пузыря в установленной preview.14, неактивные аккаунты, массовые удаления и сбои записи требуют проверки. Время удаления не является временем отправки.
- Ранняя защита режима призрака и уведомлений, полный цикл установленного клиента с несколькими аккаунтами и материалы для публичного распространения остаются открытыми.

Публичный установщик и его SHA-256 будут указаны отдельно только после проверок. [История изменений](../CHANGELOG.md).

## English

The local `main` contains the 8.9.0-native.preview.14 source candidate for MAX 26.35.1.80662. Its intended installed runtime is C++/Qt and Rust, without a Python runtime or local HTTP helper. Evidence for [preview.12](NATIVE_PREVIEW_12.md) is historical and does not validate preview.14.

### Source changes

- **Selected-chat export · BETA.** Ordinary api2 retrieves available history. Photos, videos and voice messages are streamed with a local 4 GiB per-file limit. The UI warns about incomplete history, reports progress and supports cancellation.
- **Bounded network use.** Requests are paced; idle and total transfer times are bounded. One alternative verified video resolution may be tried after an eligible failure.
- **Dependency materials.** The build plan includes QtImageFormats materials and libtiff/libwebp notices. Whole-client redistribution materials and rights remain unresolved.

### Open checks

- Complete server history is unproven. The archive may contain only available messages, and unavailable attachments should appear as omissions in the export report.
- Installed preview.14 needs checks for a large chat, photos, videos, voice messages, omission reports, progress, cancellation, pacing and network failures.
- Retained deletions apply only to messages already received after the feature was enabled. The installed preview.14 bubble, inactive accounts, high-volume deletions and storage failures need verification. A deletion timestamp is not the original send time.
- Early ghost and notification protection, the full installed-client lifecycle with multiple accounts, and public redistribution materials remain open.

A public installer and SHA-256 will be listed separately only after verification. [Changelog](../CHANGELOG.md).
