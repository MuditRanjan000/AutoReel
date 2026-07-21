"""
core/experiment_engine.py
An automated A/B testing engine that randomly samples video styles,
tones, topics, and YouTube packaging metadata to prevent the channel
from getting stuck in a single pattern and to discover what goes viral.
"""

import random
import json
import os
from collections import deque

class ExperimentEngine:
    def __init__(self, ctx=None):
        """
        Args:
            ctx: Optional ChannelContext. When provided, channel-specific pools
                 are selected from ctx.channel_name instead of os.environ.
                 This makes ExperimentEngine fully thread-safe and channel-isolated.
        """
        self.ctx = ctx

        # ── Video Production Pools ──
        self.voices = [
            "en-US-Journey-D"
        ]
        self.voice_rates = ["+5%", "+8%", "+10%"]  # A/B tested — let data decide (epsilon-greedy will demote if robotic)
        
        # Format: (primary_color, secondary_color)
        self.caption_styles = [
            ("Yellow", "White"),
            ("Green", "White"),
            ("Red", "White"),
            ("Cyan", "White")
        ]
        
        # Default BGM Moods
        self.bgm_moods = ["suspense", "phonk", "dramatic", "upbeat", "aggressive", "emotional", "sci-fi", "ambient", "trap", "cheerful"]
        
        # Define channel-specific topic sub-niches
        # Prefer ctx.channel_name (thread-safe); fall back to env var for legacy callers
        if ctx is not None:
            channel = ctx.channel_name.strip().lower()
        else:
            channel = os.environ.get("ACTIVE_CHANNEL", "example_philosophy").strip().lower()
        if "cricket" in channel:
            self.voices = [
                "en-GB-Journey-D",
                "en-AU-Journey-D"
            ]
            self.voice_rates = ["+2%", "+5%", "+8%"]  # A/B tested — data decides (slower for sports narration readability)
            self.topics = [
                "IPL drama, conflicts, and betrayals",
                "Player mistakes that cost the match or season",
                "Cricket secrets and stories nobody talks about",
                "Shocking match results the fans won't forget"
            ]
            self.tones = [
                "Dramatic and Shocking",
                "Outrage and Conflict",
                "Critical and Polarizing",
                "Fear and Urgency"
            ]
            self.bgm_moods = ["phonk", "dramatic", "upbeat", "aggressive", "trap"]
        elif "culture" in channel:
            self.voices = [
                "en-US-Journey-F",
                "en-US-Journey-O"
            ]
            self.voice_rates = ["-2%", "+0%", "+2%"]  # Conversational, natural pacing  # A/B tested — data decides (slower for conversational culture vlogs)
            self.topics = [
                "why women from this culture behave this way — and it surprises everyone",
                "shocking dating rules and relationship norms that foreigners can't believe",
                "the unwritten social rules that make women from this culture unique",
                "culture shocks expats experience when dating or befriending local women"
            ]
            self.tones = [
                "Shocking and Curious",
                "Conversational and Intriguing",
                "Revealing and Taboo-breaking",
                "Informative and Entertaining"
            ]
            self.bgm_moods = ["upbeat", "cheerful"]
            # ── Video Format Pool (self-learning will shift weights over time) ──
            # Based on reference channel analysis — all formats use women from
            # specific cultures as the primary visual hook throughout.
            # Performance data from reference channel:
            #   A_culture_observation: "Why Russian Women Rarely Smile" (268K)
            #   B_dating_guide:        "Culture Shocks Dating a Japanese Girl" (4.8M!)
            #   C_culture_shock_place: "Culture Shocks Your First Time in Azerbaijan" (4.3M!)
            #   D_expat_perspective:   "3 Years in Philippines and Still..." (2.2M)
            #   E_meet_a_girl:         "Culture Shocks When You Meet a Girl from..." (1.9M)
            #   F_survival_guide:      "Vietnam Survival Guide for First Timers" (4M!)
            #   G_will_shock_break:    "South America Will Break You" (3.7M!)
            self.video_formats = [
                "A_culture_observation",   # "Why [Culture] Women [do X]"
                "B_dating_guide",          # "Culture Shocks Dating a [Culture] Girl" ⭐ 4.8M
                "C_culture_shock_place",   # "Culture Shocks Your First Time in [Place]" ⭐ 4.3M
                "D_expat_perspective",     # "[N] Years in [Country] and Still..." 
                "E_meet_a_girl",           # "Culture Shocks When You Meet a Girl from [Country]"
                "F_survival_guide",        # "[Country] Survival Guide for First Timers" ⭐ 4M
                "G_will_shock_break",      # "[Country/Region] Will Break/Shock You" ⭐ 3.7M
            ]
        elif "crime" in channel:
            self.voices = [
                "en-US-Journey-D"
            ]
            self.voice_rates = ["-5%", "-2%", "+0%"]  # Slower for true crime gravity
            self.topics = [
                "Cold cases that were finally solved after decades",
                "Unsolved mysteries that still baffle investigators",
                "Famous heists and the brilliant minds behind them",
                "Forensic breakthroughs that changed criminal history"
            ]
            self.tones = [
                "Serious and Suspenseful",
                "Forensic and Analytical",
                "Respectful and Somber",
                "Mysterious and Investigative"
            ]
            self.bgm_moods = ["suspense", "dark", "dramatic"]
        elif "stoic" in channel:
            self.voices = [
                "en-US-Studio-Q"
            ]
            self.voice_rates = ["-8%", "-5%", "-2%"]  # Extra slow for maximum authoritative gravity
            self.topics = [
                "Why modern society makes men weak",
                "Stoic secrets to mastering your emotions",
                "The truth about discipline vs motivation",
                "How to become emotionally unbreakable"
            ]
            self.tones = [
                "Deep and Philosophical",
                "Commanding and Authoritative",
                "Serious and Reflective",
                "Intense and Motivating"
            ]
            self.bgm_moods = ["dramatic", "ambient"]
        else: # Default Fallback for unexpected channels
            self.topics = [
                "Tech giant mistakes and public failures",
                "AI secrets they don't want you to know",
                "Tech conflict: who's winning, who's lying",
                "The fear behind the next big tech shift"
            ]
            self.tones = [
                "Conflict and Outrage",
                "Secrets and Insider Exposure",
                "Fear and Urgency",
                "Drama and Shocking Reversals"
            ]
            self.bgm_moods = ["suspense", "sci-fi", "dramatic", "phonk", "ambient", "trap"]

        # ── YouTube Packaging Pools ──
        self.title_strategies = [
            "Clickbait Question (e.g. 'Is This The End Of...?')",
            "Shocking Statement (e.g. 'They Lied To You About...')",
            "Minimalist and Mysterious (e.g. 'the hidden truth.')",
            "Bold Claim (e.g. 'This Changes Everything')"
        ]
        
        self.description_strategies = [
            "Minimalist (just 1 sentence and 3 tags)",
            "SEO Stuffed (long detailed paragraphs with lots of keywords)",
            "Storytelling (a hooky paragraph that makes them want to watch)"
        ]
        
        self.tagging_strategies = [
            "Broad Viral Tags (e.g. #shorts #viral #trending)",
            "Niche Specific Tags (highly targeted to the exact topic)"
        ]
        
        # ── BGM Volume Pools ──
        # Set to [0.22, 0.28, 0.35, 0.40] across all channels based on user preference for audible BGM.
        self.bgm_volumes = [0.24, 0.28, 0.32, 0.36]
        self.thumbnail_colors = ["Red", "Blue", "Green", "Purple"]
        
        # ── Engagement Pools ──
        self.hook_styles = [
            "Question Hook (open with a shocking question)",
            "Statistic Hook (open with a specific number or data point)",
            "Contradiction Hook (state something that seems impossible first)",
            "Second Person Hook (make the viewer the protagonist immediately)",
            "Quantitative Compulsion (start with an irrationally large number or grueling task)"
        ]
        
        self.cta_styles = [
            "Reflective Open Question (e.g. invite reflection on the core philosophical or mystery lesson)",
            "Debate Trigger (e.g. a natural open question about what they would do in this scenario)",
            "Memorable Closing Observation (e.g. a profound final thought that loop-hooks back to the start)"
        ]
        
        self.narrative_frameworks = [
            "Mystery Box (open with an unsolved question, tease the answer until the end)",
            "Inverted Pyramid (give the biggest conclusion first, then explain the details)",
            "Countdown/Listicle (present 3 fast facts or points sequentially)",
            "Conflict to Resolution (present two opposing forces, then show who won)",
            "Shock to Explanation (open with an unbelievable fact, then scientifically or logically explain it)"
        ]
        
        self.pacing_styles = ["Fast (1.5s cuts)", "Dynamic (Sentiment based)", "Relaxed (3.0s cuts)"]
        
        self._apply_winning_strategy()
        self._apply_pool_order_from_file()
        # Cooldown: track last 3 (tone, hook_style, bgm_mood) combos to prevent exact repeats
        self._recent_combos: deque = deque(maxlen=3)
        
    def _apply_pool_order_from_file(self):
        """Read pool orderings from config/pool_order_{channel}.json and reorder in-memory pools.
        Written by auto_tune.py's reorder_engine_pool(). This replaces the risky
        regex-on-Python-source approach: auto_tune never touches .py files for pool ordering.
        """
        channel = self.ctx.channel_name if self.ctx else os.environ.get("ACTIVE_CHANNEL", "default")
        order_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config", f"pool_order_{channel}.json"
        )
        if not os.path.exists(order_file):
            return
        try:
            with open(order_file, "r", encoding="utf-8") as f:
                overrides = json.load(f)
            pool_map = {
                "voices":               self.voices,
                "tones":                getattr(self, "tones", []),
                "topics":               getattr(self, "topics", []),
                "title_strategies":     getattr(self, "title_strategies", []),
                "thumbnail_colors":     getattr(self, "thumbnail_colors", []),
                "hook_styles":          getattr(self, "hook_styles", []),
                "cta_styles":           getattr(self, "cta_styles", []),
                "tagging_strategies":   getattr(self, "tagging_strategies", []),
                "bgm_moods":            getattr(self, "bgm_moods", []),
                "narrative_frameworks": getattr(self, "narrative_frameworks", []),
                "pacing_styles":        getattr(self, "pacing_styles", []),
            }
            for attr, ordered_vals in overrides.items():
                if attr in pool_map and isinstance(ordered_vals, list):
                    current = pool_map[attr]
                    reordered = ordered_vals + [v for v in current if v not in ordered_vals]
                    setattr(self, attr, reordered)
            print(f"[ExperimentEngine] Pool order applied from {order_file}")
        except Exception as e:
            print(f"[ExperimentEngine] WARNING: Failed to apply pool order from {order_file}: {e}")

    def _apply_winning_strategy(self):
        """Reads the CMO's findings and biases the random selections heavily toward winners."""
        # Use ctx.channel_name when available for full channel isolation
        if self.ctx is not None:
            channel = self.ctx.channel_name
        else:
            channel = os.environ.get("ACTIVE_CHANNEL", "default")
        strategy_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", f"winning_strategy_{channel}.json")
        if not os.path.exists(strategy_file): return
        
        try:
            with open(strategy_file, "r") as f:
                winners = json.load(f)
                
            # If a winner exists, insert it 5 extra times into the pool (increasing probability to ~60-80%)
            if winners.get("voice_name"): self.voices.extend([winners["voice_name"]] * 5)
            if winners.get("hook_style"): self.hook_styles.extend([winners["hook_style"]] * 5)
            if winners.get("cta_style"): self.cta_styles.extend([winners["cta_style"]] * 5)
            if winners.get("bgm_mood"): self.bgm_moods.extend([winners["bgm_mood"]] * 5)
            if winners.get("narrative_framework"): self.narrative_frameworks.extend([winners["narrative_framework"]] * 5)
            if winners.get("pacing_style"): self.pacing_styles.extend([winners["pacing_style"]] * 5)
            
            # Inject new hooks discovered by the R&D Agent (Spying on competitors)
            if winners.get("rnd_hook_style"):
                # We add the new competitor hook 2 times so it has a good chance of being tested
                self.hook_styles.extend([winners["rnd_hook_style"]] * 2)
            
            # ── Video Format Self-Learning ─────────────────────────────────────
            # Load format weights and bias the pool accordingly.
            # Weight of 2.0 = inserted 2x, so double the probability vs weight 1.0.
            format_weights = winners.get("video_format_weights", {})
            if format_weights and hasattr(self, "video_formats"):
                weighted_pool = []
                for fmt in self.video_formats:
                    weight = format_weights.get(fmt, 1.0)
                    weighted_pool.extend([fmt] * max(1, int(weight * 2)))
                self.video_formats = weighted_pool
            
            # Inject any new AI-suggested formats (Format C, D, etc.) from learning
            new_formats = winners.get("suggested_new_formats", [])
            if new_formats and hasattr(self, "video_formats"):
                for fmt in new_formats:
                    fmt_id = fmt.get("id")
                    if fmt_id and fmt_id not in self.video_formats:
                        # New format — add once to start testing
                        self.video_formats.append(fmt_id)
                        print(f"[ExperimentEngine] New AI-suggested format injected: {fmt_id}")
        except Exception as e:
            print(f"[ExperimentEngine] WARNING: Failed to load winning strategy from {strategy_file}: {e}")
            print(f"[ExperimentEngine] Falling back to default exploration pools.")

    def _epsilon_greedy_pick(self, pool: list, epsilon: float = 0.3):
        """
        Epsilon-greedy selection so auto_tune pool reordering has real effect.
        With probability (1 - epsilon) = 70%: pick pool[0] (the current winner).
        With probability epsilon = 30%: explore randomly from the rest of the pool, OR rarely from the global pool.
        """
        if not pool:
            return None
            
        if len(pool) == 1 or random.random() > epsilon:
            return pool[0]  # Exploit: the winner is always first after auto_tune reorders
            
        # 10% chance during exploration to pick a completely random voice from the master Journey pool
        # to ensure we don't get permanently stuck in local minimums if a better voice exists.
        if any("Journey" in str(v) for v in pool): # Simple check if this is a voice pool
            if random.random() < 0.1:
                global_voices = ["en-US-Journey-D", "en-US-Journey-O", "en-US-Journey-F", "en-GB-Journey-D", "en-GB-Journey-O", "en-AU-Journey-D", "en-AU-Journey-O"]
                return random.choice(global_voices)
                
        return random.choice(pool[1:] if len(pool) > 1 else pool)

    def _dedupe_combo(self, recipe: dict) -> dict:
        """
        Prevent the exact same (tone, hook_style, bgm_mood) triple appearing
        more than once in the last 3 runs. Forces variety in exploration.
        """
        key = (recipe.get("tone"), recipe.get("hook_style"), recipe.get("bgm_mood"))
        if key in self._recent_combos:
            # Force a different hook_style to break the repetition
            alternative_hooks = [h for h in self.hook_styles if h != recipe.get("hook_style")]
            if alternative_hooks:
                recipe["hook_style"] = random.choice(alternative_hooks)
        self._recent_combos.append(key)
        return recipe

    def generate_recipe(self) -> dict:
        """Samples a complete recipe using epsilon-greedy to exploit winners 70% of the time."""
        caption_style = random.choice(self.caption_styles)
        recipe = {
            # Voice is intentionally omitted here so that voiceover.py respects the JSON config.
            # Epsilon-greedy: auto_tune reordering NOW has real effect on selection probability
            "voice_rate":          random.choice(self.voice_rates),  # Full exploration on rate
            "caption_color_primary":   caption_style[0],
            "caption_color_secondary": caption_style[1],
            "topic":           random.choice(self.topics),      # Full exploration on topic
            "tone":            self._epsilon_greedy_pick(self.tones),
            "title_strategy":  self._epsilon_greedy_pick(self.title_strategies),
            "description_strategy": random.choice(self.description_strategies),
            "tagging_strategy":    self._epsilon_greedy_pick(self.tagging_strategies),
            "bgm_volume":      random.choice(self.bgm_volumes),  # Full exploration on volume
            "bgm_mood":        self._epsilon_greedy_pick(self.bgm_moods),
            "thumbnail_color": self._epsilon_greedy_pick(self.thumbnail_colors),
            "hook_style":      self._epsilon_greedy_pick(self.hook_styles),
            "cta_style":       self._epsilon_greedy_pick(self.cta_styles),
            "narrative_framework": self._epsilon_greedy_pick(self.narrative_frameworks),
            "pacing_style":        self._epsilon_greedy_pick(self.pacing_styles),
        }
        # Include video_format if this channel has the format pool
        if hasattr(self, "video_formats") and self.video_formats:
            recipe["video_format"] = self._epsilon_greedy_pick(self.video_formats)
            
        # --- MANUAL CHANNEL OVERRIDES ---
        # Strictly obey user settings defined in channels/*.json if they exist
        if self.ctx and getattr(self.ctx, "raw_config", None):
            config = self.ctx.raw_config
            if "voice_rate" in config:
                recipe["voice_rate"] = config["voice_rate"]
            if "bgm_volume" in config:
                recipe["bgm_volume"] = config["bgm_volume"]
            if "pacing_style" in config:
                recipe["pacing_style"] = config["pacing_style"]
                
            # Visual overrides
            visuals = config.get("visuals", {})
            if "primary_text_color" in visuals:
                recipe["caption_color_primary"] = visuals["primary_text_color"]
            if "secondary_text_color" in visuals:
                recipe["caption_color_secondary"] = visuals["secondary_text_color"]
            if "thumbnail_color" in visuals:
                recipe["thumbnail_color"] = visuals["thumbnail_color"]

        # Prevent exact same tone+hook+mood triple 3 runs in a row
        return self._dedupe_combo(recipe)
