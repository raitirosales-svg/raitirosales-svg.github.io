# -*- coding: utf-8 -*-
"""Sincronizador del Tablero L10 para GitHub Actions.

Lee las tareas de Asana asignadas al dueño del token, calcula los KPIs y escribe
el JSON que consume el tablero. Solo LEE Asana: no mueve tickets ni comenta.

Uso:
    ASANA_TOKEN=... python l10-sync/asana_sync.py l10/data.json
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "https://app.asana.com/api/1.0/"
ASANA_TOKEN = os.environ.get("ASANA_TOKEN", "").strip()
ASANA_WORKSPACE = os.environ.get("ASANA_WORKSPACE", "1176142409313345")
CAMPOS = {
    "empresa": "Nombre de la empresa",
    "industria": "Industria empresa",
    "etapa": "Etapas implementación",
    "tipo": "Ticket o proyecto",
    "mrr": "MRR",
}


def log(*a):
    print(*a, flush=True)


def asana_get(path):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + ASANA_TOKEN})
    for attempt in range(4):
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=60).read())
            return r.get("data", []), r.get("next_page")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503):
                time.sleep(3 + attempt * 3)
                continue
            raise
        except Exception:
            time.sleep(3 + attempt * 3)
    return [], None


def asana_all(path):
    out = []
    first = True
    next_offset = None
    while True:
        if first:
            url = path
        else:
            sep = "&" if "?" in path else "?"
            url = path + sep + "offset=" + next_offset
        data, npg = asana_get(url)
        out.extend(data)
        if npg and npg.get("offset"):
            next_offset = npg["offset"]
            first = False
        else:
            break
    return out


def campo(t, nombre):
    for c in t.get("custom_fields", []):
        if c.get("name") == nombre:
            return c.get("display_value")
    return None


def limpiar_nota(txt):
    txt = re.sub(r"https://app\.asana\.com/\S+", " ", txt or "")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt).strip()
    return txt


def construir_data():
    hoy = datetime.date.today()
    desde7 = (hoy - datetime.timedelta(days=7)).isoformat()
    fields = ("name,completed,completed_at,due_on,projects.name,memberships.section_name,"
              "custom_fields.name,custom_fields.display_value")
    tasks = asana_all(
        "tasks?assignee=me&workspace=%s&completed_since=%s&opt_fields=%s&limit=100"
        % (ASANA_WORKSPACE, desde7, fields)
    )
    log("Tareas obtenidas:", len(tasks))

    activas, completadas7, vencidas = [], [], []
    for t in tasks:
        empresa = campo(t, CAMPOS["empresa"])
        if not empresa:
            continue
        item = {
            "nombre": (t.get("name") or "")[:80],
            "empresa": empresa or "(sin empresa)",
            "industria": (campo(t, CAMPOS["industria"]) or "—").replace("🏬 ", ""),
            "etapa": (campo(t, CAMPOS["etapa"]) or "—"),
            "tipo": campo(t, CAMPOS["tipo"]) or "—",
            "mrr": float(campo(t, CAMPOS["mrr"]) or 0),
            "due": t.get("due_on") or "",
            "proyectos": ", ".join(p.get("name", "") for p in (t.get("projects") or []))[:60],
            "completada": (t.get("completed_at") or "")[:10],
            "gid": t.get("gid"),
        }
        if t.get("completed"):
            completadas7.append(item)
        else:
            activas.append(item)
            if item["due"] and item["due"] < hoy.isoformat():
                vencidas.append(item)

    for a in activas[:15]:
        try:
            stories = asana_all("tasks/%s/stories?limit=100" % a["gid"]) or []
            comentarios = [s for s in stories
                           if s.get("resource_subtype") == "comment_added" or s.get("type") == "comment"]
            if comentarios:
                c = comentarios[-1]
                a["ultima_nota"] = limpiar_nota(c.get("text"))[:4000]
                a["nota_fecha"] = (c.get("created_at") or "")[:16].replace("T", " ")
        except Exception as e:
            log("nota err (%s): %s" % (a["gid"], repr(e)[:80]))

    por_etapa = {}
    for a in activas:
        por_etapa[a["etapa"]] = por_etapa.get(a["etapa"], 0) + 1
    retail = [a for a in activas if "retail" in a["industria"].lower()]
    multi = [a for a in activas if "retail" not in a["industria"].lower()]

    total_con_fecha = len(completadas7) + len(vencidas)
    cumplimiento = round(100 * len(completadas7) / total_con_fecha) if total_con_fecha else 100

    return {
        "generado": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kpis": {
            "activas": len(activas),
            "retail": len(retail),
            "multi": len(multi),
            "entregas_7d": len(completadas7),
            "vencidas": len(vencidas),
            "mrr_activo": sum(a["mrr"] for a in activas),
            "cumplimiento": cumplimiento,
        },
        "por_etapa": por_etapa,
        "activas": sorted(activas, key=lambda x: (x["due"] or "9999")),
        "vencidas": vencidas,
        "completadas_7d": sorted(completadas7, key=lambda x: x.get("due") or "", reverse=True),
    }


def main():
    if not ASANA_TOKEN:
        raise SystemExit("Falta ASANA_TOKEN en el entorno")
    out = sys.argv[1] if len(sys.argv) > 1 else "l10/data.json"
    data = construir_data()
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    log("data.json generado ->", out)
    log("KPIs:", json.dumps(data["kpis"], ensure_ascii=False))


if __name__ == "__main__":
    main()
