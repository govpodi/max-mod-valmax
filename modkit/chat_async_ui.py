"""Pinned chat-list shell with independently incubated native row contents.

Applied after chat_pool_ui; the native ChatDelegate/PIN implementation remains
unchanged. Required model roles stay on the lightweight outer delegate.
"""
import hashlib

RESOURCE = ':/qt/qml/OneMeUi/ChatListView.qml'
INPUT_SHA256 = '1d487cd96a20ad668e76145bdd8b13f249d4e4a408315bf2a4dbed76f1195cb6'
MARKER = '// VALMAX_ASYNC_CHAT_ROWS'
ROLES = (
    ('var','chatClientId'), ('var','chatId'), ('var','contactId'), ('var','ownerId'),
    ('int','newMessagesCount'), ('int','chatType'), ('var','messageContext'),
    ('bool','isFavorites'), ('bool','hasReactions'), ('bool','hasMentions'),
    ('string','draftText'), ('var','lastReadMessage'), ('bool','isSavedMessagesChat'),
)

def checked(source):
    if type(source) is not str:
        raise ValueError('Chat async shell requires text')
    source = source.replace('\r\n','\n')
    if hashlib.sha256(source.encode('utf8')).hexdigest() != INPUT_SHA256:
        raise ValueError('Chat async shell version/order mismatch')
    return source

def delegate_block(source):
    start = source.index('            delegate: ChatDelegate {\n')
    end = source.index('            footer: ColumnLayout {\n',start)
    return source[start:end]

def shell(old_block):
    events = old_block[old_block.index('                onCreateFolderForChat:'):]
    roles = ''.join('                required property '+kind+' '+name+'\n' for kind,name in ROLES)
    forward = ''.join('                        '+name+': chatDelegate.'+name+'\n' for _kind,name in ROLES)
    return '''            delegate: ValMAX.ValMAXAsyncListRow {
                id: chatDelegate
                // VALMAX_ASYNC_CHAT_ROWS
'''+roles+'''
                required property var commonDependency
                required property var currentChatId
                property bool hasMessage: messageContext !== null && messageContext !== undefined && !messageContext.isNull
                property bool valmaxPendingOpen: false
                property string valmaxPendingIdentity: ""
                property int valmaxPendingGeneration: -1
                property bool valmaxSpacePressed: false
                property string valmaxSpaceIdentity: ""
                property string valmaxLeftPressIdentity: ""
                property string valmaxRightPressIdentity: ""
                signal clicked()
                signal createFolderForChat(var chatId)
                signal closeChatRequested(var chatId)
                signal previewClicked()
                signal rightMouseButtonClicked()

                view: allChatsListView
                width: root.width
                commonDependency: root.commonDependency
                currentChatId: root.currentChatId
                selected: currentChatId === chatId
                readonly property bool valmaxStories: commonDependency.serverParameters.storiesEnabled
                readonly property int valmaxTextSize: commonDependency.userAppearanceContext.typographySize
                rowHeight: Math.max(
                    Typography.FontUtil.getLineHeight(Typography.FontUtil.Styles.Body, valmaxTextSize)
                    + 2 * Typography.FontUtil.getLineHeight(Typography.FontUtil.Styles.BubbleDescription, valmaxTextSize)
                    + 2 * UiKit.DesignSystem.SpacingCommon._2XS,
                    valmaxStories ? UiKit.DesignSystem.AvatarSize.px64 : UiKit.DesignSystem.AvatarSize.px56)
                    + 2 * (valmaxStories ? UiKit.DesignSystem.SpacingCommon._S : 9)
                avatarSize: valmaxStories ? UiKit.DesignSystem.AvatarSize.px64 : UiKit.DesignSystem.AvatarSize.px56
                avatarX: valmaxStories ? UiKit.DesignSystem.SpacingCommon._S : UiKit.DesignSystem.SpacingCommon._L
                textOffset: avatarX + avatarSize + (valmaxStories ? UiKit.DesignSystem.SpacingCommon._S : UiKit.DesignSystem.SpacingCommon._M)
                viewportMargin: Math.min(rowHeight * 2, allChatsListView.height / 3)
                identityKey: {
                    try { return String(QmlUtils.contactIdString(commonDependency.selfInfo.id)) + ":" + String(QmlUtils.chatIdString(chatId)); }
                    catch (_) { return ""; }
                }
                function valmaxPool() {
                    pooled = true;
                    valmaxClearInput();
                }
                function valmaxClearInput() {
                    valmaxPendingOpen = false;
                    valmaxPendingIdentity = "";
                    valmaxPendingGeneration = -1;
                    valmaxSpacePressed = false;
                    valmaxSpaceIdentity = "";
                    valmaxLeftPressIdentity = "";
                    valmaxRightPressIdentity = "";
                }
                function valmaxReuse() { pooled = false; }
                function valmaxOpen() {
                    if (pooled || !eligible || !identityKey) return;
                    allChatsListView.valmaxNavigationGeneration += 1;
                    if (ready && item) { item.clicked(); return; }
                    valmaxPendingIdentity = identityKey;
                    valmaxPendingGeneration = allChatsListView.valmaxNavigationGeneration;
                    valmaxPendingOpen = true;
                }
                function valmaxCompletePendingOpen() {
                    if (!valmaxPendingOpen || !ready || !item) return;
                    const matches = !pooled && eligible && valmaxPendingIdentity === identityKey
                        && valmaxPendingGeneration === allChatsListView.valmaxNavigationGeneration;
                    valmaxPendingOpen = false;
                    valmaxPendingIdentity = "";
                    valmaxPendingGeneration = -1;
                    if (matches) item.clicked();
                }
                onClicked: valmaxOpen()
                onLoaded: Qt.callLater(valmaxCompletePendingOpen)
                Connections {
                    target: chatDelegate
                    function onIdentityKeyChanged() {
                        chatDelegate.valmaxClearInput();
                    }
                    function onEligibleChanged() {
                        if (!chatDelegate.eligible) {
                            chatDelegate.valmaxClearInput();
                        }
                    }
                }
                ListView.onPooled: {
                    chatDelegate.valmaxPool();
                    allChatsListView.delegateRemoved(chatDelegate.chatId);
                }
                ListView.onReused: chatDelegate.valmaxReuse()
                activeFocusOnTab: !ready || activeFocus
                Keys.forwardTo: ready && item && !valmaxSpacePressed ? [item] : []
                Keys.onReturnPressed: event => { chatDelegate.clicked(); event.accepted = true; }
                Keys.onEnterPressed: event => { chatDelegate.clicked(); event.accepted = true; }
                Keys.onPressed: event => {
                    event.accepted = false;
                    if (event.key === Qt.Key_Space && !chatDelegate.ready) {
                        if (!event.isAutoRepeat && chatDelegate.eligible && !chatDelegate.pooled) {
                            chatDelegate.valmaxSpacePressed = true;
                            chatDelegate.valmaxSpaceIdentity = chatDelegate.identityKey;
                        }
                        event.accepted = true;
                    }
                }
                Keys.onReleased: event => {
                    event.accepted = false;
                    if (event.key === Qt.Key_Space && chatDelegate.valmaxSpacePressed) {
                        const matches = !event.isAutoRepeat && chatDelegate.eligible && !chatDelegate.pooled
                            && chatDelegate.valmaxSpaceIdentity === chatDelegate.identityKey;
                        if (!event.isAutoRepeat) {
                            chatDelegate.valmaxSpacePressed = false;
                            chatDelegate.valmaxSpaceIdentity = "";
                            if (matches) chatDelegate.clicked();
                        }
                        event.accepted = true;
                    }
                }
                Accessible.onPressAction: chatDelegate.clicked()
                TapHandler {
                    enabled: chatDelegate.eligible && !chatDelegate.ready && !chatDelegate.pooled
                    acceptedButtons: Qt.LeftButton
                    acceptedModifiers: Qt.KeyboardModifierMask
                    onPressedChanged: if (pressed) chatDelegate.valmaxLeftPressIdentity = chatDelegate.identityKey
                    onTapped: {
                        const matches = chatDelegate.valmaxLeftPressIdentity === chatDelegate.identityKey;
                        chatDelegate.valmaxLeftPressIdentity = "";
                        if (matches) chatDelegate.clicked();
                    }
                }
                TapHandler {
                    enabled: chatDelegate.eligible && !chatDelegate.ready && !chatDelegate.pooled
                    acceptedButtons: Qt.RightButton
                    onPressedChanged: if (pressed) chatDelegate.valmaxRightPressIdentity = chatDelegate.identityKey
                    onTapped: {
                        const matches = chatDelegate.eligible && !chatDelegate.pooled
                            && chatDelegate.valmaxRightPressIdentity === chatDelegate.identityKey;
                        chatDelegate.valmaxRightPressIdentity = "";
                        if (matches) chatDelegate.rightMouseButtonClicked();
                    }
                }
                rowComponent: Component {
                    ChatDelegate {
                        id: valmaxReadyChatRow
                        width: chatDelegate.width
                        commonDependency: chatDelegate.commonDependency
                        currentChatId: chatDelegate.currentChatId
'''+forward+'''                        onCreateFolderForChat: chatId => chatDelegate.createFolderForChat(chatId)
                        onCloseChatRequested: chatId => chatDelegate.closeChatRequested(chatId)
                        onRightMouseButtonClicked: chatDelegate.rightMouseButtonClicked()
                        onPreviewClicked: chatDelegate.previewClicked()
                        Connections {
                            target: valmaxReadyChatRow
                            function onClicked() { allChatsListView.valmaxNavigationGeneration += 1; }
                        }
                    }
                }

'''+events

def transform(source):
    source = checked(source)
    old = delegate_block(source)
    result = source.replace(old,shell(old),1)
    if result.count('import QtQuick\n') != 1:
        raise ValueError('Chat async import anchor mismatch')
    result = result.replace('import QtQuick\n','import QtQuick\nimport "qrc:/valmax" as ValMAX\n',1)
    anchor = '            id: allChatsListView\n'
    if result.count(anchor) != 1:
        raise ValueError('Chat view lifecycle anchor mismatch')
    return result.replace(anchor, anchor+'''            property int valmaxNavigationGeneration: 0
            Connections {
                target: root
                function onCurrentChatIdChanged() { allChatsListView.valmaxNavigationGeneration += 1; }
            }
            onVisibleChanged: if (!visible) {
                valmaxNavigationGeneration += 1;
                cancelFlick();
            }
''', 1)

def transform_sources(sources):
    if type(sources) is not dict or 'ChatListView.qml' not in sources:
        raise ValueError('Pinned chat list required')
    result = dict(sources)
    result['ChatListView.qml'] = transform(result['ChatListView.qml'])
    return result

def reverse(source, original):
    """Accept only the exact pinned original and this adapter's golden output."""
    original = checked(original)
    if type(source) is not str or source.replace('\r\n', '\n') != transform(original):
        raise ValueError('Chat async shell output drift')
    return original
