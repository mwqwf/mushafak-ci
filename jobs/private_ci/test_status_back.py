#!/usr/bin/env python3
"""🧪 الحالةُ الواحدة: سياقُ public-ci/<نوعٌ من السبعة> وحده · ثلاثُ حالاتٍ نهائيّة لا «قيد الانتظار» · وصفٌ من أعدادٍ
ورموزٍ ثابتة (≤140) · رابطٌ واحدٌ مسموح (صفحةُ التشغيلة في mushafak-ci) · ولا إرسالَ بلا رمز."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import status_back as sb  # noqa: E402

RUN = "https://github.com/mwqwf/mushafak-ci/actions/runs/42"


class StatusTest(unittest.TestCase):
    def test_payload_shape(self):
        p = sb.build_payload("success", "app-test", "ok", {"tests": 5, "failures": 0, "errors": 0}, RUN)
        self.assertEqual((p["state"], p["context"], p["target_url"]), ("success", "public-ci/app-test", RUN))
        self.assertIn("5 اختباراً", p["description"])
        self.assertLessEqual(len(p["description"]), 140)

    def test_description_excluded_count_is_a_number_only_and_optional(self):
        t = {"tests": 3082, "failures": 0, "errors": 0}
        self.assertIn("3 مستثناةً بالتصميم", sb.build_payload("success", "app-test", "ok", t, RUN, 3)["description"])
        self.assertNotIn("مستثناةً", sb.build_payload("success", "app-test", "ok", t, RUN)["description"])
        self.assertNotIn("مستثناةً", sb.build_payload("success", "app-test", "ok", t, RUN, 0)["description"])
        self.assertNotIn("مستثناةً", sb.build_description("error", "plan", None, 3))     # بلا أعدادٍ لا يُذكر
        with self.assertRaises(ValueError):
            sb.build_payload("success", "app-test", "ok", t, RUN, "x")                    # ليس رقماً ⇒ لا نصٌّ حرٌّ يعبر
        self.assertLessEqual(len(sb.build_description("failure", "isolation", {"tests": 999999, "failures": 999999, "errors": 999999}, 999)), 140)

    def test_only_final_states_and_known_kinds(self):
        for st in ("pending", "ok", ""):
            with self.subTest(st=st), self.assertRaises(ValueError):
                sb.build_payload(st, "app-test", "ok", None, RUN)
        for kind in ("recite-switch-emu", "engine-models", "dry-run", "app-test; x", "", "x" * 80):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                sb.build_payload("success", kind, "ok", None, RUN)
        self.assertEqual(len(sb.KINDS), 7)

    def test_link_allowlist(self):
        for url in ("https://github.com/mwqwf/rafiq-align-ci/actions/runs/42", "https://evil.example.com/",
                    RUN + "?x=1", "https://" + "0" * 32 + ".r2.cloudflarestorage.com/x?X-Amz-Signature=1", ""):
            with self.subTest(url=url), self.assertRaises(ValueError):
                sb.build_payload("failure", "app-test", "tests", None, url)

    def test_reasons_are_closed(self):
        with self.assertRaises(ValueError):
            sb.build_payload("error", "app-test", "app/src/main/x.kt", None, RUN)
        for r in sb.REASONS:
            self.assertLessEqual(len(sb.build_description("error", r, {"tests": 999999, "failures": 999999,
                                                                       "errors": 999999})), 140)

    def test_send_requires_token_and_sha(self):
        with self.assertRaises(ValueError):
            sb.send("a" * 40, {}, "")
        with self.assertRaises(ValueError):
            sb.send("x", {}, "t")
        seen = []

        class R:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def op(req, timeout=0):
            seen.append((req.full_url, json.loads(req.data), req.headers.get("Authorization")))
            return R()
        p = sb.build_payload("success", "device-matrix", "ok", None, RUN)
        self.assertEqual(sb.send("a" * 40, p, "tok", op), 201)
        self.assertEqual(seen[0][0], "https://api.github.com/repos/mwqwf/QuranRafiq/statuses/" + "a" * 40)
        self.assertEqual(seen[0][2], "Bearer tok")


if __name__ == "__main__":
    unittest.main()
