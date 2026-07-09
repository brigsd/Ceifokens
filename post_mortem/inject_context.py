"""Script para injeção de contexto/mensagens artificiais no histórico do Antigravity.

Uso:
  python inject_context.py <ID_OU_TITULO_CONVERSA> "<TEXTO_DA_MENSAGEM>" [--role user|assistant]

Este script grava um novo step (CortexStep) diretamente no SQLite aberto da conversa,
codificando o payload protobuf de forma nativa e cirúrgica.
Suporta busca inteligente por correspondência parcial no título da conversa.
"""
import os
import sqlite3
import sys
import uuid

from ag_client import Antigravity

CONVO_DIR = r"C:\Users\tiago.marcondes\.gemini\antigravity\conversations"

# Template hexadecimal de metadados legítimos de 158 bytes coletado do Antigravity
TEMPLATE_METADATA_HEX = (
    "0a0c089cfcbdd20610dcb19cba031804622434313833393766322d326664622d343761382d39353734"
    "2d346132633437663762373336a201500a2463356432393834662d333135642d346463342d61663630"
    "2d31353038373839343939626410071801222436366630373437612d333730372d343739632d613237"
    "642d343536626566623939333561d201120a100803120c089cfcbdd20610fcf2acbc03"
)

# UUIDs contidos no template original para substituição exata de bytes
TEMPLATE_STEP_ID = b"418397f2-2fdb-47a8-9574-4a2c47f7b736"
TEMPLATE_TRAJECTORY_ID = b"c5d2984f-315d-4dc4-af60-1508789499bd"
TEMPLATE_CASCADE_ID = b"66f0747a-3707-479c-a27d-456befb9935a"



def encode_varint(value):
    """Codifica um número inteiro no padrão Varint do Protobuf."""
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def encode_length_delimited(tag, data):
    """Codifica um campo do tipo length-delimited (tag, tamanho, bytes)."""
    tag_bytes = encode_varint((tag << 3) | 2)
    return tag_bytes + encode_varint(len(data)) + data


def extract_tag_bytes(payload, target_tag):
    """Extrai os bytes de um campo do tipo length-delimited (wire type 2) específico no payload Protobuf."""
    if not payload:
        return None
    i = 0
    length_payload = len(payload)
    while i < length_payload:
        try:
            # 1. Decodificar a chave (tag e wire type)
            val = 0
            shift = 0
            while True:
                b = payload[i]
                val |= (b & 0x7F) << shift
                i += 1
                if not (b & 0x80):
                    break
                shift += 7
            tag = val >> 3
            wire = val & 7
            
            # 2. Pular campos baseados no wire type
            if wire == 0:  # Varint
                while True:
                    b = payload[i]
                    i += 1
                    if not (b & 0x80):
                        break
            elif wire == 1:  # 64-bit
                i += 8
            elif wire == 2:  # Length-delimited
                l_val = 0
                shift = 0
                while True:
                    b = payload[i]
                    l_val |= (b & 0x7F) << shift
                    i += 1
                    if not (b & 0x80):
                        break
                    shift += 7
                chunk = payload[i:i+l_val]
                i += l_val
                if tag == target_tag:
                    return chunk
            elif wire == 5:  # 32-bit
                i += 4
            else:
                break
        except Exception:
            break
    return None


def build_user_input_step(text, meta_bytes=None, state_bytes=None):
    """Reconstrói um CortexStep do tipo CORTEX_STEP_TYPE_USER_INPUT (14) com estado clonado."""
    # UserInputItem (tag 1: text)
    item_bytes = encode_length_delimited(1, text.encode("utf-8"))
    
    # UserInput (tag 1: repeated UserInputItem, tag 2: user_response)
    user_input_bytes = encode_length_delimited(1, item_bytes) + encode_length_delimited(2, text.encode("utf-8"))
    
    # CortexStep:
    # tag 1: step_type (Varint) = 14 (USER_INPUT). Bytes: 08 0E
    # tag 4: status (Varint) = 3 (DONE). Bytes: 20 03
    # tag 5: metadata (length-delimited) = meta_bytes (opcional)
    # tag 8: user_input (length-delimited) = user_input_bytes
    # tag 19: workspace_state (length-delimited) = state_bytes (opcional)
    payload = b"\x08\x0e\x20\x03"
    if meta_bytes:
        payload += encode_length_delimited(5, meta_bytes)
    payload += encode_length_delimited(8, user_input_bytes)
    if state_bytes:
        payload += encode_length_delimited(19, state_bytes)
    return payload


def build_planner_response_step(text, meta_bytes=None, state_bytes=None):
    """Reconstrói um CortexStep do tipo CORTEX_STEP_TYPE_PLANNER_RESPONSE (15) com estado clonado."""
    # PlannerResponse (tag 1: thinking)
    planner_response_bytes = encode_length_delimited(1, text.encode("utf-8"))
    
    # CortexStep:
    # tag 1: step_type (Varint) = 15 (PLANNER_RESPONSE). Bytes: 08 0F
    # tag 4: status (Varint) = 3 (DONE). Bytes: 20 03
    # tag 5: metadata (length-delimited) = meta_bytes (opcional)
    # tag 9: planner_response (length-delimited) = planner_response_bytes
    # tag 20: planner_state (length-delimited) = state_bytes (opcional)
    payload = b"\x08\x0f\x20\x03"
    if meta_bytes:
        payload += encode_length_delimited(5, meta_bytes)
    payload += encode_length_delimited(9, planner_response_bytes)
    if state_bytes:
        payload += encode_length_delimited(20, state_bytes)
    return payload


def find_conversation(search_term):
    """Busca inteligente de conversa por ID ou correspondência de título (summary) na API."""
    try:
        ag = Antigravity()
        conversations = ag.list_conversations()
    except RuntimeError:
        # Fallback offline se o Antigravity estiver fechado
        return None, "offline"

    if not conversations:
        return None, "empty"

    search_lower = search_term.lower()
    matches = []

    # 1. Busca direta por ID ou prefixo de ID
    for cid, info in conversations.items():
        if cid.lower().startswith(search_lower):
            matches.append((cid, info))

    # 2. Se não achou por ID, busca por substring no título (summary)
    if not matches:
        for cid, info in conversations.items():
            summary = (info.get("summary") or "").lower()
            if search_lower in summary:
                matches.append((cid, info))

    return matches, "online"


def append_to_transcript(convo_id, text_content, role):
    """Sincroniza a mensagem injetada escrevendo nos arquivos de log JSONL (transcript.jsonl e transcript_full.jsonl)."""
    import os
    import json
    from datetime import datetime

    brain_dir = r"C:\Users\tiago.marcondes\.gemini\antigravity\brain"
    logs_dir = os.path.join(brain_dir, convo_id, ".system_generated", "logs")
    
    # Se a pasta de logs não existir, não há como sincronizar
    if not os.path.exists(logs_dir):
        print(f"Diretório de logs não localizado: {logs_dir}")
        return False

    transcript_paths = [
        os.path.join(logs_dir, "transcript.jsonl"),
        os.path.join(logs_dir, "transcript_full.jsonl")
    ]

    # 1. Determinar o próximo step_index lendo o transcript.jsonl existente
    next_step_index = 0
    main_transcript_path = transcript_paths[0]
    if os.path.exists(main_transcript_path):
        try:
            with open(main_transcript_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1].strip()
                    if last_line:
                        last_data = json.loads(last_line)
                        next_step_index = last_data.get("step_index", 0) + 1
        except Exception as e:
            print(f"Erro ao ler o último step_index do transcript: {e}")

    # 2. Gerar timestamp no formato UTC correto
    timestamp_utc = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # 3. Formatar o JSON do Step
    if role == "user":
        content_str = f"<USER_REQUEST>\n{text_content}\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nThe current local time is: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}-03:00.\n</ADDITIONAL_METADATA>"
        step_data = {
            "step_index": next_step_index,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": timestamp_utc,
            "content": content_str
        }
    else:
        step_data = {
            "step_index": next_step_index,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": timestamp_utc,
            "content": text_content
        }

    # 4. Gravar nos dois arquivos
    json_str = json.dumps(step_data, ensure_ascii=False)
    for path in transcript_paths:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json_str + "\n")
            print(f"Sincronizado JSONL com sucesso em: {path}")
        except Exception as e:
            print(f"Erro ao escrever em {path}: {e}")
            return False

    return True


def main():
    if len(sys.argv) < 3:
        print("Uso:")
        print("  python inject_context.py <ID_OU_TITULO_CONVERSA> \"<TEXTO>\" [--role user|assistant]")
        sys.exit(1)

    search_query = sys.argv[1].strip()
    text_content = sys.argv[2].strip()
    
    role = "user"
    if "--role" in sys.argv:
        try:
            role_idx = sys.argv.index("--role")
            role = sys.argv[role_idx + 1].strip().lower()
        except IndexError:
            pass

    if role not in ("user", "assistant"):
        print("Erro: --role deve ser 'user' ou 'assistant'")
        sys.exit(1)

    # Executar busca flexível
    matches, status = find_conversation(search_query)
    target_cid = None

    if status == "offline":
        # No modo offline, tentamos encontrar diretamente pelo prefixo do ID no nome do arquivo físico
        print("Aviso: Antigravity fechado. Buscando correspondência de ID diretamente no disco...")
        for f in os.listdir(CONVO_DIR):
            if f.lower().startswith(search_query.lower()) and f.endswith(".db"):
                target_cid = f[:-3]
                break
        if not target_cid:
            print(f"Erro: Não foi possível localizar arquivo .db correspondente ao ID '{search_query}' offline.")
            sys.exit(1)
    elif status == "empty" or not matches:
        print(f"Erro: Nenhuma conversa ativa corresponde a '{search_query}' (ID ou Título) no seu workspace.")
        sys.exit(1)
    elif len(matches) == 1:
        target_cid, info = matches[0]
        print(f"Conversa selecionada: {target_cid[:8]} — \"{info.get('summary')}\"")
    else:
        print(f"\nMúltiplas conversas encontradas para '{search_query}':")
        for i, (cid, info) in enumerate(matches, 1):
            print(f"  [{i}] {cid[:8]} — \"{info.get('summary') or '(sem titulo)'}\"")
        try:
            sel = int(input("\nDigite o número correspondente: ").strip())
            if sel < 1 or sel > len(matches):
                raise ValueError()
            target_cid, _ = matches[sel - 1]
        except (ValueError, KeyboardInterrupt):
            print("Seleção cancelada.")
            sys.exit(0)

    db_file_path = os.path.join(CONVO_DIR, f"{target_cid}.db")

    if not os.path.exists(db_file_path):
        print(f"Erro: O banco SQLite '{target_cid[:8]}.db' não existe em '{CONVO_DIR}'.")
        print("Converta a conversa primeiro rodando: python convert_pb_to_db.py")
        sys.exit(1)

    try:
        conn = sqlite3.connect(db_file_path)
        cursor = conn.cursor()

        # 1. Obter trajectory_id e cascade_id legítimos do banco
        cursor.execute("SELECT trajectory_id, cascade_id FROM trajectory_meta LIMIT 1")
        row = cursor.fetchone()
        if not row:
            print("Erro: A tabela 'trajectory_meta' está vazia neste banco. Execute o convert_pb_to_db primeiro!")
            sys.exit(1)
        
        current_trajectory_id = row[0].encode("ascii")
        cascade_id = row[1].encode("ascii")

        # 2. Obter o próximo idx disponível
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
            role_desc = "Usuário"
            # Tenta obter o estado da última mensagem de usuário
            cursor.execute("SELECT step_payload FROM steps WHERE step_type = 14 ORDER BY idx DESC LIMIT 1")
            last_row = cursor.fetchone()
            state_bytes = extract_tag_bytes(last_row[0], 19) if last_row else None
            payload = build_user_input_step(text_content, meta_bytes, state_bytes)
        else:
            step_type = 15
            role_desc = "Agente"
            # Tenta obter o estado da última resposta do agente
            cursor.execute("SELECT step_payload FROM steps WHERE step_type = 15 ORDER BY idx DESC LIMIT 1")
            last_row = cursor.fetchone()
            state_bytes = extract_tag_bytes(last_row[0], 20) if last_row else None
            payload = build_planner_response_step(text_content, meta_bytes, state_bytes)

        print(f"Injetando step no índice {next_idx} ({role_desc})...")
        
        cursor.execute(
            "INSERT INTO steps (idx, step_type, status, metadata, step_payload) VALUES (?, ?, 3, ?, ?)",
            (next_idx, step_type, meta_bytes, payload)
        )

        conn.commit()
        conn.close()
        
        # Sincroniza o log de transcrição JSONL
        append_to_transcript(target_cid, text_content, role)
        
        print("\n[SUCESSO] Contexto injetado com êxito!")
        print("IMPORTANTE: Para que o Antigravity reconheça o novo contexto na barra de chat,")
        print("certifique-se de fechar e reabrir o Antigravity (ou fechar e reabrir a conversa) para limpar o cache!")
    except Exception as e:
        print(f"Erro crítico ao injetar no SQLite: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
