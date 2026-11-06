#!/usr/bin/env python3
"""🧱 حارسُ نطاق mwqwf/mushafak-ci (D1 · D2 · D9 · D11–D14 · v3) — الفحصُ المطلوبُ في حماية main. يفشل إن:

  ١. حوى المستودعُ غيرَ .github/workflows/{private-ci,private-ci-sweeper,private-ci-scope-guard}.yml وjobs/private_ci/ وREADME.md
     (لا agent_cmd ولا سيورَ أخرى ولا سكربتاتِ وكلاء)؛
  ٢. ذكر سيرٌ غيرُ private-ci.yml بيئتَي build/publish أو أيَّ سرّ، أو استدعى private-ci.yml؛
  ٣. خرج private-ci.yml عن بنيته: زنادان فقط · permissions: {} أعلاه وأذوناتٌ محدّدةٌ لكلّ وظيفة · ثلاثُ وظائف بأسمائها
     وبيئاتها · كلُّ إجراءٍ مثبَّتٌ بـSHA ومن قائمةٍ مسموحة · checkout لا يحفظ الاعتماد · لا cache · Artifact بيومٍ واحد
     من build وحده وتنزيلُه في publish وحده · مفتاحُ القراءة في خطوة «الاستنساخ» وحدها · سرّا R2 والحالة في خطوة publish.py
     وحدها · بناء (v3): prep قبل clone وclone مشروطةٌ بنجاح prep والبناءُ والمحاكي مشروطان بنجاح clone وfin بـalways() وتشغّل
     finalize وخطوةُ الرفع شرطُها نصّاً `always() && steps.fin.outputs.ready == 'true'` (يُمسك always() وحده) ولا خطوةَ بعد prep
     تستدعي أدواتٍ من مساحة العمل (كلُّها من $RUNNER_TEMP/pci/ctl) · لا set -x ولا طباعةَ للسجلّ · publish بـalways() · بلا إلغاءٍ متزامن؛
  ٤. خرج الكنّاسُ عن workflow_run و`actions: write` على وظيفته وحدها وGITHUB_TOKEN وحده؛
  ٥. فقد سكربتٌ ‎.sh شرطَ الاستدعاء من private-ci.yml قبل أوّل أمر، أو حمل set -x، أو حمل `sudo` وهو غيرُ supervise.sh (D11)؛
  ٦. (الإصلاح الرابع · 13-ج) عاد إجراءُ محاكٍ يعمل بحساب العدّاء، أو استدعى المشرفُ adb/emulator/avdmanager بنفسه (أ2)، أو عاد مدخلُ
     التحكّم إلى بيت pcibuild (أ1)، أو ذكر سيرٌ متغيّراً PCI_TEST_* (أ2-٤)، أو لم تكن مهلةُ build هي job_timeout (أ4).
"""
import os
import re
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover — السيرُ يثبّتها؛ وغيابُها لا يُبرّئ (الاختبارُ يُتخطّى ويُرى)
    yaml = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
WF_DIR = os.path.join(ROOT, ".github", "workflows")
MAIN, SWEEPER, SCOPE = "private-ci.yml", "private-ci-sweeper.yml", "private-ci-scope-guard.yml"
SUPERVISOR = "supervise.sh"
ENVS = {"build", "publish"}
SECRETS = {"PCI_DEPLOY_KEY_B64", "PRIVATE_CI_R2_JSON", "PCI_STATUS_TOKEN"}
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
ALLOWED_USES = {
    "plan": {"actions/checkout"},
    # أ2: لا إجراءَ محاكٍ — المحاكي وADB بمستخدم البناء داخل أطوار المشرف
    "build": {"actions/checkout", "actions/setup-java", "actions/upload-artifact"},
    "publish": {"actions/checkout", "actions/download-artifact"},
}
JOB_PERMS = {"plan": {"contents": "read"}, "build": {"contents": "read"},
             "publish": {"contents": "read", "actions": "read"}}
JOB_ENVS = {"plan": None, "build": "build", "publish": "publish"}
PREP_RUN = "bash ctl/jobs/private_ci/supervise.sh prepare"
SUP = 'bash "$RUNNER_TEMP/pci/ctl/supervise.sh"'
READY_IF = "always() && steps.fin.outputs.ready == 'true'"
PREP = PREP_RUN
SECRET_STEP = {  # السرّ ⇒ (الوظيفة، معرّفُ الخطوة، نصُّ run المطلوب)
    "PCI_DEPLOY_KEY_B64": ("build", "clone", f"{SUP} clone"),
    "PRIVATE_CI_R2_JSON": ("publish", "pub", "python3 jobs/private_ci/publish.py"),
    "PCI_STATUS_TOKEN": ("publish", "pub", "python3 jobs/private_ci/publish.py"),
}
TOP_ALLOWED = {".github", "jobs", "README.md", ".gitignore"}


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def workflows():
    if not os.path.isdir(WF_DIR):
        return {}
    return {n: read(os.path.join(WF_DIR, n)) for n in sorted(os.listdir(WF_DIR))}


def parse(text):
    d = yaml.safe_load(text) or {}
    if True in d and "on" not in d:  # PyYAML يقرأ المفتاحَ on قيمةً منطقيّة
        d["on"] = d.pop(True)
    return d


def env_name(job):
    env = job.get("environment")
    if isinstance(env, dict):
        env = env.get("name")
    return env


def tree_violations(root):
    v = []
    top = set(os.listdir(root)) - {".git"}
    for extra in sorted(top - TOP_ALLOWED):
        v.append(f"/{extra}: ليس من محتوى المستودع المخصّص")
    gh = os.path.join(root, ".github")
    if os.path.isdir(gh):
        for extra in sorted(set(os.listdir(gh)) - {"workflows"}):
            v.append(f".github/{extra}: غيرُ مسموح")
    jobs = os.path.join(root, "jobs")
    if os.path.isdir(jobs):
        for extra in sorted(set(os.listdir(jobs)) - {"private_ci"}):
            v.append(f"jobs/{extra}: غيرُ مسموح")
    return v


def find_violations(files):
    v = []
    names = set(files)
    if names != {MAIN, SWEEPER, SCOPE}:
        v.append(f"السيور: {sorted(names)} ≠ الثلاثة المعلَنة")
    for name, text in files.items():
        if name == MAIN:
            continue
        if re.search(r"secrets\.", text):
            v.append(f"{name}: يذكر سرّاً")
        for s in SECRETS:
            if s in text:
                v.append(f"{name}: يذكر {s}")
        if re.search(r"uses:\s*\./\.github/workflows/", text):
            v.append(f"{name}: يستدعي سيراً")
        try:
            d = parse(text)
        except yaml.YAMLError:
            v.append(f"{name}: YAML غيرُ مقروء")
            continue
        for jid, job in (d.get("jobs") or {}).items():
            if isinstance(job, dict) and job.get("environment") is not None:
                v.append(f"{name}/{jid}: يذكر بيئة")
        if name in (SWEEPER, SCOPE) and d.get("permissions") != {}:
            v.append(f"{name}: الصلاحياتُ على مستوى السير ليست {{}}")
        if name not in (SWEEPER, SCOPE):
            v.append(f"{name}: سيرٌ غيرُ معلَن")
    if MAIN in files:
        v += main_violations(files[MAIN])
    if SWEEPER in files:
        v += sweeper_violations(files[SWEEPER])
    if SCOPE in files:
        v += scope_wf_violations(files[SCOPE])
    return v


def main_violations(text):
    v = []
    d = parse(text)
    on = d.get("on") or {}
    if set(on) != {"repository_dispatch", "workflow_dispatch"}:
        v.append(f"{MAIN}: الزنادُ يجب أن يكون repository_dispatch وworkflow_dispatch وحدهما")
    rd = on.get("repository_dispatch") or {}
    if (rd.get("types") if isinstance(rd, dict) else None) != ["private-ci"]:
        v.append(f"{MAIN}: repository_dispatch بنوعٍ غير private-ci")
    wd = on.get("workflow_dispatch") or {}
    if set(((wd.get("inputs") or {}) if isinstance(wd, dict) else {})) != {"dry_run", "plant_leak"}:
        v.append(f"{MAIN}: مدخلاتُ workflow_dispatch غيرُ dry_run وplant_leak")
    if d.get("permissions") != {}:
        v.append(f"{MAIN}: permissions أعلى السير ليست {{}}")
    conc = d.get("concurrency") or {}
    if not isinstance(conc, dict) or conc.get("cancel-in-progress") is not False:
        v.append(f"{MAIN}: concurrency بلا cancel-in-progress: false")
    if re.search(r"(?m)^\s*set\s+-[a-z]*x|set\s+-o\s+xtrace|\bbash\s+-x\b", text):
        v.append(f"{MAIN}: set -x")
    if re.search(r"\b(?:cat|tail|head|less|more)\b[^\n|]*log\.txt", text):
        v.append(f"{MAIN}: يطبع السجلّ")
    if "pull_request_target" in text or "workflow_call" in text:
        v.append(f"{MAIN}: زنادٌ ممنوع في النصّ")
    if "PCI_TEST_" in text:
        v.append(f"{MAIN}: متغيّرُ اختبارٍ PCI_TEST_* في السير")
    if "android-emulator-runner" in text:
        v.append(f"{MAIN}: إجراءُ محاكٍ بحساب العدّاء")
    jobs = d.get("jobs") or {}
    if set(jobs) != {"plan", "build", "publish"}:
        v.append(f"{MAIN}: الوظائفُ غيرُ plan/build/publish")
    seen_secret = {s: 0 for s in SECRETS}
    for jid, job in jobs.items():
        if not isinstance(job, dict):
            continue
        if "timeout-minutes" not in job:
            v.append(f"{MAIN}/{jid}: بلا timeout-minutes")
        if job.get("permissions") != JOB_PERMS.get(jid):
            v.append(f"{MAIN}/{jid}: أذوناتٌ غيرُ {JOB_PERMS.get(jid)}")
        if env_name(job) != JOB_ENVS.get(jid):
            v.append(f"{MAIN}/{jid}: بيئةٌ غيرُ {JOB_ENVS.get(jid)}")
        if "secrets." in str(job.get("env") or {}):
            v.append(f"{MAIN}/{jid}: سرٌّ في env الوظيفة")
        if "secrets" in job or job.get("uses"):
            v.append(f"{MAIN}/{jid}: وظيفةٌ تستدعي سيراً أو ترث الأسرار")
        if jid == "build":
            v += build_job_violations(job.get("steps") or [])
        for st in job.get("steps") or []:
            uses = st.get("uses", "") or ""
            w = st.get("with") or {}
            run = st.get("run", "") or ""
            env_txt = str(st.get("env") or {})
            if uses:
                base = uses.split("@")[0]
                if base not in ALLOWED_USES.get(jid, set()):
                    v.append(f"{MAIN}/{jid}: إجراءٌ غيرُ مسموح {base}")
                if not SHA_PIN.match(uses):
                    v.append(f"{MAIN}/{jid}: إجراءٌ غيرُ مثبَّتٍ بـSHA {base}")
                if base == "actions/checkout":
                    if w.get("persist-credentials") is not False:
                        v.append(f"{MAIN}/{jid}: checkout يحفظ الاعتماد")
                    if "repository" in w or "ref" in w:
                        v.append(f"{MAIN}/{jid}: checkout لغير هذا المستودع عند github.sha")
                if base == "actions/setup-java" and "cache" in w:
                    v.append(f"{MAIN}/{jid}: setup-java بذاكرةٍ مؤقّتة")
                if base == "actions/upload-artifact":
                    if w.get("retention-days") != 1:
                        v.append(f"{MAIN}/{jid}: Artifact بغير يومٍ واحد")
                    if not str(w.get("path", "")).rstrip("/").endswith("/pci/art"):
                        v.append(f"{MAIN}/{jid}: Artifact من غير مجلّد art")
                    if not str(w.get("name", "")).startswith("pci-"):
                        v.append(f"{MAIN}/{jid}: اسمُ Artifact غيرُ pci-")
                if base == "actions/download-artifact" and w.get("pattern") != "pci-*":
                    v.append(f"{MAIN}/{jid}: تنزيلٌ بغير pci-*")
            if "secrets." in run or "secrets." in str(w):
                v.append(f"{MAIN}/{jid}: سرٌّ داخل run/with لا في env")
            for sec in re.findall(r"secrets\.(\w+)", env_txt):
                if sec not in SECRET_STEP:
                    v.append(f"{MAIN}/{jid}: سرٌّ غيرُ معلَن {sec}")
                    continue
                want_job, want_id, want_run = SECRET_STEP[sec]
                seen_secret[sec] += 1
                if (jid, st.get("id"), run.strip()) != (want_job, want_id, want_run):
                    v.append(f"{MAIN}/{jid}: السرّ {sec} خارج خطوته")
            if jid == "publish" and run and run.strip() not in (
                    "python3 jobs/private_ci/publish.py",) and not run.strip().startswith("echo "):
                v.append(f"{MAIN}/publish: خطوةٌ تنفّذ غيرَ publish.py")
        if jid == "publish" and "always()" not in str(job.get("if", "")):
            v.append(f"{MAIN}/publish: بلا always()")
        if jid == "build" and str(job.get("timeout-minutes")) != "${{ matrix.leg.job_timeout }}":
            v.append(f"{MAIN}/build: المهلةُ يجب أن تكون job_timeout (الميزانية + هامش finalize)")
        if jid == "build" and job.get("name") != "build-${{ matrix.leg.id }}":
            v.append(f"{MAIN}/build: اسمُ الوظيفة يجب أن يكون build-<شوط> (يقرؤه publish)")
    for sec, n in seen_secret.items():
        if n != 1:
            v.append(f"{MAIN}: السرّ {sec} يُذكر {n} مرّة (المطلوب مرّةٌ واحدة)")
    return v


def build_job_violations(steps):
    """D11/D13: ترتيبُ خطوات build وشروطُها — prep ← clone ← بناء/محاكي ← fin ← رفعٌ مشروطٌ بـready."""
    v = []
    ids = [st.get("id") for st in steps]
    for need in ("prep", "clone", "fin"):
        if ids.count(need) != 1:
            v.append(f"{MAIN}/build: الخطوةُ {need} يجب أن تُذكر مرّةً واحدة")
            return v
    ip, ic, ifin = ids.index("prep"), ids.index("clone"), ids.index("fin")
    if not ip < ic < ifin:
        v.append(f"{MAIN}/build: الترتيبُ يجب أن يكون prep ثمّ clone ثمّ fin")
    for st in steps[:ip]:
        if st.get("run"):
            v.append(f"{MAIN}/build: خطوةٌ تنفّذ شيئاً قبل prep")
    if str(steps[ip].get("run", "")).strip() != PREP_RUN:
        v.append(f"{MAIN}/build: prep يجب أن تشغّل المشرفَ من مساحة العمل بالأمر prepare وحده")
    if str(steps[ic].get("if", "")).strip() != "steps.prep.outcome == 'success'":
        v.append(f"{MAIN}/build: clone غيرُ مشروطةٍ بنجاح prep")
    fin = steps[ifin]
    if str(fin.get("if", "")).strip() != "always()":
        v.append(f"{MAIN}/build: fin يجب أن تكون if: always()")
    if str(fin.get("run", "")).strip() != f"{SUP} finalize":
        v.append(f"{MAIN}/build: fin لا تشغّل supervise.sh finalize")
    uploads = 0
    for i, st in enumerate(steps):
        run = str(st.get("run", "") or "")
        script = str((st.get("with") or {}).get("script", "") or "")
        text = run + "\n" + script
        cond = str(st.get("if", "") or "").strip()
        if i > ip and "private_ci" in text:
            v.append(f"{MAIN}/build: خطوةٌ بعد prep تستدعي أدواتٍ من مساحة العمل (يجب $RUNNER_TEMP/pci/ctl)")
        if i > ip and "supervise.sh" in text:
            if "$RUNNER_TEMP/pci/ctl/supervise.sh" not in text:
                v.append(f"{MAIN}/build: المشرفُ بعد prep يجب أن يُستدعى من $RUNNER_TEMP/pci/ctl")
            mm = re.search(r'supervise\.sh"?\s+(\w+)', text)
            mode = mm.group(1) if mm else ""
            if mode in ("build", "emu") and "steps.clone.outcome == 'success'" not in cond:
                v.append(f"{MAIN}/build: {mode} غيرُ مشروطٍ بنجاح الاستنساخ")
            if mode in ("prepare",):
                v.append(f"{MAIN}/build: prepare بعد prep")
        if i < ic and i != ip and "supervise.sh" in text:
            v.append(f"{MAIN}/build: المشرفُ قبل clone")
        if i > ip and "guard.py" in text and "$RUNNER_TEMP/pci/ctl/guard.py" not in text:
            v.append(f"{MAIN}/build: guard.py من غير ctl الموثوق")
        if str(st.get("uses", "") or "").startswith("actions/upload-artifact"):
            uploads += 1
            if cond != READY_IF:
                v.append(f"{MAIN}/build: شرطُ الرفع يجب أن يكون نصّاً «{READY_IF}»")
            if i < ifin:
                v.append(f"{MAIN}/build: الرفعُ قبل fin")
    if uploads != 1:
        v.append(f"{MAIN}/build: خطوةُ رفعٍ واحدةٌ مطلوبة ({uploads})")
    return v


def sweeper_violations(text):
    v = []
    d = parse(text)
    if set((d.get("on") or {})) != {"workflow_run"}:
        v.append(f"{SWEEPER}: الزنادُ غيرُ workflow_run")
    for jid, job in (d.get("jobs") or {}).items():
        if job.get("permissions") != {"actions": "write", "contents": "read"}:
            v.append(f"{SWEEPER}/{jid}: أذوناتٌ غيرُ actions: write وcontents: read")
    return v


def scope_wf_violations(text):
    v = []
    d = parse(text)
    if set((d.get("on") or {})) != {"pull_request", "push"}:
        v.append(f"{SCOPE}: الزنادُ غيرُ pull_request وpush")
    for jid, job in (d.get("jobs") or {}).items():
        if job.get("permissions") != {"contents": "read"}:
            v.append(f"{SCOPE}/{jid}: أذوناتٌ غيرُ contents: read")
    return v


def sh_violations(jobs_dir):
    v = []
    for name in sorted(os.listdir(jobs_dir)):
        if not name.endswith(".sh"):
            continue
        body = read(os.path.join(jobs_dir, name))
        head = body.split("exec >>", 1)[0] if "exec >>" in body else body.split("MODE=", 1)[0]
        if "*/.github/workflows/private-ci.yml@*" not in head or "GITHUB_WORKFLOW_REF" not in head:
            v.append(f"jobs/private_ci/{name}: شرطُ الاستدعاء غائبٌ قبل أوّل أمر")
        if re.search(r"(?m)^\s*set\s+-[a-z]*x", body):
            v.append(f"jobs/private_ci/{name}: set -x")
        # D11: sudo للمشرف وحده (التهيئةُ والتحصينُ والأوامرُ الثابتةُ) — لا في build.sh ولا clone.sh ولا غيرِهما
        if name != SUPERVISOR and re.search(r"(?m)^[^#\n]*\bsudo\b", body):
            v.append(f"jobs/private_ci/{name}: sudo خارج supervise.sh")
        if name == SUPERVISOR:
            # أ2: المشرفُ (حسابُ العدّاء) لا يشغّل adb ولا المحاكي ولا avdmanager — تلك بمستخدم البناء وحده
            if re.search(r"(?m)^[^#\n]*(?:platform-tools/adb|emulator/emulator|\bavdmanager\b|\badb\s+(?:start-server|-s|shell|kill))",
                         body):
                v.append(f"jobs/private_ci/{name}: المشرفُ يشغّل adb أو المحاكي")
            # أ1: مدخلُ التحكّم في شجرةٍ يملكها root لا في بيت مستخدم البناء
            if 'CTL_DIR="/var/lib/pci-ctl"' not in body or "/home/pcibuild/ctl" in body:
                v.append(f"jobs/private_ci/{name}: مدخلُ التحكّم ليس في /var/lib/pci-ctl")
    if SUPERVISOR not in set(os.listdir(jobs_dir)):
        v.append(f"jobs/private_ci/{SUPERVISOR}: غائب")
    return v


@unittest.skipIf(yaml is None, "PyYAML غيرُ مثبّت")
class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.files = workflows()
        if not self.files:
            self.skipTest("لا مجلّدَ .github/workflows في هذه الشجرة (استنساخٌ جزئيّ)")

    def test_repository_is_clean(self):
        self.assertEqual(find_violations(self.files), [])

    def test_tree(self):
        self.assertEqual(tree_violations(ROOT), [])

    def test_sh(self):
        self.assertEqual(sh_violations(HERE), [])


@unittest.skipIf(yaml is None, "PyYAML غيرُ مثبّت")
class ScopeNegativeTest(unittest.TestCase):
    """الحارسُ يُمسك المخالفاتِ المزروعة — لا يكفي أن يمرّ على الشجرة النظيفة."""

    def setUp(self):
        self.files = workflows()
        if not self.files:
            self.skipTest("لا مجلّدَ سيور")

    def assertCaught(self, files, label):
        self.assertTrue(find_violations(files), f"{label} مرّ")

    def test_other_workflows(self):
        evil = {
            "agent_cmd.yml": "on: push\npermissions: {}\njobs: {}\n",
            "env.yml": "on: push\njobs:\n  x:\n    runs-on: ubuntu-latest\n    environment: build\n    steps: []\n",
            "sec.yml": "on: push\njobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ${{ secrets.PCI_STATUS_TOKEN }}\n",
        }
        for name, text in evil.items():
            with self.subTest(name=name):
                self.assertCaught(dict(self.files, **{name: text}), name)
        sw = self.files[SWEEPER].replace("GH_TOKEN: ${{ github.token }}", "GH_TOKEN: ${{ secrets.X }}", 1)
        self.assertCaught(dict(self.files, **{SWEEPER: sw}), "sweeper-secret")
        sw2 = self.files[SWEEPER].replace("      actions: write\n      contents: read\n", "      actions: write\n      contents: write\n", 1)
        self.assertNotEqual(sw2, self.files[SWEEPER])
        self.assertCaught(dict(self.files, **{SWEEPER: sw2}), "sweeper-perms")

    def test_weakened_private_ci_is_caught(self):
        good = self.files[MAIN]
        BLD = '        run: bash "$RUNNER_TEMP/pci/ctl/supervise.sh" build\n'
        m = {
            "pull_request": good.replace("  workflow_dispatch:\n", "  pull_request:\n  workflow_dispatch:\n", 1),
            "top-perms": good.replace("permissions: {}\n", "permissions:\n  contents: write\n", 1),
            "job-perms": good.replace("      contents: read\n      actions: read\n",
                                      "      contents: read\n      actions: write\n", 1),
            "unpinned": good.replace("actions/setup-java@cf277c60eb25467037889841efdb72551f06f6c3", "actions/setup-java@v4", 1),
            "cache": good.replace("      - name: تهيئة العزل واختباره\n",
                                  "      - uses: actions/cache@0400d5f644dc74513175e3cd8d07132dd4860809\n        with: {path: x, key: y}\n      - name: تهيئة العزل واختباره\n", 1),
            "retention": good.replace("retention-days: 1", "retention-days: 7", 1),
            "key-in-build": good.replace(BLD,
                                         "        env:\n          K: ${{ secrets.PCI_DEPLOY_KEY_B64 }}\n" + BLD, 1),
            "r2-in-build": good.replace(BLD,
                                        "        env:\n          K: ${{ secrets.PRIVATE_CI_R2_JSON }}\n" + BLD, 1),
            "key-in-prep": good.replace("        id: prep\n", "        id: prep\n        env:\n          K: ${{ secrets.PCI_DEPLOY_KEY_B64 }}\n", 1),
            "build-unconditional": good.replace("        if: steps.clone.outcome == 'success'\n" + BLD, BLD, 1),
            "emu-unconditional": good.replace("        if: steps.clone.outcome == 'success' && matrix.leg.has_emu\n", "", 1),
            "emu-after-prep-only": good.replace("steps.clone.outcome == 'success' && matrix.leg.has_emu", "steps.prep.outcome == 'success' && matrix.leg.has_emu", 1),
            "clone-unconditional": good.replace("        if: steps.prep.outcome == 'success'\n", "", 1),
            "clone-on-always": good.replace("if: steps.prep.outcome == 'success'", "if: always()", 1),
            "upload-always-only": good.replace(READY_IF, "always()", 1),
            "upload-no-ready": good.replace("        if: " + READY_IF + "\n", "", 1),
            "upload-ready-no-always": good.replace(READY_IF, "steps.fin.outputs.ready == 'true'", 1),
            "upload-wrong-output": good.replace("steps.fin.outputs.ready == 'true'", "steps.fin.outputs.ready != 'false'", 1),
            "fin-not-always": good.replace("        id: fin\n        if: always()\n", "        id: fin\n", 1),
            "fin-wrong-run": good.replace(SUP + " finalize", SUP + " emu", 1),
            "workspace-tools-after-prep": good.replace(BLD, "        run: bash ctl/jobs/private_ci/supervise.sh build\n", 1),
            "plant-from-workspace": good.replace('python3 -I -S "$RUNNER_TEMP/pci/ctl/guard.py" --emit-plant', "python3 -I -S ctl/jobs/private_ci/guard.py --emit-plant", 1),
            "supervisor-from-workspace-clone": good.replace(SUP + " clone", "bash ctl/jobs/private_ci/supervise.sh clone", 1),
            "exec-before-prep": good.replace("      - name: تهيئة العزل واختباره\n", "      - run: echo hi\n      - name: تهيئة العزل واختباره\n", 1),
            "prep-after-clone": good.replace("        id: prep\n", "        id: prepx\n", 1),
            "prep-extra-args": good.replace(PREP, PREP + " && bash x.sh", 1),
            "setx": good.replace(BLD, "        run: |\n          set -x\n          " + SUP + " build\n", 1),
            "cat-log": good.replace("run: python3 jobs/private_ci/publish.py", "run: cat $RUNNER_TEMP/pci/out/log.txt", 1),
            "persist": good.replace("persist-credentials: false", "persist-credentials: true", 1),
            "publish-exec-artifact": good.replace("run: python3 jobs/private_ci/publish.py",
                                                  "run: bash ${{ runner.temp }}/in/pci-main/x.sh", 1),
            "env-swap": good.replace("    environment: publish\n", "    environment: build\n", 1),
            "cancel": good.replace("cancel-in-progress: false", "cancel-in-progress: true", 1),
            "no-always": good.replace("if: always() && needs.plan.outputs.sha != ''", "if: needs.plan.outputs.sha != ''", 1),
            "checkout-other": good.replace("          sparse-checkout: jobs/private_ci\n          path: ctl\n",
                                           "          sparse-checkout: jobs/private_ci\n          path: ctl\n          ref: main\n", 1),
            "extra-job": good + "\n  extra:\n    runs-on: ubuntu-latest\n    timeout-minutes: 1\n    permissions: {}\n    steps: []\n",
            # الإصلاح الرابع
            "emu-action-back": good.replace('        run: bash "$RUNNER_TEMP/pci/ctl/supervise.sh" emu\n',
                                            "        uses: reactivecircus/android-emulator-runner@a421e43855164a8197daf9d8d40fe71c6996bb0d\n"
                                            '        with:\n          script: bash "$RUNNER_TEMP/pci/ctl/supervise.sh" emu\n', 1),
            "test-flag-in-env": good.replace("      PCI_LINT: ${{ matrix.leg.lint }}\n",
                                             "      PCI_LINT: ${{ matrix.leg.lint }}\n      PCI_TEST_NO_ISOLATION: '1'\n", 1),
            "budget-timeout": good.replace("timeout-minutes: ${{ matrix.leg.job_timeout }}", "timeout-minutes: ${{ matrix.leg.timeout }}", 1),
        }
        for label, text in m.items():
            with self.subTest(m=label):
                self.assertNotEqual(text, good, f"الطفرة {label} لم تُطبَّق")
                self.assertCaught(dict(self.files, **{MAIN: text}), label)

    def test_tree_and_sh_mutations(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for p in (".github/workflows", "jobs/private_ci", "tools/ci_fleet"):
                os.makedirs(os.path.join(d, p))
            open(os.path.join(d, "README.md"), "w").close()
            self.assertTrue(any("tools" in x for x in tree_violations(d)))
            with open(os.path.join(d, "jobs", "private_ci", "x.sh"), "w") as f:
                f.write("#!/bin/bash\nset -x\necho hi\n")
            v = sh_violations(os.path.join(d, "jobs", "private_ci"))
            self.assertTrue(any("شرطُ الاستدعاء" in x for x in v))
            self.assertTrue(any("set -x" in x for x in v))

    def test_sudo_only_in_supervisor(self):
        """D11: sudo في المشرف وحده؛ وفي أيّ .sh آخر مخالفة (ولا يُحسب ذكرُه في تعليق)."""
        import tempfile
        guard_head = ('#!/bin/bash\ncase "${GITHUB_WORKFLOW_REF:-}" in\n  */.github/workflows/private-ci.yml@*) ;;\n'
                      '  *) exit 2 ;;\nesac\nMODE=x\n')
        with tempfile.TemporaryDirectory() as d:
            def put(name, body):
                with open(os.path.join(d, name), "w") as f:
                    f.write(guard_head + body)
            put(SUPERVISOR, 'CTL_DIR="/var/lib/pci-ctl"\nsudo -n true\n')
            put("build.sh", "# بلا sudo أبداً\necho ok\n")
            self.assertEqual(sh_violations(d), [])
            put("build.sh", "echo ok\nsudo -n rm -rf /x\n")
            self.assertTrue(any("build.sh" in x and "sudo" in x for x in sh_violations(d)))
            put("build.sh", "echo ok\nif sudo -n true; then :; fi\n")
            self.assertTrue(any("build.sh" in x and "sudo" in x for x in sh_violations(d)))
            put("clone.sh", "x=1 sudo cp a b\n")
            self.assertTrue(any("clone.sh" in x for x in sh_violations(d)))
            os.remove(os.path.join(d, SUPERVISOR))
            self.assertTrue(any(SUPERVISOR in x for x in sh_violations(d)))

    def test_supervisor_never_runs_adb_and_keeps_control_entry_root_owned(self):
        """أ1 · أ2: طفراتٌ في المشرف يمسكها الحارس — adb/المحاكي بحساب العدّاء، أو مدخلُ التحكّم في بيت pcibuild."""
        import tempfile
        good = read(os.path.join(HERE, SUPERVISOR))
        self.assertEqual([x for x in sh_violations(HERE) if SUPERVISOR in x], [])
        muts = {
            "adb": good.replace("  ran_add cleanup\n", '  ran_add cleanup\n  "$sdk/platform-tools/adb" kill-server\n', 1),
            "adb-shell": good.replace("  ran_add cleanup\n", "  ran_add cleanup\n  adb -s emulator-5554 emu kill\n", 1),
            "emulator": good.replace("  ran_add cleanup\n", '  ran_add cleanup\n  "$sdk/emulator/emulator" -avd x &\n', 1),
            "avdmanager": good.replace("  ran_add cleanup\n", "  ran_add cleanup\n  avdmanager delete avd -n pci\n", 1),
            "ctl-in-home": good.replace('CTL_DIR="/var/lib/pci-ctl"', 'CTL_DIR="/home/pcibuild/ctl"', 1),
        }
        with tempfile.TemporaryDirectory() as d:
            for label, body in muts.items():
                with self.subTest(m=label):
                    self.assertNotEqual(body, good, label)
                    with open(os.path.join(d, SUPERVISOR), "w", encoding="utf-8") as f:
                        f.write(body)
                    self.assertTrue(any(SUPERVISOR in x for x in sh_violations(d)), label)


if __name__ == "__main__":
    unittest.main()
