#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict, deque
import argparse
import json
from pathlib import Path
import re
import sys
import yaml

from test_support import locate


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--repo',default='.')
    args=parser.parse_args(); repo,module_root,contracts=locate()
    acceptance=yaml.safe_load((contracts/'acceptance_contract.yaml').read_text(encoding='utf-8'))
    dag=yaml.safe_load((contracts/'task_dag.yaml').read_text(encoding='utf-8'))
    fixtures=yaml.safe_load((contracts/'blind_fixtures.yaml').read_text(encoding='utf-8'))
    ids={item['id'] for item in acceptance['global_invariants']}
    for values in acceptance['domains'].values(): ids.update(item['id'] for item in values)
    tasks={item['id']:item for item in dag['tasks']}
    if len(tasks)!=len(dag['tasks']): raise AssertionError('duplicate task ID')
    indegree={key:0 for key in tasks}; outgoing=defaultdict(list); stage_counts=defaultdict(int)
    mapped=set()
    for key,item in tasks.items():
        stage_counts[item['stage']]+=1
        if stage_counts[item['stage']]>5: raise AssertionError('stage exceeds five tasks')
        for dep in item['depends_on']:
            if dep not in tasks: raise AssertionError(f'missing dependency {dep}')
            outgoing[dep].append(key); indegree[key]+=1
        for ref in item['acceptance_refs']:
            if ref not in ids: raise AssertionError(f'unknown acceptance ref {ref}')
            mapped.add(ref)
        if not item.get('rollback') or not item.get('stop_condition'): raise AssertionError(f'incomplete task {key}')
    queue=deque(key for key,value in indegree.items() if value==0); seen=[]
    while queue:
        key=queue.popleft(); seen.append(key)
        for nxt in outgoing[key]:
            indegree[nxt]-=1
            if indegree[nxt]==0: queue.append(nxt)
    if len(seen)!=len(tasks): raise AssertionError('task graph contains cycle')
    missing=ids-mapped
    if missing: raise AssertionError(f'unmapped acceptance {sorted(missing)}')
    fixture_ids=set()
    for item in fixtures['fixtures']:
        if item['id'] in fixture_ids: raise AssertionError('duplicate blind fixture')
        fixture_ids.add(item['id'])
        for ref in item['acceptance_refs']:
            if ref not in ids: raise AssertionError(f'blind fixture unknown acceptance {ref}')
    result={'acceptance':len(ids),'tasks':len(tasks),'fixtures':len(fixture_ids),'acyclic':True,'max_stage_tasks':max(stage_counts.values())}
    print(json.dumps(result,ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
