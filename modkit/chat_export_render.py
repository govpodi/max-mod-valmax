"""Bounded offline chat renderer. Input is an explicitly selected native DTO.

No client/API/credential access happens here. The coordinator owns pagination,
account checks, native downloads and the destination selection. Rows must be in
their desired display order; this renderer does not invent unavailable history.
"""
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import json
from pathlib import PurePosixPath
import re

from chat_export_storage import ChatExportStorageError, ExportWorkspace, relative_path

MAX_MESSAGES = 1_000_000
MAX_ATTACHMENTS = 1_000_000
MESSAGES_PER_PAGE = 1000
MAX_TEXT = 1_048_576
MAX_ASSET_BYTES = 16 * 1024**3
ATTACHMENT_TYPES = frozenset({'image', 'video', 'audio', 'voice', 'file', 'unsupported'})
REASONS = frozenset({'not_available', 'permission_denied', 'download_failed', 'timeout',
                     'unsupported', 'cancelled', 'not_downloaded', 'source_unavailable',
                     'asset_too_large', 'source_changed'})
STOP_REASONS = frozenset({'end_of_history', 'cancelled', 'timeout', 'history_error', 'no_progress',
                          'page_limit', 'message_limit', 'attachment_limit', 'message_too_large', 'unsupported_history'})
SAFE_SOURCE = re.compile(r'media/[A-Za-z0-9_-]{1,96}\.(?:jpg|jpeg|png|webp|gif|mp4|webm|ogg|opus|mp3|wav|m4a|flac|pdf|bin)\Z')
MIME_EXT = {'image/jpeg':'jpg', 'image/png':'png', 'image/gif':'gif', 'image/webp':'webp',
            'video/mp4':'mp4', 'video/webm':'webm', 'video/ogg':'ogg', 'audio/ogg':'ogg',
            'audio/opus':'opus', 'audio/mpeg':'mp3', 'audio/wav':'wav', 'audio/x-wav':'wav',
            'audio/mp4':'m4a', 'audio/webm':'webm', 'audio/flac':'flac', 'application/pdf':'pdf'}
DOWNLOAD_EXTENSIONS = frozenset({'doc','docx','xls','xlsx','ppt','pptx','txt','csv','zip','rar','7z','pdf',
    'jpg','jpeg','png','webp','gif','mp4','m4v','mov','webm','mkv','avi','mpeg','mpg','3gp',
    'ogg','oga','opus','mp3','wav','m4a','flac','aac','wma'})
ACTIVE_EXTENSIONS = frozenset({'html','htm','xhtml','svg','svgz','js','mjs','cjs','vbs','vbe','wsf','wsh',
    'ps1','psm1','bat','cmd','com','exe','dll','scr','msi','msix','appx','hta','lnk','url','reg','jar'})
DETECTED_EXTENSIONS = {'image/jpeg':{'jpg','jpeg'},'image/png':{'png'},'image/gif':{'gif'},'image/webp':{'webp'},
    'video/mp4':{'mp4','m4v','mov','3gp'},'audio/mp4':{'m4a','mp4'},'video/webm':{'webm'},'audio/webm':{'webm'},
    'video/ogg':{'ogg'},'audio/ogg':{'ogg','oga','opus'},'audio/mpeg':{'mp3'},'audio/wav':{'wav'},'audio/flac':{'flac'}}
REASON_TEXT = {'not_available':'Недоступно в клиенте', 'permission_denied':'Нет доступа',
               'download_failed':'Не удалось скачать', 'timeout':'Истекло время загрузки',
               'unsupported':'Этот тип не поддерживается', 'cancelled':'Загрузка отменена',
               'not_downloaded':'Не загружено', 'source_unavailable':'Локальный файл недоступен',
               'asset_too_large':'Файл превышает предел размера', 'source_changed':'Файл изменился при чтении'}
TYPE_TEXT = {'image':'Изображение', 'video':'Видео', 'voice':'Голосовое сообщение',
             'audio':'Аудиозапись', 'file':'Файл', 'unsupported':'Неподдерживаемое вложение'}


class ChatExportError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _string(value, maximum, code, *, empty=True):
    if type(value) is not str or len(value) > maximum or (not empty and not value):
        raise ChatExportError(code)
    # Invalid UTF-16 surrogate pairs cannot safely be emitted as UTF-8 text.
    if any(0xD800 <= ord(char) <= 0xDFFF or ord(char) == 0 for char in value):
        raise ChatExportError(code)
    return value


def _identifier(value, code):
    value = _string(value, 128, code, empty=False)
    if any(ord(char) < 33 or ord(char) > 126 for char in value): raise ChatExportError(code)
    return value


def _keys(value, required, optional, code):
    if type(value) is not dict or not required.issubset(value) or set(value) - required - optional:
        raise ChatExportError(code)


def validate_chat(value):
    code = 'invalid_chat'
    _keys(value, {'id', 'title', 'timezoneOffsetMinutes'}, set(), code)
    offset = value['timezoneOffsetMinutes']
    if type(offset) is not int or not -840 <= offset <= 840: raise ChatExportError(code)
    return {'id':_identifier(value['id'], code), 'title':_string(value['title'], 4096, code), 'timezoneOffsetMinutes':offset}


def validate_message(value, *, allow_pending=False):
    code = 'invalid_message'
    _keys(value, {'id','author','timestampMs','text','outgoing','reply','attachments'}, {'kind'}, code)
    _keys(value['author'], {'id','name'}, set(), code)
    stamp = value['timestampMs']
    if stamp is not None and (type(stamp) is not int or not -62135546400000 <= stamp <= 253402250399999):
        raise ChatExportError(code)
    if type(value['outgoing']) is not bool: raise ChatExportError(code)
    if type(value['attachments']) is not list or len(value['attachments']) > 1000: raise ChatExportError(code)
    kind = value.get('kind', 'message')
    if type(kind) is not str or kind not in ('message','service','unsupported'): raise ChatExportError(code)
    reply = value['reply']
    if reply is not None:
        _keys(reply, {'id','authorName','text'}, set(), code)
        reply = {'id':_identifier(reply['id'],code), 'authorName':_string(reply['authorName'],1024,code), 'text':_string(reply['text'],MAX_TEXT,code)}
    attachments = []
    seen = set()
    for item in value['attachments']:
        _keys(item, {'id','type','name','mime','status','reason'}, {'source','bytes','sha256'}, code)
        identifier = _identifier(item['id'],code)
        if identifier in seen: raise ChatExportError(code)
        seen.add(identifier)
        if type(item['type']) is not str or item['type'] not in ATTACHMENT_TYPES: raise ChatExportError(code)
        states = ('ready','unavailable','cancelled','error','pending') if allow_pending else ('ready','unavailable','cancelled','error')
        if type(item['status']) is not str or item['status'] not in states or (item['reason'] is not None and (type(item['reason']) is not str or item['reason'] not in REASONS)):
            raise ChatExportError(code)
        source = item.get('source')
        if item['status'] == 'ready':
            if type(source) is not str or SAFE_SOURCE.fullmatch(source) is None or item['reason'] is not None:
                raise ChatExportError(code)
            try: relative_path(source)
            except ChatExportStorageError: raise ChatExportError(code) from None
        elif source is not None:
            raise ChatExportError(code)
        if item['status'] == 'pending' and item['reason'] is not None: raise ChatExportError(code)
        mime = _string(item['mime'], 256, code).lower().strip()
        if any(ord(char) < 32 or ord(char) > 126 for char in mime): raise ChatExportError(code)
        attachments.append({'id':identifier,'type':item['type'],'name':_string(item['name'],1024,code),
                            'mime':mime,'status':item['status'],'reason':item['reason'],'source':source})
    return {'id':_identifier(value['id'],code),
            'author':{'id':_identifier(value['author']['id'],code),'name':_string(value['author']['name'],1024,code)},
            'timestampMs':stamp, 'text':_string(value['text'],MAX_TEXT,code), 'outgoing':value['outgoing'],
            'reply':reply,'attachments':attachments,'kind':kind}


def validate_completion(value):
    code = 'invalid_completion'
    _keys(value, {'historyComplete','stopReason','pagesFetched'}, set(), code)
    if (type(value['historyComplete']) is not bool or type(value['stopReason']) is not str or value['stopReason'] not in STOP_REASONS
            or type(value['pagesFetched']) is not int or not 0 <= value['pagesFetched'] <= 20000
            or value['historyComplete'] != (value['stopReason'] == 'end_of_history')):
        raise ChatExportError(code)
    return dict(value)


def _date(stamp, offset):
    if stamp is None: return None, 'Время не указано', ''
    date = datetime(1970,1,1,tzinfo=timezone.utc) + timedelta(milliseconds=stamp,minutes=offset)
    return date.strftime('%d.%m.%Y'), date.strftime('%H:%M'), date.strftime('%d.%m.%Y %H:%M:%S')


def _safe_download_name(name, mime, *, detected=False):
    # Names are labels, never paths; executable/HTML/SVG suffixes cannot survive.
    basename = re.split(r'[/\\]', name)[-1]
    if '.' in basename: stem,extension = basename.rsplit('.',1)
    else: stem,extension = basename,''
    extension = extension.lower()
    stem = ''.join(char for char in stem if char not in '<>:"|?*' and ord(char) >= 32 and ord(char) != 127 and not 0x202A <= ord(char) <= 0x202E and not 0x2066 <= ord(char) <= 0x2069).strip(' .')[:120]
    if not stem or stem.split('.')[0].upper() in {'CON','NUL','PRN','AUX',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}: stem = 'Вложение'
    suffix = ('bin' if extension in ACTIVE_EXTENSIONS else extension if extension in DOWNLOAD_EXTENSIONS
              and (not detected or extension in DETECTED_EXTENSIONS.get(mime,set())) else MIME_EXT.get(mime,'bin'))
    return stem + '.' + suffix


def _inline_kind(workspace, item):
    """Restrict embedded formats to inert media with a matching magic header."""
    from launch_mod import _config_stream
    with _config_stream(workspace.staging / relative_path(item['source']), create=False) as stream:
        head = stream.read(32)
    kind = item['type']; detected = None
    if head.startswith(b'\x89PNG\r\n\x1a\n'): detected='image/png'
    elif head.startswith(b'\xff\xd8\xff'): detected='image/jpeg'
    elif head[:6] in (b'GIF87a',b'GIF89a'): detected='image/gif'
    elif head[:4] == b'RIFF' and head[8:12] == b'WEBP': detected='image/webp'
    if kind == 'image' and detected: return 'image',detected
    if head[4:8] == b'ftyp': detected='video/mp4' if kind=='video' else 'audio/mp4'
    elif head.startswith(b'\x1aE\xdf\xa3'): detected='video/webm' if kind=='video' else 'audio/webm'
    elif head.startswith(b'OggS'): detected='video/ogg' if kind=='video' else 'audio/ogg'
    elif head[:4] == b'RIFF' and head[8:12] == b'WAVE': detected='audio/wav'
    elif head.startswith(b'fLaC'): detected='audio/flac'
    elif head.startswith(b'ID3') or len(head)>1 and head[0]==255 and head[1]&0xE0==0xE0: detected='audio/mpeg'
    if detected and detected.startswith(('audio/','video/')) and kind in ('video','audio','voice'):
        return ('video' if kind == 'video' else 'audio'),detected
    return None,None


CSS = '''*{box-sizing:border-box}html{color-scheme:light}body{margin:0;background:#eef0f4;color:#17212b;font:15px/1.45 system-ui,"Segoe UI",sans-serif}header{background:#fff;border-bottom:1px solid #dce1e7;padding:22px max(20px,calc((100vw - 920px)/2));overflow-wrap:anywhere}h1{margin:0 0 5px;font-size:23px}.muted{color:#687889;font-size:13px}.notice{margin-top:12px;padding:10px 12px;border-radius:10px;background:#edf5ff}.notice.partial{background:#fff3dc}main{max-width:920px;margin:auto;padding:20px 16px 28px}.day{text-align:center;color:#657281;margin:18px 0 12px;font-size:12px}.message{display:flex;margin:7px 0}.message.outgoing{justify-content:flex-end}.bubble{max-width:78%;min-width:100px;border:1px solid #e2e7ed;border-radius:15px 15px 15px 4px;background:#fff;padding:10px 13px;overflow-wrap:anywhere}.outgoing .bubble{background:#e1edff;border-color:#d3e4fc;border-radius:15px 15px 4px 15px}.author{font-size:13px;font-weight:600;color:#2868c7;margin-bottom:4px}.text{white-space:pre-wrap}.time{text-align:right;font-size:11px;color:#718293;margin-top:5px}.reply{border-left:3px solid #6ca1ec;background:#f3f6fb;border-radius:4px;padding:6px 9px;margin:2px 0 8px;font-size:13px;white-space:pre-wrap}.reply strong{display:block;color:#2868c7}.attachment{margin-top:9px;padding-top:7px;border-top:1px solid #dce4ef}.attachment img,.attachment video{display:block;max-width:100%;max-height:480px;border-radius:8px}.attachment audio{display:block;width:360px;max-width:100%}a{color:#1976e9;text-decoration:none}a:hover{text-decoration:underline}.attachment a{display:inline-block;margin-top:5px}.missing{padding:8px;border-radius:6px;background:#f3f4f6;color:#667383;font-size:13px}.service{color:#687889;font-size:13px}footer{max-width:920px;margin:auto;padding:16px 24px 32px;color:#687889;font-size:12px}@media(max-width:600px){.bubble{max-width:94%}header{padding:18px 16px}main{padding:12px 8px}.bubble{padding:9px 11px}}'''


def _with_next(rows):
    """Hold only the current row and one next row, including exact boundaries."""
    sentinel = object(); iterator = iter(rows); current = next(iterator,sentinel)
    while current is not sentinel:
        following = next(iterator,sentinel)
        yield current,following is not sentinel
        current = following


def _page_file(number): return 'index.html' if number == 1 else f'messages-{number}.html'


def _header(html,chat,completion):
    html.write('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
               '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; connect-src \'none\'; script-src \'none\'; object-src \'none\'; base-uri \'none\'; form-action \'none\'; frame-src \'none\'; img-src \'self\' file:; media-src \'self\' file:; style-src \'unsafe-inline\'">'
               '<meta name="referrer" content="no-referrer"><title>'+escape(chat['title'])+' · ValMAX</title><style>'+CSS+'</style></head><body><header><h1>'+escape(chat['title'] or 'Чат')+'</h1>')
    offset = chat['timezoneOffsetMinutes']; sign = '+' if offset >= 0 else '−'
    html.write('<div class="muted">Экспорт ValMAX · локальная копия · время UTC'+sign+f'{abs(offset)//60:02d}:{abs(offset)%60:02d}'+'</div>')
    if completion['historyComplete']:
        html.write('<div class="notice">Клиент достиг начала доступной истории. Удалённые и недоступные сообщения в копию не входят.</div>')
    else:
        html.write('<div class="notice partial">Сохранена часть доступной истории. Полнота не подтверждена.</div>')
    html.write('</header><main>')


def _footer(html,counts,page,page_start,has_next):
    html.write('</main><footer>')
    if page > 1 or has_next:
        html.write('<nav aria-label="Страницы переписки" style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;margin-bottom:16px;font-size:14px">')
        if page > 1: html.write('<a rel="prev" href="'+_page_file(page-1)+'">Предыдущая</a>')
        html.write('<span>Страница '+str(page)+'</span>')
        if has_next: html.write('<a rel="next" href="'+_page_file(page+1)+'">Следующая</a>')
        html.write('</nav>На странице: '+str(counts['messages']-page_start['messages'])+' сообщений · вложений сохранено: '+str(counts['mediaReady']-page_start['mediaReady'])+' · не сохранено: '+str(counts['mediaMissing']-page_start['mediaMissing'])+'.<br>')
        if not has_next: html.write('Всего в копии: '+str(counts['messages'])+' сообщений · вложений сохранено: '+str(counts['mediaReady'])+' · не сохранено: '+str(counts['mediaMissing'])+'.<br>')
    else:
        html.write('Сообщений: '+str(counts['messages'])+' · вложений сохранено: '+str(counts['mediaReady'])+' · не сохранено: '+str(counts['mediaMissing'])+'.<br>')
    html.write('Часовой пояс зафиксирован при экспорте; исторические переходы на летнее время не пересчитываются. Копия открывается без подключения к сети.</footer></body></html>')


def render_into(workspace, chat, messages, completion, *, progress=None, deduplicated=False):
    """Render ready files already inside workspace/media; caller publishes.

    `deduplicated=True` is only for a coordinator whose disk index enforces
    unique message IDs and opaque media paths; it avoids history-sized ID sets.
    `progress` receives aggregate counts only. Any exception leaves the caller
    responsible for workspace.abort(). No message content appears in errors.
    """
    chat = validate_chat(chat); completion = validate_completion(completion)
    counts = dict.fromkeys(('messages','attachments','mediaReady','mediaMissing','unsupported','bytes'),0)
    omissions = Counter(); seen = set(); seen_sources = set(); previous_day = object()
    page = 1; page_start = dict(counts); offset = chat['timezoneOffsetMinutes']
    with workspace.open_text('assets.jsonl') as assets, workspace.open_text('omissions.jsonl') as missing, ExitStack() as page_streams:
        html = page_streams.enter_context(workspace.open_text(_page_file(page)))
        _header(html,chat,completion)
        for original,has_next in _with_next(messages):
            workspace.check_cancelled()
            item = validate_message(original)
            identity = hashlib.sha256(item['id'].encode('ascii')).digest()
            if not deduplicated:
                if identity in seen: raise ChatExportError('invalid_message')
                seen.add(identity)
            counts['messages'] += 1
            if counts['messages'] > MAX_MESSAGES: raise ChatExportError('limit_exceeded')
            day, clock, fulltime = _date(item['timestampMs'],offset)
            if day != previous_day:
                html.write('<div class="day">'+escape(day or 'Дата не указана')+'</div>'); previous_day = day
            html.write('<article class="message'+(' outgoing' if item['outgoing'] else '')+'" id="m'+identity.hex()+'"><div class="bubble"><div class="author">'+escape(item['author']['name'] or 'Автор не указан')+'</div>')
            if item['reply']:
                reply = item['reply']; html.write('<div class="reply"><strong>'+escape(reply['authorName'] or 'Ответ на сообщение')+'</strong>'+escape(reply['text'])+'</div>')
            if item['kind'] != 'message':
                counts['unsupported'] += 1
                html.write('<div class="service">'+('Служебное сообщение' if item['kind']=='service' else 'Содержимое этого сообщения поддерживается не полностью')+'</div>')
            html.write('<div class="text">'+escape(item['text'])+'</div>')
            for attachment in item['attachments']:
                counts['attachments'] += 1
                if counts['attachments'] > MAX_ATTACHMENTS: raise ChatExportError('limit_exceeded')
                reason = attachment['reason'] or ('unsupported' if attachment['type']=='unsupported' else 'not_downloaded')
                ready = attachment['status'] == 'ready'
                proof = None
                if ready:
                    if not deduplicated:
                        if attachment['source'] in seen_sources: raise ChatExportError('invalid_message')
                        seen_sources.add(attachment['source'])
                    try: proof = workspace.verify_asset(attachment['source'],max_bytes=MAX_ASSET_BYTES)
                    except ChatExportStorageError as error:
                        if error.code not in ('source_unavailable','asset_too_large','source_changed'): raise
                        reason = error.code; ready = False
                        workspace.discard_asset(attachment['source'])
                html.write('<div class="attachment">')
                label = attachment['name'] or TYPE_TEXT[attachment['type']]
                if ready:
                    counts['mediaReady'] += 1; counts['bytes'] += proof['bytes']
                    source = attachment['source']; inline,detected_mime = _inline_kind(workspace,attachment)
                    if inline == 'image': html.write('<img loading="lazy" src="'+source+'" alt="'+escape(label,quote=True)+'">')
                    elif inline in ('video','audio'):
                        html.write('<'+inline+' controls preload="none" src="'+source+'"></'+inline+'>')
                    html.write('<a href="'+source+'" download="'+escape(_safe_download_name(label,detected_mime or attachment['mime'],detected=detected_mime is not None),quote=True)+'">'+escape(label)+'</a>')
                    assets.write(json.dumps({'messageId':item['id'],'attachmentId':attachment['id'],'source':source,'name':label,'mime':attachment['mime'],'detectedMime':detected_mime,**proof},ensure_ascii=False)+'\n')
                else:
                    counts['mediaMissing'] += 1; omissions[reason] += 1
                    html.write('<div class="missing">'+escape(label)+' · '+REASON_TEXT[reason]+'</div>')
                    missing.write(json.dumps({'messageId':item['id'],'attachmentId':attachment['id'],'type':attachment['type'],'reason':reason},ensure_ascii=False)+'\n')
                if attachment['type']=='unsupported': counts['unsupported'] += 1
                html.write('</div>')
            html.write('<div class="time" title="'+escape(fulltime,quote=True)+'">'+clock+'</div></div></article>')
            if progress is not None and counts['messages'] % 50 == 0: progress(dict(counts))
            if has_next and counts['messages']-page_start['messages'] == MESSAGES_PER_PAGE:
                _footer(html,counts,page,page_start,True); page_streams.close()
                page += 1; page_start = dict(counts); previous_day = object()
                html = page_streams.enter_context(workspace.open_text(_page_file(page)))
                _header(html,chat,completion)
        _footer(html,counts,page,page_start,False)
    summary = {'schema':1,'counts':counts,**completion,'htmlFile':'index.html','htmlPages':page}
    with workspace.open_text('manifest.json') as stream:
        json.dump({**summary,'chat':chat,'omissions':dict(omissions),'assetsFile':'assets.jsonl','omissionsFile':'omissions.jsonl'},stream,ensure_ascii=False,indent=2)
    if progress is not None: progress(dict(counts))
    return summary


def render_chat(destination, chat, messages, completion, *, spool_root, cancel_event=None, progress=None):
    """Convenience wrapper for a separate controlled spool (tests/adapters)."""
    with ExportWorkspace(destination,cancel_event=cancel_event) as workspace:
        def copied_rows():
            serial = 0
            for original in messages:
                row = validate_message(original)
                for attachment in row['attachments']:
                    if attachment['status'] != 'ready': continue
                    serial += 1; target = 'media/a'+f'{serial:010d}'+'.'+MIME_EXT.get(attachment['mime'],'bin')
                    try:
                        workspace.copy_asset(spool_root,attachment['source'],target,max_bytes=MAX_ASSET_BYTES)
                        attachment['source'] = target
                    except ChatExportStorageError as error:
                        if error.code not in ('source_unavailable','asset_too_large','source_changed'): raise
                        attachment.update(status='unavailable',source=None,reason=error.code)
                yield row
        summary = render_into(workspace,chat,copied_rows(),completion,progress=progress)
        workspace.publish()
        return summary
