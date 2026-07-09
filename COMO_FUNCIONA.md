# Como funciona por dentro

Isto documenta a API interna do Antigravity que o Ceifokens usa. Serve para
consertar a ferramenta quando o Antigravity atualizar e algo quebrar.

> Nada disto é oficial nem documentado pelo Google. Foi obtido por observação do
> próprio programa rodando. Pode mudar a cada versão.

## Onde as conversas ficam

- Servidor local: `language_server` (binário em
  `...\Antigravity\resources\bin\language_server.exe`).
- Ele guarda cada conversa em `~/.gemini/antigravity/conversations/`.
  - Conversas antigas: `<id>.db` (SQLite, texto aberto).
  - Conversas novas: `<id>.pb` (protobuf **criptografado** com AES-GCM; a chave é do
    módulo nativo, não dá pra abrir o arquivo direto).
- Como o arquivo pode estar cifrado, o Ceifokens **não lê o arquivo**. Ele pede os
  dados ao servidor pela API, que devolve tudo em claro.

## Como falar com o servidor

- O servidor sobe em `https://127.0.0.1:<porta>` (porta dinâmica).
- Autenticação: header `x-codeium-csrf-token`.
- A porta e o token estão na linha de comando do processo `language_server`
  (`--https_server_port` é 0/dinâmica; `--csrf_token <uuid>`). O Ceifokens acha os
  dois via `psutil` (ver `ag_client.py`).
- Protocolo: ConnectRPC. Chamada = `POST /<Servico>/<Metodo>` com
  `Content-Type: application/json` (ou `application/proto`) e header
  `Connect-Protocol-Version: 1`. Certificado é self-signed: ignore a verificação TLS.
- Serviço: `exa.language_server_pb.LanguageServerService`.

## Métodos Usados e Descobertas Recentes (Julho de 2026)

Originalmente, o Ceifokens interagia apenas com endpoints de leitura. Em 09 de Julho de 2026, realizamos uma varredura binária direta no executável `language_server.exe` mapeando **251 métodos ConnectRPC/gRPC** registrados na classe `exa.language_server_pb.LanguageServerService` (a lista completa está em [grpc_methods.txt](file:///c:/Users/tiago.marcondes/Desktop/Sanitizador/post_mortem/grpc_methods.txt)).

### Principais Métodos Identificados:
- `GetAllCascadeTrajectories {}` → mapa `trajectorySummaries` com id, título, `stepCount`, projeto, datas. Só lista o **workspace aberto**.
- `GetCascadeTrajectorySteps {cascadeId}` → `steps[]`, o conteúdo da conversa. Aceita resposta em `application/proto` (cada step vem como um `CortexStep`).
- `RevertToCascadeStep` → (Mutação Ativa) Sonda para reverter e truncar o histórico de conversas na nuvem do Google até um índice de passo específico.
- `DeleteQueuedUserInputStep` → (Mutação Ativa) Remove passos de entrada de usuário enfileirados no servidor.
- `DeleteCascadeTrajectory` → (Gestão) Exclui uma conversa completa diretamente dos registros locais e de nuvem.

---

## Restrição de Conectividade: Sandbox do Agente vs. Execução do Usuário

Ao integrar e testar chamadas de rede dinâmicas e varreduras de processos (como busca por portas ativas e CSRF tokens):
1. **No Sandbox do Agente (Segundo Plano):** Comandos rodando dentro do terminal do assistente de IA são executados sob restrições severas de privilégios e em contexto não-interativo no Windows. Chamadas ao `psutil.net_connections()` ou consultas WMI de processos de terceiros (`Get-CimInstance`) geram bloqueios (hangs) ou timeouts silenciosos pelo Windows Defender.
2. **Na Máquina do Usuário (Primeiro Plano):** A execução interativa de scripts Python (como `test_grpc_calls.py`) ocorre sob privilégios normais de usuário e é **instantânea e 100% livre de bloqueios**.

Sempre priorize delegar execuções de testes ativos que exijam varredura de sockets do sistema operacional diretamente para o terminal do usuário humano.

---

## Formato de um step (o que o RAG aproveita)

Cada step tem `type`, `status`, `metadata` e um bloco de payload conforme o tipo:

- `CORTEX_STEP_TYPE_USER_INPUT` → `userInput`: suas mensagens (campos `text`).
- `CORTEX_STEP_TYPE_PLANNER_RESPONSE` → `plannerResponse`: resposta do agente.
  - `thinking`: raciocínio (texto).
  - `toolCalls[]`: ações. Cada uma tem `name` e `argumentsJson` (string JSON) com,
    por ex., `ArtifactMetadata.Summary`, `CommandLine`, `TargetFile`, `Query`.
- Outros tipos (code action, run command, etc.) são ações; o Ceifokens os ignora no
  texto limpo, mas eles ficam em `raw_steps.json`.

## Conversão e Edição de Conversas de .pb para .db (SQLite)

O Ceifokens agora inclui a ferramenta `convert_pb_to_db.py`, que permite decifrar e migrar uma conversa no formato criptografado `.pb` para o formato aberto `.db` (SQLite). Isso permite edição direta do histórico no banco de dados.

### Sincronismo de Metadados e Integridade (Evitando "Cascade ID Mismatch")

O executor do Antigravity exige consistência estrita de IDs. Ele valida as seguintes tabelas no banco de dados SQLite:
1. **`trajectory_meta`**: Onde o `cascade_id` (ID da conversa) e o `trajectory_id` (UUID único gerado por execução) são mapeados.
2. **`trajectory_metadata_blob`**: Onde reside um objeto Protobuf complexo (no ID `'main'`) contendo informações do workspace e, crucialmente, os mesmos IDs de `cascade_id` e `trajectory_id`.

Se a tabela `trajectory_metadata_blob` estiver vazia ou com IDs divergentes, o executor retornará o erro:
`[unknown] cascade ID mismatch, executor: , input: <id>`

**A Solução Técnica**: 
Como o Protobuf armazena strings de forma posicional e com tamanhos explícitos, e os UUIDs possuem sempre **comprimento fixo de 36 bytes**, o `convert_pb_to_db.py` utiliza um blob de metadados funcional como template e realiza uma **substituição cirúrgica dos UUIDs antigos pelos novos de forma binária** antes de salvá-lo na tabela `trajectory_metadata_blob`. Isso preserva a sintaxe do Protobuf e elimina 100% os erros de integridade.

### Fluxo de Conversão:
1. **API ConnectRPC**: O script consome o método `GetCascadeTrajectorySteps` com formato `application/proto` para coletar as mensagens (`CortexStep`) em claro da API em segundo plano do Antigravity.
2. **Separação de Steps**: O buffer é lido por um parser Protobuf minimalista de baixo nível escrito em Python, isolando cada blob de step e decifrando dinamicamente suas tags de `step_type` e `status`.
3. **Backup e Substituição**: O script aguarda o usuário fechar o Antigravity (matando o processo `language_server.exe`), renomeia o arquivo `.pb` original para `.pb.bak`, cria o `.db` (SQLite) com todas as tabelas necessárias e popula os metadados reconstruídos cirurgicamente.

## Injeção de Contexto Direta (Escrita no Disco)

O Sanitizador Ceifokens permite injetar mensagens diretamente no histórico do Antigravity 2 enquanto o editor está fechado, escrevendo de forma síncrona no banco de dados SQLite e no log de execução (`transcript.jsonl`).

### Sincronização Síncrona SQLite + JSONL
Para que o histórico seja atualizado visualmente na timeline do chat:
1. **SQLite (`steps`)**: Novas linhas consecutivas com o próximo `idx` sequencial são inseridas com a tabela de metadados binários sincronizada e UUIDs casados. Os tipos de passos suportados são `14` (UserInput) e `15` (PlannerResponse).
2. **Logs (`transcript.jsonl`)**: O Sanitizador realiza um parse sequencial das linhas de logs do Brain para determinar o próximo `step_index` consecutivo, montando o JSON estruturado com wrappers XML de metadados (como `<USER_REQUEST>`).

### Gerenciador de Processos
O painel web do Sanitizador possui um sistema de detecção de processos que varre instâncias de `Antigravity.exe` e `language_server.exe` em execução no Windows. Ele oferece o endpoint `/api/kill-processes` para encerrar forçadamente essas instâncias com `taskkill /F /IM` antes de realizar a injeção física. Isso impede que o cache na memória RAM do processo ativo sobrescreva os arquivos em disco.

## Se quebrar depois de um update

1. Confirme que o processo ainda se chama `language_server` e ainda passa
   `--csrf_token` na linha de comando.
2. Confirme a porta ouvindo (`psutil` / netstat) e teste
   `GetAllCascadeTrajectories`.
3. Se um método mudou de nome/campo, os erros do servidor são descritivos
   (dizem qual campo falta). Ajuste em `ag_client.py` / `harvest.py`.
