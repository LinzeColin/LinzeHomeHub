"""authority 的合同已从「克隆私有仓 + git commit」换成「private_db_client put/get + 逐字节 readback」。

这个文件原本断言的是旧合同(脏工作树被拒、相同事实不产生第二个 commit)。
新合同下 **任何 git 传输都是违规**(AC-002 / OR-NO-CLONE),所以旧断言不是「过时」而是「反向」。
这里把旧的两条**意图**平移到新合同,并补上旧套件没有的三条 —— 覆盖只增不减:

  旧「脏工作树必须被拒」      → 新「配置非法必须 fail-closed,绝不静默成功」
  旧「相同事实不产生第二提交」→ 新「相同事实幂等:同路径同摘要,不产生第二份产物」
  新增「readback 摘要不一致 = 失败」          (AC-003:写后逐字节一致)
  新增「部分失败必须隔离,不污染成功项」        (RISK: partial-success)
  新增「空事实集 = NO_NEW_FACT,不产生空提交」  (AC-003:无空提交)
"""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_support import locate
locate()

from controlplane.authority import AuthoritySyncError, sync_events, validate_client_contract


class FakeClient:
    """把 private_db_client.py 的 put/get 替换成内存存储,不碰网络也不碰 git。"""

    def __init__(self, *, corrupt_readback=False, fail_put_for=()):
        self.objects = {}
        self.commands = []
        self.corrupt_readback = corrupt_readback
        self.fail_put_for = tuple(fail_put_for)

    def __call__(self, command, **kwargs):
        args = command[2:]
        self.commands.append(list(args))
        if args == ["--help"]:
            return subprocess.CompletedProcess(command, 0, "commands: put get", "")
        if args[0] == "put":
            area, relative, source = args[1], args[2], args[3]
            if any(marker in relative for marker in self.fail_put_for):
                return subprocess.CompletedProcess(command, 3, "", "put rejected")
            self.objects[(area, relative)] = Path(source).read_bytes()
            return subprocess.CompletedProcess(command, 0, "ok", "")
        if args[0] == "get":
            area, relative, target = args[1], args[2], args[3]
            payload = self.objects[(area, relative)]
            Path(target).write_bytes(b"tampered\n" if self.corrupt_readback else payload)
            return subprocess.CompletedProcess(command, 0, "ok", "")
        return subprocess.CompletedProcess(command, 2, "", "unsupported")


def _event(event_id, **extra):
    base = {
        "event_id": event_id,
        "fact_type": "status.release",
        "completed_at": "2026-07-27T00:00:00+00:00",
        "payload": {"release": "v1"},
    }
    base.update(extra)
    return base


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = Path(self.temp.name) / "private_db_client.py"
        self.client.write_text("print()\n", encoding="utf-8")
        self.event = _event("event:1")

    def tearDown(self):
        self.temp.cleanup()

    def test_invalid_authority_config_is_rejected(self):
        """旧「脏工作树被拒」的等价物:配置不成立时必须抛错,不能静默当成写成功。"""
        fake = FakeClient()
        missing = Path(self.temp.name) / "nope" / "private_db_client.py"
        with self.assertRaises(AuthoritySyncError):
            sync_events(missing, [self.event], runner=fake)
        with self.assertRaises(AuthoritySyncError):
            sync_events(self.client, [self.event], area="Public-Whatever", runner=fake)
        self.assertEqual(fake.objects, {}, "配置非法却已经写出去了")

    def test_identical_fact_is_idempotent_and_creates_no_second_object(self):
        """旧「相同事实不产生第二个 commit」的等价物:同事实落同一路径、同摘要、只有一份产物。"""
        fake = FakeClient()
        first = sync_events(self.client, [self.event], runner=fake)
        after_first = dict(fake.objects)
        second = sync_events(self.client, [self.event], runner=fake)

        self.assertEqual(first["state"], "SYNCED")
        self.assertEqual(second["state"], "SYNCED")
        self.assertEqual(first["items"][0]["relative_path"], second["items"][0]["relative_path"])
        self.assertEqual(first["items"][0]["payload_sha256"], second["items"][0]["payload_sha256"])
        self.assertEqual(fake.objects, after_first, "同一事实写了第二遍,产物却变了")
        self.assertEqual(len(fake.objects), 1, "同一事实产生了第二份产物")

    def test_empty_event_set_is_no_new_fact(self):
        fake = FakeClient()
        result = sync_events(self.client, [], runner=fake)
        self.assertEqual(result["state"], "NO_NEW_FACT")
        self.assertEqual(result["sent_event_ids"], [])
        self.assertEqual(fake.objects, {}, "没有新事实却写了东西")

    def test_readback_mismatch_is_failure_not_success(self):
        """AC-003:写后逐字节一致。readback 对不上就必须是失败,不能记成已送达。"""
        fake = FakeClient(corrupt_readback=True)
        result = sync_events(self.client, [self.event], runner=fake)
        self.assertEqual(result["state"], "FAILED")
        self.assertEqual(result["sent_event_ids"], [])
        self.assertEqual(result["failed_event_ids"], ["event:1"])
        self.assertEqual(result["items"][0]["error_code"], "AUTHORITY_READBACK_FAILED")

    def test_partial_failure_isolates_the_bad_event(self):
        """一条失败不得把另一条也拖成失败,也不得让整批假装成功。"""
        fake = FakeClient(fail_put_for=("event-bad",))
        result = sync_events(self.client, [_event("event-good"), _event("event-bad")], runner=fake)
        self.assertEqual(result["state"], "PARTIAL_FAILURE")
        self.assertEqual(result["sent_event_ids"], ["event-good"])
        self.assertEqual(result["failed_event_ids"], ["event-bad"])

    def test_client_without_put_verb_is_rejected_even_if_help_says_output(self):
        """★ 破坏测试:`--output` 里含 "put"。子串匹配会把「写不进去的客户端」判成合格。

        实测过的假绿:一个只有 {ingest,get,list}、帮助里有 `--output` 的客户端,
        旧实现给出 CLIENT_CONTRACT_VERIFIED / doctor state=PASS。
        权威写入的第一道门如果能被一个标志位骗过,后面所有 readback 证据都建在沙上。
        """

        def help_with_output(command, **kwargs):
            return subprocess.CompletedProcess(
                command, 0,
                "usage: fake.py {ingest,get,list} [--output PATH]\n  --output PATH  where to write\n", "")

        with self.assertRaises(AuthoritySyncError):
            validate_client_contract(self.client, runner=help_with_output)

    def test_client_declaring_real_put_and_get_is_accepted(self):
        def help_with_verbs(command, **kwargs):
            return subprocess.CompletedProcess(
                command, 0, "commands: put get ingest list verify [--output PATH]\n", "")

        verified = validate_client_contract(self.client, runner=help_with_verbs)
        self.assertEqual(verified["state"], "CLIENT_CONTRACT_VERIFIED")
        self.assertEqual(verified["commands"], {"put": True, "get": True})

    def test_no_git_transport_is_used(self):
        """AC-002 / OR-NO-CLONE:整条写入路径不得出现任何 git 子命令。"""
        fake = FakeClient()
        sync_events(self.client, [self.event], runner=fake)
        for command in fake.commands:
            self.assertNotIn("git", command, f"写入路径里出现了 git:{command}")


if __name__ == "__main__":
    unittest.main()
