#!/usr/bin/env python3
"""🧪 وظيفةُ publish (v3): الحكمُ المركَّب لكلّ تركيبةٍ من {الاختبارات، الحارس، الرفع، المخطّط، تجربةٌ جافّة} · حالاتُ «تعذّر» ·
قراءةُ الناتج بياناتٍ لا شيفرة عبر artifact_check · الحارسُ على سجلّات build قبل الحالة · ولا رمزَ GitHub في التحويل إلى
مضيف التخزين ولا مضيفَ خارج القائمة ولا سجلّاً فوق الحدّ (D18) · D20-ج: تجربةٌ جافّةٌ خضراءُ بلا بيانة R2 ·
وحالةٌ واحدةٌ فقط تُرسل في كلّ تشغيلة."""
import hashlib
import io
import itertools
import json
import os
import sys
import tempfile
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import counts_schema as cs  # noqa: E402
import guard  # noqa: E402
import publish as pub  # noqa: E402
from test_artifact_check import fake_age  # noqa: E402

SHA = "a" * 40
KINDS = cs.load_kinds()
AGE = fake_age()


def counts(kind="app-test", leg="main", ok=True, clone_ok=True):
    """counts صالحٌ لشوطٍ كاملٍ بأطواره الإلزاميّة؛ وclone_ok=False ⇒ فشلُ الاستنساخ (ran = العزل والاستنساخ)."""
    if clone_ok:
        ran = ["isolation", "clone"] + [p["id"] for p in KINDS[kind]["phases"] if not p.get("optional")]
        rcs = {p: 0 for p in ran}
    else:
        ran, rcs = ["isolation", "clone"], {"isolation": 0, "clone": 1}
    return cs.make_counts(kind, leg, SHA, rcs, (10, 0 if ok else 2, 0, 0, 1), None, ran, KINDS)


def leg(present=True, schema_ok=True, ok=True, clone_ok=True):
    return {"id": "main", "present": present, "schema_ok": schema_ok and present,
            "counts": counts(ok=ok, clone_ok=clone_ok) if (schema_ok and present) else None}


class ComposeTest(unittest.TestCase):
    def test_isolation_failure_is_its_own_reason(self):
        """فشلُ العزل (لا استنساخَ بعده) يُحكم «isolation» لا «clone» — ليُعرف السببُ من الحالة الواحدة."""
        c = cs.make_counts("app-test", "main", SHA, {"isolation": 1, "cleanup": 0}, (0, 0, 0, 0, 0), None,
                           ["isolation", "cleanup"], KINDS)
        self.assertEqual((c["clone_ok"], c["failed_phase_ids"]), (False, ["isolation"]))
        l = {"id": "main", "present": True, "schema_ok": True, "counts": c}
        self.assertEqual(pub.compose(True, [l], "clean", "skipped", dry=True), ("error", "isolation"))
        self.assertEqual(pub.compose(True, [l], "clean", "verified"), ("error", "isolation"))

    def test_all_sixteen_combinations(self):
        """{اختبارات ناجحة/فاشلة} × {حارس نظيف/مشتبه} × {رفع محقَّق/متعذّر} × {مخطّط صحيح/مرفوض}."""
        for tests_ok, guard_clean, upload_ok, schema_ok in itertools.product([True, False], repeat=4):
            legs = [leg(schema_ok=schema_ok, ok=tests_ok)]
            state, reason = pub.compose(True, legs, "clean" if guard_clean else "suspicious",
                                        "verified" if upload_ok else "failed")
            with self.subTest(tests=tests_ok, guard=guard_clean, upload=upload_ok, schema=schema_ok):
                if tests_ok and guard_clean and upload_ok and schema_ok:
                    self.assertEqual((state, reason), ("success", "ok"))
                elif not guard_clean:
                    self.assertEqual((state, reason), ("failure", "guard"))
                elif not schema_ok:
                    self.assertEqual((state, reason), ("failure", "schema"))
                elif not tests_ok:
                    self.assertEqual((state, reason), ("failure", "tests"))
                else:  # كلُّ شيءٍ سليمٌ إلا الرفع ⇒ تعذّر لا نجاح
                    self.assertEqual((state, reason), ("error", "upload"))

    def test_error_cases(self):
        good = [leg()]
        self.assertEqual(pub.compose(False, good, "clean", "verified"), ("error", "plan"))
        self.assertEqual(pub.compose(True, [leg(present=False)], "clean", "verified"), ("error", "artifact"))
        self.assertEqual(pub.compose(True, [], "clean", "verified"), ("error", "artifact"))
        self.assertEqual(pub.compose(True, [leg(clone_ok=False)], "clean", "verified"), ("error", "clone"))
        self.assertEqual(pub.compose(True, good, "unavailable", "verified"), ("error", "guard-unavailable"))
        self.assertEqual(pub.compose(True, good, "clean", "skipped"), ("error", "upload"))
        # شوطٌ ناجحٌ وآخرُ غائب ⇒ تعذّر؛ وشوطٌ فاشلٌ وآخرُ غائب ⇒ فشل (الإخفاقُ يُقدَّم)
        self.assertEqual(pub.compose(True, [leg(), leg(present=False)], "clean", "verified"), ("error", "artifact"))
        self.assertEqual(pub.compose(True, [leg(ok=False), leg(present=False)], "clean", "verified"),
                         ("failure", "tests"))

    def test_dry_run_accepts_skipped_upload_only_for_dry(self):
        """D20-ج: تجربةٌ جافّةٌ بلا بيانة R2 تُقبل (skipped) لهذا النوع وحده؛ ولأيّ نوعٍ خاصٍّ يبقى skipped تعذّراً."""
        good = [leg()]
        self.assertEqual(pub.compose(True, good, "clean", "skipped", dry=True), ("success", "ok"))
        self.assertEqual(pub.compose(True, good, "clean", "skipped", dry=False), ("error", "upload"))
        self.assertEqual(pub.compose(True, good, "clean", "skipped"), ("error", "upload"))
        # الرفعُ الفاشلُ يبقى تعذّراً ولو في التجربة؛ والحارسُ والاختباراتُ تحكم كما هي
        self.assertEqual(pub.compose(True, good, "clean", "failed", dry=True), ("error", "upload"))
        self.assertEqual(pub.compose(True, good, "suspicious", "skipped", dry=True), ("failure", "guard"))
        self.assertEqual(pub.compose(True, good, "unavailable", "skipped", dry=True), ("error", "guard-unavailable"))
        self.assertEqual(pub.compose(True, [leg(ok=False)], "clean", "skipped", dry=True), ("failure", "tests"))
        self.assertEqual(pub.compose(True, [leg(present=False)], "clean", "skipped", dry=True), ("error", "artifact"))
        self.assertEqual(pub.compose(True, [leg(schema_ok=False)], "clean", "skipped", dry=True), ("failure", "schema"))
        self.assertEqual(pub.compose(True, [leg(clone_ok=False)], "clean", "skipped", dry=True), ("error", "clone"))

    def test_sixteen_combinations_with_dry_flag(self):
        """كلُّ التركيبات مرّةً أخرى بـdry=True: skipped يُقبل وحده، ولا يرفع أيَّ حكمٍ آخر."""
        for tests_ok, guard_clean, up, schema_ok in itertools.product([True, False], [True, False],
                                                                      ["verified", "failed", "skipped"], [True, False]):
            legs = [leg(schema_ok=schema_ok, ok=tests_ok)]
            gs = "clean" if guard_clean else "suspicious"
            s_dry, r_dry = pub.compose(True, legs, gs, up, dry=True)
            s_prv, r_prv = pub.compose(True, legs, gs, up, dry=False)
            with self.subTest(tests=tests_ok, guard=guard_clean, up=up, schema=schema_ok):
                if (s_dry, r_dry) != (s_prv, r_prv):
                    self.assertEqual((up, s_dry, s_prv, r_prv), ("skipped", "success", "error", "upload"))
                    self.assertTrue(tests_ok and guard_clean and schema_ok)

    def test_never_success_unless_everything(self):
        states = set()
        for g, up in itertools.product(["clean", "suspicious", "unavailable"], ["verified", "failed", "skipped"]):
            for legs in ([leg()], [leg(ok=False)], [leg(schema_ok=False)], [leg(present=False)], [leg(clone_ok=False)]):
                s, _ = pub.compose(True, legs, g, up)
                states.add(s)
                if s == "success":
                    self.assertEqual((g, up), ("clean", "verified"))
                    self.assertTrue(legs[0]["counts"]["ok"])
        self.assertEqual(states, {"success", "failure", "error"})


def make_art(root, leg_id, files):
    d = os.path.join(root, f"pci-{leg_id}")
    os.makedirs(d)
    for name, data in files.items():
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)
    return d


class ReadLegsTest(unittest.TestCase):
    def good_files(self, kind="app-test", leg_id="main"):
        return {"counts.json": json.dumps(counts(kind, leg_id)).encode(), "log.txt.gz.age": AGE}

    def test_good(self):
        with tempfile.TemporaryDirectory() as d:
            make_art(d, "main", self.good_files())
            (r,) = pub.read_legs(d, "app-test", SHA, KINDS)
            self.assertTrue(r["schema_ok"])
            self.assertEqual(r["log"], AGE)

    def test_rejections(self):
        cases = {
            "plaintext-log": ({"log.txt.gz.age": b"> Task :app:compileDebugKotlin\n"}, True),
            "old-schema": ({"counts.json": json.dumps(dict(counts(), schema=1)).encode()}, True),
            "missing-mandatory-phases": ({"counts.json": json.dumps(dict(counts(), ran=["isolation", "clone"],
                                                                         phases=2)).encode()}, True),
            "extra-file": ({"source.zip": b"PK"}, True),
            "bad-schema": ({"counts.json": b'{"tests": 1}'}, True),
            "missing-log": (None, False),
        }
        for label, (patch, present) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as d:
                files = self.good_files()
                if patch is None:
                    files.pop("log.txt.gz.age")
                else:
                    files.update(patch)
                make_art(d, "main", files)
                (r,) = pub.read_legs(d, "app-test", SHA, KINDS)
                self.assertFalse(r["schema_ok"])
                self.assertEqual(r["present"], present)

    def test_uses_artifact_check_rules(self):
        """القاعدةُ نفسُها التي فحص بها المشرفُ ناتجَه: مقطعٌ غيرُ X25519 · ترويسةٌ بلا حمولة · MAC قصير ⇒ مرفوض."""
        bad_logs = {"scrypt": fake_age().replace(b"-> X25519 ", b"-> scrypt ", 1), "no-payload": fake_age(payload=0),
                    "gz-only": b"\x1f\x8b\x08" + bytes(100)}
        for label, log in bad_logs.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as d:
                files = self.good_files()
                files["log.txt.gz.age"] = log
                make_art(d, "main", files)
                (r,) = pub.read_legs(d, "app-test", SHA, KINDS)
                self.assertEqual((r["present"], r["schema_ok"], r["why"]), (True, False, "age-header"))
                self.assertEqual(pub.compose(True, [r], "clean", "verified"), ("failure", "schema"))

    @unittest.skipUnless(__import__("shutil").which("age") and __import__("shutil").which("age-keygen"), "لا age")
    def test_real_age_file_is_accepted(self):
        from test_artifact_check import real_age
        with tempfile.TemporaryDirectory() as d:
            files = self.good_files()
            files["log.txt.gz.age"] = real_age("سجلّ ".encode() * 1000)
            make_art(d, "main", files)
            (r,) = pub.read_legs(d, "app-test", SHA, KINDS)
            self.assertTrue(r["schema_ok"])

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            target = os.path.join(other, "secret")
            with open(target, "wb") as f:
                f.write(AGE)
            dd = make_art(d, "main", {"counts.json": self.good_files()["counts.json"]})
            os.symlink(target, os.path.join(dd, "log.txt.gz.age"))
            (r,) = pub.read_legs(d, "app-test", SHA, KINDS)
            self.assertFalse(r["schema_ok"])
            self.assertTrue(r["present"])

    def test_unexpected_artifact_dir(self):
        with tempfile.TemporaryDirectory() as d:
            make_art(d, "main", self.good_files())
            make_art(d, "evil", self.good_files())
            legs = pub.read_legs(d, "app-test", SHA, KINDS)
            self.assertEqual(len(legs), 2)
            self.assertEqual(pub.compose(True, legs, "clean", "verified"), ("failure", "schema"))

    def test_all_legs_of_multi_leg_kind(self):
        with tempfile.TemporaryDirectory() as d:
            make_art(d, "api29", self.good_files("ads-emulator-smoke", "api29"))
            legs = pub.read_legs(d, "ads-emulator-smoke", SHA, KINDS)
            self.assertEqual([x["id"] for x in legs], ["api29", "api34"])
            self.assertFalse(legs[1]["present"])


class Resp(io.BytesIO):
    def __init__(self, data=b"", status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FetchLogsTest(unittest.TestCase):
    BLOB = "https://productionresultssa7.blob.core.windows.net/actions-results/log?sig=x"

    def jobs_resp(self, n=1):
        jobs = [{"id": 7 + i, "name": "build-main"} for i in range(n)] + [{"id": 99, "name": "plan"}]
        return Resp(json.dumps({"jobs": jobs}).encode())

    def fetch(self, location=None, body=b"Run bash x\n", direct=None, legs=1, token="tok"):
        seen = []

        def api(req, timeout=0):
            seen.append(("api", req.full_url, req.headers.get("Authorization")))
            if req.full_url.endswith("/jobs?per_page=100"):
                return self.jobs_resp(legs)
            if direct is not None:
                return Resp(direct)
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": location}, None)

        def plain(req, timeout=0):
            seen.append(("plain", req.full_url, req.headers.get("Authorization")))
            return Resp(body)
        return pub.fetch_build_logs("mwqwf/mushafak-ci", "5", "1", token, legs, api, plain), seen

    def test_redirect_drops_auth_and_counts_jobs(self):
        texts, seen = self.fetch(self.BLOB)
        self.assertEqual(len(texts), 1)
        self.assertEqual([s for s in seen if s[0] == "plain"], [("plain", self.BLOB, None)])
        self.assertTrue(all(s[2] == "Bearer tok" for s in seen if s[0] == "api"))
        nop = lambda r, timeout=0: Resp(b"")  # noqa: E731
        with self.assertRaises(RuntimeError) as cm:                              # شوطٌ ناقص
            pub.fetch_build_logs("mwqwf/mushafak-ci", "5", "1", "tok", 2, lambda r, timeout=0: self.jobs_resp(1), nop)
        self.assertEqual(str(cm.exception), "job-count")
        with self.assertRaises(RuntimeError) as cm:                              # بلا رمز
            self.fetch(self.BLOB, token="")
        self.assertEqual(str(cm.exception), "no-token")

    def test_accepted_hosts(self):
        for loc in (self.BLOB, "https://pipelines.actions.githubusercontent.com/abc/log",
                    "https://results-receiver.actions.githubusercontent.com:443/x", "https://api.github.com/x"):
            with self.subTest(loc=loc):
                texts, _ = self.fetch(loc)
                self.assertEqual(len(texts), 1)

    def test_rejected_hosts_never_reach_plain_open(self):
        bad = ("https://evil.example/log", "http://x.blob.core.windows.net/log",
               "https://blob.core.windows.net/log", "https://.blob.core.windows.net/log",
               "https://blob.core.windows.net.evil.com/log", "https://evilblob.core.windows.net.attacker.io/log",
               "https://x.blob.core.windows.net:8443/log", "https://user:pw@x.blob.core.windows.net/log",
               "https://x.actions.githubusercontent.com@evil.example/log", "https://actions.githubusercontent.com/x",
               "https://githubusercontent.com/x", "ftp://x.blob.core.windows.net/x", "//x.blob.core.windows.net/x",
               "", "file:///etc/passwd", "https://127.0.0.1/x", "https://[::1]/x")
        for loc in bad:
            with self.subTest(loc=loc):
                seen = []

                def api(req, timeout=0, loc=loc):
                    if req.full_url.endswith("/jobs?per_page=100"):
                        return self.jobs_resp(1)
                    raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": loc}, None)

                def plain(req, timeout=0):
                    seen.append(req.full_url)
                    return Resp(b"x")
                with self.assertRaises(RuntimeError) as cm:
                    pub.fetch_build_logs("mwqwf/mushafak-ci", "5", "1", "tok", 1, api, plain)
                self.assertEqual(str(cm.exception), "logs-host")
                self.assertEqual(seen, [])

    def test_size_cap_on_redirect_and_direct(self):
        old = pub.MAX_BUILD_LOG
        pub.MAX_BUILD_LOG = 16
        try:
            self.assertEqual(len(self.fetch(self.BLOB, body=b"x" * 16)[0]), 1)           # عند الحدّ تماماً: مقبول
            for kw in ({"location": self.BLOB, "body": b"x" * 17}, {"direct": b"y" * 17}):
                with self.subTest(kw=list(kw)), self.assertRaises(RuntimeError) as cm:
                    self.fetch(**kw)
                self.assertEqual(str(cm.exception), "logs-size")
            self.assertEqual(len(self.fetch(direct=b"y" * 16)[0]), 1)
        finally:
            pub.MAX_BUILD_LOG = old

    def test_read_is_bounded_not_unbounded(self):
        """يُقرأ cap+1 بايتٍ على الأكثر (لا read() بلا حدّ فيُملأ ذاكرةُ العدّاء)."""
        asked = []

        class Probe(Resp):
            def read(self, n=-1):
                asked.append(n)
                return super().read(n)

        def api(req, timeout=0):
            if req.full_url.endswith("/jobs?per_page=100"):
                return self.jobs_resp(1)
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": self.BLOB}, None)
        pub.fetch_build_logs("mwqwf/mushafak-ci", "5", "1", "tok", 1, api, lambda r, timeout=0: Probe(b"ok"))
        self.assertEqual(asked, [pub.MAX_BUILD_LOG + 1])
        self.assertEqual(pub.MAX_BUILD_LOG, 32 * 1024 * 1024)

    def chain(self, locs, final=b"Run bash x\n"):
        """سلسلةُ تحويلات: api ← locs[0] ← locs[1] … ثمّ الجسمُ النهائيّ؛ ويُسجَّل كلُّ طلبٍ بترويسة اعتماده."""
        seen = []

        def api(req, timeout=0):
            seen.append(("api", req.full_url, req.headers.get("Authorization")))
            if req.full_url.endswith("/jobs?per_page=100"):
                return self.jobs_resp(1)
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": locs[0]}, None)

        def plain(req, timeout=0):
            seen.append(("plain", req.full_url, req.get_header("Authorization")))
            i = locs.index(req.full_url)
            if i + 1 < len(locs):
                raise urllib.error.HTTPError(req.full_url, 307, "Temp", {"Location": locs[i + 1]}, None)
            return Resp(final)
        return (lambda: pub.fetch_build_logs("mwqwf/mushafak-ci", "5", "1", "tok", 1, api, plain)), seen

    def test_a9_every_hop_is_checked_and_capped_at_three(self):
        """أ9: كلُّ قفزةٍ يُفحص مضيفُها قبل الطلب، ولا Authorization بعد الطلب الأوّل، ولا أكثرَ من ثلاث قفزات."""
        two = [self.BLOB, "https://pipelines.actions.githubusercontent.com/x/log"]
        run, seen = self.chain(two)
        self.assertEqual(len(run()), 1)
        self.assertEqual([s[1] for s in seen if s[0] == "plain"], two)
        self.assertTrue(all(s[2] is None for s in seen if s[0] == "plain"))
        # قفزةٌ ثانيةٌ إلى مضيفٍ ممنوع ⇒ تُرفض قبل أن تُطلب
        run, seen = self.chain([self.BLOB, "https://evil.example/log"])
        with self.assertRaises(RuntimeError) as cm:
            run()
        self.assertEqual(str(cm.exception), "logs-host")
        self.assertEqual([s[1] for s in seen if s[0] == "plain"], [self.BLOB])
        # ثلاثُ قفزاتٍ مقبولة؛ والرابعةُ ⇒ logs-hops (ولا تُطلب)
        three = [self.BLOB + "&a=1", self.BLOB + "&a=2", self.BLOB + "&a=3"]
        run, _ = self.chain(three)
        self.assertEqual(len(run()), 1)
        run, seen = self.chain(three + [self.BLOB + "&a=4"])
        with self.assertRaises(RuntimeError) as cm:
            run()
        self.assertEqual(str(cm.exception), "logs-hops")
        self.assertNotIn(self.BLOB + "&a=4", [s[1] for s in seen])
        # تحويلٌ نسبيٌّ لا يُحلّ ولا يُتبع
        run, seen = self.chain(["/repos/x/logs2"])
        with self.assertRaises(RuntimeError) as cm:
            run()
        self.assertEqual(str(cm.exception), "logs-host")

    def test_a9_default_openers_never_follow_redirects(self):
        import urllib.request
        api_open, plain_open = pub.default_openers()
        for op in (api_open, plain_open):
            self.assertIsNot(op, urllib.request.urlopen)
            self.assertTrue(any(isinstance(h, pub._NoRedirect) for h in op.__self__.handlers))

    def test_non_redirect_http_error(self):
        def api(req, timeout=0):
            if req.full_url.endswith("/jobs?per_page=100"):
                return self.jobs_resp(1)
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        with self.assertRaises(RuntimeError) as cm:
            pub.fetch_build_logs("mwqwf/mushafak-ci", "5", "1", "tok", 1, api, lambda r, timeout=0: Resp(b""))
        self.assertEqual(str(cm.exception), "logs-status")


class RunTest(unittest.TestCase):
    """التشغيلُ كاملاً بمزيّفاتٍ للشبكة: حالةٌ واحدةٌ تُرسل، بمحتوى يطابق الحكم."""

    def env(self, d, **kw):
        e = {"PCI_KIND": "app-test", "PCI_SHA": SHA, "PCI_PLAN_OK": "true", "PCI_IN": d,
             "GITHUB_REPOSITORY": "mwqwf/mushafak-ci", "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1",
             "GH_TOKEN": "gh", "PCI_STATUS_TOKEN": "st",
             "PCI_R2_JSON": json.dumps({"endpoint": "https://" + "0" * 32 + ".r2.cloudflarestorage.com",
                                        "accessKeyId": "a", "secretAccessKey": "b", "bucket": "mushafak-ci-logs"})}
        e.update(kw)
        return e

    def go(self, env, logs=("clean line\n",), upload_ok=True, ok=True):
        sent, uploaded = [], []

        def fetch(*a):
            if logs is None:
                raise RuntimeError("x")
            return list(logs)

        def up(creds, key, body):
            uploaded.append((key, hashlib.md5(body).hexdigest()))
            if not upload_ok:
                raise RuntimeError("x")
            return True

        def send(sha, payload, token):
            sent.append((sha, payload, token))
            return 201
        res = pub.run(env, KINDS, fetch, up, send)
        return res, sent, uploaded

    def test_success_sends_one_status(self):
        with tempfile.TemporaryDirectory() as d:
            make_art(d, "main", ReadLegsTest().good_files())
            res, sent, up = self.go(self.env(d))
            self.assertEqual(res["state"], "success")
            self.assertEqual(len(sent), 1)
            sha, payload, token = sent[0]
            self.assertEqual((sha, payload["context"], payload["state"], token), (SHA, "public-ci/app-test", "success", "st"))
            self.assertEqual(payload["target_url"], "https://github.com/mwqwf/mushafak-ci/actions/runs/42")
            self.assertEqual(up[0][0], "logs/42/1/app-test/main.log.txt.gz.age")
            self.assertEqual(sum(guard.scan_text(res["summary"]).values()), 0)

    def test_planted_leak_in_build_log_fails_status(self):
        with tempfile.TemporaryDirectory() as d:
            make_art(d, "main", ReadLegsTest().good_files())
            res, sent, _ = self.go(self.env(d), logs=("ok\n", guard.planted_line() + "\n"))
            self.assertEqual((res["state"], res["reason"], res["suspicious"]), ("failure", "guard", True))
            self.assertEqual(sent[0][1]["state"], "failure")

    def test_errors(self):
        with tempfile.TemporaryDirectory() as d:
            make_art(d, "main", ReadLegsTest().good_files())
            for label, env, kw, want in (
                ("upload", self.env(d), {"upload_ok": False}, ("error", "upload")),
                ("guard-unavailable", self.env(d), {"logs": None}, ("error", "guard-unavailable")),
                ("no-creds", self.env(d, PCI_R2_JSON=""), {}, ("error", "upload")),
                ("bad-bucket", self.env(d, PCI_R2_JSON=json.dumps({"endpoint": "https://" + "0" * 32 +
                                                                    ".r2.cloudflarestorage.com", "accessKeyId": "a",
                                                                    "secretAccessKey": "b",
                                                                    "bucket": "mushafak-private"})), {},
                 ("error", "upload")),
                ("plan", self.env(d, PCI_PLAN_OK="false"), {}, ("error", "plan")),
                ("no-artifact", self.env(os.path.join(d, "nope")), {}, ("error", "artifact")),
            ):
                with self.subTest(label=label):
                    res, sent, _ = self.go(env, **kw)
                    self.assertEqual((res["state"], res["reason"]), want)
                    self.assertEqual(len(sent), 1)
                    self.assertEqual(sent[0][1]["state"], want[0])

    def dry_env(self, d, **kw):
        c = counts("dry-run", "main")
        make_art(d, "main", {"counts.json": json.dumps(c).encode(), "log.txt.gz.age": AGE})
        return self.env(d, PCI_KIND="dry-run", PCI_STATUS_TOKEN="", **kw)

    def test_fix6_dry_run_with_creds_present_never_calls_the_uploader(self):
        """الإصلاحُ السادس (Codex 15 · الملاحظة 3): جافّةٌ + بيانةٌ صالحةٌ موجودة ⇒ لا استدعاءَ للرافع ولا حالة · skipped وتحذيرٌ صريح
        · ولا تُعدّ اشتباهاً. (كان السلوكُ السابق يرفع إلى dryrun/ متى وُجدت البيانة.)"""
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                res, sent, up = self.go(self.dry_env(d))
            out = buf.getvalue()
            self.assertEqual((res["state"], res["reason"], res["upload"], res["suspicious"]), ("success", "ok", "skipped", False))
            self.assertEqual((sent, up), ([], []))
            self.assertIn("الرفع: ⚠ بيانة R2 وصلت إلى تجربةٍ جافّة — أُهملت", out)
            self.assertIn("الرفع: تجربةٌ جافّة — لا رفعَ ولا أثرَ خارجيّ", out)
            self.assertNotIn("✅", out.split("الرفع: تجربةٌ جافّة")[1].split("\n")[0])
        for kind in ("dry-run-emu", "dry-run-stress"):
            if kind not in KINDS:
                continue
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                leg = KINDS[kind]["legs"][0]["id"]
                make_art(d, leg, {"counts.json": json.dumps(counts(kind, leg)).encode(), "log.txt.gz.age": AGE})
                res, sent, up = self.go(self.env(d, PCI_KIND=kind, PCI_STATUS_TOKEN=""))
                self.assertEqual((res["upload"], sent, up), ("skipped", [], []))

    def test_dry_run_green_without_any_secret(self):
        """D20-ج: بلا بيانة R2 ولا رمز حالة: الرفعُ skipped ويُقبل في الحكم؛ ولا حالةَ تُرسل ولا رفع."""
        with tempfile.TemporaryDirectory() as d:
            env = self.dry_env(d)
            env.pop("PCI_R2_JSON")
            res, sent, up = self.go(env)
            self.assertEqual((res["state"], res["reason"], res["upload"]), ("success", "ok", "skipped"))
            self.assertEqual((sent, up), ([], []))
            env["PCI_R2_JSON"] = ""
            self.assertEqual(self.go(env)[0]["state"], "success")

    def test_dry_run_with_bad_creds_is_ignored_not_read(self):
        """الإصلاحُ السادس: في الجافّة لا تُقرأ البيانةُ أصلاً (صالحةً أو مرفوضة) ⇒ لا رفعَ ولا أثر · skipped."""
        with tempfile.TemporaryDirectory() as d:
            res, sent, up = self.go(self.dry_env(d, PCI_R2_JSON="{not json"))
            self.assertEqual((res["state"], res["reason"], res["upload"]), ("success", "ok", "skipped"))
            self.assertEqual((sent, up), ([], []))

    def test_private_source_still_uploads_under_private_prefix(self):
        """سلوكُ المصدر الخاصّ كما هو: يُرفع كلُّ شوطٍ صالحٍ إلى logs/ وبيانةٌ مرفوضةٌ تعذّر."""
        with tempfile.TemporaryDirectory() as d:
            make_art(d, "main", ReadLegsTest().good_files())
            res, _, up = self.go(self.env(d))
            self.assertEqual((res["upload"], len(up)), ("verified", 1))
            self.assertTrue(up[0][0].startswith("logs/42/1/app-test/"))
            res, _, up = self.go(self.env(d, PCI_R2_JSON="{not json"))
            self.assertEqual((res["state"], res["reason"], res["upload"], up), ("error", "upload", "failed", []))

    def test_dry_run_failures_still_fail_without_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.dry_env(d)
            env.pop("PCI_R2_JSON")
            res, _, _ = self.go(env, logs=("ok\n", guard.planted_line() + "\n"))   # plant_leak ⇒ يسقط
            self.assertEqual((res["state"], res["reason"], res["suspicious"]), ("failure", "guard", True))
        with tempfile.TemporaryDirectory() as d:
            c = counts("dry-run", "main", ok=False)
            make_art(d, "main", {"counts.json": json.dumps(c).encode(), "log.txt.gz.age": AGE})
            env = self.env(d, PCI_KIND="dry-run", PCI_STATUS_TOKEN="")
            env.pop("PCI_R2_JSON")
            self.assertEqual(self.go(env)[0]["reason"], "tests")

    def test_private_kind_without_creds_is_never_success(self):
        """لأيّ نوعٍ خاصٍّ يبقى skipped تعذّراً (error · upload) — ولو كلُّ ما عداه سليم."""
        with tempfile.TemporaryDirectory() as d:
            make_art(d, "main", ReadLegsTest().good_files())
            env = self.env(d)
            env.pop("PCI_R2_JSON")
            res, sent, up = self.go(env)
            self.assertEqual((res["state"], res["reason"]), ("error", "upload"))
            self.assertEqual((len(sent), sent[0][1]["state"], up), (1, "error", []))

    def test_dry_flag_cannot_be_forged_by_a_private_kind(self):
        """kind يأتي من الخطّة المفحوصة لا من الناتج: ناتجُ dry-run لا يرفع نوعاً خاصّاً إلى «تجربة»."""
        with tempfile.TemporaryDirectory() as d:
            make_art(d, "main", ReadLegsTest().good_files())
            env = self.env(d, PCI_KIND="app-test")
            env.pop("PCI_R2_JSON")
            self.assertEqual(self.go(env)[0]["state"], "error")

    def test_invalid_kind_or_sha_sends_nothing(self):
        for kind, sha in (("recite-switch-emu", SHA), ("app-test", "x"), ("", "")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                res, sent, _ = self.go(self.env(d, PCI_KIND=kind, PCI_SHA=sha))
                self.assertEqual(res["state"], "error")
                self.assertEqual(sent, [])


class MainTest(unittest.TestCase):
    def test_internal_error_never_success_and_outputs_written(self):
        with tempfile.TemporaryDirectory() as d:
            out, summ = os.path.join(d, "o"), os.path.join(d, "s")
            env = {"PCI_KIND": "app-test", "PCI_SHA": SHA, "PCI_PLAN_OK": "true", "PCI_IN": d,
                   "GITHUB_OUTPUT": out, "GITHUB_STEP_SUMMARY": summ, "GITHUB_RUN_ID": "1",
                   "GITHUB_REPOSITORY": "mwqwf/mushafak-ci"}
            orig_run, orig_send = pub.run, pub.status_back.send
            sent = []
            pub.run = lambda e: (_ for _ in ()).throw(KeyError("boom"))
            pub.status_back.send = lambda sha, p, t: sent.append(p) or 201
            old = dict(os.environ)
            try:
                os.environ.clear(); os.environ.update(env)
                rc = pub.main()
            finally:
                os.environ.clear(); os.environ.update(old)
                pub.run, pub.status_back.send = orig_run, orig_send
            self.assertEqual(rc, 1)
            self.assertEqual(sent[0]["state"], "error")
            self.assertIn("state=error", open(out, encoding="utf-8").read())
            self.assertTrue(os.path.getsize(summ) > 0)


if __name__ == "__main__":
    unittest.main()
