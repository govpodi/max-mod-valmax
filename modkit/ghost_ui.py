"""Source-only native ghost settings entry; no native hooks or profile access.

The shared generator must pin LeadingSettingsBar.qml and disable its AOT entry.
Requested settings are stored separately from the runtime's confirmed filters.
"""
from pathlib import Path

from quick_replies_ui import once

RESOURCE_NAMES = ("LeadingSettingsBar.qml",)


def transform_leading(source, directory=None):
    source = source.replace("\r\n", "\n")
    if "settingsValMAXGhost" in source:
        raise ValueError("Ghost settings transform already applied")
    directory = Path(directory) if directory is not None else Path(__file__).parent
    panel = (directory / "ghost_panel.qml").read_text(encoding="utf8")
    component_anchor = "    Component {\n        id: settingsDev\n"
    source = once(source, component_anchor, panel + "\n" + component_anchor)
    privacy_anchor = '''                OpenPageSettingsCellImpl {
                    title: qsTranslate("Settings", "Privacy")
'''
    entry = '''                OpenPageSettingsCellImpl {
                    title: "Режим призрака"
                    iconSource: "qrc:/valmax/icons/ghost.svg"
                    settingsPage: settingsValMAXGhost
                }

'''
    return once(source, privacy_anchor, entry + privacy_anchor)


def transform_sources(sources, directory=None):
    if "LeadingSettingsBar.qml" not in sources:
        raise ValueError("Ghost settings require LeadingSettingsBar.qml")
    result = dict(sources)
    result["LeadingSettingsBar.qml"] = transform_leading(sources["LeadingSettingsBar.qml"], directory)
    return result
