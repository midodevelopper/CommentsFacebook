# CommentsFacebook — دليل المشروع

أرشفة منشورات وتعليقات جروبات فيسبوك إلى Google Sheet، والرد عليها من الشيت،
عن طريق كروميوم حقيقي متحكَّم فيه بـ CDP على بروفايل ثابت.

ريبو: https://github.com/midodevelopper/CommentsFacebook (عام / public)

---

## ⚠️ قاعدة إلزامية: كل تحديث يتسجّل هنا

**أي تعديل في المشروع لازم يتضاف في قسم [سجل التحديثات](#سجل-التحديثات) في آخر الملف ده،
في نفس الـ commit بتاع التعديل.** ده ذاكرة المشروع — مش ملف اختياري.

لكل مدخل: التاريخ، وإيه اللي اتغير، و**ليه**. السبب أهم من الوصف؛
الكود بيوصف نفسه، إنما النية لأ.

ترتيب الإدخال: الأحدث فوق.

---

## الفروع

الشغل كله على فرع **`dev`**. مفيش commit مباشر على `main`.

```powershell
git switch dev
git push origin dev
```

`main` هو الخط المنشور المستقر، وبيتقدّم بالدمج (merge / PR) من `dev` بس.
قبل أي commit، اتأكد إنك على `dev`.

---

## حدود ثابتة ماتتغيرش

1. **بروفايل واحد بس:** `D:\Hermes Browser\profile`
   المتصفح مايفتحش على بروفايل تاني أبدًا. سبب وجوده إن جلسة فيسبوك تتسجّل
   مرة واحدة وتفضل محفوظة. أي حاجة بتفتح بروفايل مؤقت = تسجيل الدخول بيضيع.

2. **`profile/` و `dumps/` و `debug/` مستبعدين من git.**
   `profile/` فيه كوكيز جلسة حقيقية (~198MB). `dumps/` و `debug/` فيهم بيانات
   مستخدمين حقيقيين وصور صفحات مسجّل فيها دخول. الريبو **عام**، فالاستبعاد ده أمان مش ترتيب.

3. **السحب قراءة فقط.** `fb_groups_to_sheet.py` مابيردّش ومابيعملش لايك ومابيكتبش حاجة.
   الكتابة في `fb_return_replies.py` بس.

4. **الفرز مش اختياري.** المنشورات زمني (`CHRONOLOGICAL`)، والتعليقات «الأحدث».
   لو فرز التعليقات مانجحش، السكربت **بيوقف** بـ `SystemExit` بدل ما يسحب ترتيب غلط.
   ماتخليهوش يكمل بصمت.

5. **الضغط على عناصر فيسبوك لازم يكون حقيقي** (`locator.click()`)، مش `element.click()`
   من JavaScript، في فتح قوائم الفرز. الـ React handlers مابتردّش على النقر الصناعي.
   استثناء: `expand_comments` بتستخدم JS عن قصد لأنها بتضغط عشرات الأزرار في لفة واحدة.

---

## البنية

| الملف | الوظيفة |
| --- | --- |
| `launch_browser.py` | يفتح كروميوم Playwright على البروفايل الثابت + منفذ CDP. بيحلّ مسار كروميوم ديناميكيًا |
| `start-cdp-browser.cmd` | wrapper بيشغّل اللي فوق من الـ venv |
| `fb_groups_to_sheet.py` | السحب: جروب → منشورات → تعليقات → الشيت |
| `fb_return_replies.py` | الرد: عمود `reply` في الشيت → تعليقات فيسبوك |
| `browser_debug.py` | التشخيص: HTML، كونسول، أخطاء، شبكة، صور، storage |
| `facebook_sheet_receiver.gs` | Apps Script: upsert بـ `post_id` / `comment_id` بدون تكرار |
| `cdp-ready.html` | صفحة البداية في النافذة المتحكَّم فيها |

## التشغيل

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium

.\start-cdp-browser.cmd                      # افتح المتصفح، وسجّل دخول فيسبوك مرة واحدة
.venv\Scripts\python.exe fb_groups_to_sheet.py --group URL --min-posts 1 --max-posts 5 --min-comments 1 --max-comments 30
.venv\Scripts\python.exe fb_return_replies.py
```

كل السكربتات بتتوصل على المتصفح المفتوح بـ `connect_over_cdp`؛ مفيش سكربت بيفتح متصفح لوحده
غير `launch_browser.py`.

## التشخيص لما حاجة تبوظ

فيسبوك بيغيّر الـ DOM من غير سابق إنذار، فأغلب الأعطال سببها selector بار.
التشخيص بيوصل لده في خطوة واحدة:

```powershell
# ابدأ من هنا: إيه اللي بار في الـ DOM؟
.venv\Scripts\python.exe browser_debug.py selectors --url "https://www.facebook.com/groups/G/posts/P/"

# دَمب تلقائي عند أي فشل أثناء الشغل (زوّد --trace لتتبّع كامل + HAR)
.venv\Scripts\python.exe fb_groups_to_sheet.py --group URL ... --debug
.venv\Scripts\python.exe fb_return_replies.py --debug

# لقطة يدوية للحالة الحالية
.venv\Scripts\python.exe browser_debug.py snapshot
.venv\Scripts\python.exe browser_debug.py snapshot --url "https://www.facebook.com/groups/123"

# مراقبة لايف وإنت بتتصرف في المتصفح بإيدك
.venv\Scripts\python.exe browser_debug.py watch
```

المخرجات في `debug/<الوقت>-<اسم>-<سبب>/`:
`summary.txt` (ابدأ منه)، `page.html`، `page.png`، `page.txt`،
`console.jsonl`، `errors.jsonl`، `network.jsonl`، `browser-log.jsonl`،
`lifecycle.jsonl`، `storage.json`، `meta.json`.

في الكود:

```python
from browser_debug import PageRecorder, recording

with recording(page, "post-123") as rec:   # دَمب تلقائي لو حصل استثناء
    page.goto(url)
    rec.mark("after-goto")
```

`PageRecorder.dump()` **مابيرفعش استثناء أبدًا** — لازم يفضل يشتغل والصفحة بايظة.
لما تزوّد التقاط جديد، حافظ على الخاصية دي.

جدول الـ selectors كله في `PROBES` في `browser_debug.py`. **أي selector جديد يتضاف
في الكود، لازم يتضاف هناك كمان** — ده اللي بيخلي الفحص يفضل نافع.

`--trace` / `--har` على مستوى الـ context مش الصفحة، فمسجّل واحد بس بياخدهم
في نفس الوقت. في `watch` أول تاب بس هو اللي بياخدهم.

## أسرار

`TOKEN` ورابط الـ Apps Script `/exec` **مكتوبين في الكود** في
`fb_groups_to_sheet.py` و `fb_return_replies.py` و `facebook_sheet_receiver.gs`،
والريبو عام. ده قرار صاحب المشروع وهو عارفه.
`fb_groups_to_sheet.py` بيقرأ `FB_SHEET_TOKEN` من البيئة لو موجود.
لو اتقرر التدوير: غيّر التوكن في الـ `.gs`، انشر deployment جديد، وحدّث الرابط في السكربتين.

---

## سجل التحديثات

### 2026-10-21 — فحص selectors، تتبّع Playwright، HAR، و MCP
- **`browser_debug.py selectors` جديد** — بيختبر 20 selector المشروع معتمد عليها على
  صفحة حقيقية ويقول كل واحد لقى كام عنصر، مصنّفين `critical` / `degraded` / `context`،
  وبيستنتج نوع الصفحة (post / feed) من الرابط فيتجاهل اللي مش بتاعها.
  exit code 1 لو حاجة `critical` بارت.
  **ليه:** فشل السحب بيطلع كـ `no-comment` أو `no-reply-button` من غير دليل. الأمر ده
  بيحوّل التخمين لسطر واحد محدد.
- **تتبّع Playwright و HAR اتفعلوا.** متأكد عمليًا إن `tracing.start()` و
  `tracing.start_har()` (1.60+) **بيشتغلوا فوق `connect_over_cdp`** على 1.64 —
  `trace.zip` طلع 110KB بـ 6 فريمات صور و DOM snapshot و stacks و sources.
  حد معروف: الخطوات اللي بتتعمل بـ CDP خام مش بتظهر في الخط الزمني، لكن خطوات
  Playwright (goto/click/evaluate) بتظهر — وده اللي السكربتات بتستخدمه.
  كل دَمب بياخد trace chunk لوحده (`start_chunk`/`stop_chunk`) فالدمبات المتعددة
  في `fb_return_replies.py` كل واحد ليه تتبّعه.
- **`perf.json`** من `Performance.getMetrics` عبر CDP (36 عدّاد).
- **`--trace` في السكربتين** (مع `--debug`).
- **`.mcp.json`**: سيرفر `chrome-devtools-mcp` موصّل على `--browser-url=http://127.0.0.1:9333`.
  محتاج موافقة تفاعلية أول مرة (`claude` في مجلد المشروع). Node ≥ 23.
- **`page.accessibility` مابقاش موجود** في Playwright Python 1.64 — ماتستخدمهوش.

نتايج بحث تخص المشروع:
- **Facebook Groups API ميّت خلاص.** اتشال من Graph API v19 في 23 يناير 2024،
  واتمسح من كل النسخ في 22 أبريل 2024، ولسه مش موجود في v26 (أكتوبر 2026).
  `publish_to_groups` و `groups_access_member_info` اتشالوا كمان. يعني **مفيش بديل
  رسمي** لقراءة منشورات وتعليقات الجروبات — أتوماتة المتصفح هي الطريق الوحيد،
  وده بيثبت إن معمارية المشروع صح.
- **مفيش stealth plugins بقصد.** كروم حقيقي + بروفايل ثابت + CDP = أقل بصمة أصلًا.
  حِزَم الـ stealth بتعالج بصمة الـ headless، وإحنا مش headless.
- مفيش `pytest-playwright` لحد ما يبقى في اختبارات تستاهل.

### 2026-10-21 — فرع `dev` للشغل
- **فرع `dev`** اتعمل من `3c1a6b9`، وبقى هو مكان كل الـ commits والـ pushes الجديدة.
  **ليه:** `main` اتنشر وخلاص، فيفضل خط مستقر، والشغل الجاري يبعد عنه.
  `main` مابياخدش commit مباشر؛ بيتقدّم بالدمج من `dev` بس.

### 2026-10-21 — أدوات تشخيص المتصفح
- **جديد `browser_debug.py`**: يلتقط HTML، كونسول (بمكانه في الكود)، استثناءات غير ملتقطة
  بـ stack trace، طلبات فاشلة وردود 4xx/5xx، سجل المتصفح من CDP
  (`Log.entryAdded` — بيجيب أخطاء CORS و CSP و mixed content اللي الكونسول العادي بيفوّتها)،
  صورة full-page، النص الظاهر، localStorage/sessionStorage، وملخص بشري.
  أوامر: `snapshot` و `watch`. و API: `PageRecorder` و `recording()`.
  **ليه:** لما فيسبوك يغيّر الـ DOM، السكربت بيفشل بـ `no-comment` أو `no-reply-button`
  من غير أي دليل. كان لازم لقطة كاملة لحظة الفشل بدل إعادة التشغيل والتخمين.
- **`--debug` في `fb_groups_to_sheet.py` و `fb_return_replies.py`.**
  في السحب، الالتقاط بيمسك `BaseException` مش `Exception` — لأن فشل فرز التعليقات
  بيرفع `SystemExit`، وده بالظبط الفشل اللي محتاجين نشوف صفحته.
  في الردود، بيعمل دَمب للحالات اللي بتفشل من غير استثناء (`no-comment` / `no-reply-button`).
- **`debug/` مستبعد من git** — فيه HTML وصور لصفحات مسجّل فيها دخول.
- **`FB_CDP` في `fb_return_replies.py`** + `--cdp`، بدل عنوان مكتوب في الكود،
  توحيدًا مع باقي السكربتات.
- الكوكيز في الدَمب: **أسماء ونطاقات بس، من غير قيم.** القيم بتساوي الجلسة نفسها.

### 2026-10-21 — إعداد البيئة والريبو
- **venv** في `.venv` + `requirements.txt` (playwright 1.64، websocket-client، requests)،
  وكروميوم Playwright منزّل (build 1248).
- **`launch_browser.py` جديد** بيحلّ مسار كروميوم من Playwright نفسه.
  **ليه:** `start-cdp-browser.cmd` كان فيه `chromium-1234` مكتوب في الكود،
  والمنزّل بقى 1248 — فاللانشر كان بيوقع بعد أي تحديث لـ Playwright.
  كمان بيتأكد إن المنفذ رد قبل ما يقول جاهز، ومابيفتحش متصفح تاني لو واحد شغال.
- `start-cdp-browser.cmd` بقى wrapper على اللي فوق، بيستخدم بايثون الـ venv.
- **`.gitignore`**: `profile/` و `dumps/` و `.venv/`.
- **`README.md`** جديد، و **git init على `main`**، ورفع لريبو عام `CommentsFacebook`.
- متأكد إنه ماشي: Playwright اتوصل بـ CDP على المتصفح الشغال، و 9 كوكيز فيسبوك
  موجودة في البروفايل — يعني الجلسة بتفضل محفوظة فعلًا.
