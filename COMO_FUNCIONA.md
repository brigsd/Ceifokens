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

## Métodos usados

- `GetAllCascadeTrajectories {}` → mapa `trajectorySummaries` com id, título,
  `stepCount`, projeto, datas. Só lista o **workspace aberto**.
- `GetCascadeTrajectorySteps {cascadeId}` → `steps[]`, o conteúdo da conversa.
  Aceita resposta em `application/proto` (cada step vem como um `CortexStep`).

## Formato de um step (o que o RAG aproveita)

Cada step tem `type`, `status`, `metadata` e um bloco de payload conforme o tipo:

- `CORTEX_STEP_TYPE_USER_INPUT` → `userInput`: suas mensagens (campos `text`).
- `CORTEX_STEP_TYPE_PLANNER_RESPONSE` → `plannerResponse`: resposta do agente.
  - `thinking`: raciocínio (texto).
  - `toolCalls[]`: ações. Cada uma tem `name` e `argumentsJson` (string JSON) com,
    por ex., `ArtifactMetadata.Summary`, `CommandLine`, `TargetFile`, `Query`.
- Outros tipos (code action, run command, etc.) são ações; o Ceifokens os ignora no
  texto limpo, mas eles ficam em `raw_steps.json`.

## Corte de conversa via .db (experimental, NÃO no Ceifokens)

Descoberto mas deixado de fora por ser frágil: dá para trocar o `<id>.pb` cifrado por
um `<id>.db` (SQLite) montado com menos steps, e o servidor lê o `.db` no lugar do
`.pb`. Os blobs de `step_payload` do `.db` são exatamente os `CortexStep` que a API
devolve em `application/proto`. Só fazer com o Antigravity fechado e com backup do
`.pb`. Isto depende muito da versão e pode parar de funcionar.

## Se quebrar depois de um update

1. Confirme que o processo ainda se chama `language_server` e ainda passa
   `--csrf_token` na linha de comando.
2. Confirme a porta ouvindo (`psutil` / netstat) e teste
   `GetAllCascadeTrajectories`.
3. Se um método mudou de nome/campo, os erros do servidor são descritivos
   (dizem qual campo falta). Ajuste em `ag_client.py` / `harvest.py`.
