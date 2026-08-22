"""
core/agents/analytics_agent.py
The Analytics & Strategy Agent (CMO).
Reads the SQLite database to identify which hooks, voices, and CTAs
are generating the highest retention, and alerts the King (via Telegram).
"""

import json
import os
from collections import defaultdict
from core.experiment_tracker import ExperimentTracker
from core.telegram_bot import send_message
from config.settings import MIN_SAMPLES_TO_TUNE

class AnalyticsAgent:
    def __init__(self, ctx=None):
        self.ctx = ctx
        self.tracker = ExperimentTracker()

    def run_analysis(self):
        channel = self.ctx.channel_name if self.ctx else os.environ.get("ACTIVE_CHANNEL", "default")
        display = self.ctx.display_name  if self.ctx else channel
        print(f"[AnalyticsAgent] Running weekly A/B test analysis for {display} ({channel})...")
        all_experiments = self.tracker.get_all_with_metrics()
        
        # Isolate experiments to ONLY the active channel
        experiments = [exp for exp in all_experiments if exp["parameters"].get("channel_name") == channel]
        
        if not experiments:
            print("[AnalyticsAgent] No metric data available yet for this channel.")
            return
            
        print(f"[AnalyticsAgent] Analyzing {len(experiments)} completed videos...")
        
        # Analyze parameters
        hooks = self._analyze_parameter(experiments, "hook_style")
        voices = self._analyze_parameter(experiments, "voice_name")
        moods = self._analyze_parameter(experiments, "bgm_mood")
        
        # Generate report
        report = f"📊 *Weekly Analytics Report ({display})*\n\n"
        
        report += self._format_findings("Hook Styles", hooks)
        report += self._format_findings("Voice Actors", voices)
        report += self._format_findings("Music Moods", moods)
        
        # Send to King
        send_message(report)
        print("[AnalyticsAgent] Report sent to Telegram.")
        
        # Save Winning Strategy
        winners = {}
        if hooks and hooks[0]["eligible"]: winners["hook_style"] = hooks[0]["value"]
        if voices and voices[0]["eligible"]: winners["voice_name"] = voices[0]["value"]
        if moods and moods[0]["eligible"]: winners["bgm_mood"] = moods[0]["value"]
        
        strategy_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", f"winning_strategy_{channel}.json")
        with open(strategy_file, "w") as f:
            json.dump(winners, f, indent=2)
        print(f"[AnalyticsAgent] Winning strategy saved to {strategy_file}")
        
    def _analyze_parameter(self, experiments: list, param_key: str) -> dict:
        """Groups experiments by parameter and calculates average retention."""
        groups = defaultdict(list)
        for exp in experiments:
            val = exp["parameters"].get(param_key)
            m = exp["metrics"].get("avg_view_percentage")
            if val is not None and m is not None:
                groups[str(val)].append(float(m))
                
        rankings = []
        for val, scores in groups.items():
            avg = sum(scores) / len(scores)
            rankings.append({
                "value": val,
                "avg": round(avg, 1),
                "samples": len(scores),
                "eligible": len(scores) >= MIN_SAMPLES_TO_TUNE
            })
            
        # Sort by average retention descending
        return sorted(rankings, key=lambda x: x["avg"], reverse=True)
        
    def _format_findings(self, title: str, rankings: list) -> str:
        """Formats the A/B test results into a readable string."""
        if not rankings:
            return f"*{title}*: No data yet.\n\n"
            
        res = f"*{title}*\n"
        for i, r in enumerate(rankings):
            medal = "🥇 " if i == 0 else "   "
            res += f"{medal}`{r['value']}`: {r['avg']}% ({r['samples']} videos)\n"
            
        res += "\n"
        return res

if __name__ == "__main__":
    agent = AnalyticsAgent()
    agent.run_analysis()
