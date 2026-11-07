#!/usr/bin/env python3
"""↩️ الحالةُ الواحدة النهائيّة على الإيداع في المستودع الخاص — برمزٍ دقيق النطاق (Commit statuses: write على QuranRafiq وحده).

يستعمله publish.py في وظيفة publish وحدها (D4)؛ ولا حالةَ وسيطةَ من هذا المستودع («قيد الانتظار» يكتبها المرسِلُ الخاصّ).
⛔ ما يُرسل مقيَّدٌ هنا لا في السير: سياقٌ public-ci/<نوعٌ من القائمة>، وحالةٌ من ثلاث نهائيّة، ووصفٌ يُبنى من أعدادٍ
   ورموزِ سببٍ ثابتة فقط (≤140)، ورابطٌ واحدٌ مسموح: صفحةُ هذه التشغيلة في mwqwf/mushafak-ci.
⛔ ولا يُطبع الرمزُ ولا جسمُ الردّ: المطبوعُ السياقُ والحالةُ ورمزُ HTTP فقط.
"""
import json
import re
import urllib.error
import urllib.request

REPO = "mwqwf/QuranRafiq"
RUNNER_REPO = "mwqwf/mushafak-ci"
FINAL_STATES = ("success", "failure", "error")
KINDS = ("app-test", "engine-test", "tasmi-model-emulator-smoke", "ui-shots-emu", "hudhud-feature-test",
         "device-matrix", "ads-emulator-smoke")
CONTEXT_RE = re.compile(r"public-ci/(?:" + "|".join(re.escape(k) for k in KINDS) + r")")
SHA_RE = re.compile(r"[0-9a-f]{40}")
RUN_URL_RE = re.compile(r"https://github\.com/mwqwf/mushafak-ci/actions/runs/\d+(?:/attempts/\d+)?")
WORDS = {"success": "نجح", "failure": "فشل", "error": "تعذّر"}
# رموزُ السبب الثابتة (publish.compose) ⇒ نصٌّ ثابت؛ ولا يدخل الوصفَ نصٌّ غيرُها.
REASONS = {
    "ok": "",
    "tests": "اختباراتٌ فشلت",
    "guard": "اشتباهٌ في السجلّ العلنيّ",
    "schema": "مخطّطُ الأعداد مرفوض",
    "artifact": "ناتجُ الشوط ناقص",
    "guard-unavailable": "تعذّر فحصُ السجلّ العلنيّ",
    "upload": "تعذّر تحقّقُ رفع السجلّ",
    "clone": "تعذّر الاستنساخ",
    "isolation": "عزلُ البناء على العدّاء لم يثبت",
    "plan": "الطلبُ مرفوضٌ أو العدّاءُ غيرُ مطابق",
    "internal": "عطبٌ داخليّ",
}


def build_description(state, reason, totals, excluded=0):
    """excluded: عددُ الدوالّ المستثناةِ بالتصميم (تقرأ مساراً محرَّماً لا يُستنسخ) — رقمٌ فقط، يُذكر إن كان موجباً."""
    if state not in FINAL_STATES or reason not in REASONS:
        raise ValueError("حالةٌ أو سببٌ غيرُ مسموح")
    d = WORDS[state]
    if totals:
        t, f, e = (int(totals.get(k, 0)) for k in ("tests", "failures", "errors"))
        d += f" · {t} اختباراً · إخفاق {f} · عطب {e}"
        x = int(excluded or 0)
        if x > 0:
            d += f" · {x} مستثناةً بالتصميم"
    if REASONS[reason]:
        d += " · " + REASONS[reason]
    return d[:140]


def build_payload(state, kind, reason, totals, run_url, excluded=0):
    if state not in FINAL_STATES:
        raise ValueError("حالةٌ غيرُ مسموحة")
    context = f"public-ci/{kind}"
    if not CONTEXT_RE.fullmatch(context):
        raise ValueError("سياقٌ غيرُ مسموح")
    if not RUN_URL_RE.fullmatch(run_url or ""):
        raise ValueError("رابطٌ غيرُ مسموح")
    return {"state": state, "context": context, "description": build_description(state, reason, totals, excluded),
            "target_url": run_url}


def send(sha, payload, token, opener=urllib.request.urlopen):
    """رمزُ HTTP (0 عند تعذّر الاتصال)."""
    if not SHA_RE.fullmatch(sha or ""):
        raise ValueError("sha غيرُ صالح")
    if not token:
        raise ValueError("لا رمز")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/statuses/{sha}",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0
