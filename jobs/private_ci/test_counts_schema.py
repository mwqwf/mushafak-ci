#!/usr/bin/env python3
"""🧪 مخطّطُ counts.json الصارم (الإصدار 2): ما يكتبه المشرفُ يُقبل، وأيُّ انحرافٍ بحرفٍ يُرفض (مفتاحٌ زائد · نصٌّ حرّ · نوعٌ خاطئ ·
bool مكان int · NaN · مفتاحٌ مكرَّر · شوطٌ أو SHA غيرُ المتوقَّع · ok يخالف الأرقام · معرّفُ طورٍ خارج القائمة) +
D12: الأطوارُ الإلزاميّة ran (phases-missing) · وقراءةُ JUnit بلا اتّباع روابط ولا ملفّاتٍ كبيرة ولا أكثرَ من 10,000 ملفّ."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import counts_schema as cs  # noqa: E402

SHA = "d" * 40
KINDS = cs.load_kinds()
APP_RAN = ["isolation", "clone", "app_guide", "no_blocking", "back_handling", "i18n_verify", "i18n_locale", "gradle"]


def good(**kw):
    c = {"schema": 2, "kind": "app-test", "leg": "main", "sha": SHA, "tests": 359, "failures": 0, "errors": 0,
         "skipped": 3, "reports": 74, "phases": len(APP_RAN), "failed_phases": 0, "failed_phase_ids": [],
         "ran": list(APP_RAN), "clone_ok": True, "ok": True}
    c.update(kw)
    return c


def raw(c):
    return json.dumps(c).encode()


def put(path, data):
    with open(path, "wb") as f:
        f.write(data)


def full_rcs(kind, kinds=KINDS, fail=()):
    """رموزُ خروجٍ لشوطٍ كاملٍ للنوع: ran بترتيب kinds.json (الاختياريُّ خارجٌ) ومعها isolation وclone في أوّله."""
    ran = ["isolation", "clone"] + [p["id"] for p in kinds[kind]["phases"] if not p.get("optional")]
    return ran, {p: (1 if p in fail else 0) for p in ran}


class ValidateTest(unittest.TestCase):
    def v(self, c, kind="app-test", leg="main", sha=SHA):
        return cs.validate(raw(c) if not isinstance(c, (bytes, bytearray)) else c, kind, leg, sha, KINDS)

    def test_good_passes(self):
        self.assertEqual(self.v(good())["tests"], 359)
        failing = good(failures=2, ok=False, failed_phases=1, failed_phase_ids=["gradle"])
        self.assertFalse(self.v(failing)["ok"])

    def test_rejections(self):
        cases = {
            "extra-key": dict(good(), note="x"),
            "missing-key": {k: v for k, v in good().items() if k != "skipped"},
            "no-ran-key": {k: v for k, v in good().items() if k != "ran"},
            "free-text": good(failed_phase_ids=["app/src/main/kotlin/X.kt"], failed_phases=1, ok=False),
            "unknown-phase": good(failed_phase_ids=["exfil"], failed_phases=1, ok=False),
            "dup-phase": good(failed_phase_ids=["gradle", "gradle"], failed_phases=2, ok=False),
            "phase-count": good(failed_phase_ids=["gradle"], failed_phases=0, ok=False),
            "bool-as-int": good(tests=True),
            "float": good(tests=3.0),
            "negative": good(errors=-1),
            "huge": good(tests=10 ** 9),
            "int-as-bool": good(clone_ok=1),
            "string-num": good(tests="359"),
            "ok-lies": good(failures=1),
            "ok-without-tests": good(tests=0, failures=0),
            "failures-gt-tests": good(tests=1, failures=2, ok=False),
            "wrong-kind": good(kind="engine-test"),
            "wrong-leg": good(leg="api29"),
            "wrong-sha": good(sha="e" * 40),
            "old-version": good(schema=1),
            "future-version": good(schema=3),
            "clone-lie": good(clone_ok=False),
            # D12 — حقل ran
            "ran-dup": good(ran=APP_RAN + ["gradle"], phases=len(APP_RAN) + 1),
            "ran-unknown-id": good(ran=APP_RAN[:-1] + ["exfil"]),
            "ran-free-text": good(ran=APP_RAN[:-1] + ["app/src/x"]),
            "ran-not-list": good(ran="isolation"),
            "ran-too-long": good(ran=["unit"] * 33, phases=33),
            "ran-int": good(ran=APP_RAN[:-1] + [7]),
            "phases-ne-ran": good(phases=len(APP_RAN) - 1),
            "failed-not-in-ran": good(ran=APP_RAN[:-1], phases=len(APP_RAN) - 1, failed_phase_ids=["gradle"],
                                      failed_phases=1, ok=False),
        }
        for label, c in cases.items():
            with self.subTest(label=label), self.assertRaises(cs.SchemaError):
                self.v(c)

    def test_mandatory_phases_must_have_run(self):
        """D12: شوطٌ لم تجرِ أطوارُه الإلزاميّةُ كلُّها يُرفض (phases-missing) ولو بدت أرقامُه ناجحة."""
        for missing in APP_RAN:
            ran = [p for p in APP_RAN if p != missing]
            with self.subTest(missing=missing), self.assertRaises(cs.SchemaError) as cm:
                self.v(good(ran=ran, phases=len(ran)))
            self.assertIn(str(cm.exception), ("phases-missing", "clone-mismatch"))
        with self.assertRaises(cs.SchemaError) as cm:
            self.v(good(ran=["isolation", "clone", "gradle"], phases=3))
        self.assertEqual(str(cm.exception), "phases-missing")

    def test_early_failures_stay_valid_as_errors_not_schema(self):
        """فشلُ العزل أو الاستنساخ يبقى ناتجاً صالحاً (يصير «تعذّراً» في publish لا «فشلَ مخطّط»)."""
        iso = good(ran=["isolation"], phases=1, failed_phase_ids=["isolation"], failed_phases=1, clone_ok=False,
                   ok=False, tests=0, failures=0, skipped=0, reports=0)
        self.assertFalse(self.v(iso)["clone_ok"])
        clone = good(ran=["isolation", "clone"], phases=2, failed_phase_ids=["clone"], failed_phases=1, clone_ok=False,
                     ok=False, tests=0, failures=0, skipped=0, reports=0)
        self.assertFalse(self.v(clone)["clone_ok"])
        # لكنّ الاستنساخَ لا يُزعَم فشلُه ليُخفى نقصُ الأطوار إن كان clone_ok صحيحاً
        with self.assertRaises(cs.SchemaError):
            self.v(dict(clone, clone_ok=True))
        # وغيابُ العزل من ran مرفوضٌ دائماً
        with self.assertRaises(cs.SchemaError):
            self.v(good(ran=APP_RAN[1:], phases=len(APP_RAN) - 1))
        # وعزلٌ فاشلٌ مع استنساخٍ جرى (لا يجوز: المفتاحُ لا يبلغ الاستنساخ)
        with self.assertRaises(cs.SchemaError):
            self.v(good(failed_phase_ids=["isolation"], failed_phases=1, ok=False))

    def test_optional_phase_not_mandatory(self):
        ran, _ = full_rcs("device-matrix")
        self.assertNotIn("lint", ran)
        c = cs.make_counts("device-matrix", "a26-phone", SHA, {p: 0 for p in ran}, (0, 0, 0, 0, 0), (2, 0), ran, KINDS)
        self.assertTrue(cs.validate(raw(c), "device-matrix", "a26-phone", SHA, KINDS)["ok"])

    def test_raw_level_rejections(self):
        for label, b in {
            "nan": b'{"tests": NaN}',
            "dup-key": raw(good())[:-1] + b', "tests": 1}',
            "not-object": b"[1,2]",
            "not-json": b"{",
            "empty": b"",
            "too-big": b" " * (cs.MAX_BYTES + 1),
            "bad-utf8": b"\xff\xfe",
        }.items():
            with self.subTest(label=label), self.assertRaises(cs.SchemaError):
                self.v(b)

    def test_expected_leg_must_exist_in_kind(self):
        ran, _ = full_rcs("device-matrix")
        c = good(kind="device-matrix", leg="a24-small-x86", ran=ran, phases=len(ran), tests=3)
        self.assertTrue(self.v(c, kind="device-matrix", leg="a24-small-x86"))
        with self.assertRaises(cs.SchemaError):
            self.v(good(kind="device-matrix", leg="nope"), kind="device-matrix", leg="nope")

    def test_error_code_never_echoes_input(self):
        try:
            self.v(dict(good(), **{"SECRET-KEY": "app/src/main"}))
        except cs.SchemaError as e:
            self.assertNotIn("SECRET", str(e))
            self.assertNotIn("app/src", str(e))


class KindsTest(unittest.TestCase):
    def test_every_kind_has_valid_ordered_phases(self):
        for kind, spec in KINDS.items():
            phases = spec["phases"]
            ids = [p["id"] for p in phases]
            with self.subTest(kind=kind):
                self.assertTrue(phases)
                self.assertEqual(len(ids), len(set(ids)), "طورٌ مكرّر")
                for p in phases:
                    self.assertEqual(set(p), {"id", "who", "emu", "optional"})
                    self.assertIn(p["who"], ("builder", "runner"))
                    self.assertIn(p["id"], cs.PHASES)
                    self.assertNotIn(p["id"], ("isolation", "clone", "cleanup"), "أطوارُ المشرف لا تُدرج")
                    self.assertFalse(p["id"].startswith("selftest_"), "طورٌ تجريبيٌّ في الإنتاج")
                    self.assertIs(type(p["emu"]), bool)
                    self.assertIs(type(p["optional"]), bool)
                # أطوارُ المشرف الثابتةُ ثلاثةٌ فقط (sysimg · disk · kvm) وتسبق طورَ المحاكي؛ والإصلاحُ الخامس: مسبارُ المحاكي emu_probe
                # (موثوقٌ بحساب العدّاء) والشجرةُ الكبيرة stress_tree لا يكونان إلا في نوعٍ مصدرُه عامّ (تجربةٌ يدويّة)
                runner = {p["id"] for p in phases if p["who"] == "runner"}
                if spec["source"] == "public":
                    self.assertTrue(runner <= {"sysimg", "disk", "kvm", "emu_probe"})
                else:
                    self.assertTrue(runner <= {"sysimg", "disk", "kvm"})
                    self.assertNotIn("emu_probe", ids)
                    self.assertNotIn("stress_tree", ids)
                if "emu_probe" in ids:
                    self.assertEqual(phases[-1], {"id": "emu_probe", "who": "runner", "emu": True, "optional": False})
                # أ2: نوعٌ بمحاكٍ يبدأ بـsysimg (قبل أيّ طورٍ غيرِ موثوق) وأوّلُ أطوار المحاكي emu_up بمستخدم البناء
                if any(p["emu"] for p in phases):
                    self.assertEqual(phases[0], {"id": "sysimg", "who": "runner", "emu": False, "optional": False})
                    first_emu = next(p for p in phases if p["emu"])
                    self.assertEqual(first_emu, {"id": "emu_up", "who": "builder", "emu": True, "optional": False})
                else:
                    self.assertNotIn("sysimg", ids)
                    self.assertNotIn("emu_up", ids)
                # كلُّ طورٍ يجري داخل خطوة المحاكي يتبع كلَّ ما قبله
                flags = [p["emu"] for p in phases]
                self.assertEqual(flags, sorted(flags), "طورٌ بلا محاكٍ بعد طورٍ بمحاكٍ")
                has_emu_leg = ["emu" in leg for leg in spec["legs"]]
                self.assertEqual(any(flags), all(has_emu_leg), "أطوارُ محاكٍ لشوطٍ بلا محاكٍ أو العكس")
                self.assertTrue(cs.mandatory_phases(KINDS, kind))

    def test_known_orderings_match_v2(self):
        order = {k: [p["id"] for p in v["phases"]] for k, v in KINDS.items()}
        self.assertEqual(order["app-test"], ["app_guide", "no_blocking", "back_handling", "i18n_verify", "i18n_locale", "gradle"])
        self.assertEqual(order["tasmi-model-emulator-smoke"], ["sysimg", "whisper", "fatiha", "disk", "kvm", "emu_up", "emu"])
        self.assertEqual(order["device-matrix"], ["sysimg", "whisper", "fatiha", "gradle", "lint", "disk", "kvm", "emu_up",
                                                  "emu_smoke", "emu_tasmi"])
        self.assertEqual(order["dry-run"], ["unit"])
        self.assertEqual(order["dry-run-emu"], ["sysimg", "kvm", "emu_up", "emu_probe"])
        self.assertEqual(order["dry-run-stress"], ["stress_tree"])
        self.assertEqual(order["engine-test"], ["pip", "fixture_selftest", "fixture_audit", "ffmpeg", "phoneme_gold", "jni", "gradle"])

    def test_phases_for_modes_and_lint(self):
        self.assertEqual(cs.phases_for(KINDS, "device-matrix", "a24-small-x86", "build"),
                         [("sysimg", "runner"), ("whisper", "builder"), ("fatiha", "builder"), ("gradle", "builder"),
                          ("lint", "builder"), ("disk", "runner"), ("kvm", "runner")])
        self.assertEqual(cs.phases_for(KINDS, "device-matrix", "a26-phone", "build"),
                         [("sysimg", "runner"), ("whisper", "builder"), ("fatiha", "builder"), ("gradle", "builder"),
                          ("disk", "runner"), ("kvm", "runner")])
        self.assertEqual(cs.phases_for(KINDS, "device-matrix", "a26-phone", "emu"),
                         [("emu_up", "builder"), ("emu_smoke", "builder"), ("emu_tasmi", "builder")])
        self.assertEqual(cs.phases_for(KINDS, "app-test", "main", "emu"), [])
        with self.assertRaises(cs.SchemaError):
            cs.phases_for(KINDS, "app-test", "nope", "build")


class EmitTest(unittest.TestCase):
    def test_make_counts_round_trips_for_every_kind_and_leg(self):
        for kind, spec in KINDS.items():
            ran, rcs = full_rcs(kind)
            for leg in spec["legs"]:
                with self.subTest(kind=kind, leg=leg["id"]):
                    r = list(ran) + (["lint"] if leg.get("lint") else [])
                    c = cs.make_counts(kind, leg["id"], SHA, dict(rcs, lint=1), (10, 0, 0, 1, 2),
                                       (2, 0) if kind == "device-matrix" else None, r, KINDS)
                    self.assertEqual(cs.validate(raw(c), kind, leg["id"], SHA, KINDS), c)
                    self.assertEqual(c["phases"], len(r))

    def test_outcomes(self):
        ran, rcs = full_rcs("engine-test", fail=("jni",))
        c = cs.make_counts("engine-test", "main", SHA, rcs, (5, 0, 0, 0, 1), None, ran, KINDS)
        self.assertFalse(c["ok"])
        self.assertEqual(c["failed_phase_ids"], ["jni"])
        ran, rcs = full_rcs("device-matrix")
        lint_only = cs.make_counts("device-matrix", "a24-small-x86", SHA, dict(rcs, lint=1), (0, 0, 0, 0, 0), (2, 0),
                                   ran + ["lint"], KINDS)
        self.assertTrue(lint_only["ok"], "lint لا يُسقط الشوط كالأصل")
        clone_fail = cs.make_counts("app-test", "main", SHA, {"isolation": 0, "clone": 1}, (0, 0, 0, 0, 0), None,
                                    ["isolation", "clone"], KINDS)
        self.assertFalse(clone_fail["clone_ok"])
        self.assertEqual(clone_fail["ran"], ["isolation", "clone"])
        unknown = cs.make_counts("dry-run", "main", SHA, {"isolation": 0, "clone": 0, "app/src/x": 1, "unit": 0},
                                 (0, 0, 0, 0, 0), None, ["isolation", "clone", "app/src/x", "unit"], KINDS)
        self.assertEqual(unknown["failed_phase_ids"], ["unknown"])

    def test_missing_isolation_is_a_failed_isolation(self):
        c = cs.make_counts("app-test", "main", SHA, {"clone": 0}, (9, 0, 0, 0, 1), None, ["clone"], KINDS)
        self.assertEqual((c["ran"], c["failed_phase_ids"], c["clone_ok"], c["ok"]), (["isolation"], ["isolation"], False, False))
        self.assertTrue(cs.validate(raw(c), "app-test", "main", SHA, KINDS))

    def test_unrun_mandatory_phases_are_recorded_as_failed_125(self):
        """المحاكي لم يُقلع ⇒ أطوارُه الإلزاميّةُ لم تجرِ: تُسجَّل فاشلةً (125) فيسقط الشوطُ لا يختفي ناتجُه."""
        ran, rcs = full_rcs("tasmi-model-emulator-smoke")
        ran = [p for p in ran if p != "emu"]
        rcs = {p: rcs[p] for p in ran}
        c = cs.make_counts("tasmi-model-emulator-smoke", "api30", SHA, rcs, (3, 0, 0, 0, 1), None, ran, KINDS)
        self.assertIn("emu", c["ran"])
        self.assertEqual(c["failed_phase_ids"], ["emu"])
        self.assertFalse(c["ok"])
        self.assertTrue(cs.validate(raw(c), "tasmi-model-emulator-smoke", "api30", SHA, KINDS))

    def test_phase_counted_kinds_count_each_named_phase(self):
        ran, rcs = full_rcs("dry-run")
        c = cs.make_counts("dry-run", "main", SHA, rcs, (0, 0, 0, 0, 0), None, ran, KINDS)
        self.assertEqual((c["tests"], c["failures"], c["ok"]), (1, 0, True))   # unit وحدها؛ العزل والتنظيف لا يُعدّان

    def test_cli_emit_validate_phases_timeout(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            st = os.path.join(d, "state"); os.makedirs(st)
            ran, rcs = full_rcs("dry-run")
            with open(os.path.join(st, "ran.txt"), "w") as f:
                f.write("\n".join(ran) + "\n")
            for k, v in rcs.items():
                with open(os.path.join(st, f"rc_{k}"), "w") as f:
                    f.write(f"{v}\n")
            out = os.path.join(d, "counts.json")
            self.assertEqual(cs.main(["emit", "--state", st, "--kind", "dry-run", "--leg", "main", "--sha", SHA,
                                      "--out", out]), 0)
            self.assertEqual(cs.main(["validate", out, "--kind", "dry-run", "--leg", "main", "--sha", SHA]), 0)
            self.assertEqual(cs.main(["validate", out, "--kind", "dry-run", "--leg", "main", "--sha", "f" * 40]), 1)
            self.assertEqual(cs.main(["phases", "--kind", "app-test", "--leg", "main", "--mode", "build"]), 0)
            self.assertEqual(cs.main(["phases", "--kind", "nope", "--leg", "main", "--mode", "build"]), 2)
            self.assertEqual(cs.main(["timeout", "--kind", "device-matrix", "--leg", "a26-phone"]), 0)
            self.assertEqual(cs.main(["timeout", "--kind", "device-matrix", "--leg", "zzz"]), 2)

    def test_emit_without_state_files_is_a_failed_isolation_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            st = os.path.join(d, "state"); os.makedirs(st)
            out = os.path.join(d, "counts.json")
            self.assertEqual(cs.main(["emit", "--state", st, "--kind", "app-test", "--leg", "main", "--sha", SHA,
                                      "--out", out]), 0)
            with open(out) as f:
                c = json.load(f)
            self.assertEqual((c["ran"], c["ok"]), (["isolation"], False))

    def test_emit_does_not_read_the_tree_after_failed_cleanup_or_with_no_tree(self):
        """أ3: تنظيفٌ فاشل (rc_cleanup≠0) أو --no-tree ⇒ لا يُقرأ JUnit ولا ملفّاتُ الجهاز أصلاً (والشوطُ يسقط بفشل cleanup)."""
        with tempfile.TemporaryDirectory() as d:
            st, src = os.path.join(d, "st"), os.path.join(d, "src")
            os.makedirs(st)
            base = os.path.join(src, "app", "build", "test-results", "t"); os.makedirs(base)
            put(os.path.join(base, "a.xml"), junit_xml(5))
            ran, rcs = full_rcs("app-test")
            ran = ran + ["cleanup"]

            def emit(cleanup_rc, *extra):
                put(os.path.join(st, "ran.txt"), ("\n".join(ran) + "\n").encode())
                for k, v in dict(rcs, cleanup=cleanup_rc).items():
                    put(os.path.join(st, "rc_" + k), f"{v}\n".encode())
                out = os.path.join(d, "c.json")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cs.main(["emit", "--state", st, "--src", src, "--kind", "app-test", "--leg", "main",
                                              "--sha", SHA, "--out", out] + list(extra)), 0)
                with open(out, "rb") as f:
                    return json.loads(f.read())
            ok = emit(0)
            self.assertEqual((ok["tests"], ok["reports"]), (5, 1))
            bad = emit(1)
            self.assertEqual((bad["tests"], bad["reports"], bad["ok"]), (0, 0, False))
            self.assertIn("cleanup", bad["failed_phase_ids"])
            nt = emit(0, "--no-tree")
            self.assertEqual((nt["tests"], nt["reports"]), (0, 0))

    def test_emu_params_are_closed_patterns(self):
        """أ2: معاملاتُ المحاكي تصل إلى بيئة البناء ⇒ خمسٌ بأنماطٍ مغلقة لكلّ شوطٍ بمحاكٍ، ولا شيءَ لغيره، وأيُّ انحرافٍ يُرفض."""
        for kind, spec in KINDS.items():
            for leg in spec["legs"]:
                got = cs.emu_params(KINDS, kind, leg["id"])
                self.assertEqual(len(got), 5 if "emu" in leg else 0, (kind, leg["id"]))
        self.assertEqual(cs.emu_params(KINDS, "device-matrix", "a24-small-x86"), ["24", "default", "x86", "Nexus S", "1024M"])
        for field, bad in (("profile", "pixel;id"), ("profile", "a$(x)"), ("target", "Google APIs"), ("arch", "arm64"),
                           ("api", "3"), ("ram", "2G"), ("ram", "2048M\n")):
            k = json.loads(json.dumps(KINDS))
            k["ads-emulator-smoke"]["legs"][0]["emu"][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(cs.SchemaError):
                cs.emu_params(k, "ads-emulator-smoke", "api29")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cs.main(["emu", "--kind", "ads-emulator-smoke", "--leg", "api34"]), 0)
        self.assertEqual(out.getvalue().splitlines(), ["34", "google_apis", "x86_64", "pixel_2", "2048M"])

    def test_emit_rejects_unknown_kind(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cs.main(["emit", "--state", d, "--kind", "nope", "--leg", "main", "--sha", SHA,
                                      "--out", os.path.join(d, "o")]), 1)


def junit_xml(tests=2, failures=0, errors=0):
    return (f'<?xml version="1.0"?><testsuite name="x" tests="{tests}" failures="{failures}" errors="{errors}" '
            f'skipped="0"></testsuite>').encode()


class JunitSafetyTest(unittest.TestCase):
    def tree(self, d, files):
        base = os.path.join(d, "app", "build", "test-results", "t")
        os.makedirs(base)
        for name, data in files.items():
            put(os.path.join(base, name), data)
        return base

    def test_reads_regular_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.tree(d, {"a.xml": junit_xml(3, 1), "b.xml": junit_xml(4, 0, 1), "c.txt": b"x"})
            self.assertEqual(cs.junit(d, "app-test"), (7, 1, 1, 0, 2))

    def test_symlinked_file_is_not_read_and_counts_as_error(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            secret = os.path.join(other, "s.xml")
            put(secret, junit_xml(999))
            base = self.tree(d, {"a.xml": junit_xml(1)})
            os.symlink(secret, os.path.join(base, "link.xml"))
            t, f, e, s, r = cs.junit(d, "app-test")
            self.assertEqual((t, r), (1, 1))
            self.assertEqual(e, 1)

    def test_symlinked_directory_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            put(os.path.join(other, "s.xml"), junit_xml(999))
            base = self.tree(d, {"a.xml": junit_xml(1)})
            os.symlink(other, os.path.join(base, "sub"))
            t, _, e, _, r = cs.junit(d, "app-test")
            self.assertEqual((t, r, e), (1, 1, 1))

    def test_symlinked_results_root_is_refused(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            put(os.path.join(other, "s.xml"), junit_xml(999))
            os.makedirs(os.path.join(d, "app", "build"))
            os.symlink(other, os.path.join(d, "app", "build", "test-results"))
            self.assertEqual(cs.junit(d, "app-test"), (0, 0, 0, 0, 0))
            real = os.path.join(d, "x"); os.makedirs(real)
            os.symlink(os.path.join(d, "app"), os.path.join(real, "app"))
            self.assertEqual(cs.junit(real, "app-test"), (0, 0, 0, 0, 0))   # مكوّنٌ رمزيٌّ في الطريق

    def test_oversize_doctype_and_garbage_are_errors(self):
        with tempfile.TemporaryDirectory() as d:
            big = junit_xml(5) + b" " * (cs.MAX_XML_BYTES + 1)
            self.tree(d, {"big.xml": big, "dt.xml": b'<!DOCTYPE a [<!ENTITY x "y">]><testsuite tests="9"/>',
                          "bad.xml": b"<testsuite", "ok.xml": junit_xml(2)})
            t, f, e, s, r = cs.junit(d, "app-test")
            self.assertEqual((t, r, e), (2, 1, 3))

    def test_file_count_cap(self):
        with tempfile.TemporaryDirectory() as d:
            base = self.tree(d, {})
            for i in range(cs.MAX_XML_FILES + 5):
                put(os.path.join(base, f"{i}.xml"), b"<testsuite tests='1'/>")
            t, _, e, _, r = cs.junit(d, "app-test")
            self.assertGreaterEqual(e, 1)
            self.assertLessEqual(r, cs.MAX_XML_FILES)

    def test_huge_attribute_values_are_clamped(self):
        with tempfile.TemporaryDirectory() as d:
            self.tree(d, {"a.xml": b'<testsuite tests="99999999999999999999" failures="x"/>'})
            t, f, e, s, r = cs.junit(d, "app-test")
            self.assertEqual((t, f, r), (cs.MAX_INT, 0, 1))

    def test_hard_link_fifo_and_symlinked_device_dir_are_not_read(self):
        """أ3: ملفٌّ بأكثرَ من رابطٍ صلب (قد يكون ملفّاً خارج الشجرة) لا يُقرأ؛ وFIFO باسم .xml لا يعلّق القراءةَ (O_NONBLOCK)؛
        ومجلّدُ الجهاز الرمزيّ لا يُتبع."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            outside = os.path.join(other, "o.xml")
            put(outside, junit_xml(999))
            base = self.tree(d, {"a.xml": junit_xml(2)})
            os.link(outside, os.path.join(base, "hard.xml"))
            os.mkfifo(os.path.join(base, "pipe.xml"))
            t, f, e, s, r = cs.junit(d, "app-test")
            self.assertEqual((t, r, e), (2, 1, 2))
            dev = os.path.join(other, "dev"); os.makedirs(dev)
            put(os.path.join(dev, "result.md"), "✅".encode())
            put(os.path.join(dev, "tasmi.md"), "التسميع: ✅\n".encode())
            self.assertEqual(cs.device_results(dev), (2, 0))
            os.symlink(dev, os.path.join(d, "devlink"))
            self.assertEqual(cs.device_results(os.path.join(d, "devlink")), (2, 2))
            self.assertEqual(cs.junit("relative/path", "app-test"), (0, 0, 0, 0, 0))   # المسارُ يجب أن يكون مطلقاً

    def test_device_results_ignore_links(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            dev = os.path.join(d, "dev"); os.makedirs(dev)
            put(os.path.join(other, "r.md"), "✅".encode())
            os.symlink(os.path.join(other, "r.md"), os.path.join(dev, "result.md"))
            put(os.path.join(dev, "tasmi.md"), "التسميع: ✅ ممتاز\n".encode())
            self.assertEqual(cs.device_results(dev), (2, 1))   # result.md رابطٌ ⇒ فاشل
            self.assertEqual(cs.device_results(os.path.join(d, "none")), (2, 2))


if __name__ == "__main__":
    unittest.main()
