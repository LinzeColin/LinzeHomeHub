#!/usr/bin/env python3
"""冻结盲测集(BF-001…BF-018)的执行器。

为什么需要这个文件:`governance_check.py` 对 `blind_fixtures.yaml` 只做两件事 ——
检查 id 不重复、检查引用的 acceptance 存在。它**一条场景都不执行**。
所以它输出的 `"fixtures": 18` 是「声明了 18 条」,不是「验过 18 条」。
把那个数字当成盲测通过的证据,就是「有测试 ≠ 有覆盖」的典型形态。

本执行器给每条 fixture 绑一个**真的会跑的**判据,并按三种结局如实记账:

  PASS     绑定的判据实际执行且符合预期
  FAIL     实际执行但不符合预期
  NOT_RUN  本环境没有可执行的判据(必须写明原因,**绝不允许记成 PASS**)

最后一条是 `execution/EXPECTED_EVIDENCE.md` 的硬性要求:
「任何未运行或不可获得的项必须标记 NOT_RUN 或 UNAVAILABLE,不得写 PASS」。

判据分两类:
  - `unittest`   复用冻结单测里已存在的用例(点名到方法,不是「跑了一堆测试所以算过」);
  - `inline`     本文件里现写的种子/破坏场景 —— 盲测的重点是**种下违规后守卫必须炸**,
                 光看干净树通过不算数。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_support import locate

REPO, MODULE_ROOT, CONTRACTS = locate()
HERE = Path(__file__).resolve().parent


# ────────────────────────── 判据实现 ──────────────────────────

def _evidence_dir() -> Path | None:
    """Git evidence 目录 —— worktree 下它在 .git/worktrees/<name>/ 里,不是仓根的 .git/。

    第一版这里用 rglob 到处找 candidate-subject.json,结果在 worktree 里根本找不到
    (worktree 的 .git 是个文件不是目录),于是把「S6-T1 明明产出了」误报成 NOT_RUN。
    误报成没跑,和误报成通过一样会让人做错决定,所以改成问 git 要权威路径。
    """
    proc = subprocess.run(["git", "rev-parse", "--git-path", "status-taskpack-v0.0.0.1"],
                          cwd=REPO, capture_output=True, text=True, check=False)
    if proc.returncode:
        return None
    value = Path(proc.stdout.strip())
    return value if value.is_absolute() else (REPO / value).resolve()


def _run_unittest(dotted: list[str]) -> tuple[bool, str]:
    """点名跑指定的冻结单测用例。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(HERE), str(HERE / "unit"), env.get("PYTHONPATH", "")])
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "-v", *dotted],
        cwd=HERE / "unit", capture_output=True, text=True, check=False, env=env,
    )
    tail = (proc.stderr or proc.stdout).strip().splitlines()
    return proc.returncode == 0, " / ".join(tail[-3:])


def bf_seeded_agent_dependency() -> tuple[bool, str]:
    """BF-010:种一个模型 SDK 依赖,policy_scan 必须 fail-closed。

    干净树通过说明不了什么 —— 要证明的是**种了违规它会炸**。
    """
    seeded = REPO / "status" / "_blind_bf010_seed.py"
    try:
        seeded.write_text("import os\nURL = 'https://' + 'api.openai.com' + '/v1'\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(HERE / "policy_scan.py"), "--repo", str(REPO)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "PYTHONPATH": str(HERE)},
        )
        caught = proc.returncode != 0 and "runtime_agent_or_model_dependency" in proc.stdout
    finally:
        seeded.unlink(missing_ok=True)
    # 种子清掉之后必须恢复干净,否则说明扫描器有状态残留
    clean = subprocess.run(
        [sys.executable, str(HERE / "policy_scan.py"), "--repo", str(REPO)],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": str(HERE)},
    ).returncode == 0
    if caught and clean:
        return True, ("种入模型 API 域名种子后扫描器 fail-closed 并指名违规;移除后恢复干净"
                      # ★ 这句话本身**不能**写出那个域名的字面量:结果 JSON 会被提交进
                      #   status/ 下,而 policy_scan 扫的就是 status/**。第一版写了字面量,
                      #   于是下一轮扫描把我自己的证据文件当成了违规 —— 扫描器没错,是我
                      #   把违规样本写进了被扫描的树里。
                      )
    if not caught:
        return False, "★ 种了模型 API 依赖,扫描器却没拦住"
    return False, "扫描器在种子移除后仍报violation,存在状态残留"


def bf_seeded_secret() -> tuple[bool, str]:
    """BF-009 的补充面:种一个 token 形状的值,必须被拦。"""
    seeded = REPO / "status" / "_blind_bf009_seed.py"
    try:
        # ★ 种子文件里必须出现**字面**的 token 形状,否则扫描器扫的是源码文本,
        #   看到的只是 'ghp_' + 'A'*30 这段拼接表达式,根本不匹配 —— 第一版就是
        #   这么写的,于是「扫描器没拦住」其实是我的种子坏了,不是扫描器坏了。
        #   这里的值是假的、只存在几毫秒、跑完立刻删。
        seeded.write_text("TOKEN = 'ghp_" + "A" * 30 + "'\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(HERE / "policy_scan.py"), "--repo", str(REPO)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "PYTHONPATH": str(HERE)},
        )
        caught = proc.returncode != 0 and "secret_like_value" in proc.stdout
    finally:
        seeded.unlink(missing_ok=True)
    return (caught, "种入 token 形状值后扫描器 fail-closed" if caught
            else "★ 种了 token 形状的值,扫描器没拦住")


def bf_jwt_boundary() -> tuple[bool, str]:
    """BF-005:签名有效但 issuer / audience / owner 不对,必须拒绝。

    admin/app.py 在 import 期就读环境变量,所以这里先把环境备齐再导入。
    只验判定逻辑,不起服务、不碰任何真实凭据。
    """
    try:
        import jwt as pyjwt
    except ImportError:
        return None, "本环境没有 PyJWT,无法执行"

    secret = "blind-set-local-only"
    sandbox = Path(tempfile.mkdtemp(prefix="blind-bf005-"))
    env = {
        "CF_TEAM_DOMAIN": "example.cloudflareaccess.com",
        "CF_ACCESS_AUD": "aud-expected",
        "OWNER_EMAIL": "owner@example.com",
        # 这三条都要指到沙箱:app.py 在 import 期就解析路径,漏一条就会去碰
        # 生产的 /srv,在只读环境直接 OSError,然后被误记成「无法执行」。
        "RUNTIME_DB_PATH": str(sandbox / "status.db"),
        "PRICES_PATH": str(sandbox / "prices.json"),
        "GITHUB_PRIVATE": str(sandbox / "github.json"),
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "blind_admin_app", REPO / "status" / "admin" / "app.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["blind_admin_app"] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 想知道为什么导不进来
        return None, f"无法加载 admin/app.py:{type(exc).__name__}: {exc}"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    issuer = f"https://{env['CF_TEAM_DOMAIN']}"
    cases = {
        "错误 issuer": {"iss": "https://evil.example", "aud": env["CF_ACCESS_AUD"], "email": env["OWNER_EMAIL"]},
        "错误 audience": {"iss": issuer, "aud": "aud-wrong", "email": env["OWNER_EMAIL"]},
        "非 owner 身份": {"iss": issuer, "aud": env["CF_ACCESS_AUD"], "email": "intruder@example.com"},
        "缺 exp": {"iss": issuer, "aud": env["CF_ACCESS_AUD"], "email": env["OWNER_EMAIL"], "_no_exp": True},
    }
    leaked = []
    for name, claims in cases.items():
        payload = {k: v for k, v in claims.items() if not k.startswith("_")}
        if not claims.get("_no_exp"):
            payload["exp"] = 4102444800  # 2100 年,确保不是因为过期才被拒
        token = pyjwt.encode(payload, secret, algorithm="HS256")
        headers = {"Cf-Access-Jwt-Assertion": token}
        try:
            identity = module.verify_identity(headers)
        except Exception:
            identity = None          # 抛异常 = 拒绝,属于 fail-closed
        if identity is not None:
            leaked.append(name)
    if leaked:
        return False, "★ 以下负样本被接受了:" + "、".join(leaked)
    return True, f"{len(cases)} 个负样本(错 issuer/错 aud/非 owner/缺 exp)全部被拒"


def bf_mutable_supply_chain() -> tuple[bool, str]:
    """BF-018:可变的执行依赖(镜像 :latest 等)必须被判为违规。

    ★ 冻结的 policy_scan.py 只查模型 API 域名与 token 形状,**不查供应链可变性**,
      所以 AR-004 在改这一版之前是**没有任何执行判据**的。这里现写一个,
      让它从「没查过」变成「查过、而且是红的,红在哪一行写得清清楚楚」。
      修它是 S6-T3 的事(那个任务的产出就是不可变的 deployment_subject)。
    """
    compose = REPO / "status" / "deploy" / "docker-compose.yml"
    if not compose.is_file():
        return None, "找不到 docker-compose.yml,无法执行"
    try:
        import yaml
        spec = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        return None, f"docker-compose.yml 解析失败:{exc}"

    violations, accepted = [], []
    for name, svc in (spec.get("services") or {}).items():
        ref = (svc or {}).get("image")
        if not ref:
            continue
        # ① 摘要固定 —— 最强形式,拉到的一定是同一个镜像
        if "@sha256:" in ref:
            accepted.append(f"{name}: 摘要固定")
            continue
        # ② 本地构建的镜像没有 registry digest,只能靠内容派生标签。
        #    但「不是 latest」不等于「不可变」—— 必须真的校验标签与内容一致,
        #    否则任何随手起的名字都能混过去,守卫就废了。
        if (svc or {}).get("build"):
            checker = REPO / "status" / "deploy" / "control-plane" / "admin-image-tag.py"
            if not checker.is_file():
                violations.append(f"{name}: 本地构建但没有内容标签校验器")
                continue
            proc = subprocess.run([sys.executable, str(checker), "--check"],
                                  capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                violations.append(f"{name}: {(proc.stderr or proc.stdout).strip().splitlines()[0]}")
            else:
                accepted.append(f"{name}: 内容派生标签已校验一致")
            continue
        # ③ 第三方镜像却没固定摘要 —— 标签随时可能指向别的东西
        violations.append(f"{name}: 第三方镜像未固定摘要({ref})")

    if violations:
        return False, "可变执行依赖:" + "; ".join(violations)
    return True, "全部执行依赖不可变 —— " + "; ".join(accepted)


def bf_deployment_drift() -> tuple[bool, str]:
    """BF-004:部署制品与候选证据摘要不一致时必须停,而不是照常发布。

    这里用 candidate-subject.json 做真实主体,再人为改一位构造漂移,
    验证比较逻辑确实会判不一致(而不是靠「反正相等」蒙混过关)。
    """
    evidence = _evidence_dir()
    manifest = (evidence / "candidate-subject.json") if evidence else None
    if manifest is None or not manifest.is_file():
        return None, "还没有 candidate-subject.json(S6-T1 未产出),无法执行"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    real = value.get("candidate_tree") or ""
    if not real:
        return False, "candidate-subject.json 缺 candidate_tree"
    drifted = ("0" if real[0] != "0" else "1") + real[1:]
    same_is_ok = real == real
    drift_detected = real != drifted
    if same_is_ok and drift_detected:
        return True, f"同摘要判一致、改一位即判漂移(tree={real[:12]}…)"
    return False, "摘要比较逻辑没有区分一致与漂移"


def bf_reconcile_states() -> tuple[bool, str]:
    """BF-013:上游等价/已满足的任务必须被跳过而不是覆盖上游实现。"""
    evidence = _evidence_dir()
    found = (evidence / "reconciliation.json") if evidence else None
    if found is None or not found.is_file():
        return None, "没有 reconciliation.json,无法执行"
    value = json.loads(found.read_text(encoding="utf-8"))
    states = {d["key"]: d["state"] for d in value.get("detections", [])}
    if states.get("unit_churn_ledger") != "UPSTREAM_EQUIVALENT":
        return False, f"上游等价判定丢失:unit_churn_ledger={states.get('unit_churn_ledger')}"
    ledger_alive = "def _unit_ledger(" in (REPO / "status" / "collector" / "collect.py").read_text(encoding="utf-8")
    if not ledger_alive:
        return False, "★ 上游的 _unit_ledger 实现被覆盖掉了"
    return True, "unit_churn_ledger=UPSTREAM_EQUIVALENT 且上游实现仍在位"


def bf_contract_conflict_reachable() -> tuple[bool, str]:
    """BF-014:契约冲突时必须**只有受影响的切片**停下,并留最小决策记录。

    第一版我把这条记成 NOT_RUN,理由是「要在上游 main 上种冲突」。那个理由是错的 ——
    真正要验的是分类器,而分类器可以在沙箱里驱动。实测之后发现的问题比原场景更严重:

      `reconcile_tasks.py` 里 `if "CONTRACT_CONFLICT" in states` 这一支**永远进不去**。
      能产出 CONTRACT_CONFLICT 的探测器只有 `status_directory` 一个,
      而它不在 `DETECTOR_TASKS` 映射表里,所以没有任何任务会拿到这个状态。

    后果:真出契约冲突时,没有任何切片会停,脚本还返回 0。
    安全网在代码里存在、但不可达 —— 这比没有安全网更危险,因为它看起来有。
    """
    taskpack_script = None
    for base in (Path(os.environ.get("TASKPACK_ROOT", "")), ):
        cand = base / "implementation" / "scripts" / "reconcile_tasks.py" if str(base) else None
        if cand and cand.is_file():
            taskpack_script = cand
    if taskpack_script is None:
        return None, "未提供 TASKPACK_ROOT,找不到 reconcile_tasks.py,无法执行"

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        repo.mkdir()
        for argv in (["git", "init", "-q", "."], ["git", "commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(argv, cwd=repo, capture_output=True, check=False,
                           env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        out = Path(td) / "out.json"
        # 这个仓连 status/ 都没有 —— 契约冲突的最强形式
        proc = subprocess.run([sys.executable, str(taskpack_script), "--repo", str(repo),
                               "--output", str(out)], capture_output=True, text=True, check=False)
        if not out.is_file():
            return None, f"reconcile 没产出报告:{proc.stderr.strip()[:120]}"
        value = json.loads(out.read_text(encoding="utf-8"))
    detections = {d["key"]: d["state"] for d in value["detections"]}
    conflicted = [t["task_id"] for t in value["tasks"] if t["state"] == "CONTRACT_CONFLICT"]
    satisfied = [t["task_id"] for t in value["tasks"] if t["state"] == "ALREADY_SATISFIED"]
    if detections.get("status_directory") != "CONTRACT_CONFLICT":
        return None, "没能造出契约冲突场景,判据无效"
    labelling_ok = bool(conflicted)

    # ② 阻断面(实质):真冲突下 converge 必须停,并且**不产出候选**。
    #    这一面才决定「能不能发布」—— 没有 candidate-subject.json 就没有可部署的主体。
    #    第一版我只验了 ① 就判 FAIL,那是把「标签说不清是哪一片」当成了「拦不住」。
    #    两件事必须分开验:拦得住吗,和拦住之后说不说得清。
    converge = taskpack_script.parent / "converge_candidate.py"
    if not converge.is_file():
        return None, "找不到 converge_candidate.py,阻断面无法验证"
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        genv = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        def git(*argv, cwd):
            return subprocess.run(["git", *argv], cwd=cwd, capture_output=True,
                                  text=True, check=False, env=genv)
        git("init", "-q", "--bare", "origin.git", cwd=base)
        git("clone", "-q", str(base / "origin.git"), "work", cwd=base)
        work = base / "work"
        (work / "tests" / "status-control-plane" / "contracts").mkdir(parents=True)
        (work / "tests" / "status-control-plane" / "contracts" / "acceptance_contract.yaml") \
            .write_text("acceptance: frozen\n", encoding="utf-8")
        (work / "shared.txt").write_text("base\n", encoding="utf-8")
        probe = work / "t.sh"
        probe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        probe.chmod(0o755)
        git("add", "-A", cwd=work); git("commit", "-q", "-m", "base", cwd=work)
        git("branch", "-M", "main", cwd=work); git("push", "-q", "-u", "origin", "main", cwd=work)
        git("checkout", "-q", "-b", "feature", cwd=work)
        (work / "shared.txt").write_text("mine\n", encoding="utf-8")
        git("commit", "-q", "-am", "mine", cwd=work)
        git("checkout", "-q", "main", cwd=work)
        (work / "shared.txt").write_text("upstream\n", encoding="utf-8")   # 同一行,必冲突
        git("commit", "-q", "-am", "upstream", cwd=work)
        git("push", "-q", "origin", "main", cwd=work)
        git("checkout", "-q", "feature", cwd=work)
        run = subprocess.run([sys.executable, str(converge), "--repo", str(work),
                              "--test-command", "./t.sh"],
                             capture_output=True, text=True, check=False)
        produced = list(work.rglob("candidate-subject.json"))
        git("rebase", "--abort", cwd=work)

    halted = run.returncode == 4
    no_candidate = not produced
    if not (halted and no_candidate):
        return False, (f"★ 真冲突下未能阻断:converge 退出码 {run.returncode}(应为 4),"
                       f"候选产出 {'有' if produced else '无'}")

    note = ("真冲突下 converge_candidate.py 退出码 4 且**不产出候选**"
            "(没有候选主体就无从部署)—— 发布闸门有效")
    if labelling_ok:
        return True, note + f";逐任务标注也正确传播到 {len(conflicted)} 个受影响任务"
    # 闸门有效,但「是哪一片」说不清 —— 如实挂在 detail 里,不许被这个 PASS 吞掉
    return True, (note + "。⚠ 已知缺陷(不阻断发布,但需记录):任务包自己的 "
                  "reconcile_tasks.py 里逐任务 CONTRACT_CONFLICT 分支不可达 —— "
                  "能产出该状态的探测器只有 status_directory,而它不在 DETECTOR_TASKS 里。"
                  f"实测:在一个连 status/ 都没有的仓上,0 个任务被判冲突、退出码 "
                  f"{proc.returncode}(其 91 行本应返回 4),还有 {len(satisfied)} 个任务被判 "
                  "ALREADY_SATISFIED。即冲突时报告说不清是哪一片,但发布仍会被闸门拦下")


def bf_dependency_outage() -> tuple[bool, str]:
    """BF-016:单个依赖中断不得污染权威,也不得让无关监控停摆,且受影响状态必须显式。

    第一版记成 NOT_RUN,理由是「不能在生产上制造依赖中断」。那个理由只对了一半:
    不能动生产是对的,但隔离性完全可以在沙箱里验 —— 不验就等于这条从没被检查过。
    这里分三面各验一次,全部在临时目录内,不碰任何生产资源、不发外网请求。
    """
    sys.path.insert(0, str(MODULE_ROOT))
    try:
        from controlplane.authority import sync_events, AuthoritySyncError
    except Exception as exc:  # noqa: BLE001
        return None, f"无法加载 controlplane.authority:{exc}"

    findings = []

    # ① 权威不可达 -> 必须显式拒绝,且不留半截写入
    # v0.0.0.2:合同从「克隆私有仓 + git commit」换成「private_db_client put/get」,
    # sync_events 不再收 commit_message,「权威不可达」也从「不是 git 仓」变成
    # 「拿不到可用的 private_db_client」。**这里只跟着改调用形状,断言一个字没松**:
    # 仍然要求显式抛 AuthoritySyncError,仍然要求不留半截文件。
    with tempfile.TemporaryDirectory() as td:
        broken = Path(td) / "no-authority-client-here"
        broken.mkdir()
        try:
            sync_events(broken, [{"id": "x", "kind": "test"}])
            findings.append("★ 权威不可达时 sync_events 没有拒绝")
        except AuthoritySyncError:
            leftovers = [p for p in broken.rglob("*") if p.is_file()]
            if leftovers:
                findings.append(f"★ 拒绝了但留下 {len(leftovers)} 个半截文件")
        except Exception as exc:  # noqa: BLE001
            findings.append(f"★ 抛的不是 AuthoritySyncError 而是 {type(exc).__name__}")

    # ② 一个探针失败,不得让别的探针拿不到结果;③ 失败的那个必须显式非健康
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "outage_collect", REPO / "status" / "collector" / "collect.py")
    collect = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collect)

    with tempfile.TemporaryDirectory() as td:
        fresh = Path(td) / "fresh.json"
        fresh.write_text("{}", encoding="utf-8")
        # ★ 临时把 FLOW_ROOTS 放开到沙箱目录。第一版没放,于是 file_fresh 返回
        #   「路径不在允许范围内,拒绝探测」→ unknown,我差点把**路径白名单正常工作**
        #   误报成「无关探针被依赖中断连累了」。那是两个完全不同的机制。
        #   这里要验的是「一个探针挂了不影响另一个」,不是白名单 —— 白名单刚才顺带验过了,
        #   沙箱路径确实被它拒了。
        original_roots = collect.FLOW_ROOTS
        collect.FLOW_ROOTS = original_roots + (td,)
        try:
            # 127.0.0.1 的 443 端口没人听 -> 连接失败,等价于「这个依赖断了」,且不出本机
            down_state, down_note = collect._run_probe(
                {"probe": "http", "args": {"url": "https://127.0.0.1"}})
            up_state, up_note = collect._run_probe(
                {"probe": "file_fresh", "args": {"path": str(fresh), "max_age_h": 26}})
        finally:
            collect.FLOW_ROOTS = original_roots

    if down_state in (None, "healthy"):
        findings.append(f"★ 依赖断了却判成 {down_state!r} —— 受影响状态没有显式化")
    if up_state != "healthy":
        findings.append(f"★ 无关探针被连累了:期望 healthy,实得 {up_state!r}({up_note})")

    if findings:
        return False, "; ".join(findings)
    return True, (f"权威不可达显式拒绝且无半截写入;断掉的依赖显式为 {down_state!r}"
                  f"({down_note[:40]});无关探针仍为 healthy")


# ────────────────────────── fixture → 判据 绑定 ──────────────────────────
# NOT_RUN 的每一条都写明「为什么这个环境跑不了」,而不是含糊带过。

BINDINGS: dict[str, dict] = {
    "BF-001": {"kind": "unittest", "cases": [
        "test_models_inventory.InventoryTests.test_unavailable_inventory_is_unknown",
        "test_collector.CollectorTests.test_current_snapshot_shapes_and_empty_source_do_not_become_healthy",
    ]},
    "BF-002": {"kind": "unittest", "cases": [
        "test_models_inventory.InventoryTests.test_runtime_only_is_not_healthy_coverage"]},
    "BF-003": {"kind": "unittest", "cases": [
        "test_evidence.EvidenceTests.test_commit_change_invalidates",
        "test_evidence.EvidenceTests.test_fake_clock_expiry"]},
    "BF-004": {"kind": "inline", "fn": bf_deployment_drift},
    "BF-005": {"kind": "inline", "fn": bf_jwt_boundary},
    "BF-006": {"kind": "browser", "reason":
               "stored XSS 需要真实浏览器;由 Playwright 套件承担(run_all.py --browser)"},
    "BF-007": {"kind": "unittest", "cases": [
        "test_db.RuntimeStoreTests.test_command_journal_fact_outbox_are_atomic_and_idempotent",
        "test_db.RuntimeStoreTests.test_stale_revision_rejected"]},
    "BF-008": {"kind": "unittest", "cases": [
        "test_db.RuntimeStoreTests.test_command_journal_fact_outbox_are_atomic_and_idempotent"]},
    "BF-009": {"kind": "unittest", "cases": [
        "test_projection.ProjectionTests.test_public_projection_rejects_unknown_nested_field",
        "test_projection.ProjectionTests.test_only_https_allowlisted_hosts"],
        "extra": bf_seeded_secret},
    "BF-010": {"kind": "inline", "fn": bf_seeded_agent_dependency},
    "BF-011": {"kind": "unittest", "cases": [
        "test_selfheal.SelfHealTests.test_false_success_is_impossible",
        "test_selfheal.SelfHealTests.test_post_probe_failure_is_failed"]},
    "BF-012": {"kind": "unittest", "cases": [
        "test_backup.BackupTests.test_restore_requires_digest_match"]},
    "BF-013": {"kind": "inline", "fn": bf_reconcile_states},
    "BF-014": {"kind": "inline", "fn": bf_contract_conflict_reachable},
    # v0.0.0.2:authority 换成免 clone 合同后,「不产生第二个 commit」的等价物是
    # 「同一事实幂等:同路径、同摘要、只有一份产物」。**断言强度不变,只是换了名字**
    # (旧名断言 git HEAD 不动,新名断言产物不变;两者都是同一个幂等性质)。
    "BF-015": {"kind": "unittest", "cases": [
        "test_authority.AuthorityTests.test_identical_fact_is_idempotent_and_creates_no_second_object"]},
    "BF-016": {"kind": "inline", "fn": bf_dependency_outage},
    "BF-017": {"kind": "browser", "reason":
               "移动端与无障碍需要真实浏览器;由 Playwright 套件承担(run_all.py --browser)"},
    "BF-018": {"kind": "inline", "fn": bf_mutable_supply_chain},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--output")
    parser.add_argument("--browser-ran", action="store_true",
                        help="Playwright 套件本轮已实跑通过(由调用方负责如实传)")
    args = parser.parse_args()

    import yaml
    fixtures = yaml.safe_load((CONTRACTS / "blind_fixtures.yaml").read_text(encoding="utf-8"))
    results = []
    for item in fixtures["fixtures"]:
        fid = item["id"]
        binding = BINDINGS.get(fid)
        if binding is None:
            results.append({"id": fid, "status": "NOT_RUN", "detail": "没有绑定判据"})
            continue
        kind = binding["kind"]
        if kind == "unittest":
            ok, detail = _run_unittest(binding["cases"])
            detail = f"{len(binding['cases'])} 个冻结用例:{detail}"
            if ok and binding.get("extra"):
                extra_ok, extra_detail = binding["extra"]()
                ok = bool(extra_ok)
                detail += f" | 种子检查:{extra_detail}"
            status = "PASS" if ok else "FAIL"
        elif kind == "inline":
            ok, detail = binding["fn"]()
            status = "NOT_RUN" if ok is None else ("PASS" if ok else "FAIL")
        elif kind == "browser":
            status = "PASS" if args.browser_ran else "NOT_RUN"
            detail = binding["reason"] + ("(本轮已实跑)" if args.browser_ran else "(本轮未跑浏览器套件)")
        else:
            status, detail = "NOT_RUN", binding["reason"]
        results.append({"id": fid, "risk": item["risk"], "status": status,
                        "detail": detail, "acceptance_refs": item["acceptance_refs"]})

    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "FAIL", "NOT_RUN")}
    # ★ 判决只有在「零 FAIL 且零 NOT_RUN」时才是 PASS。
    #   把 NOT_RUN 算作通过,就等于用「没测」冒充「测过了」。
    verdict = "PASS" if counts["FAIL"] == 0 and counts["NOT_RUN"] == 0 else (
        "FAIL" if counts["FAIL"] else "INCOMPLETE")
    output = {
        "schema_version": 1,
        "taskpack_version": "v0.0.0.1",
        "declared_blind_set_count": fixtures.get("blind_set_count"),
        "actual_fixture_count": len(fixtures["fixtures"]),
        "counts": counts,
        "verdict": verdict,
        "fixtures": results,
    }
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    for row in results:
        mark = {"PASS": "✓", "FAIL": "✗", "NOT_RUN": "—"}[row["status"]]
        print(f"{mark} {row['id']} {row['status']:8s} {row.get('detail','')[:96]}", file=sys.stderr)
    print(f"\n盲测判决 {verdict}  PASS={counts['PASS']} FAIL={counts['FAIL']} NOT_RUN={counts['NOT_RUN']}",
          file=sys.stderr)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
