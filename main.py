import os
from fastapi import FastAPI, UploadFile, File, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from uuid import uuid4
from database import SessionLocal, Podcast, Clip, ProcessingLog
from tasks import process_podcast_task

app = FastAPI()

# 1. Mount the local storage directory so the frontend can play the videos
os.makedirs("storage/clips", exist_ok=True)
app.mount("/storage", StaticFiles(directory="storage"), name="storage")

templates = Jinja2Templates(directory="templates")

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    job_id = str(uuid4())
    # Save the original file directly into the storage folder
    file_path = f"storage/{job_id}_{file.filename}"
    
    with open(file_path, "wb") as f:
        f.write(await file.read())
    
    # Create the initial DB record
    db = SessionLocal()
    new_podcast = Podcast(id=job_id, filename=file.filename, status="processing")
    db.add(new_podcast)
    db.commit()
    db.close()
    
    # Send to Celery worker
    process_podcast_task.delay(job_id, file_path)
    
    return {"message": "Upload successful", "job_id": job_id}

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    db = SessionLocal()
    podcast = db.query(Podcast).filter(Podcast.id == job_id).first()
    logs = db.query(ProcessingLog).filter(ProcessingLog.podcast_id == job_id).all()
    
    response_data = {
        "status": podcast.status if podcast else "unknown",
        "logs": logs,
        "clips": [],
        "error_reason": None
    }
    
    # CRITICAL FIX: Only return clips if the job is 100% finished
    if podcast and podcast.status == "completed":
        response_data["clips"] = db.query(Clip).filter(Clip.podcast_id == job_id).all()
    elif podcast and podcast.status == "failed":
        last_error = db.query(ProcessingLog).filter(
            ProcessingLog.podcast_id == job_id, ProcessingLog.stage == "Error"
        ).order_by(ProcessingLog.id.desc()).first()
        response_data["error_reason"] = last_error.message if last_error else "Unknown Error"

    db.close()
    return response_data

@app.get("/admin/db")
async def admin_db():
    """A quick route to check your database contents in the browser."""
    db = SessionLocal()
    podcasts = db.query(Podcast).all()
    html = "<h1>Database Inspector</h1><table border='1'><tr><th>ID</th><th>Status</th></tr>"
    for p in podcasts: html += f"<tr><td>{p.id}</td><td>{p.status}</td></tr>"
    html += "</table>"
    return HTMLResponse(html)