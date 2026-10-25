# Сторонние компоненты / Third-party notices

[MIT](LICENSE) относится к оригинальному коду ValMAX и оригинальным ресурсам проекта. Он не меняет лицензии и права на сторонние материалы. ValMAX — неофициальная модификация MAX; этот репозиторий не выдаёт права на исходные компоненты MAX или Qt.

## Материалы MAX

Модификация привязана к MAX **26.35.1.80662**. Исходные компоненты MAX принадлежат их правообладателям. В репозитории присутствуют следующие производные материалы:

- `modkit/ui_privacy.py` — сгенерированные patch specifications. Добавляемый блок содержит 32 изменённых QML-ресурса MAX и объединённый RCC с **546 оригинальными иконками MAX**, а также ресурсами ValMAX. Это не полностью оригинальный код ValMAX. Генератор: `modkit/ui_prepare_features.py`; исходные ресурсы читаются из отдельно полученного точного baseline.
- `modkit/config.defaults.json` — полный шаблон embedded configuration, извлечённый из `config.dll` MAX. Его назначение и фиксированный hash описаны в `modkit/launch_mod.py`.
- Трансформаторы `modkit/*_ui.py` и native patch specifications содержат upstream якоря, идентификаторы ресурсов и ожидаемые байты для проверки точной версии.

Эти upstream части **не перелицензируются под MIT**. Существующие copyright/license notices следует сохранять. MAX/Qt executable files, DLL, MSI, извлечённые наборы upstream ресурсов и baseline не являются исходниками ValMAX под MIT и не хранятся в Git. Их наличие в готовом установщике не меняет прав правообладателей и условий соответствующих компонентов.

Семь иконок `assets/valmax-icons/icons/*.svg`, их редактируемые варианты и `source/geometry.json` описаны в [каталоге иконок](assets/valmax-icons/README.md) как заново нарисованная геометрия ValMAX. Они входят в область оригинальных ресурсов проекта.

## Нативный клиент и материалы

В нативном C++/Rust runtime нет Python, PyInstaller, PySide или прежнего локального
сервера. Python остаётся инструментом сборки и проверки. Таблица ниже описывает
прежний payload 8.8.1 и инструменты; она не является перечнем зависимостей
нового runtime.

Для нативных компонентов подготовлены отдельные материалы Rust, Qt, VC runtime,
WebView2 и Gum с привязкой к версиям и SHA-256. Материалы одного компонента не
подменяют материалы всего исходного клиента. В компактном кандидате 8.9.0
приложение использует установленный в Windows WebView2 Evergreen; прежняя
Fixed-копия в пакет не входит. Соответствующие исходные архивы Qt вынесены в
отдельный файл выпуска, который должен быть опубликован и сверён вместе с
установщиком. Полная привязка части MAX/Qt и сторонних библиотек пока не
установлена. Условия WebView2 Fixed Version относятся к прежним preview и сами
по себе не подтверждают применимость исторической редакции к тому runtime.

Для Qt 6.11.2 получены официальные исходные архивы Multimedia, Positioning,
SVG и WebChannel и исходные тексты лицензий. Из набора проверенных Qt-бинарников
preview.15 исключила семь файлов Qt Lottie и его `qmldir`: два индикатора записи
заменены на Qt Quick. Сборка preview.16 также не содержит импортов Qt Lottie.
Восемь корневых Qt DLL и четыре копии в сервисе исходного
аудита были побайтно сверены с официальными бинарными архивами; после исключения
Lottie в этом наборе остаются шесть корневых DLL и две копии в сервисе.
Проверка файлов не заменяет проверку записи аудио и видео в установленном
клиенте.

Пять плагинов **QtImageFormats** (`qicns`, `qtga`, `qtiff`, `qwbmp`, `qwebp`)
побайтно совпали с официальным бинарным архивом Qt 6.11.2. Для них подготовлены
[официальный архив исходников QtImageFormats](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtimageformats-everywhere-src-6.11.2.tar.xz),
восемь оригинальных текстов лицензий/правил и уведомления `COPYRIGHT` libtiff
и `COPYING` libwebp с Qt attribution. Эти файлы не устанавливают, какие
дополнительные кодеки статически включены в каждый плагин.

Для других компонентов подготовлены привязанные к версиям материалы:

- Официальные исходные архивы [FFmpeg 7.1.3 и 7.1.5](https://ffmpeg.org/download.html),
  их подписи и license notices сверены с указанными версиями. Это **справочные
  upstream исходники**: точное дерево, изменения и процесс сборки десяти
  FFmpeg DLL готового MAX/ONEME runtime пока не подтверждены. Все пять
  одноимённых FFmpeg DLL официального Qt 6.11.2 Multimedia имеют другие
  SHA-256, поэтому исходники Qt не доказывают их соответствие. В прежнем
  аудите нет `swscale-8.dll`.
- Тексты COPYING/LICENSE [OpenSSL 3.5.4](https://openssl-library.org/source/license/index.html)
  и [WinSparkle 0.9.3](https://github.com/vslavik/winsparkle/blob/v0.9.3/COPYING)
  сверяются с указанными upstream версиями. Установленный WinSparkle DLL
  отличается от официального файла той же версии на 23 байта в секции кода.
- [COPYING Frida Gum 17.23.3](https://github.com/frida/frida-gum/blob/17.23.3/COPYING)
  привязывается к проверенному devkit. Состав статически включённых объектов
  и уведомлений их зависимостей пока не установлен.
- 70 точных архивов crates.io и 139 исходных license/notice файлов для Windows
  MSVC графа Rust сверяются с `Cargo.lock` и доказательством сборки конкретного
  `valmax_core.dll`. Это не определяет, какой код остался после линковки, и не
  закрывает проверку встроенного C/ASM.

Этот перечень сохраняет происхождение и обозначает незакрытые условия. Он не
утверждает, что материалы всего клиента полны или что справочные upstream
архивы точно соответствуют FFmpeg DLL. Техническое состояние:
[перенос runtime](docs/native-migration.md).

## Python-компоненты прежней версии и инструменты сборки

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

The MIT license covers original ValMAX work only. Generated `modkit/ui_privacy.py` includes modified MAX QML and an RCC retaining 546 original MAX icons. `modkit/config.defaults.json` is the full embedded MAX configuration template. These materials, MAX/Qt binaries, and third-party dependencies retain their respective rights and terms. Preserve upstream notices. The legacy installer payload includes the full `THIRD-PARTY-NOTICES.txt`; the table above is a summary, not a replacement.

The native C++/Rust runtime does not bundle Python, PyInstaller, PySide or the
former local server. Python is used at build/test time. Native components have
separate version/hash-bound materials; those for one component do not establish
coverage for the whole MAX client. The applicability of the collected current
WebView2 Fixed Version terms to this runtime's historical version is not proven.

Preview.15 removed seven Qt Lottie binaries and their QML module directory after
two pinned recording indicators switched to Qt Quick. The preview.16 package
also has no Qt Lottie imports. Five QtImageFormats plugins
match the official Qt 6.11.2 binaries byte for byte, with matching source and
libtiff/libwebp notices prepared; optional static codec content is not proven.
Version-bound OpenSSL and WinSparkle notices, Frida Gum COPYING, and 70 Windows
MSVC Rust crate archives with 139 original license/notice texts have also been
prepared. The modified WinSparkle DLL's provenance, Gum static dependency
notices and linked Rust object review remain open.

Official upstream FFmpeg 7.1.3 and 7.1.5 source archives are reference
materials. The exact source tree, modifications and build process corresponding
to the ten bundled FFmpeg DLLs are not proven. Five same-named FFmpeg DLLs in
the official Qt 6.11.2 Multimedia archive have different SHA-256 hashes from
the candidate runtime. These collected materials do not certify completeness
for the whole client or exact FFmpeg source/build correspondence.
