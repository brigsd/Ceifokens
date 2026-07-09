# Post-Mortem: Falha de Persistência na Injeção e Ceifa Offline ("A Frio")

Este documento registra a análise técnica pós-testes empíricos sobre a injeção e a ceifa (truncação) de tokens realizadas diretamente nos arquivos locais do Antigravity 2.

---

## 1. O que aconteceu?

Durante os testes cognitivos de injeção de contexto e ceifa de logs gigantes realizados com o editor fechado, constatou-se que as alterações efetuadas diretamente no disco (SQLite e `transcript.jsonl`) **não se mantêm ativas de forma persistente**.

*   **Sintoma visual:** Ao abrir o painel web com o Antigravity fechado, os passos são injetados/ceifados com sucesso no banco `.db` local, sendo desenhados corretamente no painel.
*   **Sintoma real:** Ao abrir o editor do Antigravity 2, os balões originais grandes reaparecem intactos, e qualquer nova injeção é ignorada cognitivamente pelo modelo.

---

## 2. Análise dos Sistemas Offline

### A) Sistema Atual (Sanitizador Ceifokens com State-Cloning e JSONL Sync)
O sistema atual implementa técnicas complexas de manipulação binária a frio:
*   **State-Cloning (Clonagem de Estado Protobuf):** Copia as tags de estado gRPC de mensagens legítimas passadas para as novas mensagens injetadas, mantendo o tamanho e a sintaxe do Protobuf do Google.
*   **JSONL Síncrono:** Atualiza o log de execução do agente no disco de forma síncrona.
*   **Ceifa com Tamanho Preservado:** Trunca strings longas no SQLite preenchendo-as com espaços para manter o comprimento binário original exato e evitar erros de parsing.
*   **Resultado:** Embora os arquivos locais sejam perfeitamente editados sem corrupção e renderizados visualmente na interface, o editor do Antigravity **baixa o histórico original da nuvem ao iniciar e sobrescreve o banco local e os logs**, anulando os efeitos de injeção cognitiva e de economia de tokens.

### B) Sistema Antigo (`_antigo_sanitizador`)
O antigo sanitizador realizava manipulações diretas no banco SQLite:
*   **Funcionamento:** Procurava arquivos `.db` locais e tentava injetar ou limpar mensagens alterando registros diretamente via comandos SQL simples.
*   **Resultado:** Sofria exatamente do mesmo problema. Como o Antigravity armazena as sessões na nuvem do Google/Exa, qualquer modificação local estática era considerada divergente da nuvem e reescrita imediatamente na inicialização do `language_server.exe`.

---

## 3. Causa Raiz: Soberania da Nuvem

O Antigravity 2 trata a sessão em execução na nuvem da API como a **fonte única da verdade (Single Source of Truth)**. 
*   Qualquer arquivo estático alterado no disco rígido local (`.db` ou `.jsonl`) enquanto o editor está fechado é detectado como "desatualizado" ou "inconsistente".
*   O motor do `language_server.exe` força o download da trajetória oficial de conversação guardada nos servidores remotos do Google, apagando as nossas edições e reinstaurando os logs gigantes completos.

---

## 4. Alternativa Proposta (Ainda Não Testada)

Para contornar a soberania da nuvem, a hipótese de desenvolvimento é migrar de uma abordagem "offline" (edição fria de arquivos) para uma abordagem **"ativa" (Pela Porta da Frente)**:

1.  **Comunicação Direta via gRPC:** Interagir diretamente com o processo ativo do `language_server.exe` em execução.
2.  **Sincronização Oficial:** Enviar requisições gRPC (ConnectRPC) legítimas de "criação/edição de passos" para que o próprio servidor local se encarregue de registrar a alteração na nuvem do Google.
3.  **Maturação:** Esta hipótese **ainda não foi testada ou validada**, não devendo substituir ou depreciar o código atual até que sua viabilidade e eficiência sejam empiricamente comprovadas.
