import os
import json
import re
import shutil
import logging
from datetime import datetime
import threading
import time
import psutil
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Configure logging
LOG_FILE = "curator.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

CONFIG_PATH = "config.json"

def load_config():
    if not os.path.exists(CONFIG_PATH):
        # Fallback if config is deleted
        return {
            "original_brain_path": "C:\\Users\\tiago.marcondes\\.gemini\\antigravity\\brain",
            "original_convo_path": "C:\\Users\\tiago.marcondes\\.gemini\\antigravity\\conversations",
            "summaries_pb_path": "C:\\Users\\tiago.marcondes\\.gemini\\antigravity\\agyhub_summaries_proto.pb",
            "mirror_path": "C:\\Users\\tiago.marcondes\\Desktop\\Sanitizador\\mirror",
            "process_names": ["antigravity.exe", "gemini.exe", "Gemini.exe", "Antigravity.exe"]
        }
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# Extracted mapping of IDs to names from protobuf
def extract_names(pb_path):
    if not os.path.exists(pb_path):
        logging.warning(f"Summaries PB file not found: {pb_path}")
        return {}
    try:
        with open(pb_path, "rb") as f:
            data = f.read()
        
        uuid_regex = rb'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
        matches = list(re.finditer(uuid_regex, data))
        mappings = {}
        for m in matches:
            uuid_str = m.group(0).decode('utf-8')
            start_idx = m.end()
            # Find b'\x0a' in the next 15 bytes after UUID
            idx_0a = data.find(b'\x0a', start_idx, start_idx + 15)
            if idx_0a != -1 and idx_0a + 1 < len(data):
                str_len = data[idx_0a + 1]
                str_start = idx_0a + 2
                str_end = str_start + str_len
                if str_end <= len(data):
                    title_bytes = data[str_start:str_end]
                    try:
                        title = title_bytes.decode('utf-8')
                        if len(title) > 0 and all(32 <= ord(c) < 127 or ord(c) > 127 for c in title):
                            mappings[uuid_str] = title
                    except:
                        pass
        return mappings
    except Exception as e:
        logging.error(f"Error extracting names from PB file: {e}")
        return {}

# Background thread to sync new/modified files continuously
def run_continuous_sync():
    logging.info("Starting background mirror thread...")
    while True:
        try:
            config = load_config()
            src = config["original_brain_path"]
            dst = config["mirror_path"]
            
            if not os.path.exists(src):
                time.sleep(5)
                continue
                
            if not os.path.exists(dst):
                os.makedirs(dst)
                
            for root, dirs, files in os.walk(src):
                for file in files:
                    src_file = os.path.join(root, file)
                    rel_path = os.path.relpath(src_file, src)
                    dst_file = os.path.join(dst, rel_path)
                    
                    dst_dir = os.path.dirname(dst_file)
                    if not os.path.exists(dst_dir):
                        os.makedirs(dst_dir)
                    
                    # Safe copy: if destination does not exist, or original has newer mtime
                    # and the destination wasn't edited more recently by the user.
                    if not os.path.exists(dst_file):
                        shutil.copy2(src_file, dst_file)
                    else:
                        src_mtime = os.path.getmtime(src_file)
                        dst_mtime = os.path.getmtime(dst_file)
                        if src_mtime > dst_mtime:
                            shutil.copy2(src_file, dst_file)
        except Exception as e:
            pass
        time.sleep(5)

# Run process check
def is_antigravity_running():
    config = load_config()
    target_names = [name.lower() for name in config.get("process_names", [])]
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] and proc.info['name'].lower() in target_names:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

# In-memory storage of loaded steps to match indexes during editing
conversation_steps_cache = {}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/conversations", methods=["GET"])
def get_conversations():
    config = load_config()
    pb_path = config["summaries_pb_path"]
    brain_path = config["original_brain_path"]
    
    mappings = extract_names(pb_path)
    
    # Also find folders in original brain that exist but might not be in the .pb list
    all_convos = []
    if os.path.exists(brain_path):
        for name in os.listdir(brain_path):
            if os.path.isdir(os.path.join(brain_path, name)) and len(name) == 36: # matches UUID structure
                pretty_name = mappings.get(name, f"Conversa Sem Nome ({name[:8]})")
                all_convos.append({
                    "id": name,
                    "name": pretty_name
                })
                
    # Sort conversations (e.g. by last modified of their transcript files)
    def get_mtime(convo):
        convo_id = convo["id"]
        path = os.path.join(brain_path, convo_id, ".system_generated", "logs", "transcript_full.jsonl")
        if os.path.exists(path):
            return os.path.getmtime(path)
        return 0
    all_convos.sort(key=get_mtime, reverse=True)
    
    return jsonify({"success": True, "conversations": all_convos})

@app.route("/api/conversation/<convo_id>", methods=["GET"])
def get_conversation_content(convo_id):
    config = load_config()
    convo_mirror_path = os.path.join(config["mirror_path"], convo_id, ".system_generated", "logs")
    file_path = os.path.join(convo_mirror_path, "transcript_full.jsonl")
    
    # If not in mirror, force copy it from original
    if not os.path.exists(file_path):
        original_convo_path = os.path.join(config["original_brain_path"], convo_id, ".system_generated", "logs")
        original_file_path = os.path.join(original_convo_path, "transcript_full.jsonl")
        if os.path.exists(original_file_path):
            os.makedirs(convo_mirror_path, exist_ok=True)
            shutil.copy2(original_file_path, file_path)
            logging.info(f"Copied {convo_id} logs to mirror folder for editing.")
        else:
            return jsonify({"success": False, "message": "Nenhum histórico encontrado para esta conversa."})

    steps = []
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                if line.strip():
                    try:
                        steps.append(json.loads(line))
                    except Exception as parse_err:
                        logging.error(f"Error parsing json line: {parse_err}")
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro lendo arquivo: {e}"})

    # Cache original steps to reconstruct them correctly when saving
    conversation_steps_cache[convo_id] = steps
    
    # Generate text block representation
    text_parts = []
    for idx, step in enumerate(steps):
        source = step.get('source', '')
        if source == 'MODEL':
            sender_label = "ANTIGRAVITY (ASSISTENTE)"
        elif source in ['USER_EXPLICIT', 'USER_INPUT', 'USER']:
            sender_label = "USUÁRIO"
        else:
            sender_label = source or "SISTEMA"
            
        header = f"🟩🟩 [INÍCIO MENSAGEM | INDEX: {idx} | REMETENTE: {sender_label} | TIPO: {step.get('type', '')}] 🟩🟩"
        text_parts.append(header)
        
        # Thinking/Reasoning
        if "thinking" in step and step["thinking"]:
            text_parts.append("[[[ THINKING ]]]")
            text_parts.append(step["thinking"].strip())
            
        # Content
        text_parts.append("[[[ CONTENT ]]]")
        if step.get("content"):
            text_parts.append(step["content"].strip())
        else:
            text_parts.append("")
            
        footer = f"🟥🟥 [FIM MENSAGEM | INDEX: {idx}] 🟥🟥"
        text_parts.append(footer)
        text_parts.append("") # space between blocks
        
    unified_text = "\n".join(text_parts)
    return jsonify({"success": True, "text": unified_text, "steps": steps})

@app.route("/api/conversation/<convo_id>", methods=["POST"])
def save_conversation_content(convo_id):
    data = request.json
    
    if "steps" in data:
        updated_steps = data["steps"]
    else:
        edited_text = data.get("text", "")
        original_steps = conversation_steps_cache.get(convo_id)
        if not original_steps:
            # reload from original if cache expired
            config = load_config()
            file_path = os.path.join(config["mirror_path"], convo_id, ".system_generated", "logs", "transcript_full.jsonl")
            original_steps = []
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        if line.strip():
                            try:
                                original_steps.append(json.loads(line))
                            except:
                                pass
            if not original_steps:
                return jsonify({"success": False, "message": "Cache original expirou e arquivo não pôde ser lido."})

        # Parse edited text back to steps
        step_pattern = r"🟩🟩 \[INÍCIO MENSAGEM \| INDEX: (\d+) \| REMETENTE: [^|]* \| TIPO: [^\]]*\] 🟩🟩"
        headers = re.findall(step_pattern, edited_text)
        blocks = re.split(step_pattern, edited_text)
        
        if len(headers) != len(blocks) - 1:
            return jsonify({"success": False, "message": "Erro de consistência de blocos! Verifique se nenhum cabeçalho do tipo '🟩🟩 [INÍCIO MENSAGEM | INDEX: X ... ===' foi adulterado."})

        updated_steps = []
        for idx_str, block_text in zip(headers, blocks[1:]):
            orig_idx = int(idx_str)
            if orig_idx < 0 or orig_idx >= len(original_steps):
                continue
            
            orig_step = original_steps[orig_idx].copy()
            
            # Remove the footer matching this index
            block_text = re.sub(r"🟥🟥 \[FIM MENSAGEM \| INDEX: \d+\] 🟥🟥", "", block_text).strip()
            
            # Regex to parse optional THINKING and CONTENT blocks
            thinking_match = re.search(r"\[\[\[ THINKING \]\]\]\s*(.*?)\s*(?=\[\[\[ CONTENT \]\]\]|$)", block_text, re.DOTALL)
            content_match = re.search(r"\[\[\[ CONTENT \]\]\]\s*(.*?)\s*$", block_text, re.DOTALL)
            
            if thinking_match:
                orig_step["thinking"] = thinking_match.group(1).strip()
            else:
                if "thinking" in orig_step:
                    orig_step.pop("thinking")
                    
            if content_match:
                orig_step["content"] = content_match.group(1).strip()
            else:
                orig_step["content"] = ""
                
            updated_steps.append(orig_step)

    # Save to mirror folder first
    config = load_config()
    convo_mirror_path = os.path.join(config["mirror_path"], convo_id, ".system_generated", "logs")
    os.makedirs(convo_mirror_path, exist_ok=True)
    
    full_path = os.path.join(convo_mirror_path, "transcript_full.jsonl")
    norm_path = os.path.join(convo_mirror_path, "transcript.jsonl")
    
    try:
        # Write both full and norm JSONL files
        with open(full_path, "w", encoding="utf-8") as f:
            for s in updated_steps:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        
        # In norm transcript.jsonl, we do exactly the same (to preserve edit exactly)
        with open(norm_path, "w", encoding="utf-8") as f:
            for s in updated_steps:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
                
        # Update cache
        conversation_steps_cache[convo_id] = updated_steps
        logging.info(f"Successfully saved edits to mirror log for {convo_id}.")
        return jsonify({"success": True, "message": "Edição gravada no espelho com sucesso!"})
    except Exception as e:
        logging.error(f"Failed to write edited steps to mirror: {e}")
        return jsonify({"success": False, "message": f"Erro de escrita: {e}"})

@app.route("/api/sync", methods=["POST"])
def sync_mirror_to_original():
    # 1. Check if Gemini/Antigravity is running
    if is_antigravity_running():
        logging.warning("Sync block: Antigravity is currently running.")
        return jsonify({
            "success": False, 
            "message": "Operação recusada! O processo do Antigravity/Gemini está ativo. Feche o programa antes de sincronizar as alterações."
        })
        
    config = load_config()
    brain_orig = config["original_brain_path"]
    convo_orig = config["original_convo_path"]
    pb_orig = config["summaries_pb_path"]
    
    mirror = config["mirror_path"]
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parent_dir = os.path.dirname(brain_orig)
    
    # 2. Rename original directories as rollback backups
    brain_old = os.path.join(parent_dir, f"brain_old_{timestamp}")
    convo_old = os.path.join(parent_dir, f"conversations_old_{timestamp}")
    pb_old = pb_orig + f"_old_{timestamp}"
    
    try:
        logging.info("Starting safe synchronization...")
        
        # Backup brain
        if os.path.exists(brain_orig):
            shutil.copytree(brain_orig, brain_old)
            logging.info(f"Created rollback backup: {brain_old}")
            
        # Backup conversations DBs
        if os.path.exists(convo_orig):
            shutil.copytree(convo_orig, convo_old)
            logging.info(f"Created rollback backup: {convo_old}")
            
        # Backup summaries .pb
        if os.path.exists(pb_orig):
            shutil.copy2(pb_orig, pb_old)
            logging.info(f"Created rollback backup: {pb_old}")
            
        # 3. Synchronize Mirror over original brain directory
        # We delete original files that do not exist in mirror to support removals!
        for root, dirs, files in os.walk(mirror):
            for file in files:
                mirror_file = os.path.join(root, file)
                rel_path = os.path.relpath(mirror_file, mirror)
                orig_file = os.path.join(brain_orig, rel_path)
                
                orig_dir = os.path.dirname(orig_file)
                os.makedirs(orig_dir, exist_ok=True)
                
                shutil.copy2(mirror_file, orig_file)
                
        # Propagate deletions (delete original files if deleted in mirror)
        for root, dirs, files in os.walk(brain_orig):
            for file in files:
                orig_file = os.path.join(root, file)
                rel_path = os.path.relpath(orig_file, brain_orig)
                mirror_file = os.path.join(mirror, rel_path)
                
                if not os.path.exists(mirror_file):
                    os.remove(orig_file)
                    logging.info(f"Deleted original file matching mirror removal: {rel_path}")

        # 4. Clear matching conversation SQLite databases so the app is forced
        # to reload history seamlessly from the fresh JSONL log files!
        # (This avoids SQL conflicts completely)
        for convo_id in os.listdir(mirror):
            if len(convo_id) == 36:
                db_file = os.path.join(convo_orig, f"{convo_id}.db")
                if os.path.exists(db_file):
                    os.remove(db_file)
                    logging.info(f"Cleared cache SQLite DB for {convo_id} to force refresh on load.")
                # also clear WAL / SHM files if any
                for ext in [".db-shm", ".db-wal"]:
                    extra_file = os.path.join(convo_orig, f"{convo_id}{ext}")
                    if os.path.exists(extra_file):
                        os.remove(extra_file)

        logging.info("Synchronization completed successfully!")
        return jsonify({
            "success": True, 
            "message": "Sincronização aplicada com sucesso! Os logs antigos foram rotacionados em pastas 'old' e a base do Antigravity foi higienizada."
        })
        
    except Exception as e:
        logging.error(f"Error during synchronization: {e}")
        return jsonify({"success": False, "message": f"Erro crítico na sincronização: {e}. Use o Desfazer para restaurar o estado."})

@app.route("/api/undo", methods=["POST"])
def undo_latest_sync():
    config = load_config()
    brain_orig = config["original_brain_path"]
    convo_orig = config["original_convo_path"]
    pb_orig = config["summaries_pb_path"]
    
    parent_dir = os.path.dirname(brain_orig)
    
    # Locate latest brain_old, conversations_old and summaries pb backup
    all_backups = []
    try:
        for name in os.listdir(parent_dir):
            if name.startswith("brain_old_") and os.path.isdir(os.path.join(parent_dir, name)):
                timestamp = name.replace("brain_old_", "")
                all_backups.append(timestamp)
    except Exception as e:
        return jsonify({"success": False, "message": f"Falha listando backups: {e}"})
        
    if not all_backups:
        return jsonify({"success": False, "message": "Nenhum backup de sincronização encontrado."})
        
    # Get newest timestamp
    latest_ts = max(all_backups)
    
    brain_old = os.path.join(parent_dir, f"brain_old_{latest_ts}")
    convo_old = os.path.join(parent_dir, f"conversations_old_{latest_ts}")
    pb_old = pb_orig + f"_old_{latest_ts}"
    
    try:
        logging.info(f"Undoing latest sync: restoring backups from timestamp {latest_ts}")
        
        # Restore brain
        if os.path.exists(brain_orig):
            shutil.rmtree(brain_orig)
        if os.path.exists(brain_old):
            shutil.copytree(brain_old, brain_orig)
            shutil.rmtree(brain_old)
            
        # Restore conversations
        if os.path.exists(convo_orig):
            shutil.rmtree(convo_orig)
        if os.path.exists(convo_old):
            shutil.copytree(convo_old, convo_orig)
            shutil.rmtree(convo_old)
            
        # Restore summaries pb
        if os.path.exists(pb_orig):
            os.remove(pb_orig)
        if os.path.exists(pb_old):
            shutil.copy2(pb_old, pb_orig)
            os.remove(pb_old)
            
        logging.info("Rollback undo completed successfully!")
        return jsonify({"success": True, "message": f"Restauração concluída! Voltamos ao estado do backup de {latest_ts}."})
    except Exception as e:
        logging.error(f"Error during rollback: {e}")
        return jsonify({"success": False, "message": f"Erro restaurando backup: {e}"})

if __name__ == "__main__":
    # Start continuous background mirroring thread
    sync_thread = threading.Thread(target=run_continuous_sync, daemon=True)
    sync_thread.start()
    
    logging.info("Starting Web Sanitizer UI Server on http://127.0.0.1:8000")
    app.run(port=8000, debug=False)
