
import sys
import os
sys.path.append(os.getcwd())

from sqlalchemy import create_engine, inspect
from app.core.config import settings

def check_schema():
    print(f"Checking schema on: {settings.SYNC_DATABASE_URL.split('@')[1]}") # hide creds
    engine = create_engine(settings.SYNC_DATABASE_URL)
    insp = inspect(engine)
    columns = insp.get_columns("arc_requests")
    col_names = [c["name"] for c in columns]
    
    required = ["estimated_start_date", "estimated_end_date", "actual_end_date"]
    missing = [r for r in required if r not in col_names]
    
    if missing:
        print(f"FAILED: Missing columns: {missing}")
        sys.exit(1)
    else:
        print("SUCCESS: All columns present.")
        print("Columns found:", col_names)

if __name__ == "__main__":
    check_schema()
