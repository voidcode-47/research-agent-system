"""项目卫生守卫：防止密钥、隐私信息、绝对路径等敏感内容进入可公开文件。

覆盖检查项：
- 密码 / Token / API Key
- 个人隐私（手机号、非占位邮箱）
- 本地用户目录绝对路径
- 私钥块
- .env.example 必须只含占位值
- .gitignore 必须覆盖敏感路径
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 仅允许存放真实密钥的文件（本地使用，已在 .gitignore 中忽略）
SECRET_ALLOWED = {".env"}

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", "env"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".bin", ".sqlite3", ".db", ".docx"}

PATTERNS = {
    "OpenAI 风格 API Key": re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    "私钥块": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "Windows 用户目录绝对路径": re.compile(
        r"[A-Za-z]:[\\/]Users[\\/][^\\/\"'\s]+", re.IGNORECASE
    ),
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _iter_scannable_files():
    """产出除密钥文件、缓存目录和二进制文件外的所有可公开文件。"""
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel.name in SECRET_ALLOWED:
            continue
        if p.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield rel, p


def test_no_secrets_or_pii_in_public_files():
    findings = []
    for rel, p in _iter_scannable_files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                snippet = match.group()
                if len(snippet) > 16:
                    snippet = snippet[:16] + "..."
                findings.append(f"{rel}: 发现 {label} -> {snippet}")
        for match in EMAIL_RE.finditer(text):
            # noreply 占位邮箱不属于个人隐私
            if "noreply" not in match.group():
                findings.append(f"{rel}: 发现疑似个人邮箱 -> {match.group()}")
    assert not findings, "可公开文件中存在敏感内容：\n" + "\n".join(findings)


def test_env_example_contains_only_placeholders():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    bad = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().endswith(("KEY", "SECRET", "TOKEN")) and value.strip():
            bad.append(f"{key.strip()} 必须留空，当前为 {value.strip()[:12]}...")
    assert not bad, ".env.example 中存在真实凭据：\n" + "\n".join(bad)


def test_gitignore_covers_sensitive_paths():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    required = [".env", ".pytest_cache/", "data/reports/", "__pycache__/"]
    missing = [item for item in required if item not in gi]
    assert not missing, f".gitignore 缺少必要忽略项：{missing}"
