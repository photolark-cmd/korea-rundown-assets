# -*- coding: utf-8 -*-
"""API 키 없이 대화창을 돌리는 백엔드.

studio.py 의 chat() 은 anthropic SDK 의 messages.create() 모양에 맞춰 쓰여 있다.
이 모듈은 같은 모양을 흉내 내되, 실제 호출은 로컬에 설치된 Claude Code CLI
(`claude -p`)로 보낸다. 사용자의 구독으로 돌아가므로 ANTHROPIC_API_KEY 가 없어도 된다.

값이 아니라 대가를 먼저 적어 둔다 (2026-09-10 실측):
  · 한 턴 11~14초 (API 직접 호출은 5~8초)
  · 턴마다 Claude Code 자체 컨텍스트 약 70k 를 캐시에서 다시 읽는다 (구독 사용량 $0.035 상당).
    `--bare` / CLAUDE_CODE_SIMPLE=1 로 이걸 걷어내려면 OAuth 가 끊겨 API 키가 필요해지므로
    (실측: "Not logged in"), 키 없는 경로에서 이 비용은 못 없앤다.
따라서 키가 있으면 API 백엔드가 항상 낫다. 이건 키가 없을 때의 길이다.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid

import cv2
import numpy as np

CLI_TIMEOUT = 300


class CliError(RuntimeError):
    pass


# ---------------------------------------------------------------- 응답 블록
# anthropic SDK 의 content 블록과 같은 속성(.type/.text/.name/.input/.id)만 갖춘 최소 객체.

class TextBlock:
    type = 'text'

    def __init__(self, text):
        self.text = text


class ToolUseBlock:
    type = 'tool_use'

    def __init__(self, name, input_, id_=None):
        self.name = name
        self.input = input_
        self.id = id_ or ('cli_' + uuid.uuid4().hex[:16])


class Response:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.stop_details = None


# ---------------------------------------------------------------- JSON 파싱
# 한국어로 답하는 모델은 전각 구두점(，：“”)을 섞어 내보내고, 코드펜스를 붙이기도 한다.
# 파서는 관대해야 하고, 그래도 실패하면 한 번 되물을 수 있어야 한다.

_FULLWIDTH = {
    '，': ',', '：': ':', '；': ';', '（': '(', '）': ')',
    '［': '[', '］': ']', '｛': '{', '｝': '}',
    '“': '"', '”': '"', '‘': "'", '’': "'", '　': ' ',
}


def _strip_fence(s):
    m = re.search(r'```(?:json)?\s*(.+?)```', s, re.S)
    return m.group(1) if m else s


def _outermost_object(s):
    start = s.find('{')
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def parse_reply(raw):
    """모델이 낸 텍스트에서 {"say":..., "tools":[...]} 를 뽑는다. 실패하면 None."""
    if not raw:
        return None
    s = _strip_fence(raw)
    for a, b in _FULLWIDTH.items():
        s = s.replace(a, b)
    blob = _outermost_object(s)
    if blob is None:
        return None
    try:
        d = json.loads(blob)
    except Exception:
        try:                                   # 후행 쉼표만 정리해서 한 번 더
            d = json.loads(re.sub(r',\s*([}\]])', r'\1', blob))
        except Exception:
            return None
    if not isinstance(d, dict):
        return None
    tools = d.get('tools') or []
    if not isinstance(tools, list):
        tools = []
    clean = []
    for t in tools:
        if isinstance(t, dict) and isinstance(t.get('name'), str):
            clean.append({'name': t['name'], 'input': t.get('input') if isinstance(t.get('input'), dict) else {}})
    return {'say': str(d.get('say') or ''), 'tools': clean}


PROTOCOL = """
당신은 사진 보정 프로그램의 조수이며, 지금은 **JSON 으로만** 말하는 경로로 불렸습니다.

반드시 지킬 것:
1. 답은 JSON 객체 **하나**만. 코드펜스도, 설명 문장도, 앞뒤 인사도 붙이지 마세요.
2. 형식:
   {"say": "사용자에게 보여 줄 한두 문장", "tools": [{"name": "도구이름", "input": {...}}]}
3. 사진을 고쳐야 하면 "tools" 에 호출을 넣습니다. 여러 개면 순서대로 실행됩니다.
   더 할 일이 없으면 "tools" 는 빈 배열 []. 그때가 턴의 끝입니다.
4. "[이미지] <경로>" 로 준 파일은 Read 도구로 직접 열어 보세요. 보고 나서 판단합니다.
5. 쓸 수 있는 도구와 입력 스키마는 아래 목록이 전부입니다. 목록에 없는 이름은 쓰지 마세요.
6. 좌표 x, y 는 이미지 폭·높이에 대한 0~1 비율이고 원점은 왼쪽 위입니다.
"""

REPAIR = ('직전 답이 JSON 으로 파싱되지 않았습니다. 설명 없이 '
          '{"say": "...", "tools": [...]} 형태의 JSON 객체 하나만 다시 내보내세요.')


class _Messages:
    def __init__(self, owner):
        self.o = owner

    def create(self, *, model=None, system=None, tools=None, messages=None, **_):
        return self.o._create(model=model, system=system, tools=tools, messages=messages or [])


class CliClient:
    """anthropic.Anthropic() 자리에 그대로 끼워 넣는 대체 클라이언트."""

    def __init__(self, workdir, model='claude-opus-5', effort='medium', exe=None):
        self.exe = exe or shutil.which('claude') or 'claude'
        self.model = model
        self.effort = effort
        self.workdir = workdir
        self.session_id = None
        self.imgdir = os.path.join(workdir, '.cli-images')
        os.makedirs(self.imgdir, exist_ok=True)
        # CLAUDE.md 자동 발견을 피하려고 빈 폴더에서 돌린다 (전역 CLAUDE.md 는 못 막는다).
        self.cwd = os.path.join(workdir, '.cli-cwd')
        os.makedirs(self.cwd, exist_ok=True)

    messages = property(lambda self: _Messages(self))

    # -------------------------------------------------- 대화 → 프롬프트 문자열
    def _dump_image(self, b64):
        import base64
        path = os.path.join(self.imgdir, uuid.uuid4().hex[:12] + '.jpg')
        with open(path, 'wb') as f:
            f.write(base64.b64decode(b64))
        self._prune_images()
        return path

    def _prune_images(self, keep=40):
        try:
            files = [os.path.join(self.imgdir, n) for n in os.listdir(self.imgdir)]
            files.sort(key=os.path.getmtime)
            for p in files[:-keep]:
                os.remove(p)
        except OSError:
            pass

    def _render_block(self, b):
        if not isinstance(b, dict):                       # 우리가 만든 assistant 블록
            if getattr(b, 'type', '') == 'text':
                return b.text
            if getattr(b, 'type', '') == 'tool_use':
                return '(호출함) %s %s' % (b.name, json.dumps(b.input, ensure_ascii=False))
            return ''
        t = b.get('type')
        if t == 'text':
            return b['text']
        if t == 'image':
            return '[이미지] ' + self._dump_image(b['source']['data'])
        if t == 'tool_result':
            body = b.get('content')
            if isinstance(body, str):
                inner = body
            else:
                inner = '\n'.join(self._render_block(c) for c in (body or []))
            head = '[도구 오류]' if b.get('is_error') else '[도구 결과]'
            return head + ' ' + inner
        return ''

    def _render(self, messages):
        out = []
        for m in messages:
            role = '사용자' if m['role'] == 'user' else '조수'
            content = m['content']
            if isinstance(content, str):
                body = content
            else:
                body = '\n'.join(x for x in (self._render_block(b) for b in content) if x)
            out.append('### %s\n%s' % (role, body))
        return '\n\n'.join(out)

    # -------------------------------------------------- CLI 호출
    def _run(self, prompt, system=None):
        cmd = [self.exe, '-p', '--output-format', 'json', '--model', self.model,
               '--effort', self.effort, '--allowed-tools', 'Read',
               '--disable-slash-commands', '--strict-mcp-config',
               '--add-dir', self.imgdir]
        if self.session_id:
            cmd += ['--resume', self.session_id]
        elif system:
            cmd += ['--system-prompt', system]
        cmd.append(prompt)
        try:
            p = subprocess.run(cmd, cwd=self.cwd, capture_output=True, timeout=CLI_TIMEOUT,
                               stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise CliError('claude 명령을 찾지 못했습니다. Claude Code 가 설치돼 있어야 합니다.')
        except subprocess.TimeoutExpired:
            raise CliError('Claude CLI 가 %d초 안에 답하지 않았습니다.' % CLI_TIMEOUT)
        raw = (p.stdout or b'').decode('utf-8', 'replace')
        i = raw.find('{')
        if i < 0:
            err = (p.stderr or b'').decode('utf-8', 'replace').strip() or raw.strip()
            raise CliError('Claude CLI 응답을 읽지 못했습니다: %s' % err[:300])
        try:
            env = json.loads(raw[i:])
        except Exception:
            raise CliError('Claude CLI 응답이 JSON 이 아닙니다: %s' % raw[i:i + 300])
        if env.get('session_id'):
            self.session_id = env['session_id']
        text = env.get('result') or ''
        if env.get('is_error') or 'Not logged in' in text:
            raise CliError(text.strip()[:300] or 'Claude CLI 오류')
        return text

    def _create(self, *, model, system, tools, messages):
        if model:
            self.model = model
        fresh = self.session_id is None
        # 아직 CLI 로 넘기지 않은 메시지만 보낸다. 개수(인덱스)로 세면 trim_history 가
        # 앞에서 메시지를 잘라낼 때 어긋나므로, 보낸 메시지에 표시를 남긴다.
        new = [m for m in messages if not m.get('_cli_sent')]
        for m in new:
            m['_cli_sent'] = True
        body = self._render(new)
        if fresh:
            sys_prompt = ((system[0]['text'] if isinstance(system, list) else system) or '') + PROTOCOL
            prompt = ('쓸 수 있는 도구 목록(JSON 스키마):\n%s\n\n%s'
                      % (json.dumps(tools, ensure_ascii=False), body))
            raw = self._run(prompt, system=sys_prompt)
        else:
            raw = self._run(body)
        d = parse_reply(raw)
        if d is None:
            raw = self._run(REPAIR)
            d = parse_reply(raw)
        if d is None:
            raise CliError('Claude 가 JSON 형식으로 답하지 않았습니다.')
        content = []
        if d['say']:
            content.append(TextBlock(d['say']))
        for t in d['tools']:
            content.append(ToolUseBlock(t['name'], t['input']))
        return Response(content, 'tool_use' if d['tools'] else 'end_turn')


def available():
    return shutil.which('claude') is not None
