#!/usr/bin/env python3
"""🧾 فحصُ مجلّد الناتج (D13) — **بوّابةُ ما يُرفع**: يستعمله المشرفُ (finalize) قبل رفع الـArtifact، ويستعمله publish
عند قراءة ما نزل، فيصيران حَكَمَين بالقاعدة نفسِها.

المجلّدُ يحوي **بالضبط** ملفَّين: counts.json وlog.txt.gz.age؛ كلاهما ملفٌّ عاديّ (lstat · لا روابط)؛
counts.json ≤ 4096 بايت ويصحّ بـcounts_schema.validate؛ والسجلُّ ≤ 64 م.ب وبنيةُ ترويسة age كاملةً:
  السطرُ الأوّل  age-encryption.org/v1
  ثمّ مقطعٌ أو أكثر:  «-> X25519 <43 base64>» يتبعه سطرُ جسمٍ «<43 base64>»
  ثمّ  «--- <43 base64>»   (سطرُ MAC)
  ثمّ حمولةٌ ثنائيّةٌ ≥ 32 بايتاً.
لا يُبرّئ ذلك أنّ السجلّ شُفِّر **لمفتاحنا** (age لا يكشف المستلِم) — لكنّ المشرفَ هو من يكتب الملفَّ المشفَّر بنفسه من سجلٍّ
لم يملكه البناءُ قطّ (D13)؛ وهذا الفحصُ يمنع الخطأَ والتلاعبَ بعد الكتابة.

⛔ لا يطبع إلا «✅/⛔» ورمزَ سببٍ ثابت.
"""
import argparse
import os
import re
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import counts_schema  # noqa: E402

EXPECTED_FILES = frozenset({"counts.json", "log.txt.gz.age"})
MAX_LOG = 64 * 1024 * 1024
MAGIC = b"age-encryption.org/v1\n"
MAX_HEADER = 8192
MAX_STANZAS = 8
MIN_PAYLOAD = 32
_B64_43 = r"[A-Za-z0-9+/]{43}"
STANZA_RX = re.compile(rb"-> X25519 " + _B64_43.encode())
BODY_RX = re.compile(_B64_43.encode())
MAC_RX = re.compile(rb"--- " + _B64_43.encode())


class ArtifactError(ValueError):
    """رمزُ سببٍ ثابت."""


def age_header_ok(data):
    """بنيةُ ترويسة age كاملةً (لا تفكّ شيئاً)."""
    if not data.startswith(MAGIC):
        return False
    pos = len(MAGIC)
    stanzas = 0
    while True:
        end = data.find(b"\n", pos, pos + 200)
        if end < 0 or end > MAX_HEADER:
            return False
        line = data[pos:end]
        pos = end + 1
        if MAC_RX.fullmatch(line):
            break
        if not STANZA_RX.fullmatch(line):
            return False
        end = data.find(b"\n", pos, pos + 200)
        if end < 0 or not BODY_RX.fullmatch(data[pos:end]):
            return False
        pos = end + 1
        stanzas += 1
        if stanzas > MAX_STANZAS:
            return False
    return stanzas >= 1 and len(data) - pos >= MIN_PAYLOAD


def _regular_bytes(path, limit, reason):
    try:
        st = os.lstat(path)
    except OSError:
        raise ArtifactError("files")
    if not stat.S_ISREG(st.st_mode):
        raise ArtifactError("files")
    if st.st_size > limit:
        raise ArtifactError(reason)
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ArtifactError(reason)
    return data


def load_checked(dir_path, kind, leg, sha, kinds):
    """يُعيد (counts، بايتاتِ السجلّ) أو يرفع ArtifactError برمزٍ ثابت."""
    try:
        st = os.lstat(dir_path)
    except OSError:
        raise ArtifactError("dir")
    if not stat.S_ISDIR(st.st_mode):
        raise ArtifactError("dir")
    try:
        names = set(os.listdir(dir_path))
    except OSError:
        raise ArtifactError("dir")
    if names != EXPECTED_FILES:
        raise ArtifactError("files")
    raw = _regular_bytes(os.path.join(dir_path, "counts.json"), counts_schema.MAX_BYTES, "counts-size")
    log = _regular_bytes(os.path.join(dir_path, "log.txt.gz.age"), MAX_LOG, "log-size")
    try:
        counts = counts_schema.validate(raw, kind, leg, sha, kinds)
    except counts_schema.SchemaError as e:
        raise ArtifactError(f"schema:{e}")
    if not age_header_ok(log):
        raise ArtifactError("age-header")
    return counts, log


def check(dir_path, kind, leg, sha, kinds):
    """(ok، السبب) — السببُ «ok» عند القبول."""
    try:
        load_checked(dir_path, kind, leg, sha, kinds)
    except ArtifactError as e:
        return False, str(e)
    return True, "ok"


def main(argv=None):
    ap = argparse.ArgumentParser(description="فحص مجلّد ناتج private-ci")
    ap.add_argument("dir")
    ap.add_argument("--kind", required=True)
    ap.add_argument("--leg", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--kinds", default="")
    a = ap.parse_args(argv)
    ok, why = check(a.dir, a.kind, a.leg, a.sha, counts_schema.load_kinds(a.kinds or None))
    print("الناتج: ✅ مقبول" if ok else f"الناتج: ⛔ مرفوض ({why})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
