from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime

# Using SQLite for zero-config local setup
engine = create_engine("sqlite:///./podcast.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Podcast(Base):
    __tablename__ = "podcasts"
    id = Column(String, primary_key=True)
    filename = Column(String)
    status = Column(String, default="pending") 
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Clip(Base):
    __tablename__ = "clips"
    id = Column(Integer, primary_key=True)
    podcast_id = Column(String, ForeignKey("podcasts.id"))
    title = Column(String)
    start_time = Column(Float)
    end_time = Column(Float)
    file_path = Column(String)

class ProcessingLog(Base):
    __tablename__ = "processing_logs"
    id = Column(Integer, primary_key=True)
    podcast_id = Column(String)
    stage = Column(String)
    message = Column(Text)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)