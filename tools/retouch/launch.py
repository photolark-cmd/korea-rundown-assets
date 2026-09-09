# -*- coding: utf-8 -*-
"""더블클릭용 실행기.

터미널을 안 쓰는 사람이 스튜디오를 여는 길. 하는 일:
  1. 지난번에 쓴 사진 폴더를 기억해 두고, 없으면 폴더 선택창을 띄운다
  2. 비어 있는 포트를 찾는다 (윈도는 포트 충돌을 오류로 알려 주지 않는다 — studio.port_taken 참고)
  3. studio.main() 을 그 인자로 부른다

배치파일에는 한글을 넣지 않는다(코드페이지에 따라 깨진다). 한글 안내는 전부 여기 있다.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import studio  # noqa: E402

SETTINGS = os.path.join(HERE, 'work', 'launcher.json')


def load():
    try:
        with open(SETTINGS, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save(d):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def pick_folder(title):
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return input(title + '\n폴더 경로를 붙여넣고 Enter: ').strip().strip('"')
    root = tkinter.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    path = filedialog.askdirectory(title=title)
    root.destroy()
    return path


def free_port(start=8792, tries=40):
    for p in range(start, start + tries):
        if not studio.port_taken(p):
            return p
    raise SystemExit('빈 포트를 찾지 못했습니다.')


def main():
    cfg = load()
    folder = cfg.get('folder')
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):   # 폴더를 아이콘에 끌어다 놓은 경우
        folder = os.path.abspath(sys.argv[1])
    if not folder or not os.path.isdir(folder):
        print('사진이 든 폴더를 고르세요. (창이 안 보이면 작업표시줄을 확인하세요)')
        folder = pick_folder('보정할 사진이 든 폴더')
        if not folder:
            raise SystemExit('폴더를 고르지 않아 종료합니다.')
    out = cfg.get('out') or os.path.join(folder, '보정본')
    cfg.update({'folder': folder, 'out': out})
    save(cfg)

    os.makedirs(out, exist_ok=True)
    port = free_port()
    print('사진 폴더 : %s' % folder)
    print('저장 폴더 : %s' % out)
    print()
    sys.argv = [sys.argv[0], '--folder', folder, '--out', out,
                '--data', os.path.join(HERE, 'work'), '--port', str(port)]
    studio.main()


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        print()
        print('오류로 멈췄습니다: %s' % e)
        input('창을 닫으려면 Enter 를 누르세요.')
