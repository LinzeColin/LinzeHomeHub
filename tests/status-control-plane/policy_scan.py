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

# ★ 账务探针的**按文件**豁免。放宽一个安全守卫时必须同时留下「放宽到哪为止」,
#   否则下一个人只会看到「openai 域名是允许的」,那个口子会一直长大。
#   豁免只覆盖「域名出现在这一个文件里」,不覆盖下面的推理端点检查 ——
#   推理端点在**任何**文件里出现都是违规,包括这个被豁免的文件。
VENDOR_HOST_EXEMPT_FILE = 'collector/probe_ai_balance.py'

# 完整的推理端点 URL(host + 推理路径)。查账单不是调模型,但两者必须机器可分:
#   允许 https://api.openai.com/v1/organization/costs
#   禁止 https://api.openai.com/v1/chat/completions
# 只匹配「带 host 的完整 URL」,所以探针里那张裸路径黑名单不会误伤自己。
INFERENCE_ENDPOINT=[
    re.compile(r'https?://[^\s"\')]*(?:openai|anthropic|deepseek|googleapis|x\.ai|mistral)'
               r'[^\s"\')]*/(?:chat/completions|completions|responses|embeddings|'
               r'images/generations|messages|generateContent)', re.I),
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
        relative=path.as_posix()
        exempt_host=relative.endswith(VENDOR_HOST_EXEMPT_FILE)
        for pattern in FORBIDDEN_RUNTIME:
            if not pattern.search(text): continue
            # 只有「域名出现」这一条对账务探针豁免;SDK 依赖(openai==)之类照旧拦。
            if exempt_host and pattern.pattern.startswith('api\\.openai'): continue
            violations.append({'path':str(path),'type':'runtime_agent_or_model_dependency','pattern':pattern.pattern})
        # ★ 推理端点:不设任何豁免,连账务探针自己都要查。
        for pattern in INFERENCE_ENDPOINT:
            if pattern.search(text):
                violations.append({'path':str(path),'type':'model_inference_endpoint','pattern':pattern.pattern})
        for pattern in SECRET:
            if pattern.search(text): violations.append({'path':str(path),'type':'secret_like_value','pattern':pattern.pattern})
    print(json.dumps({'target':str(target),'violations':violations},ensure_ascii=False,indent=2))
    return 1 if violations else 0

if __name__=='__main__': raise SystemExit(main())
