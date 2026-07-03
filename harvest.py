"""Colheita: transforma uma conversa do Antigravity em saida limpa para RAG.

Gera, por conversa, uma pasta com:
  meta.json         - id, titulo, projeto, datas, contagem de steps
  conversation.md   - transcricao legivel (voce + agente), boa para fatiar/RAG
  raw_steps.json    - os steps crus (backup completo)
"""
import json
import os
import re

# tipos de step que carregam conversa de verdade
USER_TYPES = {"CORTEX_STEP_TYPE_USER_INPUT"}
AGENT_TYPES = {"CORTEX_STEP_TYPE_PLANNER_RESPONSE"}


def _collect_text(obj):
    """Junta recursivamente todos os campos 'text' de um payload."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "text" and isinstance(v, str) and v.strip():
                out.append(v)
            else:
                out += _collect_text(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _collect_text(v)
    return out


def _tool_summary(tc):
    """Linha legivel para um toolCall do agente."""
    name = tc.get("name") or tc.get("toolName") or "acao"
    args = {}
    raw = tc.get("argumentsJson")
    if isinstance(raw, str):
        try:
            args = json.loads(raw)
        except Exception:
            args = {}
    elif isinstance(tc.get("arguments"), dict):
        args = tc["arguments"]
    summary = ""
    meta = args.get("ArtifactMetadata") or {}
    if meta.get("Summary"):
        summary = meta["Summary"]
    for kk in ("CommandLine", "TargetFile", "AbsolutePath", "Query", "Url", "Path"):
        if not summary and args.get(kk):
            summary = str(args[kk])
            break
    return f"- {name}: {summary[:200]}".rstrip(": ").rstrip()


def extract_turns(steps):
    turns = []
    for s in steps:
        t = s.get("type", "")
        ts = (s.get("metadata") or {}).get("createdAt", "")
        if t in USER_TYPES:
            txt = "\n".join(_collect_text(s.get("userInput", {})))
            if txt.strip():
                turns.append({"role": "user", "text": txt, "ts": ts})
        elif t in AGENT_TYPES:
            pr = s.get("plannerResponse", {})
            parts = []
            if pr.get("thinking"):
                parts.append(pr["thinking"].strip())
            tool_lines = [_tool_summary(tc) for tc in pr.get("toolCalls", [])]
            tool_lines = [ln for ln in tool_lines if ln and ln != "-"]
            if tool_lines:
                parts.append("Acoes:\n" + "\n".join(tool_lines))
            txt = "\n\n".join(parts)
            if txt.strip():
                turns.append({"role": "assistant", "text": txt, "ts": ts})
    return turns


def to_markdown(meta, turns):
    lines = [f"# {meta.get('title') or meta.get('cascadeId')}", ""]
    lines.append(f"- Conversa: `{meta.get('cascadeId')}`")
    if meta.get("project"):
        lines.append(f"- Projeto: {meta['project']}")
    if meta.get("createdTime"):
        lines.append(f"- Criada: {meta['createdTime']}")
    if meta.get("lastModifiedTime"):
        lines.append(f"- Modificada: {meta['lastModifiedTime']}")
    lines.append(f"- Steps: {meta.get('stepCount')}")
    lines.append("")
    for turn in turns:
        role = turn["role"]
        if role == "user":
            lines += ["## Voce", "", turn["text"], ""]
        elif role == "assistant":
            lines += ["## Agente", "", turn["text"], ""]
        else:
            lines += [f"> {turn['text']}", ""]
    return "\n".join(lines)


def _slug(s):
    return re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip()).strip("-").lower()[:40] or "conversa"


def harvest(client, cascade_id, summary, out_root):
    steps = client.get_steps(cascade_id)
    project = ""
    ws = (summary.get("workspaces") or [{}])
    if ws and ws[0].get("workspaceFolderAbsoluteUri"):
        project = ws[0]["workspaceFolderAbsoluteUri"]
    meta = {
        "cascadeId": cascade_id,
        "trajectoryId": summary.get("trajectoryId"),
        "title": summary.get("summary"),
        "project": project,
        "createdTime": summary.get("createdTime"),
        "lastModifiedTime": summary.get("lastModifiedTime"),
        "stepCount": summary.get("stepCount", len(steps)),
    }
    turns = extract_turns(steps)
    folder = os.path.join(out_root, f"{_slug(summary.get('summary'))}_{cascade_id[:8]}")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with open(os.path.join(folder, "conversation.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(meta, turns))
    with open(os.path.join(folder, "raw_steps.json"), "w", encoding="utf-8") as f:
        json.dump(steps, f, ensure_ascii=False)
    return folder, len(turns), meta
