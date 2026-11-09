"""Report models and PR body drafting for the pr-ready-checker Hermes plugin.

This module defines the data structures used to capture the outcome of
PR-readiness checks, render human-readable summaries, serialize the
results to JSON, and draft pull-request bodies (either from a repository
template or from scratch). It is standard-library only and has no side
effects on import.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    """Outcome severity for a single check."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    """Result of a single PR-readiness check."""

    id: str
    title: str = ""
    severity: Severity = Severity.SKIP
    detail: str = ""
    fix: str = ""
    items: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # A human title is nice; when a caller only gives the stable id,
        # derive one ("clean-tree" -> "Clean Tree") instead of failing.
        if not self.title:
            self.title = self.id.replace("-", " ").replace("_", " ").title()

    def to_dict(self) -> dict:
        """Serialize this result to a plain dict."""
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity.value,
            "detail": self.detail,
            "fix": self.fix,
            "items": list(self.items),
        }

    @classmethod
    def from_parts(
        cls,
        id: str,
        title: str,
        severity: Severity,
        detail: str = "",
        fix: str = "",
        items: list[str] | None = None,
    ) -> CheckResult:
        """Build a CheckResult from positional/keyword parts."""
        return cls(
            id=id,
            title=title,
            severity=severity,
            detail=detail,
            fix=fix,
            items=list(items) if items is not None else [],
        )


@dataclass
class GateReport:
    """Aggregated report for a set of PR-readiness checks."""

    results: list[CheckResult]
    repo: str = ""
    branch: str = ""
    base: str = ""
    strict: bool = False

    @property
    def failed(self) -> list[CheckResult]:
        """All results with FAIL severity, in insertion order."""
        return [r for r in self.results if r.severity is Severity.FAIL]

    @property
    def warned(self) -> list[CheckResult]:
        """All results with WARN severity, in insertion order."""
        return [r for r in self.results if r.severity is Severity.WARN]

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.severity is Severity.PASS)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    @property
    def warned_count(self) -> int:
        return len(self.warned)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.results if r.severity is Severity.SKIP)

    @property
    def ok(self) -> bool:
        """Whether the gate passes; strict mode also requires no warnings."""
        if self.failed_count > 0:
            return False
        if self.strict and self.warned_count > 0:
            return False
        return True

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_dict(self) -> dict:
        """Serialize the report to a plain dict."""
        return {
            "repo": self.repo,
            "branch": self.branch,
            "base": self.base,
            "strict": self.strict,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "counts": {
                "passed": self.passed_count,
                "failed": self.failed_count,
                "warned": self.warned_count,
                "skipped": self.skipped_count,
            },
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self) -> str:
        """Serialize the report to a pretty-printed JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def render_text(self) -> str:
        """Render a deterministic, human-readable report."""
        symbols: dict[Severity, str] = {
            Severity.PASS: "[pass]",
            Severity.WARN: "[warn]",
            Severity.FAIL: "[FAIL]",
            Severity.SKIP: "[skip]",
        }
        lines: list[str] = []
        for result in self.results:
            lines.append(f"{symbols[result.severity]} {result.title}")
            if result.detail:
                lines.append(result.detail)
            for item in result.items[:10]:
                lines.append(f"  - {item}")
            if result.fix:
                lines.append(f"Fix: {result.fix}")
        lines.append("")
        status = "PASS" if self.ok else "FAIL"
        lines.append(
            f"PR-READY: {status} "
            f"({self.failed_count} failed, {self.warned_count} warnings, "
            f"{self.skipped_count} skipped)"
        )
        if not self.ok:
            failing_ids = [r.id for r in self.failed] or [r.id for r in self.warned]
            if failing_ids:
                lines.append("Failing checks: " + ", ".join(failing_ids))
        return "\n".join(lines) + "\n"


def _summary_lines(report: GateReport) -> list[str]:
    """Build the compact gate summary used in generated PR bodies."""
    markers: dict[Severity, str] = {
        Severity.PASS: "[pass]",
        Severity.WARN: "[warn]",
        Severity.FAIL: "[FAIL]",
        Severity.SKIP: "[skip]",
    }
    lines: list[str] = []
    for result in report.results:
        lines.append(f"- {markers[result.severity]} {result.title}")
    status = "PASS" if report.ok else "FAIL"
    lines.append(
        f"**PR-READY: {status}** "
        f"({report.failed_count} failed, {report.warned_count} warnings, "
        f"{report.skipped_count} skipped)"
    )
    return lines


# Template headings whose empty body the draft may fill in. The match is
# on the heading text after "##", lower-cased and stripped.
_SUMMARY_HEADINGS = {"what does this pr do", "summary", "description"}
_ISSUE_HEADINGS = {"related issue", "related issues", "issue", "closes"}


def _section_is_blank(body: str) -> bool:
    """True when a template section has no real content (only whitespace
    or HTML comments). Such sections are safe to fill; anything else is
    the author's own text and is left alone."""
    stripped = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
    return not stripped


def _fill_template_sections(
    template_text: str,
    commit_subjects: list[str],
    issue_ref: str,
) -> str:
    """Fill the empty summary / issue sections of a PR template.

    Only sections whose body is blank get filled: the summary section
    gets one bullet per commit subject, the issue section gets
    ``Fixes #<n>``. Every other section is returned byte-for-byte.
    """
    if not commit_subjects and not issue_ref:
        return template_text
    headers = list(re.finditer(r"^## .+$", template_text, flags=re.M))
    if not headers:
        return template_text
    out: list[str] = [template_text[: headers[0].start()]]
    for i, header in enumerate(headers):
        body_start = header.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(template_text)
        # "What does this PR do?" keeps its trailing "?" in the heading.
        heading = header.group(0)[2:].strip().lower().rstrip("?.")
        body = template_text[body_start:body_end]
        if _section_is_blank(body):
            if heading in _SUMMARY_HEADINGS and commit_subjects:
                body = "\n\n" + "\n".join(f"- {subject}" for subject in commit_subjects) + "\n\n"
            elif heading in _ISSUE_HEADINGS and issue_ref:
                body = f"\n\nFixes #{issue_ref}\n\n"
        out.append(header.group(0))
        out.append(body)
    return "".join(out)


def build_pr_body(
    template_text: str | None,
    report: GateReport,
    issue_ref: str = "",
    commit_subjects: list[str] | None = None,
) -> str:
    """Draft a pull-request body for the given gate report.

    When ``template_text`` is provided it is used as the skeleton: empty
    summary / issue sections are filled from the commit subjects and the
    inferred issue reference, and the gate summary is appended at the end
    under a ``<!-- pr-ready-checker -->`` marker. Otherwise a body is
    built from scratch describing the change, the related issue, how to
    test it, and a checklist reflecting actual check outcomes.
    """
    summary_lines = _summary_lines(report)
    subjects = commit_subjects or []

    if template_text is not None:
        body = _fill_template_sections(template_text, subjects, issue_ref)
        body = body.rstrip("\n")
        body += "\n\n<!-- pr-ready-checker -->\n"
        body += "\n".join(summary_lines)
        body += "\n"
        return body

    lines: list[str] = []
    lines.append("## What does this PR do?")
    lines.append("")
    if subjects:
        lines.extend(f"- {subject}" for subject in subjects)
    else:
        lines.append("<!-- Describe the change this PR makes and why it is needed. -->")
    lines.append("")
    lines.append("## Related Issue")
    lines.append("")
    if issue_ref:
        lines.append(f"Fixes #{issue_ref}")
    else:
        lines.append("Fixes #")
    lines.append("")
    lines.append("## How to Test")
    lines.append("")
    passed_results = [r for r in report.results if r.severity is Severity.PASS]
    if passed_results:
        for result in passed_results:
            lines.append(f"- {result.title} passed")
    else:
        lines.append("- (No automated checks passed; see the gate summary below.)")
    lines.append("")
    lines.append("## Checklist")
    lines.append("")
    for result in report.results:
        if result.severity is Severity.PASS:
            lines.append(f"- [x] {result.id}: {result.title}")
        else:
            lines.append(f"- [ ] {result.id}: {result.title}")
    lines.append("")
    lines.append("<!-- pr-ready-checker -->")
    lines.extend(summary_lines)
    lines.append("")
    return "\n".join(lines)
