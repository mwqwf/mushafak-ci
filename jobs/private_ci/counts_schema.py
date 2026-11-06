#!/usr/bin/env python3
"""🔢 مخطّطُ counts.json الصارم — الجسرُ الوحيد (مع السجلّ المشفَّر) من وظيفة build غيرِ الموثوقة إلى publish الموثوقة.

⛔ ما يعبر أرقامٌ ومعرّفاتٌ من قوائمَ ثابتة فقط — لا نصَّ حرّاً ولا اسمَ اختبارٍ ولا رسالة؛ فلا يصير المخطّطُ قناةَ تسريب
   إلى الملخّص العلنيّ أو وصفِ الحالة. وما خالف المخطّطَ بحرفٍ يُرفض كلُّه (ولا يُصلَح ولا يُقتطع).

الأوامر:
  emit     — في المشرف (finalize · v3): يعدّ رموزَ خروج الأطوار (التي سجّلها المشرفُ بنفسه) وتقاريرَ JUnit من شجرة البناء
             (ملفّاتٌ عاديّةٌ فقط · لا روابط · بحدّ حجمٍ وعدد) ويكتب counts.json بالمخطّط (الإصدار 2: ran).
  validate — في publish: يتحقّق من ملفٍّ نزل في الـArtifact ويطبع «صالح/مرفوض» ورمزَ السبب فقط.
  phases   — للمشرف: أطوارُ النوع والشوط لوضعٍ (build|emu) سطراً لكلّ طور «id who».
  timeout  — للمشرف: مهلةُ الشوط بالدقائق.
"""
import argparse
import json
import os
import re
import stat
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_VERSION = 2
MAX_BYTES = 4096
MAX_INT = 1_000_000
MAX_PHASE_IDS = 32
MAX_XML_BYTES = 16 * 1024 * 1024   # حدُّ الملفّ الواحد من تقارير JUnit
MAX_XML_FILES = 10_000             # حدُّ الملفّات المَزورة في الشجرة كلّها
MISSING_RC = 125                   # رمزُ طورٍ إلزاميٍّ لم يُشغَّل (يسجّله emit بنفسه)
SHA_RE = re.compile(r"[0-9a-f]{40}")
KEYS = {"schema", "kind", "leg", "sha", "tests", "failures", "errors", "skipped", "reports", "phases",
        "failed_phases", "failed_phase_ids", "ran", "clone_ok", "ok"}
INT_KEYS = ("tests", "failures", "errors", "skipped", "reports", "phases", "failed_phases")
# معرّفاتُ الأطوار المسموحة (أسماءُ rc_* في build.sh)؛ وما سواها يُعدّ «unknown» ولا يُنقل اسمُه.
PHASES = frozenset({
    "clone", "unit", "app_guide", "no_blocking", "back_handling", "i18n_verify", "i18n_locale", "gradle", "pip",
    "fixture_selftest", "fixture_audit", "ffmpeg", "phoneme_gold", "jni", "whisper", "fatiha", "disk", "kvm",
    "request", "bundletool", "lint", "emu", "emu_smoke", "emu_tasmi", "kind", "unknown",
    "isolation", "cleanup",
    # أطوارٌ تجريبيّةٌ للاختبار المحلّيّ وحده (kinds.json الإنتاجيّ لا يذكرها — يفرض ذلك test_counts_schema)
    "selftest_ok", "selftest_fail", "selftest_noisy", "selftest_forge", "selftest_env",
})
# أطوارُ تجهيزٍ لا تُعدّ «اختباراً» حين لا تقرير JUnit (لكنّ فشلَها يُسقط الشوط).
PREP = frozenset({"clone", "gradle", "pip", "ffmpeg", "whisper", "fatiha", "disk", "kvm", "bundletool", "request",
                  "lint", "kind", "isolation", "cleanup"})
# lint لا يُسقط الشوط (كما في device-matrix.yml الأصليّ: يُعدّ والحكمُ على المحاكيات).
NON_FATAL = frozenset({"lint"})
XML_DIRS = {
    "app-test": ["app/build/test-results", "core/quran/build/test-results", "core/learning/build/test-results"],
    "engine-test": ["engine/recitation/build/test-results"],
    "tasmi-model-emulator-smoke": ["app/build/outputs/androidTest-results/connected"],
    "ui-shots-emu": ["app/build/outputs/androidTest-results/connected"],
    "ads-emulator-smoke": ["app/build/outputs/androidTest-results/connected"],
}
# أنواعٌ بلا JUnit (أو بعضُها): كلُّ طورٍ مسمّى غيرِ تجهيزيّ يُعدّ اختباراً واحداً.
PHASE_COUNTED = frozenset({"dry-run", "engine-test", "hudhud-feature-test", "device-matrix"})


class SchemaError(ValueError):
    """رمزُ سببٍ ثابت (لا يحمل قيمةً واردة)."""


def load_kinds(path=None):
    with open(path or os.path.join(HERE, "kinds.json"), encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def derived_ok(c):
    return bool(c["clone_ok"]) and c["tests"] > 0 and c["failures"] == 0 and c["errors"] == 0 \
        and c["failed_phases"] == 0


def _no_dupes(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise SchemaError("duplicate-key")
        seen[k] = v
    return seen


def _no_constants(_name):
    raise SchemaError("non-finite")


def validate(raw, kind, leg, sha, kinds):
    """يُعيد القاموسَ إن طابق المخطّطَ حرفيّاً، وإلا يرفع SchemaError برمزٍ ثابت."""
    if not isinstance(raw, (bytes, bytearray)):
        raise SchemaError("not-bytes")
    if len(raw) == 0 or len(raw) > MAX_BYTES:
        raise SchemaError("size")
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise SchemaError("encoding")
    try:
        c = json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_no_constants)
    except SchemaError:
        raise
    except ValueError:
        raise SchemaError("json")
    if not isinstance(c, dict):
        raise SchemaError("not-object")
    if set(c) != KEYS:
        raise SchemaError("keys")
    if type(c["schema"]) is not int or c["schema"] != SCHEMA_VERSION:
        raise SchemaError("version")
    if kind not in kinds or c["kind"] != kind:
        raise SchemaError("kind")
    if c["leg"] != leg or leg not in {x["id"] for x in kinds[kind]["legs"]}:
        raise SchemaError("leg")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha) or c["sha"] != sha:
        raise SchemaError("sha")
    for k in INT_KEYS:
        v = c[k]
        if type(v) is not int or v < 0 or v > MAX_INT:
            raise SchemaError("int")
    for k in ("clone_ok", "ok"):
        if type(c[k]) is not bool:
            raise SchemaError("bool")
    ids = c["failed_phase_ids"]
    if not isinstance(ids, list) or len(ids) > MAX_PHASE_IDS:
        raise SchemaError("phase-ids")
    if any(not isinstance(i, str) or i not in PHASES for i in ids) or len(set(ids)) != len(ids):
        raise SchemaError("phase-ids")
    ran = c["ran"]
    if not isinstance(ran, list) or len(ran) > MAX_PHASE_IDS:
        raise SchemaError("ran")
    if any(not isinstance(i, str) or i not in PHASES for i in ran) or len(set(ran)) != len(ran):
        raise SchemaError("ran")
    if not set(ids) <= set(ran):
        raise SchemaError("phase-ids")
    if len(ids) != c["failed_phases"] or c["phases"] != len(ran) or c["failed_phases"] > c["phases"]:
        raise SchemaError("phase-count")
    # D12: الأطوارُ الإلزاميّة. العزلُ دائماً؛ والاستنساخُ ما لم يفشل العزلُ (فلا يبلغ المفتاحُ الاستنساخَ أصلاً)؛
    # وإن نجح الاستنساخُ فكلُّ طورٍ غيرِ اختياريٍّ في kinds.json. ⇒ شوطٌ لم تجرِ أطوارُه لا يُقبل ولا ينجح.
    required = {"isolation"}
    if "isolation" not in ids:
        required.add("clone")
    if c["clone_ok"]:
        required |= mandatory_phases(kinds, kind)
    if not required <= set(ran):
        raise SchemaError("phases-missing")
    if c["clone_ok"] != ("clone" in ran and "clone" not in ids):
        raise SchemaError("clone-mismatch")
    if "isolation" in ids and ("clone" in ran or len(ran) != 1 + ("cleanup" in ran)):
        raise SchemaError("clone-mismatch")   # عزلٌ فاشل ⇒ لا استنساخَ ولا بناء: المفتاحُ لم يُلمس
    if c["failures"] > c["tests"]:
        raise SchemaError("failures-gt-tests")
    if c["ok"] != derived_ok(c):
        raise SchemaError("ok-mismatch")
    return c


def mandatory_phases(kinds, kind):
    """معرّفاتُ الأطوار غيرِ الاختياريّة للنوع (قائمةٌ من kinds.json الموثوق لا من الناتج)."""
    return {p["id"] for p in kinds[kind].get("phases", []) if not p.get("optional")}


def phases_for(kinds, kind, leg_id, mode):
    """أطوارُ النوع لوضع المشرف (build: ما قبل المحاكي · emu: داخل خطوة المحاكي) بترتيبها؛
    والطورُ الاختياريُّ لا يُدرج إلا إن وُسم الشوطُ باسمه (lint: true)."""
    leg = next((x for x in kinds[kind]["legs"] if x["id"] == leg_id), None)
    if leg is None:
        raise SchemaError("leg")
    want_emu = mode == "emu"
    out = []
    for p in kinds[kind].get("phases", []):
        if bool(p.get("emu")) != want_emu:
            continue
        if p.get("optional") and leg.get(p["id"]) is not True:
            continue
        out.append((p["id"], p["who"]))
    return out


# ───── emit (المشرف) ─────
def _lstat_dir(path):
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _safe_join(base, rel):
    """يمشي مكوّناتِ rel من base ويرفض أيَّ رابطٍ رمزيٍّ أو غيرِ مجلّد في الطريق؛ يُعيد المسارَ أو None."""
    cur = base
    if not _lstat_dir(cur):
        return None
    for part in [p for p in rel.split("/") if p]:
        cur = os.path.join(cur, part)
        if not _lstat_dir(cur):
            return None
    return cur


def _read_regular(path, cap):
    """بايتاتُ ملفٍّ عاديٍّ (لا رابطاً) بحدّ cap؛ None إن لم يكن عاديّاً أو تجاوز الحدّ."""
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > cap:
            return None
        with open(path, "rb") as f:
            data = f.read(cap + 1)
    except OSError:
        return None
    return data if len(data) <= cap else None


def read_ran(state):
    """ran.txt: ما سجّله المشرفُ وحده بترتيب تنفيذه؛ بلا تكرار، وما ليس في القائمة يصير unknown."""
    ran = []
    try:
        with open(os.path.join(state, "ran.txt"), encoding="utf-8") as f:
            for line in f.read().split("\n"):
                line = line.strip()
                if not line:
                    continue
                pid = line if line in PHASES else "unknown"
                if pid not in ran:
                    ran.append(pid)
    except OSError:
        pass
    return ran[:MAX_PHASE_IDS]


def read_rcs(state, ran=None):
    """رموزُ خروج الأطوار من مجلّد المشرف؛ المفقودُ لطورٍ سُجّل ⇒ 1."""
    rcs = {}
    names = list(ran) if ran is not None else [n[3:] for n in os.listdir(state) if n.startswith("rc_")]
    for name in names:
        try:
            with open(os.path.join(state, "rc_" + name), encoding="utf-8") as f:
                rcs[name] = int(f.read().strip() or 1)
        except (ValueError, OSError):
            rcs[name] = 1
    return rcs


def junit(src, kind):
    """مجموعُ تقارير JUnit من شجرة البناء: ملفّاتٌ عاديّةٌ فقط (lstat) بلا اتّباع روابط، بحدّ 16 م.ب للملفّ و10,000 ملفّ.
    ما خالف (رابطٌ · كبيرٌ · DOCTYPE/ENTITY · غيرُ مقروء) يُعدّ عطباً واحداً ولا يُقرأ."""
    t = f = e = s = reports = 0
    seen = 0
    for rel in XML_DIRS.get(kind, []):
        top = _safe_join(src, rel)
        if top is None:
            continue
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            for dn in list(dirnames):          # مجلّدٌ رمزيٌّ لا يُنزَل إليه (os.walk لا يتبعه أصلاً)؛ يُسجَّل عطباً
                if not _lstat_dir(os.path.join(dirpath, dn)):
                    dirnames.remove(dn)
                    e += 1
            seen += len(dirnames)
            for fn in filenames:
                seen += 1
                if seen > MAX_XML_FILES:
                    return t, f, min(e + 1, MAX_INT), s, reports
                if not fn.endswith(".xml"):
                    continue
                data = _read_regular(os.path.join(dirpath, fn), MAX_XML_BYTES)
                if data is None or b"<!ENTITY" in data or b"<!DOCTYPE" in data:
                    e += 1
                    continue
                try:
                    r = ET.fromstring(data)
                except ET.ParseError:
                    e += 1
                    continue
                if r.tag not in ("testsuite", "testsuites"):
                    continue
                reports += 1
                t += _int_attr(r, "tests")
                f += _int_attr(r, "failures")
                e += _int_attr(r, "errors")
                s += _int_attr(r, "skipped")
    return t, f, e, s, reports


def _int_attr(node, name):
    try:
        return max(0, min(int(node.get(name, 0) or 0), MAX_INT))
    except ValueError:
        return 0


def device_results(device):
    base = device if device and _lstat_dir(device) else None
    res = _read_regular(os.path.join(base, "result.md"), MAX_XML_BYTES) if base else None
    tas = _read_regular(os.path.join(base, "tasmi.md"), MAX_XML_BYTES) if base else None
    ok1 = res is not None and "✅" in res.decode("utf-8", errors="replace")
    ok2 = tas is not None and bool(re.search(r"^التسميع: ✅", tas.decode("utf-8", errors="replace"), re.M))
    return 2, (0 if ok1 else 1) + (0 if ok2 else 1)


def make_counts(kind, leg, sha, rcs, junit_counts, device=None, ran=None, kinds=None):
    """يبني counts. ran: ما سجّله المشرفُ بترتيبه (الافتراضُ مفاتيحُ rcs). ومع kinds:
    فقدُ العزل ⇒ يُعدّ فاشلاً؛ ونجاحُ الاستنساخ مع طورٍ إلزاميٍّ لم يُشغَّل ⇒ يُسجَّل بالرمز 125 فيسقط الشوطُ (فشلُ أطوارٍ لا غيابُ ناتج)."""
    t, f, e, s, reports = junit_counts
    ran = list(ran) if ran is not None else list(rcs)
    ran = [p if p in PHASES else "unknown" for p in ran]
    ran = list(dict.fromkeys(ran))
    rcs = dict(rcs)
    if "isolation" not in ran:
        ran = ["isolation"]
        rcs = {"isolation": 1}
    clone_ran_ok = "clone" in ran and rcs.get("clone", 1) == 0
    if kinds is not None and clone_ran_ok and rcs.get("isolation", 1) == 0:
        for pid in sorted(mandatory_phases(kinds, kind) - set(ran)):
            ran.append(pid)
            rcs[pid] = MISSING_RC
    ran = ran[:MAX_PHASE_IDS]
    known = {p: rcs.get(p, 1) for p in ran}
    if kind in PHASE_COUNTED:
        named = {k: v for k, v in known.items() if k not in PREP}
        t += len(named)
        f += sum(1 for v in named.values() if v)
    if kind == "device-matrix" and device is not None:
        dt, df = device
        t += dt
        f += df
    failed = [k for k in ran if known[k] and k not in NON_FATAL]
    c = {"schema": SCHEMA_VERSION, "kind": kind, "leg": leg, "sha": sha,
         "tests": min(t, MAX_INT), "failures": min(f, t, MAX_INT), "errors": min(e, MAX_INT), "skipped": min(s, MAX_INT),
         "reports": min(reports, MAX_INT), "phases": len(ran), "failed_phases": len(failed),
         "failed_phase_ids": failed, "ran": ran, "clone_ok": clone_ran_ok and "clone" not in failed, "ok": False}
    c["ok"] = derived_ok(c)
    return c


def cmd_emit(a):
    kinds = load_kinds(a.kinds or None)
    if a.kind not in kinds:
        raise SchemaError("kind")
    ran = read_ran(a.state)
    rcs = read_rcs(a.state, ran)
    jc = junit(a.src, a.kind) if a.src and _lstat_dir(a.src) else (0, 0, 0, 0, 0)
    dev = device_results(a.device) if a.kind == "device-matrix" and a.device else None
    c = make_counts(a.kind, a.leg, a.sha, rcs, jc, dev, ran, kinds)
    raw = json.dumps(c, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    validate(raw, a.kind, a.leg, a.sha, kinds)  # ما نكتبه نحن يطابق ما سنقبله هناك
    with open(a.out, "wb") as f:
        f.write(raw)
    return 0


def cmd_validate(a):
    kinds = load_kinds(a.kinds or None)
    try:
        with open(a.file, "rb") as f:
            raw = f.read(MAX_BYTES + 1)
        validate(raw, a.kind, a.leg, a.sha, kinds)
    except SchemaError as e:
        print(f"المخطّط: ⛔ مرفوض ({e})")
        return 1
    except OSError:
        print("المخطّط: ⛔ تعذّرت القراءة")
        return 2
    print("المخطّط: ✅ صالح")
    return 0


def cmd_phases(a):
    kinds = load_kinds(a.kinds or None)
    if a.kind not in kinds:
        return 2
    for pid, who in phases_for(kinds, a.kind, a.leg, a.mode):
        print(pid, who)
    return 0


def cmd_timeout(a):
    kinds = load_kinds(a.kinds or None)
    if a.kind not in kinds:
        return 2
    leg = next((x for x in kinds[a.kind]["legs"] if x["id"] == a.leg), None)
    if leg is None:
        return 2
    print(int(leg.get("timeout", 60)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="مخطّط counts.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emit")
    e.add_argument("--state", required=True)
    e.add_argument("--src", default="")
    e.add_argument("--device", default="")
    e.add_argument("--kind", required=True)
    e.add_argument("--leg", required=True)
    e.add_argument("--sha", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--kinds", default="")
    v = sub.add_parser("validate")
    v.add_argument("file")
    v.add_argument("--kind", required=True)
    v.add_argument("--leg", required=True)
    v.add_argument("--sha", required=True)
    v.add_argument("--kinds", default="")
    p = sub.add_parser("phases")
    p.add_argument("--kind", required=True)
    p.add_argument("--leg", required=True)
    p.add_argument("--mode", choices=("build", "emu"), required=True)
    p.add_argument("--kinds", default="")
    t = sub.add_parser("timeout")
    t.add_argument("--kind", required=True)
    t.add_argument("--leg", required=True)
    t.add_argument("--kinds", default="")
    a = ap.parse_args(argv)
    try:
        return {"emit": cmd_emit, "validate": cmd_validate, "phases": cmd_phases, "timeout": cmd_timeout}[a.cmd](a)
    except SchemaError as err:
        print(f"المخطّط: ⛔ ({err})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
