import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Set, Dict, Any, List
from contextlib import contextmanager

class Database:
    def __init__(self, db_path: str = "data/state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS uploaded_videos (
                    sequence_key TEXT PRIMARY KEY,
                    sequence_num INTEGER,
                    youtube_id TEXT,
                    youtube_url TEXT,
                    title TEXT,
                    video_type TEXT,
                    has_thumbnail INTEGER DEFAULT 0,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_date TEXT NOT NULL,
                    slot TEXT,
                    sequence_key TEXT,
                    status TEXT NOT NULL,
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_seq_num ON uploaded_videos(sequence_num)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_runs_date_slot ON runs(run_date, slot)")
            conn.commit()

    def get_uploaded_keys(self) -> Set[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT sequence_key FROM uploaded_videos WHERE status = 'uploaded'")
            rows = cursor.fetchall()
            return {row["sequence_key"] for row in rows}

    def is_uploaded(self, sequence_key: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM uploaded_videos WHERE sequence_key = ? AND status = 'uploaded'",
                (sequence_key,)
            )
            return cursor.fetchone() is not None

    def record_upload(
        self,
        sequence_key: str,
        sequence_num: int,
        youtube_id: str,
        youtube_url: str,
        title: str,
        video_type: str,
        has_thumbnail: bool,
        status: str = "uploaded"
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO uploaded_videos (
                    sequence_key, sequence_num, youtube_id, youtube_url,
                    title, video_type, has_thumbnail, uploaded_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sequence_key) DO UPDATE SET
                    sequence_num = excluded.sequence_num,
                    youtube_id = excluded.youtube_id,
                    youtube_url = excluded.youtube_url,
                    title = excluded.title,
                    video_type = excluded.video_type,
                    has_thumbnail = excluded.has_thumbnail,
                    uploaded_at = excluded.uploaded_at,
                    status = excluded.status
            """, (
                sequence_key,
                sequence_num,
                youtube_id,
                youtube_url,
                title,
                video_type,
                1 if has_thumbnail else 0,
                datetime.now(timezone.utc).isoformat(),
                status
            ))
            conn.commit()

    def record_run(
        self,
        run_date: str,
        slot: Optional[str],
        sequence_key: Optional[str],
        status: str,
        message: Optional[str] = None
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO runs (run_date, slot, sequence_key, status, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                run_date,
                slot or "default",
                sequence_key,
                status,
                message,
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()

    def slot_already_ran_today(self, run_date: str, slot: Optional[str]) -> bool:
        if not slot:
            return False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 1 FROM runs
                WHERE run_date = ? AND slot = ? AND status = 'success'
            """, (run_date, slot))
            return cursor.fetchone() is not None

    def get_latest_uploaded_number(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MAX(sequence_num) as max_num FROM uploaded_videos
                WHERE status = 'uploaded'
            """)
            row = cursor.fetchone()
            if row and row["max_num"] is not None:
                return row["max_num"]
            return 0

    def get_all_uploads(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM uploaded_videos ORDER BY sequence_num ASC")
            return [dict(row) for row in cursor.fetchall()]
