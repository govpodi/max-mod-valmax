# Иконки ValMAX

Семь оригинальных иконок: приватность (щит с галочкой), призрак, MData (файл с ключом), быстрые ответы (диалог с молнией), аккаунты (два пользователя), экспорт чата (диалог со стрелкой вниз) и бизнес (портфель).

Штатные `icon_pack/privacy.svg`, `profile.svg`, `reply.svg`, `devices.svg` и `case.svg` MAX 26.35.1 изучены из неизменяемого max.exe с SHA-256 `d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182`. У них сетка 24×24, примерно 2 px оптическая линия, скругления, прозрачный фон и `fill="currentColor"`. Новые иконки следуют этим параметрам; геометрия нарисована заново.

- `icons/*.svg` — готовые ресурсы. Линии запечены в заполненные контуры с `currentColor`, как у штатного пакета. Фона, фиксированной расцветки, шрифтов, внешних ссылок и встроенных изображений нет.
- `prototypes/*.svg` — редактируемые монолинейные оригиналы с `stroke-width="2"` и круглыми окончаниями.
- `source/geometry.json` — исходные координаты, смысл и названия.
- `source/build_icons.py` — воспроизводимая генерация заполненных контуров через QPainterPathStroker; сохраняет кривые, не трассирует растровую картинку.
- `catalog.json` — роли, URL, SHA-256 и границы; `valmax-icons.qrc` — отдельное описание новых ресурсов.

Готовые SVG занимают 8.7 КБ. Они проверены QtSvg 6.11.2 при 24/32/48/72 px: прозрачные углы, без обрезания по краям, совпадение силуэта с редактируемым оригиналом. Лист сравнения светлой/тёмной/активной темы: `D:/ValMAX/analysis/valmax-icons-qa-02/valmax-icons-contact-sheet.png`. Отдельные PNG в том же каталоге имеют прозрачный фон. Фон листа сравнения не является частью иконок.

## Подключение в native UI

Новые URL — `qrc:/valmax/icons/privacy.svg`, `ghost.svg`, `mdata.svg`, `quick-replies.svg`, `accounts.svg`, `chat-export.svg`, `business.svg`. В таблицах Python им соответствует префикс `:/valmax/icons/`.

`modkit/valmax_icons.py:resource_payloads()` возвращает семь проверенных SVG-байтов. Штатный `UiKit.SvgIcon` использует `image://svg_icon/<цвет>/<source>`, а контуры `currentColor` совместимы с существующими SVG. Цвет передаётся через штатный `fillColor`/`iconFillColor`, поэтому отдельные светлые/тёмные версии не нужны. Поддержка data-URI именно внутри native SVG provider не установлена; не следует подставлять туда data-URI без проверки.

Для новых URL подготовлен `modkit/icon_resources.py`. Он клонирует **весь отдельный icon_pack RCC**: распаковывает 546 оригинальных файлов, добавляет семь ValMAX, затем официальным Qt rcc создаёт binary format 3 с zlib. Все исходные имена и содержимое после распаковки сохраняются. Output должен быть новым пустым каталогом. Публичные help/version/noargs команды Qt tools не используются: на Windows они показывают окно Usage. Компиляция запускается скрыто с действительным QRC, stdout/stderr перенаправлены.

`prepare(baseline_bytes, empty_output)` возвращает `(bundle_bytes, evidence)`. `evidence.section_offsets` содержит позиции `tree`, `names`, `data` внутри полного RCC blob; `evidence.anchors` — шесть native LEA с raw/RVA и ожидаемыми байтами. Shared builder должен добавить blob в существующую read-only UI PE-секцию и рассчитать RIP displacement к `bundleRva + section_offsets[field]`:

```python
replacement = original_lea[:3] + struct.pack('<i', new_target_rva - anchor_rva - 7)
```

Обновить надо и регистрацию, и штатную очистку ресурса:

| Путь | data LEA RVA | names LEA RVA | tree LEA RVA |
|---|---|---|---|
| qRegisterResourceData, call 0x9e2b3e | 0x9e2b24 | 0x9e2b30 | 0x9e2b37 |
| qUnregisterResourceData, call 0x9e2b04 | 0x9e2aec | 0x9e2af6 | 0x9e2afd |

Новые вызовы, code shims, отдельные регистрации через помощник, изменения ABI/unwind не нужны. Старые общие иконки не подменяются. Размер PE-секции, SHA/byte guards и обратимость остаются обязанностью shared builder; helper max.exe не изменяет. Изменённые QML-ресурсы по-прежнему требуют обычного AOT cache-key invalidation.

Пакет проверен Qt QResource/QFile в отдельном Python-процессе: все 553 URL разрешаются, хеш каждого файла совпадает, ресурс корректно unregister. Повторные компилирования в разных каталогах дали одинаковые байты. Доказательства: `D:/ValMAX/analysis/valmax-icons-rcc-qa-03/icon-resource-evidence.json` и `qt-rcc-verification.json`.

## Воспроизведение

Из `D:/ValMAX` с лабораторным Python/PySide6:

```text
python -B -X utf8 assets/valmax-icons/source/build_icons.py
python -B -X utf8 assets/valmax-icons/source/render_qa.py --output D:/ValMAX/analysis/new-icon-qa
python -B -X utf8 -m unittest discover -s modkit -p "test*icon*.py"
```

Native MAX, профили, credentials, переписка и сетевые запросы этой работой не затрагивались. Ссылки из панелей и shared RCC generator в этом изменении ещё не переключены.
