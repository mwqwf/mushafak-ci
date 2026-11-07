#!/usr/bin/env python3
"""🧪 حارسُ الاستثناءات بالتصميم (forbidden_excludes.py) وسكربتُ تهيئة Gradle المرافق له:
قائمةُ الإنتاج صالحةٌ وتطابق FORBIDDEN_RX حرفاً مع clone.sh · موجبٌ (ثلاثُ دوالَّ تقرأ مساراً محرَّماً) · سالبٌ لدالّةٍ مدرجةٍ لا يذكر ملفُّها
مساراً محرَّماً (ولو ذكره تعليقٌ فقط) · سالبٌ لصنفٍ أو دالّةٍ غيرِ موجودة · رفضُ نجمةٍ ومسارٍ غيرِ محرَّم وتكرارٍ وحقولٍ زائدة ·
ملفٌّ رمزيٌّ أو صنفٌ في ملفّين أو في غير src/test · لا يبقى ملفُّ أنماطٍ بعد رفض · ومطابقةُ المشرف (check) وختمُه (sealed) ·
وسكربتُ Gradle يطبّق الأنماطَ فعلاً ويرفض ملفاً مفقوداً ونجمةً وأيَّ فرقٍ عن القائمة الموثوقة ومالكاً آخر وكتابةً لغيره ورابطاً (Codex 6 §3)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import forbidden_excludes as fe  # noqa: E402

SCRIPT = os.path.join(HERE, "forbidden_excludes.py")
INIT = os.path.join(HERE, "forbidden_excludes.init.gradle")
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))   # جذرُ المستودع الخاصّ (في مرآة العدّاء لا يوجد app/)

# الاختباراتُ الثلاثة كما هي في الخاصّ: (الصنف الكامل، الدالّة، نصُّ الملفّ كما يُذكر فيه المسارُ المحرَّم بين علامتَي تنصيص)
SOURCES = {
    "com.ali.rafiq.monetization.AdUnitIdsTest": ("app-ads txt يحمل ناشر التطبيق", 'File(module.parentFile, "store/site/app-ads.txt")'),
    "com.ali.rafiq.ui.AyahLinkTest": ("بصمات assetlinks موجودة وملف الموقع يقدّم الصفحة الاحتياطية",
                                      'sequenceOf("store/site", "../store/site").map { File(it) }'),
    "com.ali.rafiq.ui.tasmi.WhisperHallucinationLexiconTest": ("القائمتان في المحرّك والخادم حرفاً واحدة",
                                                               'File(root(), "cloud/worker/src/audio.ts").readText()'),
}


def kt(fqcn, method, body):
    pkg, cls = fqcn.rsplit(".", 1)
    return f"package {pkg}\n\nclass {cls} {{\n    @Test\n    fun `{method}`() {{\n        {body}\n    }}\n}}\n"


def put(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def class_rel(fqcn, base="app/src/test/kotlin"):
    pkg, cls = fqcn.rsplit(".", 1)
    return os.path.join(base, *pkg.split("."), cls + ".kt")


def entry(fqcn, method, path, kind="app-test", reason="سبب"):
    return {"kind": kind, "class": fqcn, "method": method, "forbidden_path": path, "reason": reason}


def write_list(d, entries):
    p = os.path.join(d, "list.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"excludes": entries}, f, ensure_ascii=False)
    return p


def cli(*args):
    return subprocess.run([sys.executable, "-I", "-S", SCRIPT, *args], capture_output=True, text=True, timeout=60)


class ProductionListTest(unittest.TestCase):
    def test_list_is_valid_and_has_the_three_measured_methods(self):
        es = fe.load_list()
        self.assertEqual([(e["kind"], e["class"].rsplit(".", 1)[1]) for e in es],
                         [("app-test", "AdUnitIdsTest"), ("app-test", "AyahLinkTest"),
                          ("app-test", "WhisperHallucinationLexiconTest")])
        self.assertEqual(len(fe.entries_for("app-test")), 3)
        self.assertEqual(fe.entries_for("engine-test"), [])
        for e in es:
            self.assertTrue(fe.FORBIDDEN_RX.search(e["forbidden_path"]), e)
            self.assertNotIn("*", e["class"] + e["method"])

    def test_forbidden_rx_is_literally_the_one_in_clone_sh(self):
        """⛔ تشديدُ FORBIDDEN_RX في clone.sh يجب أن يُنسخ إلى الحارس — وإلا افترق الحارسُ عمّا يُمنع استنساخُه فعلاً."""
        with open(os.path.join(HERE, "clone.sh"), encoding="utf-8") as f:
            m = re.search(r"(?m)^FORBIDDEN_RX='(.*)'$", f.read())
        self.assertIsNotNone(m)
        self.assertEqual(fe.FORBIDDEN_RX.pattern, m.group(1))

    @unittest.skipUnless(os.path.isdir(os.path.join(ROOT, "app", "src", "test", "kotlin")),
                         "الشجرةُ الكاملة للمستودع الخاصّ غيرُ موجودة (مرآةُ العدّاء)")
    def test_production_list_verifies_against_the_real_private_tree(self):
        """حارسُ الانجراف في الخاصّ: لو غُيّر اسمُ اختبارٍ مدرجٍ أو حُذف ذكرُ المسار المحرَّم فيه يفشل هذا فوراً."""
        pats, lines = fe.verify(ROOT, "app-test", fe.load_list(), absent=False)   # في الخاصّ المحرَّمُ موجودٌ أصلاً؛ فحصُ الغياب له اختبارُه
        self.assertEqual(len(pats), 3)
        self.assertEqual(len(lines), 3)


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.src = os.path.join(self.d, "src")
        os.makedirs(self.src)
        for fqcn, (method, body) in SOURCES.items():
            put(self.src, class_rel(fqcn), kt(fqcn, method, body))
        self.entries = fe.load_list()

    def test_positive_three_methods_pass_and_are_reported(self):
        pats, lines = fe.verify(self.src, "app-test", self.entries)
        self.assertEqual(pats, [f"{e['class']}.{e['method']}" for e in self.entries])
        self.assertEqual(len(lines), 3)
        for e, ln in zip(self.entries, lines):
            self.assertIn(e["class"], ln)
            self.assertIn(e["method"], ln)
            self.assertIn(e["forbidden_path"], ln)
            self.assertIn(e["reason"], ln)

    def test_cli_positive_writes_patterns_and_prints_the_excluded(self):
        out = os.path.join(self.d, "out.txt")
        r = cli("verify", "--src", self.src, "--kind", "app-test", "--out", out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("3 دالّةً مستثناةً بالتصميم", r.stdout)
        self.assertEqual(r.stdout.count("⛔ مستثنى بالتصميم:"), 3)
        with open(out, encoding="utf-8") as f:
            body = [ln for ln in f.read().splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(body, [f"{e['class']}.{e['method']}" for e in self.entries])
        self.assertEqual(cli("count", "--kind", "app-test").stdout.strip(), "3")
        self.assertEqual(cli("count", "--kind", "engine-test").stdout.strip(), "0")

    def test_kind_without_entries_writes_an_empty_pattern_file(self):
        out = os.path.join(self.d, "out.txt")
        r = cli("verify", "--src", self.src, "--kind", "engine-test", "--out", out)
        self.assertEqual(r.returncode, 0)
        self.assertIn("0 دالّةً", r.stdout)
        with open(out, encoding="utf-8") as f:
            self.assertEqual([ln for ln in f.read().splitlines() if ln and not ln.startswith("#")], [])

    # ───── سالبٌ: الدالّةُ المدرجةُ لا يذكر ملفُّها مساراً محرَّماً ─────
    def test_negative_listed_method_whose_file_has_no_forbidden_path(self):
        fq = "com.ali.rafiq.ui.tasmi.WhisperHallucinationLexiconTest"
        put(self.src, class_rel(fq), kt(fq, SOURCES[fq][0], 'File(root(), "core/quran/src/main/assets/quran/text_hafs.jz")'))
        with self.assertRaises(fe.Refusal) as cm:
            fe.verify(self.src, "app-test", self.entries)
        self.assertIn("WhisperHallucinationLexiconTest", str(cm.exception))
        self.assertIn("لا يذكر المسارَ المحرَّم", str(cm.exception))

    def test_negative_forbidden_path_only_in_a_comment_is_not_enough(self):
        fq = "com.ali.rafiq.monetization.AdUnitIdsTest"
        put(self.src, class_rel(fq), kt(fq, SOURCES[fq][0], "// يقرأ store/site/app-ads.txt\n        val x = 1"))
        with self.assertRaises(fe.Refusal) as cm:
            fe.verify(self.src, "app-test", self.entries)
        self.assertIn("بين علامتَي تنصيص", str(cm.exception))

    def test_negative_declared_path_differs_from_what_the_file_reads(self):
        fq = "com.ali.rafiq.monetization.AdUnitIdsTest"
        es = [entry(fq, SOURCES[fq][0], "cloud/worker/src/audio.ts")]
        with self.assertRaises(fe.Refusal):
            fe.verify(self.src, "app-test", es)

    def test_cli_negative_exits_1_with_reason_and_leaves_no_pattern_file(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        put(self.src, class_rel(fq), kt(fq, SOURCES[fq][0], 'File("app/src/main")'))
        out = os.path.join(self.d, "out.txt")
        with open(out, "w") as f:
            f.write("com.stale.Old.method\n")                      # ملفٌّ قديمٌ يجب ألّا يبقى بعد رفض
        r = cli("verify", "--src", self.src, "--kind", "app-test", "--out", out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("رُفض الاستثناء", r.stdout)
        self.assertIn("AyahLinkTest", r.stdout)
        self.assertFalse(os.path.exists(out))

    # ───── تشديدٌ بأمر المراجعة: نطاقُ الدالّة لا الملفّ · وغيابُ المحرَّم فعلاً ─────
    def two_method_class(self, fq, listed_body, neighbour_body, listed_first=True, class_level=""):
        pkg, cls = fq.rsplit(".", 1)
        m = SOURCES[fq][0]
        a = f"    @Test\n    fun `{m}`() {{\n        {listed_body}\n    }}\n"
        b = f"    @Test\n    fun `دالّة مجاورة`() {{\n        {neighbour_body}\n    }}\n"
        put(self.src, class_rel(fq), f"package {pkg}\n\nclass {cls} {{\n{class_level}{a + b if listed_first else b + a}}}\n")

    def test_negative_neighbour_method_mentions_forbidden_but_listed_does_not(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        for first in (True, False):
            with self.subTest(listed_first=first):
                self.two_method_class(fq, "assertTrue(true)", SOURCES[fq][1], listed_first=first)
                with self.assertRaises(fe.Refusal) as cm:
                    fe.verify(self.src, "app-test", self.entries)
                self.assertIn("جسمُ الدالّة", str(cm.exception))

    def test_negative_forbidden_only_in_class_level_val(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        self.two_method_class(fq, "assertTrue(true)", "assertTrue(true)", class_level='    private val site = File("store/site")\n')
        with self.assertRaises(fe.Refusal):
            fe.verify(self.src, "app-test", self.entries)

    def test_positive_forbidden_in_listed_body_with_neighbour_methods_around(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        self.two_method_class(fq, SOURCES[fq][1], "assertTrue(true)", listed_first=False)
        pats, _ = fe.verify(self.src, "app-test", self.entries)
        self.assertEqual(len(pats), 3)

    def test_negative_forbidden_only_in_a_comment_inside_the_listed_body(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        for name, body in (("line", 'val x = 1 // File("store/site")'),
                           ("line-own", '// File("store/site")\n        val x = 1'),
                           ("block", '/* File("store/site") */ val x = 1'),
                           ("block-multi", '/*\n         * "store/site"\n         */\n        val x = 1'),
                           ("block-nested", '/* a /* "store/site" */ "store/site" */ val x = 1')):
            with self.subTest(name):
                put(self.src, class_rel(fq), kt(fq, SOURCES[fq][0], body))
                with self.assertRaises(fe.Refusal):
                    fe.verify(self.src, "app-test", self.entries)

    def test_positive_string_with_slashes_and_comment_markers_survives_stripping(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        put(self.src, class_rel(fq), kt(fq, SOURCES[fq][0], 'val a = "x" // تعليق\n        val s = File("store/site") /* c */'))
        self.assertEqual(len(fe.verify(self.src, "app-test", self.entries)[0]), 3)
        self.assertEqual(fe.strip_comments('a "u//v" /* "z" */ \'"\' b // "t"'), 'a "u//v"   \'"\' b ')

    def test_negative_declared_forbidden_path_exists_in_clone(self):
        for rel, kind in (("store/site/app-ads.txt", "file"), ("store/site", "dir"), ("cloud/worker/src/audio.ts", "file")):
            with self.subTest(rel=rel):
                p = os.path.join(self.src, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w").close()
                es = [e for e in self.entries if e["forbidden_path"] == rel]
                self.assertEqual(len(es), 1)
                with self.assertRaises(fe.Refusal) as cm:
                    fe.verify(self.src, "app-test", es)
                self.assertIn("موجودٌ في الاستنساخ", str(cm.exception))
                shutil.rmtree(os.path.join(self.src, rel.split("/")[0]))
        # ورابطٌ رمزيّ معلَّقٌ بالاسم نفسه يُعدّ موجوداً (lexists)
        os.makedirs(os.path.join(self.src, "store"))
        os.symlink("/nonexistent", os.path.join(self.src, "store", "site"))
        with self.assertRaises(fe.Refusal):
            fe.verify(self.src, "app-test", [e for e in self.entries if e["forbidden_path"] == "store/site"])

    # ───── سالبٌ: صنفٌ أو دالّةٌ غيرُ موجودة ─────
    def test_negative_class_not_in_clone(self):
        shutil.rmtree(os.path.join(self.src, "app"))
        with self.assertRaises(fe.Refusal) as cm:
            fe.verify(self.src, "app-test", self.entries)
        self.assertIn("صنفٌ غيرُ موجود", str(cm.exception))

    def test_negative_method_not_in_class_file(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        put(self.src, class_rel(fq), kt(fq, "اسمٌ آخر", 'File("store/site")'))
        with self.assertRaises(fe.Refusal) as cm:
            fe.verify(self.src, "app-test", self.entries)
        self.assertIn("دالّةٌ غيرُ موجودة", str(cm.exception))

    def test_negative_class_only_outside_src_test_does_not_count(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        os.remove(os.path.join(self.src, class_rel(fq)))
        put(self.src, class_rel(fq, "app/src/main/kotlin"), kt(fq, SOURCES[fq][0], 'File("store/site")'))
        with self.assertRaises(fe.Refusal):
            fe.verify(self.src, "app-test", self.entries)

    def test_negative_class_in_two_files_is_ambiguous(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        put(self.src, class_rel(fq, "core/quran/src/test/kotlin"), kt(fq, SOURCES[fq][0], SOURCES[fq][1]))
        with self.assertRaises(fe.Refusal) as cm:
            fe.verify(self.src, "app-test", self.entries)
        self.assertIn("أكثرَ من ملفّ", str(cm.exception))

    def test_negative_symlinked_test_file_is_refused(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        real = os.path.join(self.d, "real.kt")
        shutil.move(os.path.join(self.src, class_rel(fq)), real)
        os.symlink(real, os.path.join(self.src, class_rel(fq)))
        with self.assertRaises(fe.Refusal):
            fe.verify(self.src, "app-test", self.entries)

    def test_negative_wrong_package_declaration(self):
        fq = "com.ali.rafiq.ui.AyahLinkTest"
        put(self.src, class_rel(fq), kt(fq, SOURCES[fq][0], SOURCES[fq][1]).replace("package com.ali.rafiq.ui", "package com.other"))
        with self.assertRaises(fe.Refusal):
            fe.verify(self.src, "app-test", self.entries)


class ListValidationTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.ok = entry("com.a.B", "m", "store/site")

    def load(self, entries):
        return fe.load_list(write_list(self.d, entries))

    def test_good_entry_loads(self):
        self.assertEqual(len(self.load([self.ok])), 1)

    def test_path_that_is_not_forbidden_is_refused(self):
        for p in ("app/src/main", "tools/ai/x.py", "docs/ai/app_guide/a.md", "ops/ui_shots/request.txt", "core/quran"):
            with self.subTest(p=p), self.assertRaises(fe.Refusal):
                self.load([dict(self.ok, forbidden_path=p)])

    def test_wildcards_and_incomplete_names_are_refused(self):
        for bad in (dict(self.ok, **{"class": "com.a.*"}), dict(self.ok, **{"class": "NoPackage"}),
                    dict(self.ok, method="m*"), dict(self.ok, method="a.b"), dict(self.ok, method="a#b"),
                    dict(self.ok, method=" m"), dict(self.ok, method="a`b"), dict(self.ok, kind="App Test")):
            with self.subTest(bad=bad), self.assertRaises(fe.Refusal):
                self.load([bad])

    def test_extra_or_missing_fields_and_duplicates_are_refused(self):
        with self.assertRaises(fe.Refusal):
            self.load([dict(self.ok, extra="x")])
        with self.assertRaises(fe.Refusal):
            self.load([{k: v for k, v in self.ok.items() if k != "reason"}])
        with self.assertRaises(fe.Refusal):
            self.load([self.ok, dict(self.ok)])
        with self.assertRaises(fe.Refusal):
            self.load([dict(self.ok, reason="")])

    def test_same_method_for_two_kinds_is_not_a_duplicate(self):
        self.assertEqual(len(self.load([self.ok, dict(self.ok, kind="engine-test")])), 2)

    def test_cli_with_a_broken_list_exits_1(self):
        p = os.path.join(self.d, "bad.json")
        with open(p, "w") as f:
            f.write("{")
        r = cli("verify", "--src", self.d, "--kind", "app-test", "--out", os.path.join(self.d, "o.txt"), "--list", p)
        self.assertEqual(r.returncode, 1)


class CheckAndSealTest(unittest.TestCase):
    """Codex 6 §3: المشرفُ يطابق ملفَّ الأنماط بالقائمة الموثوقة بايتاً ببايت (check) ويختم المثبَّتَ بعد زوال البناء (sealed)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.want = fe.canonical(fe.expected_patterns("app-test"))
        self.f = put(self.d, "ex.txt", self.want)

    def test_canonical_is_header_then_the_listed_patterns_in_order(self):
        self.assertEqual(self.want.splitlines()[1:], [fe.pattern_of(e) for e in fe.entries_for("app-test")])
        self.assertTrue(self.want.startswith("#"))
        self.assertEqual(fe.check_patterns(self.f, "app-test"), 3)
        r = cli("check", "--kind", "app-test", "--patterns", self.f)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "3"))

    def test_check_refuses_any_difference_from_the_trusted_list(self):
        pats = fe.expected_patterns("app-test")
        cases = {
            "extra": fe.canonical(pats + ["com.evil.X.y"]),
            "missing": fe.canonical(pats[:-1]),
            "reordered": fe.canonical(list(reversed(pats))),
            "wildcard": fe.canonical(pats[:-1] + ["com.ali.rafiq.*"]),
            "other-kind": fe.canonical(fe.expected_patterns("engine-test")),
            "no-header": "".join(x + "\n" for x in pats),
            "trailing-space": fe.canonical(pats).replace("\n", " \n", 2),
            "crlf": fe.canonical(pats).replace("\n", "\r\n"),
            "extra-comment": fe.canonical(pats) + "# سطرٌ زائد\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                put(self.d, "bad.txt", text)
                with self.assertRaises(fe.Refusal):
                    fe.check_patterns(os.path.join(self.d, "bad.txt"), "app-test")
                r = cli("check", "--kind", "app-test", "--patterns", os.path.join(self.d, "bad.txt"))
                self.assertEqual(r.returncode, 1)
                self.assertIn("forbidden_excludes: ⛔", r.stdout)

    def test_check_refuses_symlink_hardlink_and_missing(self):
        link = os.path.join(self.d, "link.txt"); os.symlink(self.f, link)
        hard = os.path.join(self.d, "hard.txt"); os.link(self.f, hard)
        for name, path in (("symlink", link), ("hardlink", hard), ("missing", os.path.join(self.d, "nope.txt"))):
            with self.subTest(name):
                with self.assertRaises(fe.Refusal):
                    fe.check_patterns(path, "app-test")

    def test_sealed_accepts_an_identical_copy_and_refuses_a_change(self):
        ref = put(self.d, "ref.txt", self.want)
        fe.sealed(self.f, ref)
        self.assertEqual(cli("sealed", "--file", self.f, "--ref", ref).returncode, 0)
        with open(self.f, "a", encoding="utf-8") as f:
            f.write("com.evil.X.y\n")
        with self.assertRaises(fe.Refusal):
            fe.sealed(self.f, ref)
        r = cli("sealed", "--file", self.f, "--ref", ref)
        self.assertEqual(r.returncode, 1)
        self.assertIn("forbidden_excludes: ⛔", r.stdout)
        os.remove(self.f); os.symlink(ref, self.f)                       # رابطٌ إلى نسخةٍ مطابقة: مرفوض
        with self.assertRaises(fe.Refusal):
            fe.sealed(self.f, ref)

    def test_sealed_with_chain_uid_requires_owner_and_no_group_or_other_write(self):
        ref = put(self.d, "ref.txt", self.want)
        sub = os.path.join(self.d, "ctl"); os.makedirs(sub); os.chmod(sub, 0o755)
        f = put(self.d, "ctl/ex.txt", self.want); os.chmod(f, 0o644)
        me = os.getuid()
        # آباءُ المجلّد المؤقّت قد لا تكون للمستخدم نفسِه (‏/tmp لـroot بـ1777) ⇒ تُفحص السلسلةُ بدالّتها مباشرةً على مكوّنٍ نملكه
        self.assertFalse(fe.chain_ok("relative/ex.txt", me))
        self.assertFalse(fe.chain_ok(os.path.join(sub, "..", "ctl", "ex.txt"), me))
        if not fe.chain_ok(f, me):
            # السلسلةُ من الجذر لا تكون كلُّها لنا في بيئة الاختبار: يكفي أن نُثبت أنّ الختمَ يرفض حين تُطلب السلسلة
            with self.assertRaises(fe.Refusal):
                fe.sealed(f, ref, chain_uid=me)
        else:
            fe.sealed(f, ref, chain_uid=me)
        os.chmod(sub, 0o775)
        self.assertFalse(fe.chain_ok(f, me)); os.chmod(sub, 0o755)
        os.chmod(f, 0o646)
        self.assertFalse(fe.chain_ok(f, me)); os.chmod(f, 0o644)
        self.assertFalse(fe.chain_ok(f, me + 1))                         # مالكٌ آخر
        link = os.path.join(self.d, "lnk"); os.symlink(sub, link)
        self.assertFalse(fe.chain_ok(os.path.join(link, "ex.txt"), me))  # رابطٌ في الآباء

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "يحتاج root لسلسلةٍ من الجذر")
    def test_chain_ok_from_root_for_a_root_owned_tree(self):
        parent = next((p for p in ("/var/lib", "/root", "/opt") if os.path.isdir(p) and os.stat(p).st_uid == 0
                       and not os.stat(p).st_mode & 0o022), None)
        if parent is None:
            self.skipTest("لا مجلّدَ نظاميٌّ لـroot بلا كتابة")
        base = tempfile.mkdtemp(prefix="pci-seal-test-", dir=parent)
        self.addCleanup(shutil.rmtree, base, True)
        os.chmod(base, 0o755)
        f = os.path.join(base, "ex.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(self.want)
        os.chmod(f, 0o644)
        if not fe.chain_ok(f, 0):
            self.skipTest("آباءُ المجلّد المؤقّت ليست لـroot بلا كتابة (بيئةُ الاختبار)")
        ref = put(self.d, "ref.txt", self.want)
        fe.sealed(f, ref, chain_uid=0)
        os.chown(f, 65534, -1)
        with self.assertRaises(fe.Refusal):
            fe.sealed(f, ref, chain_uid=0)


OWNER = __import__("pwd").getpwuid(os.getuid()).pw_name


class GradleInitScriptTest(unittest.TestCase):
    def test_static_shape_no_ignore_no_assume_and_filter_only(self):
        with open(INIT, encoding="utf-8") as f:
            body = f.read()
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("//"))
        self.assertIn("excludeTestsMatching", code)
        self.assertIn("System.getProperty('pci.test.excludes')", code)
        # Codex 6 §3: ربطُ ما يقرؤه Gradle بالقائمة الموثوقة ومالكِ مدخل التحكّم — والمقارنةُ حرفاً وترتيباً
        for prop in ("pci.test.excludes.list", "pci.test.excludes.kind", "pci.test.excludes.owner"):
            self.assertIn(f"System.getProperty('{prop}')", code)
        self.assertIn("LinkOption.NOFOLLOW_LINKS", code)
        self.assertIn("if (patterns != expected)", code)
        for bad in ("@Ignore", "assume", "includeTestsMatching", "enabled = false", "onlyIf"):
            self.assertNotIn(bad, code)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        os.chmod(self.d, 0o755)
        put(self.d, "proj/settings.gradle", "")
        put(self.d, "proj/build.gradle", "tasks.register('t', Test)\n"
                                         "tasks.register('show') { doLast { println 'EX=' + tasks.t.filter.excludePatterns.toList().sort() } }\n")
        self.ctl = os.path.join(self.d, "ctl")
        os.makedirs(self.ctl); os.chmod(self.ctl, 0o755)
        self.good = "# تعليق\ncom.a.B.بصمات assetlinks موجودة\ncom.a.C.app-ads txt\n"
        self.ex = put(self.d, "ctl/ex.txt", self.good); os.chmod(self.ex, 0o644)
        self.lst = os.path.join(self.ctl, "list.json")
        with open(self.lst, "w", encoding="utf-8") as f:
            json.dump({"excludes": [entry("com.a.B", "بصمات assetlinks موجودة", "store/site"),
                                    entry("com.a.C", "app-ads txt", "store/site/app-ads.txt"),
                                    entry("com.a.D", "m", "cloud/x", kind="engine-test")]}, f, ensure_ascii=False)
        os.chmod(self.lst, 0o644)

    def gradle(self, *props):
        env = dict(os.environ, GRADLE_USER_HOME=os.path.join(self.d, "guh"))
        return subprocess.run(["gradle", "--offline", "--no-daemon", "-q", "--init-script", INIT, *props,
                               "-p", os.path.join(self.d, "proj"), "show"], capture_output=True, text=True, timeout=240, env=env)

    def props(self, ex=None, lst=None, kind="app-test", owner=OWNER):
        return [f"-Dpci.test.excludes={ex or self.ex}", f"-Dpci.test.excludes.list={lst or self.lst}",
                f"-Dpci.test.excludes.kind={kind}", f"-Dpci.test.excludes.owner={owner}"]

    def refused(self, r, why):
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("EX=", r.stdout)
        self.assertIn("pci:", r.stderr)
        self.assertIn(why, r.stderr)

    @unittest.skipUnless(shutil.which("gradle"), "لا gradle في هذه البيئة")
    def test_gradle_applies_exactly_the_listed_patterns(self):
        r = self.gradle(*self.props())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("EX=[com.a.B.بصمات assetlinks موجودة, com.a.C.app-ads txt]", r.stdout)

    @unittest.skipUnless(shutil.which("gradle"), "لا gradle في هذه البيئة")
    def test_gradle_accepts_the_supervisor_canonical_file_for_the_production_list(self):
        """الربطُ بين الطرفين: ما يكتبه المشرفُ (canonical) للقائمة الحقيقيّة يقبله سكربتُ Gradle ويطبّق أنماطَها الثلاثة بأعيانها."""
        lst = os.path.join(self.ctl, "prod.json")
        shutil.copyfile(os.path.join(HERE, "forbidden_excludes.json"), lst); os.chmod(lst, 0o644)
        with open(self.ex, "w", encoding="utf-8") as f:
            f.write(fe.canonical(fe.expected_patterns("app-test")))
        r = self.gradle(*self.props(lst=lst))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("EX=" + str(sorted(fe.expected_patterns("app-test"))).replace("'", ""), r.stdout)

    @unittest.skipUnless(shutil.which("gradle"), "لا gradle في هذه البيئة")
    def test_gradle_refuses_patterns_that_differ_from_the_trusted_list(self):
        """العبثُ بعد الحارس: سطرٌ زائد · ترتيبٌ مقلوب · نوعٌ آخر · نجمة — لا يجري Gradle بأيٍّ منها."""
        cases = (("extra", self.good + "com.a.D.m\n", "app-test", "لا تطابق القائمةَ الموثوقة"),
                 ("reordered", "# تعليق\ncom.a.C.app-ads txt\ncom.a.B.بصمات assetlinks موجودة\n", "app-test", "لا تطابق القائمةَ الموثوقة"),
                 ("other-kind", self.good, "engine-test", "لا تطابق القائمةَ الموثوقة"),
                 ("wildcard", "com.a.B.*\n", "app-test", "نمطُ استثناءٍ غيرُ مقبول"))
        for name, text, kind, why in cases:
            with self.subTest(name):
                with open(self.ex, "w", encoding="utf-8") as f:
                    f.write(text)
                self.refused(self.gradle(*self.props(kind=kind)), why)

    @unittest.skipUnless(shutil.which("gradle"), "لا gradle في هذه البيئة")
    def test_gradle_refuses_wrong_owner_writable_symlink_missing_and_unbound(self):
        link = os.path.join(self.ctl, "link.txt"); os.symlink(self.ex, link)
        with self.subTest("owner"):
            self.refused(self.gradle(*self.props(owner="nobody-pci")), "لغير مالكها المعلَن")
        with self.subTest("group-writable-file"):
            os.chmod(self.ex, 0o664)
            self.refused(self.gradle(*self.props()), "قابلةٌ للكتابة لغير مالكها")
            os.chmod(self.ex, 0o644)
        with self.subTest("other-writable-dir"):
            os.chmod(self.ctl, 0o757)
            self.refused(self.gradle(*self.props()), "قابلةٌ للكتابة لغير مالكها")
            os.chmod(self.ctl, 0o755)
        with self.subTest("symlink"):
            self.refused(self.gradle(*self.props(ex=link)), "ليس ملفّاً عاديّاً")
        with self.subTest("missing"):
            self.refused(self.gradle(*self.props(ex=os.path.join(self.ctl, "nope.txt"))), "ليس ملفّاً عاديّاً")
        with self.subTest("unbound"):
            self.refused(self.gradle(f"-Dpci.test.excludes={self.ex}"), "لا يجري Gradle بلا ربطٍ بالقائمة الموثوقة")


if __name__ == "__main__":
    unittest.main()
