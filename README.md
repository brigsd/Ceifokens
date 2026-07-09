# Ceifokens

Colheita de conversas do Antigravity para uma base de conhecimento (RAG).

Lê as conversas direto do servidor local do Antigravity e exporta cada uma como
texto limpo, pronto para indexar. Não mexe nas conversas; só lê.

## Como funciona

O Antigravity roda um servidor local (`language_server`) que guarda as conversas.
O Ceifokens acha esse servidor sozinho (porta e token) usando `psutil`, lê os steps
pela API e transforma em Markdown legível.

Requisito: **Antigravity aberto** (o servidor precisa estar rodando).

## Instalação

```
pip install psutil
```

## Uso

```
python ceifokens.py list                      # lista as conversas do workspace ativo
python ceifokens.py export <id>               # exporta uma conversa (basta o começo do id)
python ceifokens.py export all                # exporta todas
python ceifokens.py export all --out D:\rag   # escolhe a pasta de saída
```

Saída, por conversa, numa pasta em `exports/`:

- `conversation.md` — transcrição limpa (suas mensagens, o raciocínio do agente e o
  resumo de cada ação). É o arquivo para fatiar e indexar no RAG.
- `meta.json` — id, título, projeto, datas, número de steps.
- `raw_steps.json` — os steps crus, backup completo.

## Conversão de Conversas (.pb para .db)

Novas conversas no Antigravity são salvas criptografadas em formato `.pb`. O Ceifokens inclui uma ferramenta que descriptografa e converte qualquer conversa ativa para formato aberto `.db` (SQLite), permitindo que você modifique o histórico diretamente na base de dados.

### Como usar:

1. **Abra o Antigravity** e certifique-se de que a conversa que deseja converter esteja visível.
2. Rode o script de conversão passando o ID da conversa (ou selecione da lista rodando sem argumentos):
   ```powershell
   python convert_pb_to_db.py <ID_DA_CONVERSA>
   ```
3. O script baixará o histórico descriptografado em memória e entrará em loop solicitando o encerramento do Antigravity.
4. **Feche o Antigravity**. O script detectará o fechamento de imediato, fará backup do arquivo `.pb` original para `.pb.bak`, e gerará o arquivo `.db` SQLite aberto com integridade de metadados UUID garantida.
5. **Reabra o Antigravity**. A conversa será lida de forma transparente no SQLite.

### Painel Web (Interface Visual)

Se você preferir gerenciar suas conversas de forma visual com cliques, o Ceifokens inclui um **Painel Web premium interativo** (Flask) para gerenciar, converter e injetar contextos:

1. Dê um duplo-clique em `start_painel.bat` (ou rode `python app_web.py`).
2. Abra `http://127.0.0.1:5000` em seu navegador.
3. **Funcionalidades do Painel:**
   - **Lista e busca em tempo real** de todas as suas conversas por ID ou Título.
   - **Indicador de status físico** em tempo real (.pb Criptografado vs .db SQLite Aberto vs Em Memória).
   - **Fluxo de Conversão Assistido**: Puxa os dados com 1 clique e monitora automaticamente o processo do Antigravity para você fechá-lo e aplicar o SQLite de forma segura.
   - **Timeline de Histórico**: Exibe a lista completa de mensagens decifradas para o SQLite aberto.
   - **Injetor Visual de Contexto**: Permite digitar e enviar novos passos do Usuário ou Agente com apenas 1 clique, atualizando a timeline na hora.

---

## Busca (RAG local)

Depois de exportar, indexe e busque por significado:

```
pip install fastembed numpy
python rag.py build                 # indexa exports/ (baixa o modelo na 1a vez, ~0.22 GB)
python rag.py search "sua pergunta" # busca semantica
python rag.py search "..." -k 8
```

Modelo de embedding multilíngue rodando local (sem nuvem, sem torch). O índice fica em
`index/` como numpy puro; para o tamanho de conversas pessoais isso basta e é instantâneo.
Só busca (achar trechos). Gerar respostas em texto a partir dos trechos exigiria também
um LLM local, que não está incluído.

Detalhes técnicos da API interna do Antigravity: ver [COMO_FUNCIONA.md](COMO_FUNCIONA.md).

## Limitações honestas

- Usa a API interna do Antigravity, que não é documentada. **Funciona nesta versão e
  pode precisar de ajuste quando o Antigravity atualizar.**
- Só enxerga as conversas do workspace aberto no momento. Para colher as de outro
  projeto, abra aquele projeto no Antigravity e rode de novo.
- Testado no Windows. A detecção de processo já cobre Linux/Mac (`language_server`),
  mas não foi testada nesses sistemas.

## Arquivos

- `ag_client.py` — conexão e chamadas à API (acha porta/token sozinho).
- `harvest.py` — extração e limpeza dos steps para Markdown.
- `ceifokens.py` — linha de comando.
- `convert_pb_to_db.py` — descriptografa conversas e as converte para SQLite aberto.
