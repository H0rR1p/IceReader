import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

from ...paths import DATA_DIR
from ..learning.repository import initialize_store as initialize_learning_store


ACTIVITY_PATH = DATA_DIR / "learning.sqlite3"
_lock = threading.Lock()
_initialized_path: Path | None = None


def _raw_connection() -> sqlite3.Connection:
    connection=sqlite3.connect(ACTIVITY_PATH,timeout=15)
    connection.row_factory=sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def initialize_store() -> None:
    global _initialized_path
    initialize_learning_store()
    resolved=ACTIVITY_PATH.resolve()
    if _initialized_path == resolved: return
    with _lock:
        if _initialized_path == resolved: return
        connection=_raw_connection()
        try:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS activity_windows(
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,device_id TEXT NOT NULL,session_id TEXT NOT NULL,
                    activity_type TEXT NOT NULL,window_start REAL NOT NULL,window_end REAL NOT NULL,
                    credited_seconds REAL NOT NULL,local_date TEXT NOT NULL,timezone TEXT NOT NULL,
                    cards_reviewed INTEGER NOT NULL DEFAULT 0,sentences_read INTEGER NOT NULL DEFAULT 0,
                    lookup_count INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_activity_user_window ON activity_windows(user_id,window_start,window_end);
                CREATE TABLE IF NOT EXISTS daily_learning_stats(
                    user_id TEXT NOT NULL,local_date TEXT NOT NULL,timezone TEXT NOT NULL,
                    active_seconds REAL NOT NULL DEFAULT 0,reading_seconds REAL NOT NULL DEFAULT 0,
                    review_seconds REAL NOT NULL DEFAULT 0,card_seconds REAL NOT NULL DEFAULT 0,
                    cards_reviewed INTEGER NOT NULL DEFAULT 0,sentences_read INTEGER NOT NULL DEFAULT 0,
                    lookup_count INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id,local_date,timezone)
                );
            """)
            connection.commit(); _initialized_path=resolved
        finally: connection.close()


@contextmanager
def _connect():
    initialize_store(); connection=_raw_connection()
    try: yield connection; connection.commit()
    except Exception: connection.rollback(); raise
    finally: connection.close()


def _overlap_union(rows: list[sqlite3.Row], start: float, end: float) -> float:
    intervals=[]
    for row in rows:
        left=max(start,float(row[0])); right=min(end,float(row[1]))
        if right>left: intervals.append((left,right))
    if not intervals: return 0.0
    intervals.sort(); total=0.0; left,right=intervals[0]
    for next_left,next_right in intervals[1:]:
        if next_left<=right: right=max(right,next_right)
        else: total+=right-left; left,right=next_left,next_right
    return total+right-left


def record_heartbeat(user_id: str,device_id: str,payload: dict) -> dict:
    heartbeat_id=str(payload["id"]); end=float(payload["window_end"]); start=float(payload["window_start"])
    if end<start: start,end=end,start
    start=max(start,end-15.0)
    activity_type=str(payload["activity_type"])
    if activity_type not in {"reading","cards","review","dictionary"}: raise ValueError("invalid_activity_type")
    local_date=str(payload["local_date"]); timezone=str(payload.get("timezone") or "local")[:100]
    counters={key:max(0,min(100,int(payload.get(key,0)))) for key in ("cards_reviewed","sentences_read","lookup_count")}
    with _connect() as connection:
        existing=connection.execute("SELECT credited_seconds FROM activity_windows WHERE id=? AND user_id=?",(heartbeat_id,user_id)).fetchone()
        if existing: return {"credited_seconds":float(existing[0]),"duplicate":True}
        overlaps=connection.execute("SELECT window_start,window_end FROM activity_windows WHERE user_id=? AND window_end>? AND window_start<?",(user_id,start,end)).fetchall()
        credited=max(0.0,min(15.0,end-start)-_overlap_union(overlaps,start,end))
        connection.execute("""INSERT INTO activity_windows(id,user_id,device_id,session_id,activity_type,window_start,window_end,credited_seconds,local_date,timezone,cards_reviewed,sentences_read,lookup_count,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(heartbeat_id,user_id,device_id,payload["session_id"],activity_type,start,end,credited,local_date,timezone,counters["cards_reviewed"],counters["sentences_read"],counters["lookup_count"],time.time()))
        reading=credited if activity_type in {"reading","dictionary"} else 0
        review=credited if activity_type=="review" else 0
        cards=credited if activity_type=="cards" else 0
        connection.execute("""INSERT INTO daily_learning_stats(user_id,local_date,timezone,active_seconds,reading_seconds,review_seconds,card_seconds,cards_reviewed,sentences_read,lookup_count,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,local_date,timezone) DO UPDATE SET
            active_seconds=active_seconds+excluded.active_seconds,reading_seconds=reading_seconds+excluded.reading_seconds,
            review_seconds=review_seconds+excluded.review_seconds,card_seconds=card_seconds+excluded.card_seconds,
            cards_reviewed=cards_reviewed+excluded.cards_reviewed,sentences_read=sentences_read+excluded.sentences_read,
            lookup_count=lookup_count+excluded.lookup_count,updated_at=excluded.updated_at""",
            (user_id,local_date,timezone,credited,reading,review,cards,counters["cards_reviewed"],counters["sentences_read"],counters["lookup_count"],time.time()))
    return {"credited_seconds":credited,"duplicate":False}


def heatmap(user_id: str,date_from: str,date_to: str) -> list[dict]:
    with _connect() as connection:
        rows=connection.execute("""SELECT local_date,SUM(active_seconds) AS active_seconds,SUM(reading_seconds) AS reading_seconds,
            SUM(review_seconds) AS review_seconds,SUM(card_seconds) AS card_seconds,SUM(cards_reviewed) AS cards_reviewed,
            SUM(sentences_read) AS sentences_read,SUM(lookup_count) AS lookup_count FROM daily_learning_stats
            WHERE user_id=? AND local_date BETWEEN ? AND ? GROUP BY local_date ORDER BY local_date""",(user_id,date_from,date_to)).fetchall()
    return [dict(row) for row in rows]


def summary(user_id: str,days: int=7) -> dict:
    today=date.today(); start=(today-timedelta(days=max(1,days)-1)).isoformat()
    rows=heatmap(user_id,start,today.isoformat())
    totals={key:sum(float(row[key] or 0) for row in rows) for key in ("active_seconds","reading_seconds","review_seconds","card_seconds","cards_reviewed","sentences_read","lookup_count")}
    active_dates={row["local_date"] for row in heatmap(user_id,"1970-01-01",today.isoformat()) if row["active_seconds"]>=600}
    streak=0; cursor=today
    while cursor.isoformat() in active_dates: streak+=1; cursor-=timedelta(days=1)
    longest=0; run=0; previous=None
    for value in sorted(active_dates):
        current=date.fromisoformat(value)
        run=run+1 if previous and current-previous==timedelta(days=1) else 1
        longest=max(longest,run); previous=current
    return {**totals,"days":days,"current_streak":streak,"longest_streak":longest}
