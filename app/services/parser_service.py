import re

SUBJECT_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
STEP_RE = re.compile(r"(?im)^\s*Step\s+(\d+)\s*$")
EMAIL_STEP_RE = re.compile(r"(?im)^\s*Email\s+(\d+)\s*$")
VARIANT_RE = re.compile(r"(?im)^\s*V(\d+)\s*$")
SPINTAX_RE = re.compile(r"(?im)^\s*(?:-+\s*)?Spintax(?:\s+Version)?(?:\s*-+)?\s*:?\s*$")
SUBJECT_HEADING_RE = re.compile(r"(?im)^\s*Subject\s+Line(?:\s+Options?)?\s*:?\s*$")


def extract_subjects(text: str) -> list[str]:
    return [match.group(2).strip() for match in SUBJECT_RE.finditer(text)]


def _copy_after_last_spintax_marker(text: str) -> str:
    matches = list(SPINTAX_RE.finditer(text))
    if not matches:
        return text.strip()
    return text[matches[-1].end() :].strip()


def _split_variants(step_text: str) -> list[dict]:
    matches = list(VARIANT_RE.finditer(step_text))
    if not matches:
        body = _copy_after_last_spintax_marker(step_text)
        return [{"variant_label": "A", "body": body}] if body else []

    variants: list[dict] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(step_text)
        body = _copy_after_last_spintax_marker(step_text[start:end])
        if body:
            variants.append({"variant_label": chr(ord("A") + idx), "body": body})
    return variants


def parse_messaging_file(text: str, selected_sequence_name: str | None = None) -> dict:
    repository_campaigns, parse_warnings = _parse_repository_campaigns(text)
    if repository_campaigns:
        selected, select_warnings = _select_campaign(repository_campaigns, selected_sequence_name)
        return {
            "source_format": "repository",
            "selected_campaign": selected["name"],
            "subjects": selected["subjects"],
            "steps": selected["steps"],
            "campaigns": repository_campaigns,
            "warnings": parse_warnings + select_warnings,
        }

    step_matches = list(STEP_RE.finditer(text))
    steps: list[dict] = []
    for idx, match in enumerate(step_matches):
        start = match.end()
        end = step_matches[idx + 1].start() if idx + 1 < len(step_matches) else len(text)
        steps.append(
            {
                "step_number": int(match.group(1)),
                "body_variants": _split_variants(text[start:end]),
            }
        )
    return {"source_format": "step_sections", "subjects": extract_subjects(text), "steps": steps, "campaigns": [], "warnings": []}


def _select_campaign(campaigns: list[dict], selected_sequence_name: str | None) -> tuple[dict, list[str]]:
    if selected_sequence_name:
        normalized = selected_sequence_name.strip().lower()
        for campaign in campaigns:
            if campaign["name"].lower() == normalized:
                return campaign, []
        first = campaigns[0]
        return first, [
            f"Requested sequence '{selected_sequence_name.strip()}' was not found; using first parsed sequence '{first['name']}'."
        ]
    return campaigns[0], []


def _parse_repository_campaigns(text: str) -> tuple[list[dict], list[str]]:
    subject_headings = list(SUBJECT_HEADING_RE.finditer(text))
    campaigns: list[dict] = []
    all_warnings: list[str] = []
    for idx, heading in enumerate(subject_headings):
        section_start = heading.end()
        if idx + 1 < len(subject_headings):
            section_end = _heading_start_before(text, subject_headings[idx + 1].start()) or subject_headings[idx + 1].start()
        else:
            section_end = len(text)
        section = text[section_start:section_end]
        email_matches = list(EMAIL_STEP_RE.finditer(section))
        if not email_matches:
            continue

        first_email_start = email_matches[0].start()
        subjects = extract_subjects(section[:first_email_start])
        steps, warnings = _parse_email_steps(section, email_matches)
        all_warnings.extend(warnings)
        if subjects and steps:
            campaigns.append(
                {
                    "name": _heading_name_before(text, heading.start()) or f"Sequence {idx + 1}",
                    "subjects": subjects,
                    "steps": steps,
                }
            )
    return campaigns, all_warnings


def _parse_email_steps(section: str, email_matches: list[re.Match]) -> tuple[list[dict], list[str]]:
    steps: list[dict] = []
    warnings: list[str] = []
    for idx, match in enumerate(email_matches):
        start = match.end()
        end = email_matches[idx + 1].start() if idx + 1 < len(email_matches) else len(section)
        step_number = int(match.group(1))
        variants = _split_variants(section[start:end])
        if variants:
            steps.append({"step_number": step_number, "body_variants": variants})
        else:
            steps.append({"step_number": step_number, "body_variants": []})
            warnings.append(
                f"Email {step_number} produced no variants — check that the Spintax marker "
                f"has body text under it."
            )
    return steps, warnings


def _heading_name_before(text: str, position: int) -> str | None:
    before_lines = text[:position].splitlines()
    for line in reversed(before_lines):
        candidate = line.strip()
        if candidate:
            return candidate
    return None


def _heading_start_before(text: str, position: int) -> int | None:
    line_matches = list(re.finditer(r"(?m)^.*\S.*$", text[:position]))
    if not line_matches:
        return None
    return line_matches[-1].start()
