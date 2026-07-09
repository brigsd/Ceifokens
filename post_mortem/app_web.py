"""Servidor Web (Flask) do Sanitizador Ceifokens.

Fornece uma interface web interativa de alta performance para gerenciar,
converter (de .pb criptografado para .db SQLite aberto) e injetar contexto
cirúrgico nas conversas do Antigravity no workspace ativo.
"""
import os
import sqlite3
import uuid
import sys
import psutil
import logging
from flask import Flask, render_template, request, jsonify

from ag_client import Antigravity
from inject_context import (
    build_user_input_step, 
    build_planner_response_step,
    TEMPLATE_METADATA_HEX,
    TEMPLATE_STEP_ID,
    TEMPLATE_TRAJECTORY_ID,
    TEMPLATE_CASCADE_ID,
    append_to_transcript,
    extract_tag_bytes
)

app = Flask(__name__)

# Configurações de Logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CONVO_DIR = r"C:\Users\tiago.marcondes\.gemini\antigravity\conversations"

# Cache em memória para etapas de conversão
conversion_cache = {}

# Blob de metadados padrão extraído de uma conversa funcional no workspace atual (tamanho 525 bytes)
TEMPLATE_BLOB_HEX = (
    "0a380a3466696c653a2f2f2f633a2f55736572732f746961676f2e6d6172636f6e6465732f4465736b746f702f53616e6"
    "974697a61646f721a00120b08c0fe9ad2061084f4967d1a2464303131366237302d623366332d346536312d393231632d"
    "3034323039623934633464343a3466696c653a2f2f2f633a2f55736572732f746961676f2e6d6172636f6e6465732f446"
    "5736b746f702f53616e6974697a61646f727ac002de84d732fefcdc32edf78332d4efd532d1ecbe32e0d4c632e8edba32"
    "9cd1c9329487c832a0bdc432cfd1bd328abdc432f2bac532f6b18f31acc4ae3295e9d9328fb0be32b6bdc432c1c8fd31a"
    "6f5ba32a5c0cb3282c5bc32889cb630a5c5c9308a9fcc329087c832dff7d632e5d4c632c3ecbe3283ffbc3283e5d8329e"
    "d1c932afd9c732cfecbe32add9c732d0ccc63282d0f231e4d5d332ca908232a1f5ba32d5d6cc32e284d732a1bdc43293e"
    "3f431b886c732edbabf32b3c4ae32f3a9d332fdbcc432f7d4c632bbbdc432f090b332d4c1ce32b19dc932d5b3ca3293bc"
    "c832bab28332e2f78332889fcc328987c832f0b18f3191efcb32d7edb0328bbdc432f290b33294abbf32d4edb032ecb5d"
    "432bfb28332cd908232cdd1bd32ad9dc932cfddd632eaedba32d4f9b632a5bad432aeefcb32d3d2b832ecd6cc32fbcff2"
    "3192012430336230396263302d333337352d343933312d393834322d383734616334676333623436"
)
TEMPLATE_BLOB = bytes.fromhex(TEMPLATE_BLOB_HEX)
OLD_TRAJECTORY_ID = b"d0116b70-b3f3-4e61-921c-04209b94c4d4"
OLD_CASCADE_ID = b"03b09bc0-3375-4931-9842-874ac47c3b46"


# Parser minimalista de passos de bytes para o .db
from convert_pb_to_db import parse_steps_proto, extract_step_fields


def is_antigravity_running():
    """Retorna se o processo do Antigravity/language_server está ativo no sistema."""
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() in ("language_server", "language_server.exe"):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def get_system_status():
    """Retorna se o Antigravity está ativo ou inativo no sistema."""
    return jsonify({
        "success": True,
        "antigravity_running": is_antigravity_running()
    })


@app.route("/api/conversations", methods=["GET"])
def get_conversations():
    """Lista todas as conversas do workspace ativo e seu status físico no disco."""
    try:
        ag = Antigravity()
        convos = ag.list_conversations()
    except RuntimeError:
        # Se o Antigravity estiver offline, listamos os arquivos .db locais
        logging.warning("Antigravity offline. Carregando conversas do disco...")
        convos = {}
        for f in os.listdir(CONVO_DIR):
            if f.endswith(".db"):
                cid = f[:-3]
                convos[cid] = {"summary": f"Conversa em SQLite ({cid[:8]})", "stepCount": "?"}

    list_out = []
    for cid, info in convos.items():
        pb_exists = os.path.exists(os.path.join(CONVO_DIR, f"{cid}.pb"))
        db_exists = os.path.exists(os.path.join(CONVO_DIR, f"{cid}.db"))
        
        list_out.append({
            "id": cid,
            "title": info.get("summary") or info.get("title") or "(sem titulo)",
            "step_count": info.get("stepCount", "?"),
            "pb_exists": pb_exists,
            "db_exists": db_exists,
            "status": "SQLite Aberto" if db_exists else "Criptografado (.pb)" if pb_exists else "Em Memória"
        })

    # Ordenar por id para manter consistência
    list_out.sort(key=lambda x: x["title"])
    return jsonify({"success": True, "conversations": list_out})


@app.route("/api/conversation/<convo_id>", methods=["GET"])
def get_conversation_content(convo_id):
    """Lê as mensagens (steps) decifradas do SQLite .db para exibir na interface."""
    db_file_path = os.path.join(CONVO_DIR, f"{convo_id}.db")
    if not os.path.exists(db_file_path):
        return jsonify({
            "success": False,
            "message": "Banco SQLite aberto para esta conversa não existe. Converta-a primeiro!"
        })

    steps = []
    try:
        conn = sqlite3.connect(db_file_path)
        cursor = conn.cursor()
        
        # Seleciona as mensagens injetadas ou convertidas
        cursor.execute("SELECT idx, step_type, status, step_payload FROM steps ORDER BY idx ASC")
        rows = cursor.fetchall()
        
        # Puxa o JSON de mensagens da API se o Antigravity estiver aberto para termos os textos limpos
        api_steps = []
        try:
            ag = Antigravity()
            api_steps = ag.get_steps(convo_id)
        except Exception:
            pass

        for idx, step_type, status, step_payload in rows:
            # Tenta pegar o texto claro do JSON da API se o index bater, senão mostra indicador de bytes
            text = "(Mensagem binária em bytes Protobuf)"
            sender = "SISTEMA / INTERNO"
            
            if idx < len(api_steps):
                step_json = api_steps[idx]
                t_str = step_json.get("type", "")
                if "USER_INPUT" in t_str:
                    sender = "USUÁRIO"
                    # junta campos text
                    sender_items = step_json.get("userInput", {}).get("items", [])
                    text = "\n".join([item.get("text", "") for item in sender_items if item.get("text")])
                elif "PLANNER_RESPONSE" in t_str:
                    sender = "AGENTE"
                    text = step_json.get("plannerResponse", {}).get("thinking", "") or "Raciocínio ou Ações"
            else:
                # Se for uma mensagem injetada por nós (fora do limite da API atualizada), podemos tentar extrair o texto limpo de forma nativa!
                if step_type == 14:
                    sender = "USUÁRIO (INJETADO)"
                    # tenta decodificar strings simples de utf-8 dentro do payload
                    text = "Mensagem injetada (reabra o Antigravity para renderizar texto claro da API)"
                elif step_type == 15:
                    sender = "AGENTE (INJETADO)"
                    text = "Resposta injetada (reabra o Antigravity para renderizar texto claro da API)"

            steps.append({
                "idx": idx,
                "type": step_type,
                "sender": sender,
                "text": text,
                "status": "Concluído" if status == 3 else "Em Progresso"
            })
            
        conn.close()
        return jsonify({"success": True, "steps": steps})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro ao ler banco SQLite: {e}"})


@app.route("/api/convert/start/<convo_id>", methods=["POST"])
def start_conversion(convo_id):
    """Passo 1 da conversão: Recupera e descriptografa os steps via API enquanto o Antigravity está aberto."""
    try:
        ag = Antigravity()
        raw_proto = ag.get_steps_proto(convo_id)
        raw_steps = parse_steps_proto(raw_proto)
        
        # Salva em cache para o próximo passo
        conversion_cache[convo_id] = raw_steps
        
        return jsonify({
            "success": True,
            "message": f"{len(raw_steps)} mensagens capturadas com sucesso! Prossiga fechando o Antigravity."
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Falha ao capturar mensagens via API: {e}"})


@app.route("/api/convert/finalize/<convo_id>", methods=["POST"])
def finalize_conversion(convo_id):
    """Passo 2 da conversão: Cria o .db e popula tudo com metadados cirúrgicos e consistentes."""
    raw_steps = conversion_cache.get(convo_id)
    if not raw_steps:
        return jsonify({"success": False, "message": "Nenhum histórico em memória cache para conversão."})

    if is_antigravity_running():
        return jsonify({
            "success": False,
            "message": "O Antigravity ainda está aberto! Feche o programa antes de finalizar."
        })

    pb_file_path = os.path.join(CONVO_DIR, f"{convo_id}.pb")
    db_file_path = os.path.join(CONVO_DIR, f"{convo_id}.db")

    # Criando o backup do .pb original
    if os.path.exists(pb_file_path):
        bak_path = pb_file_path + ".bak"
        try:
            if os.path.exists(bak_path):
                os.remove(bak_path)
            os.rename(pb_file_path, bak_path)
        except Exception as e:
            return jsonify({"success": False, "message": f"Erro de backup: {e}"})

    try:
        conn = sqlite3.connect(db_file_path)
        cursor = conn.cursor()

        # Cria as tabelas do Antigravity
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trajectory_meta (
            trajectory_id TEXT PRIMARY KEY,
            cascade_id TEXT,
            trajectory_type INTEGER,
            source INTEGER
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS steps (
            idx INTEGER PRIMARY KEY,
            step_type INTEGER NOT NULL DEFAULT 0,
            status INTEGER NOT NULL DEFAULT 0,
            has_subtrajectory numeric NOT NULL DEFAULT 'false',
            metadata BLOB,
            error_details BLOB,
            permissions BLOB,
            task_details BLOB,
            render_info BLOB,
            step_payload BLOB,
            step_format INTEGER NOT NULL DEFAULT 0
        )""")

        cursor.execute("CREATE TABLE IF NOT EXISTS gen_metadata (idx INTEGER PRIMARY KEY, data BLOB, size INTEGER NOT NULL DEFAULT 0)")
        cursor.execute("CREATE TABLE IF NOT EXISTS executor_metadata (idx INTEGER PRIMARY KEY, data BLOB)")
        cursor.execute("CREATE TABLE IF NOT EXISTS parent_references (idx INTEGER PRIMARY KEY, data BLOB)")
        cursor.execute("CREATE TABLE IF NOT EXISTS trajectory_metadata_blob (id TEXT PRIMARY KEY DEFAULT 'main', data BLOB)")
        cursor.execute("CREATE TABLE IF NOT EXISTS battle_mode_infos (idx INTEGER PRIMARY KEY, data BLOB)")

        # UUID e montagem do blob de metadados principais para consertar cascade ID mismatch
        new_trajectory_id = str(uuid.uuid4())
        new_trajectory_id_bytes = new_trajectory_id.encode("ascii")
        new_cascade_id_bytes = convo_id.encode("ascii")
        
        rebuilt_metadata_blob = TEMPLATE_BLOB.replace(OLD_TRAJECTORY_ID, new_trajectory_id_bytes).replace(OLD_CASCADE_ID, new_cascade_id_bytes)

        # Grava os registros de trajetória consistentes
        cursor.execute(
            "INSERT OR REPLACE INTO trajectory_meta (trajectory_id, cascade_id, trajectory_type, source) VALUES (?, ?, 4, 1)",
            (new_trajectory_id, convo_id)
        )
        cursor.execute(
            "INSERT OR REPLACE INTO trajectory_metadata_blob (id, data) VALUES ('main', ?)",
            (rebuilt_metadata_blob,)
        )

        # Grava os steps decifrados
        for idx, step_payload in enumerate(raw_steps):
            step_type, status = extract_step_fields(step_payload)
            cursor.execute(
                "INSERT INTO steps (idx, step_type, status, step_payload) VALUES (?, ?, ?, ?)",
                (idx, step_type, status, step_payload)
            )

        conn.commit()
        conn.close()
        
        # Limpa cache
        del conversion_cache[convo_id]
        
        return jsonify({
            "success": True,
            "message": "Conversão finalizada com absoluto sucesso! O SQLite aberto foi gerado."
        })
    except Exception as e:
        # Rollback do backup se der erro crítico
        if os.path.exists(pb_file_path + ".bak") and not os.path.exists(pb_file_path):
            os.rename(pb_file_path + ".bak", pb_file_path)
        return jsonify({"success": False, "message": f"Erro de escrita SQL: {e}"})


@app.route("/api/inject/<convo_id>", methods=["POST"])
def inject_context(convo_id):
    """Injeta cirurgicamente uma mensagem ou resposta fictícia de Usuário ou Agente no SQLite."""
    data = request.json or {}
    text_content = data.get("text", "").strip()
    role = data.get("role", "user").strip().lower()

    if not text_content:
        return jsonify({"success": False, "message": "O conteúdo da mensagem não pode ficar vazio!"})

    if role not in ("user", "assistant"):
        return jsonify({"success": False, "message": "Role deve ser 'user' ou 'assistant'"})

    db_file_path = os.path.join(CONVO_DIR, f"{convo_id}.db")
    if not os.path.exists(db_file_path):
        return jsonify({"success": False, "message": "O arquivo .db da conversa não existe."})

    try:
        conn = sqlite3.connect(db_file_path)
        cursor = conn.cursor()

        # 1. Recuperar trajectory_id e cascade_id legítimos do banco
        cursor.execute("SELECT trajectory_id, cascade_id FROM trajectory_meta LIMIT 1")
        row = cursor.fetchone()
        if not row:
            return jsonify({
                "success": False,
                "message": "A tabela 'trajectory_meta' está vazia neste banco de dados."
            })
        
        current_trajectory_id = row[0].encode("ascii")
        cascade_id = row[1].encode("ascii")

        # 2. Próximo idx
        cursor.execute("SELECT max(idx) FROM steps")
        max_idx = cursor.fetchone()[0]
        next_idx = 0 if max_idx is None else max_idx + 1

        # 3. Gerar um novo UUID único de Step
        new_step_id = str(uuid.uuid4()).encode("ascii")

        # 4. Construir metadados legítimos com UUIDs casados de forma binária
        meta_bytes = bytes.fromhex(TEMPLATE_METADATA_HEX)
        meta_bytes = meta_bytes.replace(TEMPLATE_STEP_ID, new_step_id)
        meta_bytes = meta_bytes.replace(TEMPLATE_TRAJECTORY_ID, current_trajectory_id)
        meta_bytes = meta_bytes.replace(TEMPLATE_CASCADE_ID, cascade_id)

        if role == "user":
            step_type = 14
            # Tenta obter o estado da última mensagem de usuário
            cursor.execute("SELECT step_payload FROM steps WHERE step_type = 14 ORDER BY idx DESC LIMIT 1")
            last_row = cursor.fetchone()
            state_bytes = extract_tag_bytes(last_row[0], 19) if last_row else None
            payload = build_user_input_step(text_content, meta_bytes, state_bytes)
        else:
            step_type = 15
            # Tenta obter o estado da última resposta do agente
            cursor.execute("SELECT step_payload FROM steps WHERE step_type = 15 ORDER BY idx DESC LIMIT 1")
            last_row = cursor.fetchone()
            state_bytes = extract_tag_bytes(last_row[0], 20) if last_row else None
            payload = build_planner_response_step(text_content, meta_bytes, state_bytes)

        cursor.execute(
            "INSERT INTO steps (idx, step_type, status, metadata, step_payload) VALUES (?, ?, 3, ?, ?)",
            (next_idx, step_type, meta_bytes, payload)
        )

        conn.commit()
        conn.close()

        # Sincroniza o log de transcrição JSONL
        append_to_transcript(convo_id, text_content, role)

        return jsonify({
            "success": True,
            "message": f"Mensagem de {role.upper()} injetada com êxito no índice {next_idx}! Lembre-se de reabrir/recarregar a aba do chat no Antigravity para limpar o cache."
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro crítico na escrita SQL: {e}"})


@app.route("/api/check-processes", methods=["GET"])
def check_processes():
    """Verifica se existem processos ativos do Antigravity rodando no Windows."""
    import subprocess
    import csv
    
    active_processes = []
    try:
        output = subprocess.check_output("tasklist /FO CSV /NH", shell=True).decode("cp1252", errors="ignore")
        reader = csv.reader(output.strip().split("\n"))
        for row in reader:
            if not row or len(row) < 5:
                continue
            name = row[0].strip()
            name_lower = name.lower()
            if "antigravity" in name_lower or "language_server" in name_lower:
                active_processes.append({
                    "name": name,
                    "pid": row[1].strip(),
                    "session_name": row[2].strip(),
                    "session_num": row[3].strip(),
                    "memory": row[4].strip()
                })
    except Exception as e:
        pass
    
    return jsonify({
        "success": True,
        "processes": active_processes,
        "has_active": len(active_processes) > 0
    })

def extract_desc_from_payload(step_type, payload):
    """Extrai uma descrição ou prévia legível do payload de acordo com o tipo de passo."""
    if not payload:
        return ""
    import re
    # Encontra trechos de texto legíveis (ASCII/UTF-8 imprimíveis)
    chunks = re.findall(b'[\x20-\x7e\xc0-\xff]{4,300}', payload)
    readable = []
    for c in chunks:
        try:
            s = c.decode("utf-8").strip()
            # Descartar UUIDs conhecidos ou lixo de formatação gRPC
            if len(s) > 4 and not re.match(r'^[a-f0-9\-]{36}$', s):
                readable.append(s)
        except Exception:
            pass
            
    # Formata de acordo com o tipo do passo
    if step_type == 14: # User Input
        user_texts = [s for s in readable if len(s) > 10 and not s.startswith("exa.") and not s.startswith("language_")]
        return user_texts[-1] if user_texts else (readable[0] if readable else "(Mensagem de Usuário)")
    elif step_type == 15: # Planner Response
        thinking_texts = [s for s in readable if len(s) > 15 and not s.startswith("exa.") and "Wait" not in s]
        return thinking_texts[0][:300] + "..." if thinking_texts else "(Resposta do Agente)"
    elif step_type == 21: # Run Command
        cmd = next((s for s in readable if "CommandLine" in s or s.startswith("python") or s.startswith("git") or s.startswith("taskkill")), "Comando")
        return f"Executou comando: {cmd}"
    else:
        return " / ".join(readable[:3]) if readable else f"Passo de Tipo {step_type}"


def ceifar_step_in_db(db_file, step_idx):
    import sqlite3
    import re
    import shutil
    
    # Criar backup antes
    shutil.copy(db_file, db_file + ".bak")
    
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    cursor.execute("SELECT step_payload FROM steps WHERE idx=?", (step_idx,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "Passo não encontrado."
        
    payload = bytearray(row[0])
    
    # Procurar por strings legíveis de grande extensão (mais de 200 bytes)
    matches = list(re.finditer(b'[\x20-\x7e\n\r\t\xc0-\xff]{200,}', payload))
    if not matches:
        conn.close()
        return False, "Nenhum bloco de texto longo (maior que 200 bytes) foi encontrado para ceifar neste passo."
        
    matches.sort(key=lambda m: len(m.group(0)), reverse=True)
    longest_match = matches[0]
    target_bytes = longest_match.group(0)
    start_pos = longest_match.start()
    
    msg = b" [--- CONTEUDO CEIFADO PELO SANITIZADOR CEIFOKENS PARA ECONOMIA DE TOKENS ---] "
    replacement = msg.ljust(len(target_bytes), b" ")
    
    payload[start_pos:start_pos+len(target_bytes)] = replacement
    
    cursor.execute("UPDATE steps SET step_payload=? WHERE idx=?", (bytes(payload), step_idx))
    conn.commit()
    conn.close()
    return True, f"Sucesso: Bloco de {len(target_bytes)} bytes ceifado e reduzido com segurança!"


@app.route("/api/steps/<convo_id>", methods=["GET"])
def get_steps(convo_id):
    """Lista todos os passos de uma conversa."""
    import sqlite3
    db_file_path = os.path.join(CONVO_DIR, f"{convo_id}.db")
    if not os.path.exists(db_file_path):
        return jsonify({"success": False, "message": "Arquivo SQLite da conversa não encontrado."})
        
    steps_list = []
    try:
        conn = sqlite3.connect(db_file_path)
        cursor = conn.cursor()
        cursor.execute("SELECT idx, step_type, length(step_payload), step_payload FROM steps ORDER BY idx ASC")
        for row in cursor.fetchall():
            idx, step_type, size, payload = row
            desc = extract_desc_from_payload(step_type, payload)
            steps_list.append({
                "idx": idx,
                "type": step_type,
                "size": size,
                "desc": desc
            })
        conn.close()
        return jsonify({"success": True, "steps": steps_list})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro ao carregar passos: {e}"})


@app.route("/api/ceifar", methods=["POST"])
def ceifar_step():
    """Realiza a ceifa (truncação) segura de um passo de conversa."""
    data = request.json or {}
    convo_id = data.get("convo_id")
    step_idx = data.get("step_idx")
    
    if not convo_id or step_idx is None:
        return jsonify({"success": False, "message": "Parâmetros 'convo_id' e 'step_idx' obrigatórios."})
        
    db_file_path = os.path.join(CONVO_DIR, f"{convo_id}.db")
    if not os.path.exists(db_file_path):
        return jsonify({"success": False, "message": "Conversa não encontrada."})
        
    success, msg = ceifar_step_in_db(db_file_path, int(step_idx))
    return jsonify({"success": success, "message": msg})


@app.route("/api/kill-processes", methods=["POST"])
def kill_processes():
    """Encerra de forma limpa/forçada os processos do Antigravity rodando no Windows."""
    import subprocess
    
    try:
        subprocess.run("taskkill /F /IM Antigravity.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run("taskkill /F /IM language_server.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"success": True, "message": "Todos os processos do Antigravity foram encerrados com sucesso!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro ao encerrar processos: {e}"})


if __name__ == "__main__":
    print("\nIniciando painel Flask do Sanitizador Ceifokens na porta 5000...")
    print("Acesse em seu navegador: http://127.0.0.1:5000\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
