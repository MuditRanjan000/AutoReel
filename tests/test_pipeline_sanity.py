"""
AutoReel v1.0 Contributor Sanity & Verification Suite.
Validates core subsystems offline without consuming live external API credits.
Run:
    pytest tests/
    or
    python tests/test_pipeline_sanity.py
"""
import sys
import os
import unittest
import json
import sqlite3

# Ensure repo root is on sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

class TestAutoReelSanity(unittest.TestCase):

    def test_01_channel_context_loading(self):
        """Verify ChannelContext parses demo_channel.json properly."""
        from core.channel_context import ChannelContext
        ctx = ChannelContext("demo_channel")
        self.assertEqual(ctx.channel_name, "demo_channel")
        self.assertTrue(bool(ctx.display_name))
        self.assertTrue(bool(ctx.niche))
        self.assertIsNotNone(ctx.config)
        self.assertEqual(ctx.get("FPS", 30), 30)

    def test_02_database_init_and_indexes(self):
        """Verify SQLite initialization and performance indexes."""
        from core.db import init_db, get_connection
        init_db()
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [r[0] for r in cursor.fetchall()]
            self.assertIn("idx_seen_channel_seen_at", indexes)
            self.assertIn("idx_ab_active_created", indexes)
            self.assertIn("idx_exp_video_id", indexes)
        finally:
            conn.close()

    def test_03_ssml_sanitization(self):
        """Verify SSML tags and phonetic aliases are cleaned accurately."""
        from execution.review_video import strip_ssml
        raw_ssml = '<speak>The tech giant <sub alias="OpenAI">Open-AI</sub> launched GPT-5.<break time="400ms"/><emphasis level="strong">Incredible.</emphasis></speak>'
        clean = strip_ssml(raw_ssml)
        self.assertNotIn("<speak>", clean)
        self.assertNotIn("<sub", clean)
        self.assertNotIn("<break", clean)
        self.assertNotIn("<emphasis", clean)
        self.assertIn("OpenAI", clean)
        self.assertIn("Incredible.", clean)

    def test_04_voiceover_phonetic_extraction(self):
        """Verify phonetic extraction converts SSML aliases to plain spoken text."""
        from core.voiceover import _extract_plain_text_with_phonetics
        text = 'Welcome to <sub alias="DeepSeek">Deep-Seek</sub> AI.<break time="300ms"/>'
        plain = _extract_plain_text_with_phonetics(text)
        self.assertIn("DeepSeek", plain)
        self.assertNotIn("Deep-Seek", plain)
        self.assertNotIn("<sub", plain)

    def test_05_review_programmatic_checks(self):
        """Verify quality review passes clean scripts without false positive word repetition."""
        from execution.review_video import run_programmatic_checks
        mock_data = {
            "run_id": "test_contributor_run",
            "summary": {
                "title": "Why Silicon Valley is Moving to Nuclear Energy",
                "script": {
                    "hook": "Silicon Valley just made a billion dollar bet on atomic power.",
                    "full_script": "<prosody rate='slow'>Silicon Valley just made a billion dollar bet on atomic power.</prosody><break time='500ms'/><emphasis level='strong'>Here is why.</emphasis> Tech giants are buying nuclear plants to feed energy-hungry AI clusters.",
                    "search_queries": ["nuclear energy tech", "silicon valley datacenters", "ai power grid", "atomic reactor cooling", "future technology server"]
                }
            }
        }
        issues = run_programmatic_checks(mock_data)
        # Ensure no false-positives on 'prosody' or 'emphasis'
        for issue in issues:
            desc = issue.get("description", "")
            self.assertNotIn("'prosody'", desc)
            self.assertNotIn("'emphasis'", desc)

if __name__ == "__main__":
    unittest.main(verbosity=2)
