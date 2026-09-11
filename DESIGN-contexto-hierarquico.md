# Contexto hierárquico em disco: quantização, seleção esparsa e reciclagem

Documento de design para armazenar uma janela de contexto muito grande fora da
VRAM e da RAM, mantendo latência de geração utilizável em hardware de
consumidor.

Estado: proposta. Nada aqui foi implementado ainda.

---

## 1. Problema

A janela de contexto de um modelo transformer é limitada na prática pelo tamanho
do KV cache, não pelo modelo em si. O cache cresce linearmente com o número de
tokens e precisa estar acessível a cada token gerado.

Dimensionamento de referência, modelo de 8B com GQA (32 camadas, 8 cabeças KV,
dimensão de cabeça 128):

    bytes por token = 2 (K e V) x 32 camadas x 8 cabeças x 128 dims x 2 bytes
                    = 131.072 bytes = 128 KB por token

    1.000.000 tokens = 128 GB em FP16
    1.000.000 tokens =  32 GB em INT4

O espaço não é o obstáculo difícil: 32 GB cabem em qualquer SSD. O obstáculo é
banda vezes latência por token gerado. A atenção, na forma ingênua, precisa do
cache inteiro a cada passo.

    32 GB / 7 GB/s (NVMe PCIe 4.0 x4) = 4,5 segundos por token

Quantizar de FP16 para INT4 multiplica a banda efetiva por 4. É necessário, mas
está duas ordens de grandeza longe de ser suficiente sozinho.

## 2. Princípio central

Duas alavancas combinadas, não uma:

1. **Quantização** reduz os bytes por token transferidos.
2. **Esparsidade dinâmica** reduz quantos tokens são transferidos.

Nada é descartado. Todo o contexto continua persistido. O que muda a cada passo
é apenas qual fatia sobe para a GPU. A perda de informação é substituída por
latência de acesso, que é a troca correta: um detalhe antigo continua
recuperável, ao contrário do que acontece em métodos de descarte permanente
(H2O, SnapKV).

Conta com as duas alavancas, orçamento de 4096 tokens efetivos por camada:

    por camada, por token, INT4 = 2 x 8 x 128 x 0,5 bytes = 1.024 bytes
    4096 tokens x 1 KB          = 4 MB por camada
    4 MB x 32 camadas           = 128 MB por token gerado
    128 MB / 7 GB/s             = ~18 ms por token (teto de I/O)

Com cache quente em RAM a taxa de acerto fica alta, porque os blocos
selecionados mudam pouco entre tokens consecutivos, e o tráfego real de disco
cai bem abaixo desse teto. Com prefetch antecipado por camada, o I/O restante
sobrepõe ao cálculo.

## 3. Arquitetura

### 3.1 Níveis de armazenamento

**Nível 0, VRAM residente.**
Sinks iniciais (primeiros 4 a 32 tokens, conforme StreamingLLM) e janela local
recente, sempre em FP16. Mais os sumários de todos os blocos, que custam poucas
centenas de bytes por bloco. Nunca são despejados.

**Nível 1, RAM fixada.**
Cache de blocos quentes, INT4 ou INT8, despejo por LRU ponderado pela frequência
de seleção. Amortecedor entre GPU e disco. Memória pinned para permitir DMA.

**Nível 2, NVMe.**
Corpo do contexto. Arquivos alinhados ao tamanho de bloco, layout agrupado por
camada e por cabeça para que a leitura de um bloco seja sequencial. Leitura
direta para a VRAM via GPUDirect Storage quando disponível, caso contrário
io_uring com buffers fixados.

**Nível 3, histórico frio.**
Blocos que não são acessados há muito tempo. Aqui o KV quantizado deixa de
compensar: guarda-se o texto do trecho (e opcionalmente seus embeddings) e o KV
é recomputado sob demanda. Recomputar 128 tokens é uma operação em lote rápida e
ocupa zero espaço de cache enquanto dorme.

### 3.2 Componentes

**Empacotador (pager e escalonador).**
Mantém a tabela de páginas dos blocos, no modelo do Paged Attention do vLLM.
A cada camada: recebe a query, consulta o índice de sumários, decide o conjunto
de blocos necessários, classifica cada um por nível, emite as leituras e dispara
o prefetch da camada seguinte enquanto a atual ainda calcula. O prefetch
antecipado é o que esconde a latência do NVMe, e funciona porque a seleção de
blocos é fortemente correlacionada entre camadas vizinhas.

**Índice de blocos.**
Para cada bloco de 128 tokens, um sumário em VRAM: mínimo e máximo por dimensão
das chaves. Antes da atenção, calcula-se um limite superior barato do score de
cada bloco contra a query e seleciona-se o top-k. É a abordagem do Quest e do
InfLLM. O custo do índice é desprezível frente ao cache.

**Reciclador.**
Política de despejo com rebaixamento de nível em vez de descarte. Um bloco
expulso da VRAM desce para a RAM, depois para o NVMe, depois para o histórico
frio. A reidratação é sob demanda e transparente para a atenção.

### 3.3 Quantização

Chaves e valores não se comportam igual e não devem ser tratados igual. As
chaves têm outliers persistentes em canais específicos; os valores não.

- Chaves: quantização por canal.
- Valores: quantização por token.
- Escala e ponto zero por grupo de 32 ou 64 elementos, armazenados junto do
  bloco para que a leitura seja uma única operação.
- Sinks e janela recente permanecem em FP16.

Essa assimetria é o resultado central do KIVI e do KVQuant, e é a diferença
entre INT4 utilizável e INT4 que degrada a saída de forma inaceitável.

A dequantização acontece dentro do kernel de atenção, lendo direto do buffer
INT4 em memória compartilhada. O cache nunca é materializado em FP16, caso
contrário a economia de VRAM desaparece. O custo aritmético da dequantização na
GPU é irrelevante frente ao custo de mover os bytes.

INT2 com quantização residual nos blocos mais frios é possível, mas só depois do
resto estar funcionando e medido.

## 4. O que não funciona

**Comprimir o contexto sem perda de qualidade.** Não existe. Qualquer estrutura
que resuma o KV perde informação. Os métodos que descartam tokens de forma
irreversível quebram em perguntas que voltam a um detalhe antigo. A saída é a
seleção por consulta, reversível, com tudo preservado em disco.

**Trazer o contexto inteiro de volta a cada passo.** Mesmo com INT2 e PCIe 5.0 a
conta não fecha por duas ordens de grandeza. A esparsidade não é opcional.

## 5. Trabalho relacionado

| Peça | Referências |
|---|---|
| Paginação de KV | Paged Attention (vLLM) |
| Offload hierárquico | FlexGen, DeepSpeed ZeRO-Inference, LMCache |
| Quantização de KV | KIVI, KVQuant |
| Seleção esparsa por bloco | Quest, InfLLM |
| Sinks de atenção | StreamingLLM |
| Descarte (abordagem rejeitada aqui) | H2O, SnapKV |
| Reuso e fusão de prefixos | CacheBlend |

Nenhuma dessas peças precisa ser reescrita. vLLM fornece paginação e
gerenciamento de blocos; LMCache fornece hierarquia de armazenamento e
transporte. O trabalho novo está no empacotador, no índice de sumários e na
política do reciclador.

## 6. Onde está a contribuição real

As peças isoladas existem. A combinação das três (quantização agressiva,
seleção esparsa por bloco e armazenamento em NVMe com prefetch) não está
resolvida, e há uma razão: pesquisa e indústria otimizam para servir muitos
usuários com prefixos compartilhados em GPU de datacenter, onde o cache cabe em
VRAM e ninguém quer tocar em SSD. O caso de uma máquina, um usuário, contexto de
milhões de tokens e hardware de consumidor é subservido.

Três frentes concretas, em ordem crescente de dificuldade:

**1. Integração funcional.** Boa parte desses trabalhos é código de pesquisa que
roda em um benchmark e nada além. Um sistema que alguém consiga usar de fato tem
valor mesmo sem conceito novo.

**2. Acoplamento entre quantização e seleção esparsa.** Questão genuinamente
aberta. Se os sumários usados para escolher blocos são calculados sobre dados já
quantizados, os erros se compõem: a quantização distorce os limites de score, a
seleção erra o bloco, e o modelo nunca vê a informação certa. Os dois trabalhos
vivem em papers separados e ninguém mediu a interação. Caracterizar isso e
propor sumários robustos à quantização é contribuição publicável.

**3. Política adaptativa do reciclador.** Quando compensa ler o KV do disco e
quando compensa recomputar a partir do texto? O ponto de equilíbrio depende de
banda, tamanho de bloco, ocupação da GPU e profundidade da camada, e muda com o
hardware. Uma política que decide isso em tempo de execução não existe pronta.

## 7. Caminho de implementação

Cada etapa é mensurável isoladamente. Não avançar sem medir a anterior.

1. **KV paginado com despejo para RAM, sem quantização.**
   Estabelece o teto de banda e a infraestrutura de blocos.
   Métrica: tokens por segundo em função do comprimento de contexto.

2. **Quantização INT4 assimétrica com dequantização no kernel.**
   Chave por canal, valor por token, sinks em FP16.
   Métrica: perplexidade e acurácia em recuperação de detalhe (needle in a
   haystack) comparadas ao baseline FP16, isoladas da esparsidade.

3. **Índice de sumários e seleção esparsa top-k.**
   É aqui que aparece o ganho real de escala.
   Métrica: qualidade em função do orçamento de tokens por camada, e a interação
   com a etapa 2 (ver seção 6, item 2).

4. **Camada de NVMe com prefetch antecipado por camada.**
   Métrica: taxa de acerto do cache de RAM, latência de I/O efetiva por token,
   fração de I/O sobreposta ao cálculo.

5. **Reciclador frio com recomputação a partir do texto.**
   Métrica: custo de reidratação comparado à leitura direta, em função do
   tamanho de bloco e da carga da GPU.

## 8. Parâmetros a definir empiricamente

- Tamanho de bloco (ponto de partida: 128 tokens). Blocos maiores melhoram a
  sequencialidade da leitura e pioram a granularidade da seleção.
- Orçamento de tokens por camada (ponto de partida: 4096). Pode ser variável por
  profundidade de camada.
- Quantidade de sinks em FP16 (ponto de partida: 16).
- Tamanho do grupo de quantização (32 ou 64).
- Tamanho do cache de RAM e limiar de rebaixamento para o nível frio.
