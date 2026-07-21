"""
core/agents/research_agent.py
The R&D Agent (Head of Research).
Scouts YouTube for trending shorts in the channel's niche,
analyzes competitor metadata/hooks via Gemini, and automatically
injects new strategies into winning_strategy.json.
"""

import json
import os
import subprocess
import time
import sys
from core.gemini_client import generate_with_rotation
from core.telegram_bot import send_message
from core.ytdlp_utils import extend_with_cookies

class ResearchAgent:
    def __init__(self, ctx=None):
        self.ctx = ctx
        channel = ctx.channel_name if ctx else os.environ.get("ACTIVE_CHANNEL", "default")
        self._channel_name = ctx.display_name if ctx else os.environ.get("ACTIVE_CHANNEL", "default")
        self._niche = ctx.niche if ctx else os.environ.get("NICHE", "general content")
        self.strategy_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config", f"winning_strategy_{channel}.json"
        )
        # Ensure the directory exists
        os.makedirs(os.path.dirname(self.strategy_file), exist_ok=True)

    def scout_competitors(self, query: str) -> list:
        """Uses yt-dlp to find top trending shorts for a given query."""
        print(f"[R&D] Scouting YouTube for: '{query}'...")
        cmd = [
            sys.executable, "-m", "yt_dlp",
            f"ytsearch5:{query}",
            "--dump-json",
            "--no-download",
            "--match-filter", "duration < 60", # Ensure they are shorts
            "--ignore-errors"
        ]
        cmd = extend_with_cookies(cmd)
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            videos = []
            for line in result.stdout.strip().split("\n"):
                if not line: continue
                try:
                    data = json.loads(line)
                    videos.append({
                        "title": data.get("title", ""),
                        "description": data.get("description", "")[:200], # keep it short
                        "view_count": data.get("view_count", 0)
                    })
                except json.JSONDecodeError:
                    pass
            # Sort by highest views
            return sorted(videos, key=lambda x: x["view_count"] or 0, reverse=True)
        except subprocess.CalledProcessError as e:
            print(f"[R&D] Scouting failed: {e}")
            return []

    def analyze_and_upgrade(self, competitors: list):
        """Passes competitor data to Gemini to extract new hook styles."""
        if not competitors:
            print("[R&D] No competitor data found. Skipping analysis.")
            return

        print(f"[R&D] Analyzing {len(competitors)} viral competitors via Gemini...")
        
        prompt = f"""
        You are the Head of Research for a YouTube Shorts automation channel.
        Your goal is to analyze competitor videos and extract their packaging strategies.
        
        Here are {len(competitors)} highly viewed competitor Shorts in our niche:
        {json.dumps(competitors, indent=2)}
        
        Analyze their titles. Identify the underlying psychological hook they are using.
        For example: If they use 'The truth about X', the hook is 'The Truth Reveal Hook'.
        
        Output exactly ONE new Hook Style that we should steal and test.
        Format your response as valid JSON matching this schema:
        {{
            "new_hook_style": "Name of Hook (explanation of how to write it)"
        }}
        """
        
        try:
            response_text = generate_with_rotation(prompt)
            # Clean JSON if wrapped in markdown
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
                
            analysis = json.loads(response_text)
            new_hook = analysis.get("new_hook_style")
            
            if new_hook:
                print(f"[R&D] Discovered New Hook Strategy: {new_hook}")
                self._inject_strategy(new_hook)
                
        except Exception as e:
            print(f"[R&D] Analysis failed: {e}")

    def _inject_strategy(self, new_hook: str):
        """Injects the new hook into winning_strategy.json securely."""
        strategy = {}
        if os.path.exists(self.strategy_file):
            try:
                with open(self.strategy_file, "r") as f:
                    strategy = json.load(f)
            except Exception:
                pass
                
        # We append it as an extra R&D hook so the Experiment Engine can pick it up
        strategy["rnd_hook_style"] = new_hook
        
        with open(self.strategy_file, "w") as f:
            json.dump(strategy, f, indent=2)
            
        print("[R&D] Upgraded internal strategy file successfully.")
        
        # Notify the King
        send_message(f"🧠 *R&D Weekly Report*\n\nI scouted top competitors and discovered a new viral trend.\n\n*New Hook Stolen*:\n`{new_hook}`\n\nI have automatically injected this into the Experiment Engine for tomorrow's videos.")

    def run_weekly_research(self):
        print("=== R&D Agent Waking Up ===")
        # Build search query based on niche
        query = f"{self._niche} trending shorts"
        competitors = self.scout_competitors(query)
        self.analyze_and_upgrade(competitors)
        print("=== R&D Agent Finished ===")

if __name__ == "__main__":
    agent = ResearchAgent()
    agent.run_weekly_research()
