"""Synthetic PE guards; no running MAX/profile or server is used."""
from pathlib import Path
import hashlib, struct, unittest
import ghost_unread_native as unread


class GhostUnreadSpecificationTests(unittest.TestCase):
    def image(self):
        data=bytearray(0x6400)
        data[:2]=b'MZ';struct.pack_into('<I',data,0x3c,0x80)
        data[0x80:0x84]=b'PE\0\0'
        struct.pack_into('<HH',data,0x84,0x8664,1)
        struct.pack_into('<H',data,0x94,0xf0)
        struct.pack_into('<H',data,0x98,0x20b)
        struct.pack_into('<I',data,0x98+56,0x5000000)
        section=0x98+0xf0;data[section:section+8]=b'.text\0\0\0'
        struct.pack_into('<IIII',data,section+8,0x6000,0x1ee9000,0x6000,0x400)
        struct.pack_into('<I',data,section+36,0x60000020)
        for guard in unread.PIN['guards']:
            expected=bytes.fromhex(guard['expected']);at=0x400+guard['rva']-0x1ee9000
            data[at:at+len(expected)]=expected
        return bytes(data)

    def test_exact_code_accepts_resource_variants_and_returns_fresh_full_identity(self):
        data=self.image()
        value=unread.specification_from_bytes(data,module_path=Path('D:/synthetic/max.exe'))
        self.assertEqual(value['moduleSha256'],hashlib.sha256(data).hexdigest())
        self.assertEqual(value['moduleSize'],len(data))
        self.assertEqual(value['imageSize'],0x5000000)
        value['selfInfo']['guards'][0]['expected']='bad'
        self.assertNotEqual(unread.PIN['selfInfo']['guards'][0]['expected'],'bad')
        changed=data+b'synthetic resource variant'
        self.assertNotEqual(unread.specification_from_bytes(changed,module_path=Path('D:/synthetic/max.exe'))['moduleSha256'],value['moduleSha256'])

    def test_wrong_machine_layout_mutable_data_and_any_guard_change_rejected(self):
        data=self.image()
        for value in (b'',bytearray(data),data[:50]):
            with self.subTest(kind=type(value)),self.assertRaises(ValueError):
                unread.specification_from_bytes(value,module_path=Path('D:/synthetic/max.exe'))
        for at in (0x84,0x98,*[0x400+g['rva']-0x1ee9000 for g in unread.PIN['guards']]):
            changed=bytearray(data);changed[at]^=0xff
            with self.subTest(at=at),self.assertRaises(ValueError):
                unread.specification_from_bytes(bytes(changed),module_path=Path('D:/synthetic/max.exe'))


if __name__=='__main__':
    unittest.main()
