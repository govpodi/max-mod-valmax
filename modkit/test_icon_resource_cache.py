"""Real rcc + two-process Qt cache regression, using tiny synthetic icons/QML.

No immutable/native client, real profile or existing QML cache is accessed.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import icon_resources

RESOURCE = ':/valmax/ChatLockController.qml'
ICON = ':/icon_pack/synthetic-cache-fixture.svg'
OLD = b'import QtQml\nQtObject { property int revision: 1111; property bool allowsZero: false }\n'
NEW = b'import QtQml\nQtObject { property int revision: 2222; property bool allowsZero: true  }\n'
TINY_ICON = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><path d="M0 0h1v1H0z"/></svg>'
FIXTURES = Path('D:/ValMAX/analysis/test-temp/icon-resource-cache')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def cache_worker(bundle):
    from PySide6.QtCore import QCoreApplication, QFile, QFileInfo, QIODevice, QResource, QUrl, qVersion
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    app = QCoreApplication([])
    app.setOrganizationName('ValMAXSyntheticCacheTest')
    app.setApplicationName('StandaloneResourceCache')
    if not QResource.registerResource(str(bundle)):
        raise ValueError('Synthetic RCC rejected')
    source = QFile(RESOURCE)
    if not source.open(QIODevice.OpenModeFlag.ReadOnly):
        raise ValueError('Synthetic source missing')
    data = bytes(source.readAll())
    source.close()
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl('qrc' + RESOURCE))
    obj = component.create()
    if obj is None:
        raise ValueError('Synthetic QML rejected')
    result = {'qtVersion': qVersion(), 'sourceSha256': sha(data), 'sourceBytes': len(data),
              'qFileInfoTimestampMs': QFileInfo(RESOURCE).lastModified().toMSecsSinceEpoch(),
              'qResourceTimestampMs': QResource(RESOURCE).lastModified().toMSecsSinceEpoch(),
              'iconTimestampMs': QFileInfo(ICON).lastModified().toMSecsSinceEpoch(),
              'revision': obj.property('revision'), 'allowsZero': obj.property('allowsZero'),
              'forcedDiskCache': os.environ.get('QML_FORCE_DISK_CACHE') == '1'}
    obj.deleteLater()
    engine.deleteLater()
    app.sendPostedEvents()
    print(json.dumps(result))


@unittest.skipUnless(icon_resources.DEFAULT_RCC.is_file(), 'Official Qt rcc is unavailable')
class StandaloneResourceCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        # Keep tiny isolated fixtures for inspection; never delete user files.
        cls.root = Path(tempfile.mkdtemp(prefix='fixture-', dir=FIXTURES))
        cls.receipts = []

    def prepare(self, label, payload, *, legacy_date=False):
        output = self.root / label
        original_utime = os.utime
        def old_date(path, *args, **kwargs):
            stamp = icon_resources.FIXED_MTIME * 1000000000
            original_utime(path, ns=(stamp, stamp))
        with patch.object(icon_resources, 'native_payloads', return_value={ICON: TINY_ICON}), \
                patch.object(icon_resources, 'registration_anchors', return_value=[]), \
                patch.object(icon_resources, 'resource_payloads', return_value={}), \
                patch.dict(os.environ, {'SOURCE_DATE_EPOCH': str(icon_resources.FIXED_MTIME)}):
            if legacy_date:
                with patch.object(icon_resources.os, 'utime', side_effect=old_date):
                    bundle, _ = icon_resources.prepare(b'synthetic-only', output, extra_resources={RESOURCE: payload})
            else:
                bundle, _ = icon_resources.prepare(b'synthetic-only', output, extra_resources={RESOURCE: payload})
        return output / 'icon-pack-plus-valmax.rcc', bundle

    def run_worker(self, bundle, case, cache):
        local = self.root / case
        local.mkdir()
        cache.mkdir(exist_ok=True)
        env = dict(os.environ)
        for key in ('APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'TEMP', 'TMP', 'TMPDIR'):
            path = local / key
            path.mkdir()
            env[key] = str(path)
        env['QML_DISK_CACHE_PATH'] = str(cache)
        env['QML_FORCE_DISK_CACHE'] = '1'
        env['QML_DISK_CACHE'] = 'qmlc'
        env['QT_QPA_PLATFORM'] = 'offscreen'
        env.pop('QML_DISABLE_DISK_CACHE', None)
        process = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()),
                                  '--cache-worker', str(bundle)], env=env, cwd=local,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=30)
        self.assertEqual(0, process.returncode, 'Synthetic Qt worker failed')
        self.assertEqual(b'', process.stderr, 'Unexpected synthetic Qt diagnostics')
        value = json.loads(process.stdout.decode('utf8'))
        value['cacheFiles'] = {path.relative_to(cache).as_posix(): sha(path.read_bytes())
                               for path in cache.rglob('*') if path.is_file()}
        self.assertTrue(value['forcedDiskCache'])
        self.assertTrue(value['cacheFiles'], 'Test must exercise an actual written disk cache')
        return value

    def test_changed_source_invalidates_real_shared_qml_cache(self):
        self.assertEqual(len(OLD), len(NEW), 'Source-size differences must not explain invalidation')
        old_path, old_bundle = self.prepare('fixed-old', OLD)
        new_path, new_bundle = self.prepare('fixed-new', NEW)
        _, repeat_bundle = self.prepare('fixed-new-repeated', NEW)
        self.assertEqual(new_bundle, repeat_bundle, 'Per-content dates must preserve reproducible RCC bytes')
        self.assertNotEqual(old_bundle, new_bundle)
        cache = self.root / 'fixed-shared-cache'
        first = self.run_worker(old_path, 'fixed-process-1', cache)
        second = self.run_worker(new_path, 'fixed-process-2', cache)
        self.assertEqual(1111, first['revision'])
        self.assertEqual(2222, second['revision'], 'Changed standalone source must not execute stale cached QML')
        self.assertFalse(first['allowsZero'])
        self.assertTrue(second['allowsZero'])
        for payload, observed in ((OLD, first), (NEW, second)):
            wanted = 1767225600000 + int.from_bytes(hashlib.sha256(payload).digest()[:8], 'big') % 31536000000
            self.assertEqual(sha(payload), observed['sourceSha256'])
            self.assertEqual(wanted, observed['qFileInfoTimestampMs'])
            self.assertEqual(wanted, observed['qResourceTimestampMs'])
            self.assertEqual(icon_resources.FIXED_MTIME * 1000, observed['iconTimestampMs'])
        self.assertNotEqual(first['cacheFiles'], second['cacheFiles'])
        self.receipts.append({'case': 'content-derived-mtime', 'first': first, 'second': second, 'repeatBundleIdentical': True})

    def test_legacy_fixed_dates_reproduce_stale_cache(self):
        old_path, _ = self.prepare('legacy-old', OLD, legacy_date=True)
        new_path, _ = self.prepare('legacy-new', NEW, legacy_date=True)
        cache = self.root / 'legacy-shared-cache'
        first = self.run_worker(old_path, 'legacy-process-1', cache)
        second = self.run_worker(new_path, 'legacy-process-2', cache)
        self.assertEqual(sha(NEW), second['sourceSha256'], 'QFile must see the new resource source')
        self.assertNotEqual(first['sourceSha256'], second['sourceSha256'])
        self.assertEqual(1111, second['revision'], 'Negative control must really execute stale cached QML')
        self.assertFalse(second['allowsZero'])
        self.assertEqual(icon_resources.FIXED_MTIME * 1000, second['qResourceTimestampMs'])
        self.assertEqual(first['cacheFiles'], second['cacheFiles'])
        self.receipts.append({'case': 'legacy-fixed-date-negative-control', 'first': first, 'second': second})

    @classmethod
    def tearDownClass(cls):
        (cls.root / 'proof.json').write_text(json.dumps({'scope': 'Synthetic prepare/rcc/Qt cache only; no MAX/profile',
                                                       'cases': cls.receipts}, indent=2) + '\n', encoding='utf8')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--cache-worker':
        cache_worker(Path(sys.argv[2]))
    else:
        unittest.main(verbosity=2)
