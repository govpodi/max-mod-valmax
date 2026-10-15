"""Real Qt binding regression: unchanged PIN polls must not invalidate lists.

Only production JS functions and synthetic identities are used. No UI toolkit,
HTTP endpoint, real profile, PIN store, credentials or MAX process is opened.
"""
from pathlib import Path
import json
import os
import unittest

OUT=Path('D:/ValMAX/analysis/chat-lock-wheel-v1')
OUT.mkdir(parents=True,exist_ok=True)
os.environ.setdefault('QML_DISABLE_DISK_CACHE','1')
from PySide6.QtCore import QCoreApplication,QObject,QUrl,Slot,qInstallMessageHandler
from PySide6.QtQml import QQmlComponent,QQmlEngine,QQmlExpression

SOURCE=Path(__file__).with_name('chat_lock_controller.qml')

def production_functions():
    text=SOURCE.read_text('utf8')
    names=('accountKey','chatKey','key','isProtected','isLocked','maskScope','deny','applyCatalog')
    out=[]
    for name in names:
        start=text.index('    function '+name+'(')
        end=text.index('\n    function ',start+1)
        out.append(text[start:end])
    return '\n'.join(out)

class Probe(QObject):
    def __init__(self):
        super().__init__();self.evaluations=0

    @Slot()
    def evaluated(self):
        self.evaluations+=1

class ChatLockCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.warnings=[]
        self.old_handler=qInstallMessageHandler(lambda kind,context,text:self.warnings.append(str(text)))
        self.probe=Probe();self.engine=QQmlEngine()
        self.engine.rootContext().setContextProperty('probe',self.probe)
        watchers=',\n'.join('QtObject { property bool masked: { probe.evaluated(); return root.maskScope("42","'+str(n)+'"); } }' for n in range(50,114))
        qml='''import QtQml
QtObject {
    id: root
    property bool ready: false
    property var catalog: ({})
    property var denied: ({})
    property var grantUntil: ({})
    property double clockMs: 1000
    property int revision: 0
    property var originalCatalog: null
    property bool selectedLocked: { probe.evaluated(); return isLocked("42","77"); }
    property bool favoritesLocked: { probe.evaluated(); return isLocked("42","0"); }
    property bool otherAccountLocked: { probe.evaluated(); return isLocked("43","77"); }
    property list<QtObject> watchers: [ WATCHERS ]
FUNCTIONS
}
'''.replace('WATCHERS',watchers).replace('FUNCTIONS',production_functions())
        self.component=QQmlComponent(self.engine)
        self.component.setData(qml.encode(),QUrl.fromLocalFile(str(OUT/'SyntheticCatalog.qml')))
        self.root=self.component.create()
        self.assertIsNotNone(self.root,'Synthetic real-Qt catalog component must compile')
        self.app.processEvents()

    def tearDown(self):
        if self.root is not None:self.root.deleteLater()
        self.engine.deleteLater();self.app.processEvents()
        qInstallMessageHandler(self.old_handler)
        self.assertEqual(self.warnings,[],'No synthetic QML warning is expected')

    def evaluate(self,code):
        expression=QQmlExpression(QQmlEngine.contextForObject(self.root),self.root,code)
        value=expression.evaluate()
        self.assertFalse(expression.hasError(),'Synthetic real-Qt expression failed')
        self.app.processEvents()
        return value[0] if isinstance(value,tuple) else value

    def apply(self,records):
        return self.evaluate('applyCatalog('+json.dumps({'schema':1,'ready':True,'protected':records})+')')

    def record(self,chat='77',locked=True,account='42'):
        return {'accountId':account,'chatId':chat,'locked':locked}

    def test_repeated_identical_polls_preserve_object_revision_and_67_bindings(self):
        self.assertTrue(self.apply([self.record(),self.record('0')]))
        self.evaluate('originalCatalog=catalog')
        revision=self.root.property('revision');before=self.probe.evaluations
        self.assertTrue(self.evaluate('''{
            let result=true;
            for(let n=0;n<50;n++) result=applyCatalog({schema:1,ready:true,protected:[
                {accountId:"42",chatId:"0",locked:true},{accountId:"42",chatId:"77",locked:true}]}) && result;
            result;
        }'''))
        self.assertEqual(self.root.property('revision'),revision)
        self.assertTrue(self.evaluate('catalog===originalCatalog'))
        self.assertEqual(self.probe.evaluations,before)

    def test_same_catalog_recovers_ready_and_actual_access_bindings(self):
        self.apply([self.record(locked=False)])
        self.evaluate('grantUntil=({"42:77":2000});originalCatalog=catalog')
        self.assertFalse(self.root.property('selectedLocked'))
        self.evaluate('ready=false')
        self.assertTrue(self.root.property('selectedLocked'))
        self.assertTrue(self.root.property('favoritesLocked'))
        revision=self.root.property('revision')
        self.assertTrue(self.apply([self.record(locked=False)]))
        self.assertTrue(self.root.property('ready'))
        self.assertFalse(self.root.property('selectedLocked'))
        self.assertFalse(self.root.property('favoritesLocked'))
        self.assertFalse(self.root.property('otherAccountLocked'))
        self.assertEqual(self.root.property('revision'),revision)
        self.assertTrue(self.evaluate('catalog===originalCatalog'))

    def test_real_changes_notify_and_preserve_local_grants_denials_and_expiry(self):
        self.apply([self.record(locked=False)])
        self.evaluate('grantUntil=({"42:77":2000})')
        self.assertFalse(self.root.property('selectedLocked'))
        revision=self.root.property('revision')
        self.apply([self.record(locked=False),self.record('0')])
        self.assertEqual(self.root.property('revision'),revision+1)
        self.assertTrue(self.root.property('favoritesLocked'))
        self.assertFalse(self.root.property('otherAccountLocked'))
        self.evaluate('clockMs=2000')
        self.assertTrue(self.root.property('selectedLocked'))
        self.evaluate('clockMs=1999')
        self.assertFalse(self.root.property('selectedLocked'))
        self.evaluate('deny("42","77")')
        self.assertTrue(self.root.property('selectedLocked'))
        self.apply([self.record(locked=False),self.record('0')])
        self.assertTrue(self.root.property('selectedLocked'))
        self.assertTrue(self.evaluate('denied["42:77"]===true && grantUntil["42:77"]===2000'))
        self.apply([self.record(locked=True),self.record('0')])
        self.assertTrue(self.root.property('selectedLocked'))
        self.apply([])
        self.assertFalse(self.root.property('selectedLocked'))
        self.assertFalse(self.root.property('favoritesLocked'))

    def test_validation_finishes_before_any_no_change_shortcut_or_mutation(self):
        self.apply([self.record()]);self.evaluate('originalCatalog=catalog')
        revision=self.root.property('revision');evaluations=self.probe.evaluations
        invalid=(
            [self.record(),self.record()],
            [self.record(),self.record('-0')],
            [{**self.record(),'locked':1}],
            [{**self.record(),'extra':False}],
            [self.record(account='0')],
        )
        for rows in invalid:
            self.assertFalse(self.apply(rows))
            self.assertEqual(self.root.property('revision'),revision)
            self.assertTrue(self.evaluate('catalog===originalCatalog'))
        self.assertEqual(self.probe.evaluations,evaluations)

    def test_security_timers_keep_original_intervals_and_conditions(self):
        text=SOURCE.read_text('utf8')
        self.assertIn('interval: 2000; repeat: true; running: root.hasLocks;',text)
        self.assertIn('interval: 250; repeat: true; running: root.hasLocks && Object.keys(root.grantUntil).length > 0;',text)
        self.assertIn('id: catalogDeadline; interval: 8000;',text)
        self.assertIn('id: mutationDeadline; interval: 10000;',text)

if __name__=='__main__':unittest.main()
