#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from test_support import locate

FORBIDDEN_RUNTIME=[
    re.compile(r'api\.openai\.com',re.I),re.compile(r'api\.anthropic\.com',re.I),
    re.compile(r'generativelanguage\.googleapis\.com',re.I),
    re.compile(r'\b(?:openai|anthropic)\s*==',re.I),
]
SECRET=[
    re.compile(r'github_pat_[A-Za-z0-9_]{16,}'),re.compile(r'\bghp_[A-Za-z0-9]{20,}'),
    re.compile(r'\bsk-[A-Za-z0-9_-]{16,}'),re.compile(r'(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}'),
]
TEXT={'.py','.js','.mjs','.html','.css','.json','.yaml','.yml','.toml','.ini','.conf','.sh','.service','.timer','.md','.txt'}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--repo',default='.')
    args=parser.parse_args(); repo,module_root,_=locate()
    target=Path(args.repo).resolve()/'status' if (Path(args.repo).resolve()/'status').is_dir() else module_root
    violations=[]
    for path in target.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in TEXT or any(x in path.parts for x in ('.git','node_modules','data','private','runtime')): continue
        try: text=path.read_text(encoding='utf-8')
        except UnicodeDecodeError: continue
        for pattern in FORBIDDEN_RUNTIME:
            if pattern.search(text): violations.append({'path':str(path),'type':'runtime_agent_or_model_dependency','pattern':pattern.pattern})
        for pattern in SECRET:
            if pattern.search(text): violations.append({'path':str(path),'type':'secret_like_value','pattern':pattern.pattern})
    print(json.dumps({'target':str(target),'violations':violations},ensure_ascii=False,indent=2))
    return 1 if violations else 0

if __name__=='__main__': raise SystemExit(main())
