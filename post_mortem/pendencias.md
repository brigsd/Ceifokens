# Pendências: Comportamento da Injeção de Contexto

Este documento registra fatos observados durante o desenvolvimento e os testes do Sanitizador em relação à injeção de contexto no Antigravity 2.

## Fatos Observados nos Testes

1. **Injeção com Editor Fechado:**
   - O Sanitizador realiza a escrita síncrona de novos passos no SQLite (tabela `steps`) e anexa registros estruturados correspondentes no arquivo de log `transcript.jsonl`.
   - Após a escrita física nos arquivos, as mensagens injetadas aparecem visualmente desenhadas na linha do tempo do chat (como balões de conversa normais na interface visual do usuário).

2. **Inexistência Cognitiva (Janela de Contexto):**
   - Quando o modelo de inteligência artificial ativo no chat é questionado sobre a informação injetada (sem a execução de ferramentas de leitura de arquivo ou comandos de terminal por parte do modelo), o modelo não demonstra conhecimento da informação em seu histórico ativo de prompt.

3. **Sobrescrita do `transcript.jsonl`:**
   - O arquivo `transcript.jsonl` editado pelo Sanitizador é modificado automaticamente pelo Antigravity 2 após a reabertura do editor ou no envio de novas mensagens no chat.
   - Após essa modificação efetuada pelo editor, os registros de passos injetados anteriormente pelo Sanitizador deixam de constar no arquivo `transcript.jsonl` no disco.

4. **Processos em Segundo Plano:**
   - Mesmo com a janela visual do Antigravity 2 fechada, os processos `Antigravity.exe` e `language_server.exe` podem permanecer em execução na memória do Windows.
   - O encerramento forçado desses processos é realizado através do utilitário `taskkill` do Windows (`taskkill /F /IM ...`).
