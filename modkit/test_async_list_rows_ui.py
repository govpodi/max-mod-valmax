"""Pinned list/header adapters; immutable native fixtures, no client data."""
from pathlib import Path
import unittest
import async_list_rows_ui as rows
import hidden_pages_ui as hidden
NATIVE=Path('D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi')

class AsyncRowsTests(unittest.TestCase):
    def native(self,name):
        if not NATIVE.is_dir(): self.skipTest('Immutable native QML unavailable')
        return (NATIVE/name).read_text('utf8')
    def test_pinned_rows_roundtrip_and_fail_closed(self):
        for name,forward,reverse in [('ContactListView.qml',rows.transform_contacts,rows.reverse_contacts),('CallsListView.qml',rows.transform_calls,rows.reverse_calls)]:
            original=self.native(name); result=forward(original)
            self.assertEqual(reverse(result),original)
            self.assertEqual(forward(original.replace('\n','\r\n')),result)
            for bad in (None,'',original+'\n',result):
                with self.assertRaises(ValueError):forward(bad)
            with self.assertRaises(ValueError):reverse(result+'\n')
    def test_native_roles_selection_actions_and_heavy_children_preserved(self):
        call=rows.transform_calls(self.native('CallsListView.qml'))
        child=self.native('CallsHistoryDelegate.qml')
        import re
        for role in re.findall(r'^    required property \w+ (\w+)$',child,re.M):
            owner='root' if role in ('commonDependency','openedChatsContext','callsManagerContext','callVestibuleContext','context') else 'valmaxCallShell'
            self.assertEqual(call.count(role+': '+owner+'.'+role+'\n'),1,role)
        self.assertIn('model: valmaxCallShell.model',call)
        self.assertIn('root.currentIndex = index;',call)
        self.assertIn('root.callLinkContext.displayCallLink(delegate.joinLink, delegate.chatId);',call)
        self.assertIn('root.openedChatsContext.openChatOnMessage(delegate.chatId, delegate.messageTimeId);',call)
        self.assertIn('rowVisible: !isRemoved',call)
        contact=rows.transform_contacts(self.native('ContactListView.qml'))
        self.assertIn('nickname: valmaxContactShell.nickname',contact)
        self.assertIn('Component.onDestruction: listView.delegateRemoved(contactDelegate.contactId)',contact)
        data={name:self.native(name)for name in ('ContactListView.qml','CallsListView.qml','ContactListDelegate.qml','CallsHistoryDelegate.qml')}
        result=rows.transform_sources(data)
        for name in ('ContactListDelegate.qml','CallsHistoryDelegate.qml'):self.assertEqual(result[name],data[name])
        with self.assertRaises(ValueError):rows.transform_sources({})
    def test_independent_async_cancel_and_static_skeleton(self):
        source=rows.row_source()
        self.assertIn('asynchronous: true',source)
        self.assertIn('active: root.valmaxLoadPermitted && (root.loadEligible || root.valmaxRetainReady)',source)
        self.assertIn('readonly property bool loadEligible:', source)
        self.assertIn('onLoadEligibleChanged:', source)
        self.assertNotRegex(source,r'active:.*(?:ready|status)')
        self.assertIn('onIdentityKeyChanged:',source)
        self.assertIn('onEligibleChanged:',source)
        self.assertIn('Window.Minimized',source)
        for heavy in ('ShaderEffect','NumberAnimation','GaussianBlur'):self.assertNotIn(heavy,source)
    def test_header_roundtrip_visibility_and_drift(self):
        for name,forward in [('BaseLeadingListHeader.qml',hidden.transform_header),('LeadingChatsBar.qml',hidden.transform_chats_bar)]:
            original=self.native(name);result=forward(original)
            self.assertEqual(hidden.reverse(result,original,name),original)
            self.assertEqual(forward(original.replace('\n','\r\n')),result)
            for bad in (None,'',original+'\n',result):
                with self.assertRaises(ValueError):forward(bad)
            with self.assertRaises(ValueError):hidden.reverse(result+'\n',original,name)
        result=hidden.transform_header(self.native('BaseLeadingListHeader.qml'))
        self.assertIn('return root.valmaxHeaderActive && root.state',result)
        result=hidden.transform_chats_bar(self.native('LeadingChatsBar.qml'))
        self.assertIn('enabled: root.valmaxChatsPageActive',result)
        self.assertIn('switchToChatListTimer.stop();',result)
        self.assertIn('showcaseStartTimer.stop();',result)
        with self.assertRaises(ValueError):hidden.transform_sources({})
if __name__=='__main__':unittest.main(verbosity=2)
