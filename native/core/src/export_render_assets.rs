// Static viewer data copied from modkit/chat_export_render.py; keep in sync.
// Source SHA256: 53f37cbaf2a58ea8c76a694761fd172a95a3e5687d2ec05606499c91103d1d9b
pub const ATTACHMENT_TYPES: &[&str] = &["audio", "file", "image", "unsupported", "video", "voice"];
pub const REASONS: &[&str] = &[
    "asset_too_large",
    "cancelled",
    "download_failed",
    "not_available",
    "not_downloaded",
    "permission_denied",
    "source_changed",
    "source_unavailable",
    "timeout",
    "unsupported",
];
pub const STOP_REASONS: &[&str] = &[
    "attachment_limit",
    "cancelled",
    "end_of_history",
    "history_error",
    "message_limit",
    "message_too_large",
    "no_progress",
    "page_limit",
    "timeout",
    "unsupported_history",
];
pub const MIME_EXT: &[(&str, &str)] = &[
    ("image/jpeg", "jpg"),
    ("image/png", "png"),
    ("image/gif", "gif"),
    ("image/webp", "webp"),
    ("video/mp4", "mp4"),
    ("video/webm", "webm"),
    ("video/ogg", "ogg"),
    ("audio/ogg", "ogg"),
    ("audio/opus", "opus"),
    ("audio/mpeg", "mp3"),
    ("audio/wav", "wav"),
    ("audio/x-wav", "wav"),
    ("audio/mp4", "m4a"),
    ("audio/webm", "webm"),
    ("audio/flac", "flac"),
    ("application/pdf", "pdf"),
];
pub const DOWNLOAD_EXTENSIONS: &[&str] = &[
    "3gp", "7z", "aac", "avi", "csv", "doc", "docx", "flac", "gif", "jpeg", "jpg", "m4a", "m4v",
    "mkv", "mov", "mp3", "mp4", "mpeg", "mpg", "oga", "ogg", "opus", "pdf", "png", "ppt", "pptx",
    "rar", "txt", "wav", "webm", "webp", "wma", "xls", "xlsx", "zip",
];
pub const ACTIVE_EXTENSIONS: &[&str] = &[
    "appx", "bat", "cjs", "cmd", "com", "dll", "exe", "hta", "htm", "html", "jar", "js", "lnk",
    "mjs", "msi", "msix", "ps1", "psm1", "reg", "scr", "svg", "svgz", "url", "vbe", "vbs", "wsf",
    "wsh", "xhtml",
];
pub const DETECTED_EXTENSIONS: &[(&str, &[&str])] = &[
    ("image/jpeg", &["jpeg", "jpg"]),
    ("image/png", &["png"]),
    ("image/gif", &["gif"]),
    ("image/webp", &["webp"]),
    ("video/mp4", &["3gp", "m4v", "mov", "mp4"]),
    ("audio/mp4", &["m4a", "mp4"]),
    ("video/webm", &["webm"]),
    ("audio/webm", &["webm"]),
    ("video/ogg", &["ogg"]),
    ("audio/ogg", &["oga", "ogg", "opus"]),
    ("audio/mpeg", &["mp3"]),
    ("audio/wav", &["wav"]),
    ("audio/flac", &["flac"]),
];
pub const REASON_TEXT: &[(&str, &str)] = &[
    ("not_available", "Недоступно в клиенте"),
    ("permission_denied", "Нет доступа"),
    ("download_failed", "Не удалось скачать"),
    ("timeout", "Истекло время загрузки"),
    ("unsupported", "Этот тип не поддерживается"),
    ("cancelled", "Загрузка отменена"),
    ("not_downloaded", "Не загружено"),
    ("source_unavailable", "Локальный файл недоступен"),
    ("asset_too_large", "Файл превышает предел размера"),
    ("source_changed", "Файл изменился при чтении"),
];
pub const TYPE_TEXT: &[(&str, &str)] = &[
    ("image", "Изображение"),
    ("video", "Видео"),
    ("voice", "Голосовое сообщение"),
    ("audio", "Аудиозапись"),
    ("file", "Файл"),
    ("unsupported", "Неподдерживаемое вложение"),
];
pub const CSS: &str = "*{box-sizing:border-box}html{color-scheme:light}body{margin:0;background:#eef0f4;color:#17212b;font:15px/1.45 system-ui,\"Segoe UI\",sans-serif}header{background:#fff;border-bottom:1px solid #dce1e7;padding:22px max(20px,calc((100vw - 920px)/2));overflow-wrap:anywhere}h1{margin:0 0 5px;font-size:23px}.muted{color:#687889;font-size:13px}.notice{margin-top:12px;padding:10px 12px;border-radius:10px;background:#edf5ff}.notice.partial{background:#fff3dc}main{max-width:920px;margin:auto;padding:20px 16px 28px}.day{text-align:center;color:#657281;margin:18px 0 12px;font-size:12px}.message{display:flex;margin:7px 0}.message.outgoing{justify-content:flex-end}.bubble{max-width:78%;min-width:100px;border:1px solid #e2e7ed;border-radius:15px 15px 15px 4px;background:#fff;padding:10px 13px;overflow-wrap:anywhere}.outgoing .bubble{background:#e1edff;border-color:#d3e4fc;border-radius:15px 15px 4px 15px}.author{font-size:13px;font-weight:600;color:#2868c7;margin-bottom:4px}.text{white-space:pre-wrap}.time{text-align:right;font-size:11px;color:#718293;margin-top:5px}.reply{border-left:3px solid #6ca1ec;background:#f3f6fb;border-radius:4px;padding:6px 9px;margin:2px 0 8px;font-size:13px;white-space:pre-wrap}.reply strong{display:block;color:#2868c7}.attachment{margin-top:9px;padding-top:7px;border-top:1px solid #dce4ef}.attachment img,.attachment video{display:block;max-width:100%;max-height:480px;border-radius:8px}.attachment audio{display:block;width:360px;max-width:100%}a{color:#1976e9;text-decoration:none}a:hover{text-decoration:underline}.attachment a{display:inline-block;margin-top:5px}.missing{padding:8px;border-radius:6px;background:#f3f4f6;color:#667383;font-size:13px}.service{color:#687889;font-size:13px}footer{max-width:920px;margin:auto;padding:16px 24px 32px;color:#687889;font-size:12px}@media(max-width:600px){.bubble{max-width:94%}header{padding:18px 16px}main{padding:12px 8px}.bubble{padding:9px 11px}}";
