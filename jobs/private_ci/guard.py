#!/usr/bin/env python3
"""🛡️ حارسُ السجلّ العلنيّ لـprivate-ci (mwqwf/mushafak-ci · v2).

يستعمله publish.py على سجلّات وظائف build العلنيّة **قبل** الحالة النهائيّة (فاشتباهُه يُسقط الحالة)، وعلى الملخّص
المولَّد من الأرقام قبل نشره؛ ويستعمله الكنّاسُ على سجلّ التشغيلة كلّه بعد اكتمالها (طبقةٌ ثانية لا أساس).

يفحص ملفّاً أو أكثر (أو مجلّداً بكلّ ما فيه) عن ستّ فئاتٍ لا يجوز أن تظهر علناً:
  kotlin  — سطرٌ يشبه شيفرةَ Kotlin/Java أو إطارَ مكدّسٍ منها أو رسالةَ ترجمة (e: file:///… · Foo.kt:12)
  gradle  — سطرٌ من مخرجات Gradle (> Task :app:… · What went wrong · BUILD FAILED in …)
  path    — مسارٌ داخليٌّ من المستودع الخاص (app/src/main/… · /QuranRafiq/… · cloud/worker/…)
  package — اسمُ الحزمة الداخليّ (com.ali.rafiq بأيّ فاصل)
  secret  — مفتاحٌ أو رمز (‏PEM · ghp_ · github_pat_ · AKIA · sk-ant- · AIza · Bearer · secretAccessKey)
  signed  — رابطٌ موقَّع (X-Amz-Signature · sig= · token= طويل)

⛔ لا يطبع أبداً سطراً ولا مقطعاً ممّا وجد — **نجح/فشل والعدُّ بكلّ فئة فقط**، فلا يصير الحارسُ نفسُه قناةَ تسريب.
رموزُ الخروج: 0 نظيف · 3 اشتباه (⇒ يُحجب الملخّص ويُطلب حذفُ سجلّ التشغيلة) · 2 خطأُ استعمال أو ملفٌّ غائب
(‏ويُعامَل 2 معاملةَ الاشتباه في السير: الحارسُ الذي لا يقرأ لا يبرّئ).

الوضع --report: يعدّ ولا يفشل (لسجلّ البناء الخاصّ قبل تشفيره — يحوي الشيفرةَ عمداً).
الوضع --emit-plant: يطبع سطراً مزروعاً يشبه شيفرةً ومساراً داخليّاً (تجربةُ plant_leak الذاتيّة وحدها)؛ يُبنى بالتركيب
فلا يحوي هذا الملفُّ السطرَ حرفيّاً.
"""
import argparse
import json
import os
import re
import sys
import unicodedata

EXIT_CLEAN, EXIT_USAGE, EXIT_SUSPICIOUS = 0, 2, 3
MAX_BYTES = 64 * 1024 * 1024
# لا تُبنى السلسلةُ حرفيّاً في الشيفرة كي لا يطابق هذا الملفُّ نفسَه إن طُبع يوماً.
_PKG = r"com[\s./\\_-]{1,3}ali[\s./\\_-]{1,3}rafiq"

PATTERNS = {
    "kotlin": [
        r"^\s*(?:package|import)\s+[a-z][\w]*(?:\.[\w*]+)+\s*$",
        r"\bfun\s+(?:<[^>]*>\s*)?[A-Za-z_][\w.]*\s*\(",
        r"\b(?:val|var)\s+[A-Za-z_]\w*\s*(?::\s*[A-Z(]|=[^=])",
        r"\b(?:data\s+class|sealed\s+class|enum\s+class|object|interface|class)\s+[A-Z]\w*\s*[({:<]",
        r"\b(?:override|private|internal|suspend|inline)\s+(?:fun|val|var)\b",
        r"@(?:Composable|Test|Before|After|Preview|JvmStatic|Serializable)\b",
        r"\b[ewi]:\s+file:///",
        r"\b\w+\.(?:kt|kts|java)\s*[:(]\s*\d+",
        r"\(\w+\.(?:kt|java):\d+\)",
        r"^\s*at\s+[a-z][\w$]*(?:\.[\w$<>]+){2,}\(",
        r"\bUnresolved reference\b|\bType mismatch\b|\bexpecting\s+'",
    ],
    "gradle": [
        r"^\s*>\s*Task\s+:",
        r"\bExecution failed for task\b",
        r"^\s*\*\s*What went wrong:",
        r"\bBUILD (?:SUCCESSFUL|FAILED) in \d",
        r"\bCompilation (?:error|failed)\b",
        r"^\s*>\s*Configure project\s+:",
        r"\btests? completed, \d+ failed\b",
    ],
    "path": [
        r"/QuranRafiq(?:/|\b)",
        r"\b(?:app|core|engine|feature|baselineprofile)/(?:[\w.-]+/)*src/(?:main|test|androidTest|debug|release)\b",
        r"\bcloud/(?:worker|functions|api)\b",
        r"\bsrc/main/(?:kotlin|java|cpp|res|assets)/",
        r"\btools/(?:store|cloud|cloud_gcp|ops)/\w",
        r"\bsecure/(?:keystore|r2_|upload)",
    ],
    "package": [_PKG],
    "secret": [
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\bb3BlbnNzaC1rZXktdjE",
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}",
        r"\bgithub_pat_[A-Za-z0-9_]{20,}",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bsk-ant-[A-Za-z0-9_\-]{10,}",
        r"\bAIza[0-9A-Za-z_\-]{30,}",
        r"\bBearer\s+[A-Za-z0-9._~+/=\-]{16,}",
        r"(?i)\b(?:secretAccessKey|aws_secret_access_key|accessKeyId)\b\s*[\"']?\s*[:=]",
    ],
    "signed": [
        r"(?i)X-Amz-(?:Signature|Credential|Security-Token)=",
        r"(?i)[?&](?:sig|signature|token|access_token|se)=[A-Za-z0-9%/+=_.\-]{16,}",
        r"(?i)\br2\.cloudflarestorage\.com/\S*\?",
    ],
}
COMPILED = {cat: [re.compile(p, re.M) for p in pats] for cat, pats in PATTERNS.items()}
# شكلُ السطر بعد التطبيع: تُزال محارفُ التحكّم وألوانُ ANSI وطوابعُ GitHub الزمنيّة قبل المطابقة،
# فلا يُخفي «\x1b[31m» أو «2026-11-06T00:00:00.0000000Z » سطراً ممنوعاً عن الحارس.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z\s?")
_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)


def normalize(line):
    line = _ANSI.sub("", line).translate(_ZW)
    line = _TS.sub("", line)
    return unicodedata.normalize("NFKC", line)


def scan_text(text):
    counts = {cat: 0 for cat in COMPILED}
    for raw in text.splitlines():
        line = normalize(raw)
        if not line.strip():
            continue
        for cat, rxs in COMPILED.items():
            if any(rx.search(line) for rx in rxs):
                counts[cat] += 1
    return counts


def iter_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for name in sorted(files):
                    yield os.path.join(root, name)
        else:
            yield p


def scan_paths(paths):
    """تُعيد (العدّ بالفئة، عدد الأسطر، عدد الملفات) أو ترفع FileNotFoundError."""
    total = {cat: 0 for cat in COMPILED}
    lines = files = 0
    seen = False
    for path in iter_files(paths):
        seen = True
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or size > MAX_BYTES:
            # ملفٌّ أكبرُ من أن يُقرأ كلُّه لا يُبرَّأ — يُحسب اشتباهاً لا تخطّياً.
            total["path"] += 1
        text = data.decode("utf-8", errors="replace")
        c = scan_text(text)
        for k, v in c.items():
            total[k] += v
        lines += text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        files += 1
    if not seen:
        raise FileNotFoundError("لا ملف")
    return total, lines, files


def planted_line():
    """سطرٌ يجب أن يُمسكه الحارس (kotlin + path) — للتجربة الذاتيّة وحدها."""
    return "e: file:///home/runner/work/_temp/" + "Quran" + "Rafiq" + "/app/src/main/kotlin/x/Planted.kt:1:1 Unresolved reference"


def main(argv=None):
    ap = argparse.ArgumentParser(description="حارس السجلّ العلنيّ (يطبع العدّ فقط)")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--emit-plant", action="store_true", help="اطبع السطرَ المزروع (تجربة plant_leak)")
    ap.add_argument("--report", action="store_true", help="عُدّ ولا تفشل (لسجلّ R2 الخاص)")
    ap.add_argument("--json", help="اكتب العدَّ JSON في هذا الملف")
    ap.add_argument("--label", default="")
    a = ap.parse_args(argv)
    if a.emit_plant:
        print(planted_line())
        return EXIT_CLEAN
    if not a.paths:
        print("الحارس: ⛔ لا ملفَّ يُفحص — يُعامَل اشتباهاً")
        return EXIT_USAGE
    label = f"{a.label} · " if a.label else ""
    try:
        counts, lines, files = scan_paths(a.paths)
    except (FileNotFoundError, OSError):
        print(f"الحارس: {label}⛔ تعذّرت القراءة — يُعامَل اشتباهاً")
        return EXIT_USAGE
    hits = sum(counts.values())
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"hits": hits, "lines": lines, "files": files, "by_category": counts}, f)
    detail = " · ".join(f"{k}={v}" for k, v in counts.items() if v)
    if hits == 0:
        print(f"الحارس: {label}✅ نظيف · {lines} سطراً في {files} ملفّ")
        return EXIT_CLEAN
    if a.report:
        print(f"الحارس (عدٌّ للسجلّ الخاص): {label}{hits} سطراً حسّاساً — {detail}")
        return EXIT_CLEAN
    print(f"الحارس: {label}⛔ اشتباه · {hits} سطراً — {detail}")
    return EXIT_SUSPICIOUS


if __name__ == "__main__":
    sys.exit(main())
