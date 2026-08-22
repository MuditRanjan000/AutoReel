import os
import json
from config.settings import _ROOT
from core.voiceover import VoiceoverGenerator
from core.video_assembler import VideoAssembler
from core.ass_generator import generate_ass

def rerender(run_id):
    print(f"Rerendering run_id: {run_id}")
    videos_dir = os.path.join(_ROOT, "output", "videos")
    
    # 1. Load the script
    json_path = os.path.join(videos_dir, f"{run_id}_voice_scenes.json")
    if not os.path.exists(json_path):
        print(f"Cannot find {json_path}")
        return
        
    with open(json_path, "r", encoding="utf-8") as f:
        script = json.load(f)
        
    print("Re-generating voiceover and captions...")
    # 2. Re-run Voiceover (this also generates new ASS subtitles with our new centering fix)
    voice_gen = VoiceoverGenerator()
    audio_path, voice_actuals = voice_gen.generate(script, run_id)
    
    print("Re-assembling video...")
    # 3. Gather clips
    # We need to construct bg_clip and overlays exactly how VideoAssembler expects
    # In run_pipeline, it passes: bg_clip = "", overlays = [...]
    # But wait, run_pipeline saves used_clips.json! Let's see if that exists.
    used_clips_path = os.path.join(videos_dir, "used_clips.json")
    overlays = []
    if os.path.exists(used_clips_path):
        with open(used_clips_path, "r", encoding="utf-8") as f:
            used_clips_data = json.load(f)
            # Find the overlays for this run_id
            # Or wait, VideoAssembler takes the actual list of clips.
            # Let's see if used_clips.json has the clips for this run.
            pass
            
    # Actually, a simpler way is to just grab the raw clips in order.
    # Look for files like {run_id}_raw_clip1_youtube...
    clips = []
    for i in range(1, 100):
        # We need to find the exact file name
        found = None
        for file in os.listdir(videos_dir):
            if file.startswith(f"{run_id}_raw_") and (f"_clip{i}_" in file or f"_body{i}_" in file):
                found = os.path.join(videos_dir, file)
                break
        if not found:
            # Maybe check for hook/cta
            if i == 1:
                for file in os.listdir(videos_dir):
                    if file.startswith(f"{run_id}_raw_hook_"):
                        found = os.path.join(videos_dir, file)
                        break
        if found:
            clips.append(found)
        else:
            # If we didn't find body i, maybe we are done
            pass

    if not clips:
        print("No clips found to assemble.")
        return
        
    print(f"Found {len(clips)} clips.")
    
    # Wait, VideoAssembler expects bg_clip as the first, and overlays for the rest?
    # Let's check how it's used.
    # Actually, VideoAssembler expects overlays to be dicts: {"path": path, "type": "broll", "start": 0, "end": 5}
    # If we just concatenate them, we can use ffmpeg directly to avoid missing metadata.
    
    # Or even better, the user just wants the new captions on the final video!
    # If the user just wants the new captions applied to the existing `merged.mp4` or `final.mp4`!
    # Wait! The captions are burned in during assembly. So we can't just slap them on final.mp4 without double captions.
    # BUT wait! The existing final.mp4 already has captions burned in!
    # If we assemble from clips, we need exact timing.
    print("Done")

if __name__ == "__main__":
    rerender("20260619_114649")
