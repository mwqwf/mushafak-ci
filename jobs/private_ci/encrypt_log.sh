#!/usr/bin/env bash
# 🔐 private-ci (v3) — تشفيرُ السجلّ الكامل بمفتاحٍ عامٍّ مضمَّن (age · X25519) قبل أن يغادر وظيفةَ build.
#    السجلُّ يحوي شيفرةً ومساراتٍ خاصّة عمداً (أخطاءُ Gradle)؛ فلا يعبر إلى الـArtifact (العلنيِّ التنزيل في مستودعٍ عامّ)
#    ولا إلى R2 إلا نصّاً مشفَّراً لا يفكّه إلا المفتاحُ الخاصّ المحفوظ سرّاً في المستودع الخاصّ (PCI_LOG_AGE_KEY).
#
# الأمران:
#   install — يثبّت age من إصدارٍ محدَّد (v1.2.1) **بعد التحقّق من بصمة الأرشيف** (sha256)، إلى $PCI_ROOT/bin.
#   encrypt — gzip ثمّ age -r <المفتاح العامّ> ⇒ $PCI_ROOT/art/log.txt.gz.age، ويتحقّق من ترويسة age في الناتج.
#
# ⛔ لا يطبع إلا رسائلَ ثابتة. وما دام log_pubkey.txt يحمل علامةَ PLACEHOLDER-TEST-KEY يُرفض التشفيرُ لمصدرٍ خاصّ.
set -uo pipefail

case "${GITHUB_WORKFLOW_REF:-}" in
  */.github/workflows/private-ci.yml@*) ;;
  *) echo "⛔ jobs/private_ci/encrypt_log.sh لا يُشغَّل إلا من private-ci.yml — لا شيء نُفّذ."; exit 2 ;;
esac

MODE="${1:-}"
: "${RUNNER_TEMP:?}" "${PCI_SOURCE:?}"
PCI_ROOT="$RUNNER_TEMP/pci"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGE_VERSION="v1.2.1"
AGE_URL="https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-linux-amd64.tar.gz"
AGE_SHA256="7df45a6cc87d4da11cc03a539a7470c15b1041ab2b396af088fe9990f7c79d50"
AGE_BIN="$PCI_ROOT/bin/age"
PUBKEY_FILE="$HERE/log_pubkey.txt"
# 🧪 للاختبار المحلّيّ وحده (test_shell.py): age النظام ومفتاحٌ مؤقّت — ولا يُقبلان داخل GitHub Actions أبداً.
if [ "${GITHUB_ACTIONS:-}" != "true" ]; then
  AGE_BIN="${PCI_TEST_AGE_BIN:-$AGE_BIN}"
  PUBKEY_FILE="${PCI_TEST_PUBKEY_FILE:-$PUBKEY_FILE}"
fi
umask 077
mkdir -p "$PCI_ROOT/bin" "$PCI_ROOT/art"

pubkey(){ # أوّلُ سطرٍ بشكل مفتاح age العامّ، ولا شيءَ غيرُه
  grep -E '^age1[02-9ac-hj-np-z]{58}$' "$PUBKEY_FILE" | head -1
}

do_install(){
  local tgz="$PCI_ROOT/age.tgz" got
  curl -fsSL --retry 3 -o "$tgz" "$AGE_URL" || { echo "age: ⛔ تعذّر التنزيل"; return 1; }
  got="$(sha256sum "$tgz" | cut -d' ' -f1)"
  [ "$got" = "$AGE_SHA256" ] || { echo "age: ⛔ بصمةُ الأرشيف لا تطابق الإصدارَ المثبَّت"; rm -f "$tgz"; return 1; }
  tar -xzf "$tgz" -C "$PCI_ROOT/bin" --strip-components=1 age/age || { echo "age: ⛔ تعذّر الفكّ"; return 1; }
  rm -f "$tgz"
  "$AGE_BIN" --version >/dev/null 2>&1 || { echo "age: ⛔ لا يعمل"; return 1; }
  echo "age: ✅ ${AGE_VERSION} مثبَّتٌ بالبصمة"
}

do_encrypt(){
  local log="$PCI_ROOT/out/log.txt" out="$PCI_ROOT/art/log.txt.gz.age" key
  key="$(pubkey)"
  [ -n "$key" ] || { echo "التشفير: ⛔ لا مفتاحَ عامّاً صالحاً"; return 1; }
  if grep -q 'PLACEHOLDER-TEST-KEY' "$PUBKEY_FILE" && [ "$PCI_SOURCE" != "public" ]; then
    echo "التشفير: ⛔ المفتاحُ العامّ وهميّ (PLACEHOLDER-TEST-KEY) — لا يُشفَّر سجلٌّ خاصٌّ لمفتاحٍ لا يملكه أحد"
    return 1
  fi
  [ -x "$AGE_BIN" ] || { echo "التشفير: ⛔ age غيرُ مثبَّت"; return 1; }
  [ -f "$log" ] || : > "$log"
  rm -f "$out"
  gzip -9 -c "$log" | "$AGE_BIN" -r "$key" -o "$out" 2>/dev/null || { echo "التشفير: ⛔ تعذّر"; rm -f "$out"; return 1; }
  [ "$(head -c 22 "$out" 2>/dev/null)" = "age-encryption.org/v1" ] || { echo "التشفير: ⛔ الناتجُ بلا ترويسة age"; rm -f "$out"; return 1; }
  # الناتجُ وحده يبقى بجوار counts.json في مجلّد الـArtifact؛ لا نسخةَ نصّيّةَ فيه.
  echo "التشفير: ✅ $(( $(stat -c %s "$out") / 1024 )) ك.ب"
}

case "$MODE" in
  install) do_install ;;
  encrypt) do_encrypt ;;
  *) echo "⛔ أمرٌ غيرُ معروف"; exit 1 ;;
esac
