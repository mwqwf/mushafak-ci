#!/usr/bin/env python3
"""☁️ رفعُ السجلّ المشفَّر إلى دلو R2 المخصّص لسجلّات CI — بمكتبة بايثون القياسيّة وحدها (توقيعُ SigV4 يدويّ).

يستعمله publish.py في وظيفة publish (الموثوقة) وحدها؛ ولا يُستورد في build.
لماذا بلا boto3: كلُّ حزمةٍ تُثبَّت من PyPI في شوطٍ يحمل بيانةَ R2 بابُ سلسلةِ توريد؛ والتوقيعُ ستّون سطراً.

⛔ D3: قائمةٌ مسموحةٌ للدلو (ALLOWED_BUCKETS = mushafak-ci-logs وحده) ولنقطة النهاية (*.r2.cloudflarestorage.com)
   وللبادئة (logs/ للتشغيل الحقيقيّ · dryrun/ للتجربة). **لا مسبارَ r2.dev**: سرّيّةُ المحتوى بالتشفير (age) لا بعموميّة
   الدلو، وخصوصيّةُ الدلو نفسِه تُثبَت مرّةً من إعداداته (خطوةُ المالك) لا بطلبٍ يردّ 404 من نطاقٍ آخر.
⛔ «الرفعُ محقَّق» = PUT ناجح **و**HEAD موقَّعٌ بعده يعيد الطولَ نفسَه وETag = MD5 المحتوى. وإلا فالرفعُ غيرُ محقَّق.
⛔ لا يطبع بيانةً ولا رابطاً ولا جسمَ ردّ.
"""
import datetime
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_BUCKETS = frozenset({"mushafak-ci-logs"})
ENDPOINT_SUFFIX = ".r2.cloudflarestorage.com"
PREFIXES = {"private": "logs/", "dryrun": "dryrun/"}
KEY_PART_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
MAX_OBJECT = 64 * 1024 * 1024


class UploadError(Exception):
    """رمزُ سببٍ ثابت."""


# ───── SigV4 (S3) — مطابقٌ لمثال وثائق AWS للرابط الموقَّع (يُختبر في test_upload_r2.py) ─────
def _h(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret, date, region, service):
    k = _h(("AWS4" + secret).encode("utf-8"), date)
    k = _h(k, region)
    k = _h(k, service)
    return _h(k, "aws4_request")


def _uri_encode(s, keep_slash):
    return urllib.parse.quote(s, safe="/~" if keep_slash else "~")


def presign_get(host, path, access_key, secret, now, expires, region="auto", service="s3"):
    """رابطٌ موقَّعٌ لـGET — لا يستعمله السيرُ؛ وُجد ليُختبر التوقيعُ بمتّجه AWS الموثَّق."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    scope = f"{date}/{region}/{service}/aws4_request"
    q = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(int(expires)),
        "X-Amz-SignedHeaders": "host",
    }
    cq = "&".join(f"{_uri_encode(k, False)}={_uri_encode(v, False)}" for k, v in sorted(q.items()))
    canonical = "\n".join(["GET", _uri_encode(path, True), cq, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    sig = hmac.new(_signing_key(secret, date, region, service), sts.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}{_uri_encode(path, True)}?{cq}&X-Amz-Signature={sig}"


def signed_request(method, host, path, access_key, secret, now, body=b"", content_type=None,
                   region="auto", service="s3"):
    """يُعيد (url, headers) لطلبٍ موقَّعٍ في الترويسة."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    if content_type:
        headers["content-type"] = content_type
    signed = ";".join(sorted(headers))
    canon_headers = "".join(f"{k}:{headers[k].strip()}\n" for k in sorted(headers))
    canonical = "\n".join([method, _uri_encode(path, True), "", canon_headers, signed, payload_hash])
    scope = f"{date}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    sig = hmac.new(_signing_key(secret, date, region, service), sts.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                                f"SignedHeaders={signed}, Signature={sig}")
    headers.pop("host")
    return f"https://{host}{_uri_encode(path, True)}", headers


# ───── السياسة ─────
def load_creds(env_name, environ=None):
    """البيانةُ من متغيّر بيئة (JSON). None إن غابت؛ UploadError إن خالفت القائمة المسموحة."""
    raw = (environ if environ is not None else os.environ).get(env_name, "")
    if not raw:
        return None
    try:
        c = json.loads(raw)
    except ValueError:
        raise UploadError("creds-json")
    need = ("endpoint", "accessKeyId", "secretAccessKey", "bucket")
    if not isinstance(c, dict) or not all(isinstance(c.get(k), str) and c.get(k) for k in need):
        raise UploadError("creds-shape")
    u = urllib.parse.urlparse(c["endpoint"])
    host = u.netloc if u.scheme == "https" else ""
    if not host or not re.fullmatch(r"[a-z0-9]{32}" + re.escape(ENDPOINT_SUFFIX), host):
        raise UploadError("endpoint")
    check_bucket(c["bucket"])
    return {"host": host, "key": c["accessKeyId"], "secret": c["secretAccessKey"], "bucket": c["bucket"]}


def check_bucket(bucket):
    if bucket not in ALLOWED_BUCKETS:
        raise UploadError("bucket")


def object_key(mode, run_id, attempt, kind, leg):
    """logs/<run_id>/<attempt>/<kind>/<leg>.log.txt.gz.age — كلُّ جزءٍ بشكلٍ مغلق (لا حقنَ مسار)."""
    if mode not in PREFIXES:
        raise UploadError("mode")
    if not str(run_id).isdigit() or not str(attempt).isdigit():
        raise UploadError("run-id")
    if not KEY_PART_RE.fullmatch(kind or "") or not KEY_PART_RE.fullmatch(leg or ""):
        raise UploadError("key-part")
    return f"{PREFIXES[mode]}{int(run_id)}/{int(attempt)}/{kind}/{leg}.log.txt.gz.age"


# ───── الشبكة ─────
def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def put_object(creds, key, body, content_type="application/octet-stream", opener=urllib.request.urlopen):
    url, headers = signed_request("PUT", creds["host"], f"/{creds['bucket']}/{key}", creds["key"], creds["secret"],
                                  _now(), body, content_type)
    req = urllib.request.Request(url, data=body, method="PUT", headers=headers)
    with opener(req, timeout=120) as r:
        if r.status not in (200, 201):
            raise UploadError("put-status")


def head_object(creds, key, opener=urllib.request.urlopen):
    """(الطول، ETag بلا علامات تنصيص) أو UploadError."""
    url, headers = signed_request("HEAD", creds["host"], f"/{creds['bucket']}/{key}", creds["key"], creds["secret"],
                                  _now())
    req = urllib.request.Request(url, method="HEAD", headers=headers)
    try:
        with opener(req, timeout=60) as r:
            if r.status != 200:
                raise UploadError("head-status")
            length = r.headers.get("Content-Length", "")
            etag = (r.headers.get("ETag", "") or "").strip('"')
    except urllib.error.HTTPError:
        raise UploadError("head-status")
    if not str(length).isdigit():
        raise UploadError("head-length")
    return int(length), etag


def upload_verified(creds, key, body, opener=urllib.request.urlopen):
    """PUT ثمّ HEAD: True فقط إن طابق الطولُ وETag (MD5) ما رُفع. وإلا UploadError."""
    if not isinstance(body, (bytes, bytearray)) or len(body) == 0 or len(body) > MAX_OBJECT:
        raise UploadError("body-size")
    put_object(creds, key, bytes(body), opener=opener)
    length, etag = head_object(creds, key, opener=opener)
    if length != len(body) or etag != hashlib.md5(bytes(body)).hexdigest():  # noqa: S324 — ETag S3 للرفع الواحد
        raise UploadError("verify-mismatch")
    return True
