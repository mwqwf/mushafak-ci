#!/usr/bin/env bash
# 🏗️ private-ci (v3) — **طورٌ واحدٌ** من بناء الشيفرة الخاصّة واختبارها، يجري بمستخدم النظام `pcibuild` (بلا sudo) داخل وظيفة build.
#    هذه الشيفرةُ (Gradle · الاختبارات · سكربتاتُ tools/ في الخاصّ) **غيرُ موثوقة** بحكم التصميم. ولا يستدعيها إلا المشرفُ
#    (supervise.sh) بالأمر:  build.sh phase <id>  ببيئةٍ من قائمةٍ مسموحة (env -i): لا GITHUB_* ولا ACTIONS_* ولا رمزَ.
#
#    ما ليس هنا عمداً: رموزُ خروج الأطوار (يسجّلها المشرفُ من رمز الخروج الفعليّ)، وكتابةُ counts.json، والتشفيرُ، والرفعُ،
#    وكلُّ ما يحتاج sudo (التفريغُ والـKVM أمرٌ ثابتٌ في المشرف) — ⛔ فلا sudo في هذا الملفّ أبداً (يفرضه test_scope).
#    ومخرجاتُه (stdout/stderr) يلتقطها المشرفُ إلى سجلٍّ يملكه؛ ولا يُطبع شيءٌ منها علناً.
set -uo pipefail

case "${GITHUB_WORKFLOW_REF:-}" in
  */.github/workflows/private-ci.yml@*) ;;
  *) echo "⛔ jobs/private_ci/build.sh لا يُشغَّل إلا من private-ci.yml — لا شيء نُفّذ."; exit 2 ;;
esac

MODE="${1:-}"
PHASE="${2:-}"
: "${PCI_KIND:?}" "${PCI_SRC:?}" "${PCI_BSTATE:?}" "${PCI_BDEVICE:?}"
PCI_LEG="${PCI_LEG:-}"
umask 077
mkdir -p "$PCI_BSTATE" "$PCI_BDEVICE"
# ⛔ ملفّاتُ أوامر GitHub تُحوَّل إلى ملفّاتٍ مهملة (دفاعٌ إضافيّ: البيئةُ المسموحةُ لا تحملها أصلاً؛ وemu_focused.sh يكتب في الملخّص).
for v in GITHUB_STEP_SUMMARY GITHUB_ENV GITHUB_OUTPUT GITHUB_PATH GITHUB_STATE; do
  export "$v=$PCI_BSTATE/inner_${v,,}"
done
export CI=true TERM=dumb PYTHONIOENCODING=utf-8
unset PCI_KEY_B64 SSH_AUTH_SOCK SSH_AGENT_PID GIT_SSH_COMMAND 2>/dev/null || true
# ffmpeg الساكنُ (ensure_ffmpeg.sh بلا sudo يبنيه تحت HOME) — كلُّ طورٍ عمليّةٌ جديدة فيلزم إدراجُه في المسار.
[ -d "$HOME/.local/ffmpeg-static" ] && export PATH="$HOME/.local/ffmpeg-static:$PATH"

say(){ printf '▸ %s [%s] %s\n' "$(date -u +%H:%M:%S)" "$PHASE" "$*"; }

fetch_whisper(){
  local ref="${PCI_WHISPER_REF:-}"
  [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || { say "⛔ لم يُقرأ WHISPER_CPP_REF — ولا يُفترض رقمٌ بديل"; return 1; }
  local wd="$PCI_SRC/third_party/whisper.cpp"
  git clone -q --filter=blob:none --no-checkout https://github.com/ggml-org/whisper.cpp "$wd" || return 1
  git -C "$wd" fetch -q --depth 1 origin "$ref" && git -C "$wd" checkout -q --detach FETCH_HEAD || return 1
  [ "$(git -C "$wd" rev-parse HEAD)" = "$ref" ]
}

fatiha_wav(){ # تلاوةٌ بشريّةٌ منشورة (الحصري · الفاتحة) — لا صوتَ مستخدم
  bash tools/ci/ensure_ffmpeg.sh || return 1
  [ -d "$HOME/.local/ffmpeg-static" ] && export PATH="$HOME/.local/ffmpeg-static:$PATH"
  mkdir -p app/src/androidTest/assets
  curl -fsSL --retry 3 -o "$PCI_BSTATE/fatiha.mp3" https://server13.mp3quran.net/husr/001.mp3 || return 1
  ffmpeg -hide_banner -loglevel error -y -i "$PCI_BSTATE/fatiha.mp3" -ac 1 -ar 16000 -c:a pcm_s16le \
    -map_metadata -1 -fflags +bitexact -flags:a +bitexact app/src/androidTest/assets/tasmi_fatiha_husary.wav || return 1
  [ "$(stat -c %s app/src/androidTest/assets/tasmi_fatiha_husary.wav)" -gt 640044 ]
}

gradle(){ chmod +x ./gradlew && ./gradlew --no-daemon --console=plain "$@"; }

jni_check(){ # فحصٌ نحويٌّ لجسر jni.c على ترويسات الالتزام المثبَّت (‏مطابقٌ لوظيفة jni في engine-test.yml)
  local ref="${PCI_WHISPER_REF:-}"; [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || return 1
  local ck="$PCI_BSTATE/ck"; mkdir -p "$ck/inc/android"
  local f n
  for f in include/whisper.h ggml/include/ggml.h ggml/include/ggml-cpu.h ggml/include/ggml-backend.h ggml/include/ggml-alloc.h; do
    n=$(basename "$f")
    curl -fsS -o "$ck/inc/$n" "https://raw.githubusercontent.com/ggml-org/whisper.cpp/$ref/$f" || return 1
  done
  bash tools/tasmi_bench/jni_stubs.sh "$ck/inc/android" || return 1
  local jh="${JAVA_HOME:?}"
  sed 's/params\.no_speech_thold/params.no_speech_tholdZZ/' engine/recitation/src/main/cpp/jni.c > "$ck/bad.c"
  if gcc -fsyntax-only -std=c11 -I "$ck/inc" -I "$jh/include" -I "$jh/include/linux" "$ck/bad.c"; then
    say "⛔ الفحصُ قبل حقلاً لا وجودَ له ⇒ لا يحرس شيئاً"; return 1
  fi
  gcc -fsyntax-only -std=c11 -Werror=implicit-function-declaration -Werror=incompatible-pointer-types \
    -Werror=int-conversion -I "$ck/inc" -I "$jh/include" -I "$jh/include/linux" engine/recitation/src/main/cpp/jni.c
}

ui_shots_request(){ # صنفُ اللقطات ومتغيّرُها من ملفّ الطلب — بقيمٍ معروفة الشكل وحدها
  local cls var
  cls=$(sed -nE 's/^class[:=][[:space:]]*//p' ops/ui_shots/request.txt | head -1 | tr -d '\r ')
  var=$(sed -nE 's/^variant[:=][[:space:]]*//p' ops/ui_shots/request.txt | head -1 | tr -d '\r ')
  cls="${cls:-com.ali.rafiq.ui.UiTextComfortShotsTest}"
  [[ "$cls" =~ ^[A-Za-z][A-Za-z0-9_.]{2,200}$ ]] || return 1
  [[ -z "$var" || "$var" =~ ^[A-Za-z0-9_-]{1,40}$ ]] || return 1
  printf '%s\n%s\n' "$cls" "$var" > "$PCI_BSTATE/ui_shots_req"
}

emu_up(){ # أ2: المحاكي وخادمُ ADB **بمستخدم البناء نفسِه** (لا بحساب العدّاء) — فلا جسرَ من البناء إلى صلاحية العدّاء عبر ADB.
  #   المعاملاتُ من المشرف (kinds.json الموثوق بأنماطٍ مغلقة) وتُفحص هنا ثانيةً؛ وخادمُ ADB بمنفذٍ خاصّ (ANDROID_ADB_SERVER_PORT)
  #   والـAVD تحت بيت البناء؛ وصورةُ النظام ثبّتها المشرفُ (sysimg) في SDK المحصَّن الذي لا يكتبه هذا المستخدم.
  local api="${PCI_EMU_API:-}" target="${PCI_EMU_TARGET:-}" arch="${PCI_EMU_ARCH:-}" profile="${PCI_EMU_PROFILE:-}" ram="${PCI_EMU_RAM:-}"
  [[ "$api" =~ ^[0-9]{2}$ && "$target" =~ ^[a-z_]{1,32}$ && "$arch" =~ ^(x86|x86_64)$ && "$profile" =~ ^[A-Za-z0-9\ _]{1,32}$ \
     && "$ram" =~ ^[0-9]{3,5}M$ ]] || { say "⛔ معاملاتُ المحاكي غيرُ صالحة"; return 1; }
  [[ "${ANDROID_ADB_SERVER_PORT:-}" =~ ^[0-9]{4,5}$ ]] || { say "⛔ لا منفذَ خاصّاً لخادم ADB"; return 1; }
  local sdk="${ANDROID_HOME:?}" avd="${ANDROID_AVD_HOME:?}" i boot=""
  mkdir -p "$avd"
  echo no | "$sdk/cmdline-tools/latest/bin/avdmanager" -s create avd --force -n pci \
    -k "system-images;android-$api;$target;$arch" -d "$profile" || return 1
  printf 'hw.ramSize=%s\n' "${ram%M}" >> "$avd/pci.avd/config.ini"
  adb start-server || return 1
  # جلسةٌ خاصّة ومخرجاتٌ إلى ملفٍّ في بيت البناء (لا يُمسك أنبوبَ الطور فلا يُعطّل مصرفَه)
  setsid "$sdk/emulator/emulator" -avd pci -no-window -gpu swiftshader_indirect -noaudio -no-boot-anim -camera-back none \
    -no-snapshot -port 5554 > "$PCI_BSTATE/emulator.log" 2>&1 < /dev/null &
  timeout 600 adb -s emulator-5554 wait-for-device || { say "⛔ المحاكي لم يظهر"; return 1; }
  for i in $(seq 1 120); do
    boot="$(timeout 20 adb -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')"
    [ "$boot" = 1 ] && break
    sleep 5
  done
  [ "$boot" = 1 ] || { say "⛔ المحاكي لم يكمل الإقلاع"; return 1; }
  for i in window_animation_scale transition_animation_scale animator_duration_scale; do
    adb -s emulator-5554 shell settings put global "$i" 0.0 || return 1
  done
}

emu_phase(){ # طورُ «emu» في الأنواع ذات المحاكي الواحد
  local cls var
  case "$PCI_KIND" in
    tasmi-model-emulator-smoke)
      bash tools/ci/emu_focused.sh com.ali.rafiq.ui.tasmi.LongTasmiModelEmulatorTest,com.ali.rafiq.ui.tasmi.ReciteWithMeCloudEmulatorTest,com.ali.rafiq.ui.tasmi.TasmiRecreateEmulatorTest,com.ali.rafiq.ui.tasmi.TasmiEngineDeviceTest,com.ali.rafiq.ui.NewTabsSmokeEmulatorTest,com.ali.rafiq.ui.BackNavigationEmulatorTest ;;
    ads-emulator-smoke)
      bash tools/ci/emu_focused.sh com.ali.rafiq.monetization.AdsEmulatorTest ;;
    ui-shots-emu)
      cls=$(sed -n 1p "$PCI_BSTATE/ui_shots_req" 2>/dev/null); var=$(sed -n 2p "$PCI_BSTATE/ui_shots_req" 2>/dev/null)
      [ -n "$cls" ] || return 1
      bash tools/ci/emu_focused.sh "$cls" "$var"; local rc=$?
      local d=app/build/outputs/androidTest-results/connected/ui_shots
      if [ -d "$d" ] && command -v mogrify >/dev/null; then mogrify -resize 540x "$d"/*.png || true; fi
      return $rc ;;
    hudhud-feature-test)
      bash tools/hudhud/emulator_probe.sh app/build/outputs/bundle/debug/app-debug.aab bundletool.jar ;;
    *) say "لا طورَ emu لهذا النوع"; return 1 ;;
  esac
}

gradle_phase(){
  case "$PCI_KIND" in
    app-test)            gradle :app:compileDebugKotlin :app:testDebugUnitTest :core:quran:testDebugUnitTest :core:learning:test ;;
    engine-test)         gradle :engine:recitation:test ;;
    hudhud-feature-test) gradle :app:bundleRelease :app:bundleDebug ;;
    device-matrix)       gradle :app:assembleDebug :app:assembleDebugAndroidTest -Prafiq.testAbis=x86 ;;
    *) say "⛔ gradle غيرُ معرَّف لهذا النوع"; return 1 ;;
  esac
}

run_phase(){
  cd "$PCI_SRC" || return 1
  case "$PHASE" in
    unit)             python3 -m unittest discover -s jobs/private_ci -p 'test_*.py' -v ;;
    app_guide)        python3 tools/ai/test_app_guide_guard.py ;;
    no_blocking)      python3 tools/ux/test_no_blocking_notices.py ;;
    back_handling)    python3 tools/ux/test_back_handling.py ;;
    i18n_verify)      python3 tools/i18n/verify.py ;;
    i18n_locale)      python3 tools/i18n/check_locale.py --all ;;
    gradle)           gradle_phase ;;
    pip)              python3 -m pip install --disable-pip-version-check --no-input -q "numpy==2.1.3" "soundfile==0.13.1" ;;
    fixture_selftest) python3 tools/tasmi_bench/fixture_audit.py --selftest ;;
    fixture_audit)    python3 tools/tasmi_bench/fixture_audit.py ;;
    ffmpeg)           bash tools/ci/ensure_ffmpeg.sh ;;
    phoneme_gold)     python3 -m unittest discover -v -s tools/tasmi_phoneme -p "test_*.py" ;;
    jni)              jni_check ;;
    whisper)          fetch_whisper ;;
    fatiha)           fatiha_wav ;;
    request)          ui_shots_request ;;
    bundletool)       curl -fsSL -o bundletool.jar https://github.com/google/bundletool/releases/download/1.18.3/bundletool-all-1.18.3.jar ;;
    lint)             gradle :app:lintDebug ;;
    emu_up)           emu_up ;;
    emu)              emu_phase ;;
    emu_smoke)        bash tools/qa/device_smoke.sh "$(ls app/build/outputs/apk/debug/*.apk | head -1)" "$PCI_BDEVICE" "$PCI_LEG" ;;
    emu_tasmi)        bash tools/qa/tasmi_device.sh "$(ls app/build/outputs/apk/androidTest/debug/*.apk | head -1)" "$PCI_BDEVICE" ;;
    # أطوارٌ تجريبيّةٌ للاختبار المحلّيّ (تُذكر في kinds تجريبيٍّ وحده)
    selftest_ok)      true ;;
    selftest_fail)    false ;;
    selftest_noisy)   head -c 200000 /dev/zero | tr "\0" x; echo ;;
    selftest_forge)   # يحاول تزويرَ رموز الخروج في مجلّده — لا أثرَ له على حالة المشرف
                      printf '0\n' > "$PCI_BSTATE/rc_selftest_forge"; printf 'selftest_forge\n' >> "$PCI_BSTATE/ran.txt"
                      printf '0\n' > "$PCI_BSTATE/rc_selftest_fail"; false ;;
    selftest_env)     env | sort ;;
    selftest_tamper)  # أ1: يحاول تبديلَ مدخل التحكّم (في الإنتاج يملكه root فيفشل؛ ومحلّيّاً يكشفه المشرفُ قبل الطور التالي)
                      printf '\n# tampered\n' >> "$HOME/ctl/build.sh" ;;
    selftest_linger)  # أ3: عمليّةٌ تهرب من جلسة الطور وتبقى بعده — يجب أن يقتلها التنظيفُ أو يمنعَ قراءةَ الشجرة
                      setsid sleep 300 > /dev/null 2>&1 < /dev/null & true ;;
    selftest_hold)    # أ4: عمليّةٌ خلفيّةٌ تمسك مخرجَ الطور مفتوحاً بعد انتهائه — لا يُنتظر EOF منها
                      sleep 300 & true ;;
    selftest_sleep)   sleep 300 ;;
    selftest_limits)  ulimit -u ;;
    *) say "⛔ طورٌ غيرُ معروف"; return 2 ;;
  esac
}

case "$MODE" in
  phase) run_phase; exit $? ;;
  *)     echo "⛔ أمرٌ غيرُ معروف"; exit 2 ;;
esac
