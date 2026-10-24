"""منفّذ خطة ENHANCEMENT_PLAN.md: الحالة، القفل، ترتيب المراحل، إعادة المحاولة، والتقرير.

اللي بينفّذ فعلًا هو Claude Code؛ الأداة دي بتمسك الحالة على الديسك عشان أي انقطاع (إعادة تشغيل الجهاز،
Ctrl+C، تايم آوت) يتكمّل من نفس النقطة، وبتمنع مرحلة تبدأ قبل ما اللي قبلها تنجح.
مفيش هنا أي قيمة سرية: المفاتيح والمعرفات في .secrets/ والحالة في dumps/plan-exec/ (الاتنين برّه git).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXEC_DIR = ROOT / "dumps" / "plan-exec"
SECRETS = ROOT / ".secrets"
