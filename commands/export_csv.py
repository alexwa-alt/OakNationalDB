"""Export CSV from the normalized SQLite database.

Exports lessons with programme/unit metadata suitable for Excel.
"""
import sqlite3
from config import config
import pandas as pd


def main(out_path: str = "data/exports/lessons.csv") -> None:
    conn = sqlite3.connect(config.db_path)
    query = """
    SELECT p.title as programme, u.subject, u.year_group, u.title as unit_title, u.slug as unit_slug,
           l.title as lesson_title, l.slug as lesson_slug, l.lesson_number, l.url as lesson_url
    FROM lessons l
    JOIN units u ON l.unit_id = u.id
    JOIN programmes p ON u.programme_id = p.id
    ORDER BY p.id, u.sequence_order, l.lesson_number
    """
    df = pd.read_sql_query(query, conn)
    out = pd.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"Exported {len(df)} rows to {out}")


if __name__ == "__main__":
    main()
