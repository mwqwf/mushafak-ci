#!/usr/bin/env python3
"""🧪 سكربتاتُ الصدفة (v3): نحوٌ سليم (bash -n) · رفضُ الاستدعاء من غير private-ci.yml · لا حرفَ إلى السجلّ العلنيّ ·
الاستنساخُ بلا مفتاح يفشل ولا يترك أثراً (وtrap على الإشارة) · الاستنساخُ التجريبيّ من مستودعٍ محلّيّ · زوالُ المفتاح ووكيلِه
(حيث ssh-agent) · التشفيرُ ذهاباً وإياباً بـage · ورفضُ المفتاح الوهميّ لمصدرٍ خاصّ وقبولُ مفتاحٍ حقيقيّ ·
والمشرفُ supervise.sh في الوضع المحلّيّ: prepare ← clone ← أطوار ← finalize ⇒ ready=true ومجلّدٌ بالملفّين بالضبط ·
رمزُ كلّ طورٍ من خروجه الفعليّ · بيئةٌ مسموحةٌ لا غير · بترُ السجلّ عند الحدّ · ولا حرفَ من السجلّ إلى stdout العلنيّ ·
ويُتجاهل وضعُ الاختبار داخل Actions."""
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
    r"^(العزل: .*|الاستنساخ: [✅⛔].*|الأطوار \((build|emu)\): \d+ · فاشلة \d+|الناتج: .*|المشرف: ⛔ .*)$")
ALLOWED_BUILDER_ENV = {
    "HOME", "PATH", "JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_USER_HOME", "GRADLE_USER_HOME", "GRADLE_OPTS",
    "CI", "TERM", "LANG", "PYTHONIOENCODING", "GITHUB_WORKFLOW_REF", "PCI_KIND", "PCI_LEG", "PCI_LINT", "PCI_SRC",
    "PCI_BSTATE", "PCI_BDEVICE", "PCI_WHISPER_REF",
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
        return e

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

    def test_deadline_is_leg_timeout_minus_six_minutes(self):
        self.write_kinds(tiny_kinds(["selftest_ok"], timeout=75))
        before = int(time.time())
        self.sup("prepare")
        dl = int(readf(os.path.join(self.pci, "state", "deadline")))
        self.assertTrue(before + 69 * 60 - 5 <= dl <= int(time.time()) + 69 * 60 + 5, dl - before)

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

    def test_no_isolation_flag_is_ignored_inside_actions(self):
        """داخل Actions يُتجاهل PCI_TEST_NO_ISOLATION: يجري العزلُ الحقيقيّ (sudo مزيَّفٌ هنا يفشل) ولا يُنشأ بيتُ البناء المحلّيّ."""
        fake = os.path.join(self.d, "fakebin"); os.makedirs(fake)
        calls = os.path.join(self.d, "sudo_calls")
        with open(os.path.join(fake, "sudo"), "w") as f:
            f.write('#!/bin/sh\necho "$@" >> "%s"\nexit 1\n' % calls)
        os.chmod(os.path.join(fake, "sudo"), 0o755)
        # ⛔ يمنع المشرفُ كلَّ sudo حقيقيٍّ هنا: مسارُه ثابتٌ، والمزيَّفُ يدخل بـPCI_TEST_BIN_DIR (مرفوضٌ على عدّاءٍ حقيقيّ)
        self.assertEqual(subprocess.run(["bash", "-c", 'PATH="%s:$PATH"; command -v sudo' % fake], capture_output=True,
                                        text=True).stdout.strip(), os.path.join(fake, "sudo"))
        kw = dict(GITHUB_ACTIONS="true", PCI_TEST_BIN_DIR=fake)
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
        for hook in ("PCI_TEST_KINDS_FILE", "PCI_TEST_PHASE_CAP"):
            self.assertEqual(code.count(hook), local_block.count(hook), hook)
        self.assertEqual(code.count("PCI_TEST_NO_ISOLATION"), 1)
        self.assertIn('"${GITHUB_ACTIONS:-}" != "true"', code)

    def test_supervisor_path_is_fixed_and_job_path_dirs_are_hardened(self):
        """مجلّداتُ PATH القابلةُ للكتابة لـpcibuild تُحصَّن ثمّ تُختبر (وإلا `bash`/`sudo` مزيَّفٌ يُنفَّذ بصلاحية runner)."""
        body = readf(os.path.join(HERE, "supervise.sh"))
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        self.assertIn('export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"', code)
        self.assertIn("harden_path_dirs", code)
        self.assertIn('JOB_PATH="$PATH"', code)
        self.assertLess(code.index('JOB_PATH="$PATH"'), code.index('export PATH="/usr/local/sbin'))
        # الحمايةُ تسبق الفحص: تحصينٌ ثمّ اختبار
        self.assertLess(code.index("harden_path_dirs ||"), code.index("isolation_tests\n"))
        # وفي الوضع المحلّيّ لا يغيّر المشرفُ PATH الاختباريّ الذي يحتاج git وage (يبقيهما في المسار الثابت)
        r = self.sup("prepare")
        self.assertEqual(r.returncode, 0)

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
                     "guard.py", "kinds.json", "log_pubkey.txt"):
            p = os.path.join(ctl, name)
            self.assertTrue(os.path.isfile(p), name)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode) & 0o077, 0, name)
        # نسخةُ البناء من build.sh (يقرؤها البناءُ ولا يعدّلها في الإنتاج)
        self.assertTrue(os.path.isfile(os.path.join(self.rt, "builder", "ctl", "build.sh")))


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
