#!/usr/bin/env python3
"""일일 저장소 점검 리포트 생성 스크립트.

Company OS(.company-os/)의 Architect/Backend/Frontend/Reviewer/QA Role이 매일
자기 담당 영역을 점검한다고 가정하고, 그 결과를 ai-office-simulator가 읽어들이는
스키마(reports/latest.json)로 만들어낸다.

설계 원칙 (중요):
- 이 스크립트는 어떤 코드도 수정/커밋/머지하지 않는다. `auto_merged`는 항상 빈
  배열이며, 발견 사항은 전부 `needs_review`로 모아 사람이 검토하게 한다.
- 외부 LLM 호출 없이 결정적(deterministic)으로 동작한다 (테스트 실행 결과,
  마이그레이션 diff, 의존성 감사, 정적 시크릿 스캔, GitHub API 등 사실 기반 점검).

사용법 (auto-trade 저장소 루트에서 실행):
    python scripts/generate_daily_report.py \
        --frontend-dir ../auto-trade-view \
        --output reports/latest.json

전제 조건:
- 백엔드 의존성(requirements.txt)이 이미 설치되어 있어야 한다.
- --frontend-dir을 지정하는 경우, 해당 디렉터리에서 `npm ci`가 이미 실행되어
  node_modules가 준비되어 있어야 한다 (이 스크립트는 설치를 수행하지 않는다).
- GitHub 이슈 자동 생성은 GitHub Actions 환경(GITHUB_ACTIONS=true)이고 `gh` CLI가
  인증되어 있을 때만 시도한다 (그 외에는 조용히 건너뛴다).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GITHUB_API = "https://api.github.com"
BACKEND_REPO = "as950118/auto-trade"
FRONTEND_REPO = "as950118/auto-trade-view"
STALE_PR_DAYS = 14

SEVERITIES = ("critical", "high", "medium", "low")

# 흔한 플레이스홀더는 시크릿 스캔에서 제외한다 (오탐 방지).
_SECRET_PLACEHOLDER_RE = re.compile(
    r"^(change-me|your-|xxxxx|<|\{\{|example|sample|test|dummy|placeholder)",
    re.IGNORECASE,
)
_SECRET_PATTERNS = [
    ("Telegram Bot Token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("AWS Access Key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "하드코딩된 자격증명 의심 값",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*['\"]([^'\"]{12,})['\"]"
        ),
    ),
]
_SECRET_SKIP_DIRS = {".venv", "venv", "migrations", "__pycache__", "node_modules", ".git"}


@dataclass
class Finding:
    title: str
    severity: str  # critical | high | medium | low
    team: str
    detail: str = ""
    url: str | None = None


@dataclass
class TeamResult:
    title: str
    summary_lines: list = field(default_factory=list)
    detail_lines: list = field(default_factory=list)
    findings: list = field(default_factory=list)

    def counts(self) -> dict:
        c = {s: 0 for s in SEVERITIES}
        for f in self.findings:
            c[f.severity] = c.get(f.severity, 0) + 1
        return c

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": " / ".join(self.summary_lines) or "이상 없음",
            "details": "\n".join(self.detail_lines) or "특이사항 없음",
            "counts": self.counts(),
        }


def run(cmd, cwd=None, env=None, timeout=900):
    """subprocess를 실행하고 (returncode, 합쳐진 stdout+stderr)을 반환한다. 예외를 던지지 않는다."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError as exc:
        return 127, f"command not found: {exc}"
    except subprocess.TimeoutExpired as exc:
        return 124, f"timeout after {timeout}s: {exc}"


def _scan_hardcoded_secrets(base_dir: Path) -> list:
    findings = []
    for path in base_dir.rglob("*.py"):
        if any(part in _SECRET_SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for label, pattern in _SECRET_PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue
                value = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(0)
                if _SECRET_PLACEHOLDER_RE.match(value):
                    continue
                rel = path.relative_to(base_dir)
                findings.append(
                    Finding(
                        title=f"{label} 하드코딩 의심: {rel}:{line_no}",
                        severity="critical",
                        team="backend",
                    )
                )
    return findings


def check_backend() -> TeamResult:
    team = TeamResult(title="백엔드 저장소 점검 (auto-trade)")
    # settings.py가 load_dotenv()를 호출하는데, dotenv는 "이미 존재하는" 환경변수는
    # 덮어쓰지 않는다. 그래서 os.environ에서 DB_HOST를 그냥 pop()하면 .env 파일에서
    # 다시 채워져 절대 건드리면 안 되는 운영 Supabase DB로 붙어버린다. 반드시 빈
    # 문자열로 "이미 존재하지만 falsy"한 상태를 만들어야 로컬 SQLite로 강제된다.
    env = dict(os.environ)
    env["DB_HOST"] = ""

    rc, out = run([sys.executable, "manage.py", "test"], cwd=REPO_ROOT, env=env)
    total_m = re.search(r"Ran (\d+) tests?", out)
    total = total_m.group(1) if total_m else "알수없음"
    if rc != 0:
        fail_m = re.search(r"FAILED \(([^)]*)\)", out)
        detail = fail_m.group(1) if fail_m else "상세 불명"
        team.findings.append(Finding(
            title=f"테스트 스위트 실패 ({detail})", severity="critical", team="backend", detail=out[-2000:],
        ))
        team.summary_lines.append(f"테스트 실패 ({detail})")
    else:
        team.summary_lines.append(f"테스트 {total}건 전부 통과")
    team.detail_lines.append(f"[test] {'통과' if rc == 0 else '실패'} (총 {total}건)")

    rc, out = run(
        [sys.executable, "manage.py", "makemigrations", "--check", "--dry-run"],
        cwd=REPO_ROOT, env=env,
    )
    if rc != 0:
        team.findings.append(Finding(
            title="적용되지 않은 모델 변경(마이그레이션 누락) 발견",
            severity="high", team="backend", detail=out[-1000:],
        ))
        team.summary_lines.append("미반영 마이그레이션 존재")
    team.detail_lines.append(f"[migrations] {'변경 없음' if rc == 0 else '변경 감지됨 - makemigrations 필요'}")

    rc, out = run([sys.executable, "-m", "pip", "list", "--outdated", "--format=json"], cwd=REPO_ROOT)
    outdated = []
    if rc == 0:
        try:
            outdated = json.loads(out)
        except json.JSONDecodeError:
            outdated = []
    if outdated:
        sev = "medium" if len(outdated) >= 5 else "low"
        names = ", ".join(p["name"] for p in outdated[:8])
        suffix = "..." if len(outdated) > 8 else ""
        team.findings.append(Finding(
            title=f"오래된 의존성 패키지 {len(outdated)}개 ({names}{suffix})", severity=sev, team="backend",
        ))
        team.summary_lines.append(f"오래된 패키지 {len(outdated)}개")
    else:
        team.summary_lines.append("의존성 최신")
    team.detail_lines.append(f"[dependencies] outdated={len(outdated)}")

    secret_findings = _scan_hardcoded_secrets(REPO_ROOT)
    team.findings.extend(secret_findings)
    if secret_findings:
        team.summary_lines.append(f"하드코딩 시크릿 의심 {len(secret_findings)}건")
    team.detail_lines.append(f"[secret-scan] {len(secret_findings)}건 발견")

    todo_count = 0
    trading_dir = REPO_ROOT / "trading"
    if trading_dir.exists():
        for path in trading_dir.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            todo_count += len(re.findall(r"#\s*(TODO|FIXME)", text))
    if todo_count:
        team.findings.append(Finding(title=f"TODO/FIXME 주석 {todo_count}건", severity="low", team="backend"))
    team.detail_lines.append(f"[todo] {todo_count}건")

    return team


def check_frontend(frontend_dir: Path | None) -> TeamResult:
    team = TeamResult(title="프론트엔드 저장소 점검 (auto-trade-view)")
    if not frontend_dir or not frontend_dir.exists():
        team.summary_lines.append("프론트엔드 디렉터리 미지정 - 점검 생략")
        team.detail_lines.append("--frontend-dir 인자로 auto-trade-view 체크아웃 경로를 지정하세요.")
        return team

    rc, out = run(["npm", "run", "build"], cwd=frontend_dir, timeout=600)
    if rc != 0:
        team.findings.append(Finding(
            title="프론트엔드 빌드 실패", severity="critical", team="frontend", detail=out[-2000:],
        ))
        team.summary_lines.append("빌드 실패")
    else:
        team.summary_lines.append("빌드 성공")
    team.detail_lines.append(f"[build] {'성공' if rc == 0 else '실패'}")

    rc, out = run(["npm", "audit", "--json"], cwd=frontend_dir, timeout=300)
    try:
        audit = json.loads(out)
    except json.JSONDecodeError:
        audit = {}
    vulns = (audit.get("metadata") or {}).get("vulnerabilities") or {}
    sev_map = {"critical": "critical", "high": "high", "moderate": "medium", "low": "low"}
    for npm_sev, our_sev in sev_map.items():
        n = vulns.get(npm_sev, 0)
        if n:
            team.findings.append(Finding(
                title=f"npm audit: {npm_sev} 등급 취약점 {n}건", severity=our_sev, team="frontend",
            ))
    total_vulns = sum(vulns.get(k, 0) for k in sev_map)
    team.summary_lines.append(f"npm audit 취약점 {total_vulns}건" if total_vulns else "npm audit 이상 없음")
    team.detail_lines.append(f"[audit] {json.dumps(vulns, ensure_ascii=False) if vulns else '데이터 없음'}")

    return team


def _gh_api_get(path: str, token: str | None):
    req = urllib.request.Request(f"{GITHUB_API}{path}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def check_repo_health(token: str | None) -> TeamResult:
    team = TeamResult(title="저장소 전반 점검 (이슈/PR 현황)")
    now = datetime.now(timezone.utc)

    for repo in (BACKEND_REPO, FRONTEND_REPO):
        prs = _gh_api_get(f"/repos/{repo}/pulls?state=open&per_page=50", token)
        prs = prs if isinstance(prs, list) else []
        for pr in prs:
            try:
                created = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            age_days = (now - created).days
            if age_days >= STALE_PR_DAYS:
                team.findings.append(Finding(
                    title=f"[{repo}] 장기 미검토 PR: {pr.get('title', '(제목 없음)')} ({age_days}일 경과)",
                    severity="high" if age_days >= 30 else "medium",
                    team="analysis",
                    url=pr.get("html_url"),
                ))

        issues = _gh_api_get(f"/repos/{repo}/issues?state=open&per_page=50", token)
        issues = issues if isinstance(issues, list) else []
        open_issue_count = len([i for i in issues if "pull_request" not in i])
        team.detail_lines.append(f"[{repo}] open PR={len(prs)}, open issue={open_issue_count}")

    if any(f.team == "analysis" for f in team.findings):
        stale_count = len(team.findings)
        team.summary_lines.append(f"장기 미검토 PR {stale_count}건")
    else:
        team.summary_lines.append("장기 미검토 PR 없음")

    return team


_TEAM_LABEL = {"backend": "개발", "frontend": "디자인", "analysis": "분석"}


def build_report(frontend_dir: Path | None, token: str | None) -> dict:
    backend = check_backend()
    frontend = check_frontend(frontend_dir)
    analysis = check_repo_health(token)

    all_findings = backend.findings + frontend.findings + analysis.findings
    needs_review = [
        {
            "title": f.title,
            "severity": f.severity,
            "team": _TEAM_LABEL.get(f.team, f.team),
            **({"pr_url": f.url} if f.url else {}),
        }
        for f in all_findings
        if f.severity in ("critical", "high", "medium")
    ]

    return {
        "date": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d"),
        "project_name": "AutoTrade Platform 일일 자동 점검",
        "teams": {
            "개발": backend.to_dict(),
            "디자인": frontend.to_dict(),
            "분석": analysis.to_dict(),
        },
        # v1: 이 스크립트는 어떤 것도 자동 머지/수정하지 않는다. 항상 빈 배열.
        "auto_merged": [],
        "needs_review": needs_review,
    }


def maybe_create_issue(report: dict) -> str | None:
    """CI 환경에서 gh CLI가 인증되어 있고 검토할 항목이 있을 때만 이슈를 만든다."""
    if not report["needs_review"]:
        return None
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        return None

    title = f"\U0001F4CA 일일 점검 리포트 - {report['date']}"

    rc, out = run(["gh", "issue", "list", "--repo", BACKEND_REPO, "--search", title, "--json", "url"])
    try:
        existing = json.loads(out) if rc == 0 else []
    except json.JSONDecodeError:
        existing = []
    if existing:
        return existing[0].get("url")

    body_lines = [
        "매일 자동 점검에서 검토가 필요한 항목입니다. (자동 머지 없음 — 모두 사람 검토 대상)",
        "",
    ]
    for item in report["needs_review"]:
        line = f"- [{item['severity'].upper()}] ({item['team']}) {item['title']}"
        if item.get("pr_url"):
            line += f" — {item['pr_url']}"
        body_lines.append(line)
    body = "\n".join(body_lines)

    rc, out = run(["gh", "issue", "create", "--repo", BACKEND_REPO, "--title", title, "--body", body])
    if rc != 0:
        print(f"[warn] failed to create issue: {out}", file=sys.stderr)
        return None
    lines = [line for line in out.strip().splitlines() if line.strip()]
    return lines[-1] if lines else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frontend-dir", type=Path, default=None, help="auto-trade-view 체크아웃 경로")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "reports" / "latest.json")
    parser.add_argument("--no-issue", action="store_true", help="검토 필요 항목이 있어도 GitHub 이슈를 생성하지 않는다")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    report = build_report(args.frontend_dir, token)

    if not args.no_issue:
        issue_url = maybe_create_issue(report)
        if issue_url:
            report["issue_url"] = issue_url

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    history_path = args.output.parent / "history" / f"{report['date']}.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"리포트 생성 완료: {args.output}")
    print(json.dumps(
        {"date": report["date"], "needs_review_count": len(report["needs_review"])},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
