# CommentsFacebook

أرشفة منشورات وتعليقات جروبات فيسبوك إلى Google Sheet، والرد على التعليقات من الشيت —
عبر متصفح كروميوم حقيقي متحكَّم فيه بـ **CDP** (Chrome DevTools Protocol) على **بروفايل ثابت**.

Archives Facebook group posts/comments into a Google Sheet, and posts replies back from
that sheet, by driving a real Chromium over CDP against one fixed browser profile.

---

## المتصفح: بروفايل واحد ثابت

المتصفح بيشتغل **دايمًا** على البروفايل:

```
profile\        (جوه فولدر المشروع، أيًا كان مكانه)
```

ده المقصود منه: تسجّل دخول فيسبوك **مرة واحدة** والجلسة تفضل محفوظة، فالسكربتات
بتتوصل على نفس المتصفح المفتوح بدل ما تفتح متصفح جديد كل مرة.

> `profile/` مستبعد من الريبو في `.gitignore` لأنه فيه كوكيز وجلسات دخول حقيقية.

---

## التنصيب

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

## فتح المتصفح

```powershell
.\start-cdp-browser.cmd
```

أو مباشرة:

```powershell
.venv\Scripts\python.exe launch_browser.py --port 9333
```

بيفتح كروميوم بتاع Playwright على البروفايل الثابت، ويفتح منفذ التحكم على
`http://127.0.0.1:9333`. لو المتصفح شغال أصلًا، مش بيفتح واحد تاني.

تتأكد إنه شغال:

```powershell
curl http://127.0.0.1:9333/json/version
```

أول مرة: سجّل دخول فيسبوك في النافذة اللي فتحت.

---

## الإعداد (Google Sheet) — API v2

الكود في `apps_script/src/` (المستقبل + الترحيل والداشبورد)، وبيتنشر بـ clasp من `tools/plan_exec/` (شوف
`ENHANCEMENT_PLAN.md`). **مفيش رابط ولا مفتاح في الكود**: كل الكتابة محتاجة مفاتيح خاصة، والهاش بتاعها بس
بيتنشر في `zz_config.gs` المتولّد (برّه git).

الإعداد المحلي (برّه git) في `.secrets/`:

| الملف | فيه |
| --- | --- |
| `config.json` | `webapp` (رابط `/exec`)، `sheet_id`، `script_id` |
| `ingest.key` | مفتاح الكتابة بتاع السحب |
| `admin.key` | مفتاح الأدمن بتاع الردود والترحيل |

أو من البيئة: `FB_SHEET_WEBAPP`، `FB_SHEET_INGEST_KEY`، `FB_SHEET_ADMIN_KEY`، `FB_SHEET_ID`.

> clasp محتاج «Google Apps Script API» شغال من https://script.google.com/home/usersettings بحساب صاحب الشيت —
> القراءة بتشتغل من غيره، بس الـ push لأ.

---

## السحب إلى الشيت

```powershell
.venv\Scripts\python.exe fb_groups_to_sheet.py `
  --group https://www.facebook.com/groups/0000000000 `
  --min-posts 1 --max-posts 5 `
  --min-comments 1 --max-comments 30
```

| المفتاح | المعنى |
| --- | --- |
| `--group` | رابط جروب. كرّره لأكتر من جروب. من غيره: الجروبات من `groups.txt` |
| `--min-posts` / `--max-posts` | حدود عدد المنشورات |
| `--min-comments` / `--max-comments` | حدود عدد التعليقات للمنشور. `--max-comments all` = كل التعليقات |
| `--workers` | كام جروب في نفس الوقت (1–6)، كل واحد في نافذة لوحده. **6 بالكتير** (خطر حظر الحساب) — والحد ده للتشغيل كله: ماتشغّلش تشغيلين في نفس الوقت |
| `--dry` | يحفظ JSON في `dumps/` بس، من غير إرسال للشيت |
| `--batch-size` | أقصى عدد تعليقات في طلب واحد للشيت (افتراضي 200) |
| `--cdp` | عنوان CDP (افتراضي `http://127.0.0.1:9333`) |

تشغيل كبير (كل الجروبات، آخر 20 منشور، كل التعليقات، 6 نوافذ):

```powershell
.venv\Scripts\python.exe fb_groups_to_sheet.py --min-posts 1 --max-posts 20 --min-comments 0 --max-comments all --workers 6
```

الإرسال للشيت بيحصل في الخلفية (السحب مابيستناش Apps Script)، وأي فشل في الإرسال بيوقف الجروب.
مع `--workers`، الجروب اللي يقف بيقف لوحده والباقي يكمل، والتشغيل كله بيخلص بـ exit 1 وبيقول مين وقف وليه.

السحب **قراءة فقط**: مفيش رد ولا لايك ولا أي كتابة. فرز المنشورات زمني،
وفرز التعليقات «الأحدث» — ولو الفرز مش اتظبط، السكربت بيوقف بدل ما يسحب ترتيب غلط.

## الرد من الشيت

اكتب الرد في عمود `reply` في تاب `Comments` (الحالة بتبقى `pending`)، وبعدين:

```powershell
.venv\Scripts\python.exe fb_return_replies.py --max-replies 5      # فاصل عشوائي 20–60 ث بين الردود
.venv\Scripts\python.exe fb_return_replies.py --dry-run            # يتأكد من الأهداف من غير ضغط «رد» ولا كتابة
```

كل رد: `claim` ← تأكد إنه مش منشور قبل كده ← كتابة ← `prepare_submit` (آخر تصريح من الشيت) ← Enter ← تأكيد صارم
(من حسابنا، تحت التعليق نفسه، نفس النص) ← الحالة. الحالات: `pending`، `sending`، `sent`، `uncertain` (اتبعت
ومااتأكدش — مابيتبعتش تاني من غير مطابقة)، `replied`، `declined` (منع تواصل دايم مع الشخص)، `failed`، `held`
(مسودات قديمة مش مصرّح بيها — مابتتبعتش). الرد على رد والرد على منشور مقفولين.

---

## التشخيص لما حاجة تبوظ

فيسبوك بيغيّر الـ DOM من غير سابق إنذار، فأغلب الأعطال سببها selector بار.
`browser_debug.py` بيجمع كل حاجة محتاجها في خطوة واحدة.

دَمب تلقائي عند أي فشل أثناء الشغل:

```powershell
.venv\Scripts\python.exe fb_groups_to_sheet.py --group URL ... --debug
.venv\Scripts\python.exe fb_return_replies.py --debug
```

**أول أمر تشغّله لما السحب يبوظ** — بيختبر كل selector المشروع معتمد عليه على صفحة
حقيقية ويقول كل واحد لقى كام عنصر:

```powershell
.venv\Scripts\python.exe browser_debug.py selectors --url "https://www.facebook.com/groups/123/posts/456/"
```

كل selector مصنّف:

| التصنيف | معنى الصفر |
| --- | --- |
| `critical` | السحب أو الرد واقف. لازم إصلاح |
| `degraded` | ماشي بس ناقص — في fallback |
| `context` | طبيعي حسب الصفحة أو حالتها. مش مشكلة |

الأمر بيرجع exit code 1 لو حاجة `critical` بارت، فينفع في سكربت أو CI.
وبيعرف نوع الصفحة من الرابط، فالـ probes اللي مش بتاعتها بتتعلّم «مش بتاع الصفحة دي».

لقطة يدوية، أو مراقبة لايف:

```powershell
.venv\Scripts\python.exe browser_debug.py snapshot
.venv\Scripts\python.exe browser_debug.py snapshot --url "https://www.facebook.com/groups/123"
.venv\Scripts\python.exe browser_debug.py watch
```

تتبّع كامل وشبكة كاملة — ينفع مع أي أمر فوق أو مع `--debug` في السكربتات:

```powershell
.venv\Scripts\python.exe browser_debug.py --trace --har snapshot --url URL
.venv\Scripts\python.exe fb_groups_to_sheet.py --group URL ... --debug --trace

# افتح التتبّع: فيه كل خطوة بصورتها و DOM بتاعها
.venv\Scripts\python.exe -m playwright show-trace "debug\...\trace.zip"
```

كل لقطة بتروح في `debug/<الوقت>-<اسم>-<سبب>/` وفيها:

| الملف | فيه إيه |
| --- | --- |
| `summary.txt` | ابدأ منه: الرابط، العدّادات، آخر الأخطاء |
| `page.html` | شكل الصفحة الكامل وقت الفشل — ده اللي يوريك الـ selector البار |
| `page.png` | صورة الصفحة كلها |
| `page.txt` | النص الظاهر، أسرع في القراءة |
| `console.jsonl` | كل رسائل الكونسول بمكانها في الكود |
| `errors.jsonl` | الاستثناءات غير الملتقطة بـ stack trace |
| `network.jsonl` | الطلبات الفاشلة والردود 4xx/5xx |
| `browser-log.jsonl` | سجل المتصفح من CDP: CORS، CSP، أمان |
| `storage.json` | localStorage و sessionStorage |
| `selectors.json` / `.txt` | حالة كل selector المشروع معتمد عليه |
| `perf.json` | عدّادات الأداء من CDP (`Performance.getMetrics`) |
| `trace.zip` | تتبّع Playwright كامل — بـ `--trace` |
| `network.har` | كل الشبكة بصيغة HAR — بـ `--har` |
| `meta.json` | الرابط، العنوان، الـ viewport، عدد الكوكيز، الاستثناء |

الكوكيز بتتسجّل **بأسمائها ونطاقاتها بس، من غير قيم** — القيم بتساوي الجلسة نفسها.
و `debug/` مستبعد من git لأن فيه HTML وصور لصفحات مسجّل فيها دخول.

في الكود:

```python
from browser_debug import PageRecorder, recording

with recording(page, "post-123") as rec:   # دَمب تلقائي لو حصل استثناء
    page.goto(url)
    rec.mark("after-goto")
```

### Chrome DevTools MCP

`.mcp.json` فيه سيرفر [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp)
موصّل على نفس المتصفح (`--browser-url=http://127.0.0.1:9333`)، فأداة الذكاء الاصطناعي
تقدر تقرأ الكونسول والشبكة وتسجّل performance traces وتاخد صور من غير ما تكتب سكربت.

أول مرة لازم توافق عليه: شغّل `claude` في مجلد المشروع ووافق على السيرفر.
محتاج Node ≥ 23 (`node --version`).

> السيرفر بيوصل للمتصفح المسجّل دخوله على فيسبوك. وافق عليه وإنت عارف ده.

### ليه مفيش stealth plugins

المشروع بيستخدم كروم حقيقي، ببروفايل حقيقي ثابت، متحكَّم فيه بـ CDP — وده أصلًا
أقل حاجة بتبان. حِزَم الـ stealth بتعالج بصمة المتصفحات الـ headless، وإحنا
مش بنستخدم headless، فمفيش منها فايدة هنا.

---

## الملفات

| الملف | الوظيفة |
| --- | --- |
| `launch_browser.py` | يفتح كروميوم على البروفايل الثابت + CDP |
| `start-cdp-browser.cmd` | اختصار للتشغيل |
| `fb_groups_to_sheet.py` | السحب: جروب > منشورات > تعليقات > الشيت |
| `fb_return_replies.py` | الرد: الشيت > تعليقات فيسبوك |
| `browser_debug.py` | التشخيص: HTML، كونسول، أخطاء، شبكة، صور |
| `apps_script/src/` | Apps Script v2: المستقبل، الترحيل، الداشبورد (المنشور حاليًا) |
| `facebook_sheet_receiver.gs` | Apps Script v1 القديم — مفيش نشر بيستخدمه بعد 2026-10-24 (للتاريخ والاختبارات) |
| `tools/plan_exec/` | منفّذ الخطة: الحالة، clasp، النشر، التجربة |
| `cdp-ready.html` | صفحة البداية في النافذة المتحكَّم فيها |
| `CLAUDE.md` | دليل المشروع وسجل التحديثات |
| `dumps/` | مخرجات JSON محلية (مستبعدة من الريبو) |
| `debug/` | لقطات التشخيص (مستبعدة من الريبو) |
| `profile/` | بروفايل كروميوم الثابت (مستبعد من الريبو) |

## ملاحظة

الأداة بتشتغل على حساب فيسبوك إنت مسجّل بيه، وبتجمع بيانات مستخدمين (أسماء،
معرفات، نصوص تعليقات). استخدمها في جروبات إنت مسؤول عنها وبما يتوافق مع شروط فيسبوك
وقوانين حماية البيانات المنطبقة.


debug\run5-ab\NEXT_SESSION_PROMPT.md