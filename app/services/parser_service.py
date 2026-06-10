import re

SUBJECT_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
STEP_RE = re.compile(r"(?im)^\s*Step\s*#?\s*(\d+)\s*$")
EMAIL_STEP_RE = re.compile(r"(?im)^\s*Email\s*#?\s*(\d+)\b[^\n]*$")
VARIANT_RE = re.compile(r"(?im)^\s*(?:V\s*#?\s*\d+|Version\s*#?\s*(?:[A-Z]|\d+))\b[^\n]*:?\s*$")
SPINTAX_RE = re.compile(r"(?im)^\s*(?:-+\s*)?Spintax(?:\s+Version)?(?:\s*-+)?\s*:?\s*$")
SUBJECT_HEADING_RE = re.compile(r"(?im)^\s*Subject\s+Lines?(?:\s+Options?)?\s*:?\s*$")
NON_EMAIL_TAIL_RE = re.compile(
    r"(?im)^\s*(?:Unique combinations\s*:.*|LinkedIn\s*:.*|LI\s*:.*|Connection Request\b.*|DM\d*\b.*)$"
)
CHANNEL_TAIL_RE = re.compile(r"(?im)^\s*(?:LinkedIn\s*:.*|LI\s*:.*|Connection Request\b.*|DM\d*\b.*)$")
STEP_CHANNEL_RE = re.compile(
    r"(?im)^\s*Step\s*#?\s*(\d+)\s*[—:\-–]\s*(Email|LinkedIn|LI|DM|Connection\s*Request)\b[^\n]*$"
)
MESSAGE_BODY_RE = re.compile(r"(?im)^\s*Message\s+Body\s*:?\s*$")
DAY_RE = re.compile(r"(?i)Day\s*[-:]?\s*(\d+)")
# Google Docs .txt exports leave comment anchors ("[a]") inline and dump the
# comment definitions at the end. Strip both so they never reach email copy.
COMMENT_DEFINITION_RE = re.compile(r"(?m)^[ \t]*\[[a-z0-9]{1,2}\][^\n]*$")
ASSIGNED_LINE_RE = re.compile(r"(?im)^[ \t]*_Assigned to[^\n]*$")
CC_LINE_RE = re.compile(r"(?im)^[ \t]*CC:\s*@[^\n]*$")
INLINE_COMMENT_MARKER_RE = re.compile(r"\[(?:[a-z]{1,2}|\d{1,3})\]")


def _strip_doc_comments(text: str) -> str:
    text = COMMENT_DEFINITION_RE.sub("", text)
    text = ASSIGNED_LINE_RE.sub("", text)
    text = CC_LINE_RE.sub("", text)
    return INLINE_COMMENT_MARKER_RE.sub("", text)


def extract_subjects(text: str) -> list[str]:
    return [match.group(2).strip() for match in SUBJECT_RE.finditer(text)]


def _copy_after_last_spintax_marker(text: str) -> str:
    matches = list(SPINTAX_RE.finditer(text))
    if not matches:
        return _strip_non_email_tail(text).strip()
    return _strip_non_email_tail(text[matches[-1].end() :]).strip()


def _strip_non_email_tail(text: str) -> str:
    match = NON_EMAIL_TAIL_RE.search(text)
    if not match:
        return text.rstrip()
    return text[: match.start()].rstrip()


def _strip_channel_tail(text: str) -> str:
    match = CHANNEL_TAIL_RE.search(text)
    if not match:
        return text.rstrip()
    return text[: match.start()].rstrip()


def _split_variants(step_text: str) -> list[dict]:
    step_text = _strip_channel_tail(step_text)
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
    text = _strip_doc_comments(text)
    step_channel = _parse_step_channel_format(text)
    if step_channel is not None:
        return step_channel

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
    if step_matches:
        source_format = "step_sections"
    else:
        step_matches = list(EMAIL_STEP_RE.finditer(text))
        source_format = "email_sections" if step_matches else "unparsed"

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
    subject_text = text[: step_matches[0].start()] if step_matches else text
    return {"source_format": source_format, "subjects": extract_subjects(subject_text), "steps": steps, "campaigns": [], "warnings": []}


def _parse_step_channel_format(text: str) -> dict | None:
    """Parse 'Step N — Email/LinkedIn (Day X)' campaigns (e.g. Google Docs exports).

    Steps are interleaved across channels. Only email steps go to Smartlead;
    each non-email step is dropped with a warning so nothing fails silently.
    Email steps are renumbered sequentially (1..N) for valid Smartlead sequences.
    """
    matches = list(STEP_CHANNEL_RE.finditer(text))
    if not matches:
        return None

    subjects = _extract_subjects_after_heading(text)
    steps: list[dict] = []
    warnings: list[str] = []
    email_number = 0
    for idx, match in enumerate(matches):
        channel_keyword = match.group(2)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end]
        if channel_keyword.lower().startswith("email"):
            email_number += 1
            variants = _split_variants(_strip_email_block_preamble(block))
            day_match = DAY_RE.search(match.group(0))
            day = int(day_match.group(1)) if day_match else None
            steps.append({"step_number": email_number, "day": day, "body_variants": variants})
            if not variants:
                warnings.append(f"Email step {email_number} produced no body text.")
        else:
            warnings.append(
                f"Skipped Step {match.group(1)} ({channel_keyword}) - not an email "
                f"channel; only email steps are synced to Smartlead."
            )

    name = _document_title(text)
    return {
        "source_format": "repository",
        "selected_campaign": name,
        "subjects": subjects,
        "steps": steps,
        "campaigns": [{"name": name, "subjects": subjects, "steps": steps}],
        "warnings": warnings,
    }


def _strip_email_block_preamble(block: str) -> str:
    """Drop the 'Message Body:' label and any leading subject-line block."""
    message_body = MESSAGE_BODY_RE.search(block)
    if message_body:
        return block[message_body.end() :]

    subject_heading = SUBJECT_HEADING_RE.search(block)
    if subject_heading:
        lines = block[subject_heading.end() :].splitlines(keepends=True)
        position = 0
        for line in lines:
            stripped = line.strip()
            if not stripped or re.match(r"^\d+\.\s+", stripped):
                position += 1
                continue
            break
        return "".join(lines[position:])
    return block


def _extract_subjects_after_heading(text: str) -> list[str]:
    heading = SUBJECT_HEADING_RE.search(text)
    if not heading:
        return []
    subjects, _ = _extract_subject_block(text[heading.end() :])
    return subjects


def _document_title(text: str) -> str | None:
    for line in text.splitlines():
        candidate = line.strip().lstrip("﻿").strip()
        if candidate and not candidate.startswith("*") and not _looks_like_metadata_line(candidate):
            return candidate
    return None


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
        subjects, body_start = _extract_subject_block(section)
        email_blocks = _repository_email_blocks(section[body_start:])
        if not email_blocks:
            continue

        steps, warnings = _parse_email_blocks(email_blocks)
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


def _extract_subject_block(section: str) -> tuple[list[str], int]:
    subjects: list[str] = []
    position = 0
    for line in section.splitlines(keepends=True):
        match = re.match(r"^\s*(\d+)\.\s+(.+?)\s*$", line)
        if match:
            subjects.append(match.group(2).strip())
            position += len(line)
            continue
        if not line.strip():
            position += len(line)
            continue
        if subjects:
            break
        position += len(line)
    return subjects, position


def _repository_email_blocks(section: str) -> list[tuple[int, str]]:
    matches = list(EMAIL_STEP_RE.finditer(section))
    blocks: list[tuple[int, str]] = []
    if matches and section[: matches[0].start()].strip():
        blocks.append((1, section[: matches[0].start()]))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(section)
        blocks.append((int(match.group(1)), section[start:end]))
    if not matches and section.strip():
        blocks.append((1, section))
    return blocks


def _parse_email_blocks(email_blocks: list[tuple[int, str]]) -> tuple[list[dict], list[str]]:
    steps: list[dict] = []
    warnings: list[str] = []
    for step_number, block in email_blocks:
        variants = _split_variants(block)
        if variants:
            steps.append({"step_number": step_number, "body_variants": variants})
        else:
            steps.append({"step_number": step_number, "body_variants": []})
            warnings.append(
                f"Email {step_number} produced no variants - check that the Spintax marker "
                f"has body text under it."
            )
    return steps, warnings


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
        if candidate and not _looks_like_metadata_line(candidate):
            return candidate
    return None


def _looks_like_metadata_line(line: str) -> bool:
    if re.match(r"^\d+\.\s+", line):
        return True
    if EMAIL_STEP_RE.match(line):
        return True
    if line.lower().startswith("audience:"):
        return True
    return False


def _heading_start_before(text: str, position: int) -> int | None:
    line_matches = list(re.finditer(r"(?m)^.*\S.*$", text[:position]))
    if not line_matches:
        return None
    return line_matches[-1].start()
