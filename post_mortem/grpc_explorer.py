import os
import re
import psutil

def find_language_server_path():
    """Localiza o caminho completo do executavel do language_server rodando."""
    names = ("language_server", "language_server.exe")
    for p in psutil.process_iter(["name", "exe"]):
        try:
            if (p.info["name"] or "").lower() in names:
                return p.info["exe"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None

def scan_binary(bin_path):
    """Varre o binario em busca de caminhos de servicos e metodos gRPC/ConnectRPC."""
    if not os.path.exists(bin_path):
        print(f"Erro: Arquivo nao encontrado em '{bin_path}'")
        return
        
    print(f"Varrendo binario: {bin_path}")
    print(f"Tamanho do arquivo: {os.path.getsize(bin_path) / (1024*1024):.2f} MB")
    
    with open(bin_path, "rb") as f:
        data = f.read()
        
    # Padrao 1: Busca por strings no formato de caminhos de metodos ConnectRPC:
    # exa.language_server_pb.LanguageServerService/NomeDoMetodo
    # Usando regex em bytes para performance
    pattern = re.compile(rb'exa\.language_server_pb\.LanguageServerService/([a-zA-Z0-9_]+)')
    matches = pattern.findall(data)
    
    methods = sorted(list(set(m.decode('utf-8', 'ignore') for m in matches)))
    
    output_lines = []
    output_lines.append("=== METODOS gRPC ENCONTRADOS VIA PREFIXO ===")
    if methods:
        for m in methods:
            output_lines.append(f"[METODO] {m}")
    else:
        output_lines.append("Nenhum metodo direto encontrado com o prefixo.")
        
    output_lines.append("\n=== STRINGS RELEVANTES DE MUTACAO ===")
    keywords = [b'Cascade', b'Trajectory', b'Step']
    found_strings = set()
    
    ascii_strings = re.findall(rb'[\x20-\x7e]{10,120}', data)
    for s in ascii_strings:
        string_text = s.decode('utf-8', 'ignore')
        if any(k in s for k in keywords) and ("Service" not in string_text):
            if any(verb in string_text for verb in ["Update", "Delete", "Edit", "Save", "Truncate", "Append", "Clear", "Get", "Set"]):
                found_strings.add(string_text)
                
    relevant_strings = sorted(list(found_strings))
    if relevant_strings:
        for rs in relevant_strings:
            output_lines.append(f"[STRING] {rs}")
            
    # Salva em arquivo de texto
    output_file = "post_mortem/grpc_methods.txt"
    with open(output_file, "w", encoding="utf-8") as out_f:
        out_f.write("\n".join(output_lines))
        
    print(f"\nVarredura concluida com sucesso! Resultado gravado em: {output_file}")
    print(f"Total de metodos mapeados: {len(methods)}")
    print(f"Total de strings de mutacao isoladas: {len(relevant_strings)}")

if __name__ == "__main__":
    path = find_language_server_path()
    if not path:
        # Se nao estiver rodando, tenta um caminho padrao conhecido do Windows
        default_path = os.path.expanduser(r"~\AppData\Local\Programs\Antigravity\resources\bin\language_server.exe")
        if os.path.exists(default_path):
            path = default_path
        else:
            print("Erro: O processo 'language_server.exe' nao esta rodando e nao foi achado no caminho padrao.")
            print("Por favor, abra o Antigravity e tente rodar o script novamente.")
            exit(1)
            
    scan_binary(path)
