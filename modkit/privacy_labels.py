"""Static Russian labels for reviewed metadata; never derive text from values."""

FIELD_TITLES = {
    "userAgent": "Сведения о клиенте",
    "deviceId": "Идентификатор устройства",
    "clientSessionId": "Идентификатор сессии клиента",
    "deviceType": "Тип устройства",
    "appVersion": "Версия приложения",
    "osVersion": "Версия операционной системы",
    "locale": "Язык приложения",
    "deviceLocale": "Язык устройства",
    "deviceName": "Имя устройства",
    "screen": "Параметры экрана",
    "timezone": "Часовой пояс",
    "msiProductCode": "Код установочного пакета",
    "buildNumber": "Номер сборки",
}

TRACER_TITLES = {
    "tracer_init": "Подготовка SDK отчётов",
    "tracer_set_platform_info": "Сведения о платформе",
    "tracer_install_crash_handler": "Обработчик сбоев",
    "tracer_hardfix_crashclient": "Дополнительная настройка обработчика сбоев",
    "tracer_upload_async": "Запрос отправки отчётов",
    "tracer_log": "Запись в журнал SDK",
    "tracer_report_error_from_here": "Отчёт об ошибке",
    "tracer_report_nonfatal_from_here": "Отчёт о некритической ошибке",
    "tracer_set_userid": "Привязка идентификатора пользователя",
    "tracer_supports_arbitrary_dump": "Проверка поддержки дампов памяти",
    "tracer_enable_logging_to_console": "Журнал SDK в консоли",
    "tracer_collect_user_sample": "Подготовка диагностических данных",
    "tracer_set_proxy_info": "Настройка прокси SDK",
    "tracer_set_environment": "Настройка среды SDK",
    "tracer_set_user_sample_custom_property": "Свойство диагностических данных",
    "tracer_set_key": "Настройка ключа SDK",
    "tracer_enable_logging_to_callable": "Подключение обработчика журнала SDK",
}

UNKNOWN_REQUEST_DETAIL = "Назначение ещё не подтверждено; наблюдаются только тип и размер"
KNOWN_HEADER_DETAIL = "Тип запроса подтверждён по коду клиента; содержимое пакета не изучалось."

# MAX26.35.1 core.dll RTTI→vtable→constructor→base opcode setter proof:
# analysis/network-privacy/opcode_labels_review.json. These are packet types,
# not claims about the contents of a captured request or its tracker status.
# Additional 32/50/65/214 constructors and common wire-opcode storage:
# analysis/external-mods/windows-opcodes/constructor-proof.json.
REQUEST_TITLES = {
    1: ("Проверка соединения", "Соединение"),
    6: ("Инициализация сессии", "Сессия"),
    19: ("Авторизация", "Аккаунт"),
    27: ("Обновление ресурсов", "Ресурсы"),
    28: ("Получение ресурсов", "Ресурсы"),
    32: ("Информация о пользователе", "Контакты"),
    35: ("Статус контактов", "Контакты"),
    50: ("Состояние прочтения чата", "Чаты"),
    65: ("Статус «печатает»", "Чаты"),
    162: ("Список причин жалобы", "Жалобы"),
    208: ("Список историй", "Истории"),
    209: ("Список историй владельца", "Истории"),
    214: ("Отметка просмотра истории", "Истории"),
    256: ("Информация об организации", "Организации"),
    272: ("Список папок", "Папки"),
    300: ("Рекомендации чатов", "Чаты"),
}


def request_labels(opcode: int) -> tuple[str, str]:
    # Do not infer a purpose from traffic frequency, size or outside guesses.
    return REQUEST_TITLES.get(opcode, (f"Запрос №{opcode}", "Назначение не подтверждено"))
