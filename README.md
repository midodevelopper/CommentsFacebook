# CommentsFacebook

أرشفة منشورات وتعليقات جروبات فيسبوك إلى Google Sheet، والرد على التعليقات من الشيت —
عبر متصفح كروميوم حقيقي متحكَّم فيه بـ **CDP** (Chrome DevTools Protocol) على **بروفايل ثابت**.

Archives Facebook group posts/comments into a Google Sheet, and posts replies back from
that sheet, by driving a real Chromium over CDP against one fixed browser profile.

---

## المتصفح: بروفايل واحد ثابت

المتصفح بيشتغل **دايمًا** على البروفايل:

```
D:\Hermes Browser\profile
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

## الإعداد (Google Sheet)

1. افتح Google Sheet جديد > Extensions > Apps Script.
2. الصق `facebook_sheet_receiver.gs` وحدّث `TOKEN` فيه بقيمة سرية من عندك.
3. شغّل `setup()` مرة واحدة لتكوين تابّات `Posts` و `Comments`.
4. Deploy > New deployment > Web app — Execute as: **Me**، Who has access: **Anyone**.
5. انسخ رابط الـ `/exec` وحطه في متغيرات البيئة:

```powershell
$env:FB_SHEET_WEBAPP = "https://script.google.com/macros/s/XXXX/exec"
$env:FB_SHEET_TOKEN  = "نفس التوكن اللي في الـ gs"
```

شوف `.env.example`.

> نشر الـ Web App بـ *Anyone* معناه إن الرابط + التوكن هما كل الحماية.
> ممنوع ترفعهم في ريبو عام.

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
| `--group` | رابط جروب. كرّره لأكتر من جروب |
| `--min-posts` / `--max-posts` | حدود عدد المنشورات |
| `--min-comments` / `--max-comments` | حدود عدد التعليقات للمنشور |
| `--dry` | يحفظ JSON في `dumps/` بس، من غير إرسال للشيت |
| `--batch-size` | حجم دفعة الإرسال (افتراضي 80) |
| `--cdp` | عنوان CDP (افتراضي `http://127.0.0.1:9333`) |

السحب **قراءة فقط**: مفيش رد ولا لايك ولا أي كتابة. فرز المنشورات زمني،
وفرز التعليقات «الأحدث» — ولو الفرز مش اتظبط، السكربت بيوقف بدل ما يسحب ترتيب غلط.

## الرد من الشيت

املأ عمود `reply` في تاب `Comments`، وبعدين:

```powershell
.venv\Scripts\python.exe fb_return_replies.py
```

بياخد الصفوف اللي فيها `reply` و `reply_status` فاضي، ينشر الرد، ويعلّم الصف
`posted` عن طريق `mark_replied`.

---

## الملفات

| الملف | الوظيفة |
| --- | --- |
| `launch_browser.py` | يفتح كروميوم على البروفايل الثابت + CDP |
| `start-cdp-browser.cmd` | اختصار للتشغيل |
| `fb_groups_to_sheet.py` | السحب: جروب > منشورات > تعليقات > الشيت |
| `fb_return_replies.py` | الرد: الشيت > تعليقات فيسبوك |
| `facebook_sheet_receiver.gs` | Apps Script اللي بيستقبل ويعمل upsert بدون تكرار |
| `cdp-ready.html` | صفحة البداية في النافذة المتحكَّم فيها |
| `dumps/` | مخرجات JSON محلية (مستبعدة من الريبو) |
| `profile/` | بروفايل كروميوم الثابت (مستبعد من الريبو) |

## ملاحظة

الأداة بتشتغل على حساب فيسبوك إنت مسجّل بيه، وبتجمع بيانات مستخدمين (أسماء،
معرفات، نصوص تعليقات). استخدمها في جروبات إنت مسؤول عنها وبما يتوافق مع شروط فيسبوك
وقوانين حماية البيانات المنطبقة.
