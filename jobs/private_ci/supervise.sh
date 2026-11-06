#!/usr/bin/env bash
# 🧑‍✈️ private-ci (v3) — **المشرفُ الموثوق** في وظيفة build، بمستخدم العدّاء `runner` (D11 · D12 · D13).
#    البناءُ غيرُ الموثوق يجري بمستخدمٍ آخر `pcibuild` بلا sudo، والمشرفُ وحده هو من: يهيّئ العزلَ ويختبره، ويستنسخ، ويشغّل كلَّ طورٍ
#    ويسجّل رمزَ خروجه الفعليّ، ويعدّ، ويشفّر، ويفحص — ولا يُرفع شيءٌ قبل الفحص.
#
# الأوضاع (المعامل الأوّل):
#   prepare  — ينسخ أدواتِه إلى $RUNNER_TEMP/pci/ctl (700) · يثبّت age · ينشئ pcibuild ويحصّن SDK وhostedtoolcache و/home/runner ·
#              ويضع مدخلَ التحكّم (build.sh · iso_probe.py) في /var/lib/pci-ctl بآباءٍ كلُّها لـroot (أ1) ·
#              ثمّ **يختبر العزلَ بنفسه** (rc_isolation — 21 فحصاً؛ والعشرون: جردُ المقابس يفشل مغلقاً ويُصنَّف)؛ أيُّ فشلٍ ⇒ خروجٌ غيرُ صفر فلا يبلغ المفتاحُ الاستنساخَ.
#   clone    — يشغّل clone.sh (المفتاحُ في بيئة الخطوة وحدها) ثمّ يُملِّك الشجرةَ لـpcibuild في /home/pcibuild/src.
#   build    — أطوارُ النوع قبل المحاكي (kinds.json) · emu — أطوارُه بعد إقلاع المحاكي **بمستخدم البناء** (emu_up · أ2).
#              كلُّ طورٍ في جلسةٍ خاصّة (setsid) بمهلة min(ما بقي من الميزانية)، تُقتل كلُّها عند انقضائها (أ4)، ومخرجُه إلى مصرفٍ يملكه
#              المشرفُ لا يُنتظر منه EOF.
#   finalize — (always) يقتل عمليّاتِ pcibuild ويتحقّق من زوالها (وإلا فلا تُقرأ شجرتُه · أ3) ← التملّك ← counts.json ← تقريرُ الحارس ←
#              التشفير ← artifact_check ⇒ ready=true|false. وكلُّه داخل ميزانيةٍ كلّيّةٍ ≤ FIN_RESERVE (أ4 · الإصلاح الخامس):
#              التملّكُ بمهلةٍ من الباقي، وانقضاؤها ⇒ لا تُقرأ الشجرة وready=false وسطرٌ علنيٌّ ثابت.
#   وفي التجربة dry-run-emu وحدها طورٌ موثوقٌ emu_probe (أ2 · الإصلاح الخامس): يُثبت حيّاً عزلَ ADB والمحاكي — انظر emu_probe.
#
# ⛔ لا `set -x`. وما يُطبع علناً جملٌ ثابتةٌ وأعدادٌ فقط؛ وكلُّ ما سواه إلى $RUNNER_TEMP/pci/out/log.txt (الواصفةُ 3 للعلنيّ).
set -uo pipefail

case "${GITHUB_WORKFLOW_REF:-}" in
  */.github/workflows/private-ci.yml@*) ;;
  *) echo "⛔ jobs/private_ci/supervise.sh لا يُشغَّل إلا من private-ci.yml — لا شيء نُفّذ."; exit 2 ;;
esac

# أ2-٤: وضعُ الاختبار لا يُقبل داخل Actions — وجودُه (ولو فارغاً) يُفشل الشوطَ قبل أيّ شيء؛ وعلى عدّاءٍ حقيقيٍّ لا متغيّرَ PCI_TEST_* أصلاً.
if [ "${GITHUB_ACTIONS:-}" = "true" ] && [ -n "${PCI_TEST_NO_ISOLATION+x}" ]; then
  echo "⛔ PCI_TEST_NO_ISOLATION مرفوضٌ داخل GitHub Actions — لا شيء نُفّذ."; exit 2
fi
if [ -n "${RUNNER_ENVIRONMENT:-}" ] && compgen -e | grep -q '^PCI_TEST_'; then
  echo "⛔ متغيّراتُ PCI_TEST_* مرفوضةٌ على عدّاءٍ حقيقيّ — لا شيء نُفّذ."; exit 2
fi

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
if [ "$LOCAL" = 1 ]; then BHOME="$RUNNER_TEMP/builder"; BUSER="$(id -un)"; else BHOME="/home/pcibuild"; BUSER=pcibuild; fi
BSRC="$BHOME/src"; BSTATE="$BHOME/state"; BDEV="$BHOME/device"
# أ1: مدخلُ التحكّم الذي يستدعيه المشرفُ لكلّ طور — في الإنتاج شجرةٌ يملكها root بآبائها كلّها (لا بيتُ pcibuild القابلُ لإعادة التسمية)
if [ "$LOCAL" = 1 ]; then CTL_DIR="$BHOME/ctl"; else CTL_DIR="/var/lib/pci-ctl"; fi
ADB_PORT=15037                     # أ2: منفذُ خادم ADB الخاصّ بمستخدم البناء (لا 5037 الافتراضيّ)
NPROC_MAX=4096                     # أ4: سقفُ عمليّات مستخدم البناء وخيوطه (ulimit -u) — يخفّف fork bomb
FIN_RESERVE=300                    # أ4: ثوانٍ تُحجز من الميزانية للتنظيف والعدّ والتشفير والفحص
# أ4 (الإصلاح الخامس): سقوفُ خطوات finalize بعد التملّك وهامشُ القتل لكلٍّ منها — والتملّكُ يأخذ ما بقي من FIN_RESERVE بعدها.
#   الإثبات: كلُّ خطوةٍ s تُعطى t_s = min(سقفها، FIN_END − الآن − المحجوزُ لما بعدها − FIN_KILL) وتنتهي حتماً قبل t_s + FIN_KILL
#   (timeout --kill-after) ⇒ تنتهي قبل FIN_END − المحجوزُ لما بعدها؛ فآخرُها ينتهي قبل FIN_END، وFIN_END ≤ بدءُ finalize + FIN_RESERVE.
#   المحجوزُ بعد التملّك = (45 + 45 + 100 + 20) + 4 × 5 = 230 ث ⇒ يبقى للتنظيف (≤ 3 × 5 ث انتظاراً) والتملّك ≈ 70 ث من 300.
FIN_KILL=5
FIN_CAP_COUNTS=45; FIN_CAP_GUARD=45; FIN_CAP_ENC=100; FIN_CAP_CHECK=20
OWN_TEST_SLEEP=""                  # 🧪 محلّيّاً وحده: يستبدل التملّكَ بـsleep لإثبات سلوك المهلة
KINDS_ARGS=()
PHASE_CAP=$((32 * 1024 * 1024))    # D18: سجلُّ الطور الواحد
SKIP_KILL=0
PHASE_SECS_MAX=0                   # 0 = لا سقفَ غيرُ الميزانية (لا مهلةَ خاصّةً بالطور في kinds.json)
if [ "$LOCAL" = 1 ]; then
  [ -n "${PCI_TEST_KINDS_FILE:-}" ] && KINDS_ARGS=(--kinds "$PCI_TEST_KINDS_FILE")
  [[ "${PCI_TEST_PHASE_CAP:-}" =~ ^[0-9]+$ ]] && PHASE_CAP="$PCI_TEST_PHASE_CAP"
  [ "${PCI_TEST_CLEANUP_SKIP_KILL:-}" = 1 ] && SKIP_KILL=1   # يحاكي عمليّةً لا تموت (لاختبار أ3)
  [[ "${PCI_TEST_PHASE_SECS:-}" =~ ^[0-9]+$ ]] && PHASE_SECS_MAX="$PCI_TEST_PHASE_SECS"   # مهلةٌ قصيرةٌ لاختبار أ4
  [[ "${PCI_TEST_FIN_RESERVE:-}" =~ ^[0-9]+$ ]] && FIN_RESERVE="$PCI_TEST_FIN_RESERVE"    # احتياطٌ أصغر لاختبار مهلة التملّك
  [[ "${PCI_TEST_OWN_SLEEP:-}" =~ ^[0-9]+$ ]] && OWN_TEST_SLEEP="$PCI_TEST_OWN_SLEEP"
fi
FIN_AFTER_OWN=$((FIN_CAP_COUNTS + FIN_CAP_GUARD + FIN_CAP_ENC + FIN_CAP_CHECK + 4 * FIN_KILL))
TOTAL_CAP=$((PHASE_CAP / 2 * 3))   # والكلّ 48 م.ب (1.5× الطور)

mkdir -p "$PCI_OUT" "$PCI_STATE" "$PCI_ART"
# ───── من هنا لا يخرج حرفٌ إلى السجلّ العلنيّ إلا عبر pub (الواصفة 3) ─────
exec 3>&1
exec >>"$PCI_OUT/log.txt" 2>&1

pub(){ printf '%s\n' "$*" >&3; }
say(){ printf '▸ %s [%s] %s\n' "$(date -u +%H:%M:%S)" "$MODE" "$*"; }
rc_set(){ printf '%s\n' "$2" > "$PCI_STATE/rc_$1"; }
ran_add(){ printf '%s\n' "$1" >> "$PCI_STATE/ran.txt"; }
# ⛔ python3 المشرف يجري بعد أطوارٍ غيرِ موثوقة على الآلة نفسها ⇒ `-I -S`: لا PYTHONPATH ولا site ولا .pth ولا sitecustomize من مجلّداتٍ قد تُفتح
#    (والفحصُ 12 من فحوص العزل يُثبت أنّ pcibuild لا يكتب في مسار وحداته). السكربتاتُ تستورد المكتبةَ القياسيّة وحدها أو جارَها بـsys.path صراحةً.
cs(){ python3 -I -S "$PCI_CTL/counts_schema.py" "$@" ${KINDS_ARGS[@]+"${KINDS_ARGS[@]}"}; }

# ───── بيئةُ البناء: قائمةٌ مسموحةٌ وحدها (env -i) — لا GITHUB_* غيرُ المرجع، ولا ACTIONS_*، ولا رمز ─────
BENV=()
EMU=()
CTL_BROKEN=0
make_benv(){
  local wref="" jh="${JAVA_HOME:-}" ah="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}" path="" part tag=""
  [ -f "$PCI_STATE/whisper_ref" ] && wref="$(head -c 64 "$PCI_STATE/whisper_ref" | tr -d '\n')"
  [[ "$wref" =~ ^[0-9a-f]{40}$ ]] || wref=""
  [ -f "$PCI_STATE/run_tag" ] && tag="$(head -c 64 "$PCI_STATE/run_tag" | tr -d '\n')"
  [[ "$tag" =~ ^[0-9a-f]{32}$ ]] || tag=""
  # أ2: معاملاتُ المحاكي من kinds.json الموثوق بأنماطٍ مغلقة (خمسٌ أو لا شيء)
  mapfile -t EMU < <(cs emu --kind "$PCI_KIND" --leg "$PCI_LEG" 2>/dev/null)
  [ "${#EMU[@]}" = 5 ] || EMU=("" "" "" "" "")
  for part in "${jh:+$jh/bin}" "${ah:+$ah/platform-tools}" "${ah:+$ah/cmdline-tools/latest/bin}" /usr/local/bin /usr/bin /bin; do
    [ -n "$part" ] && path="${path:+$path:}$part"
  done
  BENV=("HOME=$BHOME" "PATH=$path" "JAVA_HOME=$jh" "ANDROID_HOME=$ah" "ANDROID_SDK_ROOT=${ANDROID_SDK_ROOT:-$ah}"
        "ANDROID_USER_HOME=$BHOME/.android" "GRADLE_USER_HOME=$BHOME/.gradle"
        "GRADLE_OPTS=-Dorg.gradle.daemon=false -Dorg.gradle.console=plain"
        CI=true TERM=dumb LANG=C.UTF-8 PYTHONIOENCODING=utf-8
        "GITHUB_WORKFLOW_REF=${GITHUB_WORKFLOW_REF}" "PCI_KIND=$PCI_KIND" "PCI_LEG=$PCI_LEG" "PCI_LINT=${PCI_LINT:-false}"
        "PCI_SRC=$BSRC" "PCI_BSTATE=$BSTATE" "PCI_BDEVICE=$BDEV" "PCI_WHISPER_REF=$wref" "PCI_RUN_TAG=$tag"
        # أ2: خادمُ ADB والمحاكي بمستخدم البناء، بمنفذٍ ومجلّداتٍ خاصّةٍ به
        "ANDROID_ADB_SERVER_PORT=$ADB_PORT" "ADB_SERVER_SOCKET=tcp:127.0.0.1:$ADB_PORT"
        "ANDROID_AVD_HOME=$BHOME/.android/avd" "ANDROID_EMULATOR_HOME=$BHOME/.android"
        "PCI_EMU_API=${EMU[0]}" "PCI_EMU_TARGET=${EMU[1]}" "PCI_EMU_ARCH=${EMU[2]}" "PCI_EMU_PROFILE=${EMU[3]}" "PCI_EMU_RAM=${EMU[4]}")
}
# أ4: كلُّ أمرٍ بمستخدم البناء تحت سقف عمليّات (ulimit -u الصلب والليّن)؛ وتعذّرُ ضبطه ⇒ 126 فلا يجري الأمرُ بلا سقف
LIMIT=(/bin/bash -c 'h="$(ulimit -H -u)"; if [ "$h" = unlimited ] || [ "$h" -gt "$0" ]; then ulimit -S -H -u "$0" || exit 126; fi; exec "$@"' "$NPROC_MAX")
# ⛔ الواصفةُ 3 (السجلُّ العلنيّ) لا تُورَّث لأيّ عمليّةٍ غيرِ موثوقة (3>&-): لا كتابةَ منها إلى العلن، ولا إمساكَ لأنبوب الخطوة
#    بعد انتهائها (sudo يغلق ما فوق 2 أصلاً — وهذا دفاعٌ ثانٍ لا يعتمد على إعداده).
as_builder(){ # أمرٌ واحدٌ بمستخدم البناء ببيئته المسموحة
  if [ "$ISO" = 1 ]; then sudo -n -u pcibuild -H env -i "${BENV[@]}" "${LIMIT[@]}" "$@" 3>&-
  else env -i "${BENV[@]}" "${LIMIT[@]}" "$@" 3>&-; fi
}

# أ1: كلُّ مكوّنٍ من الجذر حتى الملفّ ليس رابطاً رمزيّاً، ويملكه root، ولا يحمل بِتَّ كتابةٍ للمجموعة ولا للغير (قناعُ ACL داخلٌ في بِتّ المجموعة).
#     لا يُفترض أنّ مجلّداً نظاميّاً محميّ (درسُ /usr/local/bin): يُفحص كلُّ مكوّنٍ بعينه.
root_chain_ok(){
  local p="$1" cur="/" part u m
  local -a comps
  [[ "$p" == /* ]] || return 1
  IFS=/ read -r -a comps <<<"${p#/}"
  for part in "" "${comps[@]}"; do
    if [ -n "$part" ]; then
      [ "$part" != "." ] && [ "$part" != ".." ] || return 1
      cur="${cur%/}/$part"
    fi
    [ -L "$cur" ] && return 1
    read -r u m < <(stat -c '%u %a' -- "$cur" 2>/dev/null) || return 1
    [ "$u" = 0 ] && [[ "$m" =~ ^[0-7]{3,4}$ ]] || return 1
    (( (8#$m & 8#022) == 0 )) || return 1
  done
}
# قبل كلّ طور: المدخلُ هو نسخةُ المشرف بعينها، وسلسلتُه (في الإنتاج) ما زالت لـroot
ctl_intact(){
  cmp -s "$PCI_CTL/build.sh" "$CTL_DIR/build.sh" || return 1
  [ "$ISO" = 1 ] || return 0
  root_chain_ok "$CTL_DIR/build.sh"
}

# ───── prepare ─────
install_age(){
  [ "$LOCAL" = 1 ] && return 0          # محلّيّاً: age النظام عبر PCI_TEST_AGE_BIN (بلا شبكة)
  bash "$PCI_CTL/encrypt_log.sh" install
}

setup_builder(){ # ⛔ يجري قبل أوّل سطرٍ غيرِ موثوق — وكلُّ خطوةٍ تفشل تُعلَن برمزٍ ثابت (كلمةٌ لا مسار) ليُعرف أين سقطت التهيئة
  local me home sdk tc="/opt/hostedtoolcache"
  sudo -n useradd --create-home --shell /bin/bash --user-group pcibuild || return "$(setup_fail useradd)"
  sudo -n chmod 700 /home/pcibuild || return "$(setup_fail home-mode)"
  me="$(id -un)"; home="$(getent passwd "$me" | cut -d: -f6)"
  sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
  [ -n "$sdk" ] && [ -d "$sdk" ] && [ -n "$home" ] && [ -d "$tc" ] || { say "⛔ لا SDK أو hostedtoolcache أو بيتُ العدّاء"; return "$(setup_fail paths)"; }
  sdk="$(readlink -f "$sdk")"
  sudo -n install -d -o pcibuild -g pcibuild -m 700 /home/pcibuild/state /home/pcibuild/device || return "$(setup_fail dirs)"
  # أ1: مدخلُ التحكّم في شجرةٍ يملكها root وكلُّ آبائها (/var/lib/pci-ctl) — لا في بيت pcibuild الذي يستطيع إعادةَ تسمية ما فيه
  #     (وقعت ثغرتُه في القراءة: مراجعة Codex 13-ج أ1). ثمّ تُفحص السلسلةُ كلُّها من الجذر.
  sudo -n install -d -o root -g root -m 0755 "$CTL_DIR" || return "$(setup_fail ctl-dir)"
  sudo -n install -o root -g root -m 0644 "$PCI_CTL/build.sh" "$PCI_CTL/iso_probe.py" "$CTL_DIR/" || return "$(setup_fail ctl-copy)"
  root_chain_ok "$CTL_DIR/build.sh" && root_chain_ok "$CTL_DIR/iso_probe.py" || return "$(setup_fail ctl-chain)"
  # أ3: لا مهامَّ مؤجّلةَ لمستخدم البناء (cron · at) تُحيي عمليّةً له بعد التنظيف
  printf 'pcibuild\n' | sudo -n tee -a /etc/cron.deny /etc/at.deny >/dev/null || return "$(setup_fail sched-deny)"
  # قراءةُ المشرف لشجرة البناء (تقاريرُ JUnit) بلا فتح البيت لغيره: ACL لمستخدم العدّاء وحدَه
  sudo -n setfacl -m "u:$me:rx" /home/pcibuild || return "$(setup_fail acl)"
  # لا مساحةَ عملٍ ولا _temp ولا _diag ولا .credentials تُقرأ من pcibuild
  sudo -n chmod 700 "$home" || return "$(setup_fail runner-home)"
  # لا sudo لغير العدّاء مهما قال sudoers (دفاعٌ في العمق): ثنائيُّ sudo (setuid) يُقصر تنفيذُه على root ومجموعةِ العدّاء الأساسيّة،
  # فلا يصل إليه pcibuild أصلاً — والفحصُ الأوّل من فحوص العزل يُثبت ذلك على العدّاء نفسِه.
  # ⛔ chown يمسح بِتَّ setuid (لينكس يعامل root كغيره هنا) ⇒ chown وchmod في استدعاء sudo **واحد**، وإلا فقد sudo نفسُه صلاحيتَه بين الأمرين
  #    (وقعت فعلاً: التجربة الجافّة 42248836438 «لم تكتمل التهيئة»).
  local sb; sb="$(command -v sudo)" && [ -n "$sb" ] || return "$(setup_fail sudo-path)"
  sudo -n sh -c 'chown "root:$2" "$1" && chmod 4750 "$1"' _ "$sb" "$(id -g)" || return "$(setup_fail sudo-restrict)"
  sudo -n true || { say "⛔ sudo تعطّل بعد تقييده"; return "$(setup_fail sudo-check)"; }
  # حقيقتان من مصدر صورة العدّاء: SDK بـa+rwx وhostedtoolcache بـ777 ⇒ تُحصَّنان (فلا يستبدل البناءُ adb/emulator/JDK)
  sudo -n chown -R -H "$me:$me" "$sdk" "$tc" || return "$(setup_fail sdk-own)"
  sudo -n chmod -R o-w "$sdk" "$tc" || return "$(setup_fail sdk-mode)"
  return 0
}
setup_fail(){ pub "التهيئة: ⛔ $1"; echo 1; }   # رمزٌ ثابتٌ علناً (لا مسارَ ولا اسمَ مستخدمٍ ولا مخرجاتِ أمر) ويعيد رمزَ الفشل للـreturn

harden_path_dirs(){ # D11: كلُّ مجلّدٍ في PATH الوظيفة **أو في مسار المشرف الثابت** يكتب فيه pcibuild يُجرَّد من كتابة الغير قبل أوّل سطرٍ غيرِ موثوق
  # (وإلا وضع فيه `bash` أو `sudo` أو `python3` مزيَّفاً تنفّذه خطواتٌ موثوقةٌ بصلاحية runner — والعدّاءُ نفسُه يبحث في PATH الوظيفة عن bash لكلّ خطوة)
  # ⛔ لا استثناءَ للمجلّدات النظاميّة: صورةُ العدّاء تجعل /usr/local/bin مفتوحاً للكتابة للجميع، وهو يسبق /usr/bin في المسارين
  #    (وقعت فعلاً: التجربة الجافّة 42250728242 — «مواضعُ PATH الكاتبة: 10,18» وسقوطُ الفحص 8). النظاميُّ يبقى لـroot ويُنزَع منه o+w فقط؛
  #    وغيرُه يُملَّك للعدّاء. والفشلُ يُعلَن بموضع المجلّد لا باسمه (كما يفعل الفحصُ 8)، ويُعاد الفحصُ بعد التحصين فلا يُكمَل على مجلّدٍ ما زال يُكتب.
  local me d rd pos=0; me="$(id -un)"
  local IFS=:
  for d in $JOB_PATH:$PATH; do
    pos=$((pos + 1))
    [ -n "$d" ] && [ -d "$d" ] || continue
    rd="$(readlink -f "$d")"
    as_builder test -w "$rd" >/dev/null 2>&1 || continue
    case "$rd" in
      /|/usr|/usr/local|/opt|/home|/snap) sudo -n chmod o-w "$rd" ;;   # جذرٌ واسع: المجلّدُ نفسُه فقط (لا تجوّلَ فيه)
      /usr/bin|/usr/sbin|/bin|/sbin|/usr/local/bin|/usr/local/sbin|/usr/games|/usr/local/games|/snap/bin) sudo -n chmod -R o-w "$rd" ;;   # نظاميٌّ: يبقى لـroot
      *) sudo -n chown -R -H "$me:$me" "$rd" && sudo -n chmod -R o-w "$rd" ;;
    esac || { pub "التهيئة: ⛔ path-harden $pos"; return 1; }
    if as_builder test -w "$rd" >/dev/null 2>&1; then pub "التهيئة: ⛔ path-still-writable $pos"; return 1; fi
  done
  return 0
}

isolation_tests(){ # يضبط ISO_N وISO_OK؛ ما يجب أن يفشل وما يجب أن ينجح
  local sdk tc="/opt/hostedtoolcache" me home
  me="$(id -un)"; home="$(getent passwd "$me" | cut -d: -f6)"
  sdk="$(readlink -f "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}")"
  ISO_N=0; ISO_OK=0; ISO_BAD=""   # ISO_BAD: أرقامُ الفحوص الفاشلة (أعدادٌ فقط — تُعلَن ليُعرف أيُّ فحصٍ سقط بلا كشف شيء)
  iso_bad(){ ISO_BAD="${ISO_BAD:+$ISO_BAD,}$ISO_N"; }
  must_fail(){ ISO_N=$((ISO_N + 1)); if as_builder "$@" >/dev/null 2>&1; then say "⛔ عزلٌ مفقود (فحصٌ $ISO_N)"; iso_bad; else ISO_OK=$((ISO_OK + 1)); fi; }
  must_pass(){ ISO_N=$((ISO_N + 1)); if as_builder "$@" >/dev/null 2>&1; then ISO_OK=$((ISO_OK + 1)); else say "⛔ وصولٌ لازمٌ مفقود (فحصٌ $ISO_N)"; iso_bad; fi; }
  must_fail bash -c 'sudo -n true'
  must_fail bash -c 'cat "/proc/$1/environ"' _ "$$"
  must_fail bash -c 'ls "$1"' _ "$RUNNER_TEMP"
  must_fail bash -c 'touch "$1/.pci_probe"' _ "$sdk"
  must_fail bash -c 'touch "$1/.pci_probe"' _ "$tc"
  must_fail test -r "$PCI_CTL/supervise.sh"
  must_fail bash -c 'ls "$1"' _ "$home"
  # لا مجلّدَ في PATH الوظيفة (ولا في مسار المشرف) يكتب فيه pcibuild
  ISO_N=$((ISO_N + 1)); local d bad=0 pos=0 badpos=""; local IFS=:
  for d in $JOB_PATH:$PATH; do
    pos=$((pos + 1))
    [ -n "$d" ] && [ -d "$d" ] || continue
    if as_builder test -w "$d" >/dev/null 2>&1; then bad=1; badpos="${badpos:+$badpos,}$pos"; say "⛔ مجلّدٌ في PATH يكتب فيه pcibuild: $d"; fi
  done
  IFS=$' \t\n'
  if [ "$bad" = 0 ]; then ISO_OK=$((ISO_OK + 1)); else say "⛔ مجلّدٌ في PATH يكتب فيه pcibuild (فحصٌ $ISO_N)"; iso_bad; pub "العزل: مواضعُ PATH الكاتبة: $badpos"; fi
  must_pass bash -c 'test -x "$JAVA_HOME/bin/java"'
  must_pass bash -c 'test -r "$ANDROID_HOME" && ls "$ANDROID_HOME" >/dev/null'
  must_pass bash -c 'touch "$HOME/.probe"'
  # 12) مسارُ وحدات python3 الذي يستعمله المشرفُ بعد البناء (بـ-I -S) لا يكتب فيه pcibuild — وبصرامة: غيابُ python3 عنه فشلٌ لا نجاح
  must_fail bash -c 'python3 -I -S -c "import os,sys; sys.exit(0 if any(os.access(p, os.W_OK) for p in sys.path if p and os.path.isdir(p)) else 3)"; [ $? -ne 3 ]'
  # ───── أ1: مدخلُ التحكّم لا يُستبدل (13–17) ─────
  # 13) السلسلةُ من الجذر حتى المدخلَين: root · بلا كتابةٍ للمجموعة والغير · بلا روابط
  ISO_N=$((ISO_N + 1)); if root_chain_ok "$CTL_DIR/build.sh" && root_chain_ok "$CTL_DIR/iso_probe.py"; then ISO_OK=$((ISO_OK + 1)); else say "⛔ سلسلةُ مدخل التحكّم (فحصٌ $ISO_N)"; iso_bad; fi
  must_fail bash -c 'mv -f "$1/build.sh" "$1/.pci_probe"' _ "$CTL_DIR"         # 14) إعادةُ تسمية المدخل
  must_fail bash -c 'ln -s /bin/true "$1/.pci_probe"' _ "$CTL_DIR"             # 15) إنشاءُ بديلٍ في مكانه
  must_fail bash -c 'mv -f "$1" "$1.pci_probe"' _ "$CTL_DIR"                   # 16) إعادةُ تسمية الشجرة نفسِها
  must_fail bash -c 'printf x >> "$1/build.sh"' _ "$CTL_DIR"                   # 17) الكتابةُ فيه
  # ───── أ2: لا جسرَ من حدّ البناء إلى حساب العدّاء (18–20) ─────
  # 18) لا عمليّةَ adb ولا emulator ولا qemu بحسابٍ غيرِ مستخدم البناء (يُعاد بعد آخر طورٍ في finalize)
  ISO_N=$((ISO_N + 1)); local fe; fe="$(foreign_emu_procs)"
  if [ "$fe" = 0 ]; then ISO_OK=$((ISO_OK + 1)); else say "⛔ ADB/المحاكي بحسابٍ آخر (فحصٌ $ISO_N)"; iso_bad; fi
  # 19) الاتّصالُ بمقبس Docker يُرفض بالصلاحية (أو لا مقبس)
  must_pass python3 -I -S "$CTL_DIR/iso_probe.py" deny /var/run/docker.sock
  # 20) لا مقبسَ Unix لحساب العدّاء (أو لمجموعاته) في /run و/tmp و/var/run — ولا مجرّداً تمسكه عمليّاتُه — يقبل اتّصالَ pcibuild
  #     الإصلاحُ الخامس: الجردُ الناقصُ فشلٌ لا صفر، والنتيجةُ مصنَّفة (مرفوضٌ بالصلاحية وحده مقبول) — السطرُ العلنيُّ أعدادٌ لا أسماء
  ISO_N=$((ISO_N + 1))
  if sock_probe; then ISO_OK=$((ISO_OK + 1)); else say "⛔ مقبسٌ لحساب العدّاء غيرُ مرفوضٍ بالصلاحية أو جردٌ ناقص (فحصٌ $ISO_N)"; iso_bad; fi
  pub "العزل: $SOCK_PUB"
  # ───── أ4: سقفُ العمليّات مفروضٌ فعلاً على مستخدم البناء (21) ─────
  must_pass bash -c 'l="$(ulimit -u)"; [ "$l" != unlimited ] && [ "$l" -le "$1" ]' _ "$NPROC_MAX"
  rm -f "$sdk/.pci_probe" "$tc/.pci_probe" 2>/dev/null || true   # لو نجحت كتابةٌ (عزلٌ مفقود) لا يبقى أثر
  sudo -n rm -f "$CTL_DIR/.pci_probe" 2>/dev/null || true
}

# أ2: أعدادُ عمليّات adb وemulator/qemu — «adb بمستخدم البناء · المحاكي بمستخدم البناء · أيٌّ منها بحسابٍ آخر» (أعدادٌ لا أسماء).
#     الإصلاحُ الخامس: سطرُ إثباتٍ موجب (مسبارُ المحاكي يشترط ≥1 لمستخدم البناء أثناء التشغيل) لا الصفرَ الأجنبيَّ وحده.
#     وتعذّرُ ps أو معرفةِ مستخدم البناء ⇒ «? ? 1» فلا يُقرأ صفراً.
emu_procs(){
  local bu out
  if [ "$ISO" = 1 ]; then bu="$(id -u pcibuild 2>/dev/null)" || { echo "? ? 1"; return; }; else bu="$(id -u)"; fi
  out="$(ps -eo uid=,comm= 2>/dev/null)" || { echo "? ? 1"; return; }
  awk -v bu="$bu" '$2 == "adb" && $1 == bu {a++} $2 ~ /^(emulator.*|qemu-system.*)$/ && $1 == bu {e++}
                   $2 ~ /^(adb|emulator.*|qemu-system.*)$/ && $1 != bu {f++} END {print a+0, e+0, f+0}' <<<"$out"
}
foreign_emu_procs(){ local a e f; read -r a e f <<<"$(emu_procs)"; echo "$f"; }

# أ2: يجرد المشرفُ (root) مقابسَ حساب العدّاء ثمّ يحاول pcibuild الاتّصالَ بكلٍّ منها. يضبط SOCK_PUB (سطرٌ علنيٌّ من أعدادٍ وجملٍ ثابتة).
#     الإصلاحُ الخامس (Codex 14): الجردُ يفشل مغلقاً (scan يخرج 3 ويكتب سجلَّ حالةٍ أخيراً) · والنتيجةُ مصنَّفة:
#     يُقبل فقط إن كان accepted=refused=other=0 والجردُ تامّاً (وزالت = سباقٌ يُعدّ ولا يُفشل).
SOCK_RX='^n=([0-9]+) accepted=([0-9]+) denied=([0-9]+) refused=([0-9]+) vanished=([0-9]+) other=([0-9]+) inventory=(complete|incomplete)$'
run_root(){ if [ "$ISO" = 1 ]; then sudo -n "$@"; else "$@"; fi; }
sock_probe(){
  local list="$PCI_STATE/socks" r rc src inv
  SOCK_PUB="مقابسُ حساب العدّاء: ⛔ تعذّر الجرد"
  run_root python3 -I -S "$CTL_DIR/iso_probe.py" scan --uid "$(id -u)" --gids "$(id -G | tr ' ' ,)" \
    --skip-gids "$(id -G "$BUSER" | tr ' ' ,)" > "$list"; src=$?
  if [ "$src" != 0 ] && [ "$src" != 3 ]; then rm -f "$list"; return 1; fi
  r="$(as_builder python3 -I -S "$CTL_DIR/iso_probe.py" connect < "$list" 2>/dev/null)"; rc=$?
  rm -f "$list"
  [[ "$r" =~ $SOCK_RX ]] || { SOCK_PUB="مقابسُ حساب العدّاء: ⛔ نتيجةٌ غيرُ مقروءة"; return 1; }
  inv="${BASH_REMATCH[7]}"
  [ "$src" = 3 ] && inv=incomplete     # المصدرُ نفسُه قال «ناقص» ⇒ ناقصٌ ولو خالفه سجلُّ الحالة
  SOCK_PUB="مقابسُ حساب العدّاء: ${BASH_REMATCH[1]} فُحصت · مرفوضةٌ بالصلاحية ${BASH_REMATCH[3]} · بلا مُنصِت ${BASH_REMATCH[4]}"
  SOCK_PUB+=" · زالت ${BASH_REMATCH[5]} · أخرى ${BASH_REMATCH[6]} · قَبِلت ${BASH_REMATCH[2]}"
  if [ "$inv" = complete ]; then SOCK_PUB+=" · الجردُ تامّ"; else SOCK_PUB+=" · ⛔ الجردُ ناقص"; fi
  [ "$rc" = 0 ] && [ "$src" = 0 ] && [ "$inv" = complete ] && [ "${BASH_REMATCH[2]}" = 0 ] \
    && [ "${BASH_REMATCH[4]}" = 0 ] && [ "${BASH_REMATCH[6]}" = 0 ]
}

do_prepare(){
  local t0; t0="$(date +%s)"     # أ4: بدءُ ميزانية الشوط — تشمل التهيئةَ والاستنساخ والأطوار والتنظيف والتشفير
  rm -rf "$PCI_CTL"
  mkdir -p "$PCI_CTL" && chmod 700 "$PCI_CTL" && cp -a "$HERE/." "$PCI_CTL/" && chmod -R go-rwx "$PCI_CTL" || { pub "العزل: ⛔ تعذّر نسخُ الأدوات"; return 1; }
  : > "$PCI_STATE/ran.txt"
  : > "$PCI_STATE/pgids"
  od -An -N16 -tx1 /dev/urandom | tr -d ' \n' > "$PCI_STATE/run_tag"
  if [ "$LOCAL" = 1 ]; then   # محلّيّاً بيتُ البناء تحت RUNNER_TEMP؛ وفي Actions ينشئه sudo install في setup_builder
    mkdir -p "$BSTATE" "$BDEV" && install -D -m 0644 "$PCI_CTL/build.sh" "$CTL_DIR/build.sh" \
      && install -m 0644 "$PCI_CTL/iso_probe.py" "$CTL_DIR/iso_probe.py" || return 1
  fi
  # أ4: الميزانيةُ = مهلةُ الشوط في kinds.json من بدء المشرف؛ والأطوارُ تنتهي قبلها بـFIN_RESERVE فيبقى لـfinalize وقتُه.
  #     ووظيفةُ build في السير مهلتُها الميزانيةُ + هامشٌ (plan.py: job_timeout) فيسبق finalize دائماً إيقافَ GitHub.
  local tm; tm="$(cs timeout --kind "$PCI_KIND" --leg "$PCI_LEG")"
  [[ "$tm" =~ ^[0-9]+$ ]] || tm=30
  echo $(( t0 + tm * 60 )) > "$PCI_STATE/budget_end"
  echo $(( t0 + tm * 60 - FIN_RESERVE )) > "$PCI_STATE/deadline"
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
    [ "$rc" = 0 ] && pub "العزل: ✅ $ISO_OK/$ISO_N" || pub "العزل: ⛔ $ISO_OK/$ISO_N — الفاشل: ${ISO_BAD:-?}"
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
  # أ4: الاستنساخُ داخل الميزانية (حتى 15 دقيقة)؛ وTERM يوقظ trap في clone.sh فيمحو المفتاحَ ويتحقّق
  local dl secs; dl="$(cat "$PCI_STATE/deadline" 2>/dev/null)"; [[ "$dl" =~ ^[0-9]+$ ]] || dl=0
  secs=$((dl - $(date +%s))); [ "$secs" -gt 900 ] && secs=900
  if [ "$secs" -lt 30 ]; then say "⏱ لا وقتَ للاستنساخ"; ran_add clone; rc_set clone 124; pub "الاستنساخ: ⛔"; return 1; fi
  timeout --kill-after=20s "${secs}s" bash "$PCI_CTL/clone.sh"; local rc=$?
  [ "$rc" = 0 ] || rc_set clone "$rc"
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

emu_sysimg(){ # أ2: أمرٌ ثابتٌ بحساب العدّاء — أوّلَ أطوار النوع، قبل أيّ سطرٍ غيرِ موثوق — يثبّت صورةَ النظام في SDK المحصَّن
  local secs="$1" sdk sdkm
  [ -n "${EMU[0]}" ] || { say "⛔ لا معاملاتِ محاكٍ لهذا الشوط"; return 1; }
  sdk="$(readlink -f "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}")"; sdkm="$sdk/cmdline-tools/latest/bin/sdkmanager"
  [ -x "$sdkm" ] || return 1
  mkdir -p "$PCI_ROOT/tmp" || return 1
  SDKMANAGER_OPTS="-Djava.io.tmpdir=$PCI_ROOT/tmp" timeout "${secs}s" "$sdkm" --install \
    "system-images;android-${EMU[0]};${EMU[1]};${EMU[2]}" "emulator" "platform-tools" < <(yes 2>/dev/null) 3>&- || return 1
  chmod -R go-w "$sdk/system-images" "$sdk/emulator" "$sdk/platform-tools"   # ما ثُبّت يبقى للعدّاء بلا كتابةٍ لغيره
}

# ───── أ2 (الإصلاح الخامس · قياسُ Codex 14): مسبارُ المحاكي الحيّ — طورٌ موثوقٌ بحساب العدّاء في التجربة dry-run-emu وحدها ─────
# يجري بعد emu_up (المحاكي وخادمُ ADB بمستخدم البناء) ويُثبت علناً بأعدادٍ وجملٍ ثابتة (لا مسارَ ولا اسمَ مستخدمٍ غيرَ pcibuild/runner):
#   أ. هويّةُ العمليّات أثناء التشغيل: adb والمحاكي/qemu بمستخدم البناء (≥1 لكلٍّ) · وبحسابٍ آخر 0 · ولا مُنصِتَ على 5037 الافتراضيّ.
#   ب. التحكّمُ من مستخدم البناء ناجح: getprop sys.boot_completed = 1 · وجولةُ shell echo.
#   ج. العبورُ يفشل بالصلاحية: وسيطٌ تمثيليٌّ (مقبسٌ مُنصِتٌ 0600 لحساب العدّاء) ومقبسٌ «حسّاسٌ ممثَّل» بمجموعة docker (0660)
#      ⇒ deny --exists من مستخدم البناء = EACCES؛ ومن سلسلة ADB نفسِها: adb reverse إلى الوسيط ثمّ اتّصالٌ من داخل الجهاز ⇒
#      لا تصل علامتُه، بينما يصل ضابطٌ موجبٌ (مقبسٌ لمستخدم البناء نفسِه عبر القناة نفسِها) فيثبت أنّ القناةَ تعمل.
#   د. إعادةُ الجرد أثناء التشغيل وبعد قتل المحاكي: sock_probe (جردٌ ناقص = فشل) وعدُّ العمليّات.
# ⛔ لا يُشغَّل adb هنا إلا بمستخدم البناء (badb ⇒ as_builder) — والمشرفُ لا يستدعي adb بحسابه أبداً (يفرضه test_shell).
BROKER_MARK="PCI-BROKER-MARK"; CONTROL_MARK="PCI-CONTROL-MARK"
# مستمعٌ صغير: يربط المقبسَ بالصيغة المطلوبة ويردّ على كلّ اتّصالٍ بعلامته ثمّ يغلق (المكتبةُ القياسيّة وحدها)
LISTENER='import os, socket, sys
p, mode, mark = sys.argv[1], int(sys.argv[2], 8), sys.argv[3].encode() + b"\n"
os.umask(0o177)
s = socket.socket(socket.AF_UNIX)
s.bind(p)
os.chmod(p, mode)
s.listen(16)
open(p + ".ready", "w").close()
while True:
    c, _ = s.accept()
    try:
        c.sendall(mark)
    except OSError:
        pass
    c.close()'
# أداةُ الجسر بمستخدم البناء وحده (خادمُه على ADB_PORT) — $1 المهلة
badb(){ local t="$1"; shift; as_builder timeout "$t" adb -s emulator-5554 "$@"; }
# $1 الملفّ · $2 «builder» إن كان في بيت البناء (يُفحص بمستخدم البناء) — انتظارٌ محدودٌ بعشر ثوانٍ
wait_file(){ local i; for i in $(seq 1 50); do
  if [ "${2:-}" = builder ]; then as_builder test -e "$1" && return 0; else [ -e "$1" ] && return 0; fi; sleep 0.2; done; return 1; }
ge1(){ [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ]; }

emu_probe(){ # $1 الثواني
  local secs="$1" n=0 ok=0 bad="" a e f l5 lp boot echo_r bdir broker sens grp cpid bpid spid tool t out ctl_ok=0 thru=0
  ep(){ n=$((n + 1)); if "$@"; then ok=$((ok + 1)); else bad="${bad:+$bad,}$n"; fi; }
  # أ. هويّةُ العمليّات أثناء التشغيل
  read -r a e f <<<"$(emu_procs)"
  pub "المحاكي: أثناء التشغيل — adb بمستخدم البناء $a · المحاكي/qemu بمستخدم البناء $e · ADB/المحاكي بحسابٍ آخر $f"
  ep ge1 "$a"; ep ge1 "$e"; ep [ "$f" = 0 ]
  l5="$(ss -Hltn 'sport = :5037' 2>/dev/null | wc -l)"; lp="$(ss -Hltn "sport = :$ADB_PORT" 2>/dev/null | wc -l)"
  pub "المحاكي: مُنصِتو منفذ ADB الافتراضيّ 5037: $l5 · ومنفذُ مستخدم البناء الخاصّ: $lp"
  ep [ "$l5" = 0 ]; ep ge1 "$lp"
  # ب. التحكّمُ من مستخدم البناء
  boot="$(badb 30 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n')"
  echo_r="$(badb 30 shell echo pci-echo-ok 2>/dev/null | tr -d '\r\n')"
  pub "المحاكي: التحكّمُ من مستخدم البناء — الإقلاع $([ "$boot" = 1 ] && echo ✅ || echo ⛔) · جولةُ shell echo $([ "$echo_r" = pci-echo-ok ] && echo ✅ || echo ⛔)"
  ep [ "$boot" = 1 ]; ep [ "$echo_r" = pci-echo-ok ]
  # ج. العبور: وسيطٌ وحسّاسٌ ممثَّلٌ بحساب العدّاء في /tmp (مجلّدٌ 755 يعبره أيُّ حساب — فالرفضُ من أذونات المقبس نفسِه لا من المسار)
  bdir="$(mktemp -d /tmp/pci-broker.XXXXXX)" && chmod 755 "$bdir" || { pub "المحاكي: ⛔ تعذّر إنشاءُ مجلّد الوسيط"; ep false; }
  broker="$bdir/broker.sock"; sens="$bdir/sensitive.sock"
  setsid timeout "$secs" python3 -I -S -c "$LISTENER" "$broker" 600 "$BROKER_MARK" > /dev/null 2>&1 < /dev/null 3>&- &
  bpid=$!
  setsid timeout "$secs" python3 -I -S -c "$LISTENER" "$sens" 660 "$BROKER_MARK" > /dev/null 2>&1 < /dev/null 3>&- &
  spid=$!
  wait_file "$broker.ready" && wait_file "$sens.ready" || { pub "المحاكي: ⛔ لم يُنصت الوسيط"; ep false; }
  grp="بلا مجموعة docker (يُكتفى بأذونات 0660 لمجموعة العدّاء وبمقبس Docker الحقيقيّ)"
  if id -nG | tr ' ' '\n' | grep -qx docker && chgrp docker "$sens" 2>/dev/null; then grp="بمجموعة docker"; fi
  # ضابطٌ موجب: الوسيطُ حيٌّ ويقبل اتّصالَ حساب العدّاء نفسِه (deny يخرج 1 = قَبِل) — وإلا فرفضُ مستخدم البناء لا يثبت شيئاً
  python3 -I -S "$CTL_DIR/iso_probe.py" deny "$broker" --exists; t=$?
  python3 -I -S "$CTL_DIR/iso_probe.py" deny "$sens" --exists; out=$?
  pub "المحاكي: الوسيطُ والحسّاسُ الممثَّل ($grp) يقبلان حسابَ العدّاء — $([ "$t" = 1 ] && [ "$out" = 1 ] && echo ✅ || echo ⛔)"
  ep [ "$t" = 1 ]; ep [ "$out" = 1 ]
  as_builder python3 -I -S "$CTL_DIR/iso_probe.py" deny "$broker" --exists >/dev/null 2>&1; t=$?
  as_builder python3 -I -S "$CTL_DIR/iso_probe.py" deny "$sens" --exists >/dev/null 2>&1; out=$?
  as_builder python3 -I -S "$CTL_DIR/iso_probe.py" deny /var/run/docker.sock >/dev/null 2>&1; l5=$?
  pub "المحاكي: من مستخدم البناء — الوسيط $([ "$t" = 0 ] && echo 'مرفوضٌ بالصلاحية ✅' || echo ⛔) · الحسّاسُ الممثَّل $([ "$out" = 0 ] && echo 'مرفوضٌ بالصلاحية ✅' || echo ⛔) · مقبسُ Docker $([ "$l5" = 0 ] && echo ✅ || echo ⛔)"
  ep [ "$t" = 0 ]; ep [ "$out" = 0 ]; ep [ "$l5" = 0 ]
  # الضابطُ عبر ADB: مقبسٌ لمستخدم البناء في بيته (الخادمُ نفسُه يصله) — يثبت أنّ القناةَ وأداةَ الجهاز تعملان
  as_builder setsid timeout "$secs" python3 -I -S -c "$LISTENER" "$BHOME/pci-control.sock" 600 "$CONTROL_MARK" > /dev/null 2>&1 < /dev/null &
  cpid=$!
  wait_file "$BHOME/pci-control.sock.ready" builder || pub "المحاكي: ⛔ لم يُنصت الضابط"
  badb 30 reverse tcp:9998 "localfilesystem:$BHOME/pci-control.sock" >/dev/null 2>&1
  badb 30 reverse tcp:9999 "localfilesystem:$broker" >/dev/null 2>&1; t=$?
  pub "المحاكي: تسجيلُ التحويل العكسيّ (reverse) إلى الوسيط — $([ "$t" = 0 ] && echo 'قُبل التسجيل (والحكمُ للاتّصال)' || echo 'رُفض التسجيل')"
  tool=""
  for t in "toybox nc" "nc" "busybox nc"; do
    # stdin مفتوحٌ ثانيتين (لا EOF فوريّ يُنهي بعضَ النسخ قبل القراءة)؛ والمستمعُ يكتب علامتَه ويغلق فينتهي nc
    out="$(badb 25 shell "sleep 2 | $t 127.0.0.1 9998" 2>/dev/null | tr -d '\r')"
    if grep -qx "$CONTROL_MARK" <<<"$out"; then tool="$t"; ctl_ok=1; break; fi
  done
  if [ -n "$tool" ]; then
    out="$(badb 25 shell "sleep 2 | $tool 127.0.0.1 9999" 2>/dev/null | tr -d '\r')"
    grep -q "$BROKER_MARK" <<<"$out" && thru=1
    pub "المحاكي: العبورُ من داخل الجهاز عبر ADB ($tool) — الضابطُ وصل ✅ · علامةُ الوسيط $([ "$thru" = 0 ] && echo 'لم تصل ✅' || echo 'وصلت ⛔ جسرٌ قائم')"
  else
    pub "المحاكي: ⚠ لا أداةَ اتّصالٍ داخل الجهاز وصلت بالضابط (toybox nc · nc · busybox nc) — يُكتفى بإثبات الجهة المضيفة أعلاه"
  fi
  ep [ "$thru" = 0 ]
  badb 30 reverse --remove-all >/dev/null 2>&1
  # د. الجردُ أثناء التشغيل (والوسيطان حيّان: يجب أن يُجردا ويُرفضا بالصلاحية)
  if sock_probe; then t=0; else t=1; fi
  pub "المحاكي: أثناء التشغيل — $SOCK_PUB"
  ep [ "$t" = 0 ]
  # ثمّ يُقتل المحاكي وخادمُ ADB (بمستخدم البناء) ويُعاد العدُّ والجرد
  badb 60 emu kill >/dev/null 2>&1
  as_builder timeout 30 adb kill-server >/dev/null 2>&1
  for t in $(seq 1 60); do read -r a e f <<<"$(emu_procs)"; [ "$a" = 0 ] && [ "$e" = 0 ] && break; sleep 1; done
  read -r a e f <<<"$(emu_procs)"
  pub "المحاكي: بعد الإيقاف — adb بمستخدم البناء $a · المحاكي/qemu بمستخدم البناء $e · بحسابٍ آخر $f"
  ep [ "$a" = 0 ]; ep [ "$e" = 0 ]; ep [ "$f" = 0 ]
  kill "$bpid" "$spid" 2>/dev/null; wait "$bpid" "$spid" 2>/dev/null
  if sock_probe; then t=0; else t=1; fi
  pub "المحاكي: بعد الإيقاف — $SOCK_PUB"
  ep [ "$t" = 0 ]
  rm -rf "$bdir"
  if [ "$ok" = "$n" ]; then pub "مسبارُ المحاكي: ✅ $ok/$n"; return 0; fi
  pub "مسبارُ المحاكي: ⛔ $ok/$n — الفاشل: $bad"; return 1
}

run_runner_phase(){ # أوامرُ ثابتةٌ في المشرف — لا سكربتَ من الخاصّ بصلاحيّة
  case "$1" in
    disk)      ( free_disk ) ;;
    kvm)       ( enable_kvm ) ;;
    sysimg)    ( emu_sysimg "$2" ) ;;
    emu_probe) ( emu_probe "$2" ) ;;
    *)         return 2 ;;
  esac
}

# حيٌّ (لا زومبي)؟ — بقراءة /proc لا بإشارة (عمليّةُ sudo لـroot لا يُرسل إليها المشرفُ إشارة)
alive(){ local st; st="$(sed -n 's/^.*) \(.\).*$/\1/p' "/proc/$1/stat" 2>/dev/null)"; [ -n "$st" ] && [ "$st" != Z ]; }

# معرّفاتُ عمليّات البناء في الوضع المحلّيّ (لا مستخدمَ منفصلاً): كلُّ عمليّةٍ تحمل وسمَ هذا الشوط في بيئتها
tagged_pids(){
  local tag; tag="$(cat "$PCI_STATE/run_tag" 2>/dev/null)"; [[ "$tag" =~ ^[0-9a-f]{32}$ ]] || return 0
  grep -lsaF "PCI_RUN_TAG=$tag" /proc/[0-9]*/environ 2>/dev/null | cut -d/ -f3 | grep -vx "$$" || true
}
builder_count(){
  if [ "$ISO" = 1 ]; then pgrep -u pcibuild 2>/dev/null | wc -l; else tagged_pids | wc -l; fi
}
kill_builder(){ # $1 الإشارة — كلُّ عمليّات مستخدم البناء (بالحساب لا بالجلسة: الهاربُ بـsetsid لا ينجو)
  if [ "$ISO" = 1 ]; then sudo -n pkill "-$1" -u pcibuild >/dev/null 2>&1
  else local p; for p in $(tagged_pids); do kill "-$1" "$p" 2>/dev/null; done; fi
  return 0
}
kill_phase(){ # $1 الإشارة · $2 قائدُ جلسة الطور — الجلسةُ كلُّها ثمّ كلُّ ما بقي لمستخدم البناء
  if [ "$ISO" = 1 ]; then sudo -n kill -s "$1" -- "-$2" >/dev/null 2>&1; else kill -s "$1" -- "-$2" 2>/dev/null; fi
  kill_builder "$1"
}

run_builder_phase(){ # $1 المعرّف · $2 الثواني المتاحة — أ4: جلسةٌ خاصّة · مهلةٌ تقتل الجلسةَ كلَّها · مصرفٌ لا يُنتظر منه EOF
  local id="$1" secs="$2" out="$PCI_OUT/phase_$1.txt" fifo="$PCI_STATE/pipe_$1" used cap rc sz ppid dpid end to=0 i
  used="$(cat "$PCI_STATE/log_bytes" 2>/dev/null)"; [[ "$used" =~ ^[0-9]+$ ]] || used=0
  cap="$PHASE_CAP"; [ $((TOTAL_CAP - used)) -lt "$cap" ] && cap=$((TOTAL_CAP - used)); [ "$cap" -lt 0 ] && cap=0
  # أ1: المدخلُ هو نسخةُ المشرف بعينها وسلسلتُه لـroot — وإلا فلا يُستدعى (ولا يُسجَّل نجاحُ طورٍ لم يُنفَّذ غلافُه الأصليّ)
  if ! ctl_intact; then say "⛔ مدخلُ التحكّم تغيّر قبل طور $id"; CTL_BROKEN=1; return 126; fi
  rm -f "$fifo"; mkfifo -m 600 "$fifo" || return 125
  : > "$out"
  # المصرفُ: عمليّةٌ للمشرف في جلستها تكتب حتى الحدّ ثمّ تُهمل الفائض (فلا يموت البناءُ بـSIGPIPE)
  setsid bash -c 'head -c "$1" > "$2"; exec cat > /dev/null' _ "$cap" "$out" < "$fifo" 3>&- &
  dpid=$!
  if [ "$ISO" = 1 ]; then
    ( cd "$BHOME" && exec setsid sudo -n -u pcibuild -H env -i "${BENV[@]}" "${LIMIT[@]}" /bin/bash "$CTL_DIR/build.sh" phase "$id" ) \
      > "$fifo" 2>&1 < /dev/null 3>&- &
  else
    ( cd "$BHOME" && exec setsid env -i "${BENV[@]}" "${LIMIT[@]}" /bin/bash "$CTL_DIR/build.sh" phase "$id" ) \
      > "$fifo" 2>&1 < /dev/null 3>&- &
  fi
  ppid=$!
  echo "$ppid" >> "$PCI_STATE/pgids"
  end=$(( $(date +%s) + secs ))
  while alive "$ppid"; do
    if [ "$(date +%s)" -ge "$end" ]; then to=1; break; fi
    sleep 0.2
  done
  if [ "$to" = 1 ]; then
    say "⏱ انقضت مهلةُ طور $id — تُقتل جلستُه وكلُّ عمليّات البناء"
    kill_phase TERM "$ppid"
    for i in $(seq 1 50); do alive "$ppid" || break; sleep 0.2; done
    kill_phase KILL "$ppid"
  fi
  for i in $(seq 1 50); do alive "$ppid" || break; sleep 0.2; done   # انتظارٌ محدود (لا تعليقَ على عمليّةٍ لا تموت)
  if alive "$ppid"; then rc=124; say "⛔ طور $id لم ينتهِ بعد القتل"; else wait "$ppid"; rc=$?; fi
  [ "$to" = 1 ] && rc=124
  # لا يُنتظر EOF: عمليّةٌ خلفيّةٌ قد تمسك الأنبوب ⇒ مهلةٌ قصيرةٌ ثمّ يُقتل المصرف
  for i in $(seq 1 25); do kill -0 "$dpid" 2>/dev/null || break; sleep 0.2; done
  kill -KILL -- "-$dpid" 2>/dev/null; kill -KILL "$dpid" 2>/dev/null; wait "$dpid" 2>/dev/null
  rm -f "$fifo"
  sz="$(stat -c %s "$out" 2>/dev/null || echo 0)"
  cat "$out" >> "$PCI_OUT/log.txt" 2>/dev/null; rm -f "$out"
  [ "$sz" -ge "$cap" ] && printf '%s\n' "▸ ⛔ بُتر سجلُّ الطور $id عند الحدّ"
  echo $((used + sz)) > "$PCI_STATE/log_bytes"
  return "$rc"
}

run_phases(){ # $1: build | emu
  local mode="$1" line id who rc secs deadline total=0 failed=0 halt=0
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
    if [ "$halt" = 1 ]; then rc_set "$id" 126; failed=$((failed + 1)); continue; fi
    secs=$((deadline - $(date +%s)))
    if [ "$secs" -lt 60 ]; then
      say "⏱ لا وقتَ كافياً لطور $id"; rc_set "$id" 124; failed=$((failed + 1)); continue
    fi
    [ "$PHASE_SECS_MAX" -gt 0 ] && [ "$secs" -gt "$PHASE_SECS_MAX" ] && secs="$PHASE_SECS_MAX"   # min(مهلته، ما بقي)
    say "← $id ($who)"
    case "$who" in
      runner)  run_runner_phase "$id" "$secs"; rc=$? ;;
      builder) run_builder_phase "$id" "$secs"; rc=$? ;;
      *)       rc=2 ;;
    esac
    rc_set "$id" "$rc"
    say "→ $id = $rc"
    [ "$rc" != 0 ] && failed=$((failed + 1))
    if [ "$CTL_BROKEN" = 1 ] && [ "$halt" = 0 ]; then halt=1; pub "الأطوار: ⛔ مدخلُ التحكّم تغيّر — لا يُستدعى بعده طور"; fi
  done
  pub "الأطوار ($mode): $total · فاشلة $failed"
  return 0
}

# ───── finalize ─────
do_cleanup(){ # مستخدمٌ مؤقّتٌ على آلةٍ تُتلَف — لا علاقةَ له بقاعدة «لا إنهاءَ بالجملة» على جهاز المالك
  # أ3: TERM ثمّ KILL (مرّتين) بانتظارٍ محدودٍ بعد كلٍّ ثمّ إعادةُ العدّ؛ وأيُّ عمليّةٍ باقيةٍ ⇒ فشلٌ يمنع قراءةَ الشجرة
  ran_add cleanup
  local rc=0 n sig i
  [ "$ISO" = 1 ] && sudo -n crontab -r -u pcibuild >/dev/null 2>&1
  for sig in TERM KILL KILL; do
    [ "$SKIP_KILL" = 1 ] || kill_builder "$sig"
    for i in $(seq 1 25); do [ "$(builder_count)" = 0 ] && break; sleep 0.2; done
    [ "$(builder_count)" = 0 ] && break
  done
  n="$(builder_count)"
  if [ "$n" != 0 ]; then
    rc=1; say "⛔ بقيت عمليّاتٌ لمستخدم البناء بعد التنظيف"
    pub "التنظيف: ⛔ بقيت $n عمليّةً لمستخدم البناء — لا تُقرأ شجرتُه"
  fi
  rc_set cleanup "$rc"
  return "$rc"
}

# أ4 (الإصلاح الخامس): ثواني خطوةٍ من finalize بسقفها بعد حجز ما يليها وهامش قتلها (قد تكون ≤ 0 ⇒ لا تُشغَّل)
fin_secs(){ local cap="$1" after="$2" t; t=$(( FIN_END - $(date +%s) - after - FIN_KILL )); [ "$t" -gt "$cap" ] && t="$cap"; echo "$t"; }

take_ownership(){ # $1 الثواني المتاحة — بعد التحقّق من موت البناء: شجرتُه ونتائجُ الجهاز تصير للمشرف ليقرأ JUnit (umask 077 يجعل ملفّاتِ pcibuild 600)
  # أ4 (الإصلاح الخامس · Codex 14): chown -R على شجرةٍ يتحكّم البناءُ بحجمها **بمهلة** من ميزانية finalize — `timeout` يجري بصلاحية root
  #     (sudo timeout … chown) فيقتل chown نفسَه لا sudo وحده. رموزُ الخروج: 0 تمّ · 1 تعذّر · 124 انقضت المهلة.
  local secs="$1" me d rc left
  me="$(id -un)"
  for d in "$BSRC" "$BDEV"; do
    if [ -L "$d" ]; then say "⛔ $d رابطٌ رمزيّ — لا يُقرأ"; return 1; fi   # أ3: لا رابطَ في أوّل المسار
    [ -e "$d" ] || continue
    left=$(( OWN_END - $(date +%s) ))
    [ "$left" -ge 1 ] || return 124
    if [ -n "$OWN_TEST_SLEEP" ]; then timeout -k "$FIN_KILL" "${left}s" sleep "$OWN_TEST_SLEEP"; rc=$?
    elif [ "$ISO" = 1 ]; then sudo -n timeout -k "$FIN_KILL" "${left}s" chown -R -P "$me:$me" "$d"; rc=$?   # -P: لا يُتبع رابطٌ داخلها
    else timeout -k "$FIN_KILL" "${left}s" chown -R -P "$(id -u):$(id -g)" "$d"; rc=$?; fi
    case "$rc" in
      0) ;;
      124|137) say "⏱ انقضت مهلةُ التملّك"; return 124 ;;
      *) say "⛔ تعذّر تملُّكُ $d"; return 1 ;;
    esac
  done
  return 0
}

do_finalize(){
  local ready=true tree=1 fe t0 be own_secs own_rc dt t
  # أ4 (الإصلاح الخامس): ميزانيةٌ كلّيّةٌ لكلّ finalize = min(نهايةُ الميزانية، الآن + FIN_RESERVE)؛ كلُّ خطوةٍ داخلها (انظر FIN_KILL أعلاه)
  t0="$(date +%s)"
  be="$(cat "$PCI_STATE/budget_end" 2>/dev/null)"; [[ "$be" =~ ^[0-9]+$ ]] || be=$(( t0 + FIN_RESERVE ))
  FIN_END=$(( be < t0 + FIN_RESERVE ? be : t0 + FIN_RESERVE ))
  # أ2: لا adb ولا محاكيَ بحسابٍ غيرِ مستخدم البناء بعد آخر طور
  fe="$(foreign_emu_procs)"
  if [ "$fe" != 0 ]; then pub "العزل: ⛔ بعد الأطوار: ADB/المحاكي بحسابٍ آخر: $fe"; ready=false; fi
  do_cleanup || { ready=false; tree=0; }      # محدودٌ بنفسه: 3 جولاتٍ × 5 ث انتظاراً
  if [ "$tree" = 1 ]; then
    own_secs=$(( FIN_END - $(date +%s) - FIN_AFTER_OWN - FIN_KILL ))
    OWN_END=$(( $(date +%s) + own_secs ))
    t="$(date +%s)"
    if [ "$own_secs" -ge 1 ]; then take_ownership "$own_secs"; own_rc=$?; else own_rc=124; fi
    dt=$(( $(date +%s) - t ))
    if [ "$own_rc" = 124 ]; then
      pub "الإنهاء: ⛔ انقضت مهلةُ التملّك — لا تُقرأ الشجرة (${dt} ث من ${own_secs} متاحة)"; ready=false; tree=0
    elif [ "$own_rc" != 0 ]; then
      pub "الإنهاء: ⛔ تعذّر التملّك — لا تُقرأ الشجرة"; ready=false; tree=0
    else
      pub "الإنهاء: التملّك ${dt} ث من ${own_secs} متاحة"
    fi
  fi
  local -a nt=()
  [ "$tree" = 1 ] || nt=(--no-tree)
  # كلُّ خطوةٍ بما بقي لها من الميزانية (لا أكثر من سقفها) بعد حجز ما يليها؛ وما لم يبقَ له وقتٌ لا يُشغَّل فلا يكون الناتجُ جاهزاً
  t="$(fin_secs "$FIN_CAP_COUNTS" $((FIN_CAP_GUARD + FIN_CAP_ENC + FIN_CAP_CHECK + 3 * FIN_KILL)))"
  if [ "$t" -lt 1 ] || ! timeout -k "$FIN_KILL" "${t}s" python3 -I -S "$PCI_CTL/counts_schema.py" emit --state "$PCI_STATE" --src "$BSRC" \
       --device "$BDEV" --kind "$PCI_KIND" --leg "$PCI_LEG" --sha "$PCI_SHA" --out "$PCI_ART/counts.json" \
       ${nt[@]+"${nt[@]}"} ${KINDS_ARGS[@]+"${KINDS_ARGS[@]}"}; then
    say "⛔ تعذّرت كتابةُ counts.json"; ready=false
  fi
  # عدُّ ما في السجلّ الخاصّ من أسطرٍ حسّاسة (يُكتب في السجلّ نفسِه قبل تشفيره — لا يُنشر)
  t="$(fin_secs "$FIN_CAP_GUARD" $((FIN_CAP_ENC + FIN_CAP_CHECK + 2 * FIN_KILL)))"
  [ "$t" -ge 1 ] && { timeout -k "$FIN_KILL" "${t}s" python3 -I -S "$PCI_CTL/guard.py" --report --label "السجلّ الخاص" "$PCI_OUT/log.txt" || true; }
  if [ "$ready" = true ]; then
    t="$(fin_secs "$FIN_CAP_ENC" $((FIN_CAP_CHECK + FIN_KILL)))"
    { [ "$t" -ge 1 ] && timeout -k "$FIN_KILL" "${t}s" bash "$PCI_CTL/encrypt_log.sh" encrypt; } || { say "⛔ تعذّر التشفير"; ready=false; }
  fi
  if [ "$ready" = true ]; then
    t="$(fin_secs "$FIN_CAP_CHECK" 0)"
    { [ "$t" -ge 1 ] && timeout -k "$FIN_KILL" "${t}s" python3 -I -S "$PCI_CTL/artifact_check.py" "$PCI_ART" --kind "$PCI_KIND" \
        --leg "$PCI_LEG" --sha "$PCI_SHA" ${KINDS_ARGS[@]+"${KINDS_ARGS[@]}"}; } || ready=false
  fi
  if [ "$ready" = true ]; then pub "الناتج: ✅ جاهزٌ للرفع"; else pub "الناتج: ⛔ غيرُ جاهزٍ — لا رفع"; fi
  pub "الإنهاء: $(( $(date +%s) - t0 )) ث من ميزانيةٍ أقصاها $FIN_RESERVE"
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
