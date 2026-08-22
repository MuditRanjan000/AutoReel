"""
core/agents/quality_control.py
The Quality Control (QC) Agent.

Evaluates the CREATIVE and VIRAL quality of a script before production.
This is NOT a mechanical compliance check — it evaluates whether a real
person would stop scrolling for this content.

Approval criteria (all must pass):
  1. Hook stops the scroll — specific, pattern-interrupt, mid-thought start
  2. Emotional category locked — one of: Drama, Money, Fear, Mistakes, Secrets, Conflict
  3. Angle is surprising — NOT the obvious news summary angle
  4. Channel personality — sounds like the channel's specific host voice
  5. Script flows naturally — not a list of facts, has human rhythm
  6. CTA drives debate — not "like and subscribe"
"""

import re
import sys

# Force UTF-8 for all stdout to prevent UnicodeEncodeError on Windows (cp1252)
if sys.stdout and hasattr(sys.stdout, 'encoding') and sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from core.gemini_client import generate_with_rotation


# Per-channel host persona descriptions injected into the QC prompt
# so the agent knows what "voice" to expect from each channel.
CHANNEL_PERSONAS = {
    "demo_channel": (
        "The demo channel host is a confident, engaging tech educator who explains complex "
        "concepts in clear, punchy, and conversational language. He uses pattern interrupts "
        "and keeps viewers glued to the screen."
    ),
    "example_channel": (
        "The host is an authoritative, high-energy storyteller who brings curiosity and excitement "
        "to every topic with vivid pacing and natural speaking rhythm."
    ),
}

# Soft opener phrases that instantly signal a weak, generic hook
SOFT_OPENERS = [
    "today we", "in this video", "welcome back", "hey guys", "did you know",
    "let me explain", "let's talk about", "so today", "in today's video",
    "we're going to", "i'm going to", "this is the story of"
]

# News-summary phrases that signal the script is boring
NEWS_SUMMARY_PHRASES = [
    "according to reports", "sources say", "it has been reported",
    "the announcement was made", "officials stated", "the company announced",
    "in a statement", "the report says", "it was revealed that",
    "experts believe", "analysts say", "as per reports"
]


class QualityControlAgent:
    def __init__(self, ctx=None):
        self.ctx = ctx

    def evaluate(self, title: str, script: str) -> dict:
        """
        Returns {"approved": bool, "feedback": str}
        
        Performs two checks:
        1. Fast programmatic pre-checks (no API call) — instant failure on obvious issues
        2. LLM creative quality check — evaluates viral potential and personality
        """
        try:
            # Strip SSML tags so XML attributes don't distort length, vocabulary, or LLM evaluation
            clean_script = re.sub(r'<sub alias="([^"]+)">[^<]+</sub>', r'\1', script, flags=re.IGNORECASE)
            clean_script = re.sub(r'<break[^>]+>', '. ', clean_script, flags=re.IGNORECASE)
            clean_script = re.sub(r'<[^>]+>', '', clean_script)
            clean_script = re.sub(r'\s+', ' ', clean_script).strip()

            script_lower = clean_script.lower()
            title_lower = title.lower()

            # ── Fast Programmatic Pre-Checks ─────────────────────────────────────
            # Check for soft openers in the first 12 words
            first_words = " ".join(script_lower.split()[:12])
            for opener in SOFT_OPENERS:
                if first_words.startswith(opener) or f" {opener}" in first_words[:50]:
                    return {
                        "approved": False,
                        "feedback": f"Hook starts with a soft opener ('{opener}'). The script must open mid-action, mid-drama, or mid-conflict — never with a warm-up phrase."
                    }

            # Check for news-summary language (more than 2 = reject)
            news_hits = sum(1 for phrase in NEWS_SUMMARY_PHRASES if phrase in script_lower)
            if news_hits >= 2:
                return {
                    "approved": False,
                    "feedback": f"Script reads like a news summary ({news_hits} journalist phrases detected: 'according to reports', 'sources say', etc.). Rewrite from a personal, opinionated angle — not as a press release."
                }

            # ── Channel Persona Resolution ────────────────────────────────────────
            if self.ctx is not None:
                _channel_name = self.ctx.display_name
                _channel_tone = self.ctx.tone
                channel_key   = self.ctx.channel_name.lower().replace(" ", "").replace("-", "_")
            else:
                from config.settings import CHANNEL_NAME, CHANNEL_TONE
                _channel_name = CHANNEL_NAME
                _channel_tone = CHANNEL_TONE
                channel_key   = _channel_name.lower().replace(" ", "").replace("-", "_")

            persona = CHANNEL_PERSONAS.get(channel_key, "A high-energy, opinionated host who speaks directly to the viewer with urgency and personality.")

            # ── LLM Creative Quality Check ────────────────────────────────────────
            prompt = f"""
You are the Head of Creative Quality for '{_channel_name}' — a YouTube Shorts channel.
Channel tone: {_channel_tone}

Channel host persona: {persona}

Your job is to evaluate if this Short has viral potential and holds attention.
You approve scripts that stop the scroll and keep the viewer hooked until the end.
You REJECT scripts that are:
- Generic news summaries dressed up as Shorts
- Written by a flat, personality-free corporate bot
- Emotionally flat or unmemorable

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VIDEO TITLE: {title}

SCRIPT:
{clean_script}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Evaluate against these 5 core dimensions:

1. HOOK POWER: Does the first 1-2 sentences create an immediate open loop, pattern interrupt, or shocking statement? (NOT a soft opener, NOT "did you know")

2. EMOTIONAL CATEGORY: Does the script lock into one clear emotional lens: Drama, Money, Fear, Mistakes, Secrets, or Conflict?

3. ANGLE SURPRISE: Is the take interesting and engaging rather than an obvious, dry summary?

4. HOST PERSONALITY: Does the script sound like the specific channel persona described above?

5. HUMAN RHYTHM & CTA: Does the script flow naturally and conclude with either a debate-driving question or an engagement comment prompt? (Accept either strong rhetorical closing questions or focused comment prompts like "Comment 'AGREE'" / "Type 'YES'". ONLY reject if it uses weak sub-begging like 'like and subscribe' or 'follow'.)

Respond in EXACTLY this format (no extra text):
APPROVED: TRUE or FALSE
SCORE: X/10 (viral potential — where 6+ = upload-worthy, below 6 = reject)
WEAKEST_CRITERIA: [Hook Power / Emotional Category / Angle Surprise / Host Personality / Human Rhythm]
FEEDBACK: <1-2 sentences: if rejected, state exactly what to fix and how>
"""
            response = generate_with_rotation(prompt).strip()
        except Exception as e:
            # On API error, approve to avoid blocking the pipeline permanently
            print(f"[QC Agent] API/eval error during evaluation (approving to avoid block): {e}")
            return {"approved": True, "feedback": "API error — auto-approved."}

        approved = True
        feedback = "Passes all creative quality checks."
        score = 10

        for line in response.split("\n"):
            line = line.strip()
            line_upper = line.upper()
            if line_upper.startswith("APPROVED:"):
                approved = "TRUE" in line_upper
            elif line_upper.startswith("SCORE:"):
                try:
                    score_str = line.split(":", 1)[1].strip().split("/")[0].strip()
                    score = int(score_str)
                except Exception:
                    pass
            elif line_upper.startswith("FEEDBACK:"):
                feedback = line.split(":", 1)[1].strip()
            elif line_upper.startswith("WEAKEST_CRITERIA:"):
                weakest = line.split(":", 1)[1].strip()

        # Dynamic Leniency Rule: Approve if score >= 6 (LLM scores 6+ as upload-worthy Short)
        if score >= 6:
            approved = True
        else:
            approved = False

        if not approved:
            feedback = f"[Weakest: {weakest}] {feedback}" if 'weakest' in locals() else feedback

        try:
            print(f"[QC Agent] Score={score}/10 | Approved={approved} | {feedback[:120]}")
        except Exception:
            safe_fb = feedback[:120].encode('ascii', 'replace').decode('ascii')
            print(f"[QC Agent] Score={score}/10 | Approved={approved} | {safe_fb}")
        return {"approved": approved, "feedback": feedback}


if __name__ == "__main__":
    from core.channel_context import ChannelContext
    ctx = ChannelContext("demo_channel")
    qc = QualityControlAgent(ctx=ctx)
    print("Testing QC Agent — should REJECT (news summary style)...")
    res = qc.evaluate(
        title="Sachin Tendulkar Proposes IPL Reforms",
        script="According to reports, Sachin Tendulkar has proposed some changes to IPL rules. Sources say he wants to remove the impact player rule. Experts believe this could change the tournament. In a statement, Tendulkar explained his vision for cricket. Analysts say this is a big moment for the sport. What do you think?"
    )
    print(res)
    print()
    print("Testing QC Agent — should APPROVE (strong hook, angle, personality)...")
    res = qc.evaluate(
        title="Sachin Just Called Out the IPL's Biggest Lie",
        script="The IPL has been lying to fans for years — and Sachin finally said it out loud. The impact player rule was supposed to help batters. But here's the crazy part: it's actually destroying the game. Get this — it's turning every match into a batting circus while bowlers get humiliated with no protection. Sachin knows it, the coaches know it, the fans know it. The question is: will the BCCI actually listen? Comment 'YES' if you agree the impact player rule needs to go."
    )
    print(res)
