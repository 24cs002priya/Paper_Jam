"""Provider-independent extraction for narrowly recognized statutory clauses.

This is deliberately a small, auditable rule set rather than a general legal
parser. A rule is emitted only when its operative wording is present verbatim
in an active retrieved source chunk.
"""
import re

from app.schemas.approval_engine import ExtractedApprovalRule, RuleExtraction


_FSSAI_ACT = re.compile(r"food safety and standards act[, ]+2006", re.IGNORECASE)
_FSSAI_LICENCE_CLAUSE = re.compile(
    r"no\s+person\s+shall\s+commence\s+or\s+carry\s+on\s+any\s+food\s+business\s+except\s+under\s+a\s+licen[cs]e",
    re.IGNORECASE,
)
_FOOD_PROFILE = re.compile(r"food|beverage|dairy|bakery|restaurant|catering", re.IGNORECASE)
_AUTHORIZATION = re.compile(
    r"\b(licen[cs](?:e|ed|ing)|registration|registered|register|permit|approval|consent|clearance|no[- ]objection|NOC|authorization|authorisation|certificate)\b",
    re.IGNORECASE,
)
_REQUIRED_AUTHORIZATION = re.compile(
    r"(?:\b(?:shall|must|is required to|are required to)\s+(?:(?:first|validly)\s+)?"
    r"(?:obtain|secure|hold|possess|apply for|have|be granted|be issued|be registered|be licensed)"
    r"\b.{0,180}\b(?:licen[cs]e|registration|permit|approval|consent|clearance|NOC|"
    r"authori[sz]ation|certificate)\b"
    r"|\bno\s+person\s+shall\b.{0,180}\b(?:without|except under)\s+(?:the\s+)?(?:a\s+|an\s+)?"
    r"(?:(?:previous|prior|valid|written)\s+)?(?:licen[cs]e|registration|permit|approval|consent|clearance|NOC|"
    r"authori[sz]ation|certificate)\b"
    r"|\bshall\s+be\s+(?:registered|licensed|certified|permitted|approved)\b"
    r"|\b(?:licen[cs]e|registration|permit|approval|consent|clearance|NOC|"
    r"authori[sz]ation|certificate)\b.{0,100}\b(?:shall be|has to be|must be)\s+(?:obtained|taken|secured|sought)\b"
    r"|\bshall\s+make\s+an?\s+application\b.{0,180}\bfor\s+(?:(?:the|a|an)\s+)?"
    r"(?:licen[cs]e|registration|permit|approval|consent|clearance|NOC|authori[sz]ation|certificate)\b)",
    re.IGNORECASE,
)
_CONDITIONAL_WORDS = re.compile(r"\b(?:if|unless|where|when|subject to|except|depending on)\b", re.IGNORECASE)


def _food_act_rule(context: dict, chunks: list[dict]) -> list[ExtractedApprovalRule]:
    profile_text = " ".join(str(context.get(key, "")) for key in (
        "business_type", "industry", "business_activity", "business_activities",
    ))
    if not _FOOD_PROFILE.search(profile_text):
        return []

    rules = []
    for chunk in chunks:
        source_name = str(chunk.get("regulation_name") or chunk.get("file_name") or "")
        if not _FSSAI_ACT.search(source_name):
            continue
        text = str(chunk.get("text") or "")
        match = _FSSAI_LICENCE_CLAUSE.search(text)
        if not match:
            continue
        evidence = match.group(0)
        rules.append(ExtractedApprovalRule(
            # The downstream FSSAI postprocessor gives this a clear display
            # label after merging it with any separately grounded FSSAI rules.
            approval_name="licence",
            authority=chunk.get("authority"),
            category=None,
            # Section 31(2) has a registration exception for specified petty
            # operators. Without a rule-specific threshold determination, do
            # not call this user's licence category mandatory.
            rule_type="conditional",
            reason="Section 31(1) establishes a food-business authorization requirement; section 31(2) provides a registration exception for specified petty operators, so the applicable category must be determined from eligibility evidence.",
            conditions=[],
            required_documents=[],
            dependencies=[],
            confidence=0.98,
            source_chunk_ids=[str(chunk["_id"])],
            evidence=evidence,
        ))
    return rules


def _dynamic_authorization_rules(chunks: list[dict]) -> list[ExtractedApprovalRule]:
    """Extract high-precision authorization clauses from active RAG results.

    RAG determines which current regulations are examined. The extractor only
    accepts a short, verbatim clause with a recognized legal modal and an
    explicit authorization verb/prohibition; topics and checklists alone do
    not qualify.
    """
    rules = []
    seen: set[tuple[str, str]] = set()
    for chunk in sorted(chunks, key=lambda item: item.get("score", 0), reverse=True):
        regulation = str(chunk.get("regulation_name") or chunk.get("file_name") or "").strip()
        if not regulation or "draft" in regulation.casefold() or "food safety and standards" in regulation.casefold() or "fssai" in regulation.casefold():
            continue
        text = str(chunk.get("text") or "")
        # PDF extraction often wraps sentences across lines. Normalize spacing,
        # while retaining the exact normalized quote for grounding checks.
        normalized = " ".join(text.split())
        for sentence in re.split(r"(?<=[.!?])\s+", normalized):
            sentence = sentence.strip()
            if not sentence or len(sentence) > 1200:
                continue
            requirement = _REQUIRED_AUTHORIZATION.search(sentence)
            if not requirement:
                continue
            authorization = _AUTHORIZATION.search(sentence, requirement.start(), requirement.end())
            if not authorization:
                continue
            raw_term = authorization.group(0).casefold()
            term = "licence" if raw_term.startswith(("licenc", "licens")) else (
                "registration" if raw_term in {"registered", "register"} else raw_term
            )
            key = (regulation.casefold(), term)
            if key in seen:
                continue
            seen.add(key)
            display_name = f"{term.capitalize()} under {regulation}"
            if term == "approval":
                approved_subject = re.search(r"\bapproval\s+of\s+(?:the\s+)?([a-z][a-z -]{1,35}?)(?:\s+(?:has|shall|must|is)\b|[,.;:]|$)", sentence, re.IGNORECASE)
                if approved_subject:
                    display_name = f"{approved_subject.group(1).strip().capitalize()} approval under {regulation}"
            conditional = bool(
                _CONDITIONAL_WORDS.search(sentence)
                or re.search(r"\bOCA\s+letter\b|\bwithout\b|\bpollution control area\b|\beffluent\b|\bdischarge\b", sentence, re.IGNORECASE)
            )
            rules.append(ExtractedApprovalRule(
                approval_name=display_name[:300],
                authority=chunk.get("authority"),
                category=None,
                rule_type="conditional" if conditional else "mandatory",
                reason=f"The retrieved provision expressly states: {sentence[:1200]}",
                conditions=[],
                required_documents=[],
                dependencies=[],
                confidence=0.85,
                source_chunk_ids=[str(chunk["_id"])],
                evidence=sentence[:1200],
            ))
            if len(rules) >= 12:
                return rules
    return rules


def extract_known_statutory_rules(context: dict, chunks: list[dict]) -> RuleExtraction:
    """Extract only explicit authorizations found in current retrieved sources."""
    rules = _food_act_rule(context, chunks)
    rules.extend(_dynamic_authorization_rules(chunks))
    return RuleExtraction(rules=rules[:12])
