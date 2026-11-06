#!/usr/bin/env python3
"""🔌 مسبارُ المقابس لفحوص العزل (أ2 · الإصلاحان الرابع والخامس) — يثبت على العدّاء الحقيقيّ أنّ مستخدمَ البناء `pcibuild` لا يصل
إلى مقابس حساب العدّاء (ومنها مقبسُ Docker)، فلا يصير ADB أو غيرُه جسراً من حدّ البناء إلى صلاحية العدّاء.

يُنسخ إلى شجرةِ التحكّم التي يملكها root (`/var/lib/pci-ctl`) ويُشغَّل بـ`python3 -I -S` (المكتبةُ القياسيّة وحدها).

⛔ الإصلاحُ الخامس (مراجعة Codex 14 — «مهم»): **الجردُ يفشل مغلقاً**. أيُّ نقصٍ فيه لا يُحسب صفراً بل «ناقصاً» يُسقط الفحص:
   بلوغُ MAX_ENTRIES · تعذّرُ قراءة مجلّدٍ تحت الجذور · تعذّرُ قراءة /proc/net/unix · تعذّرُ قراءة fd لعمليّةٍ بحساب العدّاء
   **ما لم تكن العمليّةُ قد زالت** (سباقٌ طبيعيّ: /proc/<pid> لم يعد موجوداً). ونتيجةُ الاتّصال تُصنَّف ولا تُخلط بالرفض.

الأوامر:
  scan     — (بصلاحية root من المشرف) يجرد المقابسَ المرشَّحة ويكتبها إلى stdout مفصولةً بـNUL، ثمّ **سجلَّ حالةٍ أخيراً**
             يبدأ بـ«!» (لا يبدأ به مسارٌ ولا اسمٌ مجرّد): `!inventory=complete|incomplete truncated=0|1 walk_errors=N
             unix_error=0|1 fd_errors=N fd_vanished=N` — أعدادٌ وكلماتٌ ثابتة. ويخرج 0 إن تمّ الجردُ و3 إن نقص.
             المرشَّحات:
             • مقابسُ نظام الملفّات تحت /run و/tmp و/var/run يملكها حسابُ العدّاء (--uid)، أو مجموعتُها من مجموعات العدّاء
               (--gids) وليست من مجموعات مستخدم البناء (--skip-gids) — مثل docker؛
             • والمقابسُ المجرّدة (abstract) التي تمسكها عمليّةٌ بحساب العدّاء: المُنصِتةُ (ACCEPTCON) **ومقابسُ datagram**
               (لا ACCEPTCON لها، وتقبل الإرسالَ بلا أيّ صلاحيّة ملفّات) — فلا تحميها الأذوناتُ أصلاً.
  connect  — (بصلاحية pcibuild) يقرأ القائمةَ (ومعها سجلَّ الحالة) من stdin ويحاول الاتّصالَ بكلٍّ منها بمهلةٍ قصيرة ثمّ يغلق فوراً،
             ويصنّف كلَّ نتيجة: accepted (قَبِل — فشل) · denied (EACCES/EPERM — **المقبولُ الوحيد**) · refused (ECONNREFUSED:
             الصلاحيةُ متاحةٌ وإن لم يُنصت أحد — فشل) · vanished (ENOENT بعد الجرد — سباقٌ يُعدّ ولا يُفشل) · other (مهلة/EAGAIN/
             غيرها — فشل). يطبع سطراً واحداً من أعدادٍ وكلماتٍ ثابتة:
             `n=N accepted=A denied=D refused=R vanished=V other=O inventory=complete|incomplete`
             ويخرج 0 فقط إن A=R=O=0 والجردُ تامّ (سجلُّ حالةٍ صالحٌ يقول complete — وغيابُه ناقص)؛ وإلا 1. ⛔ لا اسمَ ولا مسار.
  deny P [--exists]
           — (بصلاحية pcibuild) يخرج 0 إن رُفض الاتّصالُ بالمقبس P بالصلاحية (EACCES/EPERM) أو لم يوجد (ENOENT — إلا مع --exists:
             مقبسٌ أنشأه المشرفُ للتوّ فغيابُه فشل)؛ و1 إن قَبِل؛ و2 لأيّ نتيجةٍ أخرى (مثل ECONNREFUSED: الصلاحيةُ متاحة — فشلٌ لا نجاح).
"""
import errno
import os
import re
import socket
import stat
import sys

ROOTS = ("/run", "/tmp", "/var/run")
MAX_ENTRIES = 200_000          # حدُّ ما يُجرد (لا تجوّلَ بلا نهاية) — وبلوغُه **جردٌ ناقص** لا قائمةٌ جزئيّةٌ صامتة
CONNECT_TIMEOUT = 1.0
ACCEPTCON = 0x00010000        # __SO_ACCEPTCON في /proc/net/unix: مقبسٌ مُنصِت
SOCK_DGRAM_TYPE = 0x0002      # عمودُ Type في /proc/net/unix
SOCK_INODE = re.compile(r"^socket:\[(\d+)\]$")
STATUS_PREFIX = "!"
STATUS_RE = re.compile(r"^!inventory=(complete|incomplete) truncated=[01] walk_errors=\d+ unix_error=[01] "
                       r"fd_errors=\d+ fd_vanished=\d+$")
CLASSES = ("accepted", "denied", "refused", "vanished", "other")


def _ids(text):
    out = set()
    for part in (text or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


class Inventory:
    """عدّاداتُ نقص الجرد — أيُّ واحدٍ منها (عدا fd_vanished) يجعله ناقصاً."""

    def __init__(self):
        self.truncated = False
        self.walk_errors = 0
        self.unix_error = False
        self.fd_errors = 0
        self.fd_vanished = 0

    @property
    def complete(self):
        return not (self.truncated or self.walk_errors or self.unix_error or self.fd_errors)

    def status(self):
        return (f"{STATUS_PREFIX}inventory={'complete' if self.complete else 'incomplete'} "
                f"truncated={int(self.truncated)} walk_errors={self.walk_errors} unix_error={int(self.unix_error)} "
                f"fd_errors={self.fd_errors} fd_vanished={self.fd_vanished}")


def fs_candidates(uid, gids, skip_gids, roots=ROOTS, limit=MAX_ENTRIES, inv=None):
    """مقابسُ نظام الملفّات المرشَّحة (بلا اتّباع روابط، وكلُّ جذرٍ مرّةً واحدة بعد حلّه). inv يُسجَّل فيه كلُّ نقص."""
    inv = inv if inv is not None else Inventory()
    seen_roots, out, n = set(), [], 0

    def onerror(err):
        # مجلّدٌ زال بين رؤيته وقراءته سباقٌ لا نقص؛ وغيرُه (EACCES · EIO …) نقصٌ يُسقط الجرد
        if getattr(err, "errno", None) != errno.ENOENT:
            inv.walk_errors += 1

    for r in roots:
        try:
            real = os.path.realpath(r)
            st = os.lstat(real)
        except FileNotFoundError:
            continue                       # جذرٌ غائبٌ في هذه الصورة: لا شيءَ يُجرد فيه
        except OSError:
            inv.walk_errors += 1
            continue
        if not stat.S_ISDIR(st.st_mode) or real in seen_roots:
            continue
        seen_roots.add(real)
        for dirpath, dirnames, filenames in os.walk(real, followlinks=False, onerror=onerror):
            for name in dirnames + filenames:
                n += 1
                if n > limit:
                    inv.truncated = True
                    return out
                p = os.path.join(dirpath, name)
                try:
                    st = os.lstat(p)
                except FileNotFoundError:
                    continue
                except OSError:
                    inv.walk_errors += 1
                    continue
                if not stat.S_ISSOCK(st.st_mode):
                    continue
                if st.st_uid == uid or (st.st_gid in gids and st.st_gid not in skip_gids):
                    out.append(p)
    return out


def _gone(proc, pid):
    return not os.path.exists(os.path.join(proc, pid))


def abstract_candidates(uid, proc="/proc", inv=None):
    """المقابسُ المجرّدة التي تمسكها عمليّةٌ بحساب العدّاء (uid الحقيقيّ): المُنصِتةُ ومقابسُ datagram."""
    inv = inv if inv is not None else Inventory()
    held = set()
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        inv.fd_errors += 1
        pids = []
    for pid in pids:
        try:
            with open(os.path.join(proc, pid, "status"), encoding="ascii", errors="replace") as f:
                real_uid = next((int(ln.split()[1]) for ln in f if ln.startswith("Uid:")), None)
        except (OSError, ValueError, IndexError):
            if _gone(proc, pid):
                inv.fd_vanished += 1
            else:
                inv.fd_errors += 1         # لا يُعرف مالكُها ⇒ لا يُعرف أتمسك مقبساً لحساب العدّاء
            continue
        if real_uid is None:
            inv.fd_errors += 1
            continue
        if real_uid != uid:
            continue
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            if _gone(proc, pid):
                inv.fd_vanished += 1
            else:
                inv.fd_errors += 1
            continue
        for fd in fds:
            try:
                m = SOCK_INODE.match(os.readlink(os.path.join(fd_dir, fd)))
            except FileNotFoundError:
                continue                   # واصفٌ أُغلق بين السرد والقراءة: سباقٌ لا نقص
            except OSError:
                if _gone(proc, pid):
                    inv.fd_vanished += 1
                    break
                inv.fd_errors += 1
                continue
            if m:
                held.add(m.group(1))
    out = []
    try:
        with open(os.path.join(proc, "net", "unix"), encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                parts = ln.split()
                if len(parts) < 8 or not parts[7].startswith("@"):
                    continue
                try:
                    flags, kind = int(parts[3], 16), int(parts[4], 16)
                except ValueError:
                    inv.unix_error = True
                    continue
                if (flags & ACCEPTCON or kind == SOCK_DGRAM_TYPE) and parts[6] in held:
                    out.append(" ".join(parts[7:]))
    except OSError:
        inv.unix_error = True
        return out
    return sorted(set(out))


def _address(entry):
    return "\0" + entry[1:] if entry.startswith("@") else entry


def try_connect(entry, timeout=CONNECT_TIMEOUT):
    """يعيد (متّصل؟، رمزَ الخطأ) ويغلق فوراً. يجرّب stream ثمّ dgram ثمّ seqpacket ما دام الردُّ EPROTOTYPE أو ECONNREFUSED
    (المقبسُ المجرّدُ من نوع datagram يردّ stream بـECONNREFUSED ويقبل dgram بلا صلاحيّة — فلا يُحكم عليه من النوع الأوّل).
    أيُّ نوعٍ يتّصل ⇒ قَبِل؛ وغيرُ ذينك الرمزين (EACCES · ENOENT · مهلة …) حكمٌ فوريّ؛ وإن لم يتّصل نوعٌ فـECONNREFUSED إن وقع. المهلةُ ⇒ 0."""
    last, refused = 0, False
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM, socket.SOCK_SEQPACKET):
        s = socket.socket(socket.AF_UNIX, kind)
        s.settimeout(timeout)
        try:
            s.connect(_address(entry))
            return True, 0
        except OSError as e:
            last = e.errno or 0
            if last == errno.ECONNREFUSED:
                refused = True
            elif last != errno.EPROTOTYPE:
                return False, last
        finally:
            s.close()
    return False, (errno.ECONNREFUSED if refused else last)


def classify(ok, code):
    if ok:
        return "accepted"
    if code in (errno.EACCES, errno.EPERM):
        return "denied"
    if code == errno.ECONNREFUSED:
        return "refused"
    if code == errno.ENOENT:
        return "vanished"
    return "other"


def cmd_scan(argv, out=None, proc="/proc", roots=ROOTS, limit=MAX_ENTRIES):
    out = out or sys.stdout.buffer
    args = dict(zip(argv[::2], argv[1::2]))
    try:
        uid = int(args.get("--uid", ""))
    except ValueError:
        return 2
    gids, skip = _ids(args.get("--gids")), _ids(args.get("--skip-gids"))
    inv = Inventory()
    items = fs_candidates(uid, gids, skip, roots=roots, limit=limit, inv=inv) + abstract_candidates(uid, proc=proc, inv=inv)
    items = [i for i in items if not i.startswith(STATUS_PREFIX)]   # لا يُخلط مدخلٌ بسجلّ الحالة (والمسارُ المطلقُ لا يبدأ بـ«!»)
    out.write(b"".join(i.encode("utf-8", "surrogateescape") + b"\0" for i in items) + inv.status().encode("ascii") + b"\0")
    return 0 if inv.complete else 3


def connect_summary(data, connector=try_connect):
    """(سطرُ الأعداد، رمزُ الخروج) من مخرج scan."""
    recs = [x.decode("utf-8", "surrogateescape") for x in data.split(b"\0") if x]
    status = [r for r in recs if r.startswith(STATUS_PREFIX)]
    items = [r for r in recs if not r.startswith(STATUS_PREFIX)]
    complete = len(status) == 1 and recs and recs[-1] == status[0] and bool(STATUS_RE.fullmatch(status[0])) \
        and status[0].startswith(STATUS_PREFIX + "inventory=complete ")
    n = {c: 0 for c in CLASSES}
    for i in items:
        n[classify(*connector(i))] += 1
    line = (f"n={len(items)} " + " ".join(f"{c}={n[c]}" for c in CLASSES)
            + f" inventory={'complete' if complete else 'incomplete'}")
    ok = complete and n["accepted"] == 0 and n["refused"] == 0 and n["other"] == 0
    return line, (0 if ok else 1)


def cmd_connect(stream):
    line, rc = connect_summary(stream.read(16 * 1024 * 1024))
    print(line)
    return rc


def cmd_deny(path, must_exist=False):
    ok, code = try_connect(path)
    if ok:
        return 1
    allowed = (errno.EACCES, errno.EPERM) if must_exist else (errno.EACCES, errno.EPERM, errno.ENOENT)
    return 0 if code in allowed else 2


def main(argv):
    if not argv:
        return 2
    if argv[0] == "scan":
        return cmd_scan(argv[1:])
    if argv[0] == "connect":
        return cmd_connect(sys.stdin.buffer)
    if argv[0] == "deny" and len(argv) == 2:
        return cmd_deny(argv[1])
    if argv[0] == "deny" and len(argv) == 3 and argv[2] == "--exists":
        return cmd_deny(argv[1], must_exist=True)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
