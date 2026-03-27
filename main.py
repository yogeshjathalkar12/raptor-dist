from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
import secrets
import os

app = FastAPI(title="Raptor License Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# DATABASE_URL will be set in Render Environment Variables
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            key TEXT PRIMARY KEY,
            client_name TEXT,
            client_email TEXT,
            plan TEXT DEFAULT 'basic',
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMPTZ,
            is_active INTEGER DEFAULT 1,
            activations INTEGER DEFAULT 0,
            max_activations INTEGER DEFAULT 1,
            last_seen TIMESTAMPTZ
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

# Initialize DB on Startup
@app.on_event("startup")
async def startup_event():
    init_db()

# ── Models ─────────────────────────────────────────────────────
class CreateLicenseRequest(BaseModel):
    client_name: str
    client_email: str
    plan: str = "basic"
    duration_days: int = 30
    max_activations: int = 1

class ValidateRequest(BaseModel):
    key: str
    machine_id: str = ""

ADMIN_TOKEN = os.getenv("RAPTOR_ADMIN_TOKEN", "shoonya-admin-2026")

# ── Endpoints ──────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "Raptor License Server Online", "version": "1.1.0"}

@app.post("/license/create")
def create_license(req: CreateLicenseRequest, admin_token: str = ""):
    if admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")
    
    key = f"RAPTOR-{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}"
    expires = datetime.now() + timedelta(days=req.duration_days)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO licenses (key, client_name, client_email, plan, expires_at, max_activations)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (key, req.client_name, req.client_email, req.plan, expires, req.max_activations))
    conn.commit()
    cur.close()
    conn.close()
    return {"key": key, "expires_at": expires.isoformat()}

@app.post("/license/validate")
def validate_license(req: ValidateRequest):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM licenses WHERE key = %s", (req.key,))
    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="License key not found")

    if not row["is_active"] or datetime.now(row["expires_at"].tzinfo) > row["expires_at"]:
        cur.close()
        conn.close()
        raise HTTPException(status_code=403, detail="License inactive or expired")

    cur.execute("UPDATE licenses SET last_seen = %s, activations = activations + 1 WHERE key = %s", 
                (datetime.now(), req.key))
    conn.commit()
    cur.close()
    conn.close()

    return {"valid": True, "client_name": row["client_name"], "plan": row["plan"]}