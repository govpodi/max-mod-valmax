"""Synthetic selected-chat exports. Never accesses a real MAX profile."""
import copy
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import ExitStack

import chat_export_render as render
from chat_export_storage import ChatExportStorageError, ExportWorkspace, absolute_path, relative_path, hold_ancestors


CHAT = {'id':'-9007199254740993','title':'Синтетический чат','timezoneOffsetMinutes':180}
COMPLETE = {'historyComplete':True,'stopReason':'end_of_history','pagesFetched':3}


def attachment(identifier='1', **changes):
    value = {'id':identifier,'type':'image','name':'Пример.png','mime':'image/png','status':'ready','reason':None,'source':'media/asset_1.png'}
    value.update(changes); return value


def message(identifier='9007199254740993', **changes):
    value = {'id':identifier,'author':{'id':'123','name':'Синтетический автор'},'timestampMs':1700000000000,
             'text':'Синтетическое сообщение','outgoing':False,'reply':None,'attachments':[]}
    value.update(changes); return value


class Document(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.tags=[]; self.urls=[]; self.attrs=[]
    def handle_starttag(self,tag,attrs):
        self.tags.append(tag); self.attrs.append((tag,dict(attrs)))
        for key,value in attrs:
            if key in ('href','src'): self.urls.append(value)


@unittest.skipUnless(os.name == 'nt','Windows handle/path semantics')
class ExportTests(unittest.TestCase):
    def setUp(self):
        parent=Path('D:/ValMAX/analysis/chat-export-synthetic'); parent.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='case-',dir=parent); self.root=Path(self.tmp.name)
        self.destination=self.root/'Чат с пробелами'; self.spool=self.root/'spool'; (self.spool/'media').mkdir(parents=True)
    def tearDown(self): self.tmp.cleanup()
    def assert_clean(self):
        self.assertFalse(self.destination.exists()); self.assertEqual(list(self.root.glob('.valmax-export-*')),[])
    def run_export(self,rows,completion=None):
        return render.render_chat(self.destination,CHAT,iter(rows),completion or COMPLETE,spool_root=self.spool)
    def test_offline_escaped_text_reply_and_paths(self):
        hostile='<script src="https://invalid.example/secret"> & <img onerror="alert(1)">'
        row=message(text=hostile,author={'id':'5','name':hostile},reply={'id':'7','authorName':hostile,'text':hostile})
        result=self.run_export([row]); content=(self.destination/'index.html').read_text('utf8')
        parsed=Document(); parsed.feed(content)
        self.assertNotIn('script',parsed.tags); self.assertEqual(parsed.urls,[])
        self.assertIn('&lt;script',content); self.assertIn("connect-src 'none'",content)
        self.assertEqual(result['counts']['messages'],1); self.assertTrue(result['historyComplete'])
        self.assertIn('Клиент достиг начала доступной истории',content)
        manifest=json.loads((self.destination/'manifest.json').read_text('utf8'))
        self.assertEqual(manifest['chat']['id'],CHAT['id']); self.assertNotIn(hostile,json.dumps(manifest))
    def test_all_media_types_and_unsupported_report(self):
        media=[('image','image/png','png',b'\x89PNG\r\n\x1a\nsynthetic'),
               ('video','video/mp4','mp4',b'\x00\x00\x00\x18ftypisomsynthetic'),
               ('audio','audio/mpeg','mp3',b'ID3synthetic'),
               ('voice','audio/ogg','ogg',b'OggSsynthetic'),
               ('file','application/pdf','pdf',b'%PDF-synthetic')]
        attachments=[]
        for index,(kind,mime,ext,data) in enumerate(media):
            source='media/input'+str(index)+'.'+ext; (self.spool/source).write_bytes(data)
            attachments.append(attachment(str(index),type=kind,mime=mime,source=source,name='../CON.exe'))
        attachments.append(attachment('missing',type='unsupported',mime='',status='unavailable',source=None,reason='unsupported'))
        result=self.run_export([message(attachments=attachments)])
        parsed=Document(); parsed.feed((self.destination/'index.html').read_text('utf8'))
        self.assertIn('img',parsed.tags); self.assertIn('video',parsed.tags); self.assertEqual(parsed.tags.count('audio'),2)
        self.assertTrue(all(url.startswith('media/') and '..' not in url and ':' not in url for url in parsed.urls))
        self.assertEqual(result['counts'],{'messages':1,'attachments':6,'mediaReady':5,'mediaMissing':1,'unsupported':1,'bytes':sum(len(m[3]) for m in media)})
        metadata=[json.loads(line) for line in (self.destination/'assets.jsonl').read_text('utf8').splitlines()]
        self.assertEqual(len(metadata),5)
        for item in metadata: self.assertEqual(hashlib.sha256((self.destination/item['source']).read_bytes()).hexdigest(),item['sha256'])
        downloads=[attrs['download'] for tag,attrs in parsed.attrs if tag=='a']
        self.assertTrue(all(not name.endswith('.exe') and '/' not in name and '\\' not in name for name in downloads))
    def test_active_file_is_never_embedded(self):
        (self.spool/'media/active.bin').write_bytes(b'<svg onload="alert(1)"><script/></svg>')
        self.run_export([message(attachments=[attachment(mime='image/png',source='media/active.bin')])])
        parsed=Document(); parsed.feed((self.destination/'index.html').read_text('utf8'))
        self.assertNotIn('img',parsed.tags); self.assertNotIn('script',parsed.tags)
    def test_voice_empty_or_wrong_mime_uses_inert_local_magic(self):
        (self.spool/'media/voice.bin').write_bytes(b'\0\0\0\x18ftypisomsynthetic')
        self.run_export([message(attachments=[attachment(type='voice',mime='audio/ogg',source='media/voice.bin')])])
        parsed=Document(); parsed.feed((self.destination/'index.html').read_text('utf8'))
        audio=[attrs for tag,attrs in parsed.attrs if tag=='audio']
        self.assertEqual(len(audio),1); self.assertTrue(audio[0]['src'].startswith('media/'))
        self.assertNotIn('source',parsed.tags)
        metadata=json.loads((self.destination/'assets.jsonl').read_text('utf8'))
        self.assertEqual(metadata['mime'],'audio/ogg'); self.assertEqual(metadata['detectedMime'],'audio/mp4')
        self.assertTrue(next(attrs['download'] for tag,attrs in parsed.attrs if tag=='a').endswith('.m4a'))
    def test_partial_history_and_unavailable_asset_are_honest(self):
        completion={'historyComplete':False,'stopReason':'timeout','pagesFetched':2}
        result=self.run_export([message(timestampMs=None,attachments=[attachment()])],completion)
        self.assertFalse(result['historyComplete']); self.assertEqual(result['counts']['mediaMissing'],1)
        content=(self.destination/'index.html').read_text('utf8')
        self.assertIn('Полнота не подтверждена',content); self.assertIn('Дата не указана',content)
        missing=json.loads((self.destination/'omissions.jsonl').read_text('utf8'))
        self.assertEqual(missing['reason'],'source_unavailable')
    def test_existing_destination_not_overwritten(self):
        self.destination.mkdir(); (self.destination/'keep').write_text('sentinel')
        with self.assertRaises(ChatExportStorageError) as raised: self.run_export([])
        self.assertEqual(raised.exception.code,'exists'); self.assertEqual((self.destination/'keep').read_text(),'sentinel')
    def test_cancelled_and_iterator_failure_clean_own_staging(self):
        event=threading.Event()
        def rows():
            yield message(); event.set(); yield message('2')
        with self.assertRaises(ChatExportStorageError) as raised:
            render.render_chat(self.destination,CHAT,rows(),COMPLETE,spool_root=self.spool,cancel_event=event)
        self.assertEqual(raised.exception.code,'cancelled'); self.assert_clean(); self.assertTrue(self.spool.exists())
        def broken():
            yield message(); raise RuntimeError('synthetic adapter failure')
        with self.assertRaises(RuntimeError): self.run_export(broken())
        self.assert_clean()
    def test_duplicate_message_and_stream_limits_rollback(self):
        with self.assertRaises(render.ChatExportError): self.run_export([message(),message()])
        self.assert_clean()
        with patch.object(render,'MAX_MESSAGES',1):
            with self.assertRaises(render.ChatExportError) as raised: self.run_export([message('1'),message('2')])
        self.assertEqual(raised.exception.code,'limit_exceeded'); self.assert_clean()
    def test_offline_pagination_exact_boundaries_and_all_rows_once(self):
        for total,expected_pages in ((1000,1),(1001,2),(2003,3)):
            with self.subTest(total=total):
                self.destination=self.root/('paged-'+str(total))
                result=self.run_export(message(str(index),text='Синтетический-текст-'+str(index)+'-конец') for index in range(total))
                self.assertEqual(result['htmlPages'],expected_pages)
                self.assertEqual(result['counts']['messages'],total)
                self.assertEqual(len(list(self.destination.glob('*.html'))),expected_pages)
                found=[]; texts=[]
                for page in range(1,expected_pages+1):
                    name='index.html' if page==1 else f'messages-{page}.html'
                    content=(self.destination/name).read_text('utf8'); parsed=Document(); parsed.feed(content)
                    anchors=[attrs['id'] for tag,attrs in parsed.attrs if tag=='article']
                    self.assertEqual(len(anchors),min(1000,total-(page-1)*1000)); found.extend(anchors)
                    texts.extend(__import__('re').findall(r'Синтетический-текст-([0-9]+)-конец',content))
                    self.assertNotIn('script',parsed.tags)
                    links=[attrs for tag,attrs in parsed.attrs if tag=='a']
                    previous=[link['href'] for link in links if link.get('rel')=='prev']
                    following=[link['href'] for link in links if link.get('rel')=='next']
                    self.assertEqual(previous,[] if page==1 else ['index.html' if page==2 else f'messages-{page-1}.html'])
                    self.assertEqual(following,[] if page==expected_pages else [f'messages-{page+1}.html'])
                    for url in parsed.urls:
                        self.assertNotIn(':',url); self.assertNotIn('..',url); self.assertTrue((self.destination/url).is_file())
                self.assertEqual(len(set(found)),total)
                self.assertEqual(set(found),{'m'+hashlib.sha256(str(index).encode('ascii')).hexdigest() for index in range(total)})
                self.assertEqual(texts,[str(index) for index in range(total)])
                manifest=json.loads((self.destination/'manifest.json').read_text('utf8'))
                self.assertEqual(manifest['htmlPages'],expected_pages); self.assertEqual(manifest['counts'],result['counts'])
    def test_pagination_preserves_global_media_and_omissions(self):
        source=self.spool/'media/asset_1.png'; data=b'\x89PNG\r\n\x1a\nsynthetic'; source.write_bytes(data)
        def rows():
            for index in range(2003):
                items=[]
                if index in (999,1000): items=[attachment(str(index))]
                if index==2002: items=[attachment(str(index),status='unavailable',source=None,reason='not_available')]
                yield message(str(index),attachments=items)
        result=self.run_export(rows())
        self.assertEqual(result['counts'],{'messages':2003,'attachments':3,'mediaReady':2,'mediaMissing':1,'unsupported':0,'bytes':2*len(data)})
        assets=[json.loads(line) for line in (self.destination/'assets.jsonl').read_text('utf8').splitlines()]
        missing=[json.loads(line) for line in (self.destination/'omissions.jsonl').read_text('utf8').splitlines()]
        self.assertEqual([item['messageId'] for item in assets],['999','1000']); self.assertEqual([item['messageId'] for item in missing],['2002'])
        for page in range(1,4):
            name='index.html' if page==1 else f'messages-{page}.html'
            parsed=Document(); parsed.feed((self.destination/name).read_text('utf8'))
            for url in parsed.urls: self.assertTrue((self.destination/url).is_file())
    def test_pagination_lookahead_is_bounded_and_single_pass(self):
        with ExportWorkspace(self.destination) as workspace:
            consumed=0
            class Rows:
                def __init__(self): self.iterated=False
                def __iter__(self):
                    nonlocal consumed
                    if self.iterated: raise AssertionError('stream was restarted')
                    self.iterated=True
                    for index in range(2003): consumed+=1; yield message(str(index))
            def progress(counts): self.assertLessEqual(consumed,counts['messages']+1)
            result=render.render_into(workspace,CHAT,Rows(),COMPLETE,progress=progress,deduplicated=True)
            workspace.publish()
        self.assertEqual(consumed,2003); self.assertEqual(result['htmlPages'],3)
    def test_direct_workspace_reservation_hash_and_publish(self):
        with ExportWorkspace(self.destination) as workspace:
            path=workspace.reserve_asset('media/a1.png'); self.assertFalse(path.exists())
            data=b'\x89PNG\r\n\x1a\nsynthetic'; path.write_bytes(data)
            self.assertEqual(workspace.verify_asset('media/a1.png',max_bytes=100)['sha256'],hashlib.sha256(data).hexdigest())
            result=render.render_into(workspace,CHAT,[message(attachments=[attachment(source='media/a1.png')])],COMPLETE)
            workspace.publish()
        self.assertEqual(result['counts']['mediaReady'],1); self.assertTrue((self.destination/'media/a1.png').exists())
    def test_commit_with_external_parent_guards_held(self):
        with ExitStack() as held:
            hold_ancestors(held,self.root)
            self.run_export([message()])
        self.assertTrue((self.destination/'index.html').exists())
    def test_discard_failed_asset_and_refuse_other_paths(self):
        with ExportWorkspace(self.destination) as workspace:
            path=workspace.reserve_asset('media/failed.bin'); path.write_bytes(b'partial synthetic')
            workspace.discard_asset('media/failed.bin'); self.assertFalse(path.exists())
            workspace.discard_asset('media/not_created.bin')
            with self.assertRaises(ChatExportStorageError): workspace.discard_asset('index.html')
            with self.assertRaises(ChatExportStorageError): workspace.discard_asset('../other')
    def test_asset_chunks_exact_order_no_replace_and_hash(self):
        with ExportWorkspace(self.destination) as workspace:
            workspace.reserve_asset('media/voice.bin')
            self.assertEqual(workspace.write_asset_chunk('media/voice.bin',0,b'OggS',max_bytes=20),4)
            with self.assertRaises(ChatExportStorageError) as raised:
                workspace.write_asset_chunk('media/voice.bin',0,b'bad',max_bytes=20)
            self.assertEqual(raised.exception.code,'exists')
            with self.assertRaises(ChatExportStorageError) as raised:
                workspace.write_asset_chunk('media/voice.bin',3,b'bad',max_bytes=20)
            self.assertEqual(raised.exception.code,'source_changed')
            self.assertEqual(workspace.write_asset_chunk('media/voice.bin',4,b'synthetic',max_bytes=20),13)
            self.assertEqual((workspace.staging/'media/voice.bin').read_bytes(),b'OggSsynthetic')
            self.assertEqual(workspace.verify_asset('media/voice.bin',max_bytes=20)['bytes'],13)
    def test_asset_chunk_size_offset_path_and_hardlink_fail_closed(self):
        with ExportWorkspace(self.destination) as workspace:
            for offset,data,maximum in ((True,b'a',20),(-1,b'a',20),(0,b'',20),(0,b'a'*49153,100000),(0,b'a'*21,20),(0,b'a',64*1024**2+1)):
                with self.subTest(offset=offset,size=len(data),maximum=maximum),self.assertRaises(ChatExportStorageError):
                    workspace.write_asset_chunk('media/voice.bin',offset,data,max_bytes=maximum)
            with self.assertRaises(ChatExportStorageError): workspace.write_asset_chunk('../outside',0,b'a')
            with self.assertRaises(ChatExportStorageError): workspace.write_asset_chunk('index.html',0,b'a')
            original_hold=workspace._hold
            with patch.object(workspace,'_hold',side_effect=lambda path: None if path.name=='media' else original_hold(path)):
                file=workspace.reserve_asset('media/link.bin'); file.write_bytes(b'OggS'); os.link(file,workspace.staging/'media/other.bin')
            try:
                with self.assertRaises(ChatExportStorageError): workspace.write_asset_chunk('media/link.bin',4,b'a')
            finally: (workspace.staging/'media/other.bin').unlink()
            self.assertEqual(file.read_bytes(),b'OggS')
    def test_oversized_asset_is_omitted_and_removed_from_direct_output(self):
        with ExportWorkspace(self.destination) as workspace:
            workspace.reserve_asset('media/large.bin').write_bytes(b'0123456789')
            with patch.object(render,'MAX_ASSET_BYTES',5):
                result=render.render_into(workspace,CHAT,[message(attachments=[attachment(source='media/large.bin')])],COMPLETE)
            workspace.publish()
        self.assertEqual(result['counts']['mediaMissing'],1); self.assertFalse((self.destination/'media/large.bin').exists())
    def test_competing_destination_commit_does_not_replace(self):
        with self.assertRaises(ChatExportStorageError) as raised:
            with ExportWorkspace(self.destination) as workspace:
                with workspace.open_text('index.html') as stream: stream.write('synthetic')
                self.destination.mkdir(); workspace.publish()
        self.assertEqual(raised.exception.code,'exists'); self.assertTrue(self.destination.exists()); self.assertEqual(list(self.destination.iterdir()),[])
        self.assertEqual(list(self.root.glob('.valmax-export-*')),[])
    def test_copy_does_not_cleanup_success_inside_except(self):
        data=b'synthetic'; (self.spool/'media/asset.bin').write_bytes(data)
        with ExportWorkspace(self.destination) as workspace:
            try: raise RuntimeError('synthetic')
            except RuntimeError: workspace.copy_asset(self.spool,'media/asset.bin','media/copied.bin',max_bytes=100)
            self.assertEqual((workspace.staging/'media/copied.bin').read_bytes(),data)
    def test_hardlink_source_rejected_and_cancel_cleanup(self):
        source=self.spool/'media/a.bin'; source.write_bytes(b'synthetic'); os.link(source,self.spool/'media/b.bin')
        with self.assertRaises(ChatExportStorageError):
            with ExportWorkspace(self.destination) as workspace: workspace.copy_asset(self.spool,'media/a.bin','media/copied.bin',max_bytes=100)
        self.assert_clean()
    def test_constructor_hold_failure_cleans_owned_stage(self):
        import chat_export_storage as storage
        original=storage.ExportWorkspace._hold
        with patch.object(storage.ExportWorkspace,'_hold',side_effect=ValueError('synthetic')):
            with self.assertRaises(ChatExportStorageError): ExportWorkspace(self.destination)
        self.assert_clean()


class ValidationTests(unittest.TestCase):
    def test_original_safe_document_names_survive_unknown_mime(self):
        for suffix in ('doc','docx','xls','xlsx','ppt','pptx','txt','csv','zip','rar','7z','pdf','mp4','jpg','ogg'):
            with self.subTest(suffix=suffix):
                self.assertEqual(render._safe_download_name('Синтетический файл.'+suffix,'application/octet-stream'),'Синтетический файл.'+suffix)
        for suffix in ('html','svg','js','exe','cmd','ps1','lnk'):
            with self.subTest(suffix=suffix):
                self.assertEqual(render._safe_download_name('Синтетический файл.'+suffix,'application/pdf'),'Синтетический файл.bin')
        self.assertEqual(render._safe_download_name('Имя без расширения','text/plain'),'Имя без расширения.bin')
    def test_path_traversal_devices_and_streams_rejected(self):
        for path in ('.','../a','/a','a/../b','a\\b','a//b','NUL','a/COM1.bin','a:b','a/evil.html:stream','a/trailing.','a/name?'):
            with self.subTest(path=path),self.assertRaises(ChatExportStorageError): relative_path(path)
        self.assertEqual(relative_path('media/valid_file.bin'),Path('media/valid_file.bin'))
    def test_identifier_precision_and_pending_contract(self):
        value=message(attachments=[attachment(status='pending',source=None)])
        output=render.validate_message(value,allow_pending=True)
        self.assertEqual(output['id'],'9007199254740993')
        with self.assertRaises(render.ChatExportError): render.validate_message(value)
        value['id']=9007199254740993
        with self.assertRaises(render.ChatExportError): render.validate_message(value,allow_pending=True)
    def test_history_complete_requires_end_proof(self):
        for change in ({'stopReason':'timeout'},{'pagesFetched':True},{'pagesFetched':20001},{'stopReason':[]},{'extra':'token'}):
            value={**COMPLETE,**change}
            with self.subTest(change=change),self.assertRaises(render.ChatExportError): render.validate_completion(value)
    def test_no_arbitrary_fields_urls_or_active_source_extensions(self):
        for change in ({'source':'https://invalid.example/token'},{'source':'media/x.svg'},{'source':'../profile/secret'},{'status':[]},{'reason':[]},{'type':[]}):
            value=message(attachments=[attachment(**change)])
            with self.subTest(change=change),self.assertRaises(render.ChatExportError): render.validate_message(value)
        value=message(); value['authorization']='synthetic'
        with self.assertRaises(render.ChatExportError): render.validate_message(value)


if __name__=='__main__': unittest.main()
