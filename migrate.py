#!/usr/bin/env python3
"""
Run this once to migrate existing DB to add auth + new columns.
Usage: python3 migrate.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".newsmonitoer" / "news.db"


def migrate():
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH} — will be created on first startup")
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    def cols(table):
        return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]

    def tables():
        return [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

    existing = tables()
    print(f"Tables: {existing}")

    # feeds table
    if "feeds" in existing:
        feed_cols = cols("feeds")
        if "delivery_times" not in feed_cols:
            cur.execute("ALTER TABLE feeds ADD COLUMN delivery_times JSON DEFAULT '[]'")
            if "schedule_times" in feed_cols:
                cur.execute("UPDATE feeds SET delivery_times = schedule_times")
            print("✓ feeds.delivery_times added")
        if "last_delivered_at" not in feed_cols:
            cur.execute("ALTER TABLE feeds ADD COLUMN last_delivered_at DATETIME")
            print("✓ feeds.last_delivered_at added")
        if "user_id" not in feed_cols:
            cur.execute("ALTER TABLE feeds ADD COLUMN user_id INTEGER REFERENCES users(id)")
            print("✓ feeds.user_id added")

    # clusters table
    if "clusters" in existing:
        cl_cols = cols("clusters")
        if "latest_article_at" not in cl_cols:
            cur.execute("ALTER TABLE clusters ADD COLUMN latest_article_at DATETIME")
            print("✓ clusters.latest_article_at added")

    # articles table
    if "articles" in existing:
        art_cols = cols("articles")
        if "original_title" not in art_cols:
            cur.execute("ALTER TABLE articles ADD COLUMN original_title VARCHAR(500) DEFAULT ''")
            print("✓ articles.original_title added")

    con.commit()
    con.close()
    print("\nМиграция завершена ✓")
    print("Теперь запустите: ./start_backend.sh")


if __name__ == "__main__":
    migrate()
