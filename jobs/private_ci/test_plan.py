#!/usr/bin/env python3
"""🧪 اختباراتُ الخطّة (plan.py): الحمولةُ بثلاثة مفاتيح · ربطُ runner_sha بإيداع التشغيلة (D1-ب) · main وحده ·
والتجربةُ اليدويّة وحدها · ورسائلُ رفضٍ لا تعكس ما ورد · ومخرجاتٌ جزئيّةٌ لحالة «تعذّر»."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plan  # noqa: E402

SHA = "a" * 40
RUNNER = "b" * 40
KINDS = plan.load_kinds()
SEVEN = {"app-test", "engine-test", "tasmi-model-emulator-smoke", "ui-shots-emu", "hudhud-feature-test",
         "device-matrix", "ads-emulator-smoke"}


def payload(**kw):
    p = {"sha": SHA, "kind": "app-test", "runner_sha": RUNNER}
    p.update(kw)
    return json.dumps(p)


class PlanTest(unittest.TestCase):
    def ok(self, **kw):
        args = dict(event="repository_dispatch", payload_json=payload(), dry_run="", plant="false", head_sha=RUNNER,
                    ref="refs/heads/main", kinds=KINDS)
        args.update(kw)
        return plan.decide(**args)

    def test_valid_dispatch(self):
        out = self.ok()
        self.assertEqual((out["ok"], out["kind"], out["sha"], out["runner_sha"], out["source"]),
                         ("true", "app-test", SHA, RUNNER, "private"))
        self.assertEqual(json.loads(out["legs"])[0]["id"], "main")

    def test_runner_sha_must_equal_head(self):
        """D1-ب: المنفَّذُ هو ما فُحص — إيداعٌ آخر على main بعد الفحص يُرفض، ويُخرج sha/kind لحالة «تعذّر»."""
        with self.assertRaises(plan.PlanError) as cm:
            self.ok(head_sha="c" * 40)
        self.assertEqual(cm.exception.partial, {"sha": SHA, "kind": "app-test", "source": "private"})
        for bad in ("", "B" * 40, RUNNER[:39], 7, None, RUNNER + "\n"):
            with self.subTest(bad=bad), self.assertRaises(plan.PlanError):
                self.ok(payload_json=json.dumps({"sha": SHA, "kind": "app-test", "runner_sha": bad}))

    def test_rejects_wrong_keys_bad_sha_bad_kind(self):
        bad = [
            {"sha": SHA, "kind": "app-test"},                                   # بلا runner_sha
            {"sha": SHA, "kind": "app-test", "runner_sha": RUNNER, "ref": "x"},  # مفتاحٌ رابع
            {"sha": SHA.upper(), "kind": "app-test", "runner_sha": RUNNER},
            {"sha": SHA + "; rm -rf /", "kind": "app-test", "runner_sha": RUNNER},
            {"sha": SHA, "kind": "recite-switch-emu", "runner_sha": RUNNER},     # لا يُنقل
            {"sha": SHA, "kind": "engine-models", "runner_sha": RUNNER},         # ليس من السبعة
            {"sha": SHA, "kind": "dry-run", "runner_sha": RUNNER},               # التجربةُ يدويّةٌ فقط
            {"sha": SHA, "kind": "dry-run-emu", "runner_sha": RUNNER},           # وكذا كلُّ نوعٍ مصدرُه عامّ
            {"sha": SHA, "kind": "dry-run-stress", "runner_sha": RUNNER},
            {"sha": SHA, "kind": "$(id)", "runner_sha": RUNNER},
            ["not", "a", "dict"],
        ]
        for p in bad:
            with self.subTest(p=p), self.assertRaises(plan.PlanError):
                self.ok(payload_json=json.dumps(p))
        for raw in ("{not json", "", "null"):
            with self.subTest(raw=raw), self.assertRaises(plan.PlanError):
                self.ok(payload_json=raw)

    def test_rejection_message_does_not_echo_input(self):
        with self.assertRaises(plan.PlanError) as cm:
            self.ok(payload_json=json.dumps({"sha": "deadbeef-SECRET", "kind": "x", "runner_sha": "y"}))
        self.assertNotIn("SECRET", str(cm.exception))
        self.assertEqual(cm.exception.partial, {})

    def test_main_branch_only(self):
        with self.assertRaises(plan.PlanError):
            self.ok(ref="refs/heads/evil")
        with self.assertRaises(plan.PlanError):
            self.ok(event="workflow_dispatch", dry_run="true", ref="refs/heads/evil")

    def test_workflow_dispatch_only_dry_run(self):
        with self.assertRaises(plan.PlanError):
            self.ok(event="workflow_dispatch", dry_run="false")
        out = self.ok(event="workflow_dispatch", dry_run="true", plant="true")
        self.assertEqual((out["kind"], out["source"], out["plant"], out["sha"]), ("dry-run", "public", "true", RUNNER))

    def test_dispatch_probe_switches_pick_public_kinds_and_never_both(self):
        """الإصلاحُ الخامس: emu_probe ⇒ dry-run-emu (شوطُه بمحاكٍ) · stress_tree ⇒ dry-run-stress · لا يجتمعان · ومصدرُهما عامّ."""
        out = self.ok(event="workflow_dispatch", dry_run="true", emu_probe="true")
        legs = json.loads(out["legs"])
        self.assertEqual((out["kind"], out["source"], out["sha"]), ("dry-run-emu", "public", RUNNER))
        self.assertEqual([(x["id"], x["has_emu"]) for x in legs], [("api30", True)])
        out = self.ok(event="workflow_dispatch", dry_run="true", stress_tree="true")
        self.assertEqual((out["kind"], out["source"]), ("dry-run-stress", "public"))
        self.assertFalse(json.loads(out["legs"])[0]["has_emu"])
        with self.assertRaises(plan.PlanError):
            self.ok(event="workflow_dispatch", dry_run="true", emu_probe="true", stress_tree="true")
        with self.assertRaises(plan.PlanError):
            self.ok(event="workflow_dispatch", dry_run="false", emu_probe="true")
        self.assertEqual(self.ok(event="workflow_dispatch", dry_run="true", emu_probe="false")["kind"], "dry-run")
        # المفتاحان لا يغيّران repository_dispatch
        self.assertEqual(self.ok(emu_probe="true")["kind"], "app-test")

    def test_plant_ignored_on_dispatch(self):
        self.assertEqual(self.ok(plant="true")["plant"], "false")

    def test_other_events_refused(self):
        for ev in ("pull_request", "pull_request_target", "push", "issue_comment", "workflow_run", ""):
            with self.subTest(ev=ev), self.assertRaises(plan.PlanError):
                self.ok(event=ev)

    def test_seven_kinds_and_timeouts(self):
        self.assertEqual(set(KINDS), SEVEN | {"dry-run", "dry-run-emu", "dry-run-stress"})
        self.assertEqual({k for k, v in KINDS.items() if v["source"] == "public"}, {"dry-run", "dry-run-emu", "dry-run-stress"})
        for k, spec in KINDS.items():
            ids = [leg["id"] for leg in spec["legs"]]
            self.assertEqual(len(ids), len(set(ids)), k)
            for leg in spec["legs"]:
                self.assertLessEqual(leg["timeout"], 120, k)
        self.assertEqual(len(json.loads(self.ok(payload_json=payload(kind="device-matrix"))["legs"])), 6)


class PlanCliTest(unittest.TestCase):
    def run_plan(self, env):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GITHUB_OUTPUT": out}
            full.update(env)
            r = subprocess.run([sys.executable, os.path.join(HERE, "plan.py")], env=full, capture_output=True,
                               text=True, timeout=30)
            text = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
            return r, dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    def test_mismatch_writes_partial_and_fails(self):
        r, out = self.run_plan({"PCI_EVENT": "repository_dispatch", "PCI_PAYLOAD": payload(),
                                "PCI_HEAD_SHA": "c" * 40, "PCI_REF": "refs/heads/main"})
        self.assertEqual(r.returncode, 1)
        # D15: يُكتب sha وkind وsource=private وok=false — فتُكتب publish حالةَ «تعذّر» لا «قيد الانتظار»
        self.assertEqual(out, {"sha": SHA, "kind": "app-test", "source": "private", "ok": "false"})
        self.assertNotIn(RUNNER, r.stdout)

    def test_mismatch_public_kind_is_never_emitted_as_private(self):
        """المصدرُ يُؤخذ من kinds.json لا من الحمولة: نوعٌ عامٌّ (لا يُرسل من الخاصّ أصلاً) لا يُخرج source=private."""
        r, out = self.run_plan({"PCI_EVENT": "repository_dispatch", "PCI_PAYLOAD": payload(kind="dry-run"),
                                "PCI_HEAD_SHA": RUNNER, "PCI_REF": "refs/heads/main"})
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("kind", out)   # dry-run مرفوضٌ قبل صحّة النوع فلا partial

    def test_ok_writes_all(self):
        r, out = self.run_plan({"PCI_EVENT": "repository_dispatch", "PCI_PAYLOAD": payload(kind="ads-emulator-smoke"),
                                "PCI_HEAD_SHA": RUNNER, "PCI_REF": "refs/heads/main"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(out["ok"], "true")
        self.assertEqual(len(json.loads(out["legs"])), 2)


if __name__ == "__main__":
    unittest.main()
