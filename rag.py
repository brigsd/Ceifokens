"""RAG local sobre as conversas colhidas.

  python rag.py build                 indexa tudo em exports/ (gera index/)
  python rag.py search "sua pergunta" busca por significado nas conversas
  python rag.py search "..." -k 8     mais resultados

Modelo de embedding local (multilingue, ~0.22 GB), baixado na 1a vez.
Indice em numpy puro: sem banco vetorial pesado.
"""
import argparse
import glob
import json
import os
import re
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORTS = os.path.join(HERE, "exports")
INDEX_DIR = os.path.join(HERE, "index")
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MAX_CHARS = 1500
OVERLAP = 200


def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL_NAME)


def _split(text):
    """Fatia um texto longo em janelas com sobreposicao."""
    text = text.strip()
    if len(text) <= MAX_CHARS:
        return [text]
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + MAX_CHARS])
        i += MAX_CHARS - OVERLAP
    return out


def _sections(md):
    """Quebra o conversation.md em (papel, texto) por cabecalho ##."""
    parts = re.split(r"^## (.+)$", md, flags=re.MULTILINE)
    # parts[0] = cabecalho do arquivo; depois alterna (titulo, corpo)
    secs = []
    for i in range(1, len(parts), 2):
        role = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body:
            secs.append((role, body))
    return secs


def build():
    convs = sorted(glob.glob(os.path.join(EXPORTS, "*", "conversation.md")))
    if not convs:
        print("nada em exports/. Rode 'python ceifokens.py export all' antes.")
        return
    records = []
    for md_path in convs:
        folder = os.path.dirname(md_path)
        meta = {}
        mp = os.path.join(folder, "meta.json")
        if os.path.exists(mp):
            meta = json.load(open(mp, encoding="utf-8"))
        md = open(md_path, encoding="utf-8").read()
        for order, (role, body) in enumerate(_sections(md)):
            for j, chunk in enumerate(_split(body)):
                records.append({
                    "cascadeId": meta.get("cascadeId"),
                    "title": meta.get("title"),
                    "project": meta.get("project"),
                    "role": role,
                    "order": order,
                    "part": j,
                    "text": chunk,
                })
    print(f"{len(records)} trechos de {len(convs)} conversa(s). Gerando embeddings...")
    model = _model()
    vecs = np.array(list(model.embed([r["text"] for r in records])), dtype=np.float32)
    vecs /= (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
    os.makedirs(INDEX_DIR, exist_ok=True)
    np.save(os.path.join(INDEX_DIR, "vectors.npy"), vecs)
    with open(os.path.join(INDEX_DIR, "chunks.jsonl"), "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"indice salvo em {INDEX_DIR} ({vecs.shape[0]} x {vecs.shape[1]})")


def search(query, k):
    vp = os.path.join(INDEX_DIR, "vectors.npy")
    cp = os.path.join(INDEX_DIR, "chunks.jsonl")
    if not os.path.exists(vp):
        print("sem indice. Rode 'python rag.py build' antes.")
        return
    vecs = np.load(vp)
    chunks = [json.loads(l) for l in open(cp, encoding="utf-8")]
    model = _model()
    q = np.array(list(model.embed([query]))[0], dtype=np.float32)
    q /= (np.linalg.norm(q) + 1e-9)
    sims = vecs @ q
    top = np.argsort(-sims)[:k]
    print(f"\nTop {k} para: \"{query}\"\n")
    for rank, i in enumerate(top, 1):
        c = chunks[i]
        snippet = c["text"].replace("\n", " ")[:220]
        print(f"{rank}. [{sims[i]:.3f}] {c.get('title')} / {c['role']}")
        print(f"   {snippet}")
        print(f"   (conversa {str(c.get('cascadeId'))[:8]}, trecho {c['order']}.{c['part']})\n")


def main():
    p = argparse.ArgumentParser(prog="rag", description="RAG local sobre conversas colhidas.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="indexa exports/")
    ps = sub.add_parser("search", help="busca por significado")
    ps.add_argument("query")
    ps.add_argument("-k", type=int, default=5, help="numero de resultados")
    args = p.parse_args()
    if args.cmd == "build":
        build()
    elif args.cmd == "search":
        search(args.query, args.k)


if __name__ == "__main__":
    main()
