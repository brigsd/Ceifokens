# CLAUDE.md

Guia para o Claude Code trabalhar neste repositório.

## Sobre o projeto

Ceifokens é uma ferramenta pessoal em Python que colhe as conversas do Antigravity (o IDE agêntico do Google, de base Codeium) e as transforma numa base de conhecimento com busca semântica local (RAG).

O núcleo ativo fica na raiz. O `ag_client.py` descobre a porta e o token CSRF do `language_server` local via `psutil` e conversa com a API interna ConnectRPC. O `harvest.py` limpa os steps e gera Markdown legível. O `rag.py` indexa tudo com FastEmbed e busca por significado sem depender de nuvem. O `ceifokens.py` é a linha de comando.

A pasta `post_mortem/` guarda o ferramental antigo do "Sanitizador" (injeção e truncagem de contexto direto no disco). Essa abordagem não vingou porque o Antigravity trata a nuvem como fonte da verdade e sobrescreve edições locais, então está arquivada de propósito e não deve ser reativada sem necessidade.

## Dados pessoais

Conversas exportadas são dados pessoais e não entram no versionamento. O `.gitignore` já cobre `exports/` e `index/`. Nunca commitar transcrições, screenshots ou logs de conversas reais.

## Preferências de comunicação

Responder em português fluido e natural, de forma direta e resumida, indo direto ao ponto e sem texto picotado. Evitar o uso de travessão.
