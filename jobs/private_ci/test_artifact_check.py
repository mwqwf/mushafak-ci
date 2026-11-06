#!/usr/bin/env python3
"""🧪 artifact_check (D13): مجلّدُ الناتج بالضبط ملفّان عاديّان · counts صالحٌ بالمخطّط · ترويسةُ age كاملةً بحمولة.
يُختبر بملفٍّ حقيقيٍّ من `age` (إن وُجد) وببنيةٍ مولَّدةٍ، وبطفراتٍ: سطرٌ زائد · ترويسةٌ بلا حمولة · ملفٌّ ثالث · رابطٌ رمزيّ ·
حجمٌ زائد · مقطعٌ بغير X25519 · MAC بطولٍ خاطئ · نصٌّ صريحٌ مكان المشفَّر."""
import base64
import contextlib
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artifact_check as ac  # noqa: E402
import counts_schema as cs  # noqa: E402

SHA = "c" * 40
KINDS = cs.load_kinds()


def b43(seed=0):
    return base64.b64encode(bytes((seed + i) % 256 for i in range(32))).decode().rstrip("=")


def fake_age(stanzas=1, payload=64):
    """بنيةُ age صحيحةٌ شكلاً (لا تُفكّ) — مطابقةٌ لخرج `age -r age1…` الحقيقيّ."""
    out = b"age-encryption.org/v1\n"
    for i in range(stanzas):
        out += b"-> X25519 " + b43(i).encode() + b"\n" + b43(50 + i).encode() + b"\n"
    return out + b"--- " + b43(99).encode() + b"\n" + bytes(range(payload))


def counts_bytes(kind="dry-run", leg="main"):
    ran = ["isolation", "clone"] + [p["id"] for p in KINDS[kind]["phases"] if not p.get("optional")]
    c = cs.make_counts(kind, leg, SHA, {p: 0 for p in ran}, (0, 0, 0, 0, 0), None, ran, KINDS)
    return json.dumps(c, separators=(",", ":")).encode()


def make_dir(root, files, name="pci-main"):
    d = os.path.join(root, name)
    os.makedirs(d)
    for n, data in files.items():
        with open(os.path.join(d, n), "wb") as f:
            f.write(data)
    return d


def real_age(plain=b"hello", extra_recipient=False):
    ident = tempfile.mkdtemp()
    try:
        key = os.path.join(ident, "k")
        subprocess.run(["age-keygen", "-o", key], check=True, capture_output=True)
        pub = subprocess.run(["age-keygen", "-y", key], check=True, capture_output=True, text=True).stdout.strip()
        args = ["age", "-r", pub] + (["-r", pub] if extra_recipient else [])
        return subprocess.run(args, input=gzip.compress(plain), check=True, capture_output=True).stdout
    finally:
        shutil.rmtree(ident, ignore_errors=True)


class HeaderTest(unittest.TestCase):
    def test_generated_header_is_accepted(self):
        self.assertTrue(ac.age_header_ok(fake_age()))
        self.assertTrue(ac.age_header_ok(fake_age(stanzas=3)))
        self.assertTrue(ac.age_header_ok(fake_age(payload=ac.MIN_PAYLOAD)))

    @unittest.skipUnless(shutil.which("age") and shutil.which("age-keygen"), "لا age محلّيّاً")
    def test_real_age_output_is_accepted(self):
        self.assertTrue(ac.age_header_ok(real_age()))
        self.assertTrue(ac.age_header_ok(real_age(b"x" * 100000, extra_recipient=True)))

    def test_mutations_are_rejected(self):
        good = fake_age()
        cases = {
            "empty": b"",
            "magic-only": b"age-encryption.org/v1\n",
            "wrong-magic": good.replace(b"age-encryption.org/v1", b"age-encryption.org/v2", 1),
            "no-trailing-newline-after-magic": good.replace(b"v1\n", b"v1", 1),
            "header-no-payload": good[: good.index(b"\n--- ") + 1 + 4 + 43 + 1],
            "short-payload": fake_age(payload=ac.MIN_PAYLOAD - 1),
            "no-stanza": b"age-encryption.org/v1\n--- " + b43().encode() + b"\n" + bytes(64),
            "other-recipient": good.replace(b"-> X25519 ", b"-> scrypt ", 1),
            "arg-too-short": good.replace(b"-> X25519 " + b43(0).encode(), b"-> X25519 " + b43(0).encode()[:-1], 1),
            "arg-bad-char": good.replace(b"-> X25519 " + b43(0).encode()[:1], b"-> X25519 !", 1),
            "body-missing": good.replace(b"\n" + b43(50).encode() + b"\n--- ", b"\n--- ", 1),
            "body-too-long": good.replace(b43(50).encode() + b"\n", b43(50).encode() + b"AAAA\n", 1),
            "mac-short": good.replace(b"--- " + b43(99).encode(), b"--- " + b43(99).encode()[:-1], 1),
            "extra-header-line": good.replace(b"--- ", b"junk line\n--- ", 1),
            "text-before-magic": b"x" + good,
            "plaintext-log": b"> Task :app:compileDebugKotlin\n" * 10,
            "gzip-not-encrypted": gzip.compress(b"hello " * 50),
            "too-many-stanzas": fake_age(stanzas=ac.MAX_STANZAS + 1),
            "mac-without-newline-and-no-payload": b"age-encryption.org/v1\n-> X25519 " + b43().encode() + b"\n" + b43(2).encode() + b"\n--- " + b43().encode(),
        }
        for label, data in cases.items():
            with self.subTest(label=label):
                self.assertFalse(ac.age_header_ok(data))


class CheckTest(unittest.TestCase):
    def check(self, d, **kw):
        a = dict(kind="dry-run", leg="main", sha=SHA)
        a.update(kw)
        return ac.check(d, a["kind"], a["leg"], a["sha"], KINDS)

    def good(self, root, **over):
        files = {"counts.json": counts_bytes(), "log.txt.gz.age": fake_age()}
        files.update(over)
        return make_dir(root, files)

    def test_good(self):
        with tempfile.TemporaryDirectory() as r:
            self.assertEqual(self.check(self.good(r)), (True, "ok"))

    @unittest.skipUnless(shutil.which("age") and shutil.which("age-keygen"), "لا age محلّيّاً")
    def test_good_with_real_age_file(self):
        with tempfile.TemporaryDirectory() as r:
            d = self.good(r, **{"log.txt.gz.age": real_age(b"log " * 5000)})
            self.assertEqual(self.check(d), (True, "ok"))

    def test_rejections(self):
        with tempfile.TemporaryDirectory() as r:
            d = self.good(r)
            # ملفٌّ ثالث
            with open(os.path.join(d, "extra.txt"), "w") as f:
                f.write("x")
            self.assertEqual(self.check(d), (False, "files"))
            os.remove(os.path.join(d, "extra.txt"))
            # مجلّدٌ فرعيّ
            os.mkdir(os.path.join(d, "sub"))
            self.assertEqual(self.check(d), (False, "files"))
            os.rmdir(os.path.join(d, "sub"))
            # ملفٌّ مخفيّ
            with open(os.path.join(d, ".hidden"), "w") as f:
                f.write("x")
            self.assertEqual(self.check(d), (False, "files"))
        for label, files, want in (
            ("missing-log", {"counts.json": counts_bytes()}, "files"),
            ("missing-counts", {"log.txt.gz.age": fake_age()}, "files"),
            ("plaintext-log", {"counts.json": counts_bytes(), "log.txt.gz.age": b"> Task :x\n" * 20}, "age-header"),
            ("bad-counts", {"counts.json": b'{"tests":1}', "log.txt.gz.age": fake_age()}, "schema:keys"),
            ("old-schema", {"counts.json": counts_bytes().replace(b'"schema":2', b'"schema":1'), "log.txt.gz.age": fake_age()},
             "schema:version"),
            ("counts-oversize", {"counts.json": b" " * (cs.MAX_BYTES + 1), "log.txt.gz.age": fake_age()}, "counts-size"),
            ("header-only", {"counts.json": counts_bytes(), "log.txt.gz.age": fake_age(payload=0)}, "age-header"),
            ("extra-line-after-header", {"counts.json": counts_bytes(),
                                         "log.txt.gz.age": fake_age().replace(b"--- ", b"x\n--- ", 1)}, "age-header"),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as r:
                d = make_dir(r, files)
                ok, why = self.check(d)
                self.assertFalse(ok)
                self.assertEqual(why, want)

    def test_wrong_expectations(self):
        with tempfile.TemporaryDirectory() as r:
            d = self.good(r)
            self.assertEqual(self.check(d, sha="d" * 40), (False, "schema:sha"))
            self.assertEqual(self.check(d, leg="other"), (False, "schema:leg"))
            self.assertEqual(self.check(d, kind="app-test"), (False, "schema:kind"))

    def test_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as r, tempfile.TemporaryDirectory() as other:
            target = os.path.join(other, "real.age")
            with open(target, "wb") as f:
                f.write(fake_age())
            d = make_dir(r, {"counts.json": counts_bytes()})
            os.symlink(target, os.path.join(d, "log.txt.gz.age"))
            self.assertEqual(self.check(d), (False, "files"))
            os.remove(os.path.join(d, "log.txt.gz.age"))
            with open(os.path.join(d, "log.txt.gz.age"), "wb") as f:
                f.write(fake_age())
            os.remove(os.path.join(d, "counts.json"))
            tc = os.path.join(other, "c.json")
            with open(tc, "wb") as f:
                f.write(counts_bytes())
            os.symlink(tc, os.path.join(d, "counts.json"))
            self.assertEqual(self.check(d), (False, "files"))
        with tempfile.TemporaryDirectory() as r, tempfile.TemporaryDirectory() as other:
            real = make_dir(other, {"counts.json": counts_bytes(), "log.txt.gz.age": fake_age()})
            link = os.path.join(r, "pci-main")
            os.symlink(real, link)
            self.assertEqual(self.check(link), (False, "dir"))
        self.assertEqual(self.check("/nonexistent-pci-dir"), (False, "dir"))

    def test_log_size_limit(self):
        with tempfile.TemporaryDirectory() as r:
            d = self.good(r)
            path = os.path.join(d, "log.txt.gz.age")
            with open(path, "ab") as f:
                f.truncate(ac.MAX_LOG + 1)       # ملفٌّ متناثرٌ بلا قرصٍ فعليّ
            self.assertEqual(self.check(d), (False, "log-size"))
            with open(path, "wb") as f:
                f.write(fake_age())
                f.truncate(ac.MAX_LOG)
            self.assertEqual(self.check(d), (True, "ok"))

    def test_reason_codes_are_fixed_strings(self):
        with tempfile.TemporaryDirectory() as r:
            d = make_dir(r, {"counts.json": counts_bytes().replace(b"dry-run", b"SECRET/app/src"), "log.txt.gz.age": fake_age()})
            ok, why = self.check(d)
            self.assertFalse(ok)
            self.assertNotIn("SECRET", why)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as r:
            d = self.good(r)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(ac.main([d, "--kind", "dry-run", "--leg", "main", "--sha", SHA]), 0)
                self.assertEqual(ac.main([d, "--kind", "dry-run", "--leg", "main", "--sha", "e" * 40]), 1)
            self.assertIn("✅", buf.getvalue())
            self.assertIn("⛔", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
