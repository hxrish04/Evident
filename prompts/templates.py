"""
Central prompt templates for the Claude-backed stages.
"""


# Ask for a structured audit-trail answer, not a vague vibe check.
EVALUATE_CONTACT_PROMPT = """
You are evaluating whether a public academic or research contact is worth reaching out to.

User goal:
{user_goal}

Student profile:
{student_profile}

Verification status:
{verification_status}

Evidence sources:
{evidence_sources}

Supporting Evidence (retrieved from public sources):
{evidence_chunks}

Conflict note:
{conflict_note}

Evidence agreement:
{evidence_agreement}

Contact:
- Name: {name}
- Title: {title}
- Email: {email}
- Source URL: {url}
- Public research text:
{research_text}

Return only valid JSON with this exact schema:
{{
  "status": "<recommended | not_recommended | insufficient_evidence>",
  "relevance_score": <float from 0.0 to 10.0>,
  "recommended": <true or false>,
  "research_summary": "<1-2 sentences summarizing what they appear to work on>",
  "reason_trace": {{
    "match": "<one sentence: what specifically overlaps between their research and the user's goal>",
    "gap": "<one sentence: what is missing, uncertain, or weak about this match>",
    "evidence": "<one sentence: what concrete sources support this evaluation - email found, profile page, recent paper, lab page, etc.>"
  }},
  "cited_evidence": [
    {{
      "index": 1,
      "quote": "<verbatim short phrase from the chunk, max 20 words>",
      "why_relevant": "<one clause explaining how this supports the match>"
    }}
  ]
}}

Scoring guidance:
- High score: direct topical overlap, evidence of active research, and realistic outreach value.
- Mid score: partial overlap or unclear evidence.
- Low score: weak alignment, little research detail, or contact is unlikely to be a fit.
- Be selective. Only return recommended=true if the research alignment is clear and specific, not just adjacent or plausible. When in doubt, return not_recommended with a specific reason.
- Recommendation requires both a clear fit and enough public support to justify outreach. Do not recommend a contact if the supporting evidence is thin.

The reason_trace must sound like an audit trail of the decision, not marketing copy.
Keep each field to one sentence.
The gap field should state what is weaker, missing, or uncertain about the candidate.
If the available evidence is too weak to make a confident recommendation - for example, fewer than 2 usable sources, research text under 80 chars, no verifiable profile, or no clear research alignment - return:
{{
  "status": "insufficient_evidence",
  "reason": "<specific sentence explaining what is missing>"
}}
Do NOT force a recommendation when evidence is inadequate.
A cautious non-decision is more valuable than a low-confidence guess.
If recommended is false, the reason_trace.gap field must explain the specific disqualifying reason - not just say the match is weak.
Examples of valid gap reasons:
- research area is adjacent but not directly related to user's goal
- no evidence of active lab or current students
- title suggests administrative role, not active research
- no researchable public profile found
Vague gaps like "limited information" are not acceptable. Be specific about what is missing or misaligned.
Note: conflicting signals were detected in public sources for this contact. Factor this uncertainty into your confidence and reasoning when relevant.
"""


# Push the model to explicitly confirm, upgrade, or downgrade a shaky first pass.
REEVAL_CONTACT_PROMPT = """
A prior evaluation of this contact returned uncertain results.
Your job is to look harder and either confirm, upgrade, or downgrade the initial assessment.

Initial evaluation:
- Status: {initial_status}
- Relevance score: {initial_score}
- Confidence: {initial_confidence}
- Reasoning: {initial_reasoning}
- Conflict note: {conflict_note}
- Evidence agreement: {evidence_agreement}

Additional context:
{additional_chunks}

Re-evaluate and return ONLY valid JSON:
{{
  "revised_status": "recommended" | "not_recommended" | "insufficient_evidence",
  "revised_score": <float 0-10>,
  "revision_reason": "<one sentence: what changed and why>",
  "confidence_changed": <true or false>
}}
"""


# Keep run insight honest, metric-backed, and short enough for the UI.
RUN_INSIGHT_PROMPT = """
You are summarizing the quality of an AI research outreach run.
Given these run metrics, write 2-3 sentences of system-level insight for the user. Be specific and honest about limitations.

Metrics:
{metrics_json}

Focus on:
- overall evidence quality (what % of contacts had strong evidence)
- what limited system confidence most (missing emails, weak sources, conflicts)
- whether the recommended contacts seem well-supported or borderline

Rules:
- Use the metric field `direct_emails_found` as the only source of truth for email discussion.
- Do not introduce separate phrases like "email discovery" versus "direct emails extracted".
- If `direct_emails_found` is 0, say exactly: "No direct emails were extracted from public sources."
- Keep the response to 3 sentences maximum.

Do NOT praise the system. Be analytical. Return plain text only, no JSON.
"""


# Generate a grounded outreach draft that sounds like a real student, not a polished sales sequence.
GENERATE_EMAIL_PROMPT = """
You are writing a personalized outreach email based on a prior evaluation.

User goal:
{user_goal}

Student profile:
{student_profile}

Evidence sources:
{evidence_sources}

Sender signature:
{sender_signature}

Selected contact:
- Name: {name}
- Title: {title}
- Research summary: {research_summary}
- Why they were selected: {reason_trace}

Return only valid JSON:
{{
  "subject": "<specific subject line>",
  "body": "<plain-text email body with \\n newlines>"
}}

Rules:
- Keep it concise, professional, and natural.
- Keep the body between 120 and 180 words when possible.
- Use 3 or 4 short paragraphs, not one long block.
- Mention something specific from the research summary or retrieved evidence.
- Write as the student in the student profile, not as a generic applicant.
- The first paragraph must explicitly reference a concrete research area, method, or question from the retrieved evidence.
- Keep the research reference to 1 or 2 sentences max.
- Make it sound like a real undergraduate writing a thoughtful email, not a polished assistant.
- Use natural wording with one concrete reason for reaching out, not a stack of generic compliments.
- Paragraph flow:
  1. short intro plus one specific research detail,
  2. why this lab feels worth reaching out to,
  3. what the student can realistically bring or learn from the opportunity,
  4. a clear, low-pressure ask.
- Keep the tone warm, grounded, and lightly conversational while still being respectful.
- Vary sentence length a little so it does not read like a template.
- Format it like a real plain-text email:
  greeting line by itself,
  blank line,
  short paragraph,
  blank line,
  short paragraph,
  blank line,
  short paragraph,
  blank line,
  sign-off,
  sender signature.
- Make the ask concrete but low-pressure.
- Avoid generic filler like "I hope this email finds you well."
- Avoid vague lines like "I'm interested in your work" unless they are immediately tied to a specific research focus.
- Do not sound overconfident. The student is early in their research experience and should sound curious, capable, and ready to learn.
- Do not use em dashes, en dashes, or dash-based aside clauses in the email. Use normal sentences or commas instead.
- Do not use semicolons.
- Do not overuse phrases like "closely aligned", "at your convenience", "I would be grateful", "contribute where helpful", or "I came across your profile".
- Avoid lines like "exactly the kind of research I want to do" or "I'm very passionate about".
- The email should feel like a student actually sat down and wrote it in plain language.
- Write in first person for the student ("I") and second person for the recipient ("you").
- Do not use third-person pronouns for the recipient or the student.
- Do not infer or mention gender from the name, photo, or role. Use the recipient's name, title, or "you" instead of pronouns unless the source evidence explicitly states pronouns and the user asked for them.
- End with the provided sender signature exactly.
"""


# Force the ranking explanation into one concrete sentence so the UI does not turn into an essay.
COMPARE_TOP_PROMPT = """
You are explaining an AI ranking decision to a user.

Contact A ranked #1 with score {score_a}. Contact B ranked #2 with score {score_b}.

Contact A: {contact_a}
Contact B: {contact_b}

Explain in exactly one sentence (max 20 words) why Contact A ranked higher than Contact B.
Be specific. Reference actual differences in research match, evidence quality, or confidence signals. Do not be vague.
"""
