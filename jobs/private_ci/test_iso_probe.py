#!/usr/bin/env python3
"""🧪 مسبارُ المقابس (الإصلاح الخامس · مراجعة Codex 14 «مهم»): الجردُ يفشل مغلقاً، والاتّصالُ يُصنَّف.

يُختبر بـ/proc مزيَّفٍ (مجلّدٌ مؤقّتٌ بملفّات status وروابطِ fd ونسخةٍ من net/unix) ومقابسَ مؤقّتة:
· المجرّدةُ المُنصِتة **وdatagram** التي تمسكها عمليّةٌ بالحساب تُجرد، وغيرُ المُنصِتة من stream لا؛
· كلُّ نقصٍ يُعلَن: بلوغُ الحدّ · خطأُ مجلّدٍ (onerror) · تعذّرُ net/unix · تعذّرُ fd لعمليّةٍ حيّة — وزوالُ العمليّة سباقٌ لا نقص؛
· scan يكتب سجلَّ الحالة أخيراً ويخرج 3 عند النقص؛ وconnect يصنّف (accepted · denied · refused · vanished · other)
  ولا ينجح إلا بجردٍ تامٍّ وبلا accepted/refused/other — ولا يطبع اسماً ولا مساراً؛
· deny --exists لا يقبل الغياب."""
import errno
import io
import os
import socket
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import iso_probe as ip  # noqa: E402

UID = 4242
COMPLETE = "!inventory=complete truncated=0 walk_errors=0 unix_error=0 fd_errors=0 fd_vanished=0"
HDR = "Num       RefCount Protocol Flags    Type St Inode Path\n"


def unix_line(inode, path, flags="00010000", typ="0001"):
    return f"0000000000000000: 00000002 00000000 {flags} {typ} 01 {inode} {path}\n"


class FakeProc:
    """‏/proc مزيَّف: pid ⇒ (uid، [inode…])؛ وnet/unix بأسطرٍ تُعطى."""

    def __init__(self, root):
        self.root = root
        os.makedirs(os.path.join(root, "net"))

    def pid(self, pid, uid, inodes=(), fd_ok=True, status_ok=True):
        d = os.path.join(self.root, str(pid))
        os.makedirs(d)
        if status_ok:
            with open(os.path.join(d, "status"), "w") as f:
                f.write(f"Name:\tx\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        else:
            os.makedirs(os.path.join(d, "status"))           # status مجلّدٌ ⇒ تعذّرُ القراءة والعمليّةُ حيّة
        if fd_ok:
            os.makedirs(os.path.join(d, "fd"))
            for i, ino in enumerate(inodes):
                os.symlink(f"socket:[{ino}]", os.path.join(d, "fd", str(i + 3)))
            os.symlink("/dev/null", os.path.join(d, "fd", "0"))
        else:
            open(os.path.join(d, "fd"), "w").close()         # fd ملفٌّ لا مجلّد ⇒ تعذّرُ القراءة والعمليّةُ حيّة

    def vanished(self, pid):
        os.symlink(os.path.join(self.root, "gone-forever"), os.path.join(self.root, str(pid)))   # يُسرد ولا يوجد

    def unix(self, lines):
        with open(os.path.join(self.root, "net", "unix"), "w") as f:
            f.write(HDR + "".join(lines))


class AbstractInventoryTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.p = FakeProc(self.d.name)

    def test_listening_and_datagram_held_are_found_non_listening_stream_and_unheld_are_not(self):
        self.p.pid(10, UID, inodes=(101, 102, 103))
        self.p.pid(11, UID + 1, inodes=(104,))
        self.p.unix([unix_line(101, "@listen"), unix_line(102, "@dgram", flags="00000000", typ="0002"),
                     unix_line(103, "@stream-client", flags="00000000"), unix_line(104, "@other-account"),
                     unix_line(105, "/run/fs.sock"), unix_line(106, "@unheld")])
        inv = ip.Inventory()
        self.assertEqual(ip.abstract_candidates(UID, proc=self.d.name, inv=inv), ["@dgram", "@listen"])
        self.assertTrue(inv.complete, inv.status())

    def test_unreadable_fd_of_a_live_process_makes_the_inventory_incomplete(self):
        self.p.pid(10, UID, fd_ok=False)
        self.p.unix([])
        inv = ip.Inventory()
        ip.abstract_candidates(UID, proc=self.d.name, inv=inv)
        self.assertEqual((inv.fd_errors, inv.complete), (1, False))

    def test_unreadable_status_of_a_live_process_is_incomplete(self):
        self.p.pid(10, UID, status_ok=False)
        self.p.unix([])
        inv = ip.Inventory()
        ip.abstract_candidates(UID, proc=self.d.name, inv=inv)
        self.assertFalse(inv.complete)

    def test_unreadable_fd_of_another_account_is_irrelevant(self):
        self.p.pid(10, UID + 7, fd_ok=False)
        self.p.unix([])
        inv = ip.Inventory()
        ip.abstract_candidates(UID, proc=self.d.name, inv=inv)
        self.assertTrue(inv.complete)

    def test_vanished_process_is_a_race_not_a_gap(self):
        self.p.vanished(12)
        self.p.unix([])
        inv = ip.Inventory()
        ip.abstract_candidates(UID, proc=self.d.name, inv=inv)
        self.assertEqual((inv.fd_vanished, inv.fd_errors, inv.complete), (1, 0, True))

    def test_missing_net_unix_is_incomplete(self):
        self.p.pid(10, UID, inodes=(101,))
        inv = ip.Inventory()
        self.assertEqual(ip.abstract_candidates(UID, proc=self.d.name, inv=inv), [])
        self.assertEqual((inv.unix_error, inv.complete), (True, False))

    def test_garbled_net_unix_line_is_incomplete(self):
        self.p.pid(10, UID, inodes=(101,))
        self.p.unix(["0000: 00000002 00000000 ZZZZ 0001 01 101 @x\n"])
        inv = ip.Inventory()
        ip.abstract_candidates(UID, proc=self.d.name, inv=inv)
        self.assertFalse(inv.complete)

    def test_real_abstract_datagram_held_by_this_process_is_found(self):
        name = "pci-dgram-test-%d" % os.getpid()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.bind("\0" + name)
        self.addCleanup(s.close)
        self.assertIn("@" + name, ip.abstract_candidates(os.getuid()))
        ok, _ = ip.try_connect("@" + name)
        self.assertTrue(ok, "datagram مجرّدٌ يقبل الإرسالَ بلا صلاحيّة ملفّات — هذا ما يجب أن يُكشف")


class FsInventoryTest(unittest.TestCase):
    def test_hitting_the_entry_limit_is_truncation_not_a_partial_success(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(30):
                open(os.path.join(d, f"f{i}"), "w").close()
            inv = ip.Inventory()
            ip.fs_candidates(os.getuid(), set(), set(), roots=(d,), limit=10, inv=inv)
            self.assertEqual((inv.truncated, inv.complete), (True, False))
            inv2 = ip.Inventory()
            ip.fs_candidates(os.getuid(), set(), set(), roots=(d,), limit=100, inv=inv2)
            self.assertTrue(inv2.complete)

    def test_walk_error_is_counted_and_vanished_dir_is_not(self):
        real_walk = ip.os.walk

        def fake_walk(top, followlinks=False, onerror=None):
            onerror(PermissionError(errno.EACCES, "x"))
            onerror(FileNotFoundError(errno.ENOENT, "x"))
            return real_walk(top, followlinks=followlinks, onerror=onerror)

        with tempfile.TemporaryDirectory() as d:
            ip.os.walk = fake_walk
            try:
                inv = ip.Inventory()
                ip.fs_candidates(os.getuid(), set(), set(), roots=(d,), inv=inv)
            finally:
                ip.os.walk = real_walk
            self.assertEqual((inv.walk_errors, inv.complete), (1, False))

    def test_absent_root_is_not_a_gap(self):
        inv = ip.Inventory()
        self.assertEqual(ip.fs_candidates(os.getuid(), set(), set(), roots=("/nonexistent-pci-root",), inv=inv), [])
        self.assertTrue(inv.complete)


class ScanTest(unittest.TestCase):
    def test_scan_writes_status_last_and_exits_3_when_incomplete(self):
        with tempfile.TemporaryDirectory() as d:
            p = FakeProc(os.path.join(d, "proc"))
            p.pid(10, UID, inodes=(101,))
            p.unix([unix_line(101, "@held")])
            root = os.path.join(d, "root"); os.makedirs(root)
            out = io.BytesIO()
            self.assertEqual(ip.cmd_scan(["--uid", str(UID)], out=out, proc=p.root, roots=(root,)), 0)
            recs = out.getvalue().split(b"\0")
            self.assertEqual(recs[-1], b"")
            self.assertEqual(recs[-2].decode(), COMPLETE)
            self.assertIn(b"@held", recs)
            os.remove(os.path.join(p.root, "net", "unix"))
            out = io.BytesIO()
            self.assertEqual(ip.cmd_scan(["--uid", str(UID)], out=out, proc=p.root, roots=(root,)), 3)
            self.assertTrue(out.getvalue().split(b"\0")[-2].startswith(b"!inventory=incomplete "))


class ConnectTest(unittest.TestCase):
    def summary(self, items, status=COMPLETE, results=None):
        data = b"".join(i.encode() + b"\0" for i in items) + ((status.encode() + b"\0") if status else b"")
        results = results or {}
        return ip.connect_summary(data, connector=lambda e: results[e])

    def test_each_class_is_counted(self):
        res = {"/a": (False, errno.EACCES), "/b": (False, errno.EPERM), "/c": (False, errno.ENOENT)}
        self.assertEqual(self.summary(list(res), results=res),
                         ("n=3 accepted=0 denied=2 refused=0 vanished=1 other=0 inventory=complete", 0))
        for code, cls in ((errno.ECONNREFUSED, "refused"), (errno.EAGAIN, "other"), (0, "other"),
                          (errno.ETIMEDOUT, "other")):
            with self.subTest(cls=cls, code=code):
                line, rc = self.summary(["/x"], results={"/x": (False, code)})
                self.assertIn(f"{cls}=1", line)
                self.assertEqual(rc, 1)
        line, rc = self.summary(["/x"], results={"/x": (True, 0)})
        self.assertIn("accepted=1", line)
        self.assertEqual(rc, 1)

    def test_missing_or_incomplete_or_misplaced_status_fails_closed(self):
        res = {"/a": (False, errno.EACCES)}
        self.assertEqual(self.summary(["/a"], status=None, results=res)[1], 1)
        self.assertIn("inventory=incomplete", self.summary(["/a"], status=None, results=res)[0])
        bad = COMPLETE.replace("inventory=complete", "inventory=incomplete").replace("truncated=0", "truncated=1")
        self.assertEqual(self.summary(["/a"], status=bad, results=res)[1], 1)
        self.assertEqual(self.summary(["/a"], status="!inventory=complete", results=res)[1], 1)   # صيغةٌ ناقصة
        data = (COMPLETE + "\0/a\0").encode()                                                       # الحالةُ ليست أخيرة
        self.assertEqual(ip.connect_summary(data, connector=lambda e: res[e])[1], 1)
        two = (COMPLETE + "\0" + COMPLETE + "\0").encode()
        self.assertEqual(ip.connect_summary(two, connector=lambda e: res[e])[1], 1)
        self.assertEqual(self.summary([], results={}), ("n=0 accepted=0 denied=0 refused=0 vanished=0 other=0 inventory=complete", 0))

    def test_real_sockets_end_to_end_without_names(self):
        with tempfile.TemporaryDirectory() as d:
            live = os.path.join(d, "live.sock")
            s = socket.socket(socket.AF_UNIX); s.bind(live); s.listen(4)
            self.addCleanup(s.close)
            dead = os.path.join(d, "dead.sock")
            s2 = socket.socket(socket.AF_UNIX); s2.bind(dead); s2.close()
            data = b"".join(x.encode() + b"\0" for x in (live, dead, os.path.join(d, "none"))) + COMPLETE.encode() + b"\0"
            r = subprocess.run([sys.executable, "-I", "-S", os.path.join(HERE, "iso_probe.py"), "connect"], input=data,
                               capture_output=True)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stdout.decode().strip(), "n=3 accepted=1 denied=0 refused=1 vanished=1 other=0 inventory=complete")
            self.assertNotIn(d.encode(), r.stdout + r.stderr)
            self.assertRegex(r.stdout.decode().strip(), r"^n=\d+( [a-z]+=\d+){5} inventory=[a-z]+$")


class DenyTest(unittest.TestCase):
    def test_exists_flag_refuses_absence(self):
        with tempfile.TemporaryDirectory() as d:
            none = os.path.join(d, "none")
            self.assertEqual(ip.cmd_deny(none), 0)
            self.assertEqual(ip.cmd_deny(none, must_exist=True), 2)
            self.assertEqual(ip.main(["deny", none, "--exists"]), 2)
            self.assertEqual(ip.main(["deny", none, "--other"]), 2)
            live = os.path.join(d, "live.sock")
            s = socket.socket(socket.AF_UNIX); s.bind(live); s.listen(1)
            self.addCleanup(s.close)
            self.assertEqual(ip.cmd_deny(live, must_exist=True), 1)

    @unittest.skipUnless(os.geteuid() != 0, "root يتجاوز أذونات الملفّات — يُختبر الرفضُ بالصلاحية بغير root")
    def test_permission_denied_is_the_only_accepted_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "locked.sock")
            s = socket.socket(socket.AF_UNIX); s.bind(p); s.listen(1)
            self.addCleanup(s.close)
            os.chmod(p, 0)
            self.assertEqual(ip.classify(*ip.try_connect(p)), "denied")
            self.assertEqual(ip.cmd_deny(p, must_exist=True), 0)


if __name__ == "__main__":
    unittest.main()
