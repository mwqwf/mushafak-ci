#!/usr/bin/env python3
"""🧪 الرافع: توقيعُ SigV4 بمتّجه AWS الموثَّق · القائمةُ المسموحة للدلو (mushafak-ci-logs وحده) ولنقطة النهاية ·
مفتاحُ الكائن بشكلٍ مغلق · و«الرفعُ محقَّق» لا يُقال إلا إذا طابق HEAD الطولَ وMD5."""
import datetime
import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import upload_r2 as u  # noqa: E402

HOST = "0" * 32 + ".r2.cloudflarestorage.com"


def creds_env(**kw):
    c = {"endpoint": "https://" + HOST, "accessKeyId": "a", "secretAccessKey": "b", "bucket": "mushafak-ci-logs"}
    c.update(kw)
    return {"X": json.dumps(c)}


class Resp:
    def __init__(self, status, headers=None):
        self.status, self.headers = status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SigV4Test(unittest.TestCase):
    def test_aws_documented_presign_vector(self):
        """مثالُ AWS الموثَّق للرابط الموقَّع (S3 · examplebucket/test.txt · 2013-05-24 · 86400 ث)."""
        url = u.presign_get("examplebucket.s3.amazonaws.com", "/test.txt", "AKIAIOSFODNN7EXAMPLE",
                            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                            datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc), 86400, region="us-east-1")
        self.assertTrue(url.endswith(
            "X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"), url)

    def test_signed_put_shape(self):
        now = datetime.datetime(2026, 11, 6, tzinfo=datetime.timezone.utc)
        url, h = u.signed_request("PUT", HOST, "/mushafak-ci-logs/logs/1/1/app-test/main.log.txt.gz.age", "K", "S",
                                  now, b"hi", "application/octet-stream")
        self.assertEqual(url, f"https://{HOST}/mushafak-ci-logs/logs/1/1/app-test/main.log.txt.gz.age")
        self.assertTrue(h["authorization"].startswith("AWS4-HMAC-SHA256 Credential=K/20261106/auto/s3/aws4_request"))
        self.assertIn("SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date", h["authorization"])


class PolicyTest(unittest.TestCase):
    def test_bucket_allowlist(self):
        self.assertEqual(u.load_creds("X", creds_env())["bucket"], "mushafak-ci-logs")
        for b in ("mushafak-private", "rafiq-media", "mushafak-ci-logs-x", ""):
            with self.subTest(b=b), self.assertRaises(u.UploadError):
                u.load_creds("X", creds_env(bucket=b))

    def test_endpoint_must_be_r2_https(self):
        for ep in ("https://evil.example.com", "http://" + HOST, "https://x.r2.cloudflarestorage.com.evil.com",
                   "https://" + "A" * 32 + ".r2.cloudflarestorage.com", HOST, "https://pub-1.r2.dev"):
            with self.subTest(ep=ep), self.assertRaises(u.UploadError):
                u.load_creds("X", creds_env(endpoint=ep))

    def test_missing_and_malformed_creds(self):
        self.assertIsNone(u.load_creds("X", {}))
        for raw in ("{", "[]", json.dumps({"endpoint": "https://" + HOST})):
            with self.subTest(raw=raw), self.assertRaises(u.UploadError):
                u.load_creds("X", {"X": raw})

    def test_object_key_closed_shape(self):
        self.assertEqual(u.object_key("private", "42", "2", "device-matrix", "a24-small-x86"),
                         "logs/42/2/device-matrix/a24-small-x86.log.txt.gz.age")
        self.assertTrue(u.object_key("dryrun", 1, 1, "dry-run", "main").startswith("dryrun/"))
        for args in (("public", 1, 1, "app-test", "main"), ("private", "1/../x", 1, "app-test", "main"),
                     ("private", 1, 1, "../app", "main"), ("private", 1, 1, "app-test", "MAIN"),
                     ("private", 1, "x", "app-test", "main"), ("private", 1, 1, "app-test", "a/b")):
            with self.subTest(args=args), self.assertRaises(u.UploadError):
                u.object_key(*args)

    def test_no_public_probe_left(self):
        src = open(os.path.join(HERE, "upload_r2.py"), encoding="utf-8").read()
        self.assertNotIn("r2.dev/", src.replace("لا مسبارَ r2.dev", ""))
        self.assertFalse(hasattr(u, "public_probe"))


class VerifiedUploadTest(unittest.TestCase):
    def setUp(self):
        self.creds = u.load_creds("X", creds_env())
        self.body = b"age-encryption.org/v1\n-> X25519 abc\n" + os.urandom(64)

    def opener_for(self, put=200, head=200, length=None, etag=None):
        calls = []
        md5 = hashlib.md5(self.body).hexdigest()

        def op(req, timeout=0):
            calls.append(req.get_method())
            if req.get_method() == "PUT":
                return Resp(put)
            return Resp(head, {"Content-Length": str(len(self.body) if length is None else length),
                               "ETag": f'"{md5 if etag is None else etag}"'})
        return op, calls

    def test_verified(self):
        op, calls = self.opener_for()
        self.assertTrue(u.upload_verified(self.creds, "logs/1/1/app-test/main.log.txt.gz.age", self.body, op))
        self.assertEqual(calls, ["PUT", "HEAD"])

    def test_not_verified(self):
        for label, kw in {"put-500": {"put": 500}, "head-404": {"head": 404}, "length": {"length": 1},
                          "etag": {"etag": "0" * 32}}.items():
            op, _ = self.opener_for(**kw)
            with self.subTest(label=label), self.assertRaises(u.UploadError):
                u.upload_verified(self.creds, "logs/1/1/app-test/main.log.txt.gz.age", self.body, op)
        op, _ = self.opener_for()
        with self.assertRaises(u.UploadError):
            u.upload_verified(self.creds, "k", b"", op)


if __name__ == "__main__":
    unittest.main()
