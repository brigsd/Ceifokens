"""Ceifokens - colheita de conversas do Antigravity para RAG.

Uso:
  python ceifokens.py list                 lista as conversas do workspace ativo
  python ceifokens.py export <id|all>      exporta conversa(s) para ./exports
  python ceifokens.py export all --out D:\rag\antigravity

Requer o Antigravity aberto (o servidor local precisa estar rodando).
"""
import argparse
import os
import sys

from ag_client import Antigravity
from harvest import harvest

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")


def cmd_list(ag, _args):
    convs = ag.list_conversations()
    if not convs:
        print("nenhuma conversa no workspace ativo.")
        return
    print(f"{len(convs)} conversa(s):\n")
    rows = sorted(convs.items(), key=lambda kv: kv[1].get("lastModifiedTime", ""), reverse=True)
    for cid, info in rows:
        title = (info.get("summary") or "(sem titulo)")[:45]
        print(f"  {cid[:8]}  {info.get('stepCount', '?'):>4} steps  {title}")


def cmd_export(ag, args):
    convs = ag.list_conversations()
    if not convs:
        print("nenhuma conversa para exportar.")
        return
    if args.target == "all":
        targets = list(convs.keys())
    else:
        targets = [cid for cid in convs if cid.startswith(args.target)]
        if not targets:
            print(f"id '{args.target}' nao encontrado. Rode 'list' para ver os ids.")
            return
    out = args.out or DEFAULT_OUT
    os.makedirs(out, exist_ok=True)
    for cid in targets:
        try:
            folder, n, meta = harvest(ag, cid, convs[cid], out)
            print(f"  OK {cid[:8]}  {n} turnos  -> {folder}")
        except Exception as e:
            print(f"  ERRO {cid[:8]}: {e}")
    print(f"\nexportado para: {out}")


def main():
    p = argparse.ArgumentParser(prog="ceifokens", description="Colheita de conversas do Antigravity para RAG.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="lista conversas")
    pe = sub.add_parser("export", help="exporta conversa(s)")
    pe.add_argument("target", help="id (prefixo) da conversa, ou 'all'")
    pe.add_argument("--out", help="pasta de saida (padrao: ./exports)")
    args = p.parse_args()

    try:
        ag = Antigravity()
    except RuntimeError as e:
        print(f"erro: {e}")
        sys.exit(1)

    if args.cmd == "list":
        cmd_list(ag, args)
    elif args.cmd == "export":
        cmd_export(ag, args)


if __name__ == "__main__":
    main()
