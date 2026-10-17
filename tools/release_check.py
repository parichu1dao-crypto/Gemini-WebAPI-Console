"""Check distributable files only; never print suspected secret values."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    names = subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],
                                    cwd=ROOT).decode().split('\0')
    problems=[]
    for name in sorted(set(filter(None,names))):
        path=ROOT/name
        if not path.is_file():continue
        if name.startswith(('data/','accounts/','.env','.venv/')) or path.suffix in {'.db','.sqlite3','.jsonl','.log','.har'}:
            problems.append((name,'private/runtime artifact'))
            continue
        try:text=path.read_text(encoding='utf-8-sig')
        except UnicodeError:continue
        for label,pattern in (
            ('live provider key',r'nxk_live_[A-Za-z0-9_.-]{20,}'),
            ('GitHub credential',r'gh[pousr]_[A-Za-z0-9]{30,}'),
            ('private workstation path',r'[CF]:\\(?:Users\\Administrator|Auto\\|CodexProjects\\|novel_cn\\)'),
        ):
            if re.search(pattern,text):problems.append((name,label))
    for name,label in problems:print(f'{name}: {label}')
    print(f'Release scan: {len(problems)} findings. Manual review still required.')
    return bool(problems)


if __name__=='__main__':sys.exit(main())
