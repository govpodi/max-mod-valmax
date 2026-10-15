# Сторонние компоненты / Third-party notices

[MIT](LICENSE) относится к оригинальному коду ValMAX и оригинальным ресурсам проекта. Он не меняет лицензии и права на сторонние материалы. ValMAX — неофициальная модификация MAX; этот репозиторий не выдаёт права на исходные компоненты MAX или Qt.

## Материалы MAX

Модификация привязана к MAX **26.35.1.80662**. Исходные компоненты MAX принадлежат их правообладателям. В репозитории присутствуют следующие производные материалы:

- `modkit/ui_privacy.py` — сгенерированные patch specifications. Добавляемый блок содержит 32 изменённых QML-ресурса MAX и объединённый RCC с **546 оригинальными иконками MAX**, а также ресурсами ValMAX. Это не полностью оригинальный код ValMAX. Генератор: `modkit/ui_prepare_features.py`; исходные ресурсы читаются из отдельно полученного точного baseline.
- `modkit/config.defaults.json` — полный шаблон embedded configuration, извлечённый из `config.dll` MAX. Его назначение и фиксированный hash описаны в `modkit/launch_mod.py`.
- Трансформаторы `modkit/*_ui.py` и native patch specifications содержат upstream якоря, идентификаторы ресурсов и ожидаемые байты для проверки точной версии.

Эти upstream части **не перелицензируются под MIT**. Существующие copyright/license notices следует сохранять. MAX/Qt executable files, DLL, MSI, извлечённые наборы upstream ресурсов и baseline не являются исходниками ValMAX под MIT и не хранятся в Git. Их наличие в готовом установщике не меняет прав правообладателей и условий соответствующих компонентов.

Семь иконок `assets/valmax-icons/icons/*.svg`, их редактируемые варианты и `source/geometry.json` описаны в [каталоге иконок](assets/valmax-icons/README.md) как заново нарисованная геометрия ValMAX. Они входят в область оригинальных ресурсов проекта.

## Python-компоненты и инструменты сборки

Версии ниже сверены с `THIRD-PARTY-NOTICES.txt` внутри payload установщика ValMAX 8.8.1. Обозначения лицензий сверены с metadata и license files установленных пакетов; ссылки ведут к первичным страницам пакетов и их документации.

| Компонент | Версия | Лицензия / условия |
|---|---|---|
| [Frida](https://pypi.org/project/frida/17.23.3/) | 17.23.3 | wxWindows Library Licence 3.1; [COPYING upstream](https://github.com/frida/frida/blob/main/COPYING). |
| [cryptography](https://pypi.org/project/cryptography/50.0.2/) | 50.0.2 | `Apache-2.0 OR BSD-3-Clause`. |
| [cffi](https://pypi.org/project/cffi/2.1.1/) | 2.1.1 | `MIT-0`, согласно `License-Expression` этой версии. |
| [Capstone](https://pypi.org/project/capstone/5.0.9/) | 5.0.9 | BSD 3-Clause; license file включает дополнительные upstream notices. |
| [pefile](https://pypi.org/project/pefile/2024.8.26/) | 2024.8.26 | MIT. |
| [PyInstaller](https://pypi.org/project/pyinstaller/6.22.3/) | 6.22.3 | GPL v2 or later со специальным исключением; [условия upstream](https://pyinstaller.org/en/stable/license.html). |
| [pyinstaller-hooks-contrib](https://pypi.org/project/pyinstaller-hooks-contrib/2026.8/) | 2026.8 | Standard hooks: GPL-2.0-or-later; runtime hooks: Apache-2.0; [LICENSE upstream](https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/master/LICENSE). |

В payload также сохранены тексты лицензий **CPython** и **Tk**. Полный `THIRD-PARTY-NOTICES.txt`, создаваемый `modkit/package_mod.py`, сопровождает установленный helper; эта краткая таблица не заменяет его notices.

`modkit/requirements.txt` дополнительно фиксирует [zstandard 0.25.0](https://pypi.org/project/zstandard/0.25.0/) (`BSD-3-Clause`). Для генерации ресурсов и UI QA используется PySide6/Qt; они отдельно устанавливаются в среду разработчика и не покрываются MIT ValMAX. Условия Qt зависят от выбранного upstream пакета и компонентов: [официальная информация Qt](https://www.qt.io/licensing/).

## English

The MIT license covers original ValMAX work only. Generated `modkit/ui_privacy.py` includes modified MAX QML and an RCC retaining 546 original MAX icons. `modkit/config.defaults.json` is the full embedded MAX configuration template. These materials, MAX/Qt binaries, and third-party dependencies retain their respective rights and terms. Preserve upstream notices. The installer payload includes the full `THIRD-PARTY-NOTICES.txt`; the table above is a summary, not a replacement.
