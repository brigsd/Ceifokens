import sys
import os
import json

# Adiciona o diretorio raiz ao path para importar ag_client
sys.path.append(os.path.abspath(os.path.dirname(__file__) + "/.."))
from ag_client import Antigravity

def test_grpc_exploration():
    try:
        print("Conectando ao Antigravity local...")
        client = Antigravity()
        print(f"Conectado com sucesso na porta: {client.port}")
        
        print("\n--- 1. Listando Conversas Ativas ---")
        convos = client.list_conversations()
        if not convos:
            print("Nenhuma conversa ativa encontrada.")
            return
            
        convo_id = list(convos.keys())[0]
        convo_title = convos[convo_id].get("title", "Sem titulo")
        print(f"Selecionada conversa de teste: '{convo_title}' (ID: {convo_id})")
        
        print("\n--- 2. Consultando Passos da Conversa ---")
        steps = client.get_steps(convo_id)
        print(f"Total de passos na conversa: {len(steps)}")
        if len(steps) > 1:
            last_step_idx = steps[-1].get("idx", len(steps) - 1)
            print(f"Ultimo Step Index: {last_step_idx}")
        else:
            print("Poucos passos para testar reversao.")
            return
            
        # Vamos testar chamar RevertToCascadeStep para ver o comportamento do servidor
        print("\n--- 3. Testando Endpoint: RevertToCascadeStep ---")
        # Vamos enviar um payload intencionalmente vazio ou incorreto para ver a mensagem de erro da API
        # Isso revela quais sao os campos obrigatorios!
        payloads_to_test = [
            {},
            {"cascadeId": convo_id},
            {"cascadeId": convo_id, "stepIndex": last_step_idx - 1},
            {"cascadeId": convo_id, "step_idx": last_step_idx - 1},
            {"cascadeId": convo_id, "stepIdx": last_step_idx - 1},
        ]
        
        for i, payload in enumerate(payloads_to_test):
            print(f"\nTentativa {i+1} com payload: {payload}")
            try:
                # Chama RevertToCascadeStep
                res = client.call("RevertToCascadeStep", payload)
                print(f"  [SUCESSO] Resposta: {res}")
            except Exception as e:
                print(f"  [ERRO] Detalhes do erro: {e}")
                
    except Exception as e:
        print(f"Erro geral: {e}")

if __name__ == "__main__":
    test_grpc_exploration()
