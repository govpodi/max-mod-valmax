"""Actual JS scope functions; synthetic IDs only, no UI/user store."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest


class ChatLockScopeTests(unittest.TestCase):
    def test_saved_messages_is_not_an_invalid_or_other_protected_chat(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        source = Path(__file__).with_name('chat_lock_controller.qml').read_text(encoding='utf8')
        names = ('accountKey', 'chatKey', 'key', 'isProtected', 'isLocked', 'maskScope')
        functions = []
        for name in names:
            start = source.index('    function ' + name + '(')
            end = source.index('\n    function ', start + 1)
            functions.append(source[start:end])
        script = r'''
const assert = require('assert');
let revision=0, ready=true, clockMs=1000;
let catalog={'42:77':true}, denied={}, grantUntil={};
FUNCTIONS
assert.strictEqual(chatKey('0'),'0');
assert.strictEqual(key('42','0'),'42:0');
assert.strictEqual(isLocked('42','0'),false);
assert.strictEqual(maskScope('42','0'),false);
assert.strictEqual(isProtected('42','0'),false);
assert.strictEqual(isLocked('42','77'),true);
for (const chat of ['', '-0', '00', '01', 0, null, '9223372036854775808']) {
    assert.strictEqual(chatKey(chat),'');
    assert.strictEqual(isLocked('42',chat),true);
}
catalog['42:0']=true;
assert.strictEqual(isLocked('42','0'),true);
assert.strictEqual(isLocked('43','0'),false);
catalog['42:0']=false;grantUntil['42:0']=2000;
assert.strictEqual(isLocked('42','0'),false);
assert.strictEqual(isLocked('42','77'),true);
ready=false;
assert.strictEqual(isLocked('42','0'),true);
assert.strictEqual(maskScope('42','0'),true);
process.stdout.write(JSON.stringify({savedMessagesIndependent:true,unknownFailsClosed:true}));
'''.replace('FUNCTIONS', '\n'.join(functions))
        result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['savedMessagesIndependent'])


if __name__ == '__main__':
    unittest.main()
