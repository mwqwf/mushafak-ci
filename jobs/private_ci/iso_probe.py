#!/usr/bin/env python3
"""🔌 مسبارُ المقابس لفحوص العزل (أ2 · الإصلاح الرابع) — يثبت على العدّاء الحقيقيّ أنّ مستخدمَ البناء `pcibuild` لا يصل إلى
مقابس حساب العدّاء (ومنها مقبسُ Docker)، فلا يصير ADB أو غيرُه جسراً من حدّ البناء إلى صلاحية العدّاء.

يُنسخ إلى شجرةِ التحكّم التي يملكها root (`/var/lib/pci-ctl`) ويُشغَّل بـ`python3 -I -S` (المكتبةُ القياسيّة وحدها).

الأوامر:
  scan     — (بصلاحية root من المشرف) يجرد المقابسَ المرشَّحة ويكتبها إلى stdout مفصولةً بـNUL:
             • مقابسُ نظام الملفّات تحت /run و/tmp و/var/run يملكها حسابُ العدّاء (--uid)، أو مجموعتُها من مجموعات العدّاء
               (--gids) وليست من مجموعات مستخدم البناء (--skip-gids) — مثل docker؛
             • والمقابسُ المجرّدة (abstract) المُنصِتة التي تمسكها عمليّةٌ بحساب العدّاء (لا تحميها أذوناتُ الملفّات أصلاً).
  connect  — (بصلاحية pcibuild) يقرأ القائمةَ من stdin ويحاول الاتصالَ بكلٍّ منها بمهلةٍ قصيرة ثمّ يغلق فوراً؛
             يطبع «n k» (عددُ ما فُحص · عددُ ما قَبِل الاتصال) ويخرج 0 إن k=0 وإلا 1. ⛔ لا يطبع اسماً ولا مساراً.
  deny P   — (بصلاحية pcibuild) يخرج 0 إن رُفض الاتصالُ بالمقبس P بالصلاحية (EACCES/EPERM) أو لم يوجد (ENOENT)؛
             و1 إن قَبِل؛ و2 لأيّ نتيجةٍ أخرى (مثل ECONNREFUSED: الصلاحيةُ متاحةٌ وإن لم يُنصت أحد — فهي فشلٌ لا نجاح).
"""
import errno
import os
import re
import socket
import stat
import sys

ROOTS = ("/run", "/tmp", "/var/run")
MAX_ENTRIES = 200_000          # حدُّ ما يُجرد (لا تجوّلَ بلا نهاية)
CONNECT_TIMEOUT = 1.0
ACCEPTCON = 0x00010000        # __SO_ACCEPTCON في /proc/net/unix: مقبسٌ مُنصِت
SOCK_INODE = re.compile(r"^socket:\[(\d+)\]$")


def _ids(text):
    out = set()
    for part in (text or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def fs_candidates(uid, gids, skip_gids, roots=ROOTS, limit=MAX_ENTRIES):
    """مقابسُ نظام الملفّات المرشَّحة (بلا اتّباع روابط، وكلُّ جذرٍ مرّةً واحدة بعد حلّه)."""
    seen_roots, out, n = set(), [], 0
    for r in roots:
        try:
            real = os.path.realpath(r)
            st = os.lstat(real)
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode) or real in seen_roots:
            continue
        seen_roots.add(real)
        for dirpath, dirnames, filenames in os.walk(real, followlinks=False):
            for name in dirnames + filenames:
                n += 1
                if n > limit:
                    return out
                p = os.path.join(dirpath, name)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if not stat.S_ISSOCK(st.st_mode):
                    continue
                if st.st_uid == uid or (st.st_gid in gids and st.st_gid not in skip_gids):
                    out.append(p)
    return out


def abstract_candidates(uid, proc="/proc"):
    """المقابسُ المجرّدة المُنصِتة التي تمسكها عمليّةٌ بحساب العدّاء (uid الحقيقيّ)."""
    held = set()
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return []
    for pid in pids:
        try:
            with open(os.path.join(proc, pid, "status"), encoding="ascii", errors="replace") as f:
                real_uid = next((int(ln.split()[1]) for ln in f if ln.startswith("Uid:")), None)
            if real_uid != uid:
                continue
            fd_dir = os.path.join(proc, pid, "fd")
            for fd in os.listdir(fd_dir):
                m = SOCK_INODE.match(os.readlink(os.path.join(fd_dir, fd)))
                if m:
                    held.add(m.group(1))
        except (OSError, ValueError, StopIteration):
            continue
    out = []
    try:
        with open(os.path.join(proc, "net", "unix"), encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                parts = ln.split()
                if len(parts) < 8 or not parts[7].startswith("@"):
                    continue
                try:
                    flags = int(parts[3], 16)
                except ValueError:
                    continue
                if flags & ACCEPTCON and parts[6] in held:
                    out.append(" ".join(parts[7:]))
    except OSError:
        return out
    return sorted(set(out))


def _address(entry):
    return "\0" + entry[1:] if entry.startswith("@") else entry


def try_connect(entry, timeout=CONNECT_TIMEOUT):
    """يعيد (متّصل؟، رمزَ الخطأ). يجرّب stream ثمّ dgram ثمّ seqpacket عند EPROTOTYPE؛ ويغلق فوراً."""
    last = 0
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM, socket.SOCK_SEQPACKET):
        s = socket.socket(socket.AF_UNIX, kind)
        s.settimeout(timeout)
        try:
            s.connect(_address(entry))
            return True, 0
        except OSError as e:
            last = e.errno or 0
            if last != errno.EPROTOTYPE:
                return False, last
        finally:
            s.close()
    return False, last


def cmd_scan(argv):
    args = dict(zip(argv[::2], argv[1::2]))
    try:
        uid = int(args.get("--uid", ""))
    except ValueError:
        return 2
    gids, skip = _ids(args.get("--gids")), _ids(args.get("--skip-gids"))
    items = fs_candidates(uid, gids, skip) + abstract_candidates(uid)
    sys.stdout.buffer.write(b"".join(i.encode("utf-8", "surrogateescape") + b"\0" for i in items))
    return 0


def cmd_connect(stream):
    data = stream.read(16 * 1024 * 1024)
    items = [x.decode("utf-8", "surrogateescape") for x in data.split(b"\0") if x]
    k = sum(1 for i in items if try_connect(i)[0])
    print(f"{len(items)} {k}")
    return 0 if k == 0 else 1


def cmd_deny(path):
    ok, code = try_connect(path)
    if ok:
        return 1
    return 0 if code in (errno.EACCES, errno.EPERM, errno.ENOENT) else 2


def main(argv):
    if not argv:
        return 2
    if argv[0] == "scan":
        return cmd_scan(argv[1:])
    if argv[0] == "connect":
        return cmd_connect(sys.stdin.buffer)
    if argv[0] == "deny" and len(argv) == 2:
        return cmd_deny(argv[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
