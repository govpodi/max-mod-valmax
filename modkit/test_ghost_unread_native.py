"""Synthetic PE guards; no running MAX/profile or server is used."""
from pathlib import Path
import hashlib, struct, unittest
import ghost_unread_native as unread


class GhostUnreadSpecificationTests(unittest.TestCase):
    SECTIONS = ((0x1ee9000,0x400,0x6000),(0x6d000,0x6400,0x1000),
                (0x91000,0x7400,0x1000),(0x3eef000,0x8400,0x2000))

    def raw_at(self,rva):
        for address,offset,size in self.SECTIONS:
            if address<=rva<address+size:return offset+rva-address
        raise AssertionError('unmapped synthetic RVA')

    def image(self):
        data=bytearray(0xa400)
        data[:2]=b'MZ';struct.pack_into('<I',data,0x3c,0x80)
        data[0x80:0x84]=b'PE\0\0'
        struct.pack_into('<HH',data,0x84,0x8664,len(self.SECTIONS))
        struct.pack_into('<H',data,0x94,0xf0)
        struct.pack_into('<H',data,0x98,0x20b)
        struct.pack_into('<Q',data,0x98+24,0x140000000)
        struct.pack_into('<I',data,0x98+56,0x5000000)
        for index,(address,offset,size) in enumerate(self.SECTIONS):
            section=0x98+0xf0+index*40;data[section:section+8]=b'.text\0\0\0'
            struct.pack_into('<IIII',data,section+8,size,address,size,offset)
            struct.pack_into('<I',data,section+36,0x60000020)
        for guard in unread.PIN['guards']:
            expected=bytes.fromhex(guard['expected']);at=self.raw_at(guard['rva'])
            data[at:at+len(expected)]=expected
        # Independently transcribed actual PE virtual entries: slots point to
        # jump thunks, not the function bodies previously assumed by the VM.
        for slot,rva,code in ((0,0x6db01,'e9dabce701'),(8,0x916a0,'e99bbce501')):
            at=self.raw_at(rva);data[at:at+5]=bytes.fromhex(code)
            struct.pack_into('<Q',data,self.raw_at(unread.PIN['vtableRva']+slot),0x140000000+rva)
        return bytes(data)

    def test_actual_virtual_entries_are_pinned_jump_thunks_to_guarded_bodies(self):
        self.assertEqual(unread.PIN['destructorThunkRva'],0x6db01)
        self.assertEqual(unread.PIN['readThunkRva'],0x916a0)
        data=self.image()
        for rva,target in ((0x6db01,unread.PIN['destructorRva']),(0x916a0,unread.PIN['readRva'])):
            at=self.raw_at(rva)
            self.assertEqual(data[at],0xe9)
            self.assertEqual(rva+5+struct.unpack_from('<i',data,at+1)[0],target)
            self.assertIn({'rva':rva,'expected':data[at:at+5].hex()},unread.PIN['guards'])
        unread.specification_from_bytes(data,module_path=Path('D:/synthetic/max.exe'))

    def test_direct_body_or_wrong_virtual_entries_and_modified_thunks_rejected(self):
        original=self.image()
        for slot,target in ((0,unread.PIN['destructorRva']),(8,unread.PIN['readRva']),(0,0x916a0),(8,0x6db01)):
            data=bytearray(original)
            struct.pack_into('<Q',data,self.raw_at(unread.PIN['vtableRva']+slot),0x140000000+target)
            with self.subTest(slot=slot,target=target),self.assertRaises(ValueError):
                unread.specification_from_bytes(bytes(data),module_path=Path('D:/synthetic/max.exe'))
        for rva in (0x6db01,0x916a0):
            data=bytearray(original);data[self.raw_at(rva)+1]^=1
            with self.subTest(rva=rva),self.assertRaises(ValueError):
                unread.specification_from_bytes(bytes(data),module_path=Path('D:/synthetic/max.exe'))

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
        for at in (0x84,0x98,*[self.raw_at(g['rva']) for g in unread.PIN['guards']]):
            changed=bytearray(data);changed[at]^=0xff
            with self.subTest(at=at),self.assertRaises(ValueError):
                unread.specification_from_bytes(bytes(changed),module_path=Path('D:/synthetic/max.exe'))


if __name__=='__main__':
    unittest.main()
