"""Check public source and documentation without launching the native client.

Uses the Python standard library only. Diagnostics contain paths and categories,
never secret values or source lines. Run from any working directory.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import struct
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
RU_FIRST_LINE = '<p align="center"><strong>Ваше общение. Ваши правила.</strong></p>'
SCREENSHOT = Path('docs/assets/settings-ru.png')
EXCLUDED_DIRECTORIES = {
    '.git', '__pycache__', '.venv', 'venv', '.pytest_cache', '.mypy_cache',
    'analysis', 'audit', 'publish', 'release', 'runtime', 'baseline',
    'build', 'dist', 'MXMOD', 'ONEME', 'WinCred', 'legacy-C-project',
}
PRIVATE_EXTENSIONS = {'.exe', '.dll', '.msi', '.pdb', '.dmp', '.mdata',
                      '.maxsession', '.db', '.sqlite', '.sqlite3', '.zip',
                      '.7z', '.rar', '.log', '.jsonl', '.pem', '.key'}
TEXT_EXTENSIONS = {'.py', '.js', '.qml', '.json', '.md', '.txt', '.cmd',
                   '.iss', '.qrc', '.svg', '.yml', '.yaml'}
SECRET_PATTERNS = {
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'openai-token': re.compile(r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b'),
    'aws-access-id': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'slack-token': re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{20,}\b'),
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
}
INLINE_LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))[^)\n]*\)')
REFERENCE_LINK = re.compile(r'^\s*\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|(\S+))', re.MULTILINE)
HTML_LINK = re.compile(r'\b(?:src|href)\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def public_files(errors: list[str]) -> list[Path]:
    files = []
    for directory, names, filenames in os.walk(ROOT, followlinks=False):
        parent = Path(directory)
        for name in list(names):
            path = parent / name
            if name == '.git':
                names.remove(name)
            elif path.is_symlink():
                errors.append(f'{path.relative_to(ROOT).as_posix()}: linked-directory')
                names.remove(name)
            elif name in EXCLUDED_DIRECTORIES or name.startswith(('ValMAX-Chat-', 'account-prefs-test-', '.valmax-')):
                errors.append(f'{path.relative_to(ROOT).as_posix()}: private-or-generated-directory')
                names.remove(name)
        for name in filenames:
            path = parent / name
            relative = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                errors.append(f'{relative}: linked-file')
            elif (path.suffix.lower() in PRIVATE_EXTENSIONS or name.startswith('CM_')
                  or name == '.env' or (name.startswith('.env.') and name != '.env.example')):
                errors.append(f'{relative}: private-or-binary-artifact')
            else:
                files.append(path)
    return files


def without_fenced_code(text: str) -> str:
    result = []
    fence = None
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            char = marker.group(1)[0]
            fence = None if fence == char else (char if fence is None else fence)
            continue
        if fence is None:
            result.append(line)
    return '\n'.join(result)


def check_link(path: Path, target: str, errors: list[str]) -> None:
    parsed = urlsplit(target)
    if parsed.scheme in {'http', 'https', 'mailto', 'data'} or target.startswith('#'):
        return
    relative = path.relative_to(ROOT).as_posix()
    if parsed.scheme or target.startswith(('/', '\\')):
        errors.append(f'{relative}: nonportable-local-link')
        return
    decoded = unquote(parsed.path)
    if not decoded:
        return
    destination = (path.parent / decoded).resolve()
    if not destination.is_relative_to(ROOT) or not destination.exists():
        errors.append(f'{relative}: missing-or-outside-relative-link ({decoded})')


def main() -> int:
    errors: list[str] = []
    files = public_files(errors)
    python_count = markdown_count = 0
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if path.suffix in TEXT_EXTENSIONS or path.name in {'LICENSE', '.gitignore', 'optimization_qmldir'}:
            try:
                text = path.read_text(encoding='utf-8-sig')
            except (UnicodeError, OSError):
                errors.append(f'{relative}: unreadable-utf8-source')
                continue
            for category, pattern in SECRET_PATTERNS.items():
                if pattern.search(text):
                    errors.append(f'{relative}: possible-{category}')
            if path.suffix == '.py':
                python_count += 1
                try:
                    ast.parse(text, filename=relative, feature_version=(3, 13))
                except SyntaxError as error:
                    errors.append(f'{relative}:{error.lineno}: python-syntax')
            if path.suffix == '.md':
                markdown_count += 1
                prose = without_fenced_code(text)
                for pattern in (INLINE_LINK, REFERENCE_LINK):
                    for match in pattern.finditer(prose):
                        check_link(path, match.group(1) or match.group(2), errors)
                for match in HTML_LINK.finditer(prose):
                    check_link(path, match.group(1), errors)

    required = ('README.md', 'README.en.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
                'CONTRIBUTING.md', 'SECURITY.md', 'docs/DEVELOPMENT.md')
    for name in required:
        if not (ROOT / name).is_file():
            errors.append(f'{name}: required-file-missing')
    readme = ROOT / 'README.md'
    if readme.is_file():
        lines = readme.read_text(encoding='utf-8-sig').splitlines()
        if not lines or lines[0] != RU_FIRST_LINE:
            errors.append('README.md: first-slogan-line')
        if SCREENSHOT.as_posix() not in readme.read_text(encoding='utf-8-sig'):
            errors.append('README.md: screenshot-link-missing')
    english = ROOT / 'README.en.md'
    if english.is_file():
        lines = english.read_text(encoding='utf-8-sig').splitlines()
        if not lines or not re.fullmatch(r'<p align="center"><strong>[^<>]+</strong></p>', lines[0]):
            errors.append('README.en.md: first-slogan-html')

    screenshot = ROOT / SCREENSHOT
    if screenshot.is_file():
        with screenshot.open('rb') as stream:
            header = stream.read(24)
        valid_header = (len(header) == 24 and header[:8] == b'\x89PNG\r\n\x1a\n'
                        and header[8:16] == b'\0\0\0\rIHDR')
        if not valid_header or struct.unpack('>II', header[16:24]) != (1704, 923):
            errors.append(f'{SCREENSHOT.as_posix()}: expected-1704x923-redacted-png')
    else:
        errors.append(f'{SCREENSHOT.as_posix()}: required-screenshot-missing')

    if errors:
        for error in sorted(set(errors)):
            print(error)
        print(f'FAILED: {len(set(errors))} repository checks')
        return 1
    print(f'OK: {python_count} Python files, {markdown_count} Markdown files, relative links, '
          'public artifact checks, README slogans and redacted screenshot dimensions; no native client launched.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
