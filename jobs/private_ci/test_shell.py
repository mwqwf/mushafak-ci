#!/usr/bin/env python3
"""🧪 سكربتاتُ الصدفة (v3): نحوٌ سليم (bash -n) · رفضُ الاستدعاء من غير private-ci.yml · لا حرفَ إلى السجلّ العلنيّ ·
الاستنساخُ بلا مفتاح يفشل ولا يترك أثراً (وtrap على الإشارة) · الاستنساخُ التجريبيّ من مستودعٍ محلّيّ · زوالُ المفتاح ووكيلِه
(حيث ssh-agent) · التشفيرُ ذهاباً وإياباً بـage · ورفضُ المفتاح الوهميّ لمصدرٍ خاصّ وقبولُ مفتاحٍ حقيقيّ ·
والمشرفُ supervise.sh في الوضع المحلّيّ: prepare ← clone ← أطوار ← finalize ⇒ ready=true ومجلّدٌ بالملفّين بالضبط ·
رمزُ كلّ طورٍ من خروجه الفعليّ · بيئةٌ مسموحةٌ لا غير · بترُ السجلّ عند الحدّ · ولا حرفَ من السجلّ إلى stdout العلنيّ ·
ويُرفض وضعُ الاختبار داخل Actions (أ2-٤) · ومدخلُ التحكّم لا يُستبدل (أ1) · وبقاءُ عمليّةٍ للبناء يمنع قراءةَ شجرته (أ3) ·
والمهلةُ تقتل جلسةَ الطور كلَّها ولا يُنتظر EOF من عمليّةٍ خلفيّة وسقفُ العمليّات مفروض (أ4) · ومسبارُ المقابس (أ2)."""
import base64
import gzip
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artifact_check as ac  # noqa: E402
import counts_schema as cs  # noqa: E402

SCRIPTS = ("clone.sh", "build.sh", "encrypt_log.sh", "supervise.sh")
import iso_probe  # noqa: E402
REF_OK = "mwqwf/mushafak-ci/.github/workflows/private-ci.yml@refs/heads/main"
SHA = "0" * 40


def base_env(tmp, **kw):
    e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "RUNNER_TEMP": tmp,
         "GITHUB_WORKFLOW_REF": REF_OK, "PCI_KIND": "app-test", "PCI_SHA": SHA, "PCI_SOURCE": "private",
         "PCI_LEG": "main", "GITHUB_STEP_SUMMARY": os.path.join(tmp, "PUBLIC_SUMMARY")}
    e.update(kw)
    return e


def sh(script, args, env, timeout=120):
    return subprocess.run(["bash", os.path.join(HERE, script)] + list(args), env=env, capture_output=True,
                          text=True, timeout=timeout)


def readf(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8", "errors": "replace"})) as f:
        return f.read()


def fixture_pubkey(d, placeholder, ident=None):
    """ملفُّ مفتاحٍ عامٍّ للتجربة: مفتاحٌ مولَّدٌ الآن؛ وplaceholder=True يضيف علامةَ PLACEHOLDER-TEST-KEY."""
    if ident is None:
        ident = os.path.join(d, "fx_id.txt")
        subprocess.run(["age-keygen", "-o", ident], check=True, capture_output=True)
    pub = subprocess.run(["age-keygen", "-y", ident], capture_output=True, text=True, check=True).stdout.strip()
    path = os.path.join(d, "fx_pub.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(("# PLACEHOLDER-TEST-KEY — مفتاحُ تجربةٍ وهميّ\n" if placeholder else "# مفتاحُ تجربةٍ حقيقيّ\n") + pub + "\n")
    return path


class SyntaxTest(unittest.TestCase):
    def test_bash_n(self):
        for s in SCRIPTS:
            with self.subTest(s=s):
                r = subprocess.run(["bash", "-n", os.path.join(HERE, s)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_xtrace(self):
        for s in SCRIPTS:
            body = readf(os.path.join(HERE, s))
            self.assertNotRegex(body, r"(?m)^\s*set\s+-[a-z]*x")

    def test_refuse_outside_private_ci(self):
        for s in SCRIPTS:
            with self.subTest(s=s), tempfile.TemporaryDirectory() as d:
                env = base_env(d, GITHUB_WORKFLOW_REF="mwqwf/mushafak-ci/.github/workflows/evil.yml@refs/heads/main")
                r = sh(s, ["build"], env, 30)
                self.assertEqual(r.returncode, 2)
                self.assertIn("لا يُشغَّل إلا من private-ci.yml", r.stdout)
                self.assertFalse(os.path.exists(os.path.join(d, "pci")), "أنشأ مجلّداً قبل الرفض")


class CloneTest(unittest.TestCase):
    def test_private_without_key_fails_silently(self):
        with tempfile.TemporaryDirectory() as d:
            r = sh("clone.sh", [], base_env(d, PCI_KEY_B64=""), 60)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stdout + r.stderr, "", "خرج شيءٌ إلى السجلّ العلنيّ")
            self.assertFalse(os.path.exists(os.path.join(d, "PUBLIC_SUMMARY")))
            log = readf(os.path.join(d, "pci", "out", "log.txt"))
            self.assertIn("لا مفتاحَ قراءة", log)
            self.assertEqual(readf(os.path.join(d, "pci", "state", "rc_clone")).strip(), "1")
            self.assertEqual([n for n in os.listdir(os.path.join(d, "pci")) if n.startswith("k.")], [])

    def test_key_goes_to_agent_stdin_and_never_to_a_file(self):
        """D14: لا ملفَّ مفتاحٍ على القرص (ssh-add - من stdin)، وtrap على EXIT/INT/TERM، وخروجٌ 9 عند الشكّ."""
        body = readf(os.path.join(HERE, "clone.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        self.assertIn("ssh-add -q -", code)
        self.assertRegex(code, r"base64 --decode[^\n]*\|\s*ssh-add")
        self.assertNotRegex(code, r"base64 --decode\s*>")          # لا كتابةَ للمفتاح المفكوك إلى ملف
        self.assertNotIn("KD/id", code)
        self.assertNotIn("shred", code)
        self.assertIn("trap on_exit EXIT", code)
        self.assertIn("trap 'exit 130' INT", code)
        self.assertIn("trap 'exit 143' TERM", code)
        self.assertIn("exit 9", code)

    @unittest.skipUnless(shutil.which("git") and shutil.which("sleep"), "لا git")
    def test_signal_mid_clone_still_scrubs_and_records_failure(self):
        """D14: قطعٌ بإشارة وسطَ الاستنساخ ⇒ trap يمحو ويتحقّق ويكتب rc_clone=1 ويخرج 1 (لا 143 صامتاً ولا بقايا)."""
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "fakebin"); os.makedirs(fake)
            with open(os.path.join(fake, "git"), "w") as f:
                f.write("#!/bin/sh\nexec sleep 30\n")
            os.chmod(os.path.join(fake, "git"), 0o755)
            tmp = os.path.join(d, "rt"); os.makedirs(tmp)
            env = base_env(tmp, PCI_KIND="dry-run", PCI_SOURCE="public", GITHUB_REPOSITORY="x/y",
                           PCI_TEST_REMOTE="file:///nonexistent")
            env["PATH"] = fake + ":" + env["PATH"]
            p = subprocess.Popen(["bash", os.path.join(HERE, "clone.sh")], env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, start_new_session=True)
            for _ in range(100):                      # حتى يبدأ git الوهميّ
                time.sleep(0.05)
                if os.path.exists(os.path.join(tmp, "pci", "out", "log.txt")):
                    break
            time.sleep(0.3)
            os.killpg(p.pid, signal.SIGTERM)
            out, err = p.communicate(timeout=30)
            self.assertEqual(p.returncode, 1, (out, err))
            self.assertEqual(out + err, "")
            self.assertEqual(readf(os.path.join(tmp, "pci", "state", "rc_clone")).strip(), "1")
            self.assertEqual([n for n in os.listdir(os.path.join(tmp, "pci")) if n.startswith("k.")], [])
            self.assertIn("المفتاحُ زال", readf(os.path.join(tmp, "pci", "out", "log.txt")))

    @unittest.skipUnless(shutil.which("git"), "لا git")
    def test_dry_run_clone_from_local_remote(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "remote")
            os.makedirs(os.path.join(repo, "jobs", "private_ci"))
            os.makedirs(os.path.join(repo, "secure"))
            with open(os.path.join(repo, "jobs", "private_ci", "x.py"), "w") as f:
                f.write("print(1)\n")
            with open(os.path.join(repo, "secure", "keystore.properties"), "w") as f:
                f.write("x\n")
            g = ["git", "-C", repo]
            genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t")
            subprocess.run(["git", "init", "-q", repo], check=True, env=genv)
            subprocess.run(g + ["config", "uploadpack.allowFilter", "true"], check=True, env=genv)
            subprocess.run(g + ["config", "uploadpack.allowAnySHA1InWant", "true"], check=True, env=genv)
            subprocess.run(g + ["add", "-A"], check=True, env=genv)
            subprocess.run(g + ["commit", "-qm", "x"], check=True, env=genv)
            head = subprocess.run(g + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
            tmp = os.path.join(d, "rt"); os.makedirs(tmp)
            env = base_env(tmp, PCI_KIND="dry-run", PCI_SHA=head, PCI_SOURCE="public", GITHUB_REPOSITORY="x/y",
                           PCI_TEST_REMOTE="file://" + repo)
            r = sh("clone.sh", [], env, 120)
            self.assertEqual(r.stdout + r.stderr, "")
            self.assertEqual(r.returncode, 0, readf(os.path.join(tmp, "pci", "out", "log.txt"))[-800:])
            src = os.path.join(tmp, "pci", "src")
            self.assertTrue(os.path.isfile(os.path.join(src, "jobs", "private_ci", "x.py")))
            self.assertFalse(os.path.exists(os.path.join(src, "secure")), "الاستنساخُ الجزئيّ أدخل secure/")
            remotes = subprocess.run(["git", "-C", src, "remote"], capture_output=True, text=True).stdout
            self.assertEqual(remotes.strip(), "")

    @unittest.skipUnless(shutil.which("ssh-agent") and shutil.which("ssh-keygen") and shutil.which("ssh-add"),
                         "لا ssh-agent في هذه البيئة (يجري على العدّاء)")
    def test_private_key_and_agent_are_gone_after_failure(self):
        with tempfile.TemporaryDirectory() as d:
            kp = os.path.join(d, "kp")
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", kp], check=True)
            key = base64.b64encode(readf(kp, "rb")).decode()
            os.remove(kp); os.remove(kp + ".pub")
            before = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "ssh-agent"], capture_output=True,
                                    text=True).stdout.split()
            # الجلبُ من GitHub يفشل هنا (لا مفتاحَ مسجَّل) — والمطلوبُ أنّ المحوَ يجري على كلّ مخرج.
            r = sh("clone.sh", [], base_env(d, PCI_KEY_B64=key), 120)
            self.assertIn(r.returncode, (1, 9))
            self.assertEqual(r.stdout + r.stderr, "")
            self.assertEqual([n for n in os.listdir(os.path.join(d, "pci")) if n.startswith("k.")], [])
            after = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "ssh-agent"], capture_output=True,
                                   text=True).stdout.split()
            self.assertEqual(sorted(after), sorted(before), "بقي ssh-agent")
            self.assertNotIn(key[:24], readf(os.path.join(d, "pci", "out", "log.txt")))


@unittest.skipUnless(shutil.which("age") and shutil.which("age-keygen"), "لا age محلّيّاً (يُثبَّت بالبصمة على العدّاء)")
class EncryptTest(unittest.TestCase):
    def test_round_trip_and_header(self):
        with tempfile.TemporaryDirectory() as d:
            ident = os.path.join(d, "id.txt")
            subprocess.run(["age-keygen", "-o", ident], check=True, capture_output=True)
            pub = subprocess.run(["age-keygen", "-y", ident], capture_output=True, text=True, check=True).stdout
            pubf = os.path.join(d, "pub.txt")
            with open(pubf, "w") as f:
                f.write("# مفتاحُ اختبار\n" + pub)
            os.makedirs(os.path.join(d, "pci", "out"))
            secret_line = "e: file:///w/app/src/main/kotlin/X.kt:1:1 Unresolved reference\n"
            with open(os.path.join(d, "pci", "out", "log.txt"), "w") as f:
                f.write(secret_line)
            env = base_env(d, PCI_TEST_AGE_BIN=shutil.which("age"), PCI_TEST_PUBKEY_FILE=pubf)
            r = sh("encrypt_log.sh", ["encrypt"], env, 60)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertNotIn("X.kt", r.stdout)
            out = os.path.join(d, "pci", "art", "log.txt.gz.age")
            data = readf(out, "rb")
            self.assertTrue(data.startswith(b"age-encryption.org/v1\n"))
            self.assertNotIn(b"Unresolved", data)
            plain = subprocess.run(["age", "-d", "-i", ident, out], capture_output=True, check=True).stdout
            self.assertEqual(gzip.decompress(plain).decode(), secret_line)

    def test_placeholder_refused_for_private_and_allowed_for_dry_run(self):
        """D19: اختبارُ رفض المفتاح الوهميّ يجري على ملفِّ تجربةٍ (PCI_TEST_PUBKEY_FILE) بعلامة PLACEHOLDER-TEST-KEY."""
        with tempfile.TemporaryDirectory() as d:
            pubf = fixture_pubkey(d, placeholder=True)
            os.makedirs(os.path.join(d, "pci", "out"))
            open(os.path.join(d, "pci", "out", "log.txt"), "w").close()
            env = base_env(d, PCI_TEST_AGE_BIN=shutil.which("age"), PCI_TEST_PUBKEY_FILE=pubf)
            r = sh("encrypt_log.sh", ["encrypt"], env, 60)
            self.assertEqual(r.returncode, 1)
            self.assertIn("PLACEHOLDER-TEST-KEY", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(d, "pci", "art", "log.txt.gz.age")))
            env["PCI_SOURCE"] = "public"
            self.assertEqual(sh("encrypt_log.sh", ["encrypt"], env, 60).returncode, 0)

    def test_real_key_without_marker_encrypts_private_source_and_decrypts(self):
        """D19: مفتاحٌ حقيقيٌّ مولَّدٌ في الاختبار بلا علامة ⇒ التشفيرُ لمصدرٍ خاصّ ينجح والفكُّ به يعيد النصّ."""
        with tempfile.TemporaryDirectory() as d:
            ident = os.path.join(d, "id.txt")
            subprocess.run(["age-keygen", "-o", ident], check=True, capture_output=True)
            pubf = fixture_pubkey(d, placeholder=False, ident=ident)
            self.assertNotIn("PLACEHOLDER-TEST-KEY", readf(pubf))
            os.makedirs(os.path.join(d, "pci", "out"))
            text = "e: file:///w/app/src/main/kotlin/Secret.kt:3:1 Unresolved reference\n"
            with open(os.path.join(d, "pci", "out", "log.txt"), "w", encoding="utf-8") as f:
                f.write(text)
            env = base_env(d, PCI_SOURCE="private", PCI_TEST_AGE_BIN=shutil.which("age"), PCI_TEST_PUBKEY_FILE=pubf)
            r = sh("encrypt_log.sh", ["encrypt"], env, 60)
            self.assertEqual(r.returncode, 0, r.stdout)
            out = os.path.join(d, "pci", "art", "log.txt.gz.age")
            self.assertTrue(ac.age_header_ok(readf(out, "rb")))
            plain = subprocess.run(["age", "-d", "-i", ident, out], capture_output=True, check=True).stdout
            self.assertEqual(gzip.decompress(plain).decode(), text)

    def test_overrides_ignored_inside_actions(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "pci", "out"))
            open(os.path.join(d, "pci", "out", "log.txt"), "w").close()
            env = base_env(d, PCI_SOURCE="public", GITHUB_ACTIONS="true", PCI_TEST_AGE_BIN=shutil.which("age"))
            r = sh("encrypt_log.sh", ["encrypt"], env, 60)
            self.assertEqual(r.returncode, 1)           # age المثبَّت بالبصمة غائب ⇒ لا يُستعمل age النظام
            self.assertIn("age غيرُ مثبَّت", r.stdout)


class PubkeyFileTest(unittest.TestCase):
    def test_shipped_key_is_exactly_one_valid_public_key_with_no_secret(self):
        """D19: يشترط مفتاحاً عامّاً واحداً صالحاً بلا سرّ، ولا يشترط العلامة (يقبل الوهميَّ المعلَّم والحقيقيَّ)."""
        text = readf(os.path.join(HERE, "log_pubkey.txt"))
        keys = [ln for ln in text.splitlines() if ln.startswith("age1")]
        self.assertEqual(len(keys), 1)
        self.assertRegex(keys[0], r"^age1[02-9ac-hj-np-z]{58}$")
        self.assertNotIn("AGE-SECRET-KEY", text.upper())
        marked = "PLACEHOLDER-TEST-KEY" in text
        self.assertIn(marked, (True, False))      # الحالتان مقبولتان
        if marked:                                # الوهميُّ المعلَّم لا يُنشر معه مفتاحٌ ثانٍ ولا سرّ
            self.assertEqual(len([ln for ln in text.splitlines() if not ln.startswith("#") and ln.strip()]), 1)


PUBLIC_LINE = re.compile(
    r"^(العزل: .*|الاستنساخ: [✅⛔].*|الأطوار \((build|emu)\): \d+ · فاشلة \d+|الأطوار: ⛔ .*|التنظيف: ⛔ .*|الناتج: .*"
    r"|الإنهاء: (التملّك \d+ ث من -?\d+ متاحة|⛔ انقضت مهلةُ التملّك — لا تُقرأ الشجرة \(\d+ ث من -?\d+ متاحة\)|⛔ تعذّر التملّك — لا تُقرأ الشجرة|\d+ ث من ميزانيةٍ أقصاها \d+)"
    r"|المحاكي: .*|مسبارُ المحاكي: .*|المشرف: ⛔ .*)$")
ALLOWED_BUILDER_ENV = {
    "HOME", "PATH", "JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_USER_HOME", "GRADLE_USER_HOME", "GRADLE_OPTS",
    "CI", "TERM", "LANG", "PYTHONIOENCODING", "GITHUB_WORKFLOW_REF", "PCI_KIND", "PCI_LEG", "PCI_LINT", "PCI_SRC",
    "PCI_BSTATE", "PCI_BDEVICE", "PCI_WHISPER_REF", "PCI_RUN_TAG",
    # أ2: خادمُ ADB والمحاكي بمستخدم البناء بمنفذٍ ومجلّداتٍ خاصّة، ومعاملاتُ المحاكي بأنماطٍ مغلقة
    "ANDROID_ADB_SERVER_PORT", "ANDROID_AVD_HOME", "ANDROID_EMULATOR_HOME",
    "PCI_EMU_API", "PCI_EMU_TARGET", "PCI_EMU_ARCH", "PCI_EMU_PROFILE", "PCI_EMU_RAM",
    # يضبطها build.sh نفسُه (مصارفُ مهملةٌ داخل مجلّد البناء) وبيئةُ bash الذاتيّة
    "GITHUB_STEP_SUMMARY", "GITHUB_ENV", "GITHUB_OUTPUT", "GITHUB_PATH", "GITHUB_STATE", "PWD", "OLDPWD", "SHLVL", "_",
}


def tiny_kinds(phases, emu_flags=None, kind="dry-run", timeout=15):
    emu_flags = emu_flags or {}
    return {kind: {"source": "public",
                   "phases": [{"id": p, "who": "builder", "emu": emu_flags.get(p, False), "optional": False} for p in phases],
                   "legs": [{"id": "main", "timeout": timeout}]}}


@unittest.skipUnless(shutil.which("git") and shutil.which("age") and shutil.which("age-keygen"), "لا git أو age")
class SupervisorTest(unittest.TestCase):
    """المشرفُ في الوضع المحلّيّ (PCI_TEST_NO_ISOLATION=1): بلا مستخدمٍ ولا sudo ولا تحصين — يختبر منطقَه كلَّه عدا العزلَ الحقيقيّ."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.rt = os.path.join(self.d, "rt"); os.makedirs(self.rt)
        self.pci = os.path.join(self.rt, "pci")
        self.ident = os.path.join(self.d, "id.txt")
        subprocess.run(["age-keygen", "-o", self.ident], check=True, capture_output=True)
        self.pubf = fixture_pubkey(self.d, placeholder=False, ident=self.ident)
        self.gh_out = os.path.join(self.d, "gh_output")
        self.public_summary = os.path.join(self.d, "PUBLIC_SUMMARY")
        self.head = self.make_remote()
        self.write_kinds(tiny_kinds(["selftest_ok", "selftest_fail", "selftest_noisy"]))
        self.outputs = []

    def make_remote(self):
        repo = os.path.join(self.d, "remote")
        os.makedirs(os.path.join(repo, "jobs", "private_ci"))
        with open(os.path.join(repo, "jobs", "private_ci", "x.py"), "w") as f:
            f.write("print(1)\n")
        g = ["git", "-C", repo]
        genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q", repo], check=True, env=genv)
        subprocess.run(g + ["config", "uploadpack.allowFilter", "true"], check=True, env=genv)
        subprocess.run(g + ["config", "uploadpack.allowAnySHA1InWant", "true"], check=True, env=genv)
        subprocess.run(g + ["add", "-A"], check=True, env=genv)
        subprocess.run(g + ["commit", "-qm", "x"], check=True, env=genv)
        return subprocess.run(g + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

    def write_kinds(self, kinds):
        self.kinds_file = os.path.join(self.d, "kinds.json")
        with open(self.kinds_file, "w", encoding="utf-8") as f:
            json.dump(kinds, f)

    def env(self, **kw):
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.d, "RUNNER_TEMP": self.rt,
             "GITHUB_WORKFLOW_REF": REF_OK, "PCI_KIND": "dry-run", "PCI_SHA": self.head, "PCI_SOURCE": "public",
             "PCI_LEG": "main", "GITHUB_REPOSITORY": "x/y", "GITHUB_STEP_SUMMARY": self.public_summary,
             "GITHUB_OUTPUT": self.gh_out, "PCI_TEST_REMOTE": "file://" + os.path.join(self.d, "remote"),
             "PCI_TEST_NO_ISOLATION": "1", "PCI_TEST_KINDS_FILE": self.kinds_file,
             "PCI_TEST_AGE_BIN": shutil.which("age"), "PCI_TEST_PUBKEY_FILE": self.pubf}
        e.update(kw)
        return {k: v for k, v in e.items() if v is not None}   # None ⇒ يُحذف المتغيّرُ أصلاً

    def sup(self, mode, **kw):
        """يشغّل مشرفَ ctl (النسخةَ الموثوقة) إن وُجد، وإلا النسخةَ الأصليّة (لـprepare)."""
        script = os.path.join(self.pci, "ctl", "supervise.sh")
        if mode == "prepare" or not os.path.exists(script):
            script = os.path.join(HERE, "supervise.sh")
        r = subprocess.run(["bash", script, mode], env=self.env(**kw), capture_output=True, text=True, timeout=180)
        self.outputs.append((mode, r))
        return r

    def run_all(self, modes=("prepare", "clone", "build", "emu", "finalize"), **kw):
        return [self.sup(m, **kw) for m in modes]

    def log(self):
        with open(os.path.join(self.pci, "out", "log.txt"), encoding="utf-8", errors="replace") as f:
            return f.read()

    def rc(self, name):
        with open(os.path.join(self.pci, "state", f"rc_{name}")) as f:
            return f.read().strip()

    def ran(self):
        with open(os.path.join(self.pci, "state", "ran.txt")) as f:
            return f.read().split()

    def ready(self):
        if not os.path.exists(self.gh_out):
            return None
        lines = [ln.strip() for ln in readf(self.gh_out).splitlines() if ln.startswith("ready=")]
        return lines[-1] if lines else None

    def counts(self):
        return cs.validate(readf(os.path.join(self.pci, "art", "counts.json"), "rb"), "dry-run", "main", self.head,
                           json.loads(readf(self.kinds_file)))

    # ───── التدفّق الكامل ─────
    def test_full_flow_ready_and_art_has_exactly_two_files(self):
        rs = self.run_all()
        for mode, r in zip(("prepare", "clone", "build", "emu", "finalize"), rs):
            self.assertEqual(r.returncode, 0, (mode, r.stdout, r.stderr, self.log()[-600:]))
        self.assertEqual(self.ready(), "ready=true")
        self.assertEqual(sorted(os.listdir(os.path.join(self.pci, "art"))), ["counts.json", "log.txt.gz.age"])
        self.assertTrue(ac.check(os.path.join(self.pci, "art"), "dry-run", "main", self.head,
                                 json.loads(readf(self.kinds_file)))[0])
        c = self.counts()
        self.assertEqual(c["ran"], ["isolation", "clone", "selftest_ok", "selftest_fail", "selftest_noisy", "cleanup"])
        self.assertEqual(c["failed_phase_ids"], ["selftest_fail"])
        self.assertFalse(c["ok"])
        # السجلُّ المشفَّر يُفكّ بالمفتاح الخاصّ ويحوي مخرجاتِ الأطوار
        enc = os.path.join(self.pci, "art", "log.txt.gz.age")
        plain = gzip.decompress(subprocess.run(["age", "-d", "-i", self.ident, enc], capture_output=True, check=True).stdout)
        self.assertIn("← selftest_noisy".encode(), plain)
        self.assertNotIn(b"xxxxxxxxxxxxxxxx", readf(enc, "rb"))

    def test_public_stdout_is_fixed_sentences_only_and_never_the_log(self):
        rs = self.run_all()
        log = self.log()
        self.assertIn("xxxxxxxxxxxxxxxx", log)         # ضجيجُ الطور في السجلّ الخاصّ…
        for mode, r in zip(("prepare", "clone", "build", "emu", "finalize"), rs):
            self.assertEqual(r.stderr, "", (mode, r.stderr))
            for line in r.stdout.splitlines():
                self.assertRegex(line, PUBLIC_LINE, (mode, line))   # …وstdout جملٌ ثابتةٌ فقط
            self.assertNotIn("xxxxxxxx", r.stdout)
            self.assertNotIn(self.head, r.stdout)
            self.assertNotIn("selftest", r.stdout)
        self.assertFalse(os.path.exists(self.public_summary), "كتبت شيفرةٌ في الملخّص العلنيّ")
        text = "\n".join(r.stdout for r in rs)
        self.assertIn("الناتج: ✅ جاهزٌ للرفع", text)
        self.assertIn("الأطوار (build): 3 · فاشلة 1", text)

    def test_phase_rc_is_the_real_exit_code_and_ran_is_ordered(self):
        self.run_all()
        self.assertEqual((self.rc("selftest_ok"), self.rc("selftest_fail"), self.rc("selftest_noisy")), ("0", "1", "0"))
        self.assertEqual(self.ran(), ["isolation", "clone", "selftest_ok", "selftest_fail", "selftest_noisy", "cleanup"])
        c = self.counts()
        self.assertEqual((c["tests"], c["failures"], c["phases"], c["failed_phases"]), (3, 1, 6, 1))

    def test_builder_cannot_forge_supervisor_state(self):
        """طورٌ يكتب rc_x=0 وran.txt في PCI_BSTATE (مجلّدُه) لا يغيّر حالةَ المشرف: الرمزُ من خروج الطور الفعليّ."""
        self.write_kinds(tiny_kinds(["selftest_forge", "selftest_fail"]))
        self.run_all()
        self.assertEqual(self.rc("selftest_forge"), "1")
        self.assertEqual(self.rc("selftest_fail"), "1")
        self.assertEqual(self.ran(), ["isolation", "clone", "selftest_forge", "selftest_fail", "cleanup"])
        c = self.counts()
        self.assertEqual(sorted(c["failed_phase_ids"]), ["selftest_fail", "selftest_forge"])
        self.assertFalse(c["ok"])
        forged = os.path.join(self.rt, "builder", "state")
        self.assertEqual(readf(os.path.join(forged, "rc_selftest_forge")).strip(), "0")   # كتبه البناءُ في مجلّده فقط

    def test_builder_environment_is_the_allowlist_only(self):
        """env -i + قائمةٌ مسموحة: لا ACTIONS_* ولا GITHUB_TOKEN ولا مفتاحَ ولا RUNNER_TEMP ولا أيَّ سرٍّ من بيئة المشرف."""
        self.write_kinds(tiny_kinds(["selftest_env"]))
        secrets = {"ACTIONS_RUNTIME_TOKEN": "sekret-runtime-111", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "sekret-id-222",
                   "GITHUB_TOKEN": "sekret-gh-333", "GH_TOKEN": "sekret-gh-444", "PCI_KEY_B64": "sekret-key-555",
                   "PCI_R2_JSON": "sekret-r2-666", "AWS_SECRET_ACCESS_KEY": "sekret-aws-777",
                   "GITHUB_ENV": "/tmp/sekret-env-file", "GITHUB_PATH": "/tmp/sekret-path-file"}
        self.run_all(**secrets)
        log = self.log()
        self.assertNotIn("sekret", log.replace("sekret-env-file", "").replace("sekret-path-file", ""))
        start = log.index("← selftest_env")
        names = set(re.findall(r"(?m)^([A-Za-z_][A-Za-z0-9_]*)=", log[start:]))
        self.assertTrue(names, "لم تُطبع بيئةُ الطور")
        self.assertEqual(names - ALLOWED_BUILDER_ENV, set())
        for forbidden in ("ACTIONS_RUNTIME_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "PCI_KEY_B64", "PCI_R2_JSON", "RUNNER_TEMP",
                          "AWS_SECRET_ACCESS_KEY", "PCI_SHA", "PCI_SOURCE", "GITHUB_REPOSITORY"):
            self.assertNotIn(forbidden, names)
        # مصارفُ أوامر GitHub محوَّلةٌ إلى مجلّد البناء (لا إلى ملفّات الخطوة)
        bstate = os.path.join(self.rt, "builder", "state")
        for v in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT", "GITHUB_STATE"):
            self.assertRegex(log, r"(?m)^%s=%s/inner_" % (v, re.escape(bstate)))
        self.assertRegex(log, r"(?m)^GITHUB_WORKFLOW_REF=")
        self.assertRegex(log, r"(?m)^HOME=%s$" % re.escape(os.path.join(self.rt, "builder")))

    def test_oversized_phase_log_is_cut_at_cap_and_next_phases_still_run(self):
        """D18: سجلُّ الطور يُقطع عند الحدّ بعلامةٍ ثابتة؛ والأنبوبُ يُستنزَف فلا يموت البناءُ بـSIGPIPE."""
        self.write_kinds(tiny_kinds(["selftest_noisy", "selftest_ok"]))
        self.run_all(PCI_TEST_PHASE_CAP="1000")
        log = self.log()
        self.assertIn("بُتر سجلُّ الطور selftest_noisy عند الحدّ", log)
        self.assertLess(len(log.encode()), 6000)
        self.assertEqual(self.rc("selftest_noisy"), "0")      # الطورُ نفسُه أنهى عملَه (لم يُقتل)
        self.assertEqual(self.rc("selftest_ok"), "0")          # والتالي جرى
        self.assertEqual(self.counts()["failed_phases"], 0)

    def test_total_cap_exhausted_cuts_later_phases_to_marker_only(self):
        # ثلاثةُ أطوارٍ صاخبةٍ (المعرّفُ المكرَّر يُنفَّذ مرّتين) بعتبةٍ صغيرة ⇒ ينفد الكلُّ فيُقطع ما بعده إلى علامةٍ فقط
        self.write_kinds(tiny_kinds(["selftest_noisy", "selftest_env", "selftest_noisy"]))
        self.run_all(PCI_TEST_PHASE_CAP="400")
        log = self.log()
        self.assertGreaterEqual(log.count("بُتر سجلُّ الطور"), 2)
        self.assertLess(len(log.encode()), 8000)

    def test_stray_file_in_art_blocks_readiness(self):
        self.run_all(("prepare", "clone", "build", "emu"))
        with open(os.path.join(self.pci, "art", "stray.txt"), "w") as f:
            f.write("x")
        r = self.sup("finalize")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.ready(), "ready=false")
        self.assertIn("الناتج: ⛔", r.stdout)
        self.assertNotIn("✅ جاهز", r.stdout)

    def test_finalize_alone_after_failed_prepare_is_not_ready_and_does_not_crash(self):
        r = self.sup("finalize")
        self.assertEqual(r.returncode, 1)
        self.assertIsNone(self.ready())

    def test_isolation_failure_blocks_clone_and_build(self):
        self.sup("prepare")
        with open(os.path.join(self.pci, "state", "rc_isolation"), "w") as f:
            f.write("1\n")
        r = self.sup("clone")
        self.assertEqual(r.returncode, 1)
        self.assertIn("الاستنساخ: ⛔", r.stdout)
        self.assertNotIn("clone", self.ran())
        self.assertFalse(os.path.exists(os.path.join(self.rt, "builder", "src")))
        self.assertEqual(self.sup("build").returncode, 0)
        self.assertNotIn("selftest_ok", self.ran())
        self.sup("finalize")
        c = self.counts()
        self.assertEqual((c["clone_ok"], c["ok"], c["failed_phase_ids"]), (False, False, ["isolation"]))
        self.assertEqual(c["ran"], ["isolation", "cleanup"])

    def test_clone_failure_is_recorded_and_no_build(self):
        self.sup("prepare")
        r = self.sup("clone", PCI_SHA="1" * 40)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.rc("clone"), "1")
        self.sup("build")
        self.assertNotIn("selftest_ok", self.ran())
        self.sup("finalize", PCI_SHA="1" * 40)
        self.assertEqual(self.ready(), "ready=true")
        with open(os.path.join(self.pci, "art", "counts.json"), "rb") as f:
            c = cs.validate(f.read(), "dry-run", "main", "1" * 40, json.loads(readf(self.kinds_file)))
        self.assertFalse(c["clone_ok"])

    def test_expired_deadline_records_124_without_running(self):
        self.sup("prepare"); self.sup("clone")
        with open(os.path.join(self.pci, "state", "deadline"), "w") as f:
            f.write(str(int(time.time()) + 30) + "\n")        # أقلُّ من 60 ثانية
        self.sup("build")
        self.assertEqual((self.rc("selftest_ok"), self.rc("selftest_fail"), self.rc("selftest_noisy")), ("124",) * 3)
        self.assertNotIn("← selftest_ok", self.log())
        self.assertIn("لا وقتَ كافياً", self.log())

    def test_budget_starts_with_the_supervisor_and_phases_stop_five_minutes_before(self):
        """أ4: الميزانيةُ (مهلةُ الشوط) تُحسب من بدء المشرف وتشمل كلَّ شيء؛ والأطوارُ تنتهي قبلها بـFIN_RESERVE (365 ث منذ الإصلاح السادس) لـfinalize؛
        ووظيفةُ build في السير مهلتُها الميزانيةُ + هامش (job_timeout) فيسبق finalize إيقافَ GitHub."""
        self.write_kinds(tiny_kinds(["selftest_ok"], timeout=75))
        before = int(time.time())
        self.sup("prepare")
        dl = int(readf(os.path.join(self.pci, "state", "deadline")))
        be = int(readf(os.path.join(self.pci, "state", "budget_end")))
        self.assertTrue(before + 75 * 60 - 365 - 5 <= dl <= int(time.time()) + 75 * 60 - 365 + 5, dl - before)
        self.assertEqual(be - dl, 365)
        wf = readf(os.path.join(HERE, "..", "..", ".github", "workflows", "private-ci.yml"))
        self.assertIn("timeout-minutes: ${{ matrix.leg.job_timeout }}", wf)
        import plan
        for spec in plan.load_kinds().values():
            for leg in plan.legs_for(spec):
                self.assertEqual(leg["job_timeout"], leg["timeout"] + plan.JOB_MARGIN)
        self.assertGreaterEqual(plan.JOB_MARGIN, 5)

    def test_unrun_emulator_phases_are_recorded_as_failed_not_lost(self):
        self.write_kinds(tiny_kinds(["selftest_ok", "selftest_fail"], emu_flags={"selftest_fail": True}))
        self.run_all(("prepare", "clone", "build", "finalize"))      # لا emu: المحاكي لم يُقلع
        self.assertEqual(self.ready(), "ready=true")
        c = self.counts()
        self.assertEqual(c["failed_phase_ids"], ["selftest_fail"])
        self.assertIn("selftest_fail", c["ran"])
        self.assertFalse(c["ok"])

    def test_emu_mode_runs_only_emulator_phases(self):
        self.write_kinds(tiny_kinds(["selftest_ok", "selftest_fail"], emu_flags={"selftest_fail": True}))
        self.run_all(("prepare", "clone", "build"))
        self.assertEqual(self.ran(), ["isolation", "clone", "selftest_ok"])
        r = self.sup("emu")
        self.assertIn("الأطوار (emu): 1 · فاشلة 1", r.stdout)
        self.assertEqual(self.ran(), ["isolation", "clone", "selftest_ok", "selftest_fail"])

    def test_junit_counts_come_from_the_builder_tree_only_regular_files(self):
        """JUnit يُقرأ من شجرة البناء بملفّاتٍ عاديّة؛ والرابطُ الرمزيُّ لا يُتبع."""
        self.write_kinds(tiny_kinds(["selftest_ok"], kind="app-test"))
        self.run_all(("prepare", "clone"), PCI_KIND="app-test")
        base = os.path.join(self.rt, "builder", "src", "app", "build", "test-results", "t")
        os.makedirs(base)
        with open(os.path.join(base, "a.xml"), "w") as f:
            f.write('<testsuite tests="4" failures="1" errors="0" skipped="0"/>')
        secret = os.path.join(self.d, "outside.xml")
        with open(secret, "w") as f:
            f.write('<testsuite tests="1000" failures="0" errors="0" skipped="0"/>')
        os.symlink(secret, os.path.join(base, "link.xml"))
        self.sup("build", PCI_KIND="app-test"); self.sup("finalize", PCI_KIND="app-test")
        with open(os.path.join(self.pci, "art", "counts.json"), "rb") as f:
            c = cs.validate(f.read(), "app-test", "main", self.head, json.loads(readf(self.kinds_file)))
        self.assertEqual((c["tests"], c["failures"], c["errors"], c["reports"]), (4, 1, 1, 1))

    def test_rc_file_cannot_be_planted_into_supervisor_state_by_phase_names(self):
        """معرّفاتُ الأطوار من kinds الموثوق وتُفحص بنمطٍ صارم: معرّفٌ يحمل مساراً لا يُنفَّذ ولا يُكتب."""
        self.write_kinds(tiny_kinds(["selftest_ok", "../../evil", "selftest_fail"]))
        self.run_all()
        self.assertFalse(os.path.exists(os.path.join(self.rt, "evil")))
        self.assertFalse(os.path.exists(os.path.join(self.pci, "state", "rc_../../evil")))

    # ───── وضعُ الاختبار لا يُقبل خارجَه ─────
    def test_refuses_real_isolation_outside_actions(self):
        r = self.sup("prepare", PCI_TEST_NO_ISOLATION="")
        self.assertEqual(r.returncode, 2)
        self.assertIn("داخل GitHub Actions", r.stdout)
        self.assertFalse(os.path.exists(self.pci), "أنشأ مجلّداً قبل الرفض")

    def test_no_isolation_flag_is_refused_inside_actions_before_anything(self):
        """أ2-٤: وجودُ PCI_TEST_NO_ISOLATION (ولو فارغاً) داخل Actions يُفشل الشوطَ قبل أيّ شيء — لا مجلّدَ ولا مستخدم."""
        for val in ("1", "", "0"):
            with self.subTest(val=val):
                for mode in ("prepare", "clone", "build", "emu", "finalize"):
                    r = subprocess.run(["bash", os.path.join(HERE, "supervise.sh"), mode],
                                       env=self.env(GITHUB_ACTIONS="true", PCI_TEST_NO_ISOLATION=val),
                                       capture_output=True, text=True, timeout=30)
                    self.assertEqual(r.returncode, 2, (mode, r.stdout))
                    self.assertIn("مرفوضٌ داخل GitHub Actions", r.stdout)
                self.assertFalse(os.path.exists(self.pci), "أنشأ مجلّداً قبل الرفض")

    def test_any_test_variable_is_refused_on_a_real_runner(self):
        """على عدّاءٍ حقيقيّ (RUNNER_ENVIRONMENT مضبوطٌ دائماً) لا يُقبل أيُّ متغيّرٍ PCI_TEST_* — ولو لم يكن وضعَ العزل."""
        env = {k: v for k, v in self.env(GITHUB_ACTIONS="true", RUNNER_ENVIRONMENT="github-hosted").items()
               if not k.startswith("PCI_TEST_")}
        for hook in ("PCI_TEST_BIN_DIR", "PCI_TEST_KINDS_FILE", "PCI_TEST_PHASE_CAP", "PCI_TEST_CLEANUP_SKIP_KILL",
                     "PCI_TEST_PHASE_SECS", "PCI_TEST_REMOTE", "PCI_TEST_AGE_BIN"):
            with self.subTest(hook=hook):
                r = subprocess.run(["bash", os.path.join(HERE, "supervise.sh"), "prepare"], env=dict(env, **{hook: "x"}),
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("PCI_TEST_* مرفوضةٌ", r.stdout)
                self.assertFalse(os.path.exists(self.pci))

    def test_real_isolation_runs_inside_actions_without_test_flag(self):
        """داخل Actions بلا PCI_TEST_NO_ISOLATION يجري العزلُ الحقيقيّ (sudo مزيَّفٌ هنا يفشل) ولا يُنشأ بيتُ البناء المحلّيّ."""
        fake = os.path.join(self.d, "fakebin"); os.makedirs(fake)
        calls = os.path.join(self.d, "sudo_calls")
        with open(os.path.join(fake, "sudo"), "w") as f:
            f.write('#!/bin/sh\necho "$@" >> "%s"\nexit 1\n' % calls)
        os.chmod(os.path.join(fake, "sudo"), 0o755)
        # ⛔ يمنع المشرفُ كلَّ sudo حقيقيٍّ هنا: مسارُه ثابتٌ، والمزيَّفُ يدخل بـPCI_TEST_BIN_DIR (مرفوضٌ على عدّاءٍ حقيقيّ)
        self.assertEqual(subprocess.run(["bash", "-c", 'PATH="%s:$PATH"; command -v sudo' % fake], capture_output=True,
                                        text=True).stdout.strip(), os.path.join(fake, "sudo"))
        kw = dict(GITHUB_ACTIONS="true", PCI_TEST_BIN_DIR=fake, PCI_TEST_NO_ISOLATION=None)
        r = self.sup("prepare", **kw)
        self.assertEqual(subprocess.run(["getent", "passwd", "pcibuild"], capture_output=True).returncode, 2,
                         "أُنشئ مستخدمٌ حقيقيّ! (sudo غيرُ مزيَّف)")
        self.assertEqual(r.returncode, 1, (r.stdout, r.stderr))
        self.assertIn("useradd", readf(calls), "لم يُحاوَل إنشاءُ المستخدم ⇒ لم يجرِ العزلُ الحقيقيّ")
        self.assertIn("العزل: ⛔", r.stdout)
        self.assertNotIn("متخطّى", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.rt, "builder")), "أُنشئ بيتُ البناء المحلّيّ داخل Actions")
        self.assertEqual(self.rc("isolation"), "1")
        # وclone لا تعمل بعدها (دفاعٌ ثانٍ) حتى لو شُغّلت بلا شرط الخطوة
        r2 = self.sup("clone", **kw)
        self.assertEqual(r2.returncode, 1)
        self.assertNotIn("clone", self.ran())

    def test_test_hooks_live_only_in_local_branch(self):
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        # PCI_TEST_KINDS_FILE وPCI_TEST_PHASE_CAP لا يُقرآن إلا داخل if LOCAL
        local_block = code[code.index('if [ "$LOCAL" = 1 ]; then\n  [ -n "${PCI_TEST_KINDS_FILE'):]
        local_block = local_block[:local_block.index("fi\n")]
        for hook in ("PCI_TEST_KINDS_FILE", "PCI_TEST_PHASE_CAP", "PCI_TEST_CLEANUP_SKIP_KILL", "PCI_TEST_PHASE_SECS"):
            self.assertEqual(code.count(hook), local_block.count(hook), hook)
            self.assertGreater(local_block.count(hook), 0, hook)
        self.assertEqual(code.count("PCI_TEST_NO_ISOLATION"), 3)   # بوّابةُ الرفض داخل Actions (شرطٌ ورسالة) ثمّ شرطُ الوضع المحلّيّ
        self.assertIn('"${GITHUB_ACTIONS:-}" != "true"', code)

    def test_supervisor_path_is_fixed_and_job_path_dirs_are_hardened(self):
        """مجلّداتُ PATH القابلةُ للكتابة لـpcibuild — في PATH الوظيفة وفي مسار المشرف الثابت، وبلا استثناءٍ للنظاميّ — تُحصَّن ثمّ تُختبر
        (وإلا `bash`/`sudo`/`python3` مزيَّفٌ يُنفَّذ بصلاحية runner). وقعت فعلاً: /usr/local/bin مفتوحٌ للكتابة في صورة العدّاء وكان مستثنى
        من التحصين فسقط الفحصُ 8 (التجربة الجافّة 42250728242 — المواضع 10 و18)."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        self.assertIn('export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"', code)
        self.assertIn('JOB_PATH="$PATH"', code)
        self.assertLess(code.index('JOB_PATH="$PATH"'), code.index('export PATH="/usr/local/sbin'))
        fn = code[code.index("harden_path_dirs(){"):code.index("isolation_tests(){")]
        self.assertIn("for d in $JOB_PATH:$PATH; do", fn)                      # مسارُ المشرف أيضاً — كما يختبره الفحصُ 8 سواءً بسواء
        self.assertNotIn("continue ;;", fn)                                       # لا قائمةَ استثناءٍ تُخرج مجلّداً من التحصين
        self.assertNotRegex(fn, r"/usr/local/bin[^\n]*continue")
        sys_line = next(ln for ln in fn.splitlines() if "|/usr/local/bin|" in ln)
        self.assertIn("chmod -R o-w", sys_line)                                   # النظاميُّ يُجرَّد من كتابة الغير…
        self.assertNotIn("chown", sys_line)                                       # …ويبقى لـroot
        self.assertIn('pub "التهيئة: ⛔ path-harden $pos"', fn)                    # الفشلُ يُعلَن بالموضع لا بالاسم
        self.assertIn('pub "التهيئة: ⛔ path-still-writable $pos"', fn)            # ويُعاد الفحصُ بعد التحصين
        self.assertLess(fn.index("esac"), fn.index("path-still-writable"))
        self.assertNotRegex(fn, r'pub "[^"]*\$(rd|d|me)\b')                       # لا مسارَ ولا اسمَ مستخدمٍ علناً
        # الحمايةُ تسبق الفحص: تحصينٌ ثمّ اختبار
        self.assertLess(code.index("harden_path_dirs ||"), code.index("isolation_tests\n"))
        # وفي الوضع المحلّيّ لا يغيّر المشرفُ PATH الاختباريّ الذي يحتاج git وage (يبقيهما في المسار الثابت)
        r = self.sup("prepare")
        self.assertEqual(r.returncode, 0)

    def test_supervisor_python_is_isolated_and_its_module_path_is_tested(self):
        """المشرفُ يشغّل python3 بعد البناء غيرِ الموثوق على الآلة نفسها ⇒ كلُّ استدعاءٍ بـ`-I -S` (لا PYTHONPATH ولا site ولا .pth
        ولا sitecustomize)، والفحصُ 12 من فحوص العزل يُثبت أنّ pcibuild لا يكتب في مسار وحداته — وبصرامة: غيابُ python3 عنه فشلٌ لا نجاح.
        وسطرُ الزرع في سير العمل (يجري بعد البناء أيضاً) بالأعلام نفسِها."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        calls = [ln for ln in code.splitlines() if re.search(r"(^|[\s(;{'])python3\s", ln)]   # ومنها استدعاءُ الفحص 12 داخل الاقتباس
        self.assertGreaterEqual(len(calls), 4)
        for ln in calls:
            self.assertIn("python3 -I -S ", ln, ln)
        iso = code[code.index("isolation_tests(){"):code.index("do_prepare(){")]
        self.assertIn("os.access(p, os.W_OK)", iso)
        self.assertIn("else 3)", iso)
        self.assertIn("[ $? -ne 3 ]", iso)                                        # 127 (لا python3) يُحسب عزلاً مفقوداً لا نجاحاً
        self.assertEqual(sum(1 for ln in iso.splitlines() if ln.strip().startswith("must_fail ")), 12)
        self.assertEqual(sum(1 for ln in iso.splitlines() if ln.strip().startswith("must_pass ")), 5)
        wf = readf(os.path.join(HERE, "..", "..", ".github", "workflows", "private-ci.yml"))
        self.assertIn('run: python3 -I -S "$RUNNER_TEMP/pci/ctl/guard.py" --emit-plant', wf)

    def test_sudo_restriction_is_one_sudo_call_and_setup_steps_are_public(self):
        """chown يمسح بِتَّ setuid من ثنائيّ sudo (لينكس يعامل root كغيره) ⇒ chown وchmod 4750 في استدعاء sudo واحد، وإلا عجز
        الأمرُ الثاني وسقطت التهيئةُ كلُّها (وقعت فعلاً: التجربة الجافّة 42248836438). وكلُّ خطوةٍ في setup_builder تُعلن رمزَها علناً عند الفشل."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        fn = code[code.index("setup_builder(){"):code.index("harden_path_dirs(){")]
        one_call = "sudo -n sh -c 'chown \"root:$2\" \"$1\" && chmod 4750 \"$1\"' _ \"$sb\" \"$(id -g)\""
        self.assertIn(one_call, fn)
        self.assertNotRegex(fn, r'sudo -n chown [^\n]*"\$sb"')       # لا chown منفصلٌ لثنائيّ sudo
        self.assertNotRegex(fn, r'sudo -n chmod 4750')                # ولا chmod منفصل
        self.assertLess(fn.index("chmod 4750"), fn.index("sudo -n true"))   # ثمّ تحقّقٌ أنّ sudo العدّاء ما زال يعمل
        # كلُّ سطرِ sudo في التهيئة ينتهي برمزِ فشلٍ علنيّ ثابت (كلمةٌ من حروفٍ وشرطات، لا مسار)
        sudo_lines = [ln for ln in fn.splitlines() if ln.lstrip().startswith("sudo -n ")]
        self.assertGreaterEqual(len(sudo_lines), 9)
        for ln in sudo_lines:
            self.assertRegex(ln, r'setup_fail [a-z][a-z-]*\)', ln)
        self.assertIn('setup_fail(){ pub "التهيئة: ⛔ $1"; echo 1; }', code)

    def test_supervisor_refuses_unknown_mode_and_missing_tools(self):
        r = self.sup("prepare")
        self.assertEqual(r.returncode, 0)
        r = self.sup("bogus")
        self.assertEqual(r.returncode, 1)
        shutil.rmtree(os.path.join(self.pci, "ctl"))
        r = self.sup("build")
        self.assertEqual(r.returncode, 1)
        self.assertIn("المشرف: ⛔", r.stdout)

    def test_ctl_copy_is_private_and_complete(self):
        self.sup("prepare")
        ctl = os.path.join(self.pci, "ctl")
        self.assertEqual(stat.S_IMODE(os.stat(ctl).st_mode), 0o700)
        for name in ("supervise.sh", "build.sh", "clone.sh", "encrypt_log.sh", "counts_schema.py", "artifact_check.py",
                     "guard.py", "kinds.json", "log_pubkey.txt", "iso_probe.py"):
            p = os.path.join(ctl, name)
            self.assertTrue(os.path.isfile(p), name)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode) & 0o077, 0, name)
        # نسخةُ البناء من build.sh (يقرؤها البناءُ ولا يعدّلها في الإنتاج)
        self.assertTrue(os.path.isfile(os.path.join(self.rt, "builder", "ctl", "build.sh")))


    # ───── الإصلاح الرابع (مراجعة Codex 13-ج) ─────
    def tagged(self):
        tag = readf(os.path.join(self.pci, "state", "run_tag")).strip()
        out = []
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                if ("PCI_RUN_TAG=" + tag).encode() in readf(f"/proc/{pid}/environ", "rb"):
                    out.append(int(pid))
            except OSError:
                pass
        return out

    def kill_tagged(self):
        for pid in self.tagged():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def test_a1_tampered_control_entry_is_detected_and_halts_later_phases(self):
        """أ1: طورٌ يبدّل مدخلَ التحكّم ⇒ لا يُستدعى بعده طور (126) ويُعلن ذلك علناً؛ فلا يُسجَّل نجاحُ طورٍ لم يُنفَّذ غلافُه الأصليّ."""
        self.write_kinds(tiny_kinds(["selftest_tamper", "selftest_ok", "selftest_env"]))
        rs = self.run_all()
        self.assertEqual((self.rc("selftest_tamper"), self.rc("selftest_ok"), self.rc("selftest_env")), ("0", "126", "126"))
        self.assertNotIn("← selftest_env\nHOME=", self.log())
        self.assertIn("الأطوار: ⛔ مدخلُ التحكّم تغيّر", "\n".join(r.stdout for r in rs))
        c = self.counts()
        self.assertEqual(sorted(c["failed_phase_ids"]), ["selftest_env", "selftest_ok"])
        self.assertFalse(c["ok"])

    def test_a1_control_entry_lives_in_a_root_owned_tree_and_its_chain_is_checked(self):
        """أ1: في الإنتاج المدخلُ في /var/lib/pci-ctl (install -d root 0755) لا في بيت pcibuild؛ والسلسلةُ تُفحص من الجذر وقبل كلّ طور؛
        وأربعةُ فحوصٍ سلبيّة بصفة pcibuild: إعادةُ تسمية · بديلٌ في مكانه · إعادةُ تسمية الشجرة · كتابة."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        self.assertIn('CTL_DIR="/var/lib/pci-ctl"', code)
        self.assertNotIn("/home/pcibuild/ctl", code)
        self.assertIn('sudo -n install -d -o root -g root -m 0755 "$CTL_DIR"', code)
        self.assertIn('root_chain_ok "$CTL_DIR/build.sh" && root_chain_ok "$CTL_DIR/iso_probe.py" || return "$(setup_fail ctl-chain)"', code)
        iso = code[code.index("isolation_tests(){"):code.index("foreign_emu_procs(){")]
        for probe in ('mv -f "$1/build.sh"', 'ln -s /bin/true "$1/.pci_probe"', 'mv -f "$1" "$1.pci_probe"', 'printf x >> "$1/build.sh"'):
            self.assertIn(probe, iso)
            self.assertTrue(any(ln.strip().startswith("must_fail ") and probe in ln for ln in iso.splitlines()), probe)
        rbp = code[code.index("run_builder_phase(){"):code.index("run_phases(){")]
        self.assertLess(rbp.index("ctl_intact"), rbp.index("setsid sudo"))
        self.assertIn('/bin/bash "$CTL_DIR/build.sh" phase "$id"', rbp)
        # عددُ الفحوص الكلّيّ: 12 سالباً + 5 لازماً + 4 فحوصٍ يحسبها المشرفُ بنفسه (8 · 13 · 18 · 20) = 21
        manual = iso.count("ISO_N=$((ISO_N + 1))") - 2          # must_fail وmust_pass نفساهما
        self.assertEqual(manual, 4)

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "يحتاج root لتغيير المالك")
    def test_a1_root_chain_ok_rejects_non_root_owner_group_write_and_symlink(self):
        body = readf(os.path.join(HERE, "supervise.sh"))
        fn = body[body.index("root_chain_ok(){"):body.index("# قبل كلّ طور")]
        # أبٌ نظاميٌّ لـroot بلا كتابةٍ لغيره (كما /var/lib في صورة العدّاء)؛ و/tmp بـ1777 يُرفض عمداً
        parent = next((p for p in ("/var/lib", "/root", "/opt") if os.path.isdir(p) and os.stat(p).st_uid == 0
                       and not os.stat(p).st_mode & 0o022), None)
        if parent is None:
            self.skipTest("لا مجلّدَ نظاميٌّ لـroot بلا كتابة")
        base = tempfile.mkdtemp(prefix="pci-chain-test-", dir=parent)
        self.addCleanup(shutil.rmtree, base, True)
        os.chmod(base, 0o755)
        d = os.path.join(base, "ctl"); os.makedirs(d); os.chmod(d, 0o755)
        f = os.path.join(d, "build.sh"); open(f, "w").close(); os.chmod(f, 0o644)

        def ok(path):
            return subprocess.run(["bash", "-c", fn + '\nroot_chain_ok "$1"', "_", path]).returncode == 0
        if not ok(f):
            self.skipTest("آباءُ المجلّد المؤقّت ليست لـroot بلا كتابة (بيئةُ الاختبار)")
        os.chmod(d, 0o775); self.assertFalse(ok(f)); os.chmod(d, 0o755)            # كتابةٌ للمجموعة
        os.chmod(f, 0o646); self.assertFalse(ok(f)); os.chmod(f, 0o644)            # كتابةٌ للغير
        os.chown(d, 65534, -1); self.assertFalse(ok(f)); os.chown(d, 0, -1)        # أبٌ لغير root
        os.chown(f, 65534, -1); self.assertFalse(ok(f)); os.chown(f, 0, -1)
        link = os.path.join(base, "lnk"); os.symlink(d, link)
        self.assertFalse(ok(os.path.join(link, "build.sh")))                        # رابطٌ في الآباء
        self.assertFalse(ok("relative/build.sh"))
        self.assertFalse(ok(os.path.join(d, "..", "ctl", "build.sh")))
        self.assertTrue(ok(f))

    def test_a3_escaped_background_process_is_killed_by_cleanup(self):
        """أ3: عمليّةٌ تهرب من جلسة الطور (setsid) يقتلها التنظيفُ بالحساب لا بالجلسة، ويُتحقَّق من زوالها ⇒ ready=true."""
        self.write_kinds(tiny_kinds(["selftest_linger", "selftest_ok"]))
        self.addCleanup(self.kill_tagged)
        self.run_all(("prepare", "clone", "build"))
        self.assertTrue(self.tagged(), "لم تبقَ العمليّةُ الهاربة بعد الطور (الاختبارُ لا يختبر شيئاً)")
        r = self.sup("finalize")
        self.assertEqual(self.rc("cleanup"), "0")
        self.assertEqual(self.tagged(), [])
        self.assertEqual(self.ready(), "ready=true", r.stdout)

    def test_a3_surviving_builder_process_blocks_reading_the_tree(self):
        """أ3: بقاءُ عمليّةٍ للبناء بعد التنظيف ⇒ cleanup فاشل · ready=false · لا JUnit يُقرأ · وسطرٌ علنيٌّ بالعدد وحده."""
        self.write_kinds(tiny_kinds(["selftest_linger"], kind="app-test"))
        self.addCleanup(self.kill_tagged)
        self.run_all(("prepare", "clone"), PCI_KIND="app-test")
        base = os.path.join(self.rt, "builder", "src", "app", "build", "test-results", "t")
        os.makedirs(base)
        with open(os.path.join(base, "a.xml"), "w") as f:
            f.write('<testsuite tests="4" failures="0" errors="0" skipped="0"/>')
        self.sup("build", PCI_KIND="app-test")
        r = self.sup("finalize", PCI_KIND="app-test", PCI_TEST_CLEANUP_SKIP_KILL="1")
        self.assertEqual(self.rc("cleanup"), "1")
        self.assertEqual(self.ready(), "ready=false")
        self.assertIn("التنظيف: ⛔ بقيت 1 عمليّةً لمستخدم البناء — لا تُقرأ شجرتُه", r.stdout)
        self.assertIn("الناتج: ⛔", r.stdout)
        with open(os.path.join(self.pci, "art", "counts.json"), "rb") as f:
            c = json.loads(f.read())
        self.assertEqual((c["tests"], c["reports"], c["ok"]), (0, 0, False))
        self.assertIn("cleanup", c["failed_phase_ids"])
        self.assertFalse(os.path.exists(os.path.join(self.pci, "art", "log.txt.gz.age")), "شُفِّر ناتجٌ غيرُ جاهز")

    def test_a4_phase_timeout_kills_its_whole_session(self):
        """أ4: انقضاءُ مهلة الطور يقتل جلستَه كلَّها (لا ينجو ابنٌ) ويسجّل 124، ويجري الطورُ التالي."""
        self.write_kinds(tiny_kinds(["selftest_sleep", "selftest_ok"]))
        self.addCleanup(self.kill_tagged)
        self.run_all(("prepare", "clone"))
        t0 = time.time()
        self.sup("build", PCI_TEST_PHASE_SECS="2")
        self.assertLess(time.time() - t0, 40)
        self.assertEqual((self.rc("selftest_sleep"), self.rc("selftest_ok")), ("124", "0"))
        self.assertIn("انقضت مهلةُ طور selftest_sleep", self.log())
        self.assertEqual(self.tagged(), [])

    def test_a4_background_process_holding_output_does_not_block_the_supervisor(self):
        """أ4: عمليّةٌ خلفيّةٌ تمسك مخرجَ الطور بعد انتهائه لا تعلّق المشرف (لا انتظارَ EOF)؛ والطورُ ناجحٌ برمزه الفعليّ."""
        self.write_kinds(tiny_kinds(["selftest_hold", "selftest_ok"]))
        self.addCleanup(self.kill_tagged)
        self.run_all(("prepare", "clone"))
        t0 = time.time()
        r = self.sup("build")
        self.assertLess(time.time() - t0, 30, "انتظر المشرفُ EOF من عمليّةٍ خلفيّة")
        self.assertIn("الأطوار (build): 2 · فاشلة 0", r.stdout)
        self.sup("finalize")
        self.assertEqual(self.ready(), "ready=true")
        self.assertEqual(self.tagged(), [])

    def test_public_descriptor_is_never_inherited_by_builder_processes(self):
        """الواصفةُ 3 (السجلُّ العلنيّ) تُغلق لكلّ عمليّةٍ بمستخدم البناء وللمصرف: لا كتابةَ منها إلى العلن ولا إمساكَ لأنبوب الخطوة."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        fn = code[code.index("as_builder(){"):code.index("root_chain_ok(){")]
        self.assertEqual(fn.count("3>&-"), 2)
        rbp = code[code.index("run_builder_phase(){"):code.index("run_phases(){")]
        self.assertEqual(rbp.count("3>&-"), 3)

    def test_a4_builder_runs_under_a_process_cap(self):
        self.write_kinds(tiny_kinds(["selftest_limits"]))
        self.run_all()
        log = self.log()
        seg = log[log.index("← selftest_limits"):]
        n = int(re.search(r"(?m)^(\d+)$", seg).group(1))
        self.assertLessEqual(n, 4096)
        body = readf(os.path.join(HERE, "supervise.sh"))
        self.assertIn('"${LIMIT[@]}" /bin/bash "$CTL_DIR/build.sh" phase', body)
        self.assertIn('must_pass bash -c \'l="$(ulimit -u)"', body)

    def test_a2_emulator_and_adb_never_run_as_the_runner(self):
        """أ2: لا إجراءَ محاكٍ بحساب العدّاء في السير؛ والمشرفُ لا يستدعي adb ولا emulator ولا avdmanager؛ والمحاكي يُقلعه طورُ
        emu_up بمستخدم البناء بمنفذ خادمٍ خاصّ؛ وفحوصُ 18–20 قبل الاستنساخ وفحصُ 18 ثانيةً في finalize."""
        wf = readf(os.path.join(HERE, "..", "..", ".github", "workflows", "private-ci.yml"))
        self.assertNotIn("android-emulator-runner", wf)
        self.assertIn('run: bash "$RUNNER_TEMP/pci/ctl/supervise.sh" emu', wf)
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotRegex(code, r"platform-tools/adb|emulator/emulator|\bavdmanager\b")
        # الإصلاحُ الخامس: كلُّ استدعاء adb في المشرف بمستخدم البناء (as_builder) — والمسبارُ يستدعيه عبر badb وحدها
        for ln in code.splitlines():
            if re.search(r"\badb\s+(start-server|-s|shell|kill|reverse)", ln):
                self.assertRegex(ln, r"\bas_builder\s+(timeout\s+\S+\s+)?adb\s", ln)
        self.assertIn('"ANDROID_ADB_SERVER_PORT=$ADB_PORT"', code)
        self.assertNotIn("ADB_PORT=5037", code)
        iso = code[code.index("isolation_tests(){"):code.index("foreign_emu_procs(){")]
        self.assertIn('fe="$(foreign_emu_procs)"', iso)
        self.assertIn('iso_probe.py" deny /var/run/docker.sock', iso)
        self.assertIn("sock_probe", iso)
        fin = code[code.index("do_finalize(){"):]
        self.assertLess(fin.index("foreign_emu_procs"), fin.index("do_cleanup"))
        build = readf(os.path.join(HERE, "build.sh"))
        self.assertIn('setsid "$sdk/emulator/emulator"', build)
        self.assertIn("ANDROID_ADB_SERVER_PORT", build)
        self.assertEqual(self.sup("prepare").returncode, 0)
        self.assertEqual(readf(os.path.join(self.pci, "state", "run_tag")).strip().__len__(), 32)

    # ───── الإصلاحُ الخامس (مراجعة Codex 14) ─────
    def test_fix5_ownership_timeout_is_not_ready_and_tree_is_not_read(self):
        """أ4: التملّكُ بمهلةٍ من ميزانية finalize الكلّيّة؛ انقضاؤها ⇒ --no-tree وready=false وسطرٌ علنيٌّ ثابت — ولا يتجاوز finalize ميزانيتَه."""
        self.write_kinds(tiny_kinds(["selftest_ok"], kind="app-test"))
        self.run_all(("prepare", "clone", "build"), PCI_KIND="app-test")
        base = os.path.join(self.rt, "builder", "src", "app", "build", "test-results", "t")
        os.makedirs(base)
        with open(os.path.join(base, "a.xml"), "w") as f:
            f.write('<testsuite tests="4" failures="0" errors="0" skipped="0"/>')
        t0 = time.time()
        r = self.sup("finalize", PCI_KIND="app-test", PCI_TEST_FIN_RESERVE="242", PCI_TEST_OWN_SLEEP="120")
        self.assertLess(time.time() - t0, 60, "لم تُحترم مهلةُ التملّك")
        self.assertRegex(r.stdout, r"(?m)^الإنهاء: ⛔ انقضت مهلةُ التملّك — لا تُقرأ الشجرة \(\d+ ث من -?\d+ متاحة\)$")
        self.assertIn("الناتج: ⛔ غيرُ جاهزٍ — لا رفع", r.stdout)
        self.assertEqual(self.ready(), "ready=false")
        with open(os.path.join(self.pci, "art", "counts.json"), "rb") as f:
            c = json.loads(f.read())
        self.assertEqual((c["tests"], c["reports"], c["ok"]), (0, 0, False))   # الشجرةُ لم تُقرأ
        m = re.search(r"(?m)^الإنهاء: (\d+) ث من ميزانيةٍ أقصاها (\d+)$", r.stdout)
        self.assertTrue(m, r.stdout)
        self.assertLessEqual(int(m.group(1)), int(m.group(2)))

    def test_fix5_ownership_time_is_reported_when_it_succeeds(self):
        self.run_all()
        out = self.outputs[-1][1].stdout
        self.assertRegex(out, r"(?m)^الإنهاء: التملّك \d+ ث من \d+ متاحة$")
        self.assertRegex(out, r"(?m)^الإنهاء: \d+ ث من ميزانيةٍ أقصاها 365$")
        self.assertEqual(self.ready(), "ready=true")

    def test_fix5_finalize_budget_is_bounded_by_fin_reserve_in_the_source(self):
        """الإثباتُ بالقراءة: سقوفُ ما بعد التملّك + هامشُ القتل + انتظارُ التنظيف (3 × 25 × 0.2 ث) < FIN_RESERVE؛ ولا مهلةَ ثابتةً
        خارج الميزانية في finalize؛ والتملّكُ chown تحت `sudo -n timeout` (يُقتل chown نفسُه)."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        val = lambda name: int(re.search(rf"\b{name}=(\d+)", body).group(1))   # noqa: E731
        after = val("FIN_CAP_COUNTS") + val("FIN_CAP_GUARD") + val("FIN_CAP_ENC") + val("FIN_CAP_CHECK") + 4 * val("FIN_KILL")
        self.assertIn("FIN_AFTER_OWN=$((FIN_CAP_COUNTS + FIN_CAP_GUARD + FIN_CAP_ENC + FIN_CAP_CHECK + 4 * FIN_KILL))", body)
        self.assertLess(after + 3 * 25 * 0.2 + val("FIN_KILL"), val("FIN_RESERVE"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        fin = code[code.index("do_finalize(){"):code.index('if [ "$MODE" != "prepare" ]')]
        self.assertNotRegex(fin, r"\btimeout\s+\d+\s")                 # كلُّ مهلةٍ محسوبةٌ من الميزانية
        self.assertEqual(fin.count('timeout -k "$FIN_KILL" "${t}s"'), 4)
        self.assertIn("FIN_END=$(( be < t0 + FIN_RESERVE ? be : t0 + FIN_RESERVE ))", fin)
        own = code[code.index("take_ownership(){"):code.index("do_finalize(){")]
        self.assertIn('sudo -n timeout -k "$FIN_KILL" "${left}s" chown -R -P', own)
        self.assertNotRegex(own, r"sudo -n chown")

    def run_sock_probe(self, scan_rc, out, connect_rc=0):
        """يشغّل sock_probe معزولةً عن سائر المشرف (run_root وas_builder بدائلُ تعيد ما يُعطى)."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        rx = re.search(r"(?m)^SOCK_RX=.*$", body).group(0)
        fn = body[body.index("sock_probe(){"):body.index("\n}\n", body.index("sock_probe(){")) + 3]
        script = (f"{rx}\nrun_root(){{ return {scan_rc}; }}\nas_builder(){{ cat >/dev/null; printf '%s\\n' \"$FAKE\"; return {connect_rc}; }}\n"
                  f"{fn}\nPCI_STATE=$1; BUSER=$(id -un); CTL_DIR=/nonexistent\n"
                  "if sock_probe; then echo \"OK|$SOCK_PUB\"; else echo \"BAD|$SOCK_PUB\"; fi\n")
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(["bash", "-c", script, "_", d], env=dict(os.environ, FAKE=out), capture_output=True, text=True)
        return r.stdout.strip()

    def test_fix5_sock_probe_reads_the_classified_line_and_fails_closed(self):
        good = "n=15 accepted=0 denied=14 refused=0 vanished=1 other=0 inventory=complete"
        self.assertEqual(self.run_sock_probe(0, good),
                         "OK|مقابسُ حساب العدّاء: 15 فُحصت · مرفوضةٌ بالصلاحية 14 · بلا مُنصِت 0 · زالت 1 · أخرى 0 · قَبِلت 0 · الجردُ تامّ")
        self.assertTrue(self.run_sock_probe(3, good).startswith("BAD|"))          # المصدرُ قال «ناقص»
        self.assertIn("⛔ الجردُ ناقص", self.run_sock_probe(3, good))
        for bad in (good.replace("refused=0", "refused=1"), good.replace("other=0", "other=1"),
                    good.replace("accepted=0", "accepted=1"), good.replace("complete", "incomplete")):
            with self.subTest(bad=bad):
                self.assertTrue(self.run_sock_probe(0, bad).startswith("BAD|"))
        self.assertTrue(self.run_sock_probe(0, good, connect_rc=1).startswith("BAD|"))
        self.assertEqual(self.run_sock_probe(0, "15 0"), "BAD|مقابسُ حساب العدّاء: ⛔ نتيجةٌ غيرُ مقروءة")   # الصيغةُ القديمة
        self.assertEqual(self.run_sock_probe(1, good), "BAD|مقابسُ حساب العدّاء: ⛔ تعذّر الجرد")

    def test_fix5_emu_procs_counts_builder_and_foreign_and_fails_closed(self):
        body = readf(os.path.join(HERE, "supervise.sh"))
        fn = body[body.index("emu_procs(){"):body.index("\n}\n", body.index("emu_procs(){")) + 3]
        me = os.getuid()
        lines = f"{me} adb\\n{me} qemu-system-x86\\n{me} emulator\\n{me + 1} adb\\n{me} bash"
        tmo = 'tmo(){ "${@:2}"; }\n'     # الإصلاحُ السادس: ps تحت tmo — والبديلُ ينفّذ الأمرَ (الدالّةَ المزيَّفة) كما هو
        r = subprocess.run(["bash", "-c", f"ISO=0\n{tmo}ps(){{ printf '{lines}\\n'; }}\n{fn}\nemu_procs"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "1 2 1")
        r = subprocess.run(["bash", "-c", f"ISO=0\n{tmo}ps(){{ return 1; }}\n{fn}\nemu_procs"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "? ? 1")                                # تعذّرُ ps لا يُقرأ صفراً
        r = subprocess.run(["bash", "-c", f"ISO=0\ntmo(){{ return 124; }}\n{fn}\nemu_procs"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "? ? 1")                                # وانقضاءُ مهلته كذلك

    def test_fix5_emu_probe_is_trusted_and_uses_adb_only_as_the_builder(self):
        """مسبارُ المحاكي طورٌ ثابتٌ في المشرف (لا من مصدرٍ خاصّ) · يُثبت الإيجابَ (≥1 بمستخدم البناء) والصفرَ الأجنبيّ · وdeny --exists ·
        وضابطٌ موجبٌ عبر ADB قبل الحكم على الوسيط · وإعادةُ الجرد أثناء التشغيل وبعده · ويفشل بأيّ فحص."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        ep = code[code.index("emu_probe(){"):code.index("run_runner_phase(){")]
        self.assertIn("emu_probe) ( emu_probe \"$2\" ) ;;", code)
        for needle in ('ep ge1 "$a"; ep ge1 "$e"; ep [ "$f" = 0 ]', 'deny "$broker" --exists', 'deny "$sens" --exists',
                       "deny /var/run/docker.sock", "reverse tcp:9998", "reverse tcp:9999", '"$CONTROL_MARK"',
                       'ep [ "$thru" = 0 ]', "emu kill", "kill-server"):
            self.assertIn(needle, ep)
        self.assertEqual(ep.count("sock_probe"), 2)
        self.assertIn('if [ "$ok" = "$n" ]; then pub "مسبارُ المحاكي: ✅ $ok/$n"; return 0; fi', ep)
        self.assertNotIn("PCI_SRC", ep)
        self.assertNotIn("$BSRC", ep)
        kinds = json.loads(readf(os.path.join(HERE, "kinds.json")))
        self.assertEqual([k for k, v in kinds.items() if not k.startswith("_") and any(p["id"] == "emu_probe" for p in v["phases"])],
                         ["dry-run-emu"])
        self.assertEqual(kinds["dry-run-emu"]["legs"][0]["emu"]["target"], "default")
        build = readf(os.path.join(HERE, "build.sh"))
        self.assertIn("stress_tree)      stress_tree ;;", build)
        # درسُ التجربة 42282855425: صورةُ النظام تُثبَّت بـumask 022 وتُفتح للقراءة (المشرفُ بـumask 077 وإلا فلا يقرؤها مستخدمُ البناء)
        si = code[code.index("emu_sysimg(){"):code.index("run_runner_phase(){")]
        self.assertIn('( umask 022; SDKMANAGER_OPTS=', si)
        # ودرسُ 42283857840: ADB_SERVER_SOCKET بعنوانٍ رقميّ يجعل adb يرفض تشغيلَ خادمه («remote host») ⇒ المنفذُ وحده
        self.assertNotIn('"ADB_SERVER_SOCKET=', code)
        self.assertIn('chmod -R go-w,a+rX "$sdk/system-images" "$sdk/emulator" "$sdk/platform-tools"', si)

    # ───── الإصلاحُ السادس (مراجعة Codex 15) ─────
    def test_fix6_emu_probe_requires_in_device_control(self):
        """الملاحظة 1: نجاحُ الضابط من داخل الجهاز شرطٌ في الحكم — لا أداة · مستمعٌ لم يُنصت · رفضُ reverse لمنفذه · انقضاءُ المهلة
        ⇒ «العبورُ غيرُ مقيس ⇒ فشل»؛ ولا سطرَ «يُكتفى بإثبات الجهة المضيفة»؛ وفحصُ العبور باقٍ؛ والفحوصُ 19 في الحالة السليمة."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        ep = code[code.index("emu_probe(){"):code.index("run_runner_phase(){")]
        self.assertIn("ctl_ok=0", ep.split("\n")[1])                         # يُهيَّأ في رأس الدالّة قبل أيّ حلقة
        self.assertIn('ep [ "$ctl_ok" = 1 ]', ep)
        self.assertIn('ep [ "$thru" = 0 ]', ep)
        self.assertLess(ep.index('ep [ "$ctl_ok" = 1 ]'), ep.index('ep [ "$thru" = 0 ]'))
        self.assertIn('pub "المحاكي: ⛔ ${ctl_why:-الضابطُ لم يصل} — العبورُ غيرُ مقيس ⇒ فشل"', ep)
        for why in ("لا أداةَ اتّصالٍ داخل الجهاز وصلت بالضابط", "لم يُنصت مستمعُ الضابط",
                    "رُفض تسجيلُ التحويل العكسيّ (reverse) لمنفذ الضابط", "انقضت مهلةُ الاتّصال بالضابط من داخل الجهاز",
                    "انقضت مهلةُ تسجيل التحويل العكسيّ لمنفذ الضابط"):
            self.assertIn(f'ctl_why="{why}', ep)
        self.assertNotIn("يُكتفى بإثبات الجهة المضيفة", ep)
        # العبورُ لا يُقاس إلا بعد ضابطٍ ناجح: علامةُ الوسيط تُقرأ داخل فرع ctl_ok=1 وحده
        branch = ep[ep.index('if [ "$ctl_ok" = 1 ]; then'):ep.index('ep [ "$ctl_ok" = 1 ]')]
        self.assertIn("thru=1", branch.split("\n  else\n")[0])
        self.assertNotIn("thru=1", branch.split("\n  else\n")[1])
        # عدُّ الفحوص في الحالة السليمة = 19 (كلُّ ep خارج فروع الفشل «ep false»، ودون تعريف ep نفسِه)
        self.assertEqual(len(re.findall(r"(?<![\w-])ep (?!false)", ep)), 19)

    def test_fix6_emu_probe_control_branches_by_execution(self):
        """تنفيذاً: كتلةُ الضابط معزولةً ببدائلَ لـbadb وwait_file — كلُّ حالة فشلٍ تُطبع «غيرُ مقيس ⇒ فشل» وctl_ok=0، والنجاحُ ctl_ok=1."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        blk = body[body.index('  ctl_why=""\n  as_builder setsid'):body.index('  ep [ "$ctl_ok" = 1 ]')]

        def run(wait_ok, rev_rc, shell_rc, shell_out):
            script = ("pub(){ printf '%s\\n' \"$*\"; }\nas_builder(){ return 0; }\n"
                      f"wait_file(){{ return {0 if wait_ok else 1}; }}\n"
                      f"badb(){{ case \"$2\" in reverse) return {rev_rc};; shell) printf '%s\\n' '{shell_out}'; return {shell_rc};; esac; }}\n"
                      "BHOME=/x; broker=/y; secs=5; LISTENER=x; CONTROL_MARK=PCI-CONTROL-MARK; BROKER_MARK=PCI-BROKER-MARK\n"
                      "ctl_ok=0; thru=0; tool=''\n" + blk + 'echo "ctl_ok=$ctl_ok thru=$thru"\n')
            return subprocess.run(["bash", "-c", "set -uo pipefail\n" + script], capture_output=True, text=True).stdout
        ok = run(True, 0, 0, "PCI-CONTROL-MARK")
        self.assertIn("الضابطُ وصل ✅", ok)
        self.assertIn("ctl_ok=1 thru=0", ok)
        for label, args, why in (("no-tool", (True, 0, 127, "not found"), "لا أداةَ اتّصالٍ داخل الجهاز وصلت بالضابط"),
                                 ("listener", (False, 0, 0, "PCI-CONTROL-MARK"), "لم يُنصت مستمعُ الضابط"),
                                 ("reverse", (True, 1, 0, "PCI-CONTROL-MARK"), "رُفض تسجيلُ التحويل العكسيّ (reverse) لمنفذ الضابط"),
                                 ("timeout", (True, 0, 124, ""), "انقضت مهلةُ الاتّصال بالضابط من داخل الجهاز")):
            with self.subTest(label=label):
                out = run(*args)
                self.assertIn(f"المحاكي: ⛔ {why}", out)
                self.assertIn("— العبورُ غيرُ مقيس ⇒ فشل", out)
                self.assertIn("ctl_ok=0", out)

    CLEAN_FUNCS = ("emu_procs(){", "tagged_pids(){", "builder_count(){", "kill_builder(){", "do_cleanup(){")

    def test_fix6_cleanup_external_commands_are_all_under_a_deadline(self):
        """ملاحظة finalize: كلُّ ps/pgrep/pkill/crontab/id/grep-على-/proc في دوالّ التنظيف وعدِّ العمليّات ملفوفٌ بـtmo أو rtmo؛
        ولا انتظارَ بعدّ دوراتٍ (seq) بل بساعة الحائط؛ وأسوأُ الحالة من الثوابت ≤ FIN_CAP_CLEAN، والحجزُ يتّسع له وللتملّك وما بعده."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        self.assertIn('tmo(){ timeout -k "$FIN_CMD_KILL" "${1}s" "${@:2}"; }', code)
        self.assertIn('sudo -n timeout -k "$FIN_CMD_KILL" "${1}s" "${@:2}"', code)
        # foreign_emu_procs سطرٌ واحدٌ يستدعي emu_procs وحدها (لا أمرَ خارجيّ فيه)
        self.assertIn('foreign_emu_procs(){ local a e f; read -r a e f <<<"$(emu_procs)"; echo "$f"; }', code)
        for name in self.CLEAN_FUNCS:
            i = code.index("\n" + name) + 1
            fn = code[i:code.index("\n}\n", i) + 3]
            with self.subTest(fn=name):
                hits = list(re.finditer(r"(?<![\w-])(ps|pgrep|pkill|crontab|id -u pcibuild|grep -lsaF)(?![\w-])", fn))
                for m in hits:
                    pre = fn[max(0, m.start() - 20):m.start()]
                    self.assertRegex(pre, r'r?tmo "\$FIN_CMD" $', f"{name}: {m.group(0)} بلا مهلة")
                self.assertNotIn("seq ", fn)
                self.assertNotIn("sudo -n", fn)                               # sudo هنا لا يكون إلا داخل rtmo
        self.assertIn('rtmo "$FIN_CMD" pkill', code)                     # الفحصُ أعلاه ليس فارغاً: pkill موجودٌ وملفوف
        val = lambda name: int(re.search(rf"\b{name}=(\d+)", body).group(1))   # noqa: E731
        P = val("FIN_CMD") + val("FIN_CMD_KILL")
        R = P + val("FIN_CMD_KILL")
        worst = 2 * P + R + 3 * (R + val("FIN_WAIT") + P)              # foreign_emu_procs + crontab + 3 جولات
        self.assertLessEqual(worst, val("FIN_CAP_CLEAN"))
        after = val("FIN_CAP_COUNTS") + val("FIN_CAP_GUARD") + val("FIN_CAP_ENC") + val("FIN_CAP_CHECK") + 4 * val("FIN_KILL")
        self.assertLessEqual(val("FIN_CAP_CLEAN") + val("FIN_OWN_MIN") + val("FIN_KILL") + after, val("FIN_RESERVE"))
        # وخطوةُ finalize في السير لها موعدٌ خارجيّ يغطّي FIN_RESERVE وهامشاً؛ وخطوتا الأطوار مهلتُهما الميزانيةُ كلُّها
        wf = readf(os.path.join(HERE, "..", "..", ".github", "workflows", "private-ci.yml"))
        fin = wf[wf.index("- name: العدّ والتشفير والفحص"):wf.index("- name: رفع الناتج")]
        mins = int(re.search(r"timeout-minutes: (\d+)", fin).group(1))
        self.assertGreater(mins * 60, val("FIN_RESERVE") + 60)
        for step in ("- name: البناء والاختبار", "- name: المحاكي بمستخدم البناء"):
            blk = wf[wf.index(step):wf.index("- name:", wf.index(step) + 5)]
            self.assertIn("timeout-minutes: ${{ matrix.leg.timeout }}", blk)

    def test_fix6_builder_count_fails_closed_on_timeout(self):
        """انقضاءُ مهلة pgrep (124) لا يُقرأ صفراً: builder_count ⇒ «?» فيبقى التنظيفُ فاشلاً ولا تُقرأ الشجرة."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        fn = body[body.index("builder_count(){"):body.index("\n}\n", body.index("builder_count(){")) + 3]
        for rc, out, want in ((124, "", "?"), (137, "", "?"), (1, "", "0"), (0, "11\\n12", "2"), (2, "", "?")):
            with self.subTest(rc=rc):
                script = f"ISO=1; FIN_CMD=3\ntmo(){{ printf '{out}'; return {rc}; }}\n{fn}\nbuilder_count"
                r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
                self.assertEqual(r.stdout.strip(), want)

class IsoProbeTest(unittest.TestCase):
    """أ2: مسبارُ المقابس — الجردُ يلتقط مقابسَ الحساب ومجموعاته (لا مجموعات البناء)، والاتّصالُ يُعدّ ولا يطبع اسماً."""

    def test_fs_candidates_by_owner_and_group(self):
        with tempfile.TemporaryDirectory() as d:
            mine, other = os.path.join(d, "mine.sock"), os.path.join(d, "sub", "grp.sock")
            os.makedirs(os.path.dirname(other))
            socks = []
            for p in (mine, other):
                s = __import__("socket").socket(__import__("socket").AF_UNIX)
                s.bind(p); s.listen(1); socks.append(s)
            self.addCleanup(lambda: [s.close() for s in socks])
            open(os.path.join(d, "plain"), "w").close()
            uid, gid = os.getuid(), os.getgid()
            got = iso_probe.fs_candidates(uid, set(), set(), roots=(d,))
            self.assertEqual(sorted(got), sorted([mine, other]))
            self.assertEqual(iso_probe.fs_candidates(uid + 12345, set(), set(), roots=(d,)), [])
            self.assertEqual(sorted(iso_probe.fs_candidates(uid + 12345, {gid}, set(), roots=(d,))), sorted([mine, other]))
            self.assertEqual(iso_probe.fs_candidates(uid + 12345, {gid}, {gid}, roots=(d,)), [])   # مجموعةٌ يملكها البناءُ أيضاً

    def test_connect_counts_without_printing_names_and_deny(self):
        import socket
        with tempfile.TemporaryDirectory() as d:
            live = os.path.join(d, "live.sock")
            s = socket.socket(socket.AF_UNIX); s.bind(live); s.listen(4)
            self.addCleanup(s.close)
            dead = os.path.join(d, "dead.sock")
            s2 = socket.socket(socket.AF_UNIX); s2.bind(dead); s2.close()      # ملفُّ مقبسٍ بلا مُنصِت
            data = b"".join(x.encode() + b"\0" for x in (live, dead, os.path.join(d, "none")))
            complete = b"!inventory=complete truncated=0 walk_errors=0 unix_error=0 fd_errors=0 fd_vanished=0\0"
            r = subprocess.run([sys.executable, "-I", "-S", os.path.join(HERE, "iso_probe.py"), "connect"],
                               input=data + complete, capture_output=True)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stdout.decode().strip(),
                             "n=3 accepted=1 denied=0 refused=1 vanished=1 other=0 inventory=complete")
            self.assertNotIn(d.encode(), r.stdout + r.stderr)
            r0 = subprocess.run([sys.executable, "-I", "-S", os.path.join(HERE, "iso_probe.py"), "connect"],
                                input=os.path.join(d, "none").encode() + b"\0" + complete, capture_output=True)
            self.assertEqual((r0.returncode, r0.stdout.decode().strip()),
                             (0, "n=1 accepted=0 denied=0 refused=0 vanished=1 other=0 inventory=complete"))
            self.assertEqual(iso_probe.cmd_deny(os.path.join(d, "none")), 0)   # لا مقبس
            self.assertEqual(iso_probe.cmd_deny(live), 1)                       # قَبِل ⇒ فشل
            self.assertEqual(iso_probe.cmd_deny(dead), 2)                       # ECONNREFUSED: الصلاحيةُ متاحة ⇒ ليس نجاحاً

    def test_abstract_listening_socket_held_by_the_account_is_found(self):
        import socket
        name = "pci-probe-test-%d" % os.getpid()
        s = socket.socket(socket.AF_UNIX); s.bind("\0" + name); s.listen(1)
        self.addCleanup(s.close)
        self.assertIn("@" + name, iso_probe.abstract_candidates(os.getuid()))
        self.assertNotIn("@" + name, iso_probe.abstract_candidates(os.getuid() + 12345))
        self.assertEqual(iso_probe.try_connect("@" + name)[0], True)


class BuildScriptTest(unittest.TestCase):
    """build.sh طورٌ واحدٌ بلا sudo ولا رموز خروج ولا كتابة counts."""

    def test_no_sudo_and_no_rc_or_counts_logic(self):
        body = readf(os.path.join(HERE, "build.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotRegex(code, r"\bsudo\b")
        self.assertNotIn("rc_set", code)
        self.assertNotIn("counts_schema", code)
        self.assertNotIn("encrypt_log", code)
        self.assertNotIn("RUNNER_TEMP", code)
        self.assertNotIn("free_disk", code)
        self.assertNotIn("enable_kvm", code)
        self.assertIn("PCI_WHISPER_REF", code)

    def test_phase_runs_with_exit_code_and_unknown_phase_fails(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": d, "GITHUB_WORKFLOW_REF": REF_OK,
                   "PCI_KIND": "dry-run", "PCI_SRC": d, "PCI_BSTATE": os.path.join(d, "st"),
                   "PCI_BDEVICE": os.path.join(d, "dv")}
            for phase, want in (("selftest_ok", 0), ("selftest_fail", 1), ("nope", 2)):
                r = subprocess.run(["bash", os.path.join(HERE, "build.sh"), "phase", phase], env=env, capture_output=True,
                                   text=True, timeout=30)
                self.assertEqual(r.returncode, want, (phase, r.stdout))
            r = subprocess.run(["bash", os.path.join(HERE, "build.sh"), "step", "x"], env=env, capture_output=True,
                               text=True, timeout=30)
            self.assertEqual(r.returncode, 2)

    def test_ffmpeg_static_dir_is_added_to_path_for_every_phase(self):
        """ensure_ffmpeg.sh بلا sudo يبني تحت HOME؛ وكلُّ طورٍ عمليّةٌ جديدة فيلزم أن يجد ffmpeg في المسار."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".local", "ffmpeg-static"))
            env = {"PATH": "/usr/bin:/bin", "HOME": d, "GITHUB_WORKFLOW_REF": REF_OK, "PCI_KIND": "dry-run", "PCI_SRC": d,
                   "PCI_BSTATE": os.path.join(d, "st"), "PCI_BDEVICE": os.path.join(d, "dv")}
            body = readf(os.path.join(HERE, "build.sh"))
            self.assertIn('export PATH="$HOME/.local/ffmpeg-static:$PATH"', body)
            r = subprocess.run(["bash", "-c", 'source <(sed -n "/^\\[ -d/p" "%s"); echo "$PATH"' % os.path.join(HERE, "build.sh")],
                               env=env, capture_output=True, text=True)
            self.assertTrue(r.stdout.startswith(os.path.join(d, ".local", "ffmpeg-static") + ":"), r.stdout)


if __name__ == "__main__":
    unittest.main()
