#!/usr/bin/env python3
"""🧪 اختباراتُ حارس السجلّ العلنيّ — أمثلةٌ موجبةٌ (يجب أن يُمسكها) وسالبةٌ (يجب ألا يتّهمها)،
ومحاولاتُ التفاف معروفة (ألوانُ ANSI · طوابعُ GitHub · محارفُ بلا عرض · فواصلُ بديلة في اسم الحزمة).
⛔ الأمثلةُ الموجبة تُبنى بالتركيب لا حرفيّاً حيث أمكن، كي لا يصير هذا الملفُّ نفسُه شاهداً على ما يحرسه."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guard  # noqa: E402

PKG = ".".join(["com", "ali", "rafiq"])
REPO = "Quran" + "Rafiq"

POSITIVE = {
    "kotlin": [
        f"package {PKG}.ui.tasmi",
        f"import {PKG}.recitation.ReciteFollower",
        "    private fun scoreWindow(words: List<String>): Int {",
        "val threshold = 0.45f",
        "data class Ayah(val surah: Int, val ayah: Int)",
        "@Composable",
        "e: file:///home/runner/work/x/app/Foo.kt:12:5 Unresolved reference: bar",
        "w: file:///tmp/Bar.kt:3:1 warning",
        "    at androidx.compose.runtime.Recomposer.run(Recomposer.kt:120)",
        "Caused by at Foo.kt:77",
        "Type mismatch: inferred type is String but Int was expected",
    ],
    "gradle": [
        "> Task :app:compileDebugKotlin FAILED",
        "Execution failed for task ':app:testDebugUnitTest'.",
        "* What went wrong:",
        "BUILD FAILED in 4m 12s",
        "BUILD SUCCESSFUL in 7m 3s",
        "> Configure project :engine:recitation",
        "312 tests completed, 2 failed",
    ],
    "path": [
        f"/home/runner/work/_temp/{REPO}/app/build.gradle.kts",
        "app/src/main/kotlin/x/MainActivity.kt",
        "engine/recitation/src/test/resources/fixture.json",
        "see cloud/worker/index.ts",
        "tools/store/fill_data_safety.py",
    ],
    "package": [
        PKG, PKG.replace(".", "/"), PKG.replace(".", "_"), PKG.replace(".", " . "),
    ],
    "secret": [
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU",
        "ghp_" + "a" * 36,
        "github_pat_" + "B" * 40,
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "sk-ant-" + "x" * 30,
        "AIza" + "S" * 35,
        "Authorization: Bearer " + "abcDEF123456789xyz",
        '"secretAccessKey": "zzz"',
    ],
    "signed": [
        "https://acc.r2.cloudflarestorage.com/b/k?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc",
        "https://x.blob.core.windows.net/c/f?sv=2020&sig=ABCDEFGHIJKLMNOPQRSTUV%2B",
        "https://example.com/dl?token=abcdefghijklmnop1234",
    ],
}

NEGATIVE = [
    "### 🔒 private-ci · app-test",
    "| main | 359 | 0 | 0 | 2 | 0 من 6 | ✅ |",
    "| الشوط | الاختبارات | الإخفاقات | الأعطاب | المتخطّاة | أطوارٌ فشلت | الحالة |",
    "السجلُّ الكامل مشفَّرٌ في R2 الخاصّ؛ ويُقرأ من المستودع الخاصّ بسير ci-log-fetch (رقمُ هذه التشغيلة).",
    "الحارس: الملخّص · ✅ نظيف · 7 سطراً في 1 ملفّ",
    "Run bash ctl/jobs/private_ci/build.sh build",
    "Run bash ctl/jobs/private_ci/clone.sh",
    "Run python3 jobs/private_ci/publish.py",
    "Syncing repository: mwqwf/mushafak-ci",
    "Java configuration: Distribution: temurin Version: 17.0.13+11",
    "Download action repository 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262'",
    "INFO    | Boot completed in 41023 ms",
    "export PCI_LEG=main",
    "الرفع: ✅ 2 سجلّاً مشفَّراً · محقَّقٌ بالطول وMD5",
    "الحكم: success · السبب ok · الحارس clean · الرفع verified · 359 اختباراً · إخفاق 0 · عطب 0",
    "الحالة: ✅ public-ci/device-matrix = success · HTTP 201",
    "التشفير: ✅ 41 ك.ب",
    "  PCI_LEG: a24-small-x86",
    "fun fact: nothing here",  # «fun» بلا اسمٍ وقوس
    "",
]


def scan_one(line):
    return guard.scan_text(line)


class GuardPositive(unittest.TestCase):
    def test_each_category_catches(self):
        for cat, lines in POSITIVE.items():
            for line in lines:
                with self.subTest(cat=cat, line=line[:40]):
                    self.assertGreater(scan_one(line)[cat], 0, f"لم يُمسك ({cat})")

    def test_evasion_ansi_timestamp_zero_width(self):
        tricky = [
            "\x1b[31m> Task :app:compileDebugKotlin\x1b[0m",
            "2026-11-06T00:33:12.1234567Z > Task :app:testDebugUnitTest",
            "com​.ali​.rafiq.ui",
            "ｃｏｍ.ａｌｉ.ｒａｆｉｑ",  # عرضٌ كامل ⇒ يطبّعه NFKC
        ]
        for line in tricky:
            with self.subTest(line=repr(line)[:40]):
                self.assertGreater(sum(scan_one(line).values()), 0)


class GuardNegative(unittest.TestCase):
    def test_clean_lines_pass(self):
        for line in NEGATIVE:
            with self.subTest(line=line[:40]):
                self.assertEqual(sum(scan_one(line).values()), 0, f"اتّهامٌ كاذب: {line!r}")

    def test_public_workflow_text_is_clean(self):
        """السجلُّ العلنيُّ يطبع نصوصَ خطوات private-ci.yml — فلا يجوز أن يتّهمها الحارسُ نفسُه فيحذف كلَّ سجل."""
        root = os.path.abspath(os.path.join(HERE, "..", ".."))
        for name in ("private-ci.yml", "private-ci-sweeper.yml", "private-ci-scope-guard.yml"):
            p = os.path.join(root, ".github", "workflows", name)
            if not os.path.isfile(p):
                self.skipTest("لا مجلّدَ سيور في هذه الشجرة")
            with self.subTest(name=name):
                with open(p, encoding="utf-8") as f:
                    counts = guard.scan_text(f.read())
                self.assertEqual(sum(counts.values()), 0, f"{name}: {counts}")


class GuardCli(unittest.TestCase):
    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = guard.main(list(args))
        return rc, buf.getvalue()

    def test_exit_codes_and_no_echo(self):
        with tempfile.TemporaryDirectory() as d:
            clean = os.path.join(d, "clean.md")
            dirty = os.path.join(d, "dirty.md")
            open(clean, "w", encoding="utf-8").write("\n".join(NEGATIVE))
            secret_line = f"e: file:///w/{REPO}/app/src/main/kotlin/x/Secret.kt:1:1 Unresolved reference"
            open(dirty, "w", encoding="utf-8").write("ok\n" + secret_line + "\n")
            rc, out = self.run_cli(clean)
            self.assertEqual(rc, guard.EXIT_CLEAN)
            rc, out = self.run_cli(dirty)
            self.assertEqual(rc, guard.EXIT_SUSPICIOUS)
            # ⛔ الحارسُ لا يطبع ما وجد: لا اسمَ ملفٍّ ولا مسارَ ولا كلمةً من السطر
            for frag in ("Secret.kt", REPO, "Unresolved", "app/src"):
                self.assertNotIn(frag, out)
            rc, out = self.run_cli("--report", "--json", os.path.join(d, "g.json"), dirty)
            self.assertEqual(rc, guard.EXIT_CLEAN)
            data = json.load(open(os.path.join(d, "g.json")))
            self.assertGreater(data["hits"], 0)
            rc, _ = self.run_cli(os.path.join(d, "missing.txt"))
            self.assertEqual(rc, guard.EXIT_USAGE)
            rc, _ = self.run_cli()
            self.assertEqual(rc, guard.EXIT_USAGE)

    def test_emit_plant_is_caught(self):
        """السطرُ المزروع (plant_leak) يجب أن يُمسكه الحارسُ نفسُه — وإلا فالتجربةُ الذاتيّة تبرّئ زوراً."""
        rc, out = self.run_cli("--emit-plant")
        self.assertEqual(rc, guard.EXIT_CLEAN)
        c = guard.scan_text(out)
        self.assertGreater(c["kotlin"], 0)
        self.assertGreater(c["path"], 0)

    def test_directory_scan(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "run", "1"))
            open(os.path.join(d, "run", "1", "3_build.txt"), "w").write("> Task :app:assembleDebug\n")
            rc, _ = self.run_cli(d)
            self.assertEqual(rc, guard.EXIT_SUSPICIOUS)

    def test_binary_and_huge_files_are_not_silently_cleared(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bin.dat")
            open(p, "wb").write(b"\xff\xfe\x00" + PKG.encode() + b"\x00")
            rc, _ = self.run_cli(p)
            self.assertEqual(rc, guard.EXIT_SUSPICIOUS)


if __name__ == "__main__":
    unittest.main()
