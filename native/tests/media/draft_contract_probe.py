"""Bounded read-only MAX MOC/code evidence; never starts MAX or invokes a method."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import struct

import capstone
import pefile

ROOT = Path('D:/ValMAX')
BASELINE = Path('D:/CodexLabs/max-mod-26.35.1/runtime/baseline/max.exe')
FIXTURE = Path('C:/ValMAXBuild/build/max-engine-probes/probe-2335f7ff38f4437d8a23497fc19f704d/runtime/max.exe')
QML = Path('D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi')
PIN = 'd79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182'
checks = 0


def require(value, name):
    global checks
    checks += 1
    if not value:
        raise RuntimeError(name)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Image:
    def __init__(self, path):
        self.data = path.read_bytes()
        self.pe = pefile.PE(data=self.data, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase

    def raw(self, rva, count):
        return self.pe.get_data(rva, count)

    def u32(self, rva):
        return struct.unpack('<I', self.raw(rva, 4))[0]

    def pointer(self, rva):
        return struct.unpack('<Q', self.raw(rva, 8))[0] - self.base

    def meta(self, rva):
        strings_rva, data_rva = self.pointer(rva + 8), self.pointer(rva + 16)
        count = self.u32(strings_rva) // 8
        require(1 <= count <= 128, 'bounded_strings')
        strings = []
        for n in range(count):
            offset, length = struct.unpack('<II', self.raw(strings_rva + n * 8, 8))
            require(offset < 16384 and length < 4096, 'bounded_string')
            value = self.raw(strings_rva + offset, length + 1)
            require(len(value) == length + 1 and value[-1] == 0, 'terminated_string')
            strings.append(value[:-1].decode('utf8'))
        header = struct.unpack('<14I', self.raw(data_rva, 56))
        require(header[0] == 13 and header[1] == 0, 'qt_moc_revision13')

        def type_name(value):
            return strings[value & 0x7fffffff] if value & 0x80000000 else {
                1: 'bool', 2: 'int', 10: 'QString', 16: 'QDateTime', 43: 'void'}.get(value, f'QtMetaType({value})')

        methods = []
        for n in range(header[4]):
            entry = struct.unpack('<6I', self.raw(data_rva + 4 * (header[5] + n * 6), 24))
            require(entry[1] <= 8, 'bounded_method_arity')
            params = struct.unpack('<' + 'I' * (entry[1] + 1), self.raw(data_rva + entry[2] * 4, (entry[1] + 1) * 4))
            methods.append(dict(index=n, name=strings[entry[0]], returnType=type_name(params[0]),
                                parameterTypes=[type_name(t) for t in params[1:]], signal=bool(entry[4] & 4)))
        properties = []
        for n in range(header[6]):
            entry = struct.unpack('<5I', self.raw(data_rva + 4 * (header[7] + n * 5), 20))
            properties.append(dict(name=strings[entry[0]], type=type_name(entry[1]), flags=hex(entry[2]),
                                   bindable=bool(entry[2] & 0x02000000), notifyIndex=None if entry[3] == 0xffffffff else entry[3]))
        return dict(className=strings[0], metaObjectRva=hex(rva), stringsRva=hex(strings_rva),
                    dataRva=hex(data_rva), staticMetacallRva=hex(self.pointer(rva + 24)), methods=methods, properties=properties)


def main():
    image, fixture = Image(BASELINE), Image(FIXTURE)
    require(sha(image.data) == PIN, 'baseline_pin')
    metas = [image.meta(r) for r in (0x3702048, 0x3709c78, 0x3702548)]
    view, model, sender = metas
    require(view['className'] == 'Ui::History::AttachmentUpload::View', 'view_class')
    require(model['className'] == 'Ui::History::AttachmentUpload::UploadModel', 'model_class')
    require(sender['className'] == 'Ui::History::MessageSender::View', 'sender_class')
    method = next(m for m in view['methods'] if m['name'] == 'uploadPhotoOrVideo')
    require(method['returnType'] == 'void' and method['parameterTypes'] == ['QList<QUrl>'], 'upload_signature')
    require([m['name'] for m in view['methods'] if m['signal']] == ['mediaAttachLimitOverflow', 'tooLargeUploadFile', 'cleared'], 'no_view_acceptance_signal')
    require(model['methods'] == [], 'model_no_own_moc_methods')
    require(all(p['bindable'] and p['notifyIndex'] is None for p in model['properties']), 'model_properties_bindable_not_notify')
    require([p['name'] for p in model['properties']] == ['attachmentsCount', 'hasFile'], 'model_properties')
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    spans = {'viewMetacall': (0xa7b130, 0x1bc), 'uploadPhotoOrVideo': (0xd99e30, 13),
             'uploadCommon': (0xd98e80, 0x196), 'modelData': (0xd95460, 0x32c),
             'roleNames': (0xd96640, 0x76f)}
    code = {}
    for name, (rva, count) in spans.items():
        data = image.raw(rva, count)
        require(data == fixture.raw(rva, count), 'fixture_code_matches_' + name)
        code[name] = dict(rva=hex(rva), size=count, sha256=sha(data),
                         instructions=[f'{i.address:08x} {i.mnemonic} {i.op_str}' for i in decoder.disasm(data, rva)])
    expected_roles = {'attachmentType': 0x100, 'uploadState': 0x101, 'uploadProgress': 0x102,
                      'networkPreviewUrl': 0x104, 'localFilePreview': 0x105,
                      'chatId': 0x106, 'messageId': 0x107, 'postId': 0x108, 'attachmentId': 0x109}
    instructions = list(decoder.disasm(image.raw(0xd96640, 0x76f), 0xd96640))
    roles = {}
    for n, ins in enumerate(instructions):
        if ins.mnemonic != 'lea' or len(ins.operands) < 2 or ins.operands[1].type != capstone.CS_OP_MEM or ins.operands[1].mem.base != capstone.x86.X86_REG_RIP:
            continue
        rva = ins.address + ins.size + ins.operands[1].mem.disp
        try:
            name = image.raw(rva, 80).split(b'\0')[0].decode('ascii')
        except (ValueError, UnicodeDecodeError, pefile.PEFormatError):
            continue
        if name not in expected_roles:
            continue
        following = next(i for i in instructions[n + 1:] if i.mnemonic == 'mov' and len(i.operands) == 2
                         and i.operands[0].type == capstone.CS_OP_MEM and i.operands[1].type == capstone.CS_OP_IMM
                         and 0x100 <= i.operands[1].imm <= 0x10a)
        roles[name] = dict(role=following.operands[1].imm, nameReferenceRva=hex(ins.address), roleAssignmentRva=hex(following.address))
    require({name: value['role'] for name, value in roles.items()} == expected_roles, 'role_initializer_pairs')
    require(image.u32(0xd95764 + 5 * 4) == 0xd955c5, 'local_preview_data_branch')
    require(image.raw(0xd955c5, 7).hex() == '4983c0084c03c2', 'local_preview_row_plus8')
    require(image.u32(0x4563910 + 12) == 10, 'local_preview_qstring_metatype')
    require('cmp qword ptr [rcx + 0x18], 0' in '\n'.join(code['uploadCommon']['instructions']), 'unconfigured_presenter_guard')
    qml_sources = {}
    for name in ('BaseMessageSendPanel.qml', 'BaseSendPanel.qml', 'SendAttachmentView.qml'):
        qml_sources[name] = sha((QML / name).read_bytes())
    message = (QML / 'BaseMessageSendPanel.qml').read_text(encoding='utf8')
    require(message.count('onPhotoOrVideoUploadSelected: fileList => viewContext.uploadView.uploadPhotoOrVideo(fileList)') == 1, 'stock_upload_anchor')
    require(message.count('attachmentUploadModel: viewContext.uploadView.uploadModel') == 1, 'stock_model_anchor')
    require(message.count('onRemoveAttachClicked: index => viewContext.uploadView.removeRow(index)') == 1, 'stock_remove_anchor')
    require('required property string localFilePreview' in (QML / 'SendAttachmentView.qml').read_text(encoding='utf8'), 'stock_preview_role_type')
    proof = dict(schema=1, staticEvidenceVerified=True, checks=checks, baselineSha256=PIN,
                 fixtureSha256=sha(fixture.data), baselinePath=str(BASELINE), fixturePath=str(FIXTURE),
                 metadata=metas, roleEvidence=roles, codeEvidence=code, qmlSourceHashes=qml_sources,
                 sourceSha256=sha(Path(__file__).read_bytes()), actualMaxStarted=False,
                 anyUploadOrSendMethodInvoked=False, realProfilesUsed=False, syntheticDraftAckProven=False,
                 originalPathEqualsLocalFilePreviewProven=False, fileCopyOrOwnershipTransferProven=False,
                 conclusion='Usable public contract is a void draft-upload request only. Per-file acceptance and ownership transfer are not proved.',
                 limits=['Only the named three MOC tables and five bounded code spans were decoded.',
                         'The modelData branch copies a QString at row offset 8; its source/producer was not inferred.',
                         'No initialized account-scoped presenter was created. An unconfigured View can return without accepting a file.',
                         'Role or row count changes are not treated as proof that the selected original was accepted.'])
    output = ROOT / 'analysis/native-migration-v1/media-draft-static-proof.json'
    output.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps(dict(staticEvidenceVerified=True, checks=checks, syntheticDraftAckProven=False, proof=str(output))))


if __name__ == '__main__':
    main()
