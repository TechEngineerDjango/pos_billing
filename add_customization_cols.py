
import sqlite3

DB_FILE = "burger_pos.db"

def add_columns():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    columns = [
        ("card_bg_color", "TEXT", "'#1e293b'"),
        ("sidebar_bg_color", "TEXT", "'#0f172a'"),
        ("accent_color", "TEXT", "'#f97316'"),
        ("border_color", "TEXT", "'rgba(255, 255, 255, 0.1)'")
    ]
    
    for col_name, col_type, default_val in columns:
        try:
            print(f"Adding column {col_name}...")
            cursor.execute(f"ALTER TABLE shops ADD COLUMN {col_name} {col_type} DEFAULT {default_val}")
            print(f"✅ Added {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"ℹ️ Column {col_name} already exists.")
            else:
                print(f"❌ Error adding {col_name}: {e}")
                
    conn.commit()
    conn.close()

if __name__ == "__main__":
    add_columns()
