"""Immutable code/layout guards; no MAX process or profile is opened."""
from pathlib import Path
import copy
import unittest
import pefile
import notification_native as native


ROOT=Path('D:/CodexLabs/max-mod-26.35.1/runtime/baseline')

@unittest.skipUnless((ROOT/'max.exe').is_file(),'Reviewed immutable baseline unavailable')
class NotificationNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images={name:(ROOT/name).read_bytes() for name in ('max.exe','notifications.dll')}
        cls.paths={name:ROOT/name for name in cls.images}

    def test_exact_baseline_and_independent_specs(self):
        result=native.specification_from_bytes(self.images,module_paths=self.paths)
        self.assertEqual(result['layout']['dataSize'],0x148)
        self.assertEqual(len(result['modules'][0]['pins']),11)
        self.assertEqual(len(result['modules'][1]['pins']),12)
        result['layout']['chatOffset']=99
        self.assertEqual(native.FIXED['layout']['chatOffset'],16)

    def test_every_reviewed_code_guard_rejects_a_changed_instruction(self):
        for item in native.FIXED['modules']:
            original=self.images[item['name']]
            pe=pefile.PE(data=original,fast_load=True)
            for pin in item['pins']:
                modified=bytearray(original);modified[pe.get_offset_from_rva(pin['rva'])]^=1
                with self.subTest(module=item['name'],role=pin['role']):
                    with self.assertRaises(ValueError):
                        native.specification_from_bytes({**self.images,item['name']:bytes(modified)},module_paths=self.paths)

    def test_missing_image_or_foreign_machine_is_rejected(self):
        with self.assertRaises(ValueError):
            native.specification_from_bytes({'max.exe':self.images['max.exe']},module_paths=self.paths)
        blob=bytearray(self.images['max.exe']);at=int.from_bytes(blob[0x3c:0x40],'little')
        blob[at+4:at+6]=(0xaa64).to_bytes(2,'little')
        with self.assertRaises(ValueError):
            native.specification_from_bytes({**self.images,'max.exe':bytes(blob)},module_paths=self.paths)

    def test_missing_generic_image_refuses_source(self):
        with self.assertRaises(ValueError):native.source_for(ROOT,{},generic_image_path=None)

    def test_bridge_metadata_is_exact_and_bounded(self):
        valid={'kind':'valmax-notification-bridge-ready','schema':1,'pointer':'0x70000000','size':65536,'magic':'56414c4e42523031'}
        self.assertIn('rpc.exports',native.updater_source_for(valid))
        for bad in ({**valid,'pointer':'0x0'},{**valid,'size':65537},{**valid,'schema':True},{**valid,'extra':1},{**valid,'magic':'0'*16}):
            with self.assertRaises(ValueError):native.updater_source_for(bad)


if __name__=='__main__':unittest.main()
