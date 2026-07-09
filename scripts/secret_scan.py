#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SENSITIVE_KEY_PATTERN = re.compile(r"(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|secret|password|credential)", re.IGNORECASE)
ENV_ASSIGNMENT_PATTERN = re.compile(
    r"""(?x)
    ^\s*
    (?P<key>[A-Z0-9_]+)
    \s*=\s*
    (?P<value>[^\s#]+)
    """
)
STRUCTURED_ASSIGNMENT_PATTERN = re.compile(
    r"""(?x)
    ["'](?P<key>[^"']+)["']
    \s*:\s*
    ["'](?P<value>[^"']{8,})["']
    """
)
PYTHON_LITERAL_ASSIGNMENT_PATTERN = re.compile(
    r"""(?x)
    ^\s*
    (?P<key>[A-Za-z_][A-Za-z0-9_]*)
    \s*=\s*
    ["'](?P<value>[^"']{8,})["']
    """
)
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openrouter_key", re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{20,}\b")),
    ("openai_style_key", re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b")),
    ("github_fine_grained_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE)),
)
PRIVATE_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"/private/var/"),
    re.compile(r"/var/folders/"),
    re.compile(r"[A-Za-z]:\\Users\\"),
)

SKIP_DIR_PARTS = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "outputs",
    "coverage",
}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
    ".zip",
    ".gz",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".pyc",
}
PLACEHOLDER_VALUES = {
    "changeme",
    "dummy",
    "dummy-key",
    "embedding-key",
    "example",
    "example-key",
    "fake",
    "fake-key",
    "local",
    "mock",
    "not-a-real-secret",
    "placeholder",
    "test",
    "test-key",
    "your-api-key",
    "your_api_key",
}


@dataclass(frozen=True)
class SecretIssue:
    path: str
    line: int
    rule_id: str
    message: str
    preview: str


def discover_candidate_files(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return [root / line for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.CalledProcessError):
        return [path for path in root.rglob("*") if path.is_file()]


def scan_repository(root: Path, paths: Iterable[Path] | None = None) -> list[SecretIssue]:
    candidates = list(paths) if paths is not None else discover_candidate_files(root)
    issues: list[SecretIssue] = []
    for path in candidates:
        resolved = path if path.is_absolute() else root / path
        if not _should_scan_path(root, resolved):
            continue
        issues.extend(scan_file(root, resolved))
    return issues


def scan_file(root: Path, path: Path) -> list[SecretIssue]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw[:4096]:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-8", errors="ignore")
        except UnicodeDecodeError:
            return []
    relative_path = _relative_path(root, path)
    return scan_text(relative_path, text)


def scan_text(relative_path: str, text: str) -> list[SecretIssue]:
    issues: list[SecretIssue] = []
    path = Path(relative_path)
    if _is_tracked_env_file(path):
        issues.append(
            SecretIssue(
                path=relative_path,
                line=1,
                rule_id="tracked_env_file",
                message="Local environment files must not be tracked; use .env.example for templates.",
                preview=path.name,
            )
        )
    for line_number, line in enumerate(text.splitlines(), start=1):
        issues.extend(_scan_line(relative_path, line_number, line))
    return [issue for issue in issues if not _is_allowed_issue(issue, text)]


def _scan_line(relative_path: str, line_number: int, line: str) -> list[SecretIssue]:
    issues: list[SecretIssue] = []
    for rule_id, pattern in SECRET_PATTERNS:
        match = pattern.search(line)
        if match:
            issues.append(
                SecretIssue(
                    path=relative_path,
                    line=line_number,
                    rule_id=rule_id,
                    message="High-confidence secret-looking value found.",
                    preview=_redact(match.group(0)),
                )
            )
    for pattern in PRIVATE_PATH_PATTERNS:
        match = pattern.search(line)
        if match:
            issues.append(
                SecretIssue(
                    path=relative_path,
                    line=line_number,
                    rule_id="private_path",
                    message="Private workstation path found in a trackable file.",
                    preview=_redact(match.group(0)),
                )
            )
    for assignment in _sensitive_assignments(line):
        key, value = assignment
        if _looks_like_real_assignment_value(value):
            issues.append(
                SecretIssue(
                    path=relative_path,
                    line=line_number,
                    rule_id="sensitive_assignment",
                    message=f"Sensitive field {key!r} appears to contain a concrete value.",
                    preview=f"{key}={_redact(value)}",
                )
            )
    return issues


def _sensitive_assignments(line: str) -> list[tuple[str, str]]:
    assignments: list[tuple[str, str]] = []
    env_match = ENV_ASSIGNMENT_PATTERN.search(line)
    if env_match:
        key = env_match.group("key")
        if _is_sensitive_key(key) and key == key.upper() and not key.endswith("_KEYS"):
            assignments.append((key, env_match.group("value").strip().strip("'\"")))
    for pattern in (STRUCTURED_ASSIGNMENT_PATTERN, PYTHON_LITERAL_ASSIGNMENT_PATTERN):
        match = pattern.search(line)
        if match and _is_sensitive_key(match.group("key")):
            assignments.append((match.group("key"), match.group("value").strip().strip("'\"")))
    return assignments


def _is_sensitive_key(key: str) -> bool:
    return bool(SENSITIVE_KEY_PATTERN.search(str(key)))


def _should_scan_path(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    parts = set(relative.parts)
    if parts & SKIP_DIR_PARTS:
        return False
    return path.suffix.lower() not in SKIP_SUFFIXES and path.is_file()


def _is_tracked_env_file(path: Path) -> bool:
    name = path.name
    if not name.startswith(".env"):
        return False
    return not name.endswith(".example")


def _looks_like_real_assignment_value(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    if not normalized:
        return False
    if normalized in PLACEHOLDER_VALUES:
        return False
    if re.fullmatch(r"[A-Z0-9_]+", value.strip()):
        return False
    if normalized.startswith(("your_", "your-", "${", "$", "<", "{", "[", "(", "process.env.", "os.environ", "merged_env.")):
        return False
    if "*" in normalized or normalized.endswith("_set") or normalized.endswith("_masked"):
        return False
    if normalized.endswith("-"):
        return False
    if normalized.endswith("-key") and len(normalized) < 24:
        return False
    return len(normalized) >= 12


def _is_allowed_issue(issue: SecretIssue, full_text: str) -> bool:
    _ = full_text
    path = issue.path
    if issue.rule_id == "private_path":
        if path == "backend/benchmarks/versioning.py":
            return True
        if path == "backend/tests/test_benchmark_versioning.py":
            return True
        if path == "backend/tests/test_secret_scan.py":
            return True
        if path == "scripts/secret_scan.py":
            return True
        if path == "docs/ADVANCED_AGENT_MIGRATION_ROADMAP.md":
            return True
    if issue.rule_id in {"openrouter_key", "sensitive_assignment"} and path in {
        "backend/tests/test_benchmark_versioning.py",
        "backend/tests/test_secret_scan.py",
    }:
        return True
    return False


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _redact(value: str) -> str:
    text = str(value)
    if len(text) <= 8:
        return "<redacted>"
    return f"{text[:4]}…{text[-4:]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan trackable repository files for secrets and private workstation paths.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("paths", nargs="*", type=Path, help="Optional paths to scan instead of git-discovered candidates.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    selected_paths = [path if path.is_absolute() else root / path for path in args.paths] if args.paths else None
    issues = scan_repository(root, selected_paths)
    if args.json:
        print(json.dumps({"ok": not issues, "issue_count": len(issues), "issues": [asdict(issue) for issue in issues]}, ensure_ascii=False, indent=2))
    elif issues:
        print(f"Secret scan failed: {len(issues)} issue(s) found.")
        for issue in issues:
            print(f"- {issue.path}:{issue.line} [{issue.rule_id}] {issue.message} ({issue.preview})")
    else:
        print("Secret scan passed: no high-confidence secrets or private workstation paths found in trackable files.")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
