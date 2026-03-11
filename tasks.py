import imageio_ffmpeg
import os
import time
import spacy
import subprocess
from celery import Celery
from faster_whisper import WhisperModel
from database import SessionLocal, Podcast, Clip, ProcessingLog

# 1. Initialize Celery (Remember to run with --pool=solo on Windows)
celery_app = Celery("tasks", broker="redis://localhost:6379/0")

# 2. Initialize Whisper (CPU mode, int8 for speed)
whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

# 3. Initialize spaCy (Safely)
nlp = None
try:
    nlp = spacy.load("en_core_web_sm")
    print("🧠 AI Engine: spaCy NLP model loaded successfully!")
except OSError:
    print("⚠️ AI Engine: spaCy model not found. Using fallback keyword matching.")
    print("   Run 'python -m spacy download en_core_web_sm' to fix this.")

def log_event(db, job_id, stage, message):
    """Helper to write processing status to the database."""
    log = ProcessingLog(podcast_id=job_id, stage=stage, message=message)
    db.add(log)
    db.commit()
    print(f"[{stage}] {message}")

def find_ai_segments(transcript_segments, clip_count=10):
    """
    AI decides clip length by looking for semantic boundaries.
    Strictly enforces zero overlap and ensures diverse moments.
    """
    scored_segments = []
    impact_words = {"future", "problem", "crazy", "solution", "discovery", "lesson", "advice", "massive", "secret"}
    absolute_max_time = transcript_segments[-1]['end'] if transcript_segments else 0

    print("\n" + "="*50)
    print("🤖 AI SCORING IN PROGRESS...")

    # 1. Score segments based on impact
    for i, seg in enumerate(transcript_segments):
        score = 0
        text = seg['text'].lower()
        if "?" in text: score += 15 
        score += sum(5 for word in impact_words if word in text)
        if nlp:
            doc = nlp(seg['text'])
            if len(doc.ents) > 0: score += (5 * len(doc.ents)) 
                
        if score > 0:
            scored_segments.append({"index": i, "score": score})

    # Sort to pick the absolute highest-scoring moments first
    scored_segments.sort(key=lambda x: x['score'], reverse=True)
    
    final_clips = []

    # 2. Extract and Validate Clips
    for item in scored_segments:
        if len(final_clips) >= clip_count: break
        idx = item['index']

        # Look back slightly for context (reduced to 1 to tighten clips)
        start_idx = max(0, idx - 1)
        end_idx = idx
        
        # Look forward for a semantic break
        for i in range(idx + 1, min(len(transcript_segments), idx + 8)):
            end_idx = i
            check_text = transcript_segments[i]['text'].lower()
            if any(p in check_text for p in ["thank you", "anyway", "so yeah", "basically"]): break
            if (transcript_segments[i]['end'] - transcript_segments[i]['start']) > 3.0: break

        start_time = max(0.0, transcript_segments[start_idx]['start'])
        end_time = min(transcript_segments[end_idx]['end'], absolute_max_time)

        # Limit to max 60 seconds
        if (end_time - start_time) > 60.0:
            end_time = start_time + 60.0
            
        duration = end_time - start_time

        # Reject micro-glitches (e.g. less than 3 seconds), but allow intended short clips
        if duration < 3.0: 
            continue

        # --- THE OVERLAP & DIVERSITY GUARD ---
        # We require at least a 2-second gap between any two clips to ensure they are diverse moments
        min_gap = 2.0 
        is_overlapping = False
        
        for existing_clip in final_clips:
            # Math formula to check if two time ranges overlap (including the required gap)
            if (start_time < (existing_clip['end'] + min_gap)) and (end_time > (existing_clip['start'] - min_gap)):
                is_overlapping = True
                break # Exit the loop, we found a collision
        
        if is_overlapping:
            continue # Skip this clip entirely and look for the next highest-scoring, diverse moment

        # If it passes all checks, save it
        clip_text = transcript_segments[idx]['text'][:40].replace('\n', ' ').strip()
        print(f"✔️ Found clip: [{start_time:5.1f}s to {end_time:5.1f}s] | {clip_text}...")
        
        final_clips.append({
            "title": f"Highlight: {clip_text}...",
            "start": start_time,
            "end": end_time
        })

    # Sort final clips chronologically so they appear in order of the video
    final_clips.sort(key=lambda x: x['start'])

    print("="*50 + "\n")
    return final_clips

@celery_app.task(name="process_podcast_task")
def process_podcast_task(job_id, file_path):
    db = SessionLocal()
    abs_file_path = os.path.abspath(file_path)
    clips_dir = os.path.join("storage", "clips")
    os.makedirs(clips_dir, exist_ok=True)
    
    start_time_total = time.time()
    
    try:
        # --- STAGE 1: TRANSCRIPTION ---
        log_event(db, job_id, "Transcription", "Whisper is analyzing audio...")
        segments, _ = whisper_model.transcribe(abs_file_path, beam_size=5)
        transcript_data = [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
        
        # --- STAGE 2: AI ANALYSIS ---
        log_event(db, job_id, "NLP Analysis", "Scanning for viral highlights...")
        interesting_segments = find_ai_segments(transcript_data, clip_count=10)
        
        if not interesting_segments:
            log_event(db, job_id, "Warning", "No highlights found, using default 30s start.")
            interesting_segments = [{"title": "Intro Segment", "start": 0, "end": 30}]

        # --- STAGE 3: CLIPPING (FFmpeg Direct Copy) ---
        log_event(db, job_id, "Clipping", f"Extracting {len(interesting_segments)} clips rapidly...")
        
        # This tells Windows exactly where the hidden Python FFmpeg tool is located
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        
        for idx, seg in enumerate(interesting_segments):
            clip_filename = f"{job_id}_clip_{idx}.mp4"
            output_path = os.path.join(clips_dir, clip_filename)
            
            # Use the absolute path to the executable
            ffmpeg_command = [
                ffmpeg_exe,                     # <--- CHANGED THIS LINE
                "-y",                           # Overwrite output
                "-ss", str(seg["start"]),       # Start timestamp
                "-to", str(seg["end"]),         # End timestamp
                "-i", abs_file_path,            # Input file
                "-c:v", "copy",                 # Direct video copy
                "-c:a", "copy",                 # Direct audio copy
                output_path                     # Output file
            ]
            
            try:
                subprocess.run(ffmpeg_command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                db.add(Clip(
                    podcast_id=job_id,
                    title=seg["title"],
                    start_time=seg["start"],
                    end_time=seg["end"],
                    file_path=f"clips/{clip_filename}"
                ))
                db.commit()
            except subprocess.CalledProcessError as e:
                print(f"❌ FFmpeg failed on clip {idx}: {e}")
                log_event(db, job_id, "Warning", f"Failed to extract clip {idx}")

        # --- STAGE 4: FINALIZE ---
        db.query(Podcast).filter(Podcast.id == job_id).update({"status": "completed"})
        db.commit()
        log_event(db, job_id, "Success", f"Finished entirely in {round(time.time() - start_time_total, 1)}s")

    except Exception as e:
        log_event(db, job_id, "Error", f"Processing failed: {str(e)}")
        db.query(Podcast).filter(Podcast.id == job_id).update({"status": "failed"})
        db.commit()
    finally:
        db.close()