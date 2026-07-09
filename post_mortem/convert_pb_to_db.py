"""Script de conversão de conversa do Antigravity: .pb (cifrado) para .db (SQLite aberto).

Uso:
  python convert_pb_to_db.py

Este script busca os dados decifrados via API local do Antigravity ativa,
orienta o fechamento seguro do programa e gera o arquivo SQLite correspondente
com a injeção cirúrgica de metadados de ID para evitar erros de mismatch do executor.
"""
import os
import sqlite3
import uuid
import sys
import time
import psutil

from ag_client import Antigravity

CONVO_DIR = r"C:\Users\tiago.marcondes\.gemini\antigravity\conversations"

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
    "3192012430336230396263302d333337352d343933312d393834322d383734616334376333623436"
)

TEMPLATE_BLOB = bytes.fromhex(TEMPLATE_BLOB_HEX)
OLD_TRAJECTORY_ID = b"d0116b70-b3f3-4e61-921c-04209b94c4d4"
OLD_CASCADE_ID = b"03b09bc0-3375-4931-9842-874ac47c3b46"


def parse_steps_proto(raw_bytes):
    """Decodifica as mensagens CortexStep individuais de uma resposta protobuf do ConnectRPC."""
    steps = []
    idx = 0
    total_len = len(raw_bytes)
    while idx < total_len:
        tag_byte = raw_bytes[idx]
        if tag_byte != 0x0A:
            wire_type = tag_byte & 0x07
            idx += 1
            if wire_type == 0:  # Varint
                while idx < total_len and (raw_bytes[idx] & 0x80):
                    idx += 1
                idx += 1
            elif wire_type == 1:  # 64-bit
                idx += 8
            elif wire_type == 2:  # Length-delimited
                length = 0
                shift = 0
                while idx < total_len:
                    b = raw_bytes[idx]
                    length |= (b & 0x7F) << shift
                    idx += 1
                    if not (b & 0x80):
                        break
                    shift += 7
                idx += length
            elif wire_type == 5:  # 32-bit
                idx += 4
            else:
                break
            continue

        idx += 1
        length = 0
        shift = 0
        while idx < total_len:
            b = raw_bytes[idx]
            length |= (b & 0x7F) << shift
            idx += 1
            if not (b & 0x80):
                break
            shift += 7

        step_bytes = raw_bytes[idx: idx + length]
        steps.append(step_bytes)
        idx += length
    return steps


def extract_step_fields(step_bytes):
    """Lê de forma dinâmica os valores de step_type e status dentro do CortexStep protobuf."""
    step_type = 0
    status = 0
    idx = 0
    total_len = len(step_bytes)
    while idx < total_len:
        tag_byte = step_bytes[idx]
        wire_type = tag_byte & 0x07
        tag_num = tag_byte >> 3
        idx += 1

        if tag_num == 1 and wire_type == 0:  # step_type (Varint)
            val = 0
            shift = 0
            while idx < total_len:
                b = step_bytes[idx]
                val |= (b & 0x7F) << shift
                idx += 1
                if not (b & 0x80):
                    break
                shift += 7
            step_type = val
        elif tag_num == 4 and wire_type == 0:  # status (Varint)
            val = 0
            shift = 0
            while idx < total_len:
                b = step_bytes[idx]
                val |= (b & 0x7F) << shift
                idx += 1
                if not (b & 0x80):
                    break
                shift += 7
            status = val
            break
        else:
            if wire_type == 0:
                while idx < total_len and (step_bytes[idx] & 0x80):
                    idx += 1
                idx += 1
            elif wire_type == 1:
                idx += 8
            elif wire_type == 2:
                length = 0
                shift = 0
                while idx < total_len:
                    b = step_bytes[idx]
                    length |= (b & 0x7F) << shift
                    idx += 1
                    if not (b & 0x80):
                        break
                    shift += 7
                idx += length
            elif wire_type == 5:
                idx += 4
            else:
                break
    return step_type, status


def find_conversation(ag, search_term):
    """Busca inteligente de conversa por ID ou correspondência de título (summary) na API."""
    conversations = ag.list_conversations()
    if not conversations:
        return []

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

    return matches


def main():
    print("=" * 60)
    print(" CONVERSOR ANTIGRAVITY — .pb para .db (SQLite Aberto)")
    print("=" * 60)

    try:
        ag = Antigravity()
    except RuntimeError as e:
        print(f"\nErro: {e}")
        print("Certifique-se de que o Antigravity esteja ABERTO antes de iniciar o script.")
        sys.exit(1)

    # Verifica se o ID foi fornecido via argumento de linha de comando
    target_cid = None
    if len(sys.argv) > 1:
        arg_id = sys.argv[1].strip()
        matches = find_conversation(ag, arg_id)
        
        if not matches:
            print(f"\nErro: ID ou Título de conversa '{arg_id}' não encontrado no workspace ativo.")
            sys.exit(1)
        elif len(matches) == 1:
            target_cid, target_info = matches[0]
            print(f"\nConversa selecionada: {target_cid[:8]} — \"{target_info.get('summary')}\"")
        else:
            print(f"\nMúltiplas conversas encontradas para '{arg_id}':")
            for i, (cid, info) in enumerate(matches, 1):
                print(f"  [{i}] {cid[:8]} — \"{info.get('summary') or '(sem titulo)'}\"")
            try:
                sel = int(input("\nDigite o número correspondente: ").strip())
                if sel < 1 or sel > len(matches):
                    raise ValueError()
                target_cid, target_info = matches[sel - 1]
            except (ValueError, KeyboardInterrupt):
                print("Seleção cancelada.")
                sys.exit(0)
    else:
        conversations = ag.list_conversations()
        if not conversations:
            print("\nNenhuma conversa ativa encontrada no workspace atual.")
            sys.exit(0)

        print(f"\nSelecione a conversa para conversão (encontradas {len(conversations)}):")
        conv_list = sorted(conversations.items(), key=lambda kv: kv[1].get("lastModifiedTime", ""), reverse=True)
        
        for i, (cid, info) in enumerate(conv_list, 1):
            title = info.get("summary") or "(sem titulo)"
            pb_exists = os.path.exists(os.path.join(CONVO_DIR, f"{cid}.pb"))
            db_exists = os.path.exists(os.path.join(CONVO_DIR, f"{cid}.db"))
            
            fmt_label = "[PB Cifrado]" if pb_exists else "[DB SQLite]" if db_exists else "[Apenas em Memória]"
            print(f"  [{i}] {cid[:8]}  {fmt_label:<15}  {info.get('stepCount', '?'):>4} steps  {title[:40]}")

        try:
            sel = int(input("\nDigite o número correspondente: ").strip())
            if sel < 1 or sel > len(conv_list):
                raise ValueError()
        except (ValueError, KeyboardInterrupt):
            print("Seleção cancelada.")
            sys.exit(0)

        target_cid, target_info = conv_list[sel - 1]

    pb_file_path = os.path.join(CONVO_DIR, f"{target_cid}.pb")
    db_file_path = os.path.join(CONVO_DIR, f"{target_cid}.db")

    if not os.path.exists(pb_file_path):
        print(f"\nAviso: O arquivo '{target_cid}.pb' não foi encontrado em '{CONVO_DIR}'.")
        print("Essa conversa já pode ser uma base .db ou não estar salva localmente como .pb.")
        if len(sys.argv) > 1:
            print("Prosseguindo conversão forçada via CLI...")
        else:
            ans = input("Deseja continuar forçando a criação do .db a partir da API? (s/N): ").lower().strip()
            if ans != 's':
                print("Abortado.")
                sys.exit(0)

    print(f"\n[1/3] Buscando histórico decifrado da conversa {target_cid[:8]} via API...")
    try:
        raw_proto = ag.get_steps_proto(target_cid)
        raw_steps = parse_steps_proto(raw_proto)
        print(f"Sucesso! {len(raw_steps)} mensagens (steps) recuperadas e descriptografadas.")
    except Exception as e:
        print(f"Falha ao recuperar steps via API: {e}")
        sys.exit(1)

    print("\n[2/3] SOLICITAÇÃO CRÍTICA: Feche o programa Antigravity/Gemini agora!")
    print("O script precisa que o processo 'language_server' seja encerrado para aplicar a alteração.")
    
    while True:
        p = psutil.process_iter(["name"])
        running = False
        for proc in p:
            if (proc.info["name"] or "").lower() in ("language_server", "language_server.exe"):
                running = True
                break
        if not running:
            print("Encerramento detectado com sucesso!")
            break
        print("Aguardando encerramento do processo Antigravity (pressione Ctrl+C para cancelar)...")
        time.sleep(2.5)

    print("\n[3/3] Iniciando criação do banco SQLite...")
    
    # Backup do .pb anterior
    if os.path.exists(pb_file_path):
        bak_path = pb_file_path + ".bak"
        try:
            # Garante que não sobrou nenhum arquivo .bak antigo
            if os.path.exists(bak_path):
                os.remove(bak_path)
            os.rename(pb_file_path, bak_path)
            print(f"  Backup criado: {os.path.basename(bak_path)}")
        except Exception as e:
            print(f"Erro ao renomear arquivo .pb para .pb.bak: {e}")
            sys.exit(1)

    try:
        conn = sqlite3.connect(db_file_path)
        cursor = conn.cursor()

        # Criando o schema completo compatível com o Antigravity
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

        # Geração do novo trajectory_id único
        new_trajectory_id = str(uuid.uuid4())
        
        # Reconstrói cirurgicamente o blob de metadados para evitar erros de mismatch no ID do executor
        new_trajectory_id_bytes = new_trajectory_id.encode("ascii")
        new_cascade_id_bytes = target_cid.encode("ascii")
        
        rebuilt_metadata_blob = TEMPLATE_BLOB.replace(OLD_TRAJECTORY_ID, new_trajectory_id_bytes).replace(OLD_CASCADE_ID, new_cascade_id_bytes)

        # Inserindo metadados da trajetória
        cursor.execute(
            "INSERT OR REPLACE INTO trajectory_meta (trajectory_id, cascade_id, trajectory_type, source) VALUES (?, ?, 4, 1)",
            (new_trajectory_id, target_cid)
        )

        # Inserindo o blob de metadados principal reconstruído
        cursor.execute(
            "INSERT OR REPLACE INTO trajectory_metadata_blob (id, data) VALUES ('main', ?)",
            (rebuilt_metadata_blob,)
        )

        # Inserindo os steps descriptografados extraídos
        print("  Gravando mensagens e recuperando metadados dinamicamente...")
        for idx, step_payload in enumerate(raw_steps):
            step_type, status = extract_step_fields(step_payload)
            cursor.execute(
                "INSERT INTO steps (idx, step_type, status, step_payload) VALUES (?, ?, ?, ?)",
                (idx, step_type, status, step_payload)
            )

        conn.commit()
        conn.close()
        print(f"\n[SUCESSO] Conversa convertida!")
        print(f"O banco de dados SQLite foi criado em: {db_file_path}")
        print("Agora você pode abrir o Antigravity. A conversa será lida de forma transparente no SQLite!")
    except Exception as e:
        print(f"\nErro crítico de escrita no SQLite: {e}")
        # rollback de renomeação do pb se falhar
        if os.path.exists(pb_file_path + ".bak") and not os.path.exists(pb_file_path):
            os.rename(pb_file_path + ".bak", pb_file_path)
            print("Restauração do arquivo original .pb realizada devido a erro.")
        sys.exit(1)


if __name__ == "__main__":
    main()
