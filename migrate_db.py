
import sqlite3

# Connect to the SQLite database
conn = sqlite3.connect("burger_pos.db")
cursor = conn.cursor()

# Add new columns to 'shops' table
try:
    cursor.execute("ALTER TABLE shops ADD COLUMN price_card_bg TEXT DEFAULT '#1e293b'")
    print("Added price_card_bg")
except Exception as e:
    print(f"Skipped price_card_bg: {e}")

try:
    cursor.execute("ALTER TABLE shops ADD COLUMN header_text_color TEXT DEFAULT '#ffffff'")
    print("Added header_text_color")
except Exception as e:
    print(f"Skipped header_text_color: {e}")

try:
    cursor.execute("ALTER TABLE shops ADD COLUMN cart_bg_color TEXT DEFAULT '#0f172a'")
    print("Added cart_bg_color")
except Exception as e:
    print(f"Skipped cart_bg_color: {e}")

conn.commit()
conn.close()
