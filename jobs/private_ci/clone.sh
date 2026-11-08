#!/usr/bin/env bash
# 🔑 private-ci (v3) — الاستنساخُ: **الوحيدُ** الذي يحمل سرّاً (PCI_KEY_B64 = مفتاحُ نشرٍ للقراءة فقط على المستودع الخاصّ)،
#    ويستدعيه المشرفُ (supervise.sh clone) بمستخدم runner قبل أن يُنفَّذ سطرٌ واحدٌ من شيفرة الخاصّ. (D14)
#      المفتاحُ يدخل ssh-agent من stdin (`ssh-add -`) فلا يُكتب ملفّاً على القرص أصلاً (المجلّدُ المؤقّتُ للمقبس وknown_hosts فقط).
#      وtrap على EXIT/INT/TERM يمحو ويقتل الوكيلَ ثمّ **يتحقّق** في كلّ مخرج — وعند الشكّ خروجٌ برمز 9 فلا يبدأ بناء
#      (شرطُ خطوة البناء في private-ci.yml: steps.clone.outcome == 'success').
#    وكلُّ مخرجٍ إلى $PCI_ROOT/out/log.txt من أوّل أمر (لا شيءَ إلى السجلّ العلنيّ إلا سطرُ رفض الاستدعاء أدناه).
#
# رموزُ الخروج: 0 استُنسخ والمفتاحُ زال · 1 تعذّر الاستنساخ أو قُطع (والمفتاحُ زال) · 9 لم يثبت زوالُ المفتاح ⇒ لا بناء.
set -uo pipefail

case "${GITHUB_WORKFLOW_REF:-}" in
  */.github/workflows/private-ci.yml@*) ;;
  *) echo "⛔ jobs/private_ci/clone.sh لا يُشغَّل إلا من private-ci.yml — لا شيء نُفّذ."; exit 2 ;;
esac

: "${RUNNER_TEMP:?}" "${PCI_KIND:?}" "${PCI_SHA:?}" "${PCI_SOURCE:?}"
PCI_ROOT="$RUNNER_TEMP/pci"
PCI_OUT="$PCI_ROOT/out"
PCI_STATE="$PCI_ROOT/state"
PCI_SRC="$PCI_ROOT/src"
umask 077
mkdir -p "$PCI_OUT" "$PCI_STATE"

# ───── من هنا لا يخرج حرفٌ إلى السجلّ العلنيّ ─────
exec >>"$PCI_OUT/log.txt" 2>&1
say(){ printf '▸ %s [clone] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

KD=""            # مجلّدُ مقبس الوكيل وknown_hosts (يُنشأ بـmktemp) — لا ملفَّ مفتاحٍ فيه أبداً
AGENT_PID=""

# ───── أنماطُ الاستنساخ الجزئيّ (non-cone) — مجلّداتُ التطبيق والمحرّك فقط ─────
# ⛔ ما لا يُستنسخ أبداً: cloud/ · secure/ · store/ · tools/store · tools/cloud* · .github/ · docs/ (إلا دليلَ التطبيق
#    لحارسه) · ops/ (إلا ملفَّ طلب اللقطات). ويُتحقَّق بعد الإخراج أنّ شيئاً منها لم يدخل.
BUILD_SET=(/gradlew /gradle/ /settings.gradle.kts /build.gradle.kts /gradle.properties
           /app/ /core/ /engine/ /feature/ /baselineprofile/ /tools/size/)
sparse_for_kind(){
  case "$PCI_KIND" in
    dry-run|dry-run-emu|dry-run-stress) printf '%s\n' /jobs/private_ci/ ;;
    # /tools/quraat/out/ — خرائطُ الروايات ونصوصُها (قرآنٌ منشورٌ لا أسرار) تقرؤها اختباراتُ الوحدة (mapping_warsh.json) — مقيسٌ في تشغيلة 42391117587
    app-test)                    printf '%s\n' "${BUILD_SET[@]}" /tools/ai/ /tools/ux/ /tools/i18n/ /tools/quraat/out/ /docs/ai/app_guide/ ;;
    engine-test)                 printf '%s\n' "${BUILD_SET[@]}" /tools/tasmi_bench/ /tools/tasmi_phoneme/ /tools/alignment/ /tools/ci/ ;;
    tasmi-model-emulator-smoke)  printf '%s\n' "${BUILD_SET[@]}" /tools/ci/ ;;
    ui-shots-emu)                printf '%s\n' "${BUILD_SET[@]}" /tools/ci/ /ops/ui_shots/request.txt ;;
    hudhud-feature-test)         printf '%s\n' "${BUILD_SET[@]}" /tools/hudhud/ ;;
    ads-emulator-smoke)          printf '%s\n' "${BUILD_SET[@]}" /tools/ci/ ;;
    device-matrix)               printf '%s\n' "${BUILD_SET[@]}" /tools/qa/ /tools/ci/ ;;
    *) return 1 ;;
  esac
}
FORBIDDEN_RX='^(cloud|secure|store|\.github|tools/store|tools/cloud[^/]*|tools/ops)(/|$)|^docs/(?!ai/app_guide/)|^ops/(?!ui_shots/request\.txt$)'
FORBIDDEN_NAME_RX='(^|/)(keystore\.properties|google-services\.json|local\.properties|r2_credentials[^/]*|\.env[^/]*)$|\.(jks|keystore|p12|pem|key)$'

github_known_hosts(){ # مفاتيحُ مضيف GitHub من واجهته فوق TLS — لا قبولَ لمضيفٍ جديدٍ ولا لمفتاحٍ متغيّر
  python3 - "$1" <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen("https://api.github.com/meta", timeout=30) as r:
    keys = json.load(r)["ssh_keys"]
with open(sys.argv[1], "w") as f:
    for k in keys:
        f.write("github.com " + k + "\n")
print(f"known_hosts: {len(keys)} مفاتيح من api.github.com/meta")
PY
}

# ───── المحوُ والتحقّق: يجريان في كلّ مخرجٍ من هذا الطور (نجح الاستنساخُ أو فشل) ─────
scrub(){
  if [ -n "$AGENT_PID" ]; then
    SSH_AGENT_PID="$AGENT_PID" ssh-agent -k >/dev/null 2>&1 || kill "$AGENT_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$AGENT_PID" 2>/dev/null || break; sleep 0.2; done
  fi
  [ -n "$KD" ] && rm -rf "$KD"
  unset PCI_KEY_B64 GIT_SSH_COMMAND SSH_AUTH_SOCK SSH_AGENT_PID 2>/dev/null || true
}

verify_scrubbed(){ # ⛔ يُعيد غيرَ الصفر إن بقي أثرٌ واحد
  local bad=0
  if [ -n "$KD" ] && [ -e "$KD" ]; then say "⛔ مجلّدُ المقبس باقٍ"; bad=1; fi
  if [ -n "$AGENT_PID" ] && kill -0 "$AGENT_PID" 2>/dev/null; then say "⛔ ssh-agent حيّ"; bad=1; fi
  # على العدّاء لا ssh-agent غيرُ ما بدأناه؛ فأيُّ عمليّةٍ باقيةٍ للمستخدم نفسِه بعد قتله أثرٌ لا يُبرَّأ.
  if [ -n "$AGENT_PID" ] && pgrep -u "$(id -u)" -x ssh-agent >/dev/null 2>&1; then say "⛔ عمليّةُ ssh-agent باقية"; bad=1; fi
  if [ -n "${PCI_KEY_B64:-}" ] || [ -n "${SSH_AUTH_SOCK:-}" ]; then say "⛔ متغيّرُ مفتاحٍ باقٍ"; bad=1; fi
  if [ -d "$PCI_SRC/.git" ] && git -C "$PCI_SRC" remote | grep -q .; then say "⛔ بعيدٌ باقٍ في الاستنساخ"; bad=1; fi
  return $bad
}

clone_inner(){
  rm -rf "$PCI_SRC"; mkdir -p "$PCI_SRC"
  local url
  if [ "$PCI_SOURCE" = "public" ]; then
    url="https://github.com/${GITHUB_REPOSITORY:?}.git"
    # 🧪 للاختبار المحلّيّ وحده: مستودعٌ محلّيّ بدل GitHub — ولا يُقبل داخل GitHub Actions أبداً.
    [ "${GITHUB_ACTIONS:-}" != "true" ] && [ -n "${PCI_TEST_REMOTE:-}" ] && url="$PCI_TEST_REMOTE"
    unset PCI_KEY_B64 || true
  else
    [ -n "${PCI_KEY_B64:-}" ] || { say "⛔ لا مفتاحَ قراءة"; return 1; }
    url="git@github.com:mwqwf/QuranRafiq.git"
    KD="$(mktemp -d "$PCI_ROOT/k.XXXXXXXX")" || return 1
    chmod 700 "$KD"
    # المفتاحُ في ssh-agent بمقبسٍ داخل المجلّد، ويدخله من stdin: لا ملفَّ مفتاحٍ على القرص في أيّ لحظة.
    eval "$(ssh-agent -s -a "$KD/agent.sock")" >/dev/null || { unset PCI_KEY_B64; say "⛔ تعذّر ssh-agent"; return 1; }
    AGENT_PID="${SSH_AGENT_PID:-}"
    printf '%s' "$PCI_KEY_B64" | base64 --decode 2>/dev/null | ssh-add -q - >/dev/null 2>&1
    local pst=("${PIPESTATUS[@]}")
    unset PCI_KEY_B64
    [ "${pst[0]}" = 0 ] && [ "${pst[1]}" = 0 ] && [ "${pst[2]}" = 0 ] || { say "⛔ تعذّر تحميلُ المفتاح (غيرُ مقروء أو مرفوض)"; return 1; }
    github_known_hosts "$KD/known_hosts" || { say "⛔ تعذّر جلبُ مفاتيح مضيف GitHub"; return 1; }
    export GIT_SSH_COMMAND="ssh -F /dev/null -o IdentityAgent=$KD/agent.sock -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KD/known_hosts -o BatchMode=yes"
  fi
  local patterns; patterns="$(sparse_for_kind)" || { say "⛔ نوعٌ بلا أنماط"; return 1; }
  git -C "$PCI_SRC" init -q
  git -C "$PCI_SRC" remote add origin "$url"
  git -C "$PCI_SRC" config core.sparseCheckout true
  # shellcheck disable=SC2086
  git -C "$PCI_SRC" sparse-checkout set --no-cone $patterns
  # جزئيٌّ مرّتين: عمقٌ واحد وبلا كتل (blob:none) — فلا يُنزَّل من الكتل إلا ما تطلبه الأنماط عند الإخراج.
  git -C "$PCI_SRC" fetch -q --depth 1 --filter=blob:none origin "$PCI_SHA" || { say "⛔ تعذّر جلبُ الإيداع"; return 1; }
  git -C "$PCI_SRC" checkout -q --detach FETCH_HEAD || { say "⛔ تعذّر الإخراج"; return 1; }
  [ "$(git -C "$PCI_SRC" rev-parse HEAD)" = "$PCI_SHA" ] || { say "⛔ الرأسُ لا يطابق المطلوب"; return 1; }
  # مرجعُ whisper.cpp المثبَّت يُقرأ من مصدره الواحد (publish-apk.yml) **بلا إخراجه**: سطرٌ واحد والمفتاحُ حاضر.
  if [ "$PCI_SOURCE" = "private" ]; then
    git -C "$PCI_SRC" cat-file -p "HEAD:.github/workflows/publish-apk.yml" 2>/dev/null \
      | grep -oE 'WHISPER_CPP_REF: *[0-9a-f]{40}' | head -1 | grep -oE '[0-9a-f]{40}' > "$PCI_STATE/whisper_ref" || true
  fi
  unset GIT_SSH_COMMAND
  git -C "$PCI_SRC" remote remove origin   # ⛔ لا جلبَ كسولٍ بعد الآن: أيُّ كتلةٍ ناقصة تفشل ولا تستعمل مفتاحاً
  # ⛔ التحقّق على القرص (لا فهرسُ git): لا مسارَ ممنوعٌ ولا ملفُّ أسرار
  local bad
  bad=$( (cd "$PCI_SRC" && find . -path ./.git -prune -o \( -type f -o -type l \) -print | sed 's|^\./||') | python3 -c "
import re, sys
rx = re.compile(r'''$FORBIDDEN_RX'''); nx = re.compile(r'''$FORBIDDEN_NAME_RX''')
print(sum(1 for p in sys.stdin.read().split(chr(10)) if p and (rx.search(p) or nx.search(p))))")
  if [ "${bad:-1}" != "0" ]; then say "⛔ $bad مساراً ممنوعاً في الاستنساخ — يُمحى المصدر"; rm -rf "$PCI_SRC"; return 1; fi
  say "استُنسخ $( (cd "$PCI_SRC" && find . -path ./.git -prune -o -type f -print) | wc -l) ملفّاً · المصدر $PCI_SOURCE"
  return 0
}

# ───── D14: المحوُ والتحقّق في كلّ مخرجٍ (نجاحٌ · فشلٌ · إشارةٌ) ─────
RC=""
on_exit(){
  trap - EXIT INT TERM
  [ -n "$RC" ] || RC=1                       # قُطع قبل اكتمال الاستنساخ (إشارةٌ أو عطب)
  [ "$RC" -eq 0 ] || rm -rf "$PCI_SRC"       # استنساخٌ فاشلٌ أو مقطوع: لا شجرةَ ناقصةً ولا بعيداً باقياً (فيبقى 9 للشكّ في المفتاح وحده)
  scrub
  if ! verify_scrubbed; then
    say "⛔ لم يثبت زوالُ المفتاح ⇒ يُمحى المصدرُ ولا يجري البناء"
    rm -rf "$PCI_SRC"
    printf '1\n' > "$PCI_STATE/rc_clone"
    exit 9
  fi
  say "المفتاحُ زال: لا ملفّ · لا مقبس · لا ssh-agent"
  printf '%s\n' "$RC" > "$PCI_STATE/rc_clone"
  [ "$RC" -eq 0 ] && exit 0
  exit 1
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

clone_inner; RC=$?
exit 0
