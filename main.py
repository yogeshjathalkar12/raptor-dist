from fastapi import FastAPI, HTTPException, Request, Header, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from fastapi import HTTPException, Header
from datetime import datetime, timedelta
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
import secrets
import os
import hmac
import resend
import hashlib
import json
import requests
import asyncio

app = FastAPI(title="Raptor License Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# DATABASE_URL will be set in Render Environment Variables
DATABASE_URL = os.getenv("DATABASE_URL")

# --- EMAIL SETUP ---
resend.api_key = os.getenv("RESEND_API_KEY", "your_test_key_here")

def send_onboarding_email(customer_email: str, customer_name: str, license_key: str):
    """Fires a styled HTML email to the client with their Engine key."""
    
    html_content = f"""
    <div style="font-family: sans-serif; color: #111; max-width: 600px; margin: 0 auto;">
        <h1 style="color: #ff4444; letter-spacing: 2px;">RAPTOR</h1>
        <h2>Welcome to the Engine, {customer_name}.</h2>
        <p>Your enterprise license has been successfully generated and activated on our servers.</p>
        
        <p><strong>Your License Key:</strong></p>
        <div style="background: #f4f4f4; padding: 16px; border-radius: 8px; font-family: monospace; font-size: 18px; letter-spacing: 2px; text-align: center; border: 1px solid #ddd;">
            {license_key}
        </div>
        
        <h3>Next Steps:</h3>
        <ol>
            <li>Download the Raptor Desktop Client: <a href="YOUR_GOOGLE_DRIVE_OR_S3_LINK_HERE">Download Here</a></li>
            <li>Run the application.</li>
            <li>Paste your License Key when prompted to unlock the AI network.</li>
        </ol>
        
        <p>If you need technical support, reply directly to this email.</p>
        <p><em>- The Shoonya Origins Team</em></p>
    </div>
    """
    
    try:
        r = resend.Emails.send({
            "from": "Raptor <onboarding@shoonyaorigins.com>",
            "to": customer_email,
            "subject": "Your Raptor AI License Key",
            "html": html_content
        })
        print(f"📧 Email successfully fired to {customer_email}")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

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

# --- MONETIZATION: LEMON SQUEEZY WEBHOOK ---
LEMON_SECRET = os.getenv("LEMON_SECRET", "your_test_secret_here")

@app.post("/webhook/lemon")
async def lemon_squeezy_webhook(request: Request, x_signature: str = Header(None)):
    """Catches Lemon Squeezy payments and auto-generates DB licenses."""
    
    # 1. Verify the payload is actually from Lemon Squeezy
    raw_payload = await request.body()
    digest = hmac.new(LEMON_SECRET.encode(), raw_payload, hashlib.sha256).hexdigest()
    
    if x_signature is None or not hmac.compare_digest(digest, x_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    
    # 2. Parse the transaction
    data = json.loads(raw_payload)
    event_name = data.get("meta", {}).get("event_name")
    
    # 3. If a subscription or order was created, issue the license
    if event_name in ["order_created", "subscription_created"]:
        customer_email = data["data"]["attributes"]["user_email"]
        customer_name = data["data"]["attributes"]["user_name"]
        
        # Generate the RAPTOR-XXXX-XXXX key
        key = f"RAPTOR-{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}"
        
        # Default to 30 days for a monthly subscription
        expires = datetime.now() + timedelta(days=30)
        
        # Write to PostgreSQL DB
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO licenses (key, client_name, client_email, plan, expires_at, max_activations)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (key, customer_name, customer_email, "pro", expires, 1))
            conn.commit()
            print(f"SUCCESS: License {key} generated for {customer_email}")
            
            # --- THE MAGIC TRIGGER ---
            send_onboarding_email(customer_email, customer_name, key)
            
        except Exception as e:
            conn.rollback()
            print(f"Database error: {e}")
        finally:
            cur.close()
            conn.close()

    return {"status": "success"}

# --- WEBSOCKET FOR REAL-TIME UI ---
active_connections = []

@app.websocket("/ws/call")
async def call_websocket(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            # Keep connection open, wait for the backend to push data
            await asyncio.sleep(1) 
    except WebSocketDisconnect:
        active_connections.remove(websocket)

async def broadcast_to_ui(message_type: str, text: str):
    """Pushes live data instantly to the React frontend."""
    payload = {"type": message_type, "text": text}
    for connection in active_connections:
        await connection.send_json(payload)

@app.get("/proxy/search")
def proxy_serpapi(query: str, x_token: str = Header(None)):
    # 1. THE BOUNCER: Make sure the request is actually coming from your Desktop App
    if x_token != "raptor-internal-lock-2026":
        raise HTTPException(status_code=401, detail="Unauthorized proxy access")

    # 2. THE VAULT: Grab the key from Render
    secret_key = os.environ.get("SERPAPI_KEY") 
    if not secret_key:
        raise HTTPException(status_code=500, detail="API key missing on server")
    
    # 3. THE SAFE SEARCH: Use the params dictionary to handle spaces and symbols
    params = {
        "q": query,
        "api_key": secret_key
    }
    response = requests.get("https://serpapi.com/search.json", params=params)
    
    return response.json()
