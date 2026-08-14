import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def check():
    try:
        conn = psycopg2.connect(
            host=os.getenv('PG_HOST'),
            port=os.getenv('PG_PORT'),
            user=os.getenv('PG_USER'),
            password=os.getenv('PG_PASSWORD'),
            dbname=os.getenv('PG_DATABASE')
        )
        cur = conn.cursor()
        
        # Get observations columns
        cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'observations' AND table_schema = 'public'")
        print("observations columns:")
        for row in cur.fetchall():
            print(f"  {row[0]} ({row[1]})")
            
        # Get averages columns
        cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'averages' AND table_schema = 'public'")
        print("averages columns:")
        for row in cur.fetchall():
            print(f"  {row[0]} ({row[1]})")
            
        conn.close()
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    check()
