import datetime
import json
import os
import re
import threading
import time
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
import pandas as pd
from sqlalchemy import create_engine, inspect, text

caminho_env = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(caminho_env)

app = FastAPI(title="API - Monitoramento Logístico")

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

string_conexao = os.getenv("DATABASE_URL")
if not string_conexao:
  raise ValueError(
      "ERRO: A variável DATABASE_URL não foi encontrada no ficheiro .env"
  )

motor_db = create_engine(
    string_conexao,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=10,
)

# ==========================================
# CACHE EM MEMÓRIA DE ALTA VELOCIDADE (JSON PRÉ-SERIALIZADO)
# ==========================================
CACHE_DADOS = {
    "rotas_json": None,
    "rotas_ts": 0,
    "rotas_brutas_json": None,
    "rotas_brutas_ts": 0,
    "ocorrencias_json": None,
    "ocorrencias_ts": 0,
    "avulsas_json": None,
    "avulsas_ts": 0,
}

COLUNAS_UTEIS_RELATORIO = [
    "ID do Rota",
    "Nome da Rota",
    "Data da rota",
    "Data",
    "ID del Depósito",
    "Depósito",
    "ID do Nodo",
    "ID do Motorista",
    "ID do Veículo",
    "Tipo de veículo",
    "Tipo de parada",
    "Parada",
    "ID de Parada",
    "Horários",
    "ONTIME",
    "Pontualidade",
    "Hora de coleta concluída",
    "Pacotes coletados",
    "Pacotes estimados",
    "Pacotes preparados",
    "Tipo do Rota",
    "Planejado",
    "Motivo",
    "Detalhes",
    "Pacotes não preparados/Não estão na agência",
    "Pacotes não cabem no veículo",
    "Pacotes em mau estado",
]


def invalidar_cache_rotas():
  CACHE_DADOS["rotas_json"] = None
  CACHE_DADOS["rotas_ts"] = 0


def invalidar_cache_brutas():
  CACHE_DADOS["rotas_brutas_json"] = None
  CACHE_DADOS["datas_brutas"] = None
  CACHE_DADOS["por_data_json"] = {}
  CACHE_DADOS["rotas_brutas_ts"] = 0


def invalidar_cache_ocorrencias():
  CACHE_DADOS["ocorrencias_json"] = None
  CACHE_DADOS["ocorrencias_ts"] = 0


def invalidar_cache_avulsas():
  CACHE_DADOS["avulsas_json"] = None
  CACHE_DADOS["avulsas_ts"] = 0


def aquecer_cache_background():
  """Pré-carrega os dados em memória assim que a API inicia para o 1º acesso ser instantâneo"""
  time.sleep(1)
  try:
    construir_json_rotas()
    construir_json_rotas_brutas()
  except Exception:
    pass


@app.on_event("startup")
def ao_iniciar_api():
  threading.Thread(target=aquecer_cache_background, daemon=True).start()


# ==========================================
# GESTÃO DE USUÁRIOS E LOGIN
# ==========================================
@app.post("/login")
async def fazer_login(request: Request):
  dados = await request.json()
  try:
    query = text(
        "SELECT username, cargo, hubs_atribuidos FROM usuarios WHERE username ="
        " :u AND password = :p"
    )
    with motor_db.connect() as conn:
      resultado = conn.execute(
          query, {"u": dados.get("username"), "p": dados.get("password")}
      ).fetchone()

      if resultado:
        return {
            "sucesso": True,
            "usuario": {
                "username": resultado[0],
                "cargo": resultado[1],
                "hubs": resultado[2],
            },
        }
      return {"sucesso": False, "erro": "Usuário ou senha incorretos."}
  except Exception as e:
    return {"sucesso": False, "erro": f"Erro no banco: {str(e)}"}


@app.get("/usuarios")
def listar_usuarios():
  try:
    with motor_db.connect() as conn:
      result = conn.execute(
          text("SELECT username, cargo, hubs_atribuidos FROM usuarios")
      ).fetchall()
      return [
          {"username": r[0], "cargo": r[1], "hubs": r[2]} for r in result
      ]
  except Exception:
    return []


@app.post("/add_usuario")
async def add_usuario(request: Request):
  dados = await request.json()
  u = dados.get("username")
  p = dados.get("password")
  c = dados.get("cargo")
  h = dados.get("hubs", "TODOS")

  try:
    with motor_db.begin() as conn:
      count = conn.execute(
          text("SELECT count(*) FROM usuarios WHERE username=:u"), {"u": u}
      ).scalar()
      if count > 0:
        if p:
          conn.execute(
              text(
                  "UPDATE usuarios SET password=:p, cargo=:c,"
                  " hubs_atribuidos=:h WHERE username=:u"
              ),
              {"u": u, "p": p, "c": c, "h": h},
          )
        else:
          conn.execute(
              text(
                  "UPDATE usuarios SET cargo=:c, hubs_atribuidos=:h WHERE"
                  " username=:u"
              ),
              {"u": u, "c": c, "h": h},
          )
      else:
        conn.execute(
            text(
                "INSERT INTO usuarios (username, password, cargo,"
                " hubs_atribuidos) VALUES (:u, :p, :c, :h)"
            ),
            {"u": u, "p": p, "c": c, "h": h},
        )
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


@app.delete("/delete_usuario/{username}")
def del_usuario(username: str):
  try:
    with motor_db.begin() as conn:
      conn.execute(
          text("DELETE FROM usuarios WHERE username=:u"), {"u": username}
      )
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


# ==========================================
# ROTAS DE METAS POR HUB E MÊS
# ==========================================
@app.get("/metas")
def listar_metas():
  try:
    with motor_db.connect() as conn:
      rows = conn.execute(text("SELECT hub, meta FROM metas_hubs")).fetchall()
      return {str(r[0]): float(r[1]) for r in rows}
  except Exception:
    return {}


@app.post("/salvar_meta")
async def salvar_meta(request: Request):
  dados = await request.json()
  hub = str(dados.get("hub", "TODOS")).upper().strip()
  meta = float(dados.get("meta", 83.0))
  try:
    with motor_db.begin() as conn:
      conn.execute(
          text(
              "CREATE TABLE IF NOT EXISTS metas_hubs (hub VARCHAR(100) PRIMARY"
              " KEY, meta FLOAT)"
          )
      )
      count = conn.execute(
          text("SELECT count(*) FROM metas_hubs WHERE hub=:h"), {"h": hub}
      ).scalar()
      if count > 0:
        conn.execute(
            text("UPDATE metas_hubs SET meta=:m WHERE hub=:h"),
            {"m": meta, "h": hub},
        )
      else:
        conn.execute(
            text("INSERT INTO metas_hubs (hub, meta) VALUES (:h, :m)"),
            {"h": hub, "m": meta},
        )
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


@app.post("/deletar_meta")
async def deletar_meta(request: Request):
  dados = await request.json()
  hub = str(dados.get("hub", "")).upper().strip()
  try:
    with motor_db.begin() as conn:
      conn.execute(text("DELETE FROM metas_hubs WHERE hub=:h"), {"h": hub})
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


# ==========================================
# EDIÇÕES DE OCORRÊNCIAS (VETORIZADO E COM CACHE)
# ==========================================
@app.get("/ocorrencias_edicoes")
def listar_edicoes_ocorrencias():
  agora = time.time()
  if (
      CACHE_DADOS["ocorrencias_json"] is not None
      and (agora - CACHE_DADOS["ocorrencias_ts"]) < 300
  ):
    return Response(
        content=CACHE_DADOS["ocorrencias_json"], media_type="application/json"
    )

  try:
    df = pd.read_sql_query(
        text("SELECT * FROM controle_ocorrencias"), con=motor_db
    ).fillna("")
    if df.empty:
      return {}

    chaves = df["chave"].astype(str).tolist()
    ids = (
        df["id_rota"].astype(str).tolist()
        if "id_rota" in df.columns
        else [""] * len(df)
    )
    revs = (
        df["revertido"].astype(str).tolist()
        if "revertido" in df.columns
        else ["NÃO"] * len(df)
    )
    obss = (
        df["observacao"].astype(str).tolist()
        if "observacao" in df.columns
        else [""] * len(df)
    )

    resultado = {
        k: {"id_rota": ir, "revertido": rv if rv else "NÃO", "observacao": ob}
        for k, ir, rv, ob in zip(chaves, ids, revs, obss)
    }
    json_str = json.dumps(resultado, ensure_ascii=False)
    CACHE_DADOS["ocorrencias_json"] = json_str
    CACHE_DADOS["ocorrencias_ts"] = agora
    return Response(content=json_str, media_type="application/json")
  except Exception:
    return {}


@app.post("/salvar_edicao_ocorrencia")
async def salvar_edicao_ocorrencia(request: Request):
  dados = await request.json()
  chave = str(dados.get("chave", "")).strip()
  id_rota = str(dados.get("id_rota", "")).strip()
  revertido = str(dados.get("revertido", "NÃO")).upper().strip()
  observacao = str(dados.get("observacao", "")).strip()

  if not chave:
    return {"sucesso": False, "erro": "Chave inválida"}

  try:
    with motor_db.begin() as conn:
      conn.execute(text("""
                CREATE TABLE IF NOT EXISTS controle_ocorrencias (
                    chave VARCHAR(255) PRIMARY KEY,
                    id_rota VARCHAR(50),
                    revertido VARCHAR(20),
                    observacao TEXT
                )
            """))
      count = conn.execute(
          text("SELECT count(*) FROM controle_ocorrencias WHERE chave=:c"),
          {"c": chave},
      ).scalar()
      if count > 0:
        conn.execute(
            text("""
                    UPDATE controle_ocorrencias 
                    SET id_rota=:ir, revertido=:rv, observacao=:ob 
                    WHERE chave=:c
                """),
            {"ir": id_rota, "rv": revertido, "ob": observacao, "c": chave},
        )
      else:
        conn.execute(
            text("""
                    INSERT INTO controle_ocorrencias (chave, id_rota, revertido, observacao) 
                    VALUES (:c, :ir, :rv, :ob)
                """),
            {"c": chave, "ir": id_rota, "rv": revertido, "ob": observacao},
        )

    if id_rota:
      try:
        with motor_db.begin() as conn:
          conn.execute(
              text(
                  "UPDATE rotas_monitoramento SET revertido = :rv WHERE"
                  " id_rota = :ir"
              ),
              {"rv": revertido, "ir": id_rota},
          )
      except Exception:
        pass
      invalidar_cache_rotas()

    invalidar_cache_ocorrencias()
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


# ==========================================
# CONTROLE DE ROTAS AVULSAS (FORMULÁRIO, PAGAMENTO E OBSERVAÇÕES FIXOS NO BANCO)
# ==========================================
@app.get("/rotas_avulsas_edicoes")
def listar_edicoes_rotas_avulsas():
  agora = time.time()
  if (
      CACHE_DADOS.get("avulsas_json") is not None
      and (agora - CACHE_DADOS.get("avulsas_ts", 0)) < 300
  ):
    return Response(
        content=CACHE_DADOS["avulsas_json"], media_type="application/json"
    )

  try:
    df = pd.read_sql_query(
        text("SELECT * FROM controle_rotas_avulsas"), con=motor_db
    ).fillna("")
    if df.empty:
      return {}

    resultado = {}
    for _, row in df.iterrows():
      id_r = str(row.get("id_rota", "")).strip()
      if not id_r:
        continue
      form_val = str(row.get("formulario", "NÃO")).upper().strip() or "NÃO"
      pag_val = str(row.get("pagamento", "NÃO")).upper().strip() or "NÃO"
      resultado[id_r] = {
          "id_rota": id_r,
          "nome_rota": str(row.get("nome_rota", "")).strip(),
          "motorista": str(row.get("motorista", "")).strip(),
          "formulario": "SIM" if form_val == "SIM" else "NÃO",
          "pagamento": "SIM" if pag_val == "SIM" else "NÃO",
          "observacao": str(row.get("observacao", "")).strip(),
      }

    json_str = json.dumps(resultado, ensure_ascii=False)
    CACHE_DADOS["avulsas_json"] = json_str
    CACHE_DADOS["avulsas_ts"] = agora
    return Response(content=json_str, media_type="application/json")
  except Exception:
    return {}


@app.post("/salvar_edicao_avulsa")
async def salvar_edicao_avulsa(request: Request):
  dados = await request.json()
  id_rota = str(dados.get("id_rota", "")).strip()
  nome_rota = str(dados.get("nome_rota", "")).strip()
  motorista = str(dados.get("motorista", "")).strip()
  formulario = (
      "SIM"
      if str(dados.get("formulario", "NÃO")).upper().strip() == "SIM"
      else "NÃO"
  )
  pagamento = (
      "SIM"
      if str(dados.get("pagamento", "NÃO")).upper().strip() == "SIM"
      else "NÃO"
  )
  observacao = str(dados.get("observacao", "")).strip()

  if not id_rota:
    return {"sucesso": False, "erro": "ID da rota inválido"}

  try:
    with motor_db.begin() as conn:
      conn.execute(text("""
                CREATE TABLE IF NOT EXISTS controle_rotas_avulsas (
                    id_rota VARCHAR(50) PRIMARY KEY,
                    nome_rota VARCHAR(100),
                    motorista VARCHAR(255),
                    formulario VARCHAR(20) DEFAULT 'NÃO',
                    pagamento VARCHAR(20) DEFAULT 'NÃO',
                    observacao TEXT DEFAULT ''
                )
            """))
      count = conn.execute(
          text("SELECT count(*) FROM controle_rotas_avulsas WHERE id_rota = :id"),
          {"id": id_rota},
      ).scalar()
      if count > 0:
        conn.execute(
            text("""
                    UPDATE controle_rotas_avulsas
                    SET nome_rota = CASE WHEN :nr <> '' THEN :nr ELSE nome_rota END,
                        motorista = CASE WHEN :mt <> '' THEN :mt ELSE motorista END,
                        formulario = :fm,
                        pagamento = :pg,
                        observacao = :ob
                    WHERE id_rota = :id
                """),
            {
                "nr": nome_rota,
                "mt": motorista,
                "fm": formulario,
                "pg": pagamento,
                "ob": observacao,
                "id": id_rota,
            },
        )
      else:
        conn.execute(
            text("""
                    INSERT INTO controle_rotas_avulsas (id_rota, nome_rota, motorista, formulario, pagamento, observacao)
                    VALUES (:id, :nr, :mt, :fm, :pg, :ob)
                """),
            {
                "id": id_rota,
                "nr": nome_rota,
                "mt": motorista,
                "fm": formulario,
                "pg": pagamento,
                "ob": observacao,
            },
        )

    invalidar_cache_avulsas()
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


# ==========================================
# ROTAS DO SISTEMA DE MONITORAMENTO E BI
# ==========================================
def construir_json_rotas() -> str:
  try:
    df = pd.read_sql_query(
        text("SELECT * FROM rotas_monitoramento"), con=motor_db
    ).fillna("")
  except Exception:
    CACHE_DADOS["rotas_json"] = "[]"
    CACHE_DADOS["rotas_ts"] = time.time()
    return "[]"

  if df.empty:
    CACHE_DADOS["rotas_json"] = "[]"
    CACHE_DADOS["rotas_ts"] = time.time()
    return "[]"

  for col_obrigatoria in [
      "monitor",
      "ocorrencia",
      "revertido",
      "observacoes",
      "restante",
      "status_manual",
      "cores_customizadas",
      "campos_manuais",
  ]:
    if col_obrigatoria not in df.columns:
      df[col_obrigatoria] = (
          "{}"
          if col_obrigatoria in ["cores_customizadas", "campos_manuais"]
          else ""
      )

  df["monitor"] = df["monitor"].astype(str).str.upper().str.strip()

  est_series = (
      pd.to_numeric(
          df["estimado_total"]
          .astype(str)
          .str.replace(r"[^\d.-]", "", regex=True),
          errors="coerce",
      )
      .fillna(0)
      .astype(int)
  )
  col_series = (
      pd.to_numeric(
          df["coletado_geral"]
          .astype(str)
          .str.replace(r"[^\d.-]", "", regex=True),
          errors="coerce",
      )
      .fillna(0)
      .astype(int)
  )
  df["restante"] = (est_series - col_series).astype(str)

  st_upper = df["status"].astype(str).str.strip().str.upper()
  manual_upper = df["status_manual"].astype(str).str.strip().str.upper()
  tem_cor_status = (
      df["cores_customizadas"].astype(str).str.contains('"status"', na=False)
  )
  tem_campo_status = (
      df["campos_manuais"].astype(str).str.contains('"status"', na=False)
  )
  nao_manual = (
      (manual_upper != "SIM") & (~tem_cor_status) & (~tem_campo_status)
  )

  mask_sem_ini = nao_manual & st_upper.str.contains(
      "SEM INÍCIO|SEM INICIO", regex=True, na=False
  )
  mask_andamento = nao_manual & (
      st_upper.str.contains("ANDAMENTO", na=False)
      | st_upper.isin(["INÍCIO", "INICIO"])
  )
  mask_concluida = nao_manual & st_upper.str.contains("CONCLU", na=False)
  mask_finalizado = nao_manual & (st_upper == "FINALIZADO")

  df.loc[mask_sem_ini, "status"] = "PENDENTE DE LOGIN"
  df.loc[mask_andamento & (col_series > 0), "status"] = "COLETANDO"
  df.loc[mask_andamento & (col_series <= 0), "status"] = "LOGADO"
  df.loc[mask_concluida & (col_series > 0), "status"] = "FINALIZADA"
  df.loc[mask_concluida & (col_series <= 0), "status"] = "CANCELADA"
  df.loc[mask_finalizado, "status"] = "FINALIZADA"

  json_str = df.to_json(orient="records", force_ascii=False)
  CACHE_DADOS["rotas_json"] = json_str
  CACHE_DADOS["rotas_ts"] = time.time()
  return json_str


@app.get("/rotas")
def puxar_rotas():
  agora = time.time()
  if (
      CACHE_DADOS["rotas_json"] is not None
      and (agora - CACHE_DADOS["rotas_ts"]) < 600
  ):
    return Response(
        content=CACHE_DADOS["rotas_json"], media_type="application/json"
    )

  try:
    json_str = construir_json_rotas()
    return Response(content=json_str, media_type="application/json")
  except Exception as e:
    return {"erro": str(e)}


def ordenar_datas_br_lista(lista_datas):
  def chave_data(s):
    p = re.split(r"[/ -]", str(s).strip())
    if len(p) >= 3:
      if len(p[0]) == 4:
        return f"{p[0]}{p[1].zfill(2)}{p[2].zfill(2)}"
      return f"{p[2]}{p[1].zfill(2)}{p[0].zfill(2)}"
    return "00000000"

  return sorted(lista_datas, key=chave_data, reverse=True)


def obter_datas_disponiveis_brutas():
  """Busca apenas a lista de datas distintas no PostgreSQL (gasta 0 MB de RAM)"""
  agora = time.time()
  if (
      CACHE_DADOS.get("datas_brutas")
      and (agora - CACHE_DADOS.get("rotas_brutas_ts", 0)) < 600
  ):
    return CACHE_DADOS["datas_brutas"]

  try:
    insp = inspect(motor_db)
    if not insp.has_table("rotas_brutas"):
      return []
    cols = [c["name"] for c in insp.get_columns("rotas_brutas")]
    col_data = (
        "Data da rota"
        if "Data da rota" in cols
        else ("Data" if "Data" in cols else None)
    )
    if not col_data:
      return []

    with motor_db.connect() as conn:
      res = conn.execute(
          text(
              f'SELECT DISTINCT "{col_data}" FROM rotas_brutas WHERE'
              f' "{col_data}" IS NOT NULL'
          )
      ).fetchall()
    datas_unicas = [
        str(r[0]).strip()
        for r in res
        if r[0] is not None and str(r[0]).strip() != ""
    ]
    datas_ordenadas = ordenar_datas_br_lista(datas_unicas)
    CACHE_DADOS["datas_brutas"] = datas_ordenadas
    CACHE_DADOS["col_data_brutas"] = col_data
    CACHE_DADOS["rotas_brutas_ts"] = agora
    return datas_ordenadas
  except Exception:
    return []


def buscar_json_brutas_por_data(data_alvo: str) -> str:
  """Carrega do PostgreSQL APENAS as linhas do dia solicitado"""
  por_data = CACHE_DADOS.setdefault("por_data_json", {})
  if data_alvo in por_data:
    return por_data[data_alvo]

  try:
    datas_disp = obter_datas_disponiveis_brutas()
    col_data = CACHE_DADOS.get("col_data_brutas", "Data da rota")
    if not datas_disp:
      return "[]"

    df_dia = pd.read_sql_query(
        text(
            f'SELECT * FROM rotas_brutas WHERE TRIM(CAST("{col_data}" AS TEXT))'
            " = :dt"
        ),
        con=motor_db,
        params={"dt": data_alvo},
    ).fillna("")

    cols_existentes = [
        c for c in COLUNAS_UTEIS_RELATORIO if c in df_dia.columns
    ]
    if len(cols_existentes) >= 5:
      df_dia = df_dia[cols_existentes]

    json_str = df_dia.to_json(orient="records", force_ascii=False)
    if len(por_data) >= 3:
      por_data.clear()
    por_data[data_alvo] = json_str
    return json_str
  except Exception:
    return "[]"


def construir_json_rotas_brutas() -> str:
  """Pré-aquece apenas a data atual/mais recente em memória"""
  datas_disp = obter_datas_disponiveis_brutas()
  if datas_disp:
    return buscar_json_brutas_por_data(datas_disp[0])
  return "[]"


@app.get("/rotas_brutas")
def puxar_rotas_brutas(inicial: int = 0, data: str = ""):
  datas_disp = obter_datas_disponiveis_brutas()

  if inicial == 1:
    ultima_dt = datas_disp[0] if datas_disp else ""
    reg_json = buscar_json_brutas_por_data(ultima_dt) if ultima_dt else "[]"
    payload = (
        f'{{"datas": {json.dumps(datas_disp)}, "ultima_data":'
        f' {json.dumps(ultima_dt)}, "registros": {reg_json}}}'
    )
    return Response(content=payload, media_type="application/json")

  data_limpa = str(data or "").strip()
  if data_limpa and data_limpa not in ["TODAS", "PERIODO"]:
    return Response(
        content=buscar_json_brutas_por_data(data_limpa),
        media_type="application/json",
    )

  try:
    df_all = pd.read_sql_query(
        text("SELECT * FROM rotas_brutas"), con=motor_db
    ).fillna("")
    cols_existentes = [
        c for c in COLUNAS_UTEIS_RELATORIO if c in df_all.columns
    ]
    if len(cols_existentes) >= 5:
      df_all = df_all[cols_existentes]
    json_all = df_all.to_json(orient="records", force_ascii=False)
    del df_all
    return Response(content=json_all, media_type="application/json")
  except Exception:
    return Response(content="[]", media_type="application/json")


@app.post("/salvar_escala")
async def salvar_escala(request: Request):
  try:
    dados = await request.json()
    data_alvo = str(dados.get("data", "")).strip()
    hub = str(dados.get("hub", "TODOS")).upper().strip()
    atribuicoes = dados.get("atribuicoes", None)

    with motor_db.begin() as conn:
      if isinstance(atribuicoes, dict):
        for cluster, monitor in atribuicoes.items():
          mon_upper = str(monitor or "").upper().strip()
          if hub and hub != "TODOS":
            conn.execute(
                text("""
                            UPDATE rotas_monitoramento
                            SET monitor = :m
                            WHERE UPPER(TRIM(cluster)) = :c
                              AND data LIKE :dt
                              AND UPPER(nome_rota) LIKE :hb
                        """),
                {
                    "m": mon_upper,
                    "c": str(cluster).upper().strip(),
                    "dt": f"%{data_alvo}%",
                    "hb": f"{hub}%",
                },
            )
          else:
            conn.execute(
                text("""
                            UPDATE rotas_monitoramento
                            SET monitor = :m
                            WHERE UPPER(TRIM(cluster)) = :c
                              AND data LIKE :dt
                        """),
                {
                    "m": mon_upper,
                    "c": str(cluster).upper().strip(),
                    "dt": f"%{data_alvo}%",
                },
            )
      else:
        cluster = str(dados.get("cluster", "")).upper().strip()
        mon_upper = str(dados.get("monitor", "")).upper().strip()
        conn.execute(
            text("""
                    UPDATE rotas_monitoramento
                    SET monitor = :m
                    WHERE UPPER(TRIM(cluster)) = :c
                      AND data LIKE :dt
                """),
            {"m": mon_upper, "c": cluster, "dt": f"%{data_alvo}%"},
        )

    invalidar_cache_rotas()
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


# ==========================================
# ROTAS DO ORQUESTRADOR DO ROBÔ
# ==========================================
@app.get("/tickets")
def listar_tickets():
  try:
    df = pd.read_sql_query(
        text("SELECT * FROM tickets_robo"), con=motor_db
    ).fillna("")
    return df[~((df["tipo"] == "avulso") & (df["status"] == "CONCLUIDO"))].to_dict(
        orient="records"
    )
  except Exception:
    return []


@app.post("/add_ticket")
async def adicionar_ticket(request: Request):
  dados = await request.json()
  hub = dados.get("hub", "TODOS").upper().strip()
  acao = dados.get("acao", "monitoramento")
  tipo = dados.get("tipo", "avulso")
  tipo_original = dados.get("tipo_original", None)
  if not hub:
    hub = "TODOS"

  try:
    with motor_db.begin() as conn:
      conn.execute(text("""
                CREATE TABLE IF NOT EXISTS tickets_robo (
                    hub VARCHAR(50),
                    status VARCHAR(50),
                    tipo VARCHAR(50),
                    acao VARCHAR(50),
                    regras TEXT,
                    ultima_coleta VARCHAR(50)
                )
            """))
      if tipo_original and tipo_original != tipo:
        conn.execute(
            text(
                "DELETE FROM tickets_robo WHERE hub = :h AND acao = :a AND"
                " tipo = :to"
            ),
            {"h": hub, "a": acao, "to": tipo_original},
        )
      conn.execute(
          text(
              "DELETE FROM tickets_robo WHERE hub = :h AND acao = :a AND"
              " tipo = :t"
          ),
          {"h": hub, "a": acao, "t": tipo},
      )
      conn.execute(
          text("""
                INSERT INTO tickets_robo (hub, status, tipo, acao, regras, ultima_coleta) 
                VALUES (:h, 'ATIVO', :t, :a, :r, 'Aguardando')
            """),
          {
              "h": hub,
              "t": tipo,
              "a": acao,
              "r": json.dumps(dados.get("regras", {})),
          },
      )
    return {"sucesso": True, "mensagem": f"HUB {hub} ({tipo}) salvo!"}
  except Exception as e:
    return {"erro": str(e)}


@app.delete("/delete_ticket/{hub}/{acao}/{tipo}")
def deletar_ticket_com_tipo(hub: str, acao: str, tipo: str):
  try:
    with motor_db.begin() as conn:
      conn.execute(
          text(
              "DELETE FROM tickets_robo WHERE hub = :h AND acao = :a AND"
              " tipo = :t"
          ),
          {"h": hub, "a": acao, "t": tipo},
      )
    return {"sucesso": True}
  except Exception:
    return {}


@app.delete("/delete_ticket/{hub}/{acao}")
def deletar_ticket(hub: str, acao: str):
  try:
    with motor_db.begin() as conn:
      conn.execute(
          text("DELETE FROM tickets_robo WHERE hub = :h AND acao = :a"),
          {"h": hub, "a": acao},
      )
    return {"sucesso": True}
  except Exception:
    return {}


# ==========================================
# TELEMETRIA, LOGS E CONTROLE DE LOGIN OKTA DO ROBÔ
# ==========================================
logs_memoria = []
total_logs_recebidos = 0
ultimo_heartbeat = datetime.datetime.now()

codigo_okta_pendente = ""
forcar_reinicio_login = False
trocar_conta_pendente = ""
conta_okta_ativa = "Vitor.Martins"
status_login_robo = "DESCONHECIDO"
inicio_sessao_okta_ts = None


def registrar_log_interno(msg: str):
  global total_logs_recebidos
  hora = datetime.datetime.now().strftime("%H:%M:%S")
  logs_memoria.append(
      {"indice": total_logs_recebidos, "hora": hora, "msg": msg}
  )
  total_logs_recebidos += 1
  if len(logs_memoria) > 200:
    logs_memoria.pop(0)


@app.get("/heartbeat")
def ping_robo():
  global ultimo_heartbeat
  ultimo_heartbeat = datetime.datetime.now()
  return {"sucesso": True}


@app.post("/enviar_log")
async def receber_log(request: Request):
  global ultimo_heartbeat
  ultimo_heartbeat = datetime.datetime.now()
  dados = await request.json()
  msg = dados.get("mensagem", "")
  registrar_log_interno(msg)
  if "CONCLUÍDO" in msg or "atualizando planilha" in msg.lower():
    invalidar_cache_rotas()
    threading.Thread(target=construir_json_rotas, daemon=True).start()
  if "RELATÓRIO BAIXADO" in msg or "Relatório sincronizado" in msg:
    invalidar_cache_brutas()
    threading.Thread(target=construir_json_rotas_brutas, daemon=True).start()
  return {"sucesso": True}


@app.post("/enviar_codigo_okta")
async def receber_codigo_okta(request: Request):
  global codigo_okta_pendente
  dados = await request.json()
  cod = str(dados.get("codigo", "")).replace(" ", "").strip()
  if not cod:
    return {"sucesso": False, "erro": "Código vazio."}
  codigo_okta_pendente = cod
  registrar_log_interno(
      f"📲 [PAINEL WEB] Código Okta ({cod}) recebido para {conta_okta_ativa}!"
      " Enviando para o robô..."
  )
  return {"sucesso": True}


@app.post("/selecionar_conta_okta")
async def selecionar_conta_okta(request: Request):
  global conta_okta_ativa, trocar_conta_pendente, codigo_okta_pendente, status_login_robo, inicio_sessao_okta_ts
  dados = await request.json()
  nova_conta = str(dados.get("conta", "")).strip()
  if nova_conta not in ["Vitor.Martins", "Larissa.Campos"]:
    return {"sucesso": False, "erro": "Conta inválida."}
  if status_login_robo != "AGUARDANDO_OKTA":
    return {
        "sucesso": False,
        "erro": (
            "A troca de login só pode ser feita durante o processo de login e"
            " validação do Okta."
        ),
    }

  conta_okta_ativa = nova_conta
  trocar_conta_pendente = nova_conta
  codigo_okta_pendente = ""
  inicio_sessao_okta_ts = None
  registrar_log_interno(
      f"👤 [PAINEL WEB] Conta alterada para '{nova_conta}'. Ordenando ao robô"
      " voltar à tela de login..."
  )
  return {"sucesso": True, "conta": conta_okta_ativa}


@app.post("/reiniciar_login_robo")
async def solicitar_reinicio_login():
  global forcar_reinicio_login, codigo_okta_pendente, inicio_sessao_okta_ts
  forcar_reinicio_login = True
  codigo_okta_pendente = ""
  inicio_sessao_okta_ts = None
  registrar_log_interno(
      "🔄 [PAINEL WEB] Ordem de recarregar site e reiniciar login Okta enviada"
      " ao robô!"
  )
  return {"sucesso": True}


@app.get("/obter_comando_okta")
def obter_comando_okta():
  global ultimo_heartbeat, codigo_okta_pendente, forcar_reinicio_login, trocar_conta_pendente, conta_okta_ativa
  ultimo_heartbeat = datetime.datetime.now()
  cod = codigo_okta_pendente
  reiniciar = forcar_reinicio_login
  trocar = trocar_conta_pendente
  codigo_okta_pendente = ""
  forcar_reinicio_login = False
  trocar_conta_pendente = ""
  return {
      "codigo": cod,
      "reiniciar_login": reiniciar,
      "trocar_conta": trocar,
      "conta_ativa": conta_okta_ativa,
  }


@app.post("/status_login_robo")
async def atualizar_status_login_robo(request: Request):
  global ultimo_heartbeat, status_login_robo, conta_okta_ativa, inicio_sessao_okta_ts
  ultimo_heartbeat = datetime.datetime.now()
  dados = await request.json()
  novo_status = str(dados.get("status", "DESCONHECIDO"))
  nova_conta = str(dados.get("conta", "")).strip()

  if nova_conta and nova_conta != conta_okta_ativa:
    conta_okta_ativa = nova_conta
    if novo_status == "LOGADO":
      inicio_sessao_okta_ts = time.time()

  if novo_status == "LOGADO":
    if status_login_robo != "LOGADO" or inicio_sessao_okta_ts is None:
      inicio_sessao_okta_ts = time.time()
  else:
    inicio_sessao_okta_ts = None

  status_login_robo = novo_status
  return {"sucesso": True}


@app.get("/ler_logs")
def ler_logs(ultimo_indice: int = 0):
  global ultimo_heartbeat, total_logs_recebidos, status_login_robo, conta_okta_ativa, inicio_sessao_okta_ts

  if ultimo_indice > total_logs_recebidos:
    ultimo_indice = 0

  novos_logs = [log for log in logs_memoria if log["indice"] >= ultimo_indice]
  robo_on = (datetime.datetime.now() - ultimo_heartbeat).total_seconds() < 60

  tempo_sessao_seg = 0
  if (
      robo_on
      and status_login_robo == "LOGADO"
      and inicio_sessao_okta_ts is not None
  ):
    tempo_sessao_seg = max(0, int(time.time() - inicio_sessao_okta_ts))

  return {
      "logs": novos_logs,
      "novo_indice": total_logs_recebidos,
      "robo_online": robo_on,
      "status_login": status_login_robo if robo_on else "OFFLINE",
      "conta_okta": conta_okta_ativa,
      "tempo_sessao_seg": tempo_sessao_seg,
  }


# ==========================================
# ATUALIZAÇÕES MANUAIS DA PLANILHA (GRID) - ATÔMICAS E COM TRAVA MANUAL
# ==========================================
def marcar_coluna_manual_db(conn, id_alvo: str, nomes_colunas: list):
  """Registra no banco que determinadas colunas desta rota foram editadas/pintadas manualmente"""
  conn.execute(
      text(
          "ALTER TABLE rotas_monitoramento ADD COLUMN IF NOT EXISTS"
          " campos_manuais TEXT DEFAULT '{}'"
      )
  )
  row = conn.execute(
      text(
          "SELECT campos_manuais FROM rotas_monitoramento WHERE id_rota = :id"
          " LIMIT 1"
      ),
      {"id": id_alvo},
  ).fetchone()
  try:
    atual = json.loads(row[0]) if row and row[0] else {}
    if not isinstance(atual, dict):
      atual = {}
  except Exception:
    atual = {}
  for c in nomes_colunas:
    atual[c] = True
  conn.execute(
      text(
          "UPDATE rotas_monitoramento SET campos_manuais = :cm WHERE id_rota"
          " = :id"
      ),
      {"cm": json.dumps(atual), "id": id_alvo},
  )


@app.post("/atualizar_cluster")
async def atualizar_cluster(request: Request):
  dados = await request.json()
  id_alvo = str(dados.get("id_rota", "")).strip()
  novo_cluster = str(dados.get("novo_cluster", "")).upper().strip()
  try:
    with motor_db.begin() as conn:
      if "monitor" in dados:
        mon = str(dados.get("monitor", "")).upper().strip()
        conn.execute(
            text(
                "UPDATE rotas_monitoramento SET cluster = :c, monitor = :m"
                " WHERE id_rota = :id"
            ),
            {"c": novo_cluster, "m": mon, "id": id_alvo},
        )
      else:
        conn.execute(
            text(
                "UPDATE rotas_monitoramento SET cluster = :c WHERE id_rota"
                " = :id"
            ),
            {"c": novo_cluster, "id": id_alvo},
        )
      marcar_coluna_manual_db(conn, id_alvo, ["cluster"])
    invalidar_cache_rotas()
    return {"sucesso": True}
  except Exception as e:
    invalidar_cache_rotas()
    return {"sucesso": False, "erro": str(e)}


@app.post("/atualizar_observacao")
async def atualizar_obs(request: Request):
  dados = await request.json()
  id_alvo = str(dados.get("id_rota", "")).strip()
  obs = str(dados.get("observacao", ""))
  try:
    with motor_db.begin() as conn:
      conn.execute(
          text(
              "UPDATE rotas_monitoramento SET observacoes = :ob WHERE id_rota"
              " = :id"
          ),
          {"ob": obs, "id": id_alvo},
      )
      marcar_coluna_manual_db(conn, id_alvo, ["observacoes"])
    invalidar_cache_rotas()
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


@app.post("/atualizar_revertido")
async def atualizar_rev(request: Request):
  dados = await request.json()
  id_alvo = str(dados.get("id_rota", "")).strip()
  rev = str(dados.get("revertido", "")).upper().strip()
  try:
    with motor_db.begin() as conn:
      conn.execute(
          text(
              "UPDATE rotas_monitoramento SET revertido = :rv WHERE id_rota"
              " = :id"
          ),
          {"rv": rev, "id": id_alvo},
      )
      marcar_coluna_manual_db(conn, id_alvo, ["revertido"])
    invalidar_cache_rotas()
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


@app.post("/atualizar_ocorrencia")
async def atualizar_ocorrencia_rota(request: Request):
  dados = await request.json()
  id_alvo = str(dados.get("id_rota", "")).strip()
  oc = str(dados.get("ocorrencia", "")).strip()
  try:
    with motor_db.begin() as conn:
      conn.execute(
          text(
              "UPDATE rotas_monitoramento SET ocorrencia = :oc WHERE id_rota"
              " = :id"
          ),
          {"oc": oc, "id": id_alvo},
      )
      marcar_coluna_manual_db(conn, id_alvo, ["ocorrencia"])
    invalidar_cache_rotas()
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


@app.post("/atualizar_status")
async def atualizar_stat(request: Request):
  dados = await request.json()
  id_alvo = str(dados.get("id_rota", "")).strip()
  novo_status = str(dados.get("status", "")).upper().strip()
  if novo_status == "FINALIZADO":
    novo_status = "FINALIZADA"
  flag_manual = "SIM" if novo_status != "" else ""
  try:
    with motor_db.begin() as conn:
      conn.execute(
          text(
              "UPDATE rotas_monitoramento SET status = :st, status_manual = :sm"
              " WHERE id_rota = :id"
          ),
          {"st": novo_status, "sm": flag_manual, "id": id_alvo},
      )
      if flag_manual == "SIM":
        marcar_coluna_manual_db(conn, id_alvo, ["status"])
    invalidar_cache_rotas()
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


@app.post("/atualizar_pacotes_manual")
async def atualizar_pacotes_manual(request: Request):
  dados = await request.json()
  id_alvo = str(dados.get("id_rota", "")).strip()
  coluna = str(dados.get("coluna", "")).strip()
  valor = re.sub(r"\D", "", str(dados.get("valor", "0"))) or "0"

  if coluna not in ["estimado_total", "coletado_geral"]:
    return {"sucesso": False, "erro": "Coluna inválida"}

  try:
    with motor_db.begin() as conn:
      conn.execute(
          text(
              f"UPDATE rotas_monitoramento SET {coluna} = :val WHERE id_rota"
              " = :id"
          ),
          {"val": valor, "id": id_alvo},
      )
      row = conn.execute(
          text(
              "SELECT estimado_total, coletado_geral FROM rotas_monitoramento"
              " WHERE id_rota = :id LIMIT 1"
          ),
          {"id": id_alvo},
      ).fetchone()
      if row:
        est_i = int(re.sub(r"\D", "", str(row[0] or "0")) or "0")
        col_i = int(re.sub(r"\D", "", str(row[1] or "0")) or "0")
        rest_str = str(est_i - col_i)
        conn.execute(
            text(
                "UPDATE rotas_monitoramento SET restante = :re WHERE id_rota"
                " = :id"
            ),
            {"re": rest_str, "id": id_alvo},
        )
      marcar_coluna_manual_db(conn, id_alvo, [coluna])
    invalidar_cache_rotas()
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


@app.post("/atualizar_cor")
async def atualizar_cor(request: Request):
  dados = await request.json()
  id_rota = str(dados.get("id_rota", "")).strip()
  coluna = str(dados.get("coluna", "")).strip()
  cor = str(dados.get("cor", "")).strip()
  try:
    with motor_db.begin() as conn:
      conn.execute(
          text(
              "ALTER TABLE rotas_monitoramento ADD COLUMN IF NOT EXISTS"
              " cores_customizadas TEXT DEFAULT '{}'"
          )
      )
      row = conn.execute(
          text(
              "SELECT cores_customizadas FROM rotas_monitoramento WHERE"
              " id_rota = :id LIMIT 1"
          ),
          {"id": id_rota},
      ).fetchone()
      try:
        dict_cores = json.loads(row[0]) if row and row[0] else {}
        if not isinstance(dict_cores, dict):
          dict_cores = {}
      except Exception:
        dict_cores = {}

      if cor.lower() in ["#ffffff", "transparent", ""]:
        dict_cores.pop(coluna, None)
      else:
        dict_cores[coluna] = cor
        marcar_coluna_manual_db(conn, id_rota, [coluna])
        if coluna == "status":
          conn.execute(
              text(
                  "UPDATE rotas_monitoramento SET status_manual = 'SIM' WHERE"
                  " id_rota = :id"
              ),
              {"id": id_rota},
          )

      conn.execute(
          text(
              "UPDATE rotas_monitoramento SET cores_customizadas = :cc WHERE"
              " id_rota = :id"
          ),
          {"cc": json.dumps(dict_cores), "id": id_rota},
      )
    invalidar_cache_rotas()
    return {"sucesso": True}
  except Exception as e:
    return {"sucesso": False, "erro": str(e)}


# ==========================================
# DOWNLOAD DE RELATÓRIO PARA O PC DO USUÁRIO
# ==========================================
@app.get("/download_relatorio/{nome_arquivo}")
def baixar_relatorio_pc(nome_arquivo: str):
  nome_seguro = os.path.basename(nome_arquivo)
  pasta_downloads = os.path.abspath(
      os.path.join(os.path.dirname(__file__), "..", "temp", "downloads_pc")
  )
  caminho_completo = os.path.join(pasta_downloads, nome_seguro)

  if os.path.exists(caminho_completo):
    return FileResponse(
        path=caminho_completo,
        filename=nome_seguro,
        media_type="application/octet-stream",
    )
  return {"sucesso": False, "erro": "Arquivo não encontrado no servidor."}