"""Pinned visibility guards for the native per-page header contexts."""
import hashlib

PINS = {'BaseLeadingListHeader.qml': '49ea31accc928479332d9b16984db22ed8561bff099768b632278b1bc5135654', 'LeadingChatsBar.qml': 'caaf029dcf3edcd0f796cdb149e9ea1e6e5abfb1bc83e42a93df48b2de5d3ba2'}

def checked(source, name):
    if type(source) is not str:
        raise ValueError('Header visibility source must be text')
    source=source.replace('\r\n','\n')
    if hashlib.sha256(source.encode('utf8')).hexdigest()!=PINS[name]:
        raise ValueError('Header visibility source/version mismatch')
    return source

def replace_one(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Visibility candidate anchor changed')
    return source.replace(old, new, 1)


def add_visibility(source, property_name):
    source = replace_one(source, 'import QtQuick\n', 'import QtQuick\nimport QtQuick.Window\n')
    return replace_one(source, '    id: root\n', '    id: root\n\n'
        f'    readonly property bool {property_name}: root.visible && root.Window.window !== null\n'
        '        && root.Window.window.visible && root.Window.window.visibility !== Window.Minimized\n')


def _header(source):
    source = add_visibility(source, 'valmaxHeaderActive')
    return replace_one(source,
        'return root.state === root.compactStoriesView && root.stateTransitionCompleted;',
        'return root.valmaxHeaderActive && root.state === root.compactStoriesView && root.stateTransitionCompleted;')


def _chats_bar(source):
    source = add_visibility(source, 'valmaxChatsPageActive')
    source = replace_one(source, '    title: qsTranslate("LeadingPanel", "Chats")',
        '    onValmaxChatsPageActiveChanged: {\n'
        '        if (!valmaxChatsPageActive) {\n'
        '            switchToChatListTimer.stop();\n'
        '            showcaseStartTimer.stop();\n'
        '        }\n'
        '    }\n\n    title: qsTranslate("LeadingPanel", "Chats")')
    source = replace_one(source, '                    function checkClickPosition(clickPos) {\n',
        '                    function checkClickPosition(clickPos) {\n'
        '                        if (!root.valmaxChatsPageActive)\n'
        '                            return;\n')
    source = replace_one(source, '                        target: appDelegate\n',
        '                        target: appDelegate\n                        enabled: root.valmaxChatsPageActive\n')
    source = replace_one(source,
        'if (Application.state !== Qt.ApplicationInactive && Application.state !== Qt.ApplicationHidden && !root.searchHasOverlay)',
        'if (root.valmaxChatsPageActive && Application.state !== Qt.ApplicationInactive && Application.state !== Qt.ApplicationHidden && !root.searchHasOverlay)')
    source = replace_one(source,
        'if (!root.searchHasOverlay && QmlUtils.isTrimmedEmpty(searchField.text))',
        'if (root.valmaxChatsPageActive && !root.searchHasOverlay && QmlUtils.isTrimmedEmpty(searchField.text))')
    return source



def transform_header(source):
    return _header(checked(source,'BaseLeadingListHeader.qml'))

def transform_chats_bar(source):
    return _chats_bar(checked(source,'LeadingChatsBar.qml'))

def transform_sources(sources):
    if type(sources) is not dict or not PINS.keys() <= sources.keys():
        raise ValueError('Pinned header resources required')
    result=dict(sources)
    result['BaseLeadingListHeader.qml']=transform_header(result['BaseLeadingListHeader.qml'])
    result['LeadingChatsBar.qml']=transform_chats_bar(result['LeadingChatsBar.qml'])
    return result

def reverse(source, original, name):
    original=checked(original,name)
    transform=transform_header if name=='BaseLeadingListHeader.qml' else transform_chats_bar
    if type(source) is not str or source.replace('\r\n','\n')!=transform(original):
        raise ValueError('Header visibility output drift')
    return original
