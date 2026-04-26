"""
User model, JWT auth, and admin approval system.
Add to existing database.py imports.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime
import secrets

# Add these to your existing database.py alongside other models

class UserModel:
    """
    Paste this class into your existing database.py
    alongside FeedModel, SourceModel, etc.
    """
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(200), default="")
    role = Column(String(20), default="pending")  # pending | approved | admin
    created_at = Column(DateTime, default=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(255), nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    # Each user has their own feeds (feed_id ownership checked in routes)
    notes = Column(Text, default="")  # admin notes
