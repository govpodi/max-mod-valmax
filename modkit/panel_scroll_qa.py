"""Test-only registration of unchanged, version-pinned native scroll widgets.

Legacy panel fixtures keep their synthetic models/backend adapters; scroll
behavior is provided by the actual MAX SDK QML, never a Flickable stub.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

NATIVE = Path('D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/UiKitModule')
SOURCES = {'MeFlickable.qml': 'efac68730207620b2e9fb39788b7c788a58b5b4e660bfc7648baff218d618257',
           'MeScrollBar.qml': '805721825b8f121a4b44341a4383af6e60771dd7b5adf66ade4b07b9bcb90900'}
_KEEP = []


def register_native_scroll(output):
    """Call once after a fixture's existing UiKit/DesignSystem registration.

    DesignSystem.SizeBase must contain px6=6, as required by native MeScrollBar.
    Only color-token values are synthetic; component source bytes stay exact.
    """
    from PySide6.QtCore import QObject, Property, QUrl
    from PySide6.QtGui import QColor
    from PySide6.QtQml import qmlRegisterSingletonInstance, qmlRegisterType
    directory = Path(output).resolve(strict=True) / 'native-scroll-types'
    directory.mkdir(exist_ok=False)
    (directory / 'qmldir').write_text('module UiKitModule\nMeFlickable 1.0 MeFlickable.qml\nMeScrollBar 1.0 MeScrollBar.qml\n', encoding='utf8')
    for name, expected in SOURCES.items():
        data = (NATIVE / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Pinned native scroll component changed')
        target = directory / name
        target.write_bytes(data)
        qmlRegisterType(QUrl.fromLocalFile(str(target)), 'UiKitModule', 1, 0, name[:-4])

    class AppearanceTokens(QObject):
        @Property(QColor, constant=True)
        def colorStatesFloatScrollBarPressed(self):
            return QColor('#64748b')

        @Property(QColor, constant=True)
        def colorStatesFloatScrollBarHover(self):
            return QColor('#8290a1')

        @Property(QColor, constant=True)
        def colorFloatScrollBar(self):
            return QColor('#9da6b0')

    tokens = AppearanceTokens()
    qmlRegisterSingletonInstance(AppearanceTokens, 'ClientAppearanceModule', 1, 0, 'AppearanceTokens', tokens)
    _KEEP.extend((AppearanceTokens, tokens))
    return {'components': dict(SOURCES), 'nativeSourceBytesUnchanged': True}
