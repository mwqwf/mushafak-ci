#!/usr/bin/env python3
"""📣 وظيفةُ publish (v3 · D2/D4/D13/D18/D20-ج) — السياقُ الموثوق الوحيد الذي يحمل أسرارَ R2 ورمزَ الحالة.

تجري على عدّاءٍ غيرِ عدّاء build، من أدواتٍ مستنسَخةٍ من هذا المستودع عند github.sha (لا ممّا خلّفه build)، ولا تنفّذ
شيئاً من مخرجات build: تقرأ counts.json **بياناتٍ** بمخطّطٍ صارم، وتعامل log.txt.gz.age **بايتاتٍ معتمةً** ترفعها كما هي.

الترتيب:
  ١. الناتج: لكلّ شوطٍ في kinds.json مجلّدُ pci-<شوط> يفحصه artifact_check نفسُه الذي فحصه المشرفُ قبل الرفع
     (ملفّان بالضبط · عاديّان · بحدّ حجم · مخطّطٌ صالح · ترويسةُ age كاملة).
  ٢. المخطّط: counts_schema.validate بالنوع والشوط والـSHA المتوقَّعة.
  ٣. الحارس: سجلّاتُ وظائف build العلنيّة لهذه التشغيلة تُنزَّل بالواجهة وتُفحص بـguard.py **قبل** الحالة؛ والملخّصُ
     المولَّد من الأرقام يُفحص قبل نشره.
  ٤. الرفع: كلُّ سجلٍّ مشفَّرٍ صالحٍ إلى mushafak-ci-logs، ثمّ HEAD يثبت الطولَ وMD5 (upload_r2.upload_verified).
  ٥. الحكمُ المركَّب (compose) ثمّ **حالةٌ واحدةٌ نهائيّة** public-ci/<النوع>.

الحكم: success فقط إن نجحت الاختبارات **و**الحارسُ نظيف **و**الرفعُ محقَّق **و**المخطّطُ صحيح.
       أيُّ إخفاقٍ (اختبار · اشتباه · مخطّطٌ مرفوض) ⇒ failure، ويُقدَّم على التعذّر.
       أيُّ تعذّرٍ (ناتجٌ غائب · استنساخ · حارسٌ لم يقرأ · رفعٌ غيرُ محقَّق · خطّةٌ مرفوضة · استثناء) ⇒ error.
⛔ لا يطبع إلا أعداداً ورموزَ سببٍ ثابتة؛ ونصُّ الاستثناء لا يُطبع (اسمُ صنفه فقط).
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artifact_check  # noqa: E402
import counts_schema  # noqa: E402
import guard  # noqa: E402
import status_back  # noqa: E402
import upload_r2  # noqa: E402

SHA_RE = re.compile(r"[0-9a-f]{40}")
EXPECTED_FILES = artifact_check.EXPECTED_FILES
MAX_BUILD_LOG = 32 * 1024 * 1024   # D18: حدُّ ما يُقرأ من سجلّ وظيفة build العلنيّ (والمشرفُ يقطع السجلَّ عند حدٍّ دون ذلك أصلاً)
LOG_HOST_SUFFIXES = (".actions.githubusercontent.com", ".blob.core.windows.net")


# ───── الحكمُ المركَّب ─────
def compose(plan_ok, legs, guard_state, upload_state, dry=False):
    """legs: قائمةُ قواميس {present, schema_ok, counts}. guard_state: clean|suspicious|unavailable.
    upload_state: verified|failed|skipped. dry: التجربةُ الجافّة (dry-run) — D20-ج: يُقبل فيها skipped (غيابُ بيانة R2)
    **لهذا النوع وحده**؛ ولأيّ نوعٍ خاصٍّ يبقى skipped تعذّراً. يُعيد (الحالة، رمزَ السبب)."""
    if not plan_ok:
        return "error", "plan"
    # ١) الإخفاقات أوّلاً (تُقدَّم على التعذّر: ما ثبت فشلُه لا يُكتب «تعذّر»)
    if guard_state == "suspicious":
        return "failure", "guard"
    if any(leg["present"] and not leg["schema_ok"] for leg in legs):
        return "failure", "schema"
    if any(leg["schema_ok"] and leg["counts"]["clone_ok"] and not leg["counts"]["ok"] for leg in legs):
        return "failure", "tests"
    # ٢) التعذّرات
    if not legs or any(not leg["present"] for leg in legs):
        return "error", "artifact"
    if any(leg["schema_ok"] and "isolation" in (leg["counts"].get("failed_phase_ids") or []) for leg in legs):
        return "error", "isolation"   # العزلُ لم يثبت على العدّاء ⇒ لا استنساخَ أصلاً (يُقدَّم على «تعذّر الاستنساخ»)
    if any(not leg["counts"]["clone_ok"] for leg in legs):
        return "error", "clone"
    if guard_state != "clean":
        return "error", "guard-unavailable"
    if upload_state != "verified" and not (dry and upload_state == "skipped"):
        return "error", "upload"
    return "success", "ok"


def totals(legs):
    t = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for leg in legs:
        if leg.get("schema_ok"):
            for k in t:
                t[k] += leg["counts"][k]
    return t


# ───── الناتج ─────
def read_legs(in_dir, kind, sha, kinds):
    """لكلّ شوطٍ متوقَّع: {id, present, schema_ok, counts, log, why}. وأيُّ مجلّدٍ غيرِ متوقَّع ⇒ شوطٌ مرفوض.
    القبولُ كلُّه عبر artifact_check (القاعدةُ نفسُها التي فحص بها المشرفُ ناتجَه قبل الرفع)."""
    expected = [leg["id"] for leg in kinds[kind]["legs"]]
    out = []
    try:
        names = set(os.listdir(in_dir)) if in_dir and os.path.isdir(in_dir) else set()
    except OSError:
        names = set()
    for leg_id in expected:
        d = os.path.join(in_dir or "", f"pci-{leg_id}")
        rec = {"id": leg_id, "present": False, "schema_ok": False, "counts": None, "log": None, "why": "missing"}
        if os.path.isdir(d) and not os.path.islink(d):
            rec["present"] = True
            try:
                entries = set(os.listdir(d))
            except OSError:
                entries = set()
            if entries < EXPECTED_FILES and not any(os.path.islink(os.path.join(d, n)) for n in entries):
                # ناقصٌ بلا زيادة (تعذّر التشفيرُ أو العدّ) ⇒ «تعذّر» لا «فشل»
                rec["present"], rec["why"] = False, "incomplete"
            else:
                try:
                    rec["counts"], rec["log"] = artifact_check.load_checked(d, kind, leg_id, sha, kinds)
                    rec["schema_ok"], rec["why"] = True, "ok"
                except artifact_check.ArtifactError as e:
                    rec["why"] = str(e)
        out.append(rec)
    for extra in sorted(n for n in names if n not in {f"pci-{x}" for x in expected}):
        out.append({"id": "unexpected", "present": True, "schema_ok": False, "counts": None, "log": None,
                    "why": "unexpected"})
    return out


# ───── الحارس على سجلّات build العلنيّة ─────
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # ⛔ لا يُحمل رمزُ GitHub إلى مضيف التخزين في التحويل
        return None


def _api(url, token, opener):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                               "Accept": "application/vnd.github+json",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    return opener(req, timeout=60)


def default_openers():
    no_redirect = urllib.request.build_opener(_NoRedirect)
    return no_redirect.open, urllib.request.urlopen


def _log_host_ok(url):
    """D18: التحويلُ إلى مضيف تخزينٍ معروفٍ فقط — https بلا بيانات مستخدم ولا منفذٍ غيرِ 443."""
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or "").lower()
        port_ok = p.port in (None, 443)
    except ValueError:
        return False
    if p.scheme != "https" or p.username is not None or p.password is not None or not port_ok:
        return False
    return host == "api.github.com" or any(host.endswith(sfx) and len(host) > len(sfx) for sfx in LOG_HOST_SUFFIXES)


def _read_capped(resp, cap=None):
    cap = MAX_BUILD_LOG if cap is None else cap
    data = resp.read(cap + 1)
    if len(data) > cap:
        raise RuntimeError("logs-size")
    return data


def fetch_build_logs(repo, run_id, attempt, token, legs_expected, api_open=None, plain_open=None):
    """نصوصُ سجلّات وظائف build-<شوط> لهذه المحاولة. ترفع RuntimeError عند أيّ تعذّر (⇒ unavailable)."""
    if api_open is None or plain_open is None:
        api_open, plain_open = default_openers()
    if not token:
        raise RuntimeError("no-token")
    with _api(f"https://api.github.com/repos/{repo}/actions/runs/{int(run_id)}/attempts/{int(attempt)}/jobs"
              "?per_page=100", token, api_open) as r:
        jobs = json.load(r).get("jobs", [])
    builds = [j for j in jobs if str(j.get("name", "")).startswith("build-")]
    if len(builds) != legs_expected:
        raise RuntimeError("job-count")
    texts = []
    for j in builds:
        try:
            resp = _api(f"https://api.github.com/repos/{repo}/actions/jobs/{int(j['id'])}/logs", token, api_open)
            with resp as r:
                data = _read_capped(r)
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                raise RuntimeError("logs-status")
            loc = e.headers.get("Location", "")
            if not _log_host_ok(loc):
                raise RuntimeError("logs-host")
            with plain_open(urllib.request.Request(loc), timeout=120) as r2:
                data = _read_capped(r2)
        texts.append(data.decode("utf-8", errors="replace"))
    return texts


def guard_texts(texts):
    hits = 0
    for t in texts:
        hits += sum(guard.scan_text(t).values())
    return hits


# ───── الملخّص من الأرقام وحدها ─────
def summary_md(kind, state, reason, legs):
    icon = {"success": "✅", "failure": "⛔", "error": "⚠️"}[state]
    lines = [f"### 🔒 private-ci · {kind}", "",
             f"**الحكم:** {icon} {status_back.WORDS[state]}" + (f" · {status_back.REASONS[reason]}"
                                                                 if status_back.REASONS[reason] else ""), "",
             "| الشوط | الاختبارات | الإخفاقات | الأعطاب | المتخطّاة | أطوارٌ فشلت | الحالة |",
             "|---|---|---|---|---|---|---|"]
    for leg in legs:
        if leg["schema_ok"]:
            c = leg["counts"]
            lines.append(f"| {leg['id']} | {c['tests']} | {c['failures']} | {c['errors']} | {c['skipped']} | "
                         f"{c['failed_phases']} من {c['phases']} | {'✅' if c['ok'] else '⛔'} |")
        else:
            lines.append(f"| {leg['id']} | — | — | — | — | — | {'غائب' if not leg['present'] else 'مرفوض'} |")
    lines += ["", "السجلُّ الكامل مشفَّرٌ في R2 الخاصّ؛ ويُقرأ من المستودع الخاصّ بسير ci-log-fetch (رقمُ هذه التشغيلة)."]
    return "\n".join(lines) + "\n"


BLOCKED_SUMMARY = "### 🔒 private-ci\n\n⛔ اشتباه: حُجب الملخّص، ويُحذف سجلُّ هذه التشغيلة.\n"
PLAN_SUMMARY = "### 🔒 private-ci\n\n⚠️ تعذّر: الطلبُ مرفوضٌ أو العدّاءُ غيرُ مطابق — لم يُبنَ شيء.\n"


# ───── التشغيل ─────
def run(env, kinds=None, fetch_logs=fetch_build_logs, uploader=upload_r2.upload_verified, sender=status_back.send):
    kinds = kinds or counts_schema.load_kinds()
    kind, sha = env.get("PCI_KIND", ""), env.get("PCI_SHA", "")
    plan_ok = env.get("PCI_PLAN_OK", "") == "true"
    repo, run_id, attempt = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_RUN_ID", "0"), env.get(
        "GITHUB_RUN_ATTEMPT", "1")
    kind_ok = kind in kinds
    sha_ok = bool(SHA_RE.fullmatch(sha or ""))
    if not (kind_ok and sha_ok):
        plan_ok = False
    dry = kind == "dry-run"
    legs = read_legs(env.get("PCI_IN", ""), kind, sha, kinds) if plan_ok else []

    # الحارس على سجلّات build العلنيّة
    guard_state = "unavailable"
    if plan_ok:
        try:
            texts = fetch_logs(repo, run_id, attempt, env.get("GH_TOKEN", ""), len(kinds[kind]["legs"]))
            guard_state = "suspicious" if guard_texts(texts) else "clean"
        except Exception as e:  # noqa: BLE001
            print(f"الحارس: ⚠ تعذّر فحصُ سجلّات build ({type(e).__name__})")

    # الرفع (كلُّ سجلٍّ مشفَّرٍ صالح، ولو فشلت اختباراتُه — فهو ما يُقرأ لمعرفة السبب)
    upload_state = "skipped"
    valid = [leg for leg in legs if leg["schema_ok"]]
    if plan_ok and valid:
        absent = not env.get("PCI_R2_JSON", "")
        try:
            creds = upload_r2.load_creds("PCI_R2_JSON", env)
        except upload_r2.UploadError as e:
            creds = None
            print(f"الرفع: ⛔ بيانةٌ مرفوضة ({e})")
        if creds is None and dry and absent:
            # D20-ج: التجربةُ الجافّة (المصدرُ عامّ) بلا أيّ سرّ — يُعدّ الرفعُ skipped ويقبله الحكمُ لهذا النوع وحده
            upload_state = "skipped"
            print("الرفع: ⚠ تجربةٌ جافّةٌ بلا بيانة R2 — متخطّى (لا يُقبل إلا لهذا النوع)")
        elif creds is None:
            upload_state = "failed"
            print("الرفع: ⛔ لا بيانةَ R2 مقبولة")
        else:
            upload_state = "verified"
            for leg in valid:
                try:
                    key = upload_r2.object_key("dryrun" if dry else "private", run_id, attempt, kind, leg["id"])
                    uploader(creds, key, leg["log"])
                except Exception as e:  # noqa: BLE001
                    upload_state = "failed"
                    print(f"الرفع: ⛔ {leg['id']} ({type(e).__name__})")
            if upload_state == "verified":
                print(f"الرفع: ✅ {len(valid)} سجلّاً مشفَّراً · محقَّقٌ بالطول وMD5")

    state, reason = compose(plan_ok, legs, guard_state, upload_state, dry=dry)

    # الملخّص: من الأرقام وحدها، ويمرّ على الحارس قبل نشره
    md = summary_md(kind, state, reason, legs) if plan_ok else PLAN_SUMMARY
    if sum(guard.scan_text(md).values()):
        md, state, reason, guard_state = BLOCKED_SUMMARY, "failure", "guard", "suspicious"
    t = totals(legs)
    for leg in legs:
        print(f"الشوط {leg['id']}: {'صالح' if leg['schema_ok'] else 'غيرُ صالح'}")
    print(f"الحكم: {state} · السبب {reason} · الحارس {guard_state} · الرفع {upload_state} · "
          f"{t['tests']} اختباراً · إخفاق {t['failures']} · عطب {t['errors']}")

    status_code = None
    if not dry and kind_ok and sha_ok:
        run_url = f"https://github.com/{repo}/actions/runs/{int(run_id)}"
        try:
            payload = status_back.build_payload(state, kind, reason, t if plan_ok else None, run_url)
            status_code = sender(sha, payload, env.get("PCI_STATUS_TOKEN", ""))
        except ValueError:
            status_code = 0
        print(f"الحالة: {'✅' if status_code == 201 else '⛔'} public-ci/{kind} = {state} · HTTP {status_code}")
    elif dry:
        print(f"الحالة (تجربة — لم تُرسل): public-ci/dry-run = {state}")
    return {"state": state, "reason": reason, "guard": guard_state, "upload": upload_state, "summary": md,
            "status_code": status_code, "suspicious": guard_state == "suspicious"}


def main():
    env = dict(os.environ)
    try:
        res = run(env)
    except Exception as e:  # noqa: BLE001 — أيُّ عطبٍ غيرِ متوقَّع ⇒ «تعذّر» يُكتب ما أمكن
        print(f"النشر: ⛔ عطبٌ داخليّ ({type(e).__name__})")
        res = {"state": "error", "reason": "internal", "summary": BLOCKED_SUMMARY, "status_code": None,
               "suspicious": False}
        kind, sha = env.get("PCI_KIND", ""), env.get("PCI_SHA", "")
        if kind in status_back.KINDS and SHA_RE.fullmatch(sha or ""):
            try:
                payload = status_back.build_payload(
                    "error", kind, "internal", None,
                    f"https://github.com/{env.get('GITHUB_REPOSITORY', '')}/actions/runs/{int(env.get('GITHUB_RUN_ID', '0'))}")
                res["status_code"] = status_back.send(sha, payload, env.get("PCI_STATUS_TOKEN", ""))
            except Exception:  # noqa: BLE001
                res["status_code"] = 0
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(res["summary"])
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"suspicious={'true' if res.get('suspicious') else 'false'}\nstate={res['state']}\n")
    if res.get("status_code") not in (None, 201):
        return 3
    return 0 if res["state"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
