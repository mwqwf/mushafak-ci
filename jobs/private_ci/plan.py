#!/usr/bin/env python3
"""🧭 خطّةُ private-ci (v3) — تفحص الحدثَ وحمولتَه وتربط التنفيذ بالإيداع المفحوص، وتُخرج مصفوفةَ الأشواط.

⛔ الحمولةُ (client_payload) علنيّةٌ وغيرُ موثوقة: تُقرأ من متغيّر بيئة لا من حقنٍ في الصدفة، ولا يُقبل فيها إلا
   ثلاثةُ مفاتيح بالضبط: sha (الإيداعُ الخاصّ المختبَر) · kind (من kinds.json) · runner_sha (رأسُ main في هذا المستودع
   الذي فحص المرسِلُ الخاصُّ بصماتِ ملفّاته عنده).
⛔ D1-ب: إن خالف runner_sha إيداعَ هذه التشغيلة (github.sha) فشلت الخطّةُ فوراً — فالمنفَّذُ هو ما فُحص بعينه، لا ما
   دُفع إلى main بعد الفحص. ويُخرج sha وkind وsource (إن صحّ الأوّلان شكلاً) كي تكتب وظيفةُ publish حالةَ «تعذّر» بدل انتظارٍ معلَّق.
⛔ ولا تُطبع قيمةٌ واردةٌ عند الرفض — رسالةٌ ثابتةٌ فقط.
✅ workflow_dispatch لا يجري إلا تجربةً (dry_run=true) على هذا المستودع العامّ نفسِه؛ ولا طريقَ منه إلى الخاص.
   ومفتاحاه (الإصلاح الخامس): emu_probe=true ⇒ النوعُ dry-run-emu (مسبارُ المحاكي الحيّ) · stress_tree=true ⇒ dry-run-stress
   (شجرةٌ كبيرةٌ لقياس مهلة التملّك) — كلاهما مصدرُه عامّ، ولا يجتمعان. وrepository_dispatch لا يقبل نوعاً مصدرُه عامّ أصلاً.
✅ والحدثان كلاهما من main وحده (‏وبيئتا build/publish مقيَّدتان به أيضاً من الإعدادات).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHA_RE = re.compile(r"[0-9a-f]{40}")
MAIN_REF = "refs/heads/main"
PAYLOAD_KEYS = {"sha", "kind", "runner_sha"}
# أ4: مهلةُ وظيفة build في السير = ميزانيةُ الشوط (timeout في kinds.json، يحسبها المشرفُ من بدئه وتشمل finalize)
#     + هامشٌ لما قبل المشرف (checkout · setup-java) — فيسبق finalize دائماً إيقافَ GitHub. (لا حسابَ في تعابير YAML.)
JOB_MARGIN = 6


class PlanError(ValueError):
    """رفضٌ برسالةٍ ثابتة؛ وpartial يحمل ما يكفي لحالة «تعذّر» إن صحّ شكلُه."""

    def __init__(self, msg, partial=None):
        super().__init__(msg)
        self.partial = partial or {}


def load_kinds(path=None):
    with open(path or os.path.join(HERE, "kinds.json"), encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def legs_for(spec):
    legs = []
    for leg in spec["legs"]:
        legs.append({"id": leg["id"], "timeout": int(leg.get("timeout", 60)),
                     "job_timeout": int(leg.get("timeout", 60)) + JOB_MARGIN, "lint": bool(leg.get("lint", False)),
                     "has_emu": "emu" in leg,
                     "emu": leg.get("emu", {"api": 0, "target": "", "arch": "", "profile": "", "ram": ""})})
    return legs


def _true(v):
    return str(v).lower() == "true"


def decide(event, payload_json, dry_run, plant, head_sha, ref, kinds, emu_probe="false", stress_tree="false"):
    """تُعيد قاموسَ المخرجات أو ترفع PlanError."""
    if not SHA_RE.fullmatch(head_sha or ""):
        raise PlanError("⛔ رأسُ المستودع العام غيرُ صالح")
    if event == "workflow_dispatch":
        if ref != MAIN_REF:
            raise PlanError("⛔ التجربةُ من main وحده")
        if str(dry_run).lower() != "true":
            raise PlanError("⛔ التشغيلُ اليدويّ تجربةٌ فقط (dry_run=true) — ولا يُستنسخ منه المستودعُ الخاص")
        if _true(emu_probe) and _true(stress_tree):
            raise PlanError("⛔ تجربةٌ واحدةٌ في كلّ تشغيلة: emu_probe أو stress_tree لا كلاهما")
        kind = "dry-run-emu" if _true(emu_probe) else "dry-run-stress" if _true(stress_tree) else "dry-run"
        if kind not in kinds or kinds[kind].get("source") != "public":
            raise PlanError("⛔ نوعُ التجربة غيرُ معرَّف")
        sha, runner_sha = head_sha, head_sha
        plant = "true" if _true(plant) else "false"
    elif event == "repository_dispatch":
        try:
            payload = json.loads(payload_json or "null")
        except ValueError:
            raise PlanError("⛔ حمولةٌ غيرُ مقروءة")
        if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
            raise PlanError("⛔ الحمولةُ يجب أن تحمل sha وkind وrunner_sha وحدها")
        sha, kind, runner_sha = payload["sha"], payload["kind"], payload["runner_sha"]
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise PlanError("⛔ sha غيرُ صالح")
        # التجاربُ (كلُّ نوعٍ مصدرُه عامّ) يدويّةٌ وحدها — لا يطلبها المرسِلُ الخاصّ
        if not isinstance(kind, str) or kind not in kinds or kinds[kind].get("source") != "private":
            raise PlanError("⛔ نوعُ مهمّةٍ غيرُ مسموح")
        # D15: ما صحّ شكلُه يُخرج مع المصدر كي تكتب publish حالةَ «تعذّر» (error · plan) بدل أن يبقى «قيد الانتظار»
        partial = {"sha": sha, "kind": kind, "source": kinds[kind]["source"]}
        if ref != MAIN_REF:
            raise PlanError("⛔ الحدثُ ليس على main", partial)
        if not isinstance(runner_sha, str) or not SHA_RE.fullmatch(runner_sha):
            raise PlanError("⛔ runner_sha غيرُ صالح", partial)
        if runner_sha != head_sha:
            raise PlanError("⛔ runner_sha لا يطابق إيداعَ هذه التشغيلة — المنفَّذُ غيرُ ما فُحص", partial)
        plant = "false"
    else:
        raise PlanError("⛔ حدثٌ غيرُ مسموح")
    spec = kinds[kind]
    legs = legs_for(spec)
    return {
        "ok": "true",
        "kind": kind,
        "sha": sha,
        "runner_sha": runner_sha,
        "source": spec["source"],
        "plant": plant,
        "legs": json.dumps(legs, ensure_ascii=False, separators=(",", ":")),
    }


def write_outputs(path, out):
    with open(path, "a", encoding="utf-8") as f:
        for k, v in out.items():
            f.write(f"{k}={v}\n")


def main():
    e = os.environ
    try:
        out = decide(e.get("PCI_EVENT", ""), e.get("PCI_PAYLOAD", ""), e.get("PCI_DRY_RUN", ""),
                     e.get("PCI_PLANT", "false"), e.get("PCI_HEAD_SHA", ""), e.get("PCI_REF", ""), load_kinds(),
                     e.get("PCI_EMU_PROBE", "false"), e.get("PCI_STRESS_TREE", "false"))
    except PlanError as err:
        # ما صحّ شكلُه وحده يُخرج — لتكتب publish حالةَ «تعذّر» على الإيداع الخاصّ بدل أن يبقى «قيد الانتظار».
        write_outputs(e["GITHUB_OUTPUT"], dict(err.partial, ok="false"))
        print(str(err))
        return 1
    write_outputs(e["GITHUB_OUTPUT"], out)
    # ✅ ما يُطبع: النوعُ وعددُ الأشواط فقط (‏والـSHA علنيٌّ أصلاً في الحمولة).
    print(f"الخطّة: {out['kind']} · {len(json.loads(out['legs']))} شوط · المصدر {out['source']} · العدّاء مطابق")
    return 0


if __name__ == "__main__":
    sys.exit(main())
