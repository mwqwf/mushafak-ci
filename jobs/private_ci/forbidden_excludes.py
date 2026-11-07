#!/usr/bin/env python3
"""🚧 حارسُ الاستثناءات بالتصميم — يُجيز استثناءَ دالّةِ اختبارٍ من app-test على العدّاء العامّ **فقط** إن ثبت أنّها تقرأ مساراً
محرَّماً بالتصميم (FORBIDDEN_RX في clone.sh) لا يُستنسخ أبداً إلى هذا العدّاء.

الأوامر (مكتبةٌ قياسيّةٌ وحدها — يُشغَّل بـ`python3 -I -S`):
  verify — يتحقّق من كلّ عنصرٍ للنوع في القائمة ضدّ الاستنساخ (--src) ويكتب أنماطَ الاستثناء (سطراً لكلّ دالّة «الصنف.الدالّة»)
           في --out، ويطبع المستثنى (يذهب إلى السجلّ الخاصّ المشفَّر). أيُّ عنصرٍ لا يثبت ⇒ خروجٌ 1 بسببٍ مكتوب ولا يُكتب
           ملفُّ أنماطٍ أصلاً (فلا يجري Gradle بقائمةٍ ناقصةٍ التحقّق).
  count  — عددُ عناصر النوع في القائمة (يستعمله publish لوصف الحالة؛ ولا يقرأ الاستنساخ).
  check  — (المشرف) ملفُّ الأنماط يطابق **بايتاً ببايت** الشكلَ القانونيّ لأنماط النوع في القائمة الموثوقة، ويطبع عددَها.
  sealed — (المشرف في finalize) الملفُّ المثبَّت في مدخل التحكّم نسخةُ المرجع بعينها، عاديٌّ لا رابط، و(بـ--chain-uid)
           كلُّ مكوّنٍ من الجذر حتى الملفّ يملكه ذلك المستخدم بلا كتابةٍ للمجموعة ولا للغير ولا رابطٍ رمزيّ (مرآةُ root_chain_ok).

⛔ مراجعة Codex السادسة (2026-11-07 · §3): الحارسُ لا يجري بعدُ بمستخدم البناء ولا يكتب في مجلّده. المشرفُ يشغّله بحسابه على الشجرة
   الأصليّة **قبل أن يملكها البناء** (do_clone)، ثمّ يثبّت الأنماطَ في مدخل التحكّم الذي يملكه root (طورُ forbidden_excludes الموثوق)،
   فلا تغيّرها عمليّةٌ باقيةٌ ولا wrapper المستودع؛ ويطابقها سكربتُ Gradle بالقائمة الموثوقة، ويختمها المشرفُ بعد زوال البناء.

قواعدُ الحارس لكلّ عنصر:
  ١. الصنفُ موجودٌ بالضبط مرّةً في الاستنساخ: ملفٌّ باسمه (.kt/.java) تحت src/test في مجلّداتٍ تطابق حزمتَه، فيه سطرُ package وclass.
  ٢. الدالّةُ موجودةٌ في ذلك الملفّ (fun `الاسم` أو fun الاسم()).
  ٣. المسارُ المحرَّم المعلَن يطابق FORBIDDEN_RX، **ويذكره جسمُ الدالّة المستثناة نفسِها حرفاً في نصٍّ بين علامتَي تنصيص** (معادلٌ له
     أو أصلٌ لمسارٍ فيه) — لا الملفُّ ولا دالّةٌ مجاورة ولا حقلٌ على مستوى الصنف ولا تعليقٌ (`//` أو `/* */` يُنزعان من الجسم أوّلاً). الجسمُ: من سطر إعلان الدالّة حتى ما قبل
     أوّل سطرٍ تالٍ يبدأ بـ@Test أو بإعلان fun، أو نهايةِ الملفّ.
  ٤. المسارُ المعلَن **غائبٌ فعلاً** من الاستنساخ (os.path.lexists) — وإن وُجد فالاختبارُ يجب أن يجري لا أن يُستثنى.
  ⛔ لا مطابقةَ بنجمة ولا استثناءَ صنفٍ كاملاً ولا رابطاً رمزيّاً ولا ملفّاً غيرَ عاديّ؛ ولا يُغيَّر الاختبارُ نفسُه.
"""
import argparse
import json
import os
import re
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LIST = os.path.join(HERE, "forbidden_excludes.json")
# ⛔ نسخةٌ حرفيّةٌ من FORBIDDEN_RX في clone.sh (يفحص test_forbidden_excludes.py تطابقَهما حرفاً) — أيُّ تشديدٍ هناك يُنسخ هنا.
FORBIDDEN_RX = re.compile(
    r'^(cloud|secure|store|\.github|tools/store|tools/cloud[^/]*|tools/ops)(/|$)|^docs/(?!ai/app_guide/)|^ops/(?!ui_shots/request\.txt$)')
ENTRY_KEYS = {"kind", "class", "method", "forbidden_path", "reason"}
KIND_RE = re.compile(r"[a-z0-9-]{1,64}")
CLASS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+")
# اسمُ الدالّة: بلا نجمةٍ ولا علامةِ استفهامٍ ولا نقطةٍ ولا # ولا فاصلِ مسار ولا علامةِ اقتباس/أسطر (أنماطُ Gradle تحمل * فقط)
METHOD_BAD = re.compile(r"[*?.#/\\\"`\r\n\t]")
SKIP_DIRS = {".git", "build", ".gradle", "node_modules"}
MAX_FILE = 2 * 1024 * 1024
LITERAL_RX = re.compile(r'"((?:[^"\\\r\n]|\\.)*)"')


class Refusal(Exception):
    """سببٌ مكتوبٌ يُطبع في السجلّ الخاصّ (لا يحمل أسراراً: أسماءُ اختباراتٍ ومساراتٌ من القائمة)."""


def load_list(path=None):
    with open(path or DEFAULT_LIST, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("excludes"), list):
        raise Refusal("القائمةُ ليست كائناً بحقل excludes")
    seen = set()
    out = []
    for i, e in enumerate(data["excludes"]):
        if not isinstance(e, dict) or set(e) != ENTRY_KEYS or any(not isinstance(e[k], str) or not e[k] for k in ENTRY_KEYS):
            raise Refusal(f"العنصر {i}: حقولٌ ناقصةٌ أو زائدة (المطلوب {sorted(ENTRY_KEYS)})")
        if not KIND_RE.fullmatch(e["kind"]):
            raise Refusal(f"العنصر {i}: نوعٌ غيرُ صالح")
        if not CLASS_RE.fullmatch(e["class"]):
            raise Refusal(f"العنصر {i}: اسمُ الصنف يجب أن يكون كاملاً (حزمةٌ ثمّ صنف) بلا نجمة")
        if METHOD_BAD.search(e["method"]) or e["method"] != e["method"].strip():
            raise Refusal(f"العنصر {i}: اسمُ الدالّة يحمل محرفاً ممنوعاً (نجمة · نقطة · # · اقتباس · فراغٌ طرفيّ)")
        if not FORBIDDEN_RX.search(e["forbidden_path"]):
            raise Refusal(f"العنصر {i}: المسارُ المعلَن لا يطابق FORBIDDEN_RX — لا استثناءَ لمسارٍ غيرِ محرَّم")
        key = (e["kind"], e["class"], e["method"])
        if key in seen:
            raise Refusal(f"العنصر {i}: مكرَّر")
        seen.add(key)
        out.append(dict(e))
    return out


def entries_for(kind, path=None):
    return [e for e in load_list(path) if e["kind"] == kind]


def _read_regular(path):
    """Codex 6 §3: يجري بحساب المشرف على شجرةٍ غيرِ موثوقة ⇒ يُفتح بلا اتّباع رابطٍ ويُفحص الواصفُ نفسُه (لا سباقَ بين الفحص والفتح)."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > MAX_FILE:
        raise Refusal("ملفُّ الاختبار ليس ملفّاً عاديّاً مقبولاً (رابطٌ أو كبيرٌ أو أكثرُ من رابطٍ صلب)")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise Refusal("ملفُّ الاختبار ليس ملفّاً عاديّاً مقبولاً (رابطٌ أو كبيرٌ أو أكثرُ من رابطٍ صلب)")
    with os.fdopen(fd, "rb") as f:
        fst = os.fstat(f.fileno())
        if not stat.S_ISREG(fst.st_mode) or fst.st_nlink != 1 or fst.st_size > MAX_FILE:
            raise Refusal("ملفُّ الاختبار ليس ملفّاً عاديّاً مقبولاً (رابطٌ أو كبيرٌ أو أكثرُ من رابطٍ صلب)")
        return f.read(MAX_FILE + 1).decode("utf-8", errors="replace")


def find_class_file(src, fqcn):
    """مسارُ ملفّ الصنف بالضبط (مرّةً واحدة) تحت src/test، وإلا Refusal."""
    pkg, cls = fqcn.rsplit(".", 1)
    want = pkg.split(".")
    hits = []
    for dp, dirs, files in os.walk(src, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        parts = os.path.relpath(dp, src).split(os.sep)
        if "src" not in parts or len(parts) < len(want) or parts[-len(want):] != want:
            continue
        i = parts.index("src")
        if i + 1 >= len(parts) or not parts[i + 1].startswith("test"):
            continue
        for fn in files:
            if fn in (cls + ".kt", cls + ".java"):
                hits.append(os.path.join(dp, fn))
    if not hits:
        raise Refusal("صنفٌ غيرُ موجودٍ في الاستنساخ")
    if len(hits) > 1:
        raise Refusal("الصنفُ في أكثرَ من ملفّ")
    return hits[0]


def strip_comments(text):
    """ينزع تعليقاتِ `//` (حتى نهاية السطر) و`/* … */` (متداخلةً كما في كوتلن) ويحفظ ما داخل علامتَي التنصيص ومحرفَ '"'. يمرّ على المحارف
    ويعرف أنّه داخل نصّ؛ ونصٌّ مثلُ "a//b" يبقى كاملاً. (ومن قطع النزعُ نصّاً فهذا أشدّ لا أرخى.)"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith('"""', i):                       # نصٌّ خام: حتى الإغلاق
            j = text.find('"""', i + 3)
            j = n if j < 0 else j + 3
            out.append(text[i:j]); i = j
        elif c == '"':                                      # نصٌّ عاديّ بهروب
            j = i + 1
            while j < n and text[j] != '"' and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(text[i:j]); i = j
        elif c == "'":                                      # محرفٌ مفرد ('"' مثلاً)
            m = re.match(r"'(?:\\.|[^'\\\n])'", text[i:])
            if m:
                out.append(m.group(0)); i += len(m.group(0))
            else:
                out.append(c); i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j                           # يُبقي السطرَ الجديد
        elif text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth += 1; j += 2
                elif text.startswith("*/", j):
                    depth -= 1; j += 2
                else:
                    j += 1
            out.append(" " + "\n" * text.count("\n", i, j)); i = j
        else:
            out.append(c); i += 1
    return "".join(out)


def normalized_literals(text):
    out = []
    for m in LITERAL_RX.finditer(text):
        lit = m.group(1)
        while lit.startswith("../") or lit.startswith("./"):
            lit = lit[3:] if lit.startswith("../") else lit[2:]
        out.append(lit)
    return out


_BOUNDARY = re.compile(r"^\s*(@Test\b|(?:(?:private|internal|protected|public|override|open|suspend|inline)\s+)*fun\s|(?:public\s+|private\s+|protected\s+)?(?:static\s+)?void\s+\w+\s*\()")


def method_body(text, method):
    """جسمُ الدالّة: من سطر إعلانها حتى ما قبل أوّل سطرٍ تالٍ يبدأ بـ@Test أو إعلانِ fun (أو void في جافا)، أو نهاية الملفّ. None إن غابت."""
    decl = re.compile(r"\bfun\s+`" + re.escape(method) + r"`|\bfun\s+" + re.escape(method) + r"\s*\(|\bvoid\s+" + re.escape(method) + r"\s*\(")
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if decl.search(ln):
            body = [ln]
            for nxt in lines[i + 1:]:
                if _BOUNDARY.match(nxt):
                    break
                body.append(nxt)
            return "\n".join(body)
    return None


def check_entry(src, e, absent=True):
    """يرفع Refusal بسببٍ مكتوب إن لم يثبت الاستثناء."""
    fqcn, method, path = e["class"], e["method"], e["forbidden_path"]
    pkg, cls = fqcn.rsplit(".", 1)
    f = find_class_file(src, fqcn)
    text = _read_regular(f)
    if not re.search(r"(?m)^package\s+" + re.escape(pkg) + r"\s*;?\s*$", text) or \
            not re.search(r"\b(class|object)\s+" + re.escape(cls) + r"\b", text):
        raise Refusal("ملفُّ الصنف لا يعلن الحزمةَ أو الصنفَ المذكور")
    if absent and os.path.lexists(os.path.join(src, *path.split("/"))):
        raise Refusal("المسارُ المحرَّم موجودٌ في الاستنساخ — فالاختبارُ يجب أن يجري لا أن يُستثنى")
    body = method_body(text, method)
    if body is None:
        raise Refusal("دالّةٌ غيرُ موجودةٍ في ملفّ الصنف")
    ok = False
    for lit in normalized_literals(strip_comments(body)):
        if (lit == path or lit.startswith(path + "/")) and FORBIDDEN_RX.search(lit):
            ok = True
            break
    if not ok:
        raise Refusal("جسمُ الدالّة لا يذكر المسارَ المحرَّم في نصٍّ بين علامتَي تنصيص — لا استثناءَ بلا قراءةٍ فعليّةٍ لمحرَّم داخل الدالّة نفسِها")


def pattern_of(e):
    return f"{e['class']}.{e['method']}"


def verify(src, kind, entries, absent=True):
    """(الأنماط، أسطرُ التقرير). يرفع Refusal عند أوّل عنصرٍ لا يثبت (بسببه واسمِه).
    absent=False للاختبار وحده (شجرةُ الخاصّ الكاملة يوجد فيها المحرَّم أصلاً)؛ والحارسُ في CI يمرّ بـTrue دائماً."""
    mine = [e for e in entries if e["kind"] == kind]
    patterns, lines = [], []
    for e in mine:
        try:
            check_entry(src, e, absent)
        except Refusal as r:
            raise Refusal(f"{e['class']}#{e['method']}: {r}")
        patterns.append(pattern_of(e))
        lines.append(f"  ⛔ مستثنى بالتصميم: {e['class']}#{e['method']} ← {e['forbidden_path']} — {e['reason']}")
    return patterns, lines


HEADER = "# أنماطُ استثناءٍ بالتصميم — يولّدها forbidden_excludes.py ويقرؤها forbidden_excludes.init.gradle\n"
MAX_PATTERNS = 64 * 1024


def canonical(patterns):
    """الشكلُ القانونيّ الوحيد لملفّ الأنماط: الترويسةُ ثمّ نمطٌ في كلّ سطر بترتيب القائمة (يطابقه check بايتاً ببايت)."""
    return HEADER + "".join(p + "\n" for p in patterns)


def expected_patterns(kind, path=None):
    return [pattern_of(e) for e in entries_for(kind, path)]


def _read_nofollow(path, cap):
    """(البايتات، stat) لملفٍّ عاديٍّ برابطٍ صلبٍ واحد يُفتح بلا اتّباع رابط (لا سباقَ بين الفحص والفتح)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise Refusal("ملفُّ الأنماط غائبٌ أو رابطٌ رمزيّ")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > cap:
            raise Refusal("ملفُّ الأنماط ليس ملفّاً عاديّاً مقبولاً (رابطٌ أو كبيرٌ أو أكثرُ من رابطٍ صلب)")
        chunks, n = [], 0
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
            n += len(b)
            if n > cap:
                raise Refusal("ملفُّ الأنماط أكبرُ من الحدّ")
        return b"".join(chunks), st
    finally:
        os.close(fd)


def check_patterns(path, kind, listing=None):
    """عددُ الأنماط إن طابق الملفُّ الشكلَ القانونيّ لأنماط النوع في القائمة الموثوقة بايتاً ببايت، وإلا Refusal."""
    data, _ = _read_nofollow(path, MAX_PATTERNS)
    want = expected_patterns(kind, listing)
    if data != canonical(want).encode("utf-8"):
        raise Refusal("ملفُّ الأنماط لا يطابق القائمةَ الموثوقة حرفاً (عدداً أو ترتيباً أو اسماً أو زيادة)")
    return len(want)


def chain_ok(path, uid):
    """مرآةُ root_chain_ok في supervise.sh: كلُّ مكوّنٍ من الجذر حتى الملفّ ليس رابطاً، يملكه uid، بلا بِتّ كتابةٍ للمجموعة ولا للغير."""
    if not os.path.isabs(path):
        return False
    cur = "/"
    for part in [""] + [c for c in path.split("/") if c]:
        if part in (".", ".."):
            return False
        if part:
            cur = os.path.join(cur, part)
        try:
            st = os.lstat(cur)
        except OSError:
            return False
        if stat.S_ISLNK(st.st_mode) or st.st_uid != uid or st.st_mode & 0o022:
            return False
    return True


def sealed(path, ref, chain_uid=None):
    """الملفُّ المثبَّت نسخةُ المرجع بعينها (عاديٌّ برابطٍ واحد، بلا اتّباع رابط)؛ ومع chain_uid سلسلتُه كلُّها مختومة. وإلا Refusal."""
    got, _ = _read_nofollow(path, MAX_PATTERNS)
    want, _ = _read_nofollow(ref, MAX_PATTERNS)
    if got != want:
        raise Refusal("الملفُّ المثبَّت لا يطابق نسخةَ المشرف")
    if chain_uid is not None and not chain_ok(path, chain_uid):
        raise Refusal("سلسلةُ الملفّ المثبَّت فقدت ختمَها (مالكٌ أو كتابةٌ لغيره أو رابط)")


def cmd_verify(a):
    try:
        entries = load_list(a.list)
        patterns, lines = verify(a.src, a.kind, entries)
    except (Refusal, OSError, ValueError) as r:
        print(f"forbidden_excludes: ⛔ رُفض الاستثناء — {r}")
        try:
            os.remove(a.out)   # لا يبقى ملفُّ أنماطٍ قديمٌ يقرؤه Gradle بعد رفضٍ
        except OSError:
            pass
        return 1
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(canonical(patterns))
    os.replace(tmp, a.out)
    print(f"forbidden_excludes: {len(patterns)} دالّةً مستثناةً بالتصميم (النوع {a.kind})")
    for ln in lines:
        print(ln)
    return 0


def cmd_check(a):
    try:
        n = check_patterns(a.patterns, a.kind, a.list)
    except (Refusal, OSError, ValueError) as r:
        print(f"forbidden_excludes: ⛔ {r}")
        return 1
    print(n)
    return 0


def cmd_sealed(a):
    try:
        sealed(a.file, a.ref, a.chain_uid)
    except (Refusal, OSError, ValueError) as r:
        print(f"forbidden_excludes: ⛔ {r}")
        return 1
    return 0


def cmd_count(a):
    try:
        print(len(entries_for(a.kind, a.list)))
    except (Refusal, OSError, ValueError):
        print(0)
        return 1
    return 0


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--src", required=True)
    v.add_argument("--kind", required=True)
    v.add_argument("--out", required=True)
    v.add_argument("--list", default=None)
    c = sub.add_parser("count")
    c.add_argument("--kind", required=True)
    c.add_argument("--list", default=None)
    k = sub.add_parser("check")
    k.add_argument("--kind", required=True)
    k.add_argument("--patterns", required=True)
    k.add_argument("--list", default=None)
    s_ = sub.add_parser("sealed")
    s_.add_argument("--file", required=True)
    s_.add_argument("--ref", required=True)
    s_.add_argument("--chain-uid", type=int, default=None)
    a = p.parse_args(argv[1:])
    return {"verify": cmd_verify, "count": cmd_count, "check": cmd_check, "sealed": cmd_sealed}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
