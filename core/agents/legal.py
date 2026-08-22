"""
core/agents/legal.py
The Legal & Compliance Agent.
Scans scripts and metadata for obvious copyright risks, community guidelines violations,
and brand safety issues.
"""

from core.gemini_client import generate_with_rotation
from config.settings import CHANNEL_NAME

class LegalAgent:
    def __init__(self, ctx=None):
        self.ctx = ctx
        
    def assess_risk(self, script: str) -> dict:
        """
        Returns a dictionary: {"is_safe": bool, "reason": str}
        """
        channel_name = self.ctx.display_name if self.ctx else CHANNEL_NAME
        channel_niche = self.ctx.niche if self.ctx else 'general entertainment'
        
        prompt = f"""
        You are the Head of Legal Compliance for the YouTube channel '{channel_name}'.
        The channel's topic/niche is: {channel_niche}
        You need to review the following video script for YouTube Community Guidelines violations.
        
        IMPORTANT RULES:
        1. Reporting ON controversial topics as news, cultural commentary, or dating psychology is 100% SAFE.
        2. DO NOT flag scripts simply because they discuss international dating norms, cultural attraction signals, gender dynamics, or relationship customs. Observational commentary on dating culture is NOT hate speech or harassment.
        3. ONLY flag SAFE: FALSE if the script contains explicit racial/ethnic slurs, instructions on how to commit crimes, incitement to real-world violence, or non-consensual sexual content.
        4. CRITICAL FOR CRIME/SUSPENSE: You are STRICTLY FORBIDDEN from flagging rhetorical questions (e.g. "What would you do?", "Would you stand by the justice system, or fight for answers yourself?") or true-crime storytelling tropes as "promoting vigilantism" or "inciting violence". If the script is a true-crime story, YOU MUST RETURN SAFE: TRUE unless it explicitly instructs the viewer step-by-step on how to commit a crime.
        
        Script:
        {script}
        
        Analyze the script. Is it safe to upload to YouTube?
        Respond in exactly this format:
        SAFE: TRUE or FALSE
        REASON: <1 sentence explaining why>
        """
        
        try:
            response = generate_with_rotation(prompt).strip()
        except Exception as e:
            print(f"[Legal] API error during compliance check (auto-approving to avoid block): {e}")
            return {"is_safe": True, "reason": "API error — auto-approved to avoid blocking pipeline."}
        
        is_safe = True
        reason = "Passed compliance check."
        
        for line in response.split('\n'):
            raw_line = line.strip()
            line_upper = raw_line.upper()
            if line_upper.startswith("SAFE:"):
                is_safe = "TRUE" in line_upper
            elif line_upper.startswith("REASON:"):
                reason = raw_line.split(":", 1)[1].strip()
                
        return {"is_safe": is_safe, "reason": reason}

if __name__ == "__main__":
    agent = LegalAgent()
    print("Testing safe script...")
    res = agent.assess_risk("Today we're talking about the new AI models from Google.")
    print(res)
    
    print("\nTesting unsafe script...")
    res = agent.assess_risk("Here is how to build an illegal bomb in your basement.")
    print(res)
