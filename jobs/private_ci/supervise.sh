#!/usr/bin/env bash
# 🧑‍✈️ private-ci (v3) — **المشرفُ الموثوق** في وظيفة build، بمستخدم العدّاء `runner` (D11 · D12 · D13).
#    البناءُ غيرُ الموثوق يجري بمستخدمٍ آخر `pcibuild` بلا sudo، والمشرفُ وحده هو من: يهيّئ العزلَ ويختبره، ويستنسخ، ويشغّل كلَّ طورٍ
#    ويسجّل رمزَ خروجه الفعليّ، ويعدّ، ويشفّر، ويفحص — ولا يُرفع شيءٌ قبل الفحص.
#
# الأوضاع (المعامل الأوّل):
#   prepare  — ينسخ أدواتِه إلى $RUNNER_TEMP/pci/ctl (700) · يثبّت age · ينشئ pcibuild ويحصّن SDK وhostedtoolcache و/home/runner ·
#              ثمّ **يختبر العزلَ بنفسه** (rc_isolation)؛ أيُّ فشلٍ ⇒ خروجٌ غيرُ صفر فلا يبلغ المفتاحُ الاستنساخَ.
#   clone    — يشغّل clone.sh (المفتاحُ في بيئة الخطوة وحدها) ثمّ يُملِّك الشجرةَ لـpcibuild في /home/pcibuild/src.
#   build    — أطوارُ النوع قبل المحاكي (kinds.json) · emu — أطوارُه داخل خطوة المحاكي.
#   finalize — (always) يقتل عمليّاتِ pcibuild ← counts.json ← تقريرُ الحارس ← التشفير ← artifact_check ⇒ ready=true|false.
#
# ⛔ لا `set -x`. وما يُطبع علناً جملٌ ثابتةٌ وأعدادٌ فقط؛ وكلُّ ما سواه إلى $RUNNER_TEMP/pci/out/log.txt (الواصفةُ 3 للعلنيّ).
set -uo pipefail

case "${GITHUB_WORKFLOW_REF:-}" in
  */.github/workflows/private-ci.yml@*) ;;
  *) echo "⛔ jobs/private_ci/supervise.sh لا يُشغَّل إلا من private-ci.yml — لا شيء نُفّذ."; exit 2 ;;
esac

MODE="${1:-}"
# مسارُ الوظيفة كما وصل (يُحصَّن كلُّ مجلّدٍ فيه في prepare)، ومسارُ المشرف نفسِه ثابتٌ لا يعتمد على مجلّدٍ قد يكون قابلاً للكتابة
JOB_PATH="$PATH"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# 🧪 مجلّدُ أدواتٍ مزيَّفةٍ (sudo وهميّ) للاختبار وحده: لا يُقبل على عدّاءٍ حقيقيّ (RUNNER_ENVIRONMENT يضبطه العدّاءُ دائماً).
if [ -z "${RUNNER_ENVIRONMENT:-}" ] && [ -n "${PCI_TEST_BIN_DIR:-}" ]; then PATH="$PCI_TEST_BIN_DIR:$PATH"; fi
: "${RUNNER_TEMP:?}" "${PCI_KIND:?}" "${PCI_SHA:?}" "${PCI_SOURCE:?}" "${PCI_LEG:?}"
PCI_ROOT="$RUNNER_TEMP/pci"
PCI_CTL="$PCI_ROOT/ctl"          # أدواتُ المشرف (700) — لا يصل إليها البناء
PCI_OUT="$PCI_ROOT/out"          # السجلُّ الخاصّ قبل تشفيره
PCI_STATE="$PCI_ROOT/state"      # رموزُ خروج الأطوار · ran.txt · deadline — يكتبها المشرفُ وحده
PCI_ART="$PCI_ROOT/art"          # ما يُرفع: counts.json · log.txt.gz.age — لا غير
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
umask 077

# 🧪 وضعُ الاختبار المحلّيّ: لا يُقبل داخل GitHub Actions أبداً (فيُتجاهل المتغيّرُ هناك ويجري العزلُ الحقيقيّ).
LOCAL=0
if [ "${GITHUB_ACTIONS:-}" != "true" ] && [ "${PCI_TEST_NO_ISOLATION:-}" = "1" ]; then LOCAL=1; fi
ISO=$((1 - LOCAL))
if [ "$ISO" = 1 ] && [ "${GITHUB_ACTIONS:-}" != "true" ]; then
  echo "⛔ العزلُ الحقيقيّ (مستخدمٌ ومساراتٌ نظاميّة) لا يجري إلا داخل GitHub Actions — لا شيء نُفّذ."; exit 2
fi
if [ "$LOCAL" = 1 ]; then BHOME="$RUNNER_TEMP/builder"; else BHOME="/home/pcibuild"; fi
BSRC="$BHOME/src"; BSTATE="$BHOME/state"; BDEV="$BHOME/device"
KINDS_ARGS=()
PHASE_CAP=$((32 * 1024 * 1024))    # D18: سجلُّ الطور الواحد
if [ "$LOCAL" = 1 ]; then
  [ -n "${PCI_TEST_KINDS_FILE:-}" ] && KINDS_ARGS=(--kinds "$PCI_TEST_KINDS_FILE")
  [[ "${PCI_TEST_PHASE_CAP:-}" =~ ^[0-9]+$ ]] && PHASE_CAP="$PCI_TEST_PHASE_CAP"
fi
TOTAL_CAP=$((PHASE_CAP / 2 * 3))   # والكلّ 48 م.ب (1.5× الطور)

mkdir -p "$PCI_OUT" "$PCI_STATE" "$PCI_ART"
# ───── من هنا لا يخرج حرفٌ إلى السجلّ العلنيّ إلا عبر pub (الواصفة 3) ─────
exec 3>&1
exec >>"$PCI_OUT/log.txt" 2>&1

pub(){ printf '%s\n' "$*" >&3; }
say(){ printf '▸ %s [%s] %s\n' "$(date -u +%H:%M:%S)" "$MODE" "$*"; }
rc_set(){ printf '%s\n' "$2" > "$PCI_STATE/rc_$1"; }
ran_add(){ printf '%s\n' "$1" >> "$PCI_STATE/ran.txt"; }
cs(){ python3 "$PCI_CTL/counts_schema.py" "$@" ${KINDS_ARGS[@]+"${KINDS_ARGS[@]}"}; }

# ───── بيئةُ البناء: قائمةٌ مسموحةٌ وحدها (env -i) — لا GITHUB_* غيرُ المرجع، ولا ACTIONS_*، ولا رمز ─────
BENV=()
make_benv(){
  local wref="" jh="${JAVA_HOME:-}" ah="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}" path="" part
  [ -f "$PCI_STATE/whisper_ref" ] && wref="$(head -c 64 "$PCI_STATE/whisper_ref" | tr -d '\n')"
  [[ "$wref" =~ ^[0-9a-f]{40}$ ]] || wref=""
  for part in "${jh:+$jh/bin}" "${ah:+$ah/platform-tools}" "${ah:+$ah/cmdline-tools/latest/bin}" /usr/local/bin /usr/bin /bin; do
    [ -n "$part" ] && path="${path:+$path:}$part"
  done
  BENV=("HOME=$BHOME" "PATH=$path" "JAVA_HOME=$jh" "ANDROID_HOME=$ah" "ANDROID_SDK_ROOT=${ANDROID_SDK_ROOT:-$ah}"
        "ANDROID_USER_HOME=$BHOME/.android" "GRADLE_USER_HOME=$BHOME/.gradle"
        "GRADLE_OPTS=-Dorg.gradle.daemon=false -Dorg.gradle.console=plain"
        CI=true TERM=dumb LANG=C.UTF-8 PYTHONIOENCODING=utf-8
        "GITHUB_WORKFLOW_REF=${GITHUB_WORKFLOW_REF}" "PCI_KIND=$PCI_KIND" "PCI_LEG=$PCI_LEG" "PCI_LINT=${PCI_LINT:-false}"
        "PCI_SRC=$BSRC" "PCI_BSTATE=$BSTATE" "PCI_BDEVICE=$BDEV" "PCI_WHISPER_REF=$wref")
}
as_builder(){ # أمرٌ واحدٌ بمستخدم البناء ببيئته المسموحة
  if [ "$ISO" = 1 ]; then sudo -n -u pcibuild -H env -i "${BENV[@]}" "$@"; else env -i "${BENV[@]}" "$@"; fi
}

# ───── prepare ─────
install_age(){
  [ "$LOCAL" = 1 ] && return 0          # محلّيّاً: age النظام عبر PCI_TEST_AGE_BIN (بلا شبكة)
  bash "$PCI_CTL/encrypt_log.sh" install
}

setup_builder(){ # ⛔ يجري قبل أوّل سطرٍ غيرِ موثوق
  local me home sdk tc="/opt/hostedtoolcache"
  sudo -n useradd --create-home --shell /bin/bash --user-group pcibuild || return 1
  sudo -n chmod 700 /home/pcibuild || return 1
  me="$(id -un)"; home="$(getent passwd "$me" | cut -d: -f6)"
  sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
  [ -n "$sdk" ] && [ -d "$sdk" ] && [ -n "$home" ] && [ -d "$tc" ] || { say "⛔ لا SDK أو hostedtoolcache أو بيتُ العدّاء"; return 1; }
  sdk="$(readlink -f "$sdk")"
  sudo -n install -d -o pcibuild -g pcibuild -m 700 /home/pcibuild/state /home/pcibuild/device || return 1
  # نسخةُ build.sh يقرؤها البناءُ ولا يعدّلها (مِلكُ root · لا كتابة)
  sudo -n install -D -o root -g root -m 0644 "$PCI_CTL/build.sh" /home/pcibuild/ctl/build.sh || return 1
  # قراءةُ المشرف لشجرة البناء (تقاريرُ JUnit) بلا فتح البيت لغيره: ACL لمستخدم العدّاء وحدَه
  sudo -n setfacl -m "u:$me:rx" /home/pcibuild || return 1
  # لا مساحةَ عملٍ ولا _temp ولا _diag ولا .credentials تُقرأ من pcibuild
  sudo -n chmod 700 "$home" || return 1
  # حقيقتان من مصدر صورة العدّاء: SDK بـa+rwx وhostedtoolcache بـ777 ⇒ تُحصَّنان (فلا يستبدل البناءُ adb/emulator/JDK)
  sudo -n chown -R -H "$me:$me" "$sdk" "$tc" || return 1
  sudo -n chmod -R o-w "$sdk" "$tc" || return 1
  return 0
}

harden_path_dirs(){ # D11: كلُّ مجلّدٍ في PATH الوظيفة يكتب فيه pcibuild يُملَّك للعدّاء بلا كتابةٍ لغيره
  # (وإلا وضع فيه `bash` أو `sudo` أو `python3` مزيَّفاً تنفّذه خطواتٌ موثوقةٌ بصلاحية runner)
  local me d rd; me="$(id -un)"
  local IFS=:
  for d in $JOB_PATH; do
    [ -n "$d" ] && [ -d "$d" ] || continue
    rd="$(readlink -f "$d")"
    case "$rd" in /|/usr|/usr/bin|/usr/sbin|/bin|/sbin|/usr/local|/usr/local/bin|/usr/local/sbin) continue ;; esac  # نظاميّةٌ مملوكةٌ لـroot: تُختبر لا تُمسّ
    if as_builder test -w "$rd" >/dev/null 2>&1; then
      sudo -n chown -R -H "$me:$me" "$rd" && sudo -n chmod -R o-w "$rd" || return 1
    fi
  done
  return 0
}

isolation_tests(){ # يضبط ISO_N وISO_OK؛ ما يجب أن يفشل وما يجب أن ينجح
  local sdk tc="/opt/hostedtoolcache" me home
  me="$(id -un)"; home="$(getent passwd "$me" | cut -d: -f6)"
  sdk="$(readlink -f "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}")"
  ISO_N=0; ISO_OK=0
  must_fail(){ ISO_N=$((ISO_N + 1)); if as_builder "$@" >/dev/null 2>&1; then say "⛔ عزلٌ مفقود (فحصٌ $ISO_N)"; else ISO_OK=$((ISO_OK + 1)); fi; }
  must_pass(){ ISO_N=$((ISO_N + 1)); if as_builder "$@" >/dev/null 2>&1; then ISO_OK=$((ISO_OK + 1)); else say "⛔ وصولٌ لازمٌ مفقود (فحصٌ $ISO_N)"; fi; }
  must_fail bash -c 'sudo -n true'
  must_fail bash -c 'cat "/proc/$1/environ"' _ "$$"
  must_fail bash -c 'ls "$1"' _ "$RUNNER_TEMP"
  must_fail bash -c 'touch "$1/.pci_probe"' _ "$sdk"
  must_fail bash -c 'touch "$1/.pci_probe"' _ "$tc"
  must_fail test -r "$PCI_CTL/supervise.sh"
  must_fail bash -c 'ls "$1"' _ "$home"
  # لا مجلّدَ في PATH الوظيفة (ولا في مسار المشرف) يكتب فيه pcibuild
  ISO_N=$((ISO_N + 1)); local d bad=0; local IFS=:
  for d in $JOB_PATH:$PATH; do
    [ -n "$d" ] && [ -d "$d" ] || continue
    if as_builder test -w "$d" >/dev/null 2>&1; then bad=1; fi
  done
  IFS=$' \t\n'
  if [ "$bad" = 0 ]; then ISO_OK=$((ISO_OK + 1)); else say "⛔ مجلّدٌ في PATH يكتب فيه pcibuild (فحصٌ $ISO_N)"; fi
  must_pass bash -c 'test -x "$JAVA_HOME/bin/java"'
  must_pass bash -c 'test -r "$ANDROID_HOME" && ls "$ANDROID_HOME" >/dev/null'
  must_pass bash -c 'touch "$HOME/.probe"'
  rm -f "$sdk/.pci_probe" "$tc/.pci_probe" 2>/dev/null || true   # لو نجحت كتابةٌ (عزلٌ مفقود) لا يبقى أثر
}

do_prepare(){
  rm -rf "$PCI_CTL"
  mkdir -p "$PCI_CTL" && chmod 700 "$PCI_CTL" && cp -a "$HERE/." "$PCI_CTL/" && chmod -R go-rwx "$PCI_CTL" || { pub "العزل: ⛔ تعذّر نسخُ الأدوات"; return 1; }
  : > "$PCI_STATE/ran.txt"
  if [ "$LOCAL" = 1 ]; then   # محلّيّاً بيتُ البناء تحت RUNNER_TEMP؛ وفي Actions ينشئه sudo install في setup_builder
    mkdir -p "$BSTATE" "$BDEV" && install -D -m 0644 "$PCI_CTL/build.sh" "$BHOME/ctl/build.sh" || return 1
  fi
  # مهلةُ الشوط: ما بقي حتى (مهلة الشوط − 6 دقائق) فيبقى لـfinalize وقتُه
  local tm; tm="$(cs timeout --kind "$PCI_KIND" --leg "$PCI_LEG")"
  [[ "$tm" =~ ^[0-9]+$ ]] || tm=30
  echo $(( $(date +%s) + (tm - 6) * 60 )) > "$PCI_STATE/deadline"
  ran_add isolation
  local rc=0
  if [ "$LOCAL" = 1 ]; then
    install_age || rc=1
    rc_set isolation "$rc"
    pub "العزل: ⏭ متخطّى (وضعُ الاختبار المحلّيّ)"
    return "$rc"
  fi
  setup_builder || { say "⛔ تعذّر تهيئةُ المستخدم والتحصين"; rc=1; }
  [ "$rc" = 0 ] && { make_benv; harden_path_dirs || { say "⛔ تعذّر تحصينُ مجلّدات PATH"; rc=1; }; }
  [ "$rc" = 0 ] && { install_age || { say "⛔ تعذّر تثبيتُ age"; rc=1; }; }
  if [ "$rc" = 0 ]; then
    make_benv
    isolation_tests
    [ "$ISO_OK" = "$ISO_N" ] || rc=1
    [ "$rc" = 0 ] && pub "العزل: ✅ $ISO_OK/$ISO_N" || pub "العزل: ⛔ $ISO_OK/$ISO_N"
  else
    pub "العزل: ⛔ لم تكتمل التهيئة"
  fi
  rc_set isolation "$rc"
  return "$rc"
}

# ───── clone ─────
move_src(){
  if [ "$ISO" = 1 ]; then
    sudo -n rm -rf "$BSRC" && sudo -n chown -R pcibuild:pcibuild "$PCI_ROOT/src" && sudo -n mv "$PCI_ROOT/src" "$BSRC"
  else
    rm -rf "$BSRC" && mv "$PCI_ROOT/src" "$BSRC"
  fi
}

do_clone(){
  # ⛔ دفاعٌ ثانٍ: لا يُلمس المفتاحُ إن لم يثبت العزل (الخطوةُ مشروطةٌ أصلاً بنجاح prepare)
  if [ "$(cat "$PCI_STATE/rc_isolation" 2>/dev/null)" != "0" ]; then
    say "⛔ العزلُ لم يثبت — لا استنساخ"; pub "الاستنساخ: ⛔ لم يجرِ (العزلُ لم يثبت)"; return 1
  fi
  bash "$PCI_CTL/clone.sh"; local rc=$?
  ran_add clone
  if [ "$rc" = 0 ] && ! move_src; then say "⛔ تعذّر نقلُ الشجرة إلى بيت البناء"; rc_set clone 1; rc=1; fi
  if [ "$rc" = 0 ]; then pub "الاستنساخ: ✅"; else pub "الاستنساخ: ⛔"; fi
  return "$rc"
}

# ───── الأطوار ─────
free_disk(){ sudo -n rm -rf /usr/share/dotnet /opt/ghc /usr/local/.ghcup /opt/hostedtoolcache/CodeQL /usr/local/share/boost; df -h /; }

enable_kvm(){
  echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' | sudo -n tee /etc/udev/rules.d/99-kvm4all.rules >/dev/null
  sudo -n udevadm control --reload-rules && sudo -n udevadm trigger --name-match=kvm && sudo -n udevadm settle
  sudo -n setfacl -m "u:$(id -un):rw" /dev/kvm
  test -r /dev/kvm -a -w /dev/kvm
}

run_runner_phase(){ # أوامرُ ثابتةٌ في المشرف — لا سكربتَ من الخاصّ بصلاحيّة
  case "$1" in
    disk) ( free_disk ) ;;
    kvm)  ( enable_kvm ) ;;
    *)    return 2 ;;
  esac
}

run_builder_phase(){ # $1 المعرّف · $2 الثواني المتاحة — المخرجاتُ إلى ملفٍّ يملكه المشرفُ ثمّ تُلحق بحدّ
  local id="$1" secs="$2" out="$PCI_OUT/phase_$1.txt" used cap rc sz
  local -a cmd
  used="$(cat "$PCI_STATE/log_bytes" 2>/dev/null)"; [[ "$used" =~ ^[0-9]+$ ]] || used=0
  cap="$PHASE_CAP"; [ $((TOTAL_CAP - used)) -lt "$cap" ] && cap=$((TOTAL_CAP - used)); [ "$cap" -lt 0 ] && cap=0
  if [ "$ISO" = 1 ]; then
    cmd=(sudo -n -u pcibuild -H env -i "${BENV[@]}" bash /home/pcibuild/ctl/build.sh phase "$id")
  else
    cmd=(env -i "${BENV[@]}" bash "$BHOME/ctl/build.sh" phase "$id")
  fi
  # يُستنزَف الأنبوبُ بعد الحدّ (فلا يتعطّل البناءُ بـSIGPIPE) ويُهمَل الفائض
  ( cd "$BHOME" && exec timeout --kill-after=30s "${secs}s" "${cmd[@]}" </dev/null 2>&1 ) | { head -c "$cap" > "$out"; cat > /dev/null; }
  rc=${PIPESTATUS[0]}
  sz="$(stat -c %s "$out" 2>/dev/null || echo 0)"
  cat "$out" >> "$PCI_OUT/log.txt" 2>/dev/null; rm -f "$out"
  [ "$sz" -ge "$cap" ] && printf '%s\n' "▸ ⛔ بُتر سجلُّ الطور $id عند الحدّ"
  echo $((used + sz)) > "$PCI_STATE/log_bytes"
  return "$rc"
}

run_phases(){ # $1: build | emu
  local mode="$1" line id who rc secs deadline total=0 failed=0
  local -a lines
  if [ "$(cat "$PCI_STATE/rc_isolation" 2>/dev/null)" != "0" ] || [ "$(cat "$PCI_STATE/rc_clone" 2>/dev/null)" != "0" ] || [ ! -d "$BSRC" ]; then
    say "⛔ لا مصدر أو لم يثبت العزل — تخطّي"; return 0
  fi
  mapfile -t lines < <(cs phases --kind "$PCI_KIND" --leg "$PCI_LEG" --mode "$mode")
  deadline="$(cat "$PCI_STATE/deadline" 2>/dev/null)"; [[ "$deadline" =~ ^[0-9]+$ ]] || deadline=0
  make_benv
  for line in "${lines[@]}"; do
    read -r id who <<<"$line"
    [[ "$id" =~ ^[a-z0-9_]{1,40}$ ]] || continue
    total=$((total + 1))
    ran_add "$id"
    secs=$((deadline - $(date +%s)))
    if [ "$secs" -lt 60 ]; then
      say "⏱ لا وقتَ كافياً لطور $id"; rc_set "$id" 124; failed=$((failed + 1)); continue
    fi
    say "← $id ($who)"
    case "$who" in
      runner)  run_runner_phase "$id"; rc=$? ;;
      builder) run_builder_phase "$id" "$secs"; rc=$? ;;
      *)       rc=2 ;;
    esac
    rc_set "$id" "$rc"
    say "→ $id = $rc"
    [ "$rc" != 0 ] && failed=$((failed + 1))
  done
  pub "الأطوار ($mode): $total · فاشلة $failed"
  return 0
}

# ───── finalize ─────
do_cleanup(){ # مستخدمٌ مؤقّتٌ على آلةٍ تُتلَف — لا علاقةَ له بقاعدة «لا إنهاءَ بالجملة» على جهاز المالك
  ran_add cleanup
  local rc=0 i
  if [ "$ISO" = 1 ]; then
    sudo -n pkill -KILL -u pcibuild >/dev/null 2>&1
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      pgrep -u pcibuild >/dev/null 2>&1 || break
      sleep 0.5
    done
    if pgrep -u pcibuild >/dev/null 2>&1; then rc=1; fi
  fi
  rc_set cleanup "$rc"
}

take_ownership(){ # بعد موت البناء: شجرتُه ونتائجُ الجهاز تصير للمشرف ليقرأ JUnit (umask 077 يجعل ملفّاتِ pcibuild 600 فلا تُقرأ عبر المستخدمين)
  [ "$ISO" = 1 ] || return 0
  local me; me="$(id -un)"
  local d
  for d in "$BSRC" "$BDEV"; do
    [ -e "$d" ] || continue
    sudo -n chown -R -h "$me:$me" "$d" || { say "⛔ تعذّر تملُّكُ $d"; return 1; }
  done
  return 0
}

do_finalize(){
  local ready=true
  do_cleanup
  take_ownership || ready=false
  if ! cs emit --state "$PCI_STATE" --src "$BSRC" --device "$BDEV" --kind "$PCI_KIND" --leg "$PCI_LEG" \
       --sha "$PCI_SHA" --out "$PCI_ART/counts.json"; then
    say "⛔ تعذّرت كتابةُ counts.json"; ready=false
  fi
  # عدُّ ما في السجلّ الخاصّ من أسطرٍ حسّاسة (يُكتب في السجلّ نفسِه قبل تشفيره — لا يُنشر)
  python3 "$PCI_CTL/guard.py" --report --label "السجلّ الخاص" "$PCI_OUT/log.txt" || true
  if [ "$ready" = true ]; then
    bash "$PCI_CTL/encrypt_log.sh" encrypt || { say "⛔ تعذّر التشفير"; ready=false; }
  fi
  if [ "$ready" = true ]; then
    python3 "$PCI_CTL/artifact_check.py" "$PCI_ART" --kind "$PCI_KIND" --leg "$PCI_LEG" --sha "$PCI_SHA" \
      ${KINDS_ARGS[@]+"${KINDS_ARGS[@]}"} || ready=false
  fi
  if [ "$ready" = true ]; then pub "الناتج: ✅ جاهزٌ للرفع"; else pub "الناتج: ⛔ غيرُ جاهزٍ — لا رفع"; fi
  [ -n "${GITHUB_OUTPUT:-}" ] && printf 'ready=%s\n' "$ready" >> "$GITHUB_OUTPUT"
  return 0
}

if [ "$MODE" != "prepare" ] && [ ! -f "$PCI_CTL/counts_schema.py" ]; then
  say "⛔ لم تُهيَّأ الأدوات (prepare لم يجرِ)"; pub "المشرف: ⛔ لا أدوات"; exit 1
fi

case "$MODE" in
  prepare)  do_prepare; exit $? ;;
  clone)    do_clone; exit $? ;;
  build)    run_phases build; exit 0 ;;
  emu)      run_phases emu; exit 0 ;;
  finalize) do_finalize; exit 0 ;;
  *)        say "⛔ طورٌ غيرُ معروف"; pub "المشرف: ⛔ أمرٌ غيرُ معروف"; exit 1 ;;
esac
