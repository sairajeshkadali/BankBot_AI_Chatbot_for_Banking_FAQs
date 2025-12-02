import sqlite3

DB_PATH = "bank.db"

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Create users table if not exists (Double check)
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_number TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    balance INTEGER DEFAULT 0
)
""")

# Insert the 3 users
users = [
    ("100001", "Sai@1234", "Sai Rajesh", "sai@gmail.com", "9666760689", 2500000),
    ("100002", "Suriya@123", "Suriya V", "suriya@gmail.com", "1234567890", 2420000),
    ("100003", "Bhaskar@123", "Bhaskar L", "bhaskar@gmail.com", "9876543210", 300000)
]

c.executemany("""
INSERT OR REPLACE INTO users 
(account_number, password, name, email, phone, balance)
VALUES (?, ?, ?, ?, ?, ?)
""", users)

conn.commit()
conn.close()

print("✅ Users Seeded Successfully: Sai Rajesh, Suriya, Bhaskar")