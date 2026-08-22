# Directive: Run Full AutoReel Pipeline

## Goal
Execute the end-to-end 10-stage video pipeline: fetch trending stories, generate scripts, synthesize neural voiceover, render dynamic subtitles, retrieve HD footage, mix audio/video containers, evaluate quality control gating, and optionally publish to YouTube.

---

## Running the Pipeline

### Standard Single-Channel Run
```bash
python execution/run_pipeline.py --channel demo_channel
```

### Dry-Run / Mock Verification (Zero API Cost)
```bash
python execution/run_pipeline.py --channel demo_channel --dry-run
```

### Overriding the Story Topic
To produce a video on a specific topic instead of pulling from live RSS feeds:
```bash
python execution/run_pipeline.py --channel demo_channel --story "Why Fusion Energy Just Hit a Major Milestone"
```

### Forcing Video Upload
```bash
AUTO_POST_YOUTUBE=True python execution/run_pipeline.py --channel demo_channel
```

---

## Pipeline Outputs

Each execution outputs artifacts to structured directories:
- **Final Video**: `output/videos/<run_id>_final.mp4` (1080x1920, 30fps vertical Short)
- **Thumbnail Frame**: `output/thumbnails/<run_id>_thumb.jpg`
- **Execution Summary & Metadata**: `output/logs/<run_id>_summary.json`
- **Subtitles**: `output/videos/<run_id>_subtitles.ass`
- **Voiceover Audio**: `output/videos/<run_id>_voiceover.mp3`

---

## Automated Housekeeping

1. **Storage Maintenance**: Intermediate clip fragments and audio stems are purged after successful completion or by the twice-daily scheduler cleanup daemon (`03:00` and `15:00`).
2. **Manual Cleanup**:
   ```bash
   python execution/cleanup_all.py
   ```
3. **Keep Intermediate Files**:
   ```bash
   python execution/run_pipeline.py --channel demo_channel --no-cleanup
   ```

